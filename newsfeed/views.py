from django.shortcuts import render
from django.http import HttpResponse
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
    PostSave,
    NewsfeedIndex,
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
from .drf_permissions import AllowsInternalService
from .services.link_preview import extract_first_url, get_preview, fetch_image
from entity.utils import get_entity_display_username
from interests.services.affinity import bump_interest_affinity
from user_service.services.redis import RedisPubSubClient
from django.utils.timezone import now
from datetime import datetime
from .helpers.query_functions import (
    save_viewcache_engagements,
    update_ranking_score,
    interaction_score_bump,
    follower_interaction_score_bump,
    fetch_friends_posts,
    fetch_trending_posts,
    resolved_interest_categories,
)
import uuid
from community.models import RealmFollow, Realm
from entity.models import Entity
from django.shortcuts import get_object_or_404
from user.utils.blocking import get_blocked_account_ids, is_blocked
from rest_framework.exceptions import PermissionDenied
from entity.ownership import assert_owns
from entity.permissions import Permission
from entity.drf_permissions import RequiresPermission


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class NewsfeedView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def post(self, request):
        user = self.request.user
        entity = self.request.entity
        try:
            page_size = request.query_params.get("page_size", 10)

            connections = ConnectionHelpers(entity)
            connections_list = connections.get_connections()
            followed_realm_ids = list(
                RealmFollow.objects.filter(follower=entity).values_list(
                    "follower_id", flat=True
                )
            )
            blocked_account_ids = get_blocked_account_ids(entity)

            current_mode = RedisPubSubClient.get_and_toggle_feed_mode(entity.id)

            viewcache = request.data.get("viewcache", [])
            save_viewcache_engagements(entity, viewcache)

            if current_mode == "friends":
                candidate_post_ids = fetch_friends_posts(entity.id, page_size)

                if not candidate_post_ids:
                    candidate_post_ids = fetch_trending_posts(
                        entity.id, page_size, 100, resolved_interest_categories(entity)
                    )
                    current_mode = "trending"
                    RedisPubSubClient.update_feed_mode(entity.id, current_mode)
            else:
                candidate_post_ids = fetch_trending_posts(
                    entity.id, page_size, 100, resolved_interest_categories(entity)
                )

                if not candidate_post_ids:
                    candidate_post_ids = fetch_friends_posts(entity.id, page_size)
                    current_mode = "friends"
                    RedisPubSubClient.update_feed_mode(entity.id, current_mode)

            hydrated_posts = (
                Post.objects.select_related("entity", "score")
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
                            Q(entity_id__in=connections_list)
                            | Q(entity_id__in=followed_realm_ids),
                            then=Value(0.8),
                        ),
                        default=Value(0),
                        output_field=IntegerField(),
                    ),
                    is_friend_tagged=Case(
                        When(tagging__entity_id__in=connections_list, then=Value(0.5)),
                        default=Value(0),
                        output_field=IntegerField(),
                    ),
                    is_saved=Exists(
                        PostSave.objects.filter(post=OuterRef("pk"), entity=entity)
                    ),
                    entity_reaction=Coalesce(
                        Subquery(
                            Reaction.objects.filter(
                                post=OuterRef("pk"), entity=entity
                            ).values("emoji_id")[:1]
                        ),
                        Value(None),
                    ),
                )
                .filter(
                    post_id__in=candidate_post_ids, deleted_at=None, is_archived=False
                )
                .exclude(entity_id__in=blocked_account_ids)
                .order_by(
                    "-is_friend",
                    "-is_friend_tagged",
                    "-score__ranking_score",
                )
            )

            serialized_result = PostSerializer(hydrated_posts, many=True)

            is_page_matched = len(serialized_result.data) == len(candidate_post_ids)
            will_still_paginate = len(serialized_result.data) == int(page_size)
            is_next = will_still_paginate if is_page_matched else None

            return Response(
                {
                    "count": len(candidate_post_ids),
                    "next": is_next,
                    "previous": None,
                    "results": serialized_result.data,
                }
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def put(self, request):
        user = self.request.user
        try:
            post_id = request.data.get("post_id")
            fields = request.data.get("fields")

            post = get_object_or_404(Post, post_id=post_id)
            assert_owns(request, post)

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
        except PermissionDenied:
            raise
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request):
        user = self.request.user
        try:
            post_ids = request.data.get("post_ids")

            if len(post_ids) > 0:
                entity = getattr(request, "entity", None)
                owned_count = Post.objects.filter(
                    post_id__in=post_ids, entity_id=getattr(entity, "id", None)
                ).count()
                if entity is None or owned_count != len(post_ids):
                    raise PermissionDenied(
                        "You do not own all of the specified posts."
                    )

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
        except PermissionDenied:
            raise
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class NewsfeedProfileView(APIView):

    def get_permissions(self):
        if self.request.method in ["POST"]:
            return [AllowAny()]
        return super().get_permissions()

    def get_authenticators(self):
        """
        VIEW LEVEL FIX: If a guest visits without an 'x-access-token' header,
        completely strip the authentication class out of this execution thread.
        This stops the custom backend from throwing "Token not defined" errors!
        """
        if self.request.method in ["POST"]:
            token = self.request.headers.get("x-access-token")
            if not token:
                return ()  # Returns an empty tuple, bypassing your custom backend entirely for guests!

        return super().get_authenticators()

    pagination_class = Pagination

    def post(self, request, username):
        user = self.request.user
        entity = getattr(self.request, "entity", None)
        try:
            archive_param = request.query_params.get("archive", False)
            archive = True if archive_param == "true" else False

            user_reaction_subquery = Reaction.objects.filter(
                post=OuterRef("pk"), entity=entity
            ).values("emoji_id")[:1]

            viewcache = request.data.get("viewcache", [])

            if user.username != username:
                save_viewcache_engagements(entity, viewcache)

            realm_match = Realm.objects.filter(slug=username).first()

            if not realm_match and isinstance(entity, Entity):
                target_account = Account.objects.filter(username=username).first()
                if target_account and is_blocked(entity, target_account.entity):
                    empty_paginator = self.pagination_class()
                    empty_page = empty_paginator.paginate_queryset(
                        Post.objects.none(), request, view=self
                    )
                    return empty_paginator.get_paginated_response(
                        PostSerializer(empty_page, many=True).data
                    )

            profile_filter = Q(
                Q(
                    Q(entity__users__username=username)
                    | Q(tagging__entity__users__username=username)
                )
                | Q(
                    Q(entity__realms__slug=username)
                    | Q(tagging__entity__realms__slug=username)
                )
            )

            if archive:
                profile_filter = Q(entity=entity)

            queryset = (
                Post.objects.select_related("entity", "score")
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
                        PostSave.objects.filter(post=OuterRef("pk"), entity=entity)
                    ),
                    entity_reaction=Coalesce(
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

    def get_permissions(self):
        if self.request.method in ["GET"]:
            return [AllowAny()]
        return super().get_permissions()

    def get_authenticators(self):
        """
        VIEW LEVEL FIX: If a guest visits without an 'x-access-token' header,
        completely strip the authentication class out of this execution thread.
        This stops the custom backend from throwing "Token not defined" errors!
        """
        if self.request.method in ["GET"]:
            token = self.request.headers.get("x-access-token")
            if not token:
                return ()  # Returns an empty tuple, bypassing your custom backend entirely for guests!

        return super().get_authenticators()

    def get(self, request, post_id):
        user = self.request.user
        entity = getattr(self.request, "entity", None)
        try:
            user_reaction_subquery = Reaction.objects.filter(
                post=OuterRef("pk"), entity=entity
            ).values("emoji_id")[:1]

            queryset = (
                Post.objects.select_related("entity", "score")
                .prefetch_related(
                    "tagging",
                    "privacy_users",
                    "references",
                    "map_info",
                    "preview",
                )
                .annotate(
                    is_saved=Exists(
                        PostSave.objects.filter(post=OuterRef("pk"), entity=entity)
                    ),
                    entity_reaction=Coalesce(
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

    def get_permissions(self):
        if self.request.method in ["GET"]:
            return [AllowAny()]
        return super().get_permissions()

    def get_authenticators(self):
        """
        VIEW LEVEL FIX: If a guest visits without an 'x-access-token' header,
        completely strip the authentication class out of this execution thread.
        This stops the custom backend from throwing "Token not defined" errors!
        """
        if self.request.method in ["GET"]:
            token = self.request.headers.get("x-access-token")
            if not token:
                return ()  # Returns an empty tuple, bypassing your custom backend entirely for guests!

        return super().get_authenticators()

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
            entity = self.request.entity
            post_id = request.data.get("post_id")
            emoji_id = request.data.get("emoji_id")

            post = Post.objects.get(post_id=post_id)
            emoji = Emoji.objects.get(emoji_id=emoji_id)

            new_reaction_id = str(uuid.uuid4())

            with transaction.atomic():
                Reaction.objects.create(
                    reaction_id=new_reaction_id,
                    post=post,
                    entity=entity,
                    emoji=emoji,
                )

                preview_count_obj = PreviewCount.objects.get(post=post, emoji=emoji)
                preview_count_obj.count += 1
                preview_count_obj.save()

                reaction_ranking = PostScore.objects.get(post=post)
                reaction_ranking.likes_count += 1
                reaction_ranking.save()

                update_ranking_score(post_id, "react", False)
                interaction_score_bump(entity.id, post.entity.id, "LIKE", False)
                if post.entity.type == "realm":
                    follower_interaction_score_bump(
                        entity.id, post.entity.id, "LIKE", False
                    )
                bump_interest_affinity(
                    entity.id, post.interests.values_list("id", flat=True), "LIKE", False
                )

                if post.entity.id != entity.id:
                    service = NotificationService()
                    service.add_notification(
                        referenceID=new_reaction_id,
                        referenceStatus=True,
                        toUserID=post.entity.id,
                        fromUserID=entity.id,
                        content_headline="Post Reaction",
                        content_details=f"{get_entity_display_username(entity)} reacted {emoji.emoji_content} to your post.",
                        type="post_reaction",
                        isRead=False,
                    )

                    sse_sendToUser = post.entity.id
                    sse_sendToDetails = (
                        f"{get_entity_display_username(entity)} reacted {emoji.emoji_content} to your post."
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
            entity = self.request.entity
            post_id = request.data.get("post_id")
            emoji_id = request.data.get("emoji_id")

            post = Post.objects.get(post_id=post_id)
            new_emoji = Emoji.objects.get(emoji_id=emoji_id)

            with transaction.atomic():
                reaction = Reaction.objects.get(post_id=post, entity=entity)
                old_emoji = reaction.emoji
                reaction.emoji = new_emoji
                reaction.save()

                old_preview = PreviewCount.objects.get(post_id=post, emoji_id=old_emoji)
                old_preview.count = max(old_preview.count - 1, 0)
                old_preview.save()

                new_preview = PreviewCount.objects.get(post_id=post, emoji_id=new_emoji)
                new_preview.count += 1
                new_preview.save()

                if post.entity.id != entity.id:
                    service = NotificationService()
                    service.update_content(
                        reaction_id=reaction.reaction_id,
                        new_content=f"{get_entity_display_username(entity)} reacted {new_emoji.emoji_content} to your post.",
                    )

                    sse_sendToUser = post.entity.id
                    sse_sendToDetails = f"{get_entity_display_username(entity)} reacted {new_emoji.emoji_content} to your post."

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
            entity = self.request.entity
            post_id = request.data.get("post_id")
            post = Post.objects.get(post_id=post_id)

            with transaction.atomic():
                reaction = Reaction.objects.get(post=post, entity=entity)

                service = NotificationService()
                service.delete_notification_by_reference_id(
                    reaction_id=reaction.reaction_id,
                )

                reaction_ranking = PostScore.objects.get(post=post)
                reaction_ranking.likes_count -= 1
                reaction_ranking.save()

                update_ranking_score(post_id, "react", True)
                interaction_score_bump(entity.id, post.entity.id, "LIKE", True)
                if post.entity.type == "realm":
                    follower_interaction_score_bump(
                        entity.id, post.entity.id, "LIKE", True
                    )
                bump_interest_affinity(
                    entity.id, post.interests.values_list("id", flat=True), "LIKE", True
                )

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
        if self.request.method in ["GET"]:
            return [AllowAny()]
        if self.request.method == "POST":
            return [IsAuthenticated(), RequiresPermission(Permission.COMMENTS_CREATE)()]
        return super().get_permissions()

    def get_authenticators(self):
        """
        VIEW LEVEL FIX: If a guest visits without an 'x-access-token' header,
        completely strip the authentication class out of this execution thread.
        This stops the custom backend from throwing "Token not defined" errors!
        """
        if self.request.method in ["GET"]:
            token = self.request.headers.get("x-access-token")
            if not token:
                return ()  # Returns an empty tuple, bypassing your custom backend entirely for guests!

        return super().get_authenticators()

    def get(self, request):
        try:
            user = self.request.user
            entity = getattr(self.request, "entity", None)
            post_id = request.GET.get("post_id")
            parent_id = request.GET.get("parent_id")

            post = Post.objects.get(post_id=post_id)

            if parent_id:
                comment = Comment.objects.get(comment_id=parent_id)
                queryset = (
                    Comment.objects.filter(post=post, parent_comment=comment)
                    .select_related("entity")
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
                    .select_related("entity")
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
            entity = getattr(self.request, "entity", None)
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
                        entity=entity,
                    )

                    reaction_ranking = PostScore.objects.get(post=post)
                    reaction_ranking.comments_count += 1
                    reaction_ranking.save()

                    update_ranking_score(post_id, "comment", False)
                    bump_interest_affinity(
                        entity.id, post.interests.values_list("id", flat=True), "COMMENT", False
                    )

                    truncated_comment = (
                        (parent_comment.text[:30] + "...")
                        if len(parent_comment.text) > 30
                        else parent_comment.text
                    )

                    if parent_comment.entity != entity and post.entity != entity:
                        service = NotificationService()
                        service.add_notification(
                            referenceID=new_comment_id,
                            referenceStatus=True,
                            toUserID=parent_comment.entity.id,
                            fromUserID=entity.id,
                            content_headline="Replied Comment",
                            content_details=f'{get_entity_display_username(entity)} replied to your comment "{truncated_comment}"',
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
                                "message": f'{get_entity_display_username(entity)} replied to your comment "{truncated_comment}"',
                                "result": "",
                            },
                            "dateTime": now.isoformat(),
                        }

                        RedisPubSubClient.publish_json(
                            f"events_{parent_comment.entity.id}", data
                        )

                else:
                    new_comment_id = str(uuid.uuid4())
                    Comment.objects.create(
                        comment_id=new_comment_id,
                        parent_comment=None,
                        post=post,
                        text=new_comment,
                        attachment=new_attachment,
                        entity=entity,
                    )

                    reaction_ranking = PostScore.objects.get(post=post)
                    reaction_ranking.comments_count += 1
                    reaction_ranking.save()

                    update_ranking_score(post_id, "comment", False)
                    bump_interest_affinity(
                        entity.id, post.interests.values_list("id", flat=True), "COMMENT", False
                    )

                    if post.entity != entity:
                        service = NotificationService()
                        service.add_notification(
                            referenceID=new_comment_id,
                            referenceStatus=True,
                            toUserID=post.entity.id,
                            fromUserID=entity.id,
                            content_headline="Post Comment",
                            content_details=f"{get_entity_display_username(entity)} commented on your post.",
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
                                "message": f"{get_entity_display_username(entity)} commented on your post.",
                                "result": "",
                            },
                            "dateTime": now.isoformat(),
                        }

                        RedisPubSubClient.publish_json(f"events_{post.entity.id}", data)

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
                assert_owns(request, current_comment)

                if updated_comment.strip() == "" and current_comment.attachment is None:
                    raise ValueError("No comment to save.")

                current_comment.text = updated_comment
                current_comment.updated_at = now()
                current_comment.save()

            return Response("OK", status=status.HTTP_200_OK)
        except PermissionDenied:
            raise
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def delete(self, request):
        try:
            user = self.request.user
            comment_id = request.data.get("comment_id")

            with transaction.atomic():
                current_comment = Comment.objects.get(comment_id=comment_id)
                assert_owns(request, current_comment)

                current_comment.deleted_at = now()
                current_comment.deleted_by = user
                current_comment.save()

            return Response("OK", status=status.HTTP_200_OK)
        except PermissionDenied:
            raise
        except Exception as e:
            return Response({"error": str(e)}, status=500)


class PostSaveView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        try:
            user = self.request.user
            entity = self.request.entity

            post_save_query = (
                PostSave.objects.select_related("post")
                .filter(entity=entity, post__deleted_at=None)
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
            entity = self.request.entity
            post_id = request.data.get("post_id")

            current_post = get_object_or_404(Post, post_id=post_id)
            new_save_query = PostSave.objects.create(post=current_post, entity=entity)

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
            entity = self.request.entity
            post_id = request.data.get("post_id")

            current_post = get_object_or_404(Post, post_id=post_id)
            PostSave.objects.filter(post=current_post, entity=entity).delete()

            return Response(
                {"status": True, "message": "Post has been unsaved"},
                status=200,
            )
        except Exception as e:
            return Response({"error": str(e)}, status=500)


def _empty_preview(status_value="failed"):
    return {
        "url": None,
        "resolved_url": None,
        "title": None,
        "description": None,
        "image": None,
        "site_name": None,
        "favicon": None,
        "status": status_value,
    }


class LinkPreviewView(APIView):
    """
    Resolves a link-preview card for a URL. Two callers, one contract:
    (a) the webapp, authenticated as a normal end user, calling live while a
    user composes a chat message / comment / post caption / diary entry;
    (b) the Node chat server, calling server-to-server with the shared
    X-Internal-Service-Secret header (see AllowsInternalService) after a
    message is saved. Accepts either an already-extracted {"url": "..."}
    (what the webapp's composers know) or raw {"text": "..."} (what Node
    sends - Django owns URL-extraction so that logic lives in exactly one
    place). Always 200 on a well-formed request; a failed unfurl is a
    legitimate result (status: "failed"), not a client error.
    """

    permission_classes = [AllowsInternalService]

    def get_authenticators(self):
        """
        Same guest-safe pattern as CommentsView: the custom auth backend
        raises PermissionDenied outright when there's no 'origin'/'x-nonce'
        header, which real end-user requests always send but the internal-
        service caller (Node) never does. Only run it when an end-user
        token is actually present - the internal-service path is gated by
        AllowsInternalService's header check instead, not by request.user.
        """
        if not self.request.headers.get("x-access-token"):
            return ()
        return super().get_authenticators()

    def post(self, request):
        try:
            url = request.data.get("url")
            text = request.data.get("text")
            is_end_user = bool(request.user and request.user.is_authenticated)
            force_refresh = is_end_user and bool(request.data.get("force_refresh"))

            if not url and not text:
                return Response(
                    {"error": "Provide either 'url' or 'text'."}, status=400
                )

            if not url:
                url = extract_first_url(text)

            if not url:
                return Response(_empty_preview(), status=200)

            result = get_preview(url, force_refresh=force_refresh)

            return Response(result or _empty_preview(), status=200)
        except Exception as e:
            return Response({"error": str(e)}, status=500)


class LinkPreviewImageProxyView(APIView):
    """
    Streams a preview thumbnail/favicon through Django rather than letting
    the browser hotlink the original third-party URL directly - avoids
    leaking viewers' IPs to arbitrary sites on every render and avoids
    mixed-content issues. Goes through the same SSRF guard as the metadata
    fetch (services.link_preview.fetch_image), just with an image
    content-type allowlist instead of text/html.

    Deliberately AllowAny/no auth: this is loaded via plain <img src="...">
    tags in the browser, which cannot attach the x-access-token or
    X-Internal-Service-Secret headers LinkPreviewView otherwise requires.
    The SSRF guard (fetch_image) is the real protection here, not auth -
    same trust model as any public image CDN. This does mean the endpoint
    can be used by anyone to make Django fetch an arbitrary public image
    URL through it; no rate limiting exists yet (matches the rest of this
    codebase, see LinkPreviewView's docstring/plan notes) - worth adding
    a per-IP throttle as a fast-follow.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        url = request.GET.get("url")
        if not url:
            return HttpResponse(status=400)

        result = fetch_image(url)
        if result is None:
            return HttpResponse(status=404)

        body, content_type = result
        response = HttpResponse(body, content_type=content_type)
        response["Cache-Control"] = "public, max-age=86400"
        return response
