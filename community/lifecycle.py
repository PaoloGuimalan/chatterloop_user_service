"""Authoritative transitions; no purge or ordinary inactive access here."""
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from rest_framework.exceptions import APIException, PermissionDenied

from community.access import effective_realm_access, lock_realm_scope, descendant_realms
from community.lifecycle_policy import current_recovery_paths, has_lifecycle_authority
from community.lifecycle_state import voluntarily_deactivated
from community.models import Member, Realm
from entity.services.acting import can_act_as_entity


class LifecycleConflict(APIException):
    status_code = 409


def _root_id(realm):
    seen = set()
    while realm.parent_id:
        if realm.pk in seen:
            raise PermissionDenied('This realm is unavailable.')
        seen.add(realm.pk)
        realm = get_object_or_404(Realm, pk=realm.parent_id)
    return str(realm.pk)


def _lock_target(account, actor, realm_id):
    target = get_object_or_404(Realm.objects.select_related('parent'), pk=realm_id)
    scopes = [target]
    if actor and str(actor.pk) != str(getattr(account, 'entity_id', '')):
        source = Realm.objects.filter(entity=actor, type='page').first()
        if source:
            scopes.append(source)
    targets = sorted(scopes, key=lambda realm: (_root_id(realm), str(realm.pk)))
    for scope in targets:
        lock_realm_scope(scope.pk, exclusive=True)
    target = Realm.objects.select_related('parent').get(pk=realm_id)
    if actor is None or not can_act_as_entity(account, actor):
        raise PermissionDenied('You cannot use this identity.')
    return target


def _changed(realm, state):
    realm.save(update_fields=['is_active', 'deactivated_at', 'deletion_requested_at'])
    def deliver():
        from community.lifecycle_events import dispatch_lifecycle
        dispatch_lifecycle(str(realm.pk), state)
    transaction.on_commit(deliver, robust=True)
    return dict(realm_id=str(realm.pk), state=state)


def page_ownership_blockers(realm):
    if realm.type != 'page':
        return Realm.objects.none()
    return Realm.objects.filter(member__entity_id=realm.entity_id, member__role__realm_id=F('pk'),
                               member__role__code='owner', member__role__is_default=True).exclude(pk=realm.pk)


def validate_deletion_hierarchy(realm):
    nodes = {str(row['id']): row for row in descendant_realms(realm.pk).values('id','parent_id','type')}
    for row in nodes.values():
        if row['parent_id'] is None:
            continue
        parent = nodes.get(str(row['parent_id']))
        if parent is None:
            parent = Realm.objects.filter(pk=row['parent_id']).values('type').first()
        if row['type'] not in {'group','channel','voice'} or not parent or parent['type'] != 'server':
            raise LifecycleConflict({'code':'REALM_INVALID_HIERARCHY','message':'This realm has an invalid parent or child relationship. Correct it before deletion.'})


@transaction.atomic
def deactivate_realm(*, account, actor, realm_id):
    realm = _lock_target(account, actor, realm_id)
    if not has_lifecycle_authority(actor, realm, 'realm.deactivate') or realm.deletion_requested_at is not None:
        raise PermissionDenied('You cannot deactivate this realm.')
    if voluntarily_deactivated(realm):
        return dict(realm_id=str(realm.pk), state='deactivated')
    if not effective_realm_access(realm).allowed:
        raise PermissionDenied('This realm is unavailable.')
    realm.is_active, realm.deactivated_at = False, now()
    return _changed(realm, 'deactivated')


@transaction.atomic
def reactivate_realm(*, account, realm_id, actor_entity_id):
    from entity.models import Entity
    actor = Entity.objects.filter(pk=actor_entity_id).first()
    realm = _lock_target(account, actor, realm_id)
    if (realm.deletion_requested_at is not None or (not realm.is_active and not voluntarily_deactivated(realm))
            or (realm.type == 'conference' and realm.expires_at and realm.expires_at <= now())):
        raise PermissionDenied('This realm cannot be reactivated.')
    if not any(path.entity_id == str(actor_entity_id) for path in current_recovery_paths(account, realm)):
        raise PermissionDenied('You cannot reactivate this realm.')
    if realm.parent_id and not effective_realm_access(realm.parent).allowed:
        raise LifecycleConflict({'code': 'REALM_PARENT_INACTIVE', 'message': 'Reactivate the parent server first.'})
    if realm.is_active:
        return dict(realm_id=str(realm.pk), state='active')
    realm.is_active, realm.deactivated_at = True, None
    return _changed(realm, 'active')


@transaction.atomic
def request_realm_deletion(*, account, actor, realm_id, confirm_name):
    realm = _lock_target(account, actor, realm_id)
    if not has_lifecycle_authority(actor, realm, 'realm.delete'):
        raise PermissionDenied('Only an Owner can permanently delete this realm.')
    if not isinstance(confirm_name, str) or confirm_name.strip() != realm.name.strip():
        raise LifecycleConflict({'code': 'REALM_NAME_CHANGED', 'message': 'Enter the current realm name to confirm deletion.'})
    if realm.deletion_requested_at is not None:
        return dict(realm_id=str(realm.pk), state='deleting')
    if not effective_realm_access(realm).allowed:
        raise PermissionDenied('Reactivate this realm before managing deletion.')
    validate_deletion_hierarchy(realm)
    if page_ownership_blockers(realm).exists():
        raise LifecycleConflict({'code': 'REALM_OWNERSHIP_REQUIRED',
                                 'message': 'Transfer ownership of other realms before deleting this page.'})
    realm.is_active, realm.deletion_requested_at = False, now()
    return _changed(realm, 'deleting')
