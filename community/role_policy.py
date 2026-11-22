"""Shared role authorization; callers serialize writes on the realm row."""

from django.db import connection
from django.db.models import Q, Subquery
from django.utils.timezone import now
from rest_framework.exceptions import PermissionDenied, ValidationError

from community.models import Member
from entity.models import PermissionCatalogEntry, EntityPermission
from entity.permissions import Permission
from entity.roles import get_default_role, resolve_role, role_tier
from entity.services.permission_catalog_cache import get_catalog
from entity.services.permission_resolver import has_active_permission as has_permission
from entity.services.permission_applicability import applicable_permission_codes, permission_applies, effective_realm_kind

ADMINISTRATION = frozenset({Permission.REALM_ROLES_MANAGE, Permission.REALM_PERMISSIONS_MANAGE, Permission.REALM_IDENTITY_ACT})
INTRINSIC = frozenset({Permission.REALM_DELETE, Permission.REALM_OWNERSHIP_TRANSFER})
_DEFAULT_MANAGEMENT_ACTOR = object()


def role_management_actor(request, realm):
    actor = getattr(request, 'entity', None)
    if actor is not None and str(actor.pk) == str(realm.entity_id):
        return getattr(getattr(request, 'user', None), 'entity', None)
    return actor


def require_realm_actor(actor, realm, *, context=None):
    from community.access import effective_realm_access
    if actor is None or not effective_realm_access(realm, context=context).allowed:
        raise PermissionDenied("You cannot manage this realm.")
    if realm.type != "community" and str(actor.pk) == str(realm.entity_id):
        return "owner"
    key = ('actor_tier', str(actor.pk), realm.pk)
    if context is not None and key in context:
        return context[key]
    members = Member.objects.select_related("role").filter(entity=actor, realm=realm)
    if connection.in_atomic_block:
        members = members.select_for_update(of=("self",))
    member = members.first()
    if member is None or member.role.realm_id != realm.pk:
        raise PermissionDenied("Only current members can use this action.")
    tier = role_tier(member.role)
    if context is not None:
        context[key] = tier
        context[('realm_role', actor.pk, realm.pk)] = member.role
    return tier


def require_role_configuration(actor, realm, permission=Permission.REALM_ROLES_MANAGE, *, fresh=True, context=None):
    tier = require_realm_actor(actor, realm, context=None if fresh else context)
    if not has_permission(actor, permission, realm, fresh=fresh, context=context):
        raise PermissionDenied("You cannot configure permissions here.")
    return tier


def effective_role_permissions(role, realm):
    if role.realm_id != realm.pk:
        raise PermissionDenied("The role belongs to another realm.")
    if role_tier(role) == 'owner':
        return applicable_permission_codes(realm)
    if 'permissions' not in getattr(role, '_prefetched_objects_cache', {}):
        return set(role.permissions.filter(scope='realm', is_active=True,
            realm_types__code=effective_realm_kind(realm)).values_list('code', flat=True))
    return {entry.code for entry in role.permissions.all() if entry.scope == 'realm'
            and entry.is_active and permission_applies(entry, realm)}


def validate_delegation(actor, realm, codes, *, fresh=True, context=None):
    tier = require_realm_actor(actor, realm, context=None if fresh else context)
    for code in codes:
        if code in INTRINSIC:
            raise PermissionDenied("Ownership actions cannot be delegated through roles.")
        if code in ADMINISTRATION and tier != "owner":
            raise PermissionDenied("Only the owner can delegate role or permission administration.")
        if not has_permission(actor, code, realm, fresh=fresh, context=context):
            raise PermissionDenied("You cannot delegate permissions you do not hold.")


def validate_permission_codes(actor, realm, codes):
    if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
        raise ValidationError({"permissions": "Send a list of permission codes."})
    entries = list(PermissionCatalogEntry.objects.filter(code__in=set(codes), scope="realm", is_active=True).prefetch_related('realm_types'))
    if len(entries) != len(set(codes)) or any(not permission_applies(entry, realm) for entry in entries):
        raise ValidationError({"permissions": "Choose active permissions applicable to this realm only."})
    validate_delegation(actor, realm, {entry.code for entry in entries})
    return entries


def assert_can_delegate_role(actor, realm, target, role, *, fresh=True, context=None):
    actor_tier = require_realm_actor(actor, realm, context=None if fresh else context)
    if not has_permission(actor, Permission.REALM_MEMBER_ROLE_UPDATE, realm, fresh=fresh, context=context):
        raise PermissionDenied("You cannot assign roles here.")
    resolve_role(role, realm=realm)
    if role_tier(role) == "owner" or (target is not None and role_tier(target.role) == "owner"):
        raise PermissionDenied("Use ownership transfer to change the realm owner.")
    if target is not None and actor_tier != "owner":
        if role_tier(target.role) == "admin" or any(has_permission(target.entity, code, realm, fresh=fresh, context=context) for code in ADMINISTRATION):
            raise PermissionDenied("Only the owner can change an administrator's role.")
    validate_delegation(actor, realm, effective_role_permissions(role, realm), fresh=fresh, context=context)


def assert_can_edit_role(actor, realm, role, permission=Permission.REALM_ROLES_MANAGE, *, fresh=True, context=None):
    tier = require_role_configuration(actor, realm, permission, fresh=fresh, context=context)
    if role.realm_id != realm.pk:
        raise PermissionDenied("The role belongs to another realm.")
    if role_tier(role) == "owner":
        raise PermissionDenied("The Owner role cannot be changed.")
    assert_role_target_access(tier, realm, role, fresh=fresh, context=context)


