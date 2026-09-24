"""
Read side of moments and thoughts: the moments tray, one entity's moments,
"seen", the viewer list, the contacts thought lookup and the moment archive.

Creation lives in Node (server/routes/posts, /moments/create and
/thoughts/create) next to every other post write. Both kinds are ordinary
newsfeed_post rows (on_feed = moment | thought) that stop being live at
expires_at - see newsfeed/services/post_kinds.py.

VIEWS are the existing engagement log, not a new table: one
user_engagement_log row per view (activity_type "view", target_id = post id),
written through the same worker path the feed's viewcache uses. That table is
partitioned by the VIEWER, which suits "have I seen these" (one partition),
and "who viewed this moment" reads it by target_id through the SAI index in
newsfeed/cql/0001_*.cql.
"""

import logging
import uuid

from django.db.models import Exists, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from community.models import Follow
from entity.models import Entity
from entity.serializers import EntitySerializer
from entity.services.blocking import get_blocked_account_ids
from user.models import UserEngagementLog
from user.services.connections import ConnectionHelpers
from user_service.services.rabbitmq import RabbitMQClient, Queues

from .models import Post, PostKind, PostSave, Reaction
from .serializers import PostSerializer
from .services.post_kinds import is_live, live_kinds_filter
from .services.post_visibility import can_view_post, visible_posts_filter

logger = logging.getLogger(__name__)

# Upper bound on entities in one thoughts lookup - a contacts page, not a
# whole address book.
MAX_THOUGHT_ENTITIES = 100

# Upper bound on tray entries. The tray is a horizontal strip; nobody scrolls
# past this, and it caps the one seen-state query below.
MAX_TRAY_ENTRIES = 100


class MomentPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def _annotated_posts(viewer):
    """Post rows with the same per-viewer annotations the feed serializes."""
    return (
        Post.objects.select_related("entity", "score")
        .prefetch_related("tagging", "privacy_users", "references", "preview")
        .annotate(
            is_saved=Exists(PostSave.objects.filter(post=OuterRef("pk"), entity=viewer)),
            entity_reaction=Coalesce(
                Subquery(
                    Reaction.objects.filter(post=OuterRef("pk"), entity=viewer).values(
                        "emoji_id"
                    )[:1]
                ),
                Value(None),
            ),
        )
    )


def _live_moments_visible_to(viewer):
    """Every live moment `viewer` may see - the feed's audience rule."""
    return Post.objects.filter(
        visible_posts_filter(viewer),
        live_kinds_filter([PostKind.MOMENT]),
        deleted_at=None,
        is_archived=False,
    ).exclude(entity_id__in=get_blocked_account_ids(viewer))


def _seen_post_ids(viewer, post_ids):
    """
    Which of `post_ids` the viewer has a view logged for.

    One query on the viewer's own partition, whatever the number of ids - the
    batch that keeps the tray and avatar rings from costing a query each.
    """
    post_ids = [str(pid) for pid in post_ids]
    if not post_ids:
        return set()

    # user_id is a Cassandra uuid column holding the viewer's ENTITY id - the
    # same conversion the worker makes when it writes the row.
    try:
        viewer_uuid = uuid.UUID(str(viewer.id))
    except ValueError:
        return set()

    rows = (
        UserEngagementLog.objects.filter(
            user_id=viewer_uuid, activity_type="view", target_id__in=post_ids
        )
        .allow_filtering()
        .values_list("target_id", flat=True)
    )
    return {str(target_id) for target_id in rows}


