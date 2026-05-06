from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.db import models
from django.db.models import (
    Q,
    Exists,
    OuterRef,
    Subquery,
    Value,
    Case,
    When,
    IntegerField,
    BooleanField,
    F,
)
from django.db.models.functions import Coalesce
from django.db import transaction, connection
from .models import (
    Post,
    Emoji,
    Reaction,
    PreviewCount,
    Comment,
    ActivityCount,
    PostScore,
    EngagementLog,
    PostSave,
)
from user.models import Account
from .serializers import (
    PostSerializer,
    EmojiSerializer,
    PreviewCountSerializer,
    CommentSerializer,
    ActivityCountSerializer,
    PostScoreSerializer,
    PostSaveSerializer,
)
from user.serializers import ConnectionSerializer
from rest_framework.pagination import PageNumberPagination
from user.services.connections import ConnectionHelpers
from user.services.mongohelpers import NotificationService
from user_service.services.redis import RedisPubSubClient
from django.utils.timezone import now
from datetime import datetime
from .helpers.query_functions import save_viewcache_engagements
from .scripts.calculate_ranking_score import calculate_ranking_score_task
import uuid
from community.models import RealmFollow, Realm
from django.shortcuts import get_object_or_404


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class NewsfeedView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def post(self, request):
        user = self.request.user
        try:
            connections = ConnectionHelpers(user)
            connections_list = connections.get_connections()
            followed_realm_ids = list(
                RealmFollow.objects.filter(follower=user).values_list(
                    "realm_id", flat=True
                )
            )

            viewcache = request.data.get("viewcache", [])
            save_viewcache_engagements(user, viewcache)

            if len(viewcache):
                post_ids = [view["post_id"] for view in viewcache]
                existing_logs = EngagementLog.objects.filter(
                    user=user, post_id__in=post_ids, action="viewed"
                ).values("post_id", "duration_seconds")

                duration_map = {
                    log["post_id"]: log["duration_seconds"] or 0
                    for log in existing_logs
                }

                values_list = []
                params = []
                for view in viewcache:
                    pid = view["post_id"]
                    new_log_id = str(uuid.uuid4())
                    new_total_duration = duration_map.get(pid, 0) + view.get(
                        "duration", 0
                    )

                    values_list.append("(%s, %s, %s, %s, %s, %s, NOW())")
                    params.extend(
                        [
                            new_log_id,
                            user.id,
                            pid,
                            "viewed",
                            new_total_duration,
                            view["created_at"],
                        ]
                    )

                if values_list:
                    query = f"""
                        INSERT INTO newsfeed_engagementlog (log_id, user_id, post_id, action, duration_seconds, updated_at, created_at)
                        VALUES {", ".join(values_list)}
                        ON CONFLICT (user_id, post_id, action) 
                        WHERE action = 'viewed'
                        DO UPDATE SET 
                            duration_seconds = EXCLUDED.duration_seconds,
                            updated_at = EXCLUDED.updated_at;
                    """

                    with connection.cursor() as cursor:
                        cursor.execute(query, params)

            queryset = (
                Post.objects.select_related("user", "score", "author_realm")
                .prefetch_related(
                    "tagging",
                    "privacy_users",
                    "references",
                    "map_info",
                    "preview",
                )
                .annotate(
                    is_friend=Case(
                        When(
                            Q(user_id__in=connections_list)
                            | Q(author_realm_id__in=followed_realm_ids),
                            then=Value(0.8),
                        ),
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
                    my_last_view=Coalesce(
                        Subquery(
                            EngagementLog.objects.filter(
                                post=OuterRef("pk"), user=user, action="viewed"
                            )
                            .order_by("-updated_at")
                            .values("updated_at")[:1]
                        ),
                        Value("1970-01-01", output_field=models.DateTimeField()),
                    ),
                    connection_latest_engagement=Coalesce(
                        Subquery(
                            EngagementLog.objects.filter(
                                post=OuterRef("pk"),
                                user_id__in=connections_list,
                                action__in=["commented", "shared"],
                            )
                            .order_by("-updated_at")
                            .values("updated_at")[:1]
                        ),
                        Value("1970-01-01", output_field=models.DateTimeField()),
                    ),
                    should_show=Case(
                        When(
                            Q(my_last_view="1970-01-01")
                            | Q(connection_latest_engagement__gt=F("my_last_view")),
                            then=Value(True),
                        ),
                        default=Value(False, output_field=models.BooleanField()),
                        output_field=models.BooleanField(),
                    ),
                    is_saved=Exists(
                        PostSave.objects.filter(post=OuterRef("pk"), user=user)
                    ),
                    user_reaction=Coalesce(
                        Subquery(
                            Reaction.objects.filter(
                                post=OuterRef("pk"), user=user
                            ).values("emoji_id")[:1]
                        ),
                        Value(None),
                    ),
                )
                .filter(~Q(is_owner=1))
                .filter(
                    ~Q(is_owner=1), should_show=True, deleted_at=None, is_archived=False
                )
                .order_by(
                    "-is_friend",
                    "-should_show",
                    "-connection_latest_engagement",
                    "-is_friend_tagged",
                    "-score__ranking_score",
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

    def put(self, request):
        user = self.request.user
        try:
            post_id = request.data.get("post_id")
            fields = request.data.get("fields")

            if fields:
                Post.objects.filter(post_id=post_id).update(**fields)

            return Response(
                {
                    "status": True,
                    "message": "Post/s has been deleted",
                    "reference": post_id,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request):
        user = self.request.user
        try:
            post_ids = request.data.get("post_ids")

            if len(post_ids) > 0:
                Post.objects.filter(post_id__in=post_ids).update(
                    deleted_at=now(), deleted_by=user
                )

            return Response(
                {
                    "status": True,
                    "message": "Post/s has been deleted",
                    "reference": post_ids,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class NewsfeedProfileView(APIView):
    permission_classes = [AllowAny]  ## IsAuthenticated
    pagination_class = Pagination

    def post(self, request, username):
        user = self.request.user
        try:
            archive_param = request.query_params.get("archive", False)
            archive = True if archive_param == "true" else False

            user_reaction_subquery = Reaction.objects.filter(
                post=OuterRef("pk"), user=user
            ).values("emoji_id")[:1]

            viewcache = request.data.get("viewcache", [])

            if user.username != username:
                save_viewcache_engagements(user, viewcache)

            realm_match = Realm.objects.filter(slug=username).first()
            profile_filter = Q(
                Q(user__username=username) | Q(tagging__user__username=username)
            ) & Q(author_realm=None)
            if realm_match:
                profile_filter = Q(author_realm=realm_match)
            elif archive:
                profile_filter = Q(user=user) & Q(author_realm=None)

            queryset = (
                Post.objects.select_related("user", "score", "author_realm")
                .prefetch_related(
                    "tagging",
                    "privacy_users",
                    "references",
                    "map_info",
                    "preview",
                )
                .filter(profile_filter)
                .annotate(
                    is_saved=Exists(
                        PostSave.objects.filter(post=OuterRef("pk"), user=user)
                    ),
                    user_reaction=Coalesce(
                        Subquery(user_reaction_subquery), Value(None)
                    ),
                )
                .filter(deleted_at=None, is_archived=archive)
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
    permission_classes = [AllowAny]  ## IsAuthenticated

    def get(self, request, post_id):
        user = self.request.user
        try:
            user_reaction_subquery = Reaction.objects.filter(
                post=OuterRef("pk"), user=user
            ).values("emoji_id")[:1]

            queryset = (
                Post.objects.select_related("user", "score", "author_realm")
                .prefetch_related(
                    "tagging",
                    "privacy_users",
                    "references",
                    "map_info",
                    "preview",
                )
                .annotate(
                    is_saved=Exists(
                        PostSave.objects.filter(post=OuterRef("pk"), user=user)
                    ),
                    user_reaction=Coalesce(
                        Subquery(user_reaction_subquery), Value(None)
                    ),
                )
                .get(post_id=post_id)
            )

            serialized_result = PostSerializer(queryset)

            if (
                serialized_result.data["deleted_at"]
                or serialized_result.data["is_archived"]
            ):
                return Response(
                    {**serialized_result.data, "caption": "", "references": []},
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(serialized_result.data, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class EmojisView(APIView):
    permission_classes = [AllowAny]  # IsAuthenticated

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

                # calculate_ranking_score_task.apply_async(
                #     kwargs={
                #         "post_id": post_id,
                #         "update_type": "react",
                #         "is_decrease": False,
                #     },
                #     countdown=5,
                # )

                if post.user.id != user.id:
                    service = NotificationService()
                    service.add_notification(
                        referenceID=new_reaction_id,
                        referenceStatus=True,
                        toUserID=post.user.id,
                        fromUserID=user.id,
                        content_headline="Post Reaction",
                        content_details=f"@{user.username} reacted {emoji.emoji_content} to your post.",
                        type="post_reaction",
                        isRead=False,
                    )

                    sse_sendToUser = post.user.id
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

                if post.user.id != user.id:
                    service = NotificationService()
                    service.update_content(
                        reaction_id=reaction.reaction_id,
                        new_content=f"@{user.username} reacted {new_emoji.emoji_content} to your post.",
                    )

                    sse_sendToUser = post.user.id
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

                # calculate_ranking_score_task.apply_async(
                #     kwargs={
                #         "post_id": post_id,
                #         "update_type": "react",
                #         "is_decrease": True,
                #     },
                #     countdown=5,
                # )

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
    # permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]  ## IsAuthenticated()
        else:
            return [IsAuthenticated()]

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

                    # calculate_ranking_score_task.apply_async(
                    #     kwargs={
                    #         "post_id": post_id,
                    #         "update_type": "comment",
                    #         "is_decrease": False,
                    #     },
                    #     countdown=5,
                    # )

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
                            toUserID=parent_comment.user.id,
                            fromUserID=user.id,
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
                            f"events_{parent_comment.user.id}", data
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

                    # calculate_ranking_score_task.apply_async(
                    #     kwargs={
                    #         "post_id": post_id,
                    #         "update_type": "comment",
                    #         "is_decrease": False,
                    #     },
                    #     countdown=5,
                    # )

                    if post.user != user:
                        service = NotificationService()
                        service.add_notification(
                            referenceID=new_comment_id,
                            referenceStatus=True,
                            toUserID=post.user.id,
                            fromUserID=user.id,
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

                        RedisPubSubClient.publish_json(f"events_{post.user.id}", data)

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


class PostSaveView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        try:
            user = self.request.user

            post_save_query = (
                PostSave.objects.select_related("post")
                .filter(user=user, post__deleted_at=None)
                .order_by("-saved_at")
            )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                post_save_query, request, view=self
            )

            serialized_result = PostSaveSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def post(self, request):
        try:
            user = self.request.user
            post_id = request.data.get("post_id")

            current_post = get_object_or_404(Post, post_id=post_id)
            new_save_query = PostSave.objects.create(post=current_post, user=user)

            return Response(
                {
                    "status": True,
                    "message": "Post has been saved",
                    "save_id": new_save_query.id,
                },
                status=200,
            )
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def delete(self, request):
        try:
            user = self.request.user
            post_id = request.data.get("post_id")

            current_post = get_object_or_404(Post, post_id=post_id)
            PostSave.objects.filter(post=current_post, user=user).delete()

            return Response(
                {"status": True, "message": "Post has been unsaved"},
                status=200,
            )
        except Exception as e:
            return Response({"error": str(e)}, status=500)
