from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.db.models import (
    Q,
    F,
    Exists,
    OuterRef,
    BooleanField,
    Case,
    When,
    Value,
    Subquery,
    Count,
    ExpressionWrapper,
)
from .models import (
    Account,
    Connection,
    Verification,
    Block,
    Report,
    MINIMUM_AGE,
    calculate_age,
)
from .serializers import (
    AccountSerializer,
    ConnectionSerializer,
    AccountSearchSerializer,
)
from .utils.jwt_tools import JWTTools
from .utils.generators import generate_random_digit
from rest_framework.pagination import PageNumberPagination
from django.db import transaction
from user_service.services.redis import RedisPubSubClient
from datetime import datetime
from django.utils.timezone import make_aware
from django.utils.timezone import now
from .ext_models.mongomodels import Message, Conversation, Session
from .services.mongohelpers import NotificationService, SessionService
from core.models import TPAuthentication
from .utils.bcrypt_tools import hash_password
from .utils.generators import generate_unique_username
from .utils.external_requests import emailer
from django.shortcuts import get_object_or_404
from django.utils.timezone import localtime
from .utils.user_manipulation import create_user, save_profile_visit
from .utils.consent import (
    get_current_policy_documents,
    get_pending_consents,
    record_consent_acceptance,
)
from .utils.account_deletion import delete_account, DeletionChallenge
from .utils.data_export import export_account_data
from entity.services.blocking import get_blocked_account_ids, is_blocked
from entity.services.reporting import ReportTargetError, create_report
from user_service.services.rabbitmq import RabbitMQClient, Queues
from entity.services.follows import (
    follow_entity,
    purge_between,
    get_profile_relationship_state,
    link_follows_for_connection,
)
from newsfeed.services.post_visibility import apply_profile_privacy_to_posts
from entity.services.realtime import publish_profile_relationship_update
from community.models import Realm, Member, Follow, Invite
from entity.models import Entity, EntityType
from entity.permissions import Permission, MemberRole
from entity.drf_permissions import RequiresPermission
from entity.services.allowed_modules import resolve_allowed_modules_and_context
from entity.utils import (
    get_entity_display_username,
    get_entity_profile_path,
    entity_side_is_visible,
)
from community.annotations import my_role_annotation
from community.serializers import RealmSerializer
import bcrypt
import uuid
import logging

logger = logging.getLogger(__name__)

