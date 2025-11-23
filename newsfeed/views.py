from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.db.models import (
    Q,
    Exists,
    OuterRef,
    Subquery,
    Value,
    Case,
    When,
    IntegerField,
)
from django.db.models.functions import Coalesce
from django.db import transaction
from .models import (
    Post,
    Emoji,
    Reaction,
    PreviewCount,
    Comment,
    ActivityCount,
    PostScore,
)
from user.models import Account
from .serializers import (
    PostSerializer,
    EmojiSerializer,
    PreviewCountSerializer,
    CommentSerializer,
    ActivityCountSerializer,
    PostScoreSerializer,
)
from user.serializers import ConnectionSerializer
from rest_framework.pagination import PageNumberPagination
from user.services.connections import ConnectionHelpers
from user.services.mongohelpers import NotificationService
from user_service.services.redis import RedisPubSubClient
from django.utils.timezone import now
from datetime import datetime
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

            # queryset = (
            #     Post.objects.prefetch_related(
            #         "tagging",
            #         "privacy_users",
            #         "references",
            #         "map_info",
            #         "preview",
            #         "activity_counts",
            #     )
            #     .filter(
            #         Q(user_id__in=connections_list)
            #         | Q(tagging__user_id__in=connections_list)
            #         | Q(privacy_status="public"),
            #         ~Q(user=user),
            #     )
            #     .annotate(
            #         user_reaction=Coalesce(
            #             Subquery(user_reaction_subquery), Value(None)
            #         )
            #     )
            #     .order_by("-date_posted")
            # )

            queryset = (
                Post.objects.prefetch_related(
                    "tagging",
                    "privacy_users",
                    "references",
                    "map_info",
                    "preview",
                    "score",
                )
                .annotate(
                    is_friend=Case(
                        When(user_id__in=connections_list, then=Value(0.8)),
                        default=Value(0),
                        output_field=IntegerField(),
                    ),
                    is_friend_tagged=Case(
                        When(tagging__user_id__in=connections_list, then=Value(0.5)),
                        default=Value(0),
                        output_field=IntegerField(),
                    ),
                    is_owner=Case(
                        When(user=user, then=Value(1)),
                        default=Value(0),
                        output_field=IntegerField(),
                    ),
                )
                .filter(
                    # Q(user_id__in=connections_list)
                    # | Q(tagging__user_id__in=connections_list)
                    # | Q(privacy_status="public"),
                    # ~Q(user=user),
                    ~Q(is_owner=1)
                    | Q(score__ranking_score__gt=0.0)
                )
                .annotate(
                    user_reaction=Coalesce(
                        Subquery(user_reaction_subquery), Value(None)
                    )
                )
                .annotate(
                    reaction_anchor=Case(
                        When(user_reaction=None, then=Value(1)),
                        default=Value(0),
                        output_field=IntegerField(),
                    ),
                )
                .order_by(
                    "-is_friend",  # friend posts first
                    "-reaction_anchor",
                    "-is_friend_tagged",
                    "is_owner",  # own posts last
                    "-score__ranking_score",  # then by rank score
                )
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
                    "tagging",
                    "privacy_users",
                    "references",
                    "map_info",
                    "preview",
                    "score",
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
                    "tagging",
                    "privacy_users",
                    "references",
                    "map_info",
                    "preview",
                    "score",
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

            new_reaction_id = str(uuid.uuid4())

            with transaction.atomic():
                Reaction.objects.create(
                    reaction_id=new_reaction_id,
                    post=post,
                    user=user,
                    emoji=emoji,
                )

                preview_count_obj = PreviewCount.objects.get(post=post, emoji=emoji)
                preview_count_obj.count += 1
                preview_count_obj.save()

                reaction_ranking = PostScore.objects.get(post=post)
                reaction_ranking.likes_count += 1
                reaction_ranking.save()

                if post.user.username != user.username:
                    service = NotificationService()
                    service.add_notification(
                        referenceID=new_reaction_id,
                        referenceStatus=True,
                        toUserID=post.user.username,
                        fromUserID=user.username,
                        content_headline="Post Reaction",
                        content_details=f"@{user.username} reacted {emoji.emoji_content} to your post.",
                        type="post_reaction",
                        isRead=False,
                    )

                    sse_sendToUser = post.user.username
                    sse_sendToDetails = (
                        f"@{user.username} reacted {emoji.emoji_content} to your post."
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

                if post.user.username != user.username:
                    service = NotificationService()
                    service.update_content(
                        reaction_id=reaction.reaction_id,
                        new_content=f"@{user.username} reacted {new_emoji.emoji_content} to your post.",
                    )

                    sse_sendToUser = post.user.username
                    sse_sendToDetails = f"@{user.username} reacted {new_emoji.emoji_content} to your post."

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

                service = NotificationService()
                service.delete_notification_by_reference_id(
                    reaction_id=reaction.reaction_id,
                )

                reaction_ranking = PostScore.objects.get(post=post)
                reaction_ranking.likes_count -= 1
                reaction_ranking.save()

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


class ReactionsCountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, post_id):
        try:
            user = self.request.user
            post = Post.objects.get(post_id=post_id)
            query_set = PreviewCount.objects.filter(post=post)

            serialized_result = PreviewCountSerializer(query_set, many=True)
            return Response(serialized_result.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=500)


class ActivityCountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            user = self.request.user
            post_id = request.GET.get("post_id")
            count_type = request.GET.get("count_type")
            post = Post.objects.get(post_id=post_id)
            # query_set = ActivityCount.objects.filter(post=post, count_type=count_type)
            reaction_ranking = PostScore.objects.get(post=post)

            # serialized_result = ActivityCountSerializer(query_set, many=True)
            serialized_result = PostScoreSerializer(reaction_ranking, many=True)
            return Response(serialized_result.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=500)


class CommentsView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        try:
            user = self.request.user
            post_id = request.GET.get("post_id")
            parent_id = request.GET.get("parent_id")

            post = Post.objects.get(post_id=post_id)

            if parent_id:
                comment = Comment.objects.get(comment_id=parent_id)
                queryset = (
                    Comment.objects.filter(post=post, parent_comment=comment)
                    .select_related("user")
                    .order_by("created_at")
                )

                paginator = self.pagination_class()
                paginated_queryset = paginator.paginate_queryset(
                    queryset, request, view=self
                )

                serialized_result = CommentSerializer(paginated_queryset, many=True)
                data = paginator.get_paginated_response(serialized_result.data)

                return data
            else:
                queryset = (
                    Comment.objects.filter(post=post, parent_comment=None)
                    .select_related("user")
                    .order_by("created_at")
                )
                paginator = self.pagination_class()
                paginated_queryset = paginator.paginate_queryset(
                    queryset, request, view=self
                )

                serialized_result = CommentSerializer(paginated_queryset, many=True)
                data = paginator.get_paginated_response(serialized_result.data)

                return data
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def post(self, request):
        try:
            user = self.request.user
            post_id = request.data.get("post_id")
            parent_id = request.data.get("parent_id")
            new_comment = request.data.get("new_comment")
            new_attachment = request.data.get("new_attachment")

            post = Post.objects.get(post_id=post_id)

            if new_comment.strip() == "" and new_attachment is None:
                raise ValueError("No comment to save.")

            with transaction.atomic():
                if parent_id:
                    new_comment_id = str(uuid.uuid4())
                    parent_comment = Comment.objects.get(comment_id=parent_id)

                    Comment.objects.create(
                        comment_id=new_comment_id,
                        parent_comment=parent_comment,
                        post=post,
                        text=new_comment,
                        attachment=new_attachment,
                        user=user,
                    )

                    # activity_count_obj = ActivityCount.objects.get(
                    #     post=post, count_type="comment"
                    # )
                    # activity_count_obj.count += 1
                    # activity_count_obj.save()

                    reaction_ranking = PostScore.objects.get(post=post)
                    reaction_ranking.comments_count += 1
                    reaction_ranking.save()

                    truncated_comment = (
                        (parent_comment.text[:30] + "...")
                        if len(parent_comment.text) > 30
                        else parent_comment.text
                    )

                    if parent_comment.user != user and post.user != user:
                        service = NotificationService()
                        service.add_notification(
                            referenceID=new_comment_id,
                            referenceStatus=True,
                            toUserID=parent_comment.user.username,
                            fromUserID=user.username,
                            content_headline="Replied Comment",
                            content_details=f'@{user.username} replied to your comment "{truncated_comment}"',
                            type="post_comment",
                            isRead=False,
                        )

                        now = datetime.now()
                        data = {
                            "logType": None,
                            "pod": "podless",
                            "event": "notifications",
                            "message": {
                                "status": True,
                                "auth": True,
                                "message": f'@{user.username} replied to your comment "{truncated_comment}"',
                                "result": "",
                            },
                            "dateTime": now.isoformat(),
                        }

                        RedisPubSubClient.publish_json(
                            f"events_{parent_comment.user.username}", data
                        )

                else:
                    new_comment_id = str(uuid.uuid4())
                    Comment.objects.create(
                        comment_id=new_comment_id,
                        parent_comment=None,
                        post=post,
                        text=new_comment,
                        attachment=new_attachment,
                        user=user,
                    )

                    # activity_count_obj = ActivityCount.objects.get(
                    #     post=post, count_type="comment"
                    # )
                    # activity_count_obj.count += 1
                    # activity_count_obj.save()

                    reaction_ranking = PostScore.objects.get(post=post)
                    reaction_ranking.comments_count += 1
                    reaction_ranking.save()

                    if post.user != user:
                        service = NotificationService()
                        service.add_notification(
                            referenceID=new_comment_id,
                            referenceStatus=True,
                            toUserID=post.user.username,
                            fromUserID=user.username,
                            content_headline="Post Comment",
                            content_details=f"@{user.username} commented on your post.",
                            type="post_comment",
                            isRead=False,
                        )

                        now = datetime.now()
                        data = {
                            "logType": None,
                            "pod": "podless",
                            "event": "notifications",
                            "message": {
                                "status": True,
                                "auth": True,
                                "message": f"@{user.username} commented on your post.",
                                "result": "",
                            },
                            "dateTime": now.isoformat(),
                        }

                        RedisPubSubClient.publish_json(
                            f"events_{post.user.username}", data
                        )

            return Response("OK", status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def put(self, request):
        try:
            user = self.request.user
            comment_id = request.data.get("comment_id")
            updated_comment = request.data.get("updated_comment")

            with transaction.atomic():
                current_comment = Comment.objects.get(comment_id=comment_id)

                if updated_comment.strip() == "" and current_comment.attachment is None:
                    raise ValueError("No comment to save.")

                current_comment.text = updated_comment
                current_comment.updated_at = now()
                current_comment.save()

            return Response("OK", status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def delete(self, request):
        try:
            user = self.request.user
            comment_id = request.data.get("comment_id")

            with transaction.atomic():
                current_comment = Comment.objects.get(comment_id=comment_id)
                current_comment.deleted_at = now()
                current_comment.deleted_by = user
                current_comment.save()

            return Response("OK", status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=500)
