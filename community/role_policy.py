"""Shared role authorization; callers serialize writes on the realm row."""

from django.conf import settings
from django.db import connection
from rest_framework.exceptions import PermissionDenied, ValidationError

from community.models import Member
from entity.models import PermissionCatalogEntry
from entity.permissions import Permission
from entity.roles import get_default_role, resolve_role, role_tier
from entity.services.permission_catalog_cache import get_catalog
from entity.services.permission_resolver import has_active_permission as has_permission

ADMINISTRATION = frozenset({Permission.REALM_ROLES_MANAGE, Permission.REALM_PERMISSIONS_MANAGE, Permission.REALM_IDENTITY_ACT})
INTRINSIC = frozenset({Permission.REALM_DELETE, Permission.REALM_OWNERSHIP_TRANSFER})


def require_realm_actor(actor, realm):
    if actor is None or not realm.is_active:
        raise PermissionDenied("You cannot manage this realm.")
    if realm.type != "community" and str(actor.pk) == str(realm.entity_id):
        return "owner"
    members = Member.objects.select_related("role").filter(entity=actor, realm=realm)
    if connection.in_atomic_block:
        members = members.select_for_update(of=("self",))
    member = members.first()
    if member is None or member.role.realm_id != realm.pk:
        raise PermissionDenied("Only current members can use this action.")
    return role_tier(member.role)


def require_role_configuration(actor, realm, permission=Permission.REALM_ROLES_MANAGE):
    if not getattr(settings, "REALM_ROLE_CONFIGURATION_ENABLED", False):
        raise PermissionDenied("Role configuration is not available yet.")
    tier = require_realm_actor(actor, realm)
    if not has_permission(actor, permission, realm, fresh=True):
        raise PermissionDenied("You cannot configure permissions here.")
    return tier


def effective_role_permissions(role, realm):
    if role.realm_id != realm.pk:
        raise PermissionDenied("The role belongs to another realm.")
    entries = PermissionCatalogEntry.objects.filter(scope="realm", is_active=True) if role_tier(role) == "owner" else role.permissions.filter(scope="realm", is_active=True)
    return set(entries.values_list("codename", flat=True))


def validate_delegation(actor, realm, codes):
    tier = require_realm_actor(actor, realm)
    for code in codes:
        if code in INTRINSIC:
            raise PermissionDenied("Ownership actions cannot be delegated through roles.")
        if code in ADMINISTRATION and tier != "owner":
            raise PermissionDenied("Only the owner can delegate role or permission administration.")
        if not has_permission(actor, code, realm, fresh=True):
            raise PermissionDenied("You cannot delegate permissions you do not hold.")


def validate_permission_codes(actor, realm, codes):
    if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
        raise ValidationError({"permissions": "Send a list of permission codes."})
    entries = list(PermissionCatalogEntry.objects.filter(codename__in=set(codes), scope="realm", is_active=True))
    if len(entries) != len(set(codes)):
        raise ValidationError({"permissions": "Choose active realm permissions only."})
    validate_delegation(actor, realm, {entry.codename for entry in entries})
    return entries


def assert_can_delegate_role(actor, realm, target, role):
    actor_tier = require_realm_actor(actor, realm)
    if not has_permission(actor, Permission.REALM_MEMBER_ROLE_UPDATE, realm, fresh=True):
        raise PermissionDenied("You cannot assign roles here.")
    resolve_role(role, realm=realm)
    if role_tier(role) == "owner" or (target is not None and role_tier(target.role) == "owner"):
        raise PermissionDenied("Use ownership transfer to change the realm owner.")
    if target is not None and actor_tier != "owner":
        if role_tier(target.role) == "admin" or any(has_permission(target.entity, code, realm, fresh=True) for code in ADMINISTRATION):
            raise PermissionDenied("Only the owner can change an administrator's role.")
    validate_delegation(actor, realm, effective_role_permissions(role, realm))


def assert_can_edit_role(actor, realm, role, permission=Permission.REALM_ROLES_MANAGE):
    tier = require_role_configuration(actor, realm, permission)
    if role.realm_id != realm.pk:
        raise PermissionDenied("The role belongs to another realm.")
    if role_tier(role) == "owner":
        raise PermissionDenied("The Owner role cannot be changed.")
    assert_role_target_access(tier, realm, role)


