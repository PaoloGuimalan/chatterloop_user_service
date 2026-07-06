from django.db.models import Q
from django.utils.timezone import now

from entity.models import EntityPermission
from entity.permissions import GLOBAL_PLATFORM_DEFAULT, PermissionEffect
from entity.services.permission_catalog_cache import get_catalog, get_role_matrix


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
      3. Role-derived default (realm-scoped) OR platform default (global-scoped).
      4. Deny-by-default fallback -> False.

    The permission catalog and role-default matrix are database-backed
    (PermissionCatalogEntry, RolePermission) rather than hardcoded Python
    sets, cached via entity/services/permission_catalog_cache.py.
    """
    if entity is None:
        return False

    catalog = get_catalog()
    entry = catalog.get(permission)
    if entry is None or not entry["is_active"]:
        raise ValueError(f"Unknown permission string: {permission!r}")

    is_global = entry["scope"] == "global"
    if is_global and realm is not None:
        raise ValueError(f"{permission!r} is global-scoped and cannot take a realm")
    if not is_global and realm is None:
        raise ValueError(f"{permission!r} is realm-scoped and requires a realm")

    override = _active_override(entity, permission, realm=realm)
    if override is not None:
        return override.effect == PermissionEffect.GRANT

    if realm is not None:
        from community.models import Member  # local import: avoids entity<->community cycle

        member = Member.objects.filter(entity=entity, realm=realm).first()
        if member is None:
            return False
        return permission in get_role_matrix().get(member.role, [])

    default_check = GLOBAL_PLATFORM_DEFAULT.get(permission)
    return bool(default_check(entity)) if default_check else False
