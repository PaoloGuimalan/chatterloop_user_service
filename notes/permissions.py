from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from entity.permissions import Permission
from entity.services.permission_resolver import has_permission
from entity.services.acting import can_act_as_entity


def require_notes_access(request):
    if not getattr(request.user, "is_authenticated", False):
        raise NotAuthenticated()
    entity = getattr(request, "entity", None)
    if entity is None or not can_act_as_entity(request.user, entity) or not has_permission(entity, Permission.MODULE_NOTES_ACCESS):
        raise PermissionDenied("Notes are not available in this entity context")
    return entity


def can_manage_note(request, note) -> bool:
    entity = getattr(request, "entity", None)
    return bool(getattr(request.user, "is_authenticated", False) and entity
                and str(entity.id) == str(note.entity_id)
                and can_act_as_entity(request.user, entity)
                and has_permission(entity, Permission.MODULE_NOTES_ACCESS))


def can_read_note(request, note) -> bool:
    return not note.is_private or can_manage_note(request, note)


class OptionalNotesAuthentication:
    """Public reads have no auth requirements; supplied tokens still use full platform auth."""
    def authenticate(self, request):
        if not request.headers.get("x-access-token"):
            return None
        from user.backends import AutheticationBackend
        return AutheticationBackend().authenticate(request)

    def authenticate_header(self, request):
        return "Bearer"
