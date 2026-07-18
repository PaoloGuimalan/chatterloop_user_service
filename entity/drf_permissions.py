from rest_framework.permissions import BasePermission

from entity.services.permission_resolver import has_permission


def RequiresPermission(permission, realm_lookup=None):
    """
    Factory returning a BasePermission subclass gating on a single catalog
    permission string.

    `realm_lookup`: optional callable(request, view) -> Realm | None. Only
    needed for realm-scoped permissions where the realm is resolvable from
    the request itself (query params / body) before any object fetch. Where
    the realm is only known after fetching the primary object inside the
    view body, call has_permission(...) directly inline instead of using
    this declarative wrapper.
    """

    class _RequiresPermission(BasePermission):
        message = f"You do not have permission to perform this action ({permission})."

        def has_permission(self, request, view):
            entity = getattr(request, "entity", None)
            realm = realm_lookup(request, view) if realm_lookup else None
            return has_permission(entity, permission, realm=realm)

    _RequiresPermission.__name__ = f"RequiresPermission_{permission.replace('.', '_')}"
    return _RequiresPermission


class IsResourceOwner(BasePermission):
    """
    Object-level permission: the request's active entity must match the
    resource's `entity` FK. NOTE: DRF only invokes has_object_permission()
    via check_object_permissions(), which plain APIView subclasses that
    fetch objects manually (as every view in this codebase does today) never
    call automatically. Provided for completeness/future generic-view use;
    the actually-wired ownership mechanism is entity.ownership.assert_owns().
    """

    message = "You do not own this resource."

    def has_object_permission(self, request, view, obj):
        entity = getattr(request, "entity", None)
        if entity is None:
            return False
        return obj.entity_id == entity.id
