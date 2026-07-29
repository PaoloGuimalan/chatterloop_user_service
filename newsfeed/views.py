from django.shortcuts import render
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.db import models
from django.db.models import (
    Q,
    CharField,
    Count,
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
    CommentReaction,
    CommentPreviewCount,
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
    CommentPreviewCountSerializer,
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
from .services.comment_mentions import (
    extract_mention_handles,
    notify_comment_mentions,
    resolve_mentioned_entities,
)
from entity.utils import get_entity_display_username, get_entity_profile_path
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
from community.models import Follow, Realm
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
            # Was values_list("follower_id"), which returned the CURRENT
            # entity's own id for every row rather than the things it follows -
            # so this list was effectively useless. followee_id is the target.
            followed_realm_ids = list(
                Follow.objects.filter(follower=entity).values_list(
                    "followee_id", flat=True
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
                    raise PermissionDenied("You do not own all of the specified posts.")

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

                # Created on first use rather than pre-seeded for every
                # (post, emoji) pair - a zero row is indistinguishable from no
                # row to every reader (the clients filter count > 0, and the
                # emoji picker reads the Emoji table, not this one). The
                # unique constraint on (post, emoji) is what makes this safe
                # under concurrent reactions: get_or_create catches the
                # IntegrityError and re-reads instead of double-counting.
                preview_count_obj, _ = PreviewCount.objects.get_or_create(
                    post=post, emoji=emoji, defaults={"count": 0}
                )
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
                    entity.id,
                    post.interests.values_list("id", flat=True),
                    "LIKE",
                    False,
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
                    sse_sendToDetails = f"{get_entity_display_username(entity)} reacted {emoji.emoji_content} to your post."

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

                # Decrement side is tolerant of a missing row: with rows
                # created on demand, "no row" already means zero, so there is
                # nothing to take away.
                old_preview = PreviewCount.objects.filter(
                    post=post, emoji=old_emoji
                ).first()
                if old_preview:
                    old_preview.count = max(old_preview.count - 1, 0)
                    old_preview.save()

                new_preview, _ = PreviewCount.objects.get_or_create(
                    post=post, emoji=new_emoji, defaults={"count": 0}
                )
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

                preview_count = PreviewCount.objects.filter(
                    post=post, emoji=emoji
                ).first()
                if preview_count:
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


class CommentReactionsView(APIView):
    """
    Reactions on a comment - PostReactionsView one level down, same verbs and
    same payload keys (`emoji_id` plus `comment_id` where the post view takes
    `post_id`), so the client's reaction flow is identical either side.

    Two deliberate differences from the post version:

    * PostScore is NOT touched. Reacting to a comment is not a reaction to the
      post, and PostScore.likes_count is what the card renders as the post's
      like count - feeding comment reactions into it would inflate a number
      the user can see and cross-check. Ranking is left alone for the same
      reason.
    * CommentPreviewCount rows are created on demand from the start (the post
      side only got there after the seeding was removed).
    """

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), RequiresPermission(Permission.COMMENTS_CREATE)()]
        return super().get_permissions()

    def _notify_comment_author(self, comment, entity, emoji, reaction_id, verb):
        """
        Ping the comment's author, unless they are the one reacting.

        `verb` differs only in wording between a new reaction and a changed
        one; the reference id stays the reaction's, so update_content() can
        rewrite the same notification instead of stacking a second.
        """
        if comment.entity_id == entity.id:
            return

        details = (
            f"{get_entity_display_username(entity)} reacted "
            f"{emoji.emoji_content} to your comment."
        )

        service = NotificationService()
        if verb == "updated":
            service.update_content(reaction_id=reaction_id, new_content=details)
        else:
            service.add_notification(
                referenceID=reaction_id,
                referenceStatus=True,
                toUserID=comment.entity_id,
                fromUserID=entity.id,
                content_headline="Comment Reaction",
                content_details=details,
                type="comment_reaction",
                isRead=False,
            )

        RedisPubSubClient.publish_json(
            f"events_{comment.entity_id}",
            {
                "logType": None,
                "pod": "podless",
                "event": "notifications",
                "message": {
                    "status": True,
                    "auth": True,
                    "message": details,
                    "result": "",
                },
                "dateTime": datetime.now().isoformat(),
            },
        )

    def post(self, request, *args, **kwargs):
        try:
            user = self.request.user
            entity = self.request.entity
            comment_id = request.data.get("comment_id")
            emoji_id = request.data.get("emoji_id")

            comment = Comment.objects.get(comment_id=comment_id, deleted_at__isnull=True)
            emoji = Emoji.objects.get(emoji_id=emoji_id)

            new_reaction_id = str(uuid.uuid4())

            with transaction.atomic():
                CommentReaction.objects.create(
                    reaction_id=new_reaction_id,
                    comment=comment,
                    entity=entity,
                    emoji=emoji,
                )

                preview_count_obj, _ = CommentPreviewCount.objects.get_or_create(
                    comment=comment, emoji=emoji, defaults={"count": 0}
                )
                preview_count_obj.count += 1
                preview_count_obj.save()

                # The reactor is engaging with the comment's AUTHOR, so the
                # interaction bump is between those two entities - not the
                # post's author, who may be someone else entirely.
                if comment.entity_id != entity.id:
                    interaction_score_bump(entity.id, comment.entity_id, "LIKE", False)

                self._notify_comment_author(
                    comment, entity, emoji, new_reaction_id, "added"
                )

            return Response(
                {"message": "Reaction has been added"}, status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def put(self, request, *args, **kwargs):
        try:
            user = self.request.user
            entity = self.request.entity
            comment_id = request.data.get("comment_id")
            emoji_id = request.data.get("emoji_id")

            comment = Comment.objects.get(comment_id=comment_id, deleted_at__isnull=True)
            new_emoji = Emoji.objects.get(emoji_id=emoji_id)

            with transaction.atomic():
                reaction = CommentReaction.objects.get(comment=comment, entity=entity)
                old_emoji = reaction.emoji
                reaction.emoji = new_emoji
                reaction.save()

                old_preview = CommentPreviewCount.objects.filter(
                    comment=comment, emoji=old_emoji
                ).first()
                if old_preview:
                    old_preview.count = max(old_preview.count - 1, 0)
                    old_preview.save()

                new_preview, _ = CommentPreviewCount.objects.get_or_create(
                    comment=comment, emoji=new_emoji, defaults={"count": 0}
                )
                new_preview.count += 1
                new_preview.save()

                self._notify_comment_author(
                    comment, entity, new_emoji, reaction.reaction_id, "updated"
                )

            return Response(
                {"message": "Reaction has been updated"}, status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def delete(self, request, *args, **kwargs):
        try:
            user = self.request.user
            entity = self.request.entity
            comment_id = request.data.get("comment_id")

            comment = Comment.objects.get(comment_id=comment_id)

            with transaction.atomic():
                reaction = CommentReaction.objects.get(comment=comment, entity=entity)

                service = NotificationService()
                service.delete_notification_by_reference_id(
                    reaction_id=reaction.reaction_id,
                )

                if comment.entity_id != entity.id:
                    interaction_score_bump(entity.id, comment.entity_id, "LIKE", True)

                emoji = reaction.emoji
                reaction.delete()

                preview_count = CommentPreviewCount.objects.filter(
                    comment=comment, emoji=emoji
                ).first()
                if preview_count:
                    preview_count.count = max(preview_count.count - 1, 0)
                    preview_count.save()

            return Response(
                {"message": "Reaction has been deleted"}, status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response({"error": str(e)}, status=500)


class CommentReactionsCountView(APIView):
    """ReactionsCountView for a comment - the tallies behind its reaction row."""

    permission_classes = [IsAuthenticated]

    def get(self, request, comment_id):
        try:
            user = self.request.user
            comment = Comment.objects.get(comment_id=comment_id)
            query_set = CommentPreviewCount.objects.filter(comment=comment)

            serialized_result = CommentPreviewCountSerializer(query_set, many=True)
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

    @staticmethod
    def _with_reactions(queryset, entity):
        """
        Attach each comment's reaction tallies and the viewer's own reaction,
        the same two things PostSerializer gets for a post.

        `preview` is prefetched rather than joined so a comment with several
        emoji tallies doesn't fan the page out into duplicate rows. The
        entity_reaction subquery is skipped entirely for guests - this GET is
        AllowAny, so `entity` is None for them and there is no "your reaction"
        to look up.
        """
        queryset = queryset.prefetch_related("preview")

        if entity is None:
            return queryset

        return queryset.annotate(
            entity_reaction=Coalesce(
                Subquery(
                    CommentReaction.objects.filter(
                        comment=OuterRef("pk"), entity=entity
                    ).values("emoji_id")[:1]
                ),
                Value(None),
                # On the Coalesce, not on the Value: the subquery resolves to
                # the FK field while a typed Value resolves to CharField, and
                # Django refuses to infer an output_field from the two
                # ("mixed types: ForeignKey, CharField"). Emoji's pk IS a
                # CharField, so naming it here is just making that explicit.
                output_field=CharField(),
            )
        )

    def get(self, request):
        try:
            user = self.request.user
            entity = getattr(self.request, "entity", None)
            post_id = request.GET.get("post_id")
            parent_id = request.GET.get("parent_id")

            post = Post.objects.get(post_id=post_id)

            if parent_id:
                comment = Comment.objects.get(comment_id=parent_id)
                queryset = self._with_reactions(
                    # Soft-deleted comments are excluded: delete() below only
                    # stamps deleted_at, so without this a deleted comment
                    # came straight back on the next fetch.
                    Comment.objects.filter(
                        post=post, parent_comment=comment, deleted_at__isnull=True
                    ).select_related("entity"),
                    entity,
                ).order_by("created_at")

                paginator = self.pagination_class()
                paginated_queryset = paginator.paginate_queryset(
                    queryset, request, view=self
                )

                serialized_result = CommentSerializer(paginated_queryset, many=True)
                data = paginator.get_paginated_response(serialized_result.data)

                return data
            else:
                queryset = (
                    self._with_reactions(
                        # Same soft-delete exclusion as the replies branch above.
                        Comment.objects.filter(
                            post=post, parent_comment=None, deleted_at__isnull=True
                        ).select_related("entity"),
                        entity,
                    )
                    # Drives the "View N replies" affordance, so the client
                    # knows a thread HAS children without fetching them. The
                    # filter matches the replies branch above (soft-deleted
                    # replies are not returned, so they must not be counted
                    # either) - a plain Count("replies") would promise replies
                    # that then come back as an empty page.
                    #
                    # distinct=True matters more now: `preview` is prefetched
                    # rather than joined, but any future join here would
                    # otherwise multiply this count.
                    .annotate(
                        reply_count=Count(
                            "replies",
                            filter=Q(replies__deleted_at__isnull=True),
                            distinct=True,
                        )
                    )
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

            # `or ""` because text is nullable: an attachment-only comment
            # legitimately sends no text, and .strip() on None is a 500.
            if (new_comment or "").strip() == "" and new_attachment is None:
                raise ValueError("No comment to save.")

            # `replied_to` is the comment the user actually aimed at;
            # `parent_comment` is where the new row is stored. They differ only
            # when replying to a REPLY: threads are flattened to two levels, so
            # the row re-parents to that reply's top-level ancestor rather than
            # nesting a third time. The thread then stays one paginated list
            # per top-level comment, and a soft-deleted middle comment cannot
            # strand grandchildren with no reachable parent.
            replied_to = None
            parent_comment = None
            if parent_id:
                replied_to = Comment.objects.select_related(
                    "entity", "parent_comment"
                ).get(comment_id=parent_id)
                parent_comment = replied_to.parent_comment or replied_to

            # A mention IS the text (see services/comment_mentions.py) - the
            # text is parsed for "@handle" purely to send the notification,
            # and nothing about the parse is stored. The text itself is never
            # rewritten here: when a reply gets flattened, it is the CLIENT
            # that pre-fills "@handle " in the compose box, exactly as the
            # messenger does. The person actually replied to is notified
            # either way by the "Replied Comment" branch below.
            mention_entities = resolve_mentioned_entities(new_comment, entity)

            with transaction.atomic():
                new_comment_id = str(uuid.uuid4())
                comment = Comment.objects.create(
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
                    entity.id,
                    post.interests.values_list("id", flat=True),
                    "COMMENT",
                    False,
                )

                # Entities already pinged for THIS comment, so a mention does
                # not arrive as a second notification for the same event.
                notified_ids = []

                if replied_to is not None:
                    # `or ""` guards an attachment-only parent, whose text is
                    # None - len(None) used to 500 the whole reply.
                    parent_text = replied_to.text or ""
                    truncated_comment = (
                        (parent_text[:30] + "...")
                        if len(parent_text) > 30
                        else parent_text
                    )

                    if replied_to.entity != entity and post.entity != entity:
                        reply_text = (
                            f"{get_entity_display_username(entity)} replied to your "
                            f'comment "{truncated_comment}"'
                        )
                        service = NotificationService()
                        service.add_notification(
                            referenceID=new_comment_id,
                            referenceStatus=True,
                            toUserID=replied_to.entity.id,
                            fromUserID=entity.id,
                            content_headline="Replied Comment",
                            content_details=reply_text,
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
                                "message": reply_text,
                                "result": "",
                            },
                            "dateTime": now.isoformat(),
                        }

                        RedisPubSubClient.publish_json(
                            f"events_{replied_to.entity.id}", data
                        )
                        notified_ids.append(replied_to.entity.id)

                else:
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
                        notified_ids.append(post.entity.id)

                notify_comment_mentions(
                    comment, entity, mention_entities, notified_ids
                )

            return Response("OK", status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def put(self, request):
        try:
            user = self.request.user
            entity = getattr(self.request, "entity", None)
            comment_id = request.data.get("comment_id")
            updated_comment = request.data.get("updated_comment")

            with transaction.atomic():
                current_comment = Comment.objects.get(comment_id=comment_id)
                assert_owns(request, current_comment)

                if (
                    updated_comment or ""
                ).strip() == "" and current_comment.attachment is None:
                    raise ValueError("No comment to save.")

                # Handles the text ALREADY had, captured before the edit is
                # applied. An edit only notifies people the edit newly names -
                # fixing a typo must not re-ping everyone in the comment.
                previous_handles = set(extract_mention_handles(current_comment.text))

                current_comment.text = updated_comment
                current_comment.updated_at = now()
                current_comment.save()

                newly_mentioned = [
                    mentioned
                    for mentioned in resolve_mentioned_entities(updated_comment, entity)
                    if get_entity_profile_path(mentioned).lower() not in previous_handles
                ]
                notify_comment_mentions(current_comment, entity, newly_mentioned)

            return Response("OK", status=status.HTTP_200_OK)
        except PermissionDenied:
            raise
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def delete(self, request):
        try:
            user = self.request.user
            entity = self.request.entity
            comment_id = request.data.get("comment_id")

            with transaction.atomic():
                current_comment = Comment.objects.get(comment_id=comment_id)
                assert_owns(request, current_comment)

                deleted_at = now()
                current_comment.deleted_at = deleted_at
                current_comment.deleted_by = entity
                current_comment.save()

                # Deleting a top-level comment takes its thread with it.
                # Replies are only reachable through their parent (the GET
                # needs a parent_id, and the top-level list excludes deleted
                # rows), so leaving them alive just hides them forever while
                # they keep counting toward the post's comment total.
                #
                # Ids are collected BEFORE the cascade: .update() reports how
                # many rows it touched, not which, and afterwards the filter
                # no longer matches them.
                deleted_ids = [current_comment.comment_id]
                if current_comment.parent_comment_id is None:
                    reply_ids = list(
                        current_comment.replies.filter(
                            deleted_at__isnull=True
                        ).values_list("comment_id", flat=True)
                    )
                    if reply_ids:
                        Comment.objects.filter(comment_id__in=reply_ids).update(
                            deleted_at=deleted_at, deleted_by=entity
                        )
                        deleted_ids.extend(reply_ids)

                # post() counts EVERY comment, replies included, so removal has
                # to give back the same amount - the whole thread, not just the
                # row that was clicked. Without this the post's comment count
                # only ever grew.
                reaction_ranking = PostScore.objects.filter(
                    post_id=current_comment.post_id
                ).first()
                if reaction_ranking:
                    reaction_ranking.comments_count = max(
                        0, reaction_ranking.comments_count - len(deleted_ids)
                    )
                    reaction_ranking.save()

                    # Recomputes ranking_score off the count just written, so
                    # this has to follow the save above, not precede it.
                    update_ranking_score(current_comment.post_id, "comment", True)

            # Deliberately AFTER the atomic block: Mongo is not part of the
            # Postgres transaction, so doing this inside would leave the
            # notifications gone but the comment restored if the transaction
            # rolled back. Same treatment a removed reaction gets - every
            # comment notification (post comment, reply, mention) carries its
            # comment_id as referenceID, so the whole thread's worth goes in
            # one call.
            service = NotificationService()
            notified_entities = service.delete_notifications_by_reference_ids(
                deleted_ids
            )

            # Tell anyone holding a now-stale list to refetch, otherwise the
            # notification sits on their screen pointing at a deleted comment
            # until they reload the app themselves.
            for notified_entity_id in notified_entities:
                RedisPubSubClient.publish_json(
                    f"events_{notified_entity_id}",
                    {
                        "logType": None,
                        "pod": "podless",
                        "event": "notifications_reload",
                        "message": {
                            "status": True,
                            "auth": True,
                            "message": "",
                            "result": "",
                        },
                        "dateTime": datetime.now().isoformat(),
                    },
                )

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


class NewsfeedPostSearchView(APIView):
    """
    Content search for the redesigned Search page - NEW endpoint, versioned
    v2 alongside the entity search v2 family. Nothing searched post captions
    before this, and the live mobile app pins every pre-existing newsfeed
    route, so this is purely additive.

    GET /api/newsfeed/search/v2/posts/<query>/?page=&page_size=

    Paginated (drives the Content "See all" infinite scroll). Results are
    RANKED - PostScore.ranking_score DESC then recency - so relevant posts
    land on top rather than plain newest-first. Queryset + card shape live
    in services/post_search.py because the entity app's search overview
    endpoint reuses them for the page-init call.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request, query):
        entity = self.request.entity
        try:
            from .services.post_search import (
                build_post_search_queryset,
                serialize_post_hit,
            )

            blocked_ids = get_blocked_account_ids(entity)
            queryset = build_post_search_queryset(entity, query, blocked_ids)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(queryset, request, view=self)
            return paginator.get_paginated_response(
                [serialize_post_hit(post) for post in page]
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
