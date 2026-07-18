from django.db.models import Q
from django.utils.timezone import now

from entity.models import EntityPermission
from entity.permissions import GLOBAL_PLATFORM_DEFAULT, MemberRole, PermissionEffect
from entity.services.permission_catalog_cache import (
    get_catalog,
    get_entity_type_matrix,
    get_role_matrix,
)


def _active_override(entity, permission, realm=None):
    """Non-expired EntityPermission row for this exact (entity, permission,
    realm) scope, if one exists."""
    return (
        EntityPermission.objects.filter(entity=entity, permission=permission, realm=realm)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now()))
        .first()
    )


def has_permission(entity, permission, realm=None) -> bool:
    """
    Resolution priority (highest to lowest):
      1. Explicit deny (EntityPermission effect=deny, not expired) -> False, always.
      2. Explicit grant (EntityPermission effect=grant, not expired) -> True.
      3. Entity-type default (entity_type-scoped) OR role-derived default
         (realm-scoped) OR platform default (global-scoped).
      4. Deny-by-default fallback -> False.

    The permission catalog, role-default matrix, and entity-type-default
    matrix are database-backed (PermissionCatalogEntry, RolePermission,
    EntityTypeDefaultPermission) rather than hardcoded Python sets, cached
    via entity/services/permission_catalog_cache.py.
    """
    if entity is None:
        return False

    catalog = get_catalog()
    entry = catalog.get(permission)
    if entry is None or not entry["is_active"]:
        raise ValueError(f"Unknown permission string: {permission!r}")

    scope = entry["scope"]
    if scope in ("global", "entity_type") and realm is not None:
        raise ValueError(f"{permission!r} is {scope}-scoped and cannot take a realm")
    if scope == "realm" and realm is None:
        raise ValueError(f"{permission!r} is realm-scoped and requires a realm")

    override = _active_override(entity, permission, realm=realm)
    if override is not None:
        return override.effect == PermissionEffect.GRANT

    if scope == "entity_type":
        return permission in get_entity_type_matrix().get(entity.type, [])

    if realm is not None:
        from community.models import Member  # local import: avoids entity<->community cycle

        # A page's own entity can never appear as a Member of its own realm -
        # Member rows only ever represent personal accounts, never the realm
        # entity itself. So once "acting as" that exact page (entity switch
        # re-issues the JWT's entity claim to the realm's own entity), the
        # membership lookup below would always miss and wrongly deny. Resolve
        # self-administration as owner tier directly instead. This can only
        # match the realm this entity's own id belongs to - a page's entity
        # can never equal a *different* realm's entity - so it can't be used
        # to bypass authorization on any other realm.
        if getattr(realm, "entity_id", None) == entity.id:
            return permission in get_role_matrix().get(MemberRole.OWNER.value, [])

        member = Member.objects.filter(entity=entity, realm=realm).first()
        if member is None:
            return False
        return permission in get_role_matrix().get(member.role, [])

    default_check = GLOBAL_PLATFORM_DEFAULT.get(permission)
    return bool(default_check(entity)) if default_check else False