def assert_role_target_access(tier, realm, role, *, fresh=True, context=None):
    """Protect peer administrators for both role definitions and local rules."""
    if tier != "owner":
        if role_tier(role) in ("owner", "admin") or ADMINISTRATION & effective_role_permissions(role, realm):
            raise PermissionDenied("Only the owner can edit an administrative role.")
        if not fresh and context is not None:
            key = ('administrative_role_holders', realm.pk)
            if key not in context:
                codes = ADMINISTRATION & applicable_permission_codes(realm, catalog=context['catalog'])
                overrides = EntityPermission.objects.filter(realm=realm, effect='grant',
                    permission__code__in=codes).filter(Q(expires_at=None) | Q(expires_at__gt=now()))
                context[key] = set(Member.objects.filter(realm=realm, role__realm=realm,
                    entity_id__in=Subquery(overrides.values('entity_id'))).values_list('role_id', flat=True))
            if role.pk in context[key]:
                raise PermissionDenied("Only the owner can edit a role held by an administrator.")
            return
        for member in role.memberships.filter(realm=realm).select_related("entity"):
            if any(has_permission(member.entity, code, realm, fresh=True) for code in ADMINISTRATION):
                raise PermissionDenied("Only the owner can edit a role held by an administrator.")


def member_preset(realm):
    return sorted(effective_role_permissions(get_default_role("member", realm=realm), realm))


def feature_availability():
    return {
        "realm_role_configuration": True,
        "community_approvals": True,
        "submissions_supported": True,
    }


def notify_role_configuration_changed(realm):
    """Reuse the existing member/role refresh signal after a committed write."""
    from community.views import InviteView
    InviteView()._notify_members_changed(realm)


def realm_capabilities(actor, realm, *, management_actor=_DEFAULT_MANAGEMENT_ACTOR):
    """UI hints only: mutation services still recheck authoritative permissions."""
    catalog = get_catalog()
    context = {"catalog": catalog}
    eligible = False
    actor_tier = None
    if actor is not None:
        try:
            actor_tier = require_realm_actor(actor, realm)
            eligible = True
        except PermissionDenied:
            pass

    management_codes = {Permission.REALM_ROLES_MANAGE, Permission.REALM_PERMISSIONS_MANAGE,
                        Permission.REALM_MEMBER_ROLE_UPDATE, Permission.REALM_OWNERSHIP_TRANSFER}
    management_principal = actor if management_actor is _DEFAULT_MANAGEMENT_ACTOR else management_actor
    management_eligible = eligible
    management_tier = actor_tier
    if management_principal is not actor:
        try:
            management_tier = require_realm_actor(management_principal, realm)
            management_eligible = True
        except PermissionDenied:
            management_eligible = False
    def allowed(code):
        management = code in management_codes
        principal = management_principal if management else actor
        return (management_eligible if management else eligible) and code in catalog and catalog[code]["is_active"] and has_permission(principal, code, realm, context=context)

    community = realm.type == "community"
    caps = {
        "can_update_realm": allowed(Permission.REALM_UPDATE),
        "can_update_media": allowed(Permission.REALM_MEDIA_UPDATE),
        "can_assign_roles": allowed(Permission.REALM_MEMBER_ROLE_UPDATE),
        "can_transfer_ownership": management_tier == 'owner' and allowed(Permission.REALM_OWNERSHIP_TRANSFER),
        "can_remove_followers": allowed(Permission.REALM_FOLLOWER_REMOVE),
        "can_view_members": allowed(Permission.REALM_MEMBER_VIEW),
        "can_invite_members": allowed(Permission.REALM_INVITE_CREATE),
        "can_publish_invites": allowed(Permission.REALM_INVITE_PUBLISH),
        "can_revoke_invites": allowed(Permission.REALM_INVITE_REVOKE),
        "can_remove_members": allowed(Permission.REALM_MEMBER_REMOVE),
        "can_manage_roles": allowed(Permission.REALM_ROLES_MANAGE),
        "can_manage_permissions": allowed(Permission.REALM_PERMISSIONS_MANAGE),
        "can_review_posts": community and allowed(Permission.REALM_POST_REVIEW),
        "can_review_invites": community and allowed(Permission.REALM_INVITE_REVIEW),
        "can_review_join_requests": allowed(Permission.REALM_REQUEST_APPROVE),
        "can_decline_join_requests": allowed(Permission.REALM_REQUEST_DECLINE),
        "can_take_down_posts": community and allowed(Permission.REALM_POST_TAKEDOWN),
        "can_archive_community_posts": community and allowed(Permission.REALM_POST_ARCHIVE),
        "can_view_notifications": community and allowed(Permission.REALM_NOTIFICATIONS_VIEW),
        "can_submit_posts": community and allowed(Permission.REALM_POST_SUBMIT),
        "can_publish_posts": community and allowed(Permission.REALM_POST_PUBLISH),
    }
    caps['can_view_notifications'] = community and any(caps[key] for key in (
        'can_view_notifications', 'can_review_posts', 'can_review_invites', 'can_review_join_requests', 'can_decline_join_requests'))
    from community.lifecycle_policy import lifecycle_capabilities
    caps.update(lifecycle_capabilities(None, management_principal, realm))
    caps["can_manage"] = any(caps[key] for key in (
        "can_invite_members", "can_remove_members", "can_remove_followers", "can_transfer_ownership", "can_update_realm", "can_update_media", "can_archive_community_posts", "can_assign_roles", "can_manage_roles", "can_manage_permissions",
        "can_review_posts", "can_review_invites", "can_review_join_requests", "can_decline_join_requests", "can_view_notifications",
        "can_deactivate_realm", "can_delete_realm",
    ))
    return caps
