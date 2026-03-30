from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import RealmFollow, Realm, Member
from .serializers import RealmSerializer
from django.shortcuts import get_object_or_404
from django.db.models import Q, Exists, OuterRef


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class MyRealms(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            search = request.query_params.get("search", None)

            my_realm_queryset = Realm.objects.annotate(
                is_member=Exists(
                    Member.objects.filter(realm=OuterRef("pk"), account=user)
                ),
            ).filter(is_member=True)

            if search:
                my_realm_queryset = my_realm_queryset.filter(Q(slug__icontains=search))

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                my_realm_queryset, request, view=self
            )

            serialized_result = RealmSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class FollowRealmView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            search = request.query_params.get("search", None)

            followed_realm_queryset = Realm.objects.annotate(
                is_follower=Exists(
                    RealmFollow.objects.filter(realm=OuterRef("pk"), follower=user)
                ),
            ).filter(is_follower=True)

            if search:
                followed_realm_queryset = followed_realm_queryset.filter(
                    Q(slug__icontains=search)
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