jwt = JWTTools


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class UserAuthentication(APIView):

    def get_permissions(self):
        if self.request.method == "GET":
            # Require IsAuthenticated only if x-access-token header exists
            if self.request.headers.get("x-access-token"):
                return [IsAuthenticated()]
            return [AllowAny()]

        if self.request.method == "POST":
            return [AllowAny()]

        return super().get_permissions()

    def get_authenticators(self):
        """Disable authentication for POST, but allow it for GET when x-access-token exists"""
        if self.request.method == "POST":
            return []

        if self.request.method == "GET":
            # Keep authenticators if x-access-token header exists, otherwise disable
            if self.request.headers.get("x-access-token"):
                return super().get_authenticators()
            return []

        return super().get_authenticators()

    def get(self, request, username=None):
        me = self.request.user
        entity = getattr(self.request, "entity", None)
        # user = get_object_or_404(Account, username=username)
        user_queryset = Account.objects.filter(
            username=username, is_active=True, is_verified=True, user_type="user"
        )
        user = None
        transaction_type = request.query_params.get("type", None)

        if len(user_queryset) > 0:
            user = user_queryset[0]

            if isinstance(entity, Entity) and is_blocked(entity, user.entity):
                return Response(
                    {"message": "Profile not available"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # NOTE: a "single" connection is stored as two mirrored rows sharing
            # the same connection_id (one per direction), so the Q() below
            # matches both. Order by action_date so we deterministically land
            # on the row created first, i.e. the actual requester's row —
            # otherwise which row comes back is arbitrary and
            # is_user_connection_initiator flips depending on query order.

            state = get_profile_relationship_state(entity, user.entity)

            is_connection_present = (
                None
                if username == self.request.user.username
                else state["is_connection_present"]
            )
            is_connection_handshaked = state["is_connection_handshaked"]
            is_user_connection_initiator = state["is_user_connection_initiator"]
            connection_id = state["connection_id"]
            is_follower = state["is_follower"]
            is_follow_pending = state["is_follow_pending"]
            can_view = state["can_view"]

            # Fields withheld when can_view is False, below. Everything
            # outside this block is the profile HEADER - name, photos,
            # gender, join date - which stays visible so a locked profile is
            # still identifiable enough to send a request to.
            # START: private data block
            email = user.email

            # Format birthdate parts
            birthdate = user.birthdate

            if birthdate:
                birth_month = birthdate.strftime("%B")  # Full month name
                birth_day = str(birthdate.day)
                birth_year = str(birthdate.year)

            final_birthdate = (
                {
                    "month": birth_month,
                    "day": birth_day,
                    "year": birth_year,
                }
                if birthdate
                else None
            )

            # END: private data block

            # Format dateCreated parts (local timestamp)
            date_created = localtime(user.date_created)
            date_str = date_created.strftime("%m/%d/%Y")
            time_str = date_created.strftime("%I:%M:%S %p").lower()

            if entity:
                save_profile_visit(entity, user.entity.id, "profile")
                RabbitMQClient.publish_on_commit(
                    Queues.INTERACTION_SCORE_BUMP,
                    {
                        "actor_id": entity.id,
                        "receiver_id": user.entity.id,
                        "action": "PROFILE_VISIT",
                        "is_decrease": False,
                    },
                )

            if not can_view:
                email = "..."
                final_birthdate = None

            # Build response JSON matching your example
            data = {
                "data": {
                    "fullname": {
                        "firstName": user.first_name,
                        "middleName": user.middle_name,
                        "lastName": user.last_name,
                    },
                    "birthdate": final_birthdate,
                    "dateCreated": {
                        "date": date_str,
                        "time": time_str,
                    },
                    "connection": {
                        "connection_id": connection_id,
                        "is_connection_present": is_connection_present,
                        "is_connection_handshaked": is_connection_handshaked,
                        "is_user_connection_initiator": is_user_connection_initiator,
                    },
                    "is_follower": is_follower,
                    # Drives the "Requested" button state - the follow edge
                    # exists but is waiting on this profile's approval.
                    "is_follow_pending": is_follow_pending,
                    "id": str(user.id),
                    "entityID": str(user.entity.id),
                    "userID": user.username,
                    "profile": user.profile,
                    "coverphoto": user.coverphoto,
                    "gender": (
                        user.gender.title() if user.gender else None
                    ),  # Capitalize first letter, e.g. "Male"
                    "email": email,
                    "isActivated": user.is_active,
                    "isVerified": user.is_verified,
                    "isBadged": user.is_badged,
                    "isPrivate": user.is_private,
                    "canView": can_view,
                    "type": "user",
                }
            }

            return Response(data, status=status.HTTP_200_OK)

        else:
            query_filter = None

            if transaction_type and transaction_type == "manage":
                query_filter = Q(realm_id=username)
            else:
                query_filter = Q(slug=username)

            realm_queryset = get_object_or_404(
                Realm.objects.annotate(
                    followers_count=Count("entity__followers"),
                    # A page's own entity can never appear as a Member row of
                    # its own realm (Member rows only ever represent personal
                    # accounts) - so while switched to act as this exact page,
                    # `entity` IS the realm's own entity and the Member-based
                    # Exists() below would always miss. `Q(entity=entity)`
                    # covers that self-administration case directly.
                    is_admin=ExpressionWrapper(
                        Q(
                            Exists(
                                Member.objects.filter(
                                    Q(Q(role="admin") | Q(role="owner")),
                                    realm=OuterRef("pk"),
                                    entity=entity,
                                )
                            )
                        )
                        | Q(entity=entity),
                        output_field=BooleanField(),
                    ),
                    is_member=ExpressionWrapper(
                        Q(
                            Exists(
                                Member.objects.filter(
                                    realm=OuterRef("pk"), entity=entity
                                )
                            )
                        )
                        | Q(entity=entity),
                        output_field=BooleanField(),
                    ),
                    is_follower=Exists(
                        Follow.objects.filter(
                            followee=OuterRef("entity_id"), follower=entity
                        )
                    ),
                    my_role=my_role_annotation(entity),
                ),
                query_filter,
            )

            if entity:
                save_profile_visit(entity, realm_queryset.entity.id, "realm")
                RabbitMQClient.publish_on_commit(
                    Queues.FOLLOWER_INTERACTION_SCORE_BUMP,
                    {
                        "actor_id": entity.id,
                        "receiver_id": realm_queryset.entity.id,
                        "action": "PROFILE_VISIT",
                        "is_decrease": False,
                    },
                )

            # Connection state for the realm, mirroring the `connection` block
            # the user branch returns. A Connection is entity<->entity, so a
            # page can be a contact - this is what lets the realm profile
            # render Add / Pending / Connected instead of nothing.
            realm_connection_id = None
            realm_is_connected = None
            realm_is_initiator = None
            realm_has_connection = False

            if isinstance(entity, Entity) and realm_queryset.entity_id != entity.id:
                realm_connection = (
                    Connection.objects.filter(
                        Q(action_by=realm_queryset.entity, involved_entity=entity)
                        | Q(action_by=entity, involved_entity=realm_queryset.entity),
                        ~Q(action_by=F("involved_entity")),
                    )
                    .order_by("action_date")
                    .first()
                )

                if realm_connection:
                    realm_has_connection = True
                    realm_connection_id = realm_connection.connection_id
                    realm_is_connected = realm_connection.status
                    realm_is_initiator = realm_connection.action_by_id == entity.id

            serialized_realm = RealmSerializer(realm_queryset)
            data = {
                "data": {
                    **serialized_realm.data,
                    "connection": {
                        "connection_id": realm_connection_id,
                        "is_connection_present": realm_has_connection,
                        "is_connection_handshaked": realm_is_connected,
                        "is_user_connection_initiator": realm_is_initiator,
                    },
                }
            }

            return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        try:
            email_username = request.data.get("email_username")
            password = request.data.get("password")
            device_token = request.headers.get("device-token")

            if not device_token:
                return Response(
                    {
                        "status": False,
                        "message": "Device unrecognized",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            if not email_username or not password:
                return Response(
                    {
                        "status": False,
                        "message": "Email or Password is missing",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            user = Account.objects.get(
                Q(Q(email=email_username) | Q(username=email_username)),
                join_type="system",
            )

            if user:
                hashed = user.password.encode("utf-8")
                bytes_password = password.encode("utf-8")
                is_correct = bcrypt.checkpw(bytes_password, hashed)

                if is_correct:

                    serialized_user = AccountSerializer(user)

                    session = SessionService()

                    if not session.exists(device_token, user.entity.id):
                        session.add_session(request, user.entity.id, device_token)

                    # A fresh login is always the personal entity (never a
                    # page) - merged directly into this response so the
                    # frontend doesn't need a separate follow-up call to
                    # /api/entity/me/modules just to learn allowed_modules/
                    # active_entity, same data MyAllowedModules resolves.
                    allowed_modules, active_entity = (
                        resolve_allowed_modules_and_context(user.entity, user)
                    )

                    return Response(
                        {
                            "status": True,
                            "result": {
                                "usertoken": jwt.encoder(
                                    {
                                        **serialized_user.data,
                                        "entity_id": user.entity.id,
                                    }
                                ),
                                "authtoken": jwt.encoder(
                                    {
                                        "userID": str(user.id),
                                        "username": user.username,
                                        "entity": str(user.entity.id),
                                    }
                                ),
                                "allowed_modules": allowed_modules,
                                "active_entity": active_entity,
                                "personal_entity_id": str(user.entity.id),
                            },
                        },
                        status=status.HTTP_200_OK,
                    )

                return Response(
                    {
                        "status": False,
                        "message": "Incorrect email, username, or password",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            else:
                return Response(
                    {
                        "status": False,
                        "message": "Incorrect email, username, or password",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )
        except Exception as e:
            logger.warning("UserAuthentication.post rejected the request: %s", e)
            return Response(
                {"status": False, "message": f"{e}"},
                status=status.HTTP_401_UNAUTHORIZED,
            )


class ThirdPartyAuthentication(APIView):

    def get_permissions(self):
        if self.request.method in ["POST"]:
            return [AllowAny()]
        return super().get_permissions()

    def get_authenticators(self):
        """Disable authentication completely for GET and POST requests"""
        if self.request.method in ["POST"]:
            return (
                []
            )  # Returns an empty list, skipping your AuthenticationBackend completely
        return super().get_authenticators()

    def post(self, request):
        try:
            token = request.data.get("token")
            device_token = request.headers.get("device-token")

            decoded_token = JWTTools.decoder(token, options={"verify_signature": False})

            if decoded_token:
                authorization_token = decoded_token["azp"]

                tp_check_query = TPAuthentication.objects.get(
                    service_id=authorization_token
                )

                if tp_check_query:
                    email = decoded_token["email"]
                    user = Account.objects.filter(email=email)

                    session = SessionService()

                    if len(user) > 0:
                        user = user[0]
                        serialized_user = AccountSerializer(user)

                        if not session.exists(device_token, user.entity.id):
                            session.add_session(request, user.entity.id, device_token)

                        allowed_modules, active_entity = (
                            resolve_allowed_modules_and_context(user.entity, user)
                        )

                        return Response(
                            {
                                "status": True,
                                "result": {
                                    "usertoken": jwt.encoder(
                                        {
                                            **serialized_user.data,
                                            "entity_id": str(user.entity.id),
                                        }
                                    ),
                                    "authtoken": jwt.encoder(
                                        {
                                            "userID": str(user.id),
                                            "username": user.username,
                                            "entity": str(user.entity.id),
                                        }
                                    ),
                                    "allowed_modules": allowed_modules,
                                    "active_entity": active_entity,
                                    "personal_entity_id": str(user.entity.id),
                                },
                            },
                            status=status.HTTP_200_OK,
                        )
                    else:
                        # Automatic registration

                        first_name = decoded_token["given_name"]
                        middle_name = "N/A"
                        last_name = decoded_token.get("family_name", None)
                        email = decoded_token["email"]

                        if last_name is None:
                            # Google omits `family_name` for single-name
                            # (mononym) profiles, and for some accounts packs
                            # the full name into `given_name`. Split off the
                            # LAST word as the surname so multi-word given
                            # names survive ("Juan Miguel Dela Cruz" ->
                            # "Juan Miguel Dela" / "Cruz"). A one-word name has
                            # no surname to take at all - fall back to the same
                            # "N/A" sentinel create_user() uses for
                            # middle_name, since last_name is non-null.
                            split_name = first_name.strip().rsplit(" ", 1)

                            if len(split_name) == 2:
                                first_name, last_name = split_name
                            else:
                                last_name = "N/A"

                        create_user_query = create_user(
                            first_name,
                            middle_name,
                            last_name,
                            email,
                            email,
                            None,
                            None,
                            None,
                            None,
                            "google",
                            # Google's OAuth flow already establishes email
                            # ownership - no separate code-verification step
                            # for this path, unlike manual registration.
                            True,
                        )

                        if create_user_query:
                            serialized_user = AccountSerializer(create_user_query)

                            if not session.exists(
                                device_token, create_user_query.entity.id
                            ):
                                session.add_session(
                                    request, create_user_query.entity.id, device_token
                                )

                            allowed_modules, active_entity = (
                                resolve_allowed_modules_and_context(
                                    create_user_query.entity, create_user_query
                                )
                            )

                            return Response(
                                {
                                    "status": True,
                                    "result": {
                                        "usertoken": jwt.encoder(
                                            {
                                                **serialized_user.data,
                                                "entity_id": str(
                                                    create_user_query.entity.id
                                                ),
                                            }
                                        ),
                                        "authtoken": jwt.encoder(
                                            {
                                                "userID": str(create_user_query.id),
                                                "username": create_user_query.username,
                                                "entity": str(
                                                    create_user_query.entity.id
                                                ),
                                            }
                                        ),
                                        "allowed_modules": allowed_modules,
                                        "active_entity": active_entity,
                                        "personal_entity_id": str(
                                            create_user_query.entity.id
                                        ),
                                    },
                                },
                                status=status.HTTP_200_OK,
                            )

                return Response(
                    {"status": False, "message": f"User cannot be logged in"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            return Response(
                {"status": False, "message": f"Cannot proceed with login"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except Exception as e:
            logger.exception("Third-party authentication failed")
            return Response(
                {"status": False, "message": f"{e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UserContacts(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get_permissions(self):
        if self.request.method == "POST":
            return [
                IsAuthenticated(),
                RequiresPermission(Permission.CONTACTS_REQUEST_CREATE)(),
            ]
        return super().get_permissions()

    def get(self, request):
        user = self.request.user
        entity = self.request.entity
        try:
            search = request.query_params.get("search", None)
            paginated_header = request.headers.get("paginated", "true")

            # 1. OPTIMIZED BASE QUERY
            # entity_side_is_visible(): each side must be an active+verified
            # user OR an active realm. `users`/`realms` are reverse OneToOne
            # accessors on Entity, so each is at most one row - no fan-out.
            # select_related (not prefetch) on the reverse OneToOnes - they are
            # 1:1, so this is a plain LEFT JOIN and keeps EntitySerializer's
            # get_details() from issuing a query per row for either branch.
            queryset = (
                Connection.objects.filter(
                    Q(action_by=entity)
                    | Q(
                        involved_entity=entity
                    ),  # Re-added your original target entity filter
                    entity_side_is_visible("action_by"),
                    entity_side_is_visible("involved_entity"),
                    status=True,
                )
                .exclude(action_by=F("involved_entity"))
                .select_related(
                    "action_by",
                    "action_by__users",
                    "action_by__realms",
                    "involved_entity",
                    "involved_entity__users",
                    "involved_entity__realms",
                )
            )

            # --- SEARCH EXTENSION ---
            # Mirrors each user-only lookup with its realm equivalent so a page
            # is findable by the same query the client already sends:
            # "@handle" -> username or realm slug, plain text -> person name or
            # realm name.
            if search:
                if search.startswith("@"):
                    domain = search[1:]
                    queryset = queryset.filter(
                        Q(involved_entity__users__username__icontains=domain)
                        | Q(involved_entity__realms__slug__icontains=domain)
                        | Q(involved_entity__bots__handle__icontains=domain)
                    )
                else:
                    queryset = queryset.filter(
                        Q(involved_entity__users__first_name__icontains=search)
                        | Q(involved_entity__users__middle_name__icontains=search)
                        | Q(involved_entity__users__last_name__icontains=search)
                        | Q(involved_entity__realms__name__icontains=search)
                        | Q(involved_entity__bots__name__icontains=search)
                    )

            # --- CRITICAL FIX: COLLAPSE DUPLICATES BEFORE ORDERING ---
            # .distinct() cleans up database duplicates caused by the user connection tables
            queryset = queryset.distinct().order_by("-action_date")

            if paginated_header == "true":
                paginator = self.pagination_class()
                paginated_queryset = paginator.paginate_queryset(
                    queryset, request, view=self
                )
                serialized_result = ConnectionSerializer(paginated_queryset, many=True)
                return paginator.get_paginated_response(serialized_result.data)
            else:
                serialized_result = ConnectionSerializer(queryset, many=True)
                return Response(serialized_result.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception("UserContacts.get failed")
            return Response(
                {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    def _resolve_target_entity(raw_id):
        """
        Resolve a contact target to its Entity.

        A Connection is entity<->entity, so an entity id is the canonical
        input and is tried FIRST - clients now send entity ids everywhere, so
        the backend never has to translate. Account and Realm ids are still
        accepted so older clients (and any mobile build not yet updated) keep
        working. Returns None when nothing matches.
        """
        if not raw_id:
            return None

        # Canonical path: the id IS an entity id.
        entity = Entity.objects.filter(id=raw_id).first()
        if entity:
            return entity

        # Legacy: an Account pk. uuid() raises on a realm's 15-digit id, so
        # guard rather than letting it 500.
        try:
            return Account.objects.get(id=uuid.UUID(str(raw_id))).entity
        except (ValueError, TypeError, AttributeError, Account.DoesNotExist):
            pass

        # Legacy: a Realm pk / realm_id.
        realm = Realm.objects.filter(Q(realm_id=raw_id) | Q(id=raw_id)).first()
        if realm:
            return realm.entity

        return None

    def post(self, request):
        user = self.request.user  # This is an Account
        entity = self.request.entity  # This is the current user's Entity

        try:
            # entity_id is the canonical field; addUsername is the legacy key
            # (older clients send an account id under it). Either resolves.
            addUsername = request.data.get("entity_id") or request.data.get(
                "addUsername"
            )
            if not addUsername:
                return Response(
                    {"status": False, "message": "Target user ID required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            target_entity = self._resolve_target_entity(addUsername)
            if target_entity is None:
                return Response(
                    {"status": False, "message": "Target not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if target_entity.id == entity.id:
                return Response(
                    {"status": False, "message": "You cannot add yourself."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # MongoDB aggregation pipeline for existing conversations
            users = [target_entity.id, entity.id]  # entity based
            # pipeline = [
            #     {"$match": {"conversationType": "single"}},
            #     {"$match": {"$expr": {"$setEquals": ["$participant_ids", users]}}},
            #     {"$group": {"_id": "$conversationID"}},
            #     {"$project": {"_id": 0, "conversationID": "$_id"}},
            # ]

            # existing_connection_id = Conversation._get_collection().aggregate(pipeline)

            existing_connection = (
                Conversation.objects(
                    conversationType="single",
                    participant_ids__all=[str(u) for u in users],
                )
                .only("conversationID")
                .first()
            )

            new_connection_id = (
                existing_connection.conversationID
                if existing_connection
                else generate_random_digit(20)
            )

            # target_entity was already resolved above (users OR realms).

            if is_blocked(entity, target_entity):
                return Response(
                    {"status": False, "message": "You cannot contact this user"},
                    status=status.HTTP_403_FORBIDDEN,
                )

            with transaction.atomic():
                # First side of the interaction pair: Current User -> Target User
                conn1 = Connection(
                    connection_id=new_connection_id,
                    action_by=entity,  # Pass Entity instance
                    involved_entity=target_entity,  # Pass Target Entity instance
                    nickname=None,
                    type="single",
                    status=False,
                )
                conn1.save()  # Triggers full validation via custom clean()

                # Reciprocal side of the interaction pair: Target User -> Current User
                conn2 = Connection(
                    connection_id=new_connection_id,
                    action_by=target_entity,  # Target Entity acts as initiator
                    involved_entity=entity,  # Current user becomes involved
                    nickname=None,
                    type="single",
                    status=False,
                )
                conn2.save()

                # Reaching out implies interest, so the requester starts
                # following the target immediately - before, and regardless
                # of, any accept. This also seeds the requester's feed with
                # the target's posts (the feed is keyed on the follow graph),
                # so there is something to see while the request is pending.
                # Deliberately one-directional: the target has not agreed to
                # anything yet, so nothing is created on their side.
                #
                # Against a PRIVATE target the follow is parked as pending
                # and seeds nothing. That is the point: this auto-follow used
                # to hand out access to a private profile - and backfill its
                # posts into the requester's feed - on the strength of an
                # unanswered contact request. The connection accept below is
                # what promotes it (link_follows_for_connection).
                follow_entity(entity, target_entity)

                # Notification Saving and relay

                service = NotificationService()
                service.add_notification(
                    referenceID=new_connection_id,
                    referenceStatus=False,
                    toUserID=target_entity.id,
                    fromUserID=entity.id,
                    content_headline="Contact Request",
                    content_details=f"{get_entity_display_username(entity)} have sent a contact request for you.",
                    type="contact_request",
                    isRead=False,
                )

                sse_sendToUser = target_entity.id
                sse_sendToDetails = f"{get_entity_display_username(entity)} have sent a contact request for you."

                now = datetime.now()
                data = {
                    "logType": None,
                    "pod": "podless",
                    "event": "notifications",
                    "message": {
                        "status": True,
                        "auth": True,
                        "message": sse_sendToDetails,
                        "result": "",
                    },
                    "dateTime": now.isoformat(),
                }

                RedisPubSubClient.publish_json_on_commit(
                    f"events_{sse_sendToUser}", data
                )

                # The TARGET may be sitting on the requester's profile, where
                # the button still offers "Add Contact" and there is now an
                # incoming request to answer instead. Subject is the
                # requester, since that is whose profile went stale for them.
                publish_profile_relationship_update(
                    target_entity.id, entity.id, "contact_request_received"
                )

                # Only personal accounts get the email - a page has no inbox
                # to notify (Realm.email is optional and is not a user's
                # address). `users` is the reverse OneToOne from Account, so
                # this is None when the target is a realm.
                target_account = getattr(target_entity, "users", None)
                if target_account is not None and target_account.email:
                    emailer.send_contact_request_notification(
                        to_email=target_account.email,
                        from_entity_id=entity.id,
                        to_entity_id=target_entity.id,
                        from_username=get_entity_profile_path(entity),
                    )

            return Response(
                {
                    "status": True,
                    # Resolves to "@username" for a person and "@slug" for a
                    # page - already carries the "@", so none is added here.
                    "message": (
                        "You have sent a contact request to "
                        f"{get_entity_display_username(target_entity)}"
                    ),
                    "connection_id": new_connection_id,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("UserContacts.post failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def put(self, request):
        user = self.request.user
        entity = self.request.entity
        try:
            connection_id = request.data.get("connection_id")
            # Canonically an ENTITY id. Only used to route the SSE publish -
            # the actual participants are derived from the connection rows
            # below, which overwrite this with entity ids anyway.
            to_user_id = request.data.get("entity_id") or request.data.get("to_user_id")
            now = datetime.now()

            with transaction.atomic():
                existing_connection_query = Connection.objects.filter(
                    Q(~Q(action_by=entity), involved_entity=entity),
                    connection_id=connection_id,
                )

                other_users = []

                if existing_connection_query.exists():
                    to_update_query = Connection.objects.filter(
                        connection_id=connection_id,
                    )

                    for conn in existing_connection_query:
                        if conn.action_by != entity:
                            other_users.append(conn.action_by)
                            to_user_id = conn.action_by.id
                        if conn.involved_entity != entity:
                            other_users.append(conn.involved_entity)
                            to_user_id = conn.involved_entity.id

                    # Remove duplicates if needed
                    other_users = list(set(other_users))

                    to_update_query.update(status=True)

                    service = NotificationService()
                    updated = service.update_reference_status(connection_id, True)

                    if updated:

                        # Either side of a connection can be a page now, and
                        # connection_count / email only exist on Account.
                        # filter().first() instead of get() so a realm side is
                        # a no-op rather than Account.DoesNotExist.
                        accepter_update = (
                            Account.objects.select_for_update()
                            .filter(entity_id=entity.id)
                            .first()
                        )
                        if accepter_update:
                            accepter_update.connection_count += 1
                            accepter_update.save()

                        for other_user in other_users:
                            acceptee_update = (
                                Account.objects.select_for_update()
                                .filter(entity_id=other_user.id)
                                .first()
                            )
                            if acceptee_update:
                                acceptee_update.connection_count += 1
                                acceptee_update.save()

                            # Accepting implies interest in return, so the
                            # accepter now follows the requester - and the
                            # requester's own follow, which may still be
                            # sitting pending because the accepter is
                            # private, is approved at the same time.
                            # Accepting a contact request already answers the
                            # question a follow request asks, so making them
                            # approve it twice would be pure friction (and
                            # would leave a new connection with an empty
                            # feed). Backfills both buckets itself - which is
                            # why the explicit symmetric
                            # backfill_new_friend_feed() calls that used to
                            # live here are gone: the feed is driven by the
                            # follow graph now, not by connections.
                            link_follows_for_connection(entity, other_user)

                            # That may have auto-approved a follow request
                            # this person had open against us, so settle its
                            # notification too - otherwise it keeps offering
                            # Confirm/Decline for something already granted.
                            service.update_reference_status_by_type(
                                str(other_user.id),
                                "follow_request",
                                True,
                                to_user_id=entity.id,
                            )

                            if acceptee_update and acceptee_update.email:
                                emailer.send_contact_accepted_email(
                                    to_email=acceptee_update.email,
                                    from_entity_id=entity.id,
                                    to_entity_id=other_user.id,
                                    from_username=get_entity_profile_path(entity),
                                )

                        notifHeadline = "Accepted Request"
                        notifContent = f"{get_entity_display_username(entity)} accepted your request"

                        service = NotificationService()
                        service.add_notification(
                            referenceID=connection_id,
                            referenceStatus=True,
                            toUserID=to_user_id,
                            fromUserID=entity.id,
                            content_headline=notifHeadline,
                            content_details=notifContent,
                            type="info_contact_accept",
                            isRead=False,
                        )

                        data = {
                            "logType": None,
                            "pod": "podless",
                            "event": "notifications",
                            "message": {
                                "status": True,
                                "auth": True,
                                "message": notifContent,
                                "result": "",
                            },
                            "dateTime": now.isoformat(),
                        }

                        data_reload = {
                            "logType": None,
                            "pod": "podless",
                            "event": "notifications_reload",
                            "message": {
                                "status": True,
                                "auth": True,
                                "message": "",
                                "result": "",
                            },
                            "dateTime": now.isoformat(),
                        }

                        RedisPubSubClient.publish_json_on_commit(
                            f"events_{entity.id}", data_reload
                        )
                        RedisPubSubClient.publish_json_on_commit(
                            f"events_{to_user_id}", data
                        )
                    else:
                        return Response(
                            {"message": "Notification Error has occured"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        )
                else:
                    return Response(
                        {"message": "You are not allowed to approve connection"},
                        status=status.HTTP_401_UNAUTHORIZED,
                    )

                # `result` names the OTHER party from the recipient's point
                # of view, so a client can tell whether the profile it has
                # open is the one that changed. Built per recipient rather
                # than shared, because "the other party" differs for each.
                def contactslist_payload(subject_entity_id):
                    return {
                        "logType": None,
                        "pod": "podless",
                        "event": "contactslist",
                        "message": {
                            "status": True,
                            "auth": True,
                            "result": {"entity_id": str(subject_entity_id)},
                        },
                        "dateTime": now.isoformat(),
                    }

                # To the accepter: the subject is the requester. To each
                # requester: the subject is the accepter.
                RedisPubSubClient.publish_json_on_commit(
                    f"events_{entity.id}", contactslist_payload(to_user_id)
                )

                for other in other_users:
                    RedisPubSubClient.publish_json_on_commit(
                        f"events_{other.id}", contactslist_payload(entity.id)
                    )

                    # The requester may still be on the accepter's profile,
                    # where the button reads "Pending" and - if the accepter
                    # is private - the feed is empty. Both just changed.
                    publish_profile_relationship_update(
                        other.id, entity.id, "contact_request_accepted"
                    )

                return Response(
                    {"status": True, "message": "Contact has been accepted"},
                    status=status.HTTP_200_OK,
                )
        except Exception as e:
            logger.exception("UserContacts.put failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request):
        user = self.request.user
        entity = self.request.entity
        try:
            connection_id = request.data.get("connection_id")
            # Canonically an ENTITY id. Only used to route the SSE publish -
            # the actual participants are derived from the connection rows
            # below, which overwrite this with entity ids anyway.
            to_user_id = request.data.get("entity_id") or request.data.get("to_user_id")
            action = request.headers.get("action")
            now = datetime.now()

            with transaction.atomic():
                existing_connection_query = Connection.objects.filter(
                    Q(Q(action_by=entity) | Q(involved_entity=entity)),
                    type="single",
                    connection_id=connection_id,
                )

                other_users = []

                if existing_connection_query.exists():
                    for conn in existing_connection_query:
                        if conn.action_by != entity:
                            other_users.append(conn.action_by)
                            to_entity_id = conn.action_by.id
                        if conn.involved_entity != entity:
                            other_users.append(conn.involved_entity)
                            to_entity_id = conn.involved_entity.id

                    # Remove duplicates if needed
                    other_users = list(set(other_users))
                    delete_query = Connection.objects.filter(
                        type="single",
                        connection_id=connection_id,
                    )
                    delete_query.delete()

                    service = NotificationService()
                    updated = service.update_reference_status(connection_id, True)

                    if updated and not action == "decline":
                        # Same as the accept path: connection_count lives on
                        # Account only, so a page side is skipped rather than
                        # raising Account.DoesNotExist.
                        accepter_update = (
                            Account.objects.select_for_update()
                            .filter(entity_id=entity.id)
                            .first()
                        )
                        if accepter_update:
                            accepter_update.connection_count -= 1
                            accepter_update.save()

                        for other_user in other_users:
                            acceptee_update = (
                                Account.objects.select_for_update()
                                .filter(entity_id=other_user.id)
                                .first()
                            )
                            if acceptee_update:
                                acceptee_update.connection_count -= 1
                                acceptee_update.save()

                    if updated and action == "decline":
                        notifHeadline = "Declined Request"
                        notifContent = f"{get_entity_display_username(entity)} declined your request"

                        service = NotificationService()
                        service.add_notification(
                            referenceID=connection_id,
                            referenceStatus=True,
                            toUserID=to_entity_id,
                            fromUserID=entity.id,
                            content_headline=notifHeadline,
                            content_details=notifContent,
                            type="info_contact_decline",
                            isRead=False,
                        )

                        data = {
                            "logType": None,
                            "pod": "podless",
                            "event": "notifications",
                            "message": {
                                "status": True,
                                "auth": True,
                                "message": notifContent,
                                "result": "",
                            },
                            "dateTime": now.isoformat(),
                        }

                        data_reload = {
                            "logType": None,
                            "pod": "podless",
                            "event": "notifications_reload",
                            "message": {
                                "status": True,
                                "auth": True,
                                "message": "",
                                "result": "",
                            },
                            "dateTime": now.isoformat(),
                        }

                        RedisPubSubClient.publish_json_on_commit(
                            f"events_{entity.id}", data_reload
                        )
                        RedisPubSubClient.publish_json_on_commit(
                            f"events_{to_entity_id}", data
                        )
                    else:
                        if action == "decline":
                            return Response(
                                {"message": "Notification Error has occured"},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            )
                else:
                    return Response(
                        {"message": "You are not allowed to remove this connection"},
                        status=status.HTTP_401_UNAUTHORIZED,
                    )

                # See the accept path: addressed per recipient so an open
                # profile page can tell whether it is the one affected.
                def contactslist_payload(subject_entity_id):
                    return {
                        "logType": None,
                        "pod": "podless",
                        "event": "contactslist",
                        "message": {
                            "status": True,
                            "auth": True,
                            "result": {"entity_id": str(subject_entity_id)},
                        },
                        "dateTime": now.isoformat(),
                    }

                for other in other_users:
                    RedisPubSubClient.publish_json_on_commit(
                        f"events_{entity.id}", contactslist_payload(other.id)
                    )
                    RedisPubSubClient.publish_json_on_commit(
                        f"events_{other.id}", contactslist_payload(entity.id)
                    )

                message_response = (
                    "You have successfully removed connection"
                    if action == "remove"
                    else "You declined a connection request"
                )

                # Removing a connection PURGES the relationship in both
                # directions: the follows that connecting created (the
                # requester's at request time, the accepter's at accept time)
                # go too, along with each side's feed bucket of the other's
                # posts. Deliberately different from unfollowing, which is
                # one-directional and leaves the connection intact.
                #
                # Applies to "decline" as well as "remove": both delete the
                # connection rows, so both should leave no follow behind -
                # otherwise a declined request would strand the requester
                # still following someone who rejected them.
                #
                # purge_between clears each side's feed bucket of the other's
                # posts UNCONDITIONALLY, not just when a follow row happened
                # to exist - someone may have unfollowed manually earlier,
                # leaving no edge while the posts it seeded still sit in the
                # bucket.
                for other in other_users:
                    purge_between(entity, other)

                # Published only once the teardown above is DONE. This signal
                # makes the counterpart's open profile page refetch, so it
                # has to come after both the connection rows and the follow
                # edges are gone - firing it earlier raced purge_between and
                # served a page that had lost its posts but still showed
                # "Following".
                #
                # Covers decline AND remove: either way the counterpart's
                # view of this profile (button state, and access if this
                # profile is private) is now wrong.
                for other in other_users:
                    publish_profile_relationship_update(
                        other.id, entity.id, f"contact_{action}"
                    )

                return Response(
                    {"message": message_response}, status=status.HTTP_200_OK
                )
        except Exception as e:
            logger.exception("UserContacts.delete failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UserSearch(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request, query):
        user = self.request.user
        entity = self.request.entity
        try:
            # 1. Keep this as a standard QuerySet (Do NOT wrap in Exists yet)
            # 2. Change OuterRef("pk") to OuterRef("entity_id") since Connection uses Entity IDs
            base_connection_qs = Connection.objects.filter(
                Q(action_by=entity, involved_entity=OuterRef("entity_id"))
                | Q(action_by=OuterRef("entity_id"), involved_entity=entity),
                ~Q(action_by=F("involved_entity")),
                # Entity-generic: while acting as a page the old user-only
                # form matched nothing, so every result came back as "New"
                # even where a connection existed.
                entity_side_is_visible("action_by"),
                entity_side_is_visible("involved_entity"),
            )

            # Drive specialized sub-filters off the base QuerySet safely
            connection_active_qs = base_connection_qs.filter(status=True)
            connection_id_subquery = base_connection_qs.values("connection_id")[:1]
            # Who actually initiated: the action_by of the EARLIEST of the two
            # mirrored rows a connection is stored as. The previous
            # Exists(filter(action_by=entity)) was ALWAYS true - both
            # directions exist - so every result claimed the viewer was the
            # requester, showing Accept/Decline to the wrong side.
            connection_initiator_subquery = base_connection_qs.order_by(
                "action_date"
            ).values("action_by_id")[:1]

            blocked_account_ids = get_blocked_account_ids(entity)

            # Determine filter keyword based on prefix
            if query.startswith("@"):
                domain = query.split("@")[1]
                search_filter = Q(username__icontains=domain)
            else:
                search_filter = (
                    Q(first_name__icontains=query)
                    | Q(middle_name__icontains=query)
                    | Q(last_name__icontains=query)
                )

            # Build unified QuerySet execution
            users_qs = (
                Account.objects.filter(
                    search_filter,
                    ~Q(id=user.id),
                    ~Q(entity_id__in=blocked_account_ids),
                    is_active=True,
                    is_verified=True,
                )
                .annotate(
                    has_connection=Exists(base_connection_qs),
                    connection_accomplished=Case(
                        When(Exists(connection_active_qs), then=Value(True)),
                        default=Value(False),
                        output_field=BooleanField(),
                    ),
                    connection_id=Subquery(connection_id_subquery),
                    connection_initiator_id=Subquery(connection_initiator_subquery),
                )
                .annotate(
                    # Chained so it can reference the annotation above.
                    is_action_by_entity=Case(
                        When(connection_initiator_id=str(entity.id), then=Value(True)),
                        default=Value(False),
                        output_field=BooleanField(),
                    ),
                )
            )

            # Paginate and serialize output records
            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                users_qs, request, view=self
            )

            serialized_result = AccountSearchSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data

        except Exception as e:
            logger.exception("UserSearch.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UserAccountManagement(APIView):

    def get_permissions(self):
        if self.request.method in ["POST"]:
            return [AllowAny()]
        return super().get_permissions()

    def get_authenticators(self):
        """Disable authentication completely for GET and POST requests"""
        if self.request.method in ["POST"]:
            return (
                []
            )  # Returns an empty list, skipping your AuthenticationBackend completely
        return super().get_authenticators()

    def post(self, request):
        try:
            data = request.data
            first_name = data.get("firstName")
            last_name = data.get("lastName")
            email = data.get("email")
            raw_password = data.get("password")
            gender = data.get("gender")
            agreed_to_terms = data.get("agreedToTerms")
            device_token = request.headers.get("device-token")

            if not gender or not str(gender).strip():
                return Response(
                    {"status": False, "message": "Gender is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not agreed_to_terms:
                return Response(
                    {
                        "status": False,
                        "message": "You must agree to the Terms and Conditions",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                birthday = int(data.get("birthday"))  # int day
                birthmonth = int(data.get("birthmonth"))  # int month
                birthyear = int(data.get("birthyear"))  # int year
                birthdate_naive = datetime(birthyear, birthmonth, birthday)
            except (TypeError, ValueError):
                return Response(
                    {"status": False, "message": "A valid birthdate is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            birthdate = make_aware(birthdate_naive)

            age = calculate_age(birthdate)
            if age is None or age < MINIMUM_AGE:
                return Response(
                    {
                        "status": False,
                        "message": f"You must be at least {MINIMUM_AGE} years old to use Chatterloop",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            middle_name = request.data.get("middleName")
            if not middle_name or middle_name.strip() == "":
                middle_name = "N/A"
            else:
                middle_name = middle_name.strip()

            if Account.objects.filter(email=email).exists():
                return Response(
                    {"status": False, "message": "Email already in use"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            gender = gender.lower()

            new_user = create_user(
                first_name=first_name,
                middle_name=middle_name,
                last_name=last_name,
                email=email,
                raw_password=raw_password,
                birthday=birthday,
                birthmonth=birthmonth,
                birthyear=birthyear,
                gender=gender,
                join_type="system",
                # Manual registration must go through CodeVerification - the
                # email below is that code, and is_verified only flips True
                # once the correct code is submitted.
                is_verified=False,
            )

            session = SessionService()

            if not session.exists(device_token, new_user.entity.id):
                session.add_session(request, new_user.entity.id, device_token)

            record_consent_acceptance(
                new_user.entity,
                ["terms", "privacy"],
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.headers.get("User-Agent"),
            )

            emailer.send_email_verification_code(
                to_email=email,
                subject="Verification Code",
                user_id=new_user.username,
                body=None,
            )

            serialized_user = AccountSerializer(new_user)

            # A fresh registration is always the personal entity - merged
            # directly into this response so the frontend doesn't need a
            # separate follow-up call to /api/entity/me/modules.
            allowed_modules, active_entity = resolve_allowed_modules_and_context(
                new_user.entity, new_user
            )

            return Response(
                {
                    "status": True,
                    "message": "Account created",
                    "username": new_user.username,
                    "authtoken": jwt.encoder(
                        {
                            "userID": str(new_user.id),
                            "username": new_user.username,
                            "entity": str(new_user.entity.id),
                        }
                    ),
                    "usertoken": jwt.encoder(
                        {
                            **serialized_user.data,
                            "entity_id": str(new_user.entity.id),
                        }
                    ),
                    "allowed_modules": allowed_modules,
                    "active_entity": active_entity,
                    "personal_entity_id": str(new_user.entity.id),
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            logger.exception("UserAccountManagement.post failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def put(self, request):
        try:
            data = request.data
            account = self.request.user

            editable_fields = [
                "first_name",
                "middle_name",
                "last_name",
                "birthdate",
                "profile",
                "coverphoto",
                "gender",
                "email",
                "username",
                # Settings > Data and Privacy > Private profile.
                "is_private",
            ]

            # Captured BEFORE the setattr loop below: the side effects only
            # fire on an actual transition, and after the loop the old value
            # is gone.
            was_private = account.is_private

            if "birthdate" in data and data["birthdate"]:
                from django.utils.dateparse import parse_datetime

                parsed_birthdate = parse_datetime(str(data["birthdate"]))
                if parsed_birthdate is None:
                    return Response(
                        {"status": False, "message": "Invalid birthdate format"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                age = calculate_age(parsed_birthdate)
                if age is None or age < MINIMUM_AGE:
                    return Response(
                        {
                            "status": False,
                            "message": f"You must be at least {MINIMUM_AGE} years old to use Chatterloop",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            if "is_private" in data and not isinstance(data["is_private"], bool):
                return Response(
                    {"status": False, "message": "is_private must be a boolean"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            for field in editable_fields:
                if field in data:
                    setattr(account, field, data[field])

            narrowed_posts = 0

            # Both the save and the post rewrite land together - a partial
            # apply would leave the profile private while its back catalogue
            # is still public, which is the exact state the toggle exists to
            # prevent.
            with transaction.atomic():
                account.save()

                if not was_private and account.is_private:
                    # Going private narrows the existing PUBLIC back
                    # catalogue to connections-only. Going public again does
                    # NOT widen it back - see apply_profile_privacy_to_posts
                    # for why that asymmetry is deliberate.
                    narrowed_posts = apply_profile_privacy_to_posts(account.entity)

            return Response(
                {
                    "status": True,
                    "message": "Account updated successfully",
                    # Surfaced so Settings can tell the user what the toggle
                    # actually did to their existing posts instead of leaving
                    # a silent bulk rewrite.
                    "posts_restricted": narrowed_posts,
                    "data": AccountSerializer(account).data,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.exception("UserAccountManagement.put failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def delete(self, request):
        try:
            account = self.request.user
            entity = self.request.entity
            delete_account(account, entity)

            return Response(
                {"status": True, "message": "Account deleted"},
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("UserAccountManagement.delete failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AccountDataExport(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            account = self.request.user
            entity = self.request.entity
            return Response(
                {"status": True, "data": export_account_data(account, entity)},
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("AccountDataExport.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PolicyDocumentList(APIView):
    permission_classes = [AllowAny]

    def get_authenticators(self):
        return []

    def get(self, request):
        try:
            current_docs = get_current_policy_documents()
            data = [
                {
                    "document_type": doc.document_type,
                    "version": doc.version,
                    "content": doc.content,
                    "document_url": doc.document_url,
                    "effective_date": doc.effective_date,
                }
                for doc in current_docs.values()
            ]
            return Response(
                {"status": True, "data": data},
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("PolicyDocumentList.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PolicyConsentAccept(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            account = self.request.user
            entity = self.request.user.entity
            document_types = request.data.get("document_types")

            if not document_types:
                document_types = [
                    pending["document_type"] for pending in get_pending_consents(entity)
                ]

            record_consent_acceptance(
                entity,
                document_types,
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.headers.get("User-Agent"),
            )

            return Response(
                {
                    "status": True,
                    "message": "Consent recorded",
                    "data": AccountSerializer(account).data,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("PolicyConsentAccept.post failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CodeVerification(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = self.request.user
        verification_code = request.data.get("code")  # directly take code from request

        if not verification_code:
            return Response(
                {"status": False, "message": "Verification code is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            ver = Verification.objects.filter(
                user=user, ver_code=verification_code, is_used=False
            ).first()
            if ver:
                ver.is_used = True
                ver.save()

                user = ver.user
                user.is_verified = True
                user.save()

                return Response(
                    {"status": True, "message": "Account has been verified"},
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(
                    {
                        "status": False,
                        "message": "Invalid or already used verification code",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except Exception as e:
            logger.exception("CodeVerification.post failed")
            return Response(
                {"status": False, "message": f"Error verifying code: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PublicAccountDeletion(APIView):
    """
    Self-service account deletion for people who cannot sign in - the flow an
    app store requires to be reachable without the app.

    Three steps, all unauthenticated:

        POST .../deletion/lookup   {email}         -> found? + profile card
        POST .../deletion/request  {email}         -> emails a code
        POST .../deletion/confirm  {email, code}   -> deleted

    NOTE ON THE LOOKUP STEP. It reports whether an address is registered and
    returns the profile behind it, which makes this page an address checker:
    anyone can confirm a given person has a Chatterloop account and see their
    name and photo, with no proof they own it. That is a deliberate product
    decision - the alternative is answering identically either way and showing
    the account only after the code - and it is mitigated, not solved, by
    DeletionChallenge.can_lookup capping checks per IP.

    Nothing is DELETED without the emailed code, so the destructive half is
    still gated on control of the mailbox.
    """

    permission_classes = [AllowAny]

    def get_authenticators(self):
        return []

    @staticmethod
    def _normalize(email):
        return str(email or "").strip().lower()

    @staticmethod
    def _find(email):
        """The live account for an address, or None. Already-deleted accounts
        carry an anonymised @chatterloop.invalid address and never match."""
        if not email:
            return None
        return Account.objects.filter(email__iexact=email, is_active=True).first()

    @staticmethod
    def _client_ip(request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    def post(self, request, step=None):
        if step == "lookup":
            return self._lookup(request)
        if step == "request":
            return self._request_code(request)
        if step == "confirm":
            return self._confirm(request)

        return Response(
            {"status": False, "message": "Unknown deletion step"},
            status=status.HTTP_404_NOT_FOUND,
        )

    # ── step 1: find the account ─────────────────────────────────────────

    def _lookup(self, request):
        email = self._normalize(request.data.get("email"))

        if not email or "@" not in email:
            return Response(
                {"status": False, "message": "A valid email address is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not DeletionChallenge.can_lookup(self._client_ip(request)):
            return Response(
                {
                    "status": False,
                    "message": (
                        "Too many lookups from this connection. Try again later."
                    ),
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        account = self._find(email)

        if account is None:
            return Response(
                {
                    "status": True,
                    "found": False,
                    "message": "No account is registered to that email address.",
                },
                status=status.HTTP_200_OK,
            )

        # Enough to recognise the account, and no more - this is a confirmation
        # card, not a profile view.
        return Response(
            {
                "status": True,
                "found": True,
                "account": {
                    "username": account.username,
                    "first_name": account.first_name,
                    "last_name": account.last_name,
                    "profile": account.profile,
                    "date_created": account.date_created,
                },
            },
            status=status.HTTP_200_OK,
        )

    # ── step 2: email the code ───────────────────────────────────────────

    def _request_code(self, request):
        email = self._normalize(request.data.get("email"))
        account = self._find(email) if email else None

        if account is None:
            return Response(
                {
                    "status": False,
                    "message": "No account is registered to that email address.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if not DeletionChallenge.can_send(email):
            return Response(
                {
                    "status": False,
                    "message": "A code was just sent. Wait a moment before retrying.",
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        code = DeletionChallenge.issue_code(email)
        if code is None:
            return Response(
                {
                    "status": False,
                    "message": "Unable to start deletion right now, please retry",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        emailer.send_account_deletion_code(to_email=account.email, code=code)

        return Response(
            {
                "status": True,
                "message": f"We've sent a confirmation code to {account.email}.",
            },
            status=status.HTTP_200_OK,
        )

    # ── step 3: check the code and delete ────────────────────────────────

    def _confirm(self, request):
        email = self._normalize(request.data.get("email"))
        code = str(request.data.get("code") or "").strip()

        invalid = Response(
            {"status": False, "message": "That code is not valid or has expired"},
            status=status.HTTP_400_BAD_REQUEST,
        )

        if not email or not code:
            return invalid

        # Counted before the comparison, so every guess costs an attempt
        # whether or not the address is right.
        if not DeletionChallenge.register_attempt(email):
            return Response(
                {
                    "status": False,
                    "message": (
                        "Too many incorrect attempts. Request a new code to "
                        "try again."
                    ),
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        if not DeletionChallenge.check_code(email, code):
            return invalid

        account = self._find(email)
        if account is None:
            return invalid

        # Cleared BEFORE the delete: the code is single-use, and a retry after a
        # partial failure must not be able to run the teardown twice.
        DeletionChallenge.clear_code(email)

        try:
            delete_account(account, account.entity)
        except Exception as e:
            logger.exception("PublicAccountDeletion._confirm failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"status": True, "message": "Account deleted"},
            status=status.HTTP_200_OK,
        )


class ResendVerificationCode(APIView):
    """
    Issues a fresh signup verification code.

    Authenticated: registration already returns a token, so someone sitting on
    the verify screen is signed in - they are just not is_verified yet. That
    means the address is taken from the account rather than the request, so this
    cannot be pointed at a mailbox the caller does not own.

    Rate-limited to one code a minute per account, reusing the same Redis
    cooldown the notification emails use. Without it the button is an open
    relay: one click per second, to an address someone else may have typed
    during signup.
    """

    permission_classes = [IsAuthenticated]

    RESEND_COOLDOWN = 60

    def post(self, request):
        account = self.request.user

        if account.is_verified:
            return Response(
                {"status": False, "message": "This account is already verified."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not RedisPubSubClient.acquire_email_cooldown(
            "verification_resend",
            account.id,
            account.id,
            ttl=self.RESEND_COOLDOWN,
        ):
            return Response(
                {
                    "status": False,
                    "message": (
                        "A code was just sent. Give it a moment before asking "
                        "for another."
                    ),
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            # Retire outstanding codes first. CodeVerification accepts ANY
            # unused row for the account, so without this every code ever sent
            # stays valid forever and "resend" quietly widens the guessing
            # surface instead of replacing the code.
            Verification.objects.filter(user=account, is_used=False).update(
                is_used=True
            )

            emailer.send_email_verification_code(
                to_email=account.email,
                user_id=account.username,
            )

            return Response(
                {
                    "status": True,
                    "message": f"A new code is on its way to {account.email}.",
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("ResendVerificationCode.post failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BlockedUserList(APIView):
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _serialize_blocked(block):
        """One blocked row, whichever kind of entity it points at.

        A page and a person carry different name fields, so both are flattened
        onto the same shape the clients already read (username / first_name /
        last_name / profile) and `entityType` says which it really is. Falling
        back rather than raising matters here: a blocked entity whose account
        or realm row has since gone is still a block the user must be able to
        see and lift.
        """
        blocked = block.blocked
        base = {
            "entityID": blocked.id,
            "entityType": blocked.type,
            "created_at": block.created_at,
        }

        realm = getattr(blocked, "realms", None)
        if realm is not None:
            return {
                **base,
                "id": realm.id,
                "username": realm.slug or realm.name,
                "first_name": realm.name,
                "last_name": "",
                "profile": realm.profile,
                "realmType": realm.type,
            }

        account = getattr(blocked, "users", None)
        if account is not None:
            return {
                **base,
                "id": account.id,
                "username": account.username,
                "first_name": account.first_name,
                "last_name": account.last_name,
                "profile": account.profile,
            }

        return {
            **base,
            "id": blocked.id,
            "username": "",
            "first_name": "Unavailable",
            "last_name": "",
            "profile": "none",
        }

    def get(self, request):
        try:
            entity = self.request.entity
            blocks = Block.objects.filter(blocker=entity).select_related(
                "blocked", "blocked__users", "blocked__realms"
            )
            data = [self._serialize_blocked(block) for block in blocks]
            return Response({"status": True, "data": data}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("BlockedUserList.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request):
        try:
            account = self.request.user
            entity = self.request.entity
            target_id = request.data.get("entityID")

            if not target_id:
                return Response(
                    {"status": False, "message": "entityID is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if str(target_id) == str(entity.id):
                return Response(
                    {"status": False, "message": "You cannot block yourself"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            target = get_object_or_404(Entity, id=target_id)

            with transaction.atomic():
                Block.objects.get_or_create(blocker=entity, blocked=target)

                Connection.objects.filter(
                    Q(action_by=entity, involved_entity=target)
                    | Q(action_by=target, involved_entity=entity)
                ).delete()

                Invite.objects.filter(
                    Q(created_by=entity, target_entity=target)
                    | Q(created_by=target, target_entity=entity)
                ).delete()

                # Follow edges are the relationship that actually matters for
                # pages: blocking one while still following it would leave its
                # posts in the feed, which is the one outcome a block must not
                # produce. purge_between clears both directions and the seeded
                # feed rows.
                purge_between(entity, target)

            return Response(
                {
                    "status": True,
                    "message": (
                        "Page blocked"
                        if target.type == EntityType.REALM_CHOICE
                        else "User blocked"
                    ),
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.exception("BlockedUserList.post failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def delete(self, request):
        try:
            account = self.request.user
            entity = self.request.entity
            target_id = request.data.get("entityID")

            if not target_id:
                return Response(
                    {"status": False, "message": "entityID is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            Block.objects.filter(blocker=entity, blocked_id=target_id).delete()

            return Response(
                {"status": True, "message": "User unblocked"},
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("BlockedUserList.delete failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeviceSessionList(APIView):
    permission_classes = [IsAuthenticated]

    def _allowed_entity_ids(self, entity):
        """Personal entity + every page/realm entity this account can
        switch into - mirrors EntitySwitch's own admin/owner membership
        check, so a revoke can never reach an entity this device could not
        have legitimately held a session for."""
        page_entity_ids = Member.objects.filter(
            entity=entity, role__in=[MemberRole.OWNER, MemberRole.ADMIN]
        ).values_list("realm__entity_id", flat=True)
        return [str(entity.id), *[str(e) for e in page_entity_ids]]

    def get(self, request):
        try:
            entity = self.request.entity
            device_token = request.headers.get("device-token")

            session = SessionService()
            sessions = session.list_for_entity(entity.id)

            data = [
                {
                    "sessionID": s.sessionID,
                    "deviceType": s.deviceType,
                    "browser": s.browser,
                    "os": s.os,
                    "ip": s.ip,
                    "status": s.status,
                    "lastSeen": s.lastSeen,
                    "is_current_device": s.deviceToken == device_token,
                }
                for s in sessions
            ]
            return Response({"status": True, "data": data}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("DeviceSessionList.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def delete(self, request):
        try:
            entity = self.request.entity
            session_id = request.data.get("sessionID")

            if not session_id:
                return Response(
                    {"status": False, "message": "sessionID is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            allowed_ids = self._allowed_entity_ids(entity)

            target_session = Session.objects(
                sessionID=session_id, entityID__in=allowed_ids
            ).first()

            if target_session is None:
                return Response(
                    {"status": False, "message": "Session not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            session = SessionService()
            session.revoke_device(target_session.deviceToken, allowed_ids)

            return Response(
                {"status": True, "message": "Device signed out"},
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("DeviceSessionList.delete failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PokeUser(APIView):
    permission_classes = [IsAuthenticated, RequiresPermission(Permission.POKE_CREATE)]

    def post(self, request):
        user = self.request.user
        entity = self.request.entity
        try:
            target_id = request.data.get("target_id")
            if not target_id:
                return Response(
                    {"status": False, "message": "target_id is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            target_account = get_object_or_404(Account, id=target_id)
            target_entity = target_account.entity

            if target_entity == entity:
                return Response(
                    {"status": False, "message": "You cannot poke yourself"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if is_blocked(entity, target_entity):
                return Response(
                    {"status": False, "message": "You cannot poke this user"},
                    status=status.HTTP_403_FORBIDDEN,
                )

            is_connected = Connection.objects.filter(
                Q(action_by=entity, involved_entity=target_entity)
                | Q(action_by=target_entity, involved_entity=entity),
                type="single",
                status=True,
            ).exists()

            if not is_connected:
                return Response(
                    {
                        "status": False,
                        "message": "You can only poke users you are connected with",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            poke_reference_id = f"POKE_{generate_random_digit(20)}"
            poke_message = f"{get_entity_display_username(entity)} poked you."

            service = NotificationService()
            service.add_notification(
                referenceID=poke_reference_id,
                referenceStatus=True,
                toUserID=target_entity.id,
                fromUserID=entity.id,
                content_headline="New Poke",
                content_details=poke_message,
                type="poke",
                isRead=False,
            )

            data = {
                "logType": None,
                "pod": "podless",
                "event": "notifications",
                "message": {
                    "status": True,
                    "auth": True,
                    "message": poke_message,
                    "result": "",
                },
                "dateTime": datetime.now().isoformat(),
            }
            RedisPubSubClient.publish_json(f"events_{target_entity.id}", data)

            emailer.send_poke_notification_email(
                to_email=target_account.email,
                from_entity_id=entity.id,
                to_entity_id=target_entity.id,
                from_username=get_entity_profile_path(entity),
            )

            return Response(
                {
                    "status": True,
                    "message": f"You poked @{target_account.username}",
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("PokeUser.post failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ReportCreate(APIView):
    """POST /api/user/reports - file a report against any entity or one piece
    of its content.

    Target resolution lives in entity.services.reporting so the same rules
    apply wherever a report can be filed from: whatever the target_type, the
    report is stored against the responsible Entity.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """The reason list, so clients stop hardcoding their own copy."""
        return Response(
            {
                "status": True,
                "data": {
                    "target_types": [t for t, _ in Report.TARGET_TYPE_CHOICES],
                    "reasons": [
                        {"value": value, "label": label}
                        for value, label in Report.REASON_CHOICES
                    ],
                },
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        try:
            entity = self.request.entity
            data = request.data

            try:
                report, created = create_report(
                    reporter_entity=entity,
                    target_type=data.get("target_type"),
                    target_id=data.get("target_id"),
                    reason=data.get("reason"),
                    description=data.get("description", ""),
                )
            except ReportTargetError as e:
                return Response(
                    {"status": False, "message": e.message},
                    status=(
                        status.HTTP_404_NOT_FOUND
                        if e.not_found
                        else status.HTTP_400_BAD_REQUEST
                    ),
                )

            return Response(
                {
                    "status": True,
                    "message": (
                        "Report submitted"
                        if created
                        else "You have already reported this"
                    ),
                    "data": {
                        "id": report.id,
                        "target_type": report.target_type,
                        "target_id": report.target_id,
                        "created": created,
                    },
                },
                status=(
                    status.HTTP_201_CREATED if created else status.HTTP_200_OK
                ),
            )
        except Exception as e:
            logger.exception("ReportCreate.post failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
