from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.db.models import Q, Exists, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.db import transaction
from .models import Post, Emoji, Reaction, PreviewCount
from user.models import Account
from .serializers import PostSerializer, EmojiSerializer
from user.serializers import ConnectionSerializer
from rest_framework.pagination import PageNumberPagination
from user.services.connections import ConnectionHelpers
import uuid


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

            user_reaction_subquery = Reaction.objects.filter(
                post=OuterRef("pk"), user=user
            ).values("emoji_id")[:1]

            queryset = (
                Post.objects.prefetch_related(
                    "tagging", "privacy_users", "references", "map_info", "preview"
                )
                .filter(
                    Q(user_id__in=connections_list)
                    | Q(tagging__user_id__in=connections_list)
                    | Q(privacy_status="public"),
                    ~Q(user=user),
                )
                .annotate(
                    user_reaction=Coalesce(
                        Subquery(user_reaction_subquery), Value(None)
                    )
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
            user_reaction_subquery = Reaction.objects.filter(
                post=OuterRef("pk"), user=user
            ).values("emoji_id")[:1]

            queryset = (
                Post.objects.prefetch_related(
                    "tagging", "privacy_users", "references", "map_info", "preview"
                )
                .filter(
                    Q(user__username=username) | Q(tagging__user__username=username)
                )
                .annotate(
                    user_reaction=Coalesce(
                        Subquery(user_reaction_subquery), Value(None)
                    )
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


class NewsfeedPostPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, post_id):
        user = self.request.user
        try:
            user_reaction_subquery = Reaction.objects.filter(
                post=OuterRef("pk"), user=user
            ).values("emoji_id")[:1]

            queryset = (
                Post.objects.prefetch_related(
                    "tagging", "privacy_users", "references", "map_info", "preview"
                )
                .annotate(
                    user_reaction=Coalesce(
                        Subquery(user_reaction_subquery), Value(None)
                    )
                )
                .get(post_id=post_id)
            )

            serialized_result = PostSerializer(queryset)

            return Response(serialized_result.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class EmojisView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = self.request.user
        try:
            queryset = Emoji.objects.all()

            serialized_result = EmojiSerializer(queryset, many=True)

            return Response(serialized_result.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PostReactionsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            user = self.request.user
            post_id = request.data.get("post_id")
            emoji_id = request.data.get("emoji_id")

            post = Post.objects.get(post_id=post_id)
            emoji = Emoji.objects.get(emoji_id=emoji_id)

            with transaction.atomic():
                Reaction.objects.create(
                    reaction_id=str(uuid.uuid4()),
                    post=post,
                    user=user,
                    emoji=emoji,
                )

                preview_count_obj = PreviewCount.objects.get(post=post, emoji=emoji)
                preview_count_obj.count += 1
                preview_count_obj.save()

            return Response(
                {"message": "Reaction has been added"}, status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def put(self, request, *args, **kwargs):
        try:
            user = self.request.user
            post_id = request.data.get("post_id")
            emoji_id = request.data.get("emoji_id")

            post = Post.objects.get(post_id=post_id)
            new_emoji = Emoji.objects.get(emoji_id=emoji_id)

            with transaction.atomic():
                reaction = Reaction.objects.get(post_id=post, user=user)
                old_emoji = reaction.emoji
                reaction.emoji = new_emoji
                reaction.save()

            old_preview = PreviewCount.objects.get(post_id=post, emoji_id=old_emoji)
            old_preview.count = max(old_preview.count - 1, 0)
            old_preview.save()

            new_preview = PreviewCount.objects.get(post_id=post, emoji_id=new_emoji)
            new_preview.count += 1
            new_preview.save()

            return Response(
                {"message": "Reaction has been updated"}, status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def delete(self, request, *args, **kwargs):
        try:
            user = self.request.user
            post_id = request.data.get("post_id")
            post = Post.objects.get(post_id=post_id)

            with transaction.atomic():
                reaction = Reaction.objects.get(post=post, user=user)
                emoji = reaction.emoji
                reaction.delete()

                preview_count = PreviewCount.objects.get(post=post, emoji=emoji)
                preview_count.count = max(preview_count.count - 1, 0)
                preview_count.save()

                return Response(
                    {"message": "Reaction has been deleted"}, status=status.HTTP_200_OK
                )
        except Exception as e:
            return Response({"error": str(e)}, status=500)
