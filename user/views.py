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
from .ext_models.mongomodels import Message
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
from community.models import Realm, Member, RealmFollow, Invite
from community.serializers import RealmSerializer
import bcrypt

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
        # user = get_object_or_404(Account, username=username)
        user_queryset = Account.objects.filter(
            username=username, is_active=True, is_verified=True, user_type="user"
        )
        user = None
        transaction_type = request.query_params.get("type", None)

        if len(user_queryset) > 0:
            user = user_queryset[0]

            if isinstance(me, Account) and is_blocked(me, user):
                return Response(
                    {"message": "Profile not available"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            connection_exists = Connection.objects.filter(
                Q(action_by=user, involved_user=self.request.user)
                | Q(action_by=self.request.user, involved_user=user),
                ~Q(action_by=F("involved_user")),
                Q(action_by__is_active=True),
                Q(action_by__is_verified=True),
                Q(involved_user__is_active=True),
                Q(involved_user__is_verified=True),
                # status=True,
            ).distinct("connection_id")

            is_connection_present = (
                None
                if username == self.request.user.username
                else True if len(connection_exists) >= 1 else False
            )

            is_connection_handshaked = None
            is_user_connection_initiator = None
            connection_id = None

            if connection_exists:
                connection_id = connection_exists[0].connection_id
                is_connection_handshaked = connection_exists[0].status
                is_user_connection_initiator = (
                    True
                    if connection_exists[0].action_by.username == username
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

            save_profile_visit(me, user.id, "profile")
            interaction_score_bump(me.id, user.id, "PROFILE_VISIT", False)

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
                    "id": str(user.id),
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
                    followers_count=Count("followers"),
                    is_admin=Exists(
                        Member.objects.filter(
                            realm=OuterRef("pk"), account=me, role="admin"
                        )
                    ),
                    is_member=Exists(
                        Member.objects.filter(realm=OuterRef("pk"), account=me)
                    ),
                    is_follower=Exists(
                        RealmFollow.objects.filter(realm=OuterRef("pk"), follower=me)
                    ),
                ),
                query_filter,
            )

            save_profile_visit(me, realm_queryset.realm_id, "realm")
            follower_interaction_score_bump(
                me.id, realm_queryset.realm_id, "PROFILE_VISIT", False
            )

            serialized_realm = RealmSerializer(realm_queryset)
            data = {"data": {**serialized_realm.data}}

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

                    if not session.exists(device_token, user.id):
                        session.add_session(request, user.id, device_token)

                    return Response(
                        {
                            "status": True,
                            "result": {
                                "usertoken": jwt.encoder(serialized_user.data),
                                "authtoken": jwt.encoder(
                                    {"userID": str(user.id), "username": user.username}
                                ),
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

                    if len(user) > 0:
                        user = user[0]
                        serialized_user = AccountSerializer(user)

                        session = SessionService()

                        if not session.exists(device_token, user.id):
                            session.add_session(request, user.id, device_token)

                        return Response(
                            {
                                "status": True,
                                "result": {
                                    "usertoken": jwt.encoder(serialized_user.data),
                                    "authtoken": jwt.encoder(
                                        {
                                            "userID": str(user.id),
                                            "username": user.username,
                                        }
                                    ),
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
                        )

                        if create_user_query:
                            serialized_user = AccountSerializer(create_user_query)

                            return Response(
                                {
                                    "status": True,
                                    "result": {
                                        "usertoken": jwt.encoder(serialized_user.data),
                                        "authtoken": jwt.encoder(
                                            {
                                                "userID": str(create_user_query.id),
                                                "username": create_user_query.username,
                                            }
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
            print(str(e))
            return Response(
                {"status": False, "message": f"{e}"},
                status=status.HTTP_401_UNAUTHORIZED,
            )


class UserContacts(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user
        try:
            search = request.query_params.get("search", None)
            paginated_header = request.headers.get("paginated", "true")

            queryset = (
                Connection.objects.select_related("action_by", "involved_user").filter(
                    Q(Q(action_by=user) | Q(involved_user=user)),
                    ~Q(action_by=F("involved_user")),
                    Q(action_by__is_active=True),
                    Q(action_by__is_verified=True),
                    Q(involved_user__is_active=True),
                    Q(involved_user__is_verified=True),
                    status=True,
                )
                # .distinct("connection_id")
                # .order_by("connection_id", "-action_date")
                .order_by("-action_date", "connection_id")
            )

            if search:
                if search.startswith("@"):
                    queryset = queryset.filter(
                        Q(involved_user__username__icontains=search)
                    )
                else:
                    queryset = queryset.filter(
                        Q(
                            Q(involved_user__first_name__icontains=search)
                            | Q(involved_user__middle_name__icontains=search)
                            | Q(involved_user__last_name__icontains=search)
                        )
                    )

            if paginated_header == "true":
                paginator = self.pagination_class()
                paginated_queryset = paginator.paginate_queryset(
                    queryset, request, view=self
                )

                serialized_result = ConnectionSerializer(paginated_queryset, many=True)
                data = paginator.get_paginated_response(serialized_result.data)

                return data
            else:
                serialized_result = ConnectionSerializer(queryset, many=True)
                return Response(serialized_result.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def post(self, request):
        user = self.request.user
        try:
            addUsername = request.data.get("addUsername")

            users = [addUsername, user.id]
            pipeline = [
                {"$match": {"conversationType": "single"}},
                {"$match": {"$expr": {"$setEquals": ["$receivers", users]}}},
                {"$group": {"_id": "$conversationID"}},
                {"$project": {"_id": 0, "conversationID": "$_id"}},
            ]
            existing_connection_id = Message._get_collection().aggregate(pipeline)
            conversation_id_list = list(
                set(doc["conversationID"] for doc in existing_connection_id)
            )

            new_connection_id = (
                conversation_id_list[0]
                if len(conversation_id_list) == 1
                else generate_random_digit(20)
            )

            pending_involved_user = Account.objects.get(id=addUsername)

            if is_blocked(user, pending_involved_user):
                return Response(
                    {"status": False, "message": "You cannot contact this user"},
                    status=status.HTTP_403_FORBIDDEN,
                )

            with transaction.atomic():
                # Create first connection row
                conn1 = Connection(
                    connection_id=new_connection_id,
                    action_by=user,
                    involved_user=user,
                    nickname=None,
                    type="single",
                    status=False,
                )
                conn1.save()  # will call clean() and validate

                # Create reciprocal connection row
                conn2 = Connection(
                    connection_id=new_connection_id,
                    action_by=user,
                    involved_user=pending_involved_user,
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
                    toUserID=addUsername,
                    fromUserID=user.id,
                    content_headline="Contact Request",
                    content_details=f"@{user.username} have sent a contact request for you.",
                    type="contact_request",
                    isRead=False,
                )

                sse_sendToUser = addUsername
                sse_sendToDetails = (
                    f"@{user.username} have sent a contact request for you."
                )

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

            return Response(
                {
                    "status": True,
                    "message": f"You have sent a contact request to @{pending_involved_user.username}",
                    "connection_id": new_connection_id,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def put(self, request):
        user = self.request.user
        try:
            connection_id = request.data.get("connection_id")
            to_user_id = request.data.get("to_user_id")
            now = datetime.now()

            with transaction.atomic():
                existing_connection_query = Connection.objects.filter(
                    Q(~Q(action_by=user), involved_user=user),
                    connection_id=connection_id,
                )

                other_users = []

                if existing_connection_query.exists():
                    to_update_query = Connection.objects.filter(
                        connection_id=connection_id,
                    )

                    for conn in existing_connection_query:
                        if conn.action_by != user:
                            other_users.append(conn.action_by)
                        if conn.involved_user != user:
                            other_users.append(conn.involved_user)

                    # Remove duplicates if needed
                    other_users = list(set(other_users))

                    to_update_query.update(status=True)

                    service = NotificationService()
                    updated = service.update_reference_status(connection_id, True)

                    if updated:

                        accepter_update = Account.objects.select_for_update().get(
                            id=user.id
                        )
                        accepter_update.connection_count += 1
                        accepter_update.save()

                        for other_user in other_users:
                            acceptee_update = Account.objects.select_for_update().get(
                                id=other_user.id
                            )
                            acceptee_update.connection_count += 1
                            acceptee_update.save()

                            backfill_new_friend_feed(user.id, other_user.id)
                            backfill_new_friend_feed(other_user.id, user.id)

                        notifHeadline = "Accepted Request"
                        notifContent = f"@{user.username} accepted your request"

                        service = NotificationService()
                        service.add_notification(
                            referenceID=connection_id,
                            referenceStatus=True,
                            toUserID=to_user_id,
                            fromUserID=user.id,
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

                        RedisPubSubClient.publish_json(f"events_{user.id}", data_reload)
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
                RedisPubSubClient.publish_json(f"events_{user.username}", data)

                for other in other_users:
                    RedisPubSubClient.publish_json(f"events_{other.username}", data)

                return Response(
                    {"status": True, "message": "Contact has been accepted"},
                    status=status.HTTP_200_OK,
                )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request):
        user = self.request.user
        try:
            connection_id = request.data.get("connection_id")
            to_user_id = request.data.get("to_user_id")
            action = request.headers.get("action")
            now = datetime.now()

            with transaction.atomic():
                existing_connection_query = Connection.objects.filter(
                    Q(Q(action_by=user) | Q(involved_user=user)),
                    type="single",
                    connection_id=connection_id,
                )

                other_users = []

                if existing_connection_query.exists():
                    for conn in existing_connection_query:
                        if conn.action_by != user:
                            other_users.append(conn.action_by)
                            to_user_id = conn.action_by.id
                        if conn.involved_user != user:
                            other_users.append(conn.involved_user)
                            to_user_id = conn.involved_user.id

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
                        accepter_update = Account.objects.select_for_update().get(
                            id=user.id
                        )
                        accepter_update.connection_count -= 1
                        accepter_update.save()

                        for other_user in other_users:
                            acceptee_update = Account.objects.select_for_update().get(
                                id=other_user.id
                            )
                            acceptee_update.connection_count -= 1
                            acceptee_update.save()

                    if updated and action == "decline":
                        notifHeadline = "Declined Request"
                        notifContent = f"@{user.username} declined your request"

                        service = NotificationService()
                        service.add_notification(
                            referenceID=connection_id,
                            referenceStatus=True,
                            toUserID=to_user_id,
                            fromUserID=user.id,
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

                        RedisPubSubClient.publish_json(f"events_{user.id}", data_reload)
                        RedisPubSubClient.publish_json(f"events_{to_user_id}", data)
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

                RedisPubSubClient.publish_json(f"events_{user.username}", data)

                for other in other_users:
                    RedisPubSubClient.publish_json(f"events_{other.username}", data)

                message_response = (
                    "You have successfully removed connection"
                    if action == "remove"
                    else "You declined a connection request"
                )

                remove_feed_on_unfriend(user.id, to_user_id)
                remove_feed_on_unfriend(to_user_id, user.id)

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
        try:
            connection_exists = Connection.objects.filter(
                Q(action_by=user, involved_user=OuterRef("pk"))
                | Q(action_by=OuterRef("pk"), involved_user=user),
                ~Q(action_by=F("involved_user")),
                Q(action_by__is_active=True),
                Q(action_by__is_verified=True),
                Q(involved_user__is_active=True),
                Q(involved_user__is_verified=True),
                # status=True,
            ).distinct("connection_id")

            connection_action_by = connection_exists.filter(action_by=OuterRef("pk"))
            connection_active = connection_exists.filter(status=True)
            connection_id_subquery = connection_exists.values("connection_id")[:1]
            blocked_account_ids = get_blocked_account_ids(user)

            if query.startswith("@"):
                domain = query.split("@")[1]
                users_qs = Account.objects.filter(
                    ~Q(id=user.id),
                    ~Q(id__in=blocked_account_ids),
                    is_active=True,
                    is_verified=True,
                    username__icontains=domain,  # case-insensitive contains
                ).annotate(
                    has_connection=Exists(connection_exists),
                    connection_accomplished=Case(
                        When(Exists(connection_active), then=Value(True)),
                        default=Value(False),
                        output_field=BooleanField(),
                    ),
                    connection_id=Subquery(connection_id_subquery),
                    is_action_by_user=Case(
                        When(
                            Exists(connection_action_by),
                            then=Value(True),
                        ),
                        default=Value(False),
                        output_field=BooleanField(),
                    ),
                )
            else:
                users_qs = (
                    Account.objects.filter(
                        ~Q(id=user.id),
                        ~Q(id__in=blocked_account_ids),
                        is_active=True,
                        is_verified=True,
                    )
                    .filter(
                        Q(first_name__icontains=query)
                        | Q(middle_name__icontains=query)
                        | Q(last_name__icontains=query)
                    )
                    .annotate(
                        has_connection=Exists(connection_exists),
                        connection_accomplished=Case(
                            When(Exists(connection_active), then=Value(True)),
                            default=Value(False),
                            output_field=BooleanField(),
                        ),
                        connection_id=Subquery(connection_id_subquery),
                        is_action_by_user=Case(
                            When(
                                Exists(connection_action_by),
                                then=Value(True),
                            ),
                            default=Value(False),
                            output_field=BooleanField(),
                        ),
                    )
                )

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

            username = generate_unique_username(first_name)

            hashed_password = hash_password(raw_password)

            gender = gender.lower()

            new_user = Account(
                username=username,
                first_name=first_name,
                middle_name=middle_name,
                last_name=last_name,
                email=email,
                password=hashed_password,
                birthdate=birthdate,
                gender=gender,
                profile="none",
                date_created=now(),
                is_active=True,
                is_verified=False,
                is_default_user=False,
                is_superuser=False,
            )
            new_user.save()

            record_consent_acceptance(
                new_user,
                ["terms", "privacy"],
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.headers.get("User-Agent"),
            )

            emailer.send_email_verification_code(
                to_email=email,
                subject="Verification Code",
                user_id=username,
                body=None,
            )

            return Response(
                {
                    "status": True,
                    "message": "Account created",
                    "username": username,
                    "authtoken": jwt.encoder(
                        {"userID": str(new_user.id), "username": username}
                    ),
                    "usertoken": jwt.encoder(AccountSerializer(new_user).data),
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
            delete_account(account)

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
            return Response(
                {"status": True, "data": export_account_data(account)},
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
            document_types = request.data.get("document_types")

            if not document_types:
                document_types = [
                    pending["document_type"]
                    for pending in get_pending_consents(account)
                ]

            record_consent_acceptance(
                account,
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
            blocks = Block.objects.filter(blocker=account).select_related("blocked")
            data = [
                {
                    "id": block.blocked.id,
                    "username": block.blocked.username,
                    "first_name": block.blocked.first_name,
                    "last_name": block.blocked.last_name,
                    "profile": block.blocked.profile,
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
            target_id = request.data.get("user_id")

            if not target_id:
                return Response(
                    {"status": False, "message": "user_id is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if str(target_id) == str(account.id):
                return Response(
                    {"status": False, "message": "You cannot block yourself"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            target = get_object_or_404(Account, id=target_id)

            with transaction.atomic():
                Block.objects.get_or_create(blocker=account, blocked=target)

                Connection.objects.filter(
                    Q(action_by=account, involved_user=target)
                    | Q(action_by=target, involved_user=account)
                ).delete()

                Invite.objects.filter(
                    Q(created_by=account, target_user=target)
                    | Q(created_by=target, target_user=account)
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
            target_id = request.data.get("user_id")

            if not target_id:
                return Response(
                    {"status": False, "message": "user_id is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            Block.objects.filter(blocker=account, blocked_id=target_id).delete()

            return Response(
                {"status": True, "message": "User unblocked"},
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
                {"status": True, "message": "Report submitted", "data": {"id": report.id}},
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
