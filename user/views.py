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
from .utils.account_deletion import delete_account
from .utils.data_export import export_account_data
from .utils.blocking import get_blocked_account_ids, is_blocked
from newsfeed.helpers.query_functions import (
    interaction_score_bump,
    follower_interaction_score_bump,
    remove_feed_on_unfriend,
    backfill_new_friend_feed,
)
from community.models import Realm, Member, Follow, Invite
from entity.models import Entity
from entity.permissions import Permission, MemberRole
from entity.drf_permissions import RequiresPermission
from entity.services.allowed_modules import resolve_allowed_modules_and_context
from entity.utils import (
    get_entity_display_username,
    get_entity_profile_path,
    entity_side_is_visible,
)
from community.serializers import RealmSerializer
import bcrypt
import uuid

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

            connection_exists = (
                Connection.objects.select_related("action_by", "involved_entity")
                .filter(
                    Q(action_by=user.entity, involved_entity=entity)
                    | Q(action_by=entity, involved_entity=user.entity),
                    ~Q(action_by=F("involved_entity")),
                    # Each side is valid as an active+verified USER or an
                    # active REALM. The old user-only predicate matched
                    # nothing while acting as a page, so a real user<->page
                    # connection reported is_connection_present=false and the
                    # page saw "Add Contact" for an existing contact.
                    entity_side_is_visible("action_by"),
                    entity_side_is_visible("involved_entity"),
                    # status=True,
                )
                .order_by("action_date")
            )

            is_connection_present = (
                None
                if username == self.request.user.username
                else True if len(connection_exists) >= 1 else False
            )

            is_connection_handshaked = None
            is_user_connection_initiator = None
            connection_id = None

            if connection_exists:
                # 1. Fetch the first connection record from the queryset
                connection_record = connection_exists[0]

                connection_id = connection_record.connection_id
                is_connection_handshaked = connection_record.status

                # "Did the ACTING entity initiate this?" - compared on entity
                # ids so it is valid whether acting as a person or a page.
                # (Was comparing action_by.users.username to the human's
                # username, which is always False when acting as a page, since
                # a realm entity has no Account.)
                is_user_connection_initiator = (
                    connection_record.action_by_id == entity.id
                    if isinstance(entity, Entity)
                    else False
                )

            # Following is entity->entity now, so a person can be followed the
            # same way a page can. Drives the Follow button on user profiles.
            is_follower = (
                Follow.objects.filter(follower=entity, followee=user.entity).exists()
                if isinstance(entity, Entity)
                else False
            )

            # Format birthdate parts
            birthdate = user.birthdate

            if birthdate:
                birth_month = birthdate.strftime("%B")  # Full month name
                birth_day = str(birthdate.day)
                birth_year = str(birthdate.year)

            # Format dateCreated parts (local timestamp)
            date_created = localtime(user.date_created)
            date_str = date_created.strftime("%m/%d/%Y")
            time_str = date_created.strftime("%I:%M:%S %p").lower()

            if entity:
                save_profile_visit(entity, user.entity.id, "profile")
                interaction_score_bump(
                    entity.id, user.entity.id, "PROFILE_VISIT", False
                )

            # Build response JSON matching your example
            data = {
                "data": {
                    "fullname": {
                        "firstName": user.first_name,
                        "middleName": user.middle_name,
                        "lastName": user.last_name,
                    },
                    "birthdate": (
                        {
                            "month": birth_month,
                            "day": birth_day,
                            "year": birth_year,
                        }
                        if birthdate
                        else None
                    ),
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
                    "id": str(user.id),
                    "entityID": str(user.entity.id),
                    "userID": user.username,
                    "profile": user.profile,
                    "coverphoto": user.coverphoto,
                    "gender": (
                        user.gender.title() if user.gender else None
                    ),  # Capitalize first letter, e.g. "Male"
                    "email": user.email,
                    "isActivated": user.is_active,
                    "isVerified": user.is_verified,
                    "isBadged": user.is_badged,
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
                ),
                query_filter,
            )

            if entity:
                save_profile_visit(entity, realm_queryset.entity.id, "realm")
                follower_interaction_score_bump(
                    entity.id, realm_queryset.entity.id, "PROFILE_VISIT", False
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
                            split_name = first_name.split(" ")

                            first_name = split_name[0]
                            last_name = split_name[1]

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
            print(e)
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
                    )
                else:
                    queryset = queryset.filter(
                        Q(involved_entity__users__first_name__icontains=search)
                        | Q(involved_entity__users__middle_name__icontains=search)
                        | Q(involved_entity__users__last_name__icontains=search)
                        | Q(involved_entity__realms__name__icontains=search)
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

                RedisPubSubClient.publish_json(f"events_{sse_sendToUser}", data)

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
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def put(self, request):
        user = self.request.user
        entity = self.request.entity
        try:
            connection_id = request.data.get("connection_id")
            # Canonically an ENTITY id. Only used to route the SSE publish -
            # the actual participants are derived from the connection rows
            # below, which overwrite this with entity ids anyway.
            to_user_id = request.data.get("entity_id") or request.data.get(
                "to_user_id"
            )
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

                            # Entity-keyed, so this is correct for both kinds.
                            backfill_new_friend_feed(entity.id, other_user.id)
                            backfill_new_friend_feed(other_user.id, entity.id)

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

                        RedisPubSubClient.publish_json(
                            f"events_{entity.id}", data_reload
                        )
                        RedisPubSubClient.publish_json(f"events_{to_user_id}", data)
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

                data = {
                    "logType": None,
                    "pod": "podless",
                    "event": "contactslist",
                    "message": {"status": True, "auth": True, "result": ""},
                    "dateTime": now.isoformat(),
                }

                RedisPubSubClient.publish_json(f"events_{to_user_id}", data)
                RedisPubSubClient.publish_json(f"events_{entity.id}", data)

                for other in other_users:
                    RedisPubSubClient.publish_json(f"events_{other.id}", data)

                return Response(
                    {"status": True, "message": "Contact has been accepted"},
                    status=status.HTTP_200_OK,
                )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request):
        user = self.request.user
        entity = self.request.entity
        try:
            connection_id = request.data.get("connection_id")
            # Canonically an ENTITY id. Only used to route the SSE publish -
            # the actual participants are derived from the connection rows
            # below, which overwrite this with entity ids anyway.
            to_user_id = request.data.get("entity_id") or request.data.get(
                "to_user_id"
            )
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

                        RedisPubSubClient.publish_json(
                            f"events_{entity.id}", data_reload
                        )
                        RedisPubSubClient.publish_json(f"events_{to_entity_id}", data)
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

                data = {
                    "logType": None,
                    "pod": "podless",
                    "event": "contactslist",
                    "message": {"status": True, "auth": True, "result": ""},
                    "dateTime": now.isoformat(),
                }

                RedisPubSubClient.publish_json(f"events_{entity.id}", data)

                for other in other_users:
                    RedisPubSubClient.publish_json(f"events_{other.id}", data)

                message_response = (
                    "You have successfully removed connection"
                    if action == "remove"
                    else "You declined a connection request"
                )

                remove_feed_on_unfriend(entity.id, to_entity_id)
                remove_feed_on_unfriend(to_entity_id, entity.id)

                return Response(
                    {"message": message_response}, status=status.HTTP_200_OK
                )
        except Exception as e:
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
            users_qs = Account.objects.filter(
                search_filter,
                ~Q(id=user.id),
                ~Q(entity_id__in=blocked_account_ids),
                is_active=True,
                is_verified=True,
            ).annotate(
                has_connection=Exists(base_connection_qs),
                connection_accomplished=Case(
                    When(Exists(connection_active_qs), then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                ),
                connection_id=Subquery(connection_id_subquery),
                connection_initiator_id=Subquery(connection_initiator_subquery),
            ).annotate(
                # Chained so it can reference the annotation above.
                is_action_by_entity=Case(
                    When(connection_initiator_id=str(entity.id), then=Value(True)),
                    default=Value(False),
                    output_field=BooleanField(),
                ),
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
            ]

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

            for field in editable_fields:
                if field in data:
                    setattr(account, field, data[field])

            account.save()

            return Response(
                {
                    "status": True,
                    "message": "Account updated successfully",
                    "data": AccountSerializer(account).data,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
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
            return Response(
                {"status": False, "message": f"Error verifying code: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BlockedUserList(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            account = self.request.user
            entity = self.request.entity
            blocks = Block.objects.filter(blocker=entity).select_related("blocked")
            data = [
                {
                    "id": block.blocked.users.id,
                    "entityID": block.blocked.id,
                    "username": block.blocked.users.username,
                    "first_name": block.blocked.users.first_name,
                    "last_name": block.blocked.users.last_name,
                    "profile": block.blocked.users.profile,
                    "created_at": block.created_at,
                }
                for block in blocks
            ]
            return Response({"status": True, "data": data}, status=status.HTTP_200_OK)
        except Exception as e:
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

            return Response(
                {"status": True, "message": "User blocked"},
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
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
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ReportCreate(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            account = self.request.user
            data = request.data

            target_type = data.get("target_type")
            target_id = data.get("target_id")
            reason = data.get("reason")
            description = data.get("description", "")

            valid_target_types = dict(Report.TARGET_TYPE_CHOICES)
            if target_type not in valid_target_types:
                return Response(
                    {"status": False, "message": "Invalid target_type"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            valid_reasons = dict(Report.REASON_CHOICES)
            if reason not in valid_reasons:
                return Response(
                    {"status": False, "message": "Invalid reason"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if target_type == "user":
                reported_user = get_object_or_404(Account, id=target_id)
                target_id = None
            elif target_type == "post":
                from newsfeed.models import Post

                post = get_object_or_404(Post, post_id=target_id)
                reported_user = post.user
            elif target_type == "comment":
                from newsfeed.models import Comment

                comment = get_object_or_404(Comment, comment_id=target_id)
                reported_user = comment.user
            elif target_type == "message":
                message_doc = Message._get_collection().find_one(
                    {"messageID": target_id}
                )
                if not message_doc:
                    return Response(
                        {"status": False, "message": "Message not found"},
                        status=status.HTTP_404_NOT_FOUND,
                    )
                reported_user = get_object_or_404(Account, id=message_doc["sender"])

            if reported_user.id == account.id:
                return Response(
                    {"status": False, "message": "You cannot report yourself"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            report = Report.objects.create(
                reporter=account,
                reported_user=reported_user,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
                description=description,
            )

            return Response(
                {
                    "status": True,
                    "message": "Report submitted",
                    "data": {"id": report.id},
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
