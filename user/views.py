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
)
from .models import Account, Connection
from .serializers import (
    AccountSerializer,
    ConnectionSerializer,
    AccountSearchSerializer,
)
from .utils.jwt_tools import JWTTools
from .utils.generators import generate_random_digit
from rest_framework.pagination import PageNumberPagination
from django.db import transaction
import bcrypt

jwt = JWTTools


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class UserAuthentication(APIView):

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        elif self.request.method == "POST":
            return [AllowAny()]
        return super().get_permissions()

    def get(self, request):
        return Response(
            {"status": True, "message": "OK"},
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        try:
            email_username = request.data.get("email_username")
            password = request.data.get("password")

            if not email_username or not password:
                return Response(
                    {
                        "status": False,
                        "message": "Email or Password is missing",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            user = Account.objects.get(
                Q(email=email_username) | Q(username=email_username)
            )

            if user:
                hashed = user.password.encode("utf-8")
                bytes_password = password.encode("utf-8")
                is_correct = bcrypt.checkpw(bytes_password, hashed)

                if is_correct:

                    serialized_user = AccountSerializer(user)

                    return Response(
                        {
                            "status": True,
                            "result": {
                                "usertoken": jwt.encoder(serialized_user.data),
                                "authtoken": jwt.encoder(
                                    {"userID": user.username, "username": user.username}
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


class UserContacts(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user
        try:
            paginated_header = request.headers.get("paginated", "true")

            queryset = (
                Connection.objects.filter(
                    Q(Q(action_by=user) | Q(involved_user=user)),
                    ~Q(action_by=F("involved_user")),
                    Q(action_by__is_active=True),
                    Q(action_by__is_verified=True),
                    Q(involved_user__is_active=True),
                    Q(involved_user__is_verified=True),
                    status=True,
                )
                .distinct("connection_id")
                .order_by("connection_id", "-action_date")
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
            new_connection_id = generate_random_digit(20)

            pending_involved_user = Account.objects.get(username=addUsername)

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

            return Response("OK", status=status.HTTP_200_OK)
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
            connection_id_subquery = connection_exists.values("id")[:1]

            if query.startswith("@"):
                domain = query.split("@")[1]
                users_qs = Account.objects.filter(
                    ~Q(id=user.id),
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
                        ~Q(id=user.id), is_active=True, is_verified=True
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
