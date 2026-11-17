from django.db.models import Q
from django.utils.timezone import now

from entity.models import EntityPermission
from entity.permissions import GLOBAL_PLATFORM_DEFAULT, PermissionEffect
from entity.roles import realm_owner_role_id
from entity.services.permission_catalog_cache import (
    get_catalog,
    get_entity_type_matrix,
    get_role_matrix,
)


def has_active_permission(entity, permission, realm=None, **kwargs):
    """Domain guards deny disabled capabilities; invalid codes still raise."""
    try:
        return has_permission(entity, permission, realm, **kwargs)
    except ValueError:
        from entity.models import PermissionCatalogEntry

        if PermissionCatalogEntry.objects.filter(codename=permission, is_active=False).exists():
            return False
        raise


def _request_value(context, key, load):
    if context is None:
        return load()
    if key not in context:
        context[key] = load()
    return context[key]


def _active_override(entity, permission, realm=None, context=None):
    """Non-expired EntityPermission row for this exact (entity, permission,
    realm) scope, if one exists."""
    rows = (
        EntityPermission.objects.filter(
            entity=entity, realm=realm,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now()))
    )
    if context is None:
        return rows.filter(permission__codename=permission).first()

    def load():
        overrides = {}
        for row in rows.select_related("permission").order_by("pk"):
            overrides.setdefault(row.permission.codename, row)
        return overrides

    return _request_value(context, ("overrides", entity.pk, getattr(realm, "pk", None)), load).get(permission)


def has_permission(entity, permission, realm=None, *, fresh=False, context=None) -> bool:
    """
    Resolution priority (highest to lowest):
      1. The realm's immutable Owner holds all active realm capabilities.
      2. Explicit EntityPermission deny/grant for the exact scope.
      3. Entity-type default (entity_type-scoped) OR role-derived default
         (realm-scoped) OR platform default (global-scoped).
      4. Deny-by-default fallback -> False.

    The permission catalog, role-default matrix, and entity-type-default
    matrix are database-backed (PermissionCatalogEntry, RolePermission,
    PermissionEntityType) rather than hardcoded Python sets, cached
    via entity/services/permission_catalog_cache.py.
    """
    if entity is None:
        return False

    if fresh:
        # Mutation authorization must never reuse request-local read snapshots.
        context = None
        from entity.models import PermissionCatalogEntry

        catalog = {entry.codename: {"scope": entry.scope, "is_active": entry.is_active}
                   for entry in PermissionCatalogEntry.objects.filter(codename=permission)}
    else:
        catalog = _request_value(context, "catalog", get_catalog)
    entry = catalog.get(permission)
    if entry is None or not entry["is_active"]:
        raise ValueError(f"Unknown permission string: {permission!r}")

    scope = entry["scope"]
    if scope in ("global", "entity_type") and realm is not None:
        raise ValueError(f"{permission!r} is {scope}-scoped and cannot take a realm")
    if scope == "realm" and realm is None:
        raise ValueError(f"{permission!r} is realm-scoped and requires a realm")

    role_id = None
    if realm is not None:
        from community.models import Member
        from entity.models import Role

        def realm_role():
            if getattr(realm, "entity_id", None) == entity.id and realm.type != "community":
                return Role.objects.get(pk=realm_owner_role_id(realm), realm=realm)
            member = Member.objects.select_related("role").filter(entity=entity, realm=realm).first()
            return member.role if member and member.role.realm_id == realm.pk else None

        role = _request_value(context, ("realm_role", entity.pk, realm.pk), realm_role)
        if role is not None:
            role_id = role.pk
            if role.code == "owner" and role.is_default:
                return True

    override = _active_override(entity, permission, realm=realm, context=context)
    if override is not None:
        return override.effect == PermissionEffect.GRANT

    if scope == "entity_type":
        return permission in _request_value(context, "entity_types", get_entity_type_matrix).get(entity.type, [])

    if realm is not None:
        if role_id is None:
            return False
        if fresh:
            from entity.models import RolePermission

            return RolePermission.objects.filter(role_id=role_id, permission__codename=permission, permission__is_active=True).exists()
        return permission in _request_value(
            context, ("roles", realm.pk), lambda: get_role_matrix(realm.pk),
        ).get(str(role_id), [])

    default_check = GLOBAL_PLATFORM_DEFAULT.get(permission)
    return bool(default_check(entity)) if default_check else False
