from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from entity.services.allowed_modules import resolve_allowed_modules_and_context


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
    frontend cannot tell from the token alone.

    Login/register/third-party-auth now merge resolve_allowed_modules_and_
    context() directly into their own response instead of requiring this as
    a separate follow-up call - this endpoint remains for session restore
    (AuthCheck) and any future on-demand refresh.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        entity = request.entity
        user = request.user
        try:
            allowed_modules, active_entity = resolve_allowed_modules_and_context(entity, user)

            return Response(
                {
                    "status": True,
                    "result": {
                        "allowed_modules": allowed_modules,
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