def assert_role_target_access(tier, realm, role):
    """Protect peer administrators for both role definitions and local rules."""
    if tier != "owner":
        if role_tier(role) in ("owner", "admin") or ADMINISTRATION & effective_role_permissions(role, realm):
            raise PermissionDenied("Only the owner can edit an administrative role.")
        for member in role.memberships.filter(realm=realm).select_related("entity"):
            if any(has_permission(member.entity, code, realm, fresh=True) for code in ADMINISTRATION):
                raise PermissionDenied("Only the owner can edit a role held by an administrator.")


def member_preset(realm):
    return sorted(effective_role_permissions(get_default_role("member", realm=realm), realm))


def feature_availability():
    return {
        "realm_role_configuration": bool(getattr(settings, "REALM_ROLE_CONFIGURATION_ENABLED", False)),
        "community_approvals": bool(getattr(settings, "COMMUNITY_APPROVALS_ENABLED", False)),
        "submissions_supported": True,
    }


def notify_role_configuration_changed(realm):
    """Reuse the existing member/role refresh signal after a committed write."""
    from community.views import InviteView
    InviteView()._notify_members_changed(realm)


def realm_capabilities(actor, realm):
    """UI hints only: mutation services still recheck authoritative permissions."""
    catalog = get_catalog()
    context = {"catalog": catalog}
    eligible = False
    if actor is not None:
        try:
            require_realm_actor(actor, realm)
            eligible = True
        except PermissionDenied:
            pass

    def allowed(code):
        return eligible and code in catalog and catalog[code]["is_active"] and has_permission(actor, code, realm, context=context)

    community = realm.type == "community"
    caps = {
        "can_update_realm": allowed(Permission.REALM_UPDATE),
        "can_update_media": allowed(Permission.REALM_MEDIA_UPDATE),
        "can_assign_roles": allowed(Permission.REALM_MEMBER_ROLE_UPDATE),
        "can_view_members": allowed(Permission.REALM_MEMBER_VIEW),
        "can_invite_members": allowed(Permission.REALM_INVITE_CREATE),
        "can_publish_invites": allowed(Permission.REALM_INVITE_PUBLISH),
        "can_revoke_invites": allowed(Permission.REALM_INVITE_REVOKE),
        "can_remove_members": allowed(Permission.REALM_MEMBER_REMOVE),
        "can_manage_roles": feature_availability()["realm_role_configuration"] and allowed(Permission.REALM_ROLES_MANAGE),
        "can_manage_permissions": feature_availability()["realm_role_configuration"] and allowed(Permission.REALM_PERMISSIONS_MANAGE),
        "can_review_posts": community and allowed(Permission.REALM_POST_REVIEW),
        "can_review_invites": community and allowed(Permission.REALM_INVITE_REVIEW),
        "can_review_join_requests": community and allowed(Permission.REALM_REQUEST_APPROVE),
        "can_decline_join_requests": community and allowed(Permission.REALM_REQUEST_DECLINE),
        "can_take_down_posts": community and allowed(Permission.REALM_POST_TAKEDOWN),
        "can_archive_community_posts": community and allowed(Permission.REALM_POST_ARCHIVE),
        "can_view_notifications": community and allowed(Permission.REALM_NOTIFICATIONS_VIEW),
        "can_submit_posts": community and allowed(Permission.REALM_POST_SUBMIT),
        "can_publish_posts": community and allowed(Permission.REALM_POST_PUBLISH),
    }
    caps['can_view_notifications'] = community and any(caps[key] for key in (
        'can_view_notifications', 'can_review_posts', 'can_review_invites', 'can_review_join_requests', 'can_decline_join_requests'))
    caps["can_manage"] = any(caps[key] for key in (
        "can_invite_members", "can_remove_members", "can_update_realm", "can_update_media", "can_archive_community_posts", "can_assign_roles", "can_manage_roles", "can_manage_permissions",
        "can_review_posts", "can_review_invites", "can_review_join_requests", "can_decline_join_requests", "can_view_notifications",
    ))
    return caps
