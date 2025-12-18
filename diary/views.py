from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Count
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.pagination import PageNumberPagination
from .models import Entry, Tag
from user.models import Account
from .serializers import EntrySerializer, TagSerializer

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
            query_set = Entry.objects.filter(account=current_user).order_by("-entry_date", "-created_at")

            serialized_data = EntrySerializer(query_set, many=True).data

            top_tags = (
                Tag.objects
                .filter(entries__account=user)
                .annotate(entry_count=Count("entries"))
                .order_by("-entry_count")[:limit_tags]
            )

            serialized_tags = TagSerializer(top_tags, many=True).data

            latest_entry_date = None
            if len(serialized_data) > 0:
                lastest_entry = EntrySerializer(serialized_data[0]).data
                latest_entry_date = lastest_entry["entry_date"]

            return Response({
                "user": current_user.username,
                "total_entries": query_set.count(),
                "latest_entry": latest_entry_date,
                "top_tags": serialized_tags
            }, status=status.HTTP_200_OK)
        except Exception as ex:
            return Response(str(ex), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
