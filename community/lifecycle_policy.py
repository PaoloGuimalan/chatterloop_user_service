"""Narrow authority to restore voluntarily inactive realms, never ordinary access."""
from dataclasses import asdict, dataclass

from django.core import signing
from django.db.models import Case, CharField, Exists, F, OuterRef, Q, Subquery, Value, When
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now
from rest_framework.exceptions import ValidationError

from community.access import effective_realm_access
from community.lifecycle_state import voluntarily_deactivated
from community.models import Member, Realm
from entity.models import EntityPermission, PermissionCatalogEntry, RolePermission
from entity.services.acting import acting_memberships, can_act_as_entity
from entity.services.permission_applicability import effective_realm_kind

LIFECYCLE_CODES = frozenset({'realm.delete', 'realm.deactivate', 'realm.reactivate'})


@dataclass(frozen=True)
class RecoveryActor:
    entity_id: str
    name: str
    is_personal: bool


@dataclass(frozen=True)
class RecoveryPage:
    items: list
    next_cursor: str | None


def eligible_memberships(actor_ids, code):
    """One SQL authority query, including inactive targets and current overrides."""
    rows = Member.objects.filter(entity_id__in=actor_ids, role__realm_id=F('realm_id')).annotate(
        _kind=Case(When(realm__type='group', realm__parent_id__isnull=False, then=Value('channel')),
                   default=F('realm__type'), output_field=CharField()))
    owner = Q(role__code='owner', role__is_default=True)
    if code == 'realm.delete':
        return rows.filter(owner)
    overrides = EntityPermission.objects.filter(entity_id=OuterRef('entity_id'), realm_id=OuterRef('realm_id'),
        permission__code=code).filter(Q(expires_at=None) | Q(expires_at__gt=now())).order_by('pk')
    return rows.annotate(
        _catalog=Exists(PermissionCatalogEntry.objects.filter(code=code, scope='realm', is_active=True,
                                                            realm_types__code=OuterRef('_kind'))),
        _override=Subquery(overrides.values('effect')[:1]),
        _grant=Exists(RolePermission.objects.filter(role_id=OuterRef('role_id'), permission__code=code)),
    ).filter(owner | (Q(_catalog=True) & (Q(_override='grant') | Q(_override__isnull=True, _grant=True))))


def _parent_authority(realm):
    return realm.parent_id if realm.type in ('channel', 'voice', 'group') and realm.parent_id and realm.parent.type == 'server' else None


def has_lifecycle_authority(actor, realm, code):
    if actor is None or code not in LIFECYCLE_CODES or str(actor.pk) == str(realm.entity_id):
        return False
    if eligible_memberships([actor.pk], code).filter(realm=realm).exists():
        return True
    parent = _parent_authority(realm)
    return bool(parent and Member.objects.filter(entity=actor, realm_id=parent, role__realm_id=F('realm_id'),
                                                role__code='owner', role__is_default=True).exists())


def lifecycle_capabilities(account, actor, realm):
    if actor is not None and str(actor.pk) == str(realm.entity_id):
        actor = getattr(account, 'entity', None)
    valid_actor = actor is not None and (account is None or can_act_as_entity(account, actor))
    active = effective_realm_access(realm).allowed
    return {f'can_{action}_realm': bool(valid_actor and active and has_lifecycle_authority(actor, realm, f'realm.{action}'))
            for action in ('deactivate', 'delete', 'reactivate')}


def recovery_actor_choices(account):
    personal = getattr(account, 'entity_id', None)
    if not personal or not getattr(account, 'is_active', True):
        return {}
    choices = {str(personal): RecoveryActor(str(personal), 'Your profile', True)}
    for membership in acting_memberships(personal).select_related('realm'):
        page = membership.realm
        choices[str(page.entity_id)] = RecoveryActor(str(page.entity_id), page.name, False)
    return choices


def _membership_paths(choices, realm, rows):
    target, parent = str(realm.pk), _parent_authority(realm)
    permitted = set()
    for row in rows:
        if str(row['realm_id']) == target or (parent and str(row['realm_id']) == str(parent)
                and row['role__code'] == 'owner' and row['role__is_default']):
            permitted.add(str(row['entity_id']))
    return [choice for key, choice in choices.items() if key in permitted]


def current_recovery_paths(account, realm):
    """Current authority only; the caller must separately validate target state."""
    choices = recovery_actor_choices(account)
    rows = eligible_memberships(choices, 'realm.reactivate').filter(
        realm_id__in=[realm.pk, realm.parent_id]).values('realm_id', 'entity_id', 'role__code', 'role__is_default')
    return _membership_paths(choices, realm, rows)


def recovery_paths(account, realm, *, fresh=True):
    if not voluntarily_deactivated(realm) or (realm.type == 'conference' and realm.expires_at and realm.expires_at <= now()):
        return []
    return current_recovery_paths(account, realm)


def _cursor(realm):
    return signing.dumps({'at': realm.deactivated_at.isoformat(), 'id': str(realm.pk)}, salt='realm-recovery')


def list_recoverable_realms(account, *, cursor=None, limit=20):
    choices = recovery_actor_choices(account)
    if not choices:
        return RecoveryPage([], None)
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        raise ValidationError('Choose a valid page size.')
    eligible = eligible_memberships(choices, 'realm.reactivate')
    parent_owners = eligible.filter(realm__type='server', role__code='owner', role__is_default=True)
    query = Realm.objects.filter(is_active=False, deactivated_at__isnull=False, deletion_requested_at=None).filter(
        Q(pk__in=eligible.values('realm_id')) | Q(parent_id__in=parent_owners.values('realm_id'))).exclude(
        type='conference', expires_at__lte=now()).select_related('parent').order_by('-deactivated_at', '-pk')
    if cursor:
        try:
            value = signing.loads(cursor, salt='realm-recovery')
            timestamp = parse_datetime(value['at'])
            if timestamp is None or not isinstance(value['id'], str):
                raise ValueError()
        except (signing.BadSignature, KeyError, TypeError, ValueError):
            raise ValidationError('Invalid recovery list cursor.')
        query = query.filter(Q(deactivated_at__lt=timestamp) | Q(deactivated_at=timestamp, pk__lt=value['id']))
    realms = list(query[:limit + 1])
    visible = realms[:limit]
    realm_ids = {key for realm in visible for key in (realm.pk, realm.parent_id) if key}
    rows = list(eligible.filter(realm_id__in=realm_ids).values('realm_id', 'entity_id', 'role__code', 'role__is_default'))
    items = []
    for realm in visible:
        paths = _membership_paths(choices, realm, rows)
        if not paths:
            continue
        parent = realm.parent
        blocked = bool(parent and not effective_realm_access(parent).allowed)
        parent_id = parent.pk if parent and voluntarily_deactivated(parent) and _membership_paths(choices, parent, rows) else None
        items.append(dict(realm_id=str(realm.pk), name=realm.name, kind=effective_realm_kind(realm), profile=realm.profile,
            deactivated_at=realm.deactivated_at.isoformat(), actors=[asdict(path) for path in paths],
            blocked_by_parent=blocked, parent_recovery_realm_id=parent_id))
    return RecoveryPage(items, _cursor(visible[-1]) if len(realms) > limit else None)
