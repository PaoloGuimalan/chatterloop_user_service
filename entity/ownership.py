from rest_framework.exceptions import PermissionDenied


def assert_owns(request, obj, field="entity_id"):
    """
    Always-on, non-configurable object-ownership check. Raises
    PermissionDenied (DRF -> HTTP 403) unless request.entity matches the
    object's owning entity FK. Deliberately NOT part of the EntityPermission
    grant/deny system - resource ownership is never "grantable" to another
    entity, it's an intrinsic property of the resource itself.

    Callers must add `except PermissionDenied: raise` before any generic
    `except Exception` clause in the surrounding view method, since
    PermissionDenied is itself an Exception subclass and would otherwise be
    swallowed into a 500 instead of DRF's normal 403 handling.
    """
    entity = getattr(request, "entity", None)
    if entity is None or getattr(obj, field) != entity.id:
        raise PermissionDenied("You do not own this resource.")
