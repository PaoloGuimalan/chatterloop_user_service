from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from community.models import Realm, Member
from entity.permissions import MemberRole
from .utils.jwt_tools import JWTTools
from .services.mongohelpers import SessionService

jwt = JWTTools


def _active_entity_payload(realm=None, personal_entity_id=None):
    """
    Shape shared by both switch endpoints' responses. `realm=None` means
    "switched back to the personal user entity".
    """
    if realm is None:
        return {
            "id": str(personal_entity_id),
            "type": "user",
            "realm_type": None,
            "name": None,
            "slug": None,
        }
    return {
        "id": str(realm.entity_id),
        "type": "realm",
        "realm_type": realm.type,
        "name": realm.name,
        "slug": realm.slug,
    }


class EntitySwitch(APIView):
    """
    Switches the active acting entity for this account from its personal
    entity to a page it administers - re-issues the authtoken with a
    different `entity` claim (userID stays the same account, only `entity`
    changes), so everything downstream that reads request.entity (post
    authorship, comments, permission checks - both here and in the Node
    `server` repo, which shares this JWT_SECRET) attributes actions to the
    page instead of the personal user.

    Direct page-to-page switching is allowed: membership is validated
    against the account's own personal entity (request.user.entity), not
    whatever entity happens to be currently active, so this works whether
    you're currently acting as yourself or already acting as another page.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        try:
            realm_id = request.data.get("realm_id")
            device_token = request.headers.get("device-token")

            if not realm_id:
                return Response(
                    {"status": False, "message": "realm_id is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            realm = get_object_or_404(Realm, id=realm_id)

            if realm.type != "page":
                return Response(
                    {
                        "status": False,
                        "message": "Only page realms support entity switching.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            member = Member.objects.filter(
                entity=user.entity, realm=realm
            ).first()

            if member is None or member.role not in (
                MemberRole.OWNER,
                MemberRole.ADMIN,
            ):
                return Response(
                    {
                        "status": False,
                        "message": "You are not allowed to act as this page.",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            session = SessionService()
            if not session.exists(device_token, realm.entity_id):
                session.add_session(request, realm.entity_id, device_token)

            new_token = jwt.encoder(
                {
                    "userID": str(user.id),
                    "username": user.username,
                    "entity": str(realm.entity_id),
                }
            )

            return Response(
                {
                    "status": True,
                    "result": {
                        "authtoken": new_token,
                        "active_entity": _active_entity_payload(realm=realm),
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


class EntitySwitchBack(APIView):
    """Switches back to the account's own personal entity."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        entity = request.entity
        try:
            device_token = request.headers.get("device-token")

            if entity is not None and entity.id == user.entity.id:
                return Response(
                    {"status": False, "message": "You are already acting as yourself."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            session = SessionService()
            if not session.exists(device_token, user.entity.id):
                session.add_session(request, user.entity.id, device_token)

            new_token = jwt.encoder(
                {
                    "userID": str(user.id),
                    "username": user.username,
                    "entity": str(user.entity.id),
                }
            )

            return Response(
                {
                    "status": True,
                    "result": {
                        "authtoken": new_token,
                        "active_entity": _active_entity_payload(
                            realm=None, personal_entity_id=user.entity.id
                        ),
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
