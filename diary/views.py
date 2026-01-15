from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Count, Q
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.pagination import PageNumberPagination
from .models import Entry, Tag, Mood
from user.models import Account
from django.shortcuts import get_object_or_404
from .serializers import EntrySerializer, TagSerializer, MoodSerializer


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class DiaryTotalView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request, username):
        user = self.request.user

        try:
            current_user = Account.objects.get(username=username)

            limit_tags = 3
            query_set = Entry.objects.filter(account=current_user).order_by(
                "-entry_date", "-created_at"
            )

            serialized_data = EntrySerializer(query_set, many=True).data

            top_tags = (
                Tag.objects.filter(entries__account=user)
                .annotate(entry_count=Count("entries"))
                .order_by("-entry_count")[:limit_tags]
            )

            serialized_tags = TagSerializer(top_tags, many=True).data

            latest_entry_date = None
            if len(serialized_data) > 0:
                lastest_entry = EntrySerializer(serialized_data[0]).data
                latest_entry_date = lastest_entry["entry_date"]

            return Response(
                {
                    "user": current_user.username,
                    "total_entries": query_set.count(),
                    "latest_entry": latest_entry_date,
                    "top_tags": serialized_tags,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as ex:
            return Response(str(ex), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DiaryListView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            queryset = (
                Entry.objects.select_related("entry_map_info")
                .prefetch_related("attachments")
                .filter(account=user)
                .order_by("-created_at")
            )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                queryset, request, view=self
            )

            serialized_result = EntrySerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data

        except Exception as ex:
            return Response(str(ex), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MoodListView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            queryset = Mood.objects.all().order_by("id")

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                queryset, request, view=self
            )

            serialized_result = MoodSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data

        except Exception as ex:
            return Response(str(ex), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TagListView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            search = request.GET.get("search", None)
            queryset = Tag.objects
            check_tag_query = Tag.objects.filter(name=search)

            print(search)

            if search:
                queryset = (
                    queryset.filter(
                        name__icontains=search,
                    )
                    .all()
                    .order_by("id")
                )
            else:
                queryset = queryset.all().order_by("id")

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                queryset, request, view=self
            )

            serialized_result = TagSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(
                {
                    "list": serialized_result.data,
                    "is_new": (
                        False
                        if search is None
                        or (isinstance(search, str) and not search.strip())
                        else not bool(len(check_tag_query))
                    ),
                }
            )
            return data

        except Exception as ex:
            return Response(str(ex), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DiaryCRUDView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, entry_id):
        user = self.request.user

        try:
            queryset = Entry.objects.select_related("entry_map_info").prefetch_related(
                "attachments"
            )
            final_query = get_object_or_404(
                queryset, Q(account=user) | Q(is_private=False), id=entry_id
            )

            serialized_response = EntrySerializer(final_query)

            return Response(serialized_response.data, status=status.HTTP_200_OK)
        except Exception as ex:
            return Response(str(ex), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