class MomentTrayView(APIView):
    """
    GET moments/tray/ - the strip of avatars with live moments.

    Your own entry first (if you have live moments), then everyone else with
    unseen moments before fully-seen ones, newest first within each. Authors
    are who the viewer follows or is connected to - the same people whose
    posts reach their feed.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        viewer = request.entity
        try:
            followed_ids = Follow.objects.filter(
                follower=viewer, status=True
            ).values_list("followee_id", flat=True)
            author_ids = (
                set(ConnectionHelpers(viewer).get_connections())
                | {str(fid) for fid in followed_ids}
                | {str(viewer.id)}
            )

            moments = list(
                _live_moments_visible_to(viewer)
                .filter(entity_id__in=author_ids)
                .order_by("date_posted")
                .values_list("post_id", "entity_id", "date_posted")
            )

            by_author = {}
            for post_id, entity_id, date_posted in moments:
                entry = by_author.setdefault(
                    str(entity_id), {"post_ids": [], "latest_at": date_posted}
                )
                entry["post_ids"].append(post_id)
                entry["latest_at"] = max(entry["latest_at"], date_posted)

            seen = _seen_post_ids(
                viewer, [pid for entry in by_author.values() for pid in entry["post_ids"]]
            )

            entities = {
                str(item.id): item
                for item in Entity.objects.filter(id__in=by_author.keys()).select_related(
                    "users", "realms", "bots"
                )
            }

            tray = []
            for entity_id, entry in by_author.items():
                entity = entities.get(entity_id)
                if entity is None:
                    continue
                is_self = entity_id == str(viewer.id)
                tray.append(
                    {
                        "entity": EntitySerializer(entity).data,
                        "is_self": is_self,
                        "moment_count": len(entry["post_ids"]),
                        # Your own moments are never "unseen" to you: the
                        # worker does not log an author viewing their own post.
                        "has_unseen": not is_self
                        and any(pid not in seen for pid in entry["post_ids"]),
                        "latest_at": entry["latest_at"],
                    }
                )

            tray.sort(
                key=lambda item: (
                    not item["is_self"],
                    not item["has_unseen"],
                    -item["latest_at"].timestamp(),
                )
            )

            return Response({"results": tray[:MAX_TRAY_ENTRIES]})
        except Exception as e:
            logger.exception("MomentTrayView.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class EntityMomentsView(APIView):
    """
    GET moments/entity/<entity_id>/ - one entity's live moments, oldest first
    (the order they play in), each with `seen` for the viewer.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, entity_id):
        viewer = request.entity
        try:
            posts = list(
                _annotated_posts(viewer)
                .filter(
                    visible_posts_filter(viewer),
                    live_kinds_filter([PostKind.MOMENT]),
                    entity_id=entity_id,
                    deleted_at=None,
                    is_archived=False,
                )
                .exclude(entity_id__in=get_blocked_account_ids(viewer))
                .distinct()
                .order_by("date_posted")
            )

            is_self = str(entity_id) == str(viewer.id)
            seen = set() if is_self else _seen_post_ids(viewer, [p.post_id for p in posts])

            results = []
            for post, data in zip(posts, PostSerializer(posts, many=True).data):
                results.append({**data, "seen": is_self or post.post_id in seen})

            return Response({"results": results})
        except Exception as e:
            logger.exception("EntityMomentsView.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MomentSeenView(APIView):
    """
    POST moments/<post_id>/seen/ - record that the viewer watched a moment.

    Written straight away, unlike feed views, which ride along on the next
    feed request: a moment's ring has to turn grey as soon as it is watched.
    It goes through the SAME worker handler (save_viewcache_engagements), so a
    moment view is logged exactly like a post view and nothing downstream
    needs to know the difference.

    Idempotent per viewer: an already-logged view is not written again, so
    rewatching does not stack rows the viewer list would have to collapse.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, post_id):
        viewer = request.entity
        try:
            post = get_object_or_404(
                Post, post_id=post_id, on_feed=PostKind.MOMENT, deleted_at=None
            )

            if not is_live(post) or not can_view_post(post, viewer):
                return Response(
                    {"message": "Moment not available"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            is_author = str(post.entity_id) == str(viewer.id)
            if is_author or _seen_post_ids(viewer, [post.post_id]):
                return Response({"status": True, "recorded": False})

            duration = request.data.get("duration", 0)
            try:
                duration = max(float(duration), 0.0)
            except (TypeError, ValueError):
                duration = 0.0

            RabbitMQClient.publish_on_commit(
                Queues.SAVE_VIEWCACHE_ENGAGEMENTS,
                {
                    "entity_id": viewer.id,
                    "view_cache": [
                        {
                            "post_id": post.post_id,
                            "post_owner_id": str(post.entity_id),
                            "duration": duration,
                            "created_at": now().isoformat(),
                        }
                    ],
                },
            )

            return Response({"status": True, "recorded": True})
        except Exception as e:
            logger.exception("MomentSeenView.post failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MomentViewersView(APIView):
    """
    GET moments/<post_id>/viewers/ - who watched a moment. Author only, and
    expired moments included (the archive shows them).

    Entity-based: a viewer can be a user or a page, resolved through
    entity_entity like everything else. One row per viewer, at their most
    recent view.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = MomentPagination

    def get(self, request, post_id):
        viewer = request.entity
        try:
            post = get_object_or_404(
                Post, post_id=post_id, on_feed=PostKind.MOMENT, deleted_at=None
            )

            # 404, not 403, for anyone but the author: the viewer list of
            # somebody else's moment is not something to confirm exists.
            if str(post.entity_id) != str(viewer.id):
                return Response(
                    {"message": "Moment not available"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            rows = (
                UserEngagementLog.objects.filter(
                    target_id=str(post.post_id), activity_type="view"
                )
                .allow_filtering()
                .values_list("user_id", "activity_time")
            )

            latest = {}
            for user_id, viewed_at in rows:
                key = str(user_id)
                if key not in latest or viewed_at > latest[key]:
                    latest[key] = viewed_at
            latest.pop(str(viewer.id), None)

            blocked = {str(bid) for bid in get_blocked_account_ids(viewer)}
            entities = {
                str(item.id): item
                for item in Entity.objects.filter(id__in=latest.keys()).select_related(
                    "users", "realms", "bots"
                )
                if str(item.id) not in blocked
            }

            ordered = sorted(
                (
                    (entities[entity_id], viewed_at)
                    for entity_id, viewed_at in latest.items()
                    if entity_id in entities
                ),
                key=lambda pair: pair[1],
                reverse=True,
            )

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(ordered, request, view=self)
            return paginator.get_paginated_response(
                [
                    {"entity": EntitySerializer(entity).data, "viewed_at": viewed_at}
                    for entity, viewed_at in page
                ]
            )
        except Exception as e:
            logger.exception("MomentViewersView.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ThoughtsView(APIView):
    """
    GET thoughts/?entity_ids=a,b,c - the live thought of each of those
    entities that has one, for rendering over their avatars. One query for the
    whole list, so a contacts page costs one request.

    Returns {"results": {entity_id: thought}}; an entity with no live (or no
    visible) thought is simply absent.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        viewer = request.entity
        try:
            raw = request.query_params.get("entity_ids", "")
            entity_ids = [eid.strip() for eid in raw.split(",") if eid.strip()]
            entity_ids = list(dict.fromkeys(entity_ids))[:MAX_THOUGHT_ENTITIES]

            if not entity_ids:
                return Response({"results": {}})

            thoughts = (
                Post.objects.filter(
                    visible_posts_filter(viewer),
                    live_kinds_filter([PostKind.THOUGHT]),
                    entity_id__in=entity_ids,
                    deleted_at=None,
                    is_archived=False,
                )
                .exclude(entity_id__in=get_blocked_account_ids(viewer))
                .distinct()
                # Newest first, so if two were ever live at once the latest
                # wins - posting a thought ends the previous one, but this
                # should not depend on that having held.
                .order_by("entity_id", "-date_posted")
                .values("post_id", "entity_id", "caption", "date_posted", "expires_at")
            )

            results = {}
            for thought in thoughts:
                results.setdefault(
                    str(thought["entity_id"]),
                    {
                        "post_id": thought["post_id"],
                        # `content` mirrors what the create route accepts, so a
                        # thought can grow fields without a new response shape.
                        "content": {"text": thought["caption"] or ""},
                        "date_posted": thought["date_posted"],
                        "expires_at": thought["expires_at"],
                    },
                )

            return Response({"results": results})
        except Exception as e:
            logger.exception("ThoughtsView.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MomentArchiveView(APIView):
    """
    GET archive/moments/ - the acting entity's own moments, live AND expired,
    newest first. The archive's "Moments" tab; its "Feed" tab is the existing
    profile endpoint with archive=true.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = MomentPagination

    def get(self, request):
        viewer = request.entity
        try:
            queryset = (
                _annotated_posts(viewer)
                .filter(entity=viewer, on_feed=PostKind.MOMENT, deleted_at=None)
                .order_by("-date_posted")
            )

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(queryset, request, view=self)
            return paginator.get_paginated_response(
                PostSerializer(page, many=True).data
            )
        except Exception as e:
            logger.exception("MomentArchiveView.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
