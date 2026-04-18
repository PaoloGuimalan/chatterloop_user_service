from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import RealmFollow, Realm, Member
from .serializers import RealmSerializer
from django.shortcuts import get_object_or_404
from django.db.models import Q, Exists, OuterRef, Count


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class TopRealms(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            search = request.query_params.get("search", None)
            type = request.query_params.get("type", None)

            top_realm_queryset = Realm.objects.annotate(
                followers_count=Count("followers"),
                members=Count("member"),
                is_admin=Exists(
                    Member.objects.filter(
                        realm=OuterRef("pk"), account=user, role="admin"
                    )
                ),
                is_member=Exists(
                    Member.objects.filter(realm=OuterRef("pk"), account=user)
                ),
                is_follower=Exists(
                    RealmFollow.objects.filter(realm=OuterRef("pk"), follower=user)
                ),
            ).filter(type=type, is_private=False)

            if search:
                top_realm_queryset = top_realm_queryset.filter(
                    Q(slug__icontains=search) | Q(name__icontains=search)
                )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                top_realm_queryset, request, view=self
            )

            serialized_result = RealmSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MyRealms(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            search = request.query_params.get("search", None)
            type = request.query_params.get("type", None)

            my_realm_queryset = Realm.objects.annotate(
                followers_count=Count("followers"),
                members=Count("member"),
                is_admin=Exists(
                    Member.objects.filter(
                        realm=OuterRef("pk"), account=user, role="admin"
                    )
                ),
                is_member=Exists(
                    Member.objects.filter(realm=OuterRef("pk"), account=user)
                ),
                is_follower=Exists(
                    RealmFollow.objects.filter(realm=OuterRef("pk"), follower=user)
                ),
            ).filter(is_member=True, type=type)

            if search:
                my_realm_queryset = my_realm_queryset.filter(
                    Q(slug__icontains=search) | Q(name__icontains=search)
                )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                my_realm_queryset, request, view=self
            )

            serialized_result = RealmSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def put(self, request):
        user = self.request.user
        try:
            realm_id = request.data.get("realm_id")
            fields = request.data.get("fields")

            if fields:
                Realm.objects.filter(realm_id=realm_id).update(**fields)

            return Response(
                {
                    "status": True,
                    "message": "Realm has been updated",
                    "reference": realm_id,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class FollowRealmView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            search = request.query_params.get("search", None)
            type = request.query_params.get("type", None)

            followed_realm_queryset = Realm.objects.annotate(
                followers_count=Count("followers"),
                members=Count("member"),
                is_admin=Exists(
                    Member.objects.filter(
                        realm=OuterRef("pk"), account=user, role="admin"
                    )
                ),
                is_member=Exists(
                    Member.objects.filter(realm=OuterRef("pk"), account=user)
                ),
                is_follower=Exists(
                    RealmFollow.objects.filter(realm=OuterRef("pk"), follower=user)
                ),
            ).filter(is_follower=True, type=type)

            if search:
                followed_realm_queryset = followed_realm_queryset.filter(
                    Q(slug__icontains=search) | Q(name__icontains=search)
                )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                followed_realm_queryset, request, view=self
            )

            serialized_result = RealmSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def post(self, request):
        user = self.request.user

        try:
            realm_id = request.data.get("realm_id")

            realm = get_object_or_404(Realm, id=realm_id)
            follow_realm_queryset = RealmFollow.objects.create(
                follower=user, realm=realm
            )

            if follow_realm_queryset is None:
                return Response(
                    {"status": False, "message": "Error completing follow request"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {"status": True, "message": f"Followed {realm_id}"},
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request):
        user = self.request.user

        try:
            realm_id = request.data.get("realm_id")

            realm = get_object_or_404(Realm, id=realm_id)
            unfollow_realm_queryset = RealmFollow.objects.get(
                follower=user, realm=realm
            )

            if unfollow_realm_queryset is None:
                return Response(
                    {"status": False, "message": "Error completing follow request"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            unfollow_realm_queryset.delete()

            return Response(
                {"status": True, "message": f"Followed {realm_id}"},
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
