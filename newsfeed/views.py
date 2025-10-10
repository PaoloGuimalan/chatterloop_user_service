from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.db.models import Q
from .models import Post
from .serializers import PostSerializer


class NewsfeedView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = self.request.user
        try:
            queryset = (
                Post.objects.prefetch_related(
                    "tagging", "privacy_users", "references", "map_info"
                )
                .filter(privacy_status="public")
                .order_by("-date_posted")
            )

            serialized_result = PostSerializer(queryset, many=True)

            return Response(serialized_result.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
