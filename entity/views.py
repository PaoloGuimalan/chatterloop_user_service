from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from entity.permissions import Permission
from entity.services.permission_resolver import has_permission
from entity.utils import get_entity_display_name, get_entity_profile_path


class MyAllowedModules(APIView):
    """
    Returns which entity-type-scoped module codenames the current active
    entity (request.entity) is allowed to see - the frontend's source of
    truth for which UI modules to show, since only the resolver knows about
    per-entity EntityPermission overrides (e.g. a suspended page individually
    denied a module) that client-side inference from entity.type alone
    would miss.

    Also resolves and returns the active entity's own context (type, display
    name, profile path). This is the only place that CAN reliably answer
    "am I currently acting as myself or as a page" after a page reload -
    the JWT's `entity` claim is an opaque id with no type flag, so the
    frontend cannot tell from the token alone. Called on login/session
    restore and after every switch, so it's the single source of truth for
    both allowed_modules and active_entity_context.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        entity = request.entity
        user = request.user
        try:
            allowed = [
                codename
                for codename in Permission.ENTITY_TYPE_SCOPED
                if has_permission(entity, codename)
            ]

            is_personal = entity.id == user.entity.id
            if is_personal:
                active_entity = {
                    "id": str(entity.id),
                    "type": "user",
                    "realm_type": None,
                    "realm_id": None,
                    "name": None,
                    "slug": None,
                    "profile": user.profile,
                }
            else:
                realm = getattr(entity, "realms", None)
                active_entity = {
                    "id": str(entity.id),
                    "type": "realm",
                    "realm_type": realm.type if realm else None,
                    "realm_id": realm.id if realm else None,
                    "name": get_entity_display_name(entity),
                    "slug": get_entity_profile_path(entity),
                    "profile": realm.profile if realm else None,
                }

            return Response(
                {
                    "status": True,
                    "result": {
                        "allowed_modules": allowed,
                        "active_entity": active_entity,
                        "personal_entity_id": str(user.entity.id),
                    },
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
