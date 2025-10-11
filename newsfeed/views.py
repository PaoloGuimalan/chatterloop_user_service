from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.db.models import Q
from .models import Post
from user.models import Account
from .serializers import PostSerializer
from user.serializers import ConnectionSerializer
from rest_framework.pagination import PageNumberPagination
from user.services.connections import ConnectionHelpers


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class NewsfeedView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user
        try:
            connections = ConnectionHelpers(user)
            connections_list = connections.get_connections()

            queryset = (
                Post.objects.prefetch_related(
                    "tagging", "privacy_users", "references", "map_info"
                )
                .filter(
                    Q(user_id__in=connections_list)
                    | Q(tagging__user_id__in=connections_list)
                    | Q(privacy_status="public"),
                    ~Q(user=user),
                )
                .order_by("-date_posted")
            )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                queryset, request, view=self
            )

            serialized_result = PostSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class NewsfeedProfileView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request, username):
        user = self.request.user
        try:
            user_request = Account.objects.get(username=username)
            connections = ConnectionHelpers(user_request)
            connections_list = connections.get_connections()

            queryset = (
                Post.objects.prefetch_related(
                    "tagging", "privacy_users", "references", "map_info"
                )
                .filter(
                    Q(user__username=username)
                    | Q(tagging__user_id__in=connections_list)
                )
                .order_by("-date_posted")
            )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                queryset, request, view=self
            )

            serialized_result = PostSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
