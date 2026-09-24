"""
Read side of moments and thoughts, plus the few writes that are edits rather
than creation: the moments tray and ring status, one entity's moments, "seen"
and the viewer lists, the Thoughts rail and lookup, editing a thought in
place, a moment's settings, and the moment archive.

Creation lives in Node (server/routes/posts, /moments/create and
/thoughts/create) next to every other post write. Both kinds are ordinary
newsfeed_post rows (on_feed = moment | thought) that stop being live at
expires_at - see newsfeed/services/post_kinds.py. Kind-specific settings (a
thought's mood, a moment's allow_replies) live in Post.details.

VIEWS are the existing engagement log, not a new table: one
user_engagement_log row per view (activity_type "view", target_id = post id),
written through the same worker path the feed's viewcache uses. That table is
partitioned by the VIEWER, which suits "have I seen these" (one partition),
and "who viewed this" reads it by target_id through the SAI index in
newsfeed/cql/0001_*.cql.

REACTIONS are the ordinary post Reaction rows (PostReactionsView, which also
gates and words them by kind). REPLIES are chat messages whose replyingTo is
{type: <kind>, id: <post id>} - read from Mongo for the viewer list.
"""

import logging
import uuid
from datetime import timezone as dt_timezone

from django.db.models import Exists, F, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from community.models import Follow
from entity.models import Connection, Entity
from entity.serializers import EntitySerializer
from entity.services.blocking import get_blocked_account_ids
from user.ext_models.mongomodels import Message
from user.models import UserEngagementLog
from user.services.connections import ConnectionHelpers
from user_service.services.rabbitmq import RabbitMQClient, Queues

from .models import (
    THOUGHT_MAX_LENGTH,
    THOUGHT_MOODS,
    Post,
    PostKind,
    PostSave,
    Reaction,
)
from .serializers import PostSerializer
from .services.post_kinds import is_live, live_kinds_filter
from .services.post_visibility import can_view_post, visible_posts_filter

logger = logging.getLogger(__name__)

# Upper bound on entities in one thoughts / ring-status lookup - a page of
# avatars, not a whole address book.
MAX_BATCH_ENTITIES = 100

# Upper bound on tray entries. The tray is a horizontal strip; nobody scrolls
# past this, and it caps the one seen-state query below.
MAX_TRAY_ENTRIES = 100

# Upper bound on the Thoughts rail.
MAX_RAIL_THOUGHTS = 50

# Who a moment or thought can be shown to from the edit screens. "Close" is
# designed but hidden until a close-friends list exists (Node mirror:
# EPHEMERAL_AUDIENCES).
EPHEMERAL_AUDIENCES = ("public", "connections")


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


def _live_visible(viewer, kind):
    """Every live post of `kind` that `viewer` may see - the feed's audience rule."""
    return Post.objects.filter(
        visible_posts_filter(viewer),
        live_kinds_filter([kind]),
        deleted_at=None,
        is_archived=False,
    ).exclude(entity_id__in=get_blocked_account_ids(viewer))


def _circle_ids(viewer):
    """
    The entities whose moments and thoughts reach `viewer`: who they follow or
    are connected to - the same people whose posts reach their feed - and
    themselves.
    """
    followed_ids = Follow.objects.filter(follower=viewer, status=True).values_list(
        "followee_id", flat=True
    )
    return (
        {str(eid) for eid in ConnectionHelpers(viewer).get_connections()}
        | {str(fid) for fid in followed_ids}
        | {str(viewer.id)}
    )


# How many connections the Thoughts rail lists after the thoughts.
MAX_RAIL_SUGGESTIONS = 60


def _ranked_connections(viewer, limit=MAX_RAIL_SUGGESTIONS, exclude=()):
    """
    The viewer's connections - people AND pages - most-interacted-with first,
    for the Thoughts rail to list after the thoughts themselves. Both
    directions of the edge count (who started the connection does not
    matter). The client puts whoever is online first; presence lives in Node,
    not here, so this rank is the tie-break within online / offline.
    """
    scored = {}
    for field, other in (("action_by", "involved_entity_id"), ("involved_entity", "action_by_id")):
        for eid, score in (
            Connection.objects.filter(**{field: viewer}, status=True)
            .order_by(
                F("interaction_score").desc(nulls_last=True),
                F("last_interaction_at").desc(nulls_last=True),
            )
            .values_list(other, "interaction_score")[: limit * 2]
        ):
            eid = str(eid)
            score = score or 0
            if eid not in scored or score > scored[eid]:
                scored[eid] = score
    skip = {str(viewer.id), *(str(e) for e in exclude)}
    skip |= {str(bid) for bid in get_blocked_account_ids(viewer)}
    ids = [
        eid
        for eid, _ in sorted(scored.items(), key=lambda item: item[1], reverse=True)
        if eid not in skip
    ][:limit]
    entities = _entities_by_id(ids)
    return [EntitySerializer(entities[eid]).data for eid in ids if eid in entities]


def _entities_by_id(ids):
    return {
        str(item.id): item
        for item in Entity.objects.filter(id__in=list(ids)).select_related(
            "users", "realms", "bots"
        )
    }


def _parse_entity_ids(request):
    raw = request.query_params.get("entity_ids", "")
    entity_ids = [eid.strip() for eid in raw.split(",") if eid.strip()]
    return list(dict.fromkeys(entity_ids))[:MAX_BATCH_ENTITIES]


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


SHARED_POST_MEDIA = "shared_post"


def _first_media(post):
    """A post's first photo/video reference (not a shared-post pointer)."""
    for ref in post.references.all():
        if ref.reference_media_type and SHARED_POST_MEDIA not in ref.reference_media_type:
            return ref
    return None


def _shared_pointer(post):
    """The id a shared post (or shared-post moment) points at, if any."""
    if post.file_type != SHARED_POST_MEDIA:
        return None
    for ref in post.references.all():
        if ref.reference_media_type and SHARED_POST_MEDIA in ref.reference_media_type:
            return ref.reference
    return None


def _shared_previews(viewer, moments):
    """
    {moment post_id: preview} for the shared-post moments among `moments` -
    what a tile, a reply chip or the archive draws for them without fetching
    the post: its author, caption, and a thumbnail. The thumbnail is the
    shared post's first photo/video, or - when that post is itself a share -
    the ORIGINAL's, one level down (the same nesting a post card renders).

    `available` is false when the shared post is gone or the viewer may not
    see it; nothing else about it is returned then.
    """
    pointers = {m.post_id: _shared_pointer(m) for m in moments}
    pointers = {k: v for k, v in pointers.items() if v}
    if not pointers:
        return {}

    def load(ids):
        return {
            p.post_id: p
            for p in Post.objects.select_related("entity")
            .prefetch_related("references", "privacy_users")
            .filter(post_id__in=set(ids), deleted_at=None)
        }

    shared = load(pointers.values())
    originals = load(
        pointer for pointer in (_shared_pointer(p) for p in shared.values()) if pointer
    )

    previews = {}
    for moment_id, shared_id in pointers.items():
        post = shared.get(shared_id)
        if post is None or not can_view_post(post, viewer):
            previews[moment_id] = {"post_id": shared_id, "available": False}
            continue
        media = _first_media(post)
        original = originals.get(_shared_pointer(post) or "")
        if media is None and original is not None and can_view_post(original, viewer):
            media = _first_media(original)
        previews[moment_id] = {
            "post_id": shared_id,
            "available": True,
            "author": EntitySerializer(post.entity).data,
            "caption": post.caption or "",
            "is_share": original is not None,
            "thumbnail": media.reference if media else None,
            "media_type": media.reference_media_type if media else None,
        }
    return previews


def _moment_preview(post, shared_preview=None):
    """
    What a tray tile draws for one moment: its media (or, for a shared post,
    the shared post's - see _shared_previews), caption and lifetime. Reads the
    prefetched references.
    """
    references = list(post.references.all())
    first = references[0] if references else None
    is_shared = post.file_type == "shared_post"
    if is_shared:
        shared_preview = shared_preview or {}
        return {
            "post_id": post.post_id,
            "caption": post.caption or "",
            "is_shared": True,
            "shared_post_id": first.reference if first else None,
            "thumbnail": shared_preview.get("thumbnail"),
            "media_type": shared_preview.get("media_type"),
            "shared_preview": shared_preview or None,
            "date_posted": post.date_posted,
            "expires_at": post.expires_at,
        }
    return {
        "post_id": post.post_id,
        "caption": post.caption or "",
        "is_shared": is_shared,
        "shared_post_id": first.reference if (is_shared and first) else None,
        "thumbnail": None if (is_shared or first is None) else first.reference,
        "media_type": None
        if (is_shared or first is None)
        else first.reference_media_type,
        "date_posted": post.date_posted,
        "expires_at": post.expires_at,
    }


def _my_reactions(viewer, posts):
    """{post_id: emoji_id} of the viewer's own reactions to these posts."""
    return {
        str(post_id): str(emoji_id)
        for post_id, emoji_id in Reaction.objects.filter(
            entity=viewer, post_id__in=[p.post_id for p in posts]
        ).values_list("post_id", "emoji_id")
    }


def _thought_payload(post, author=None, my_reaction=None):
    """One thought as every thoughts endpoint returns it."""
    details = post.details or {}
    payload = {
        "post_id": post.post_id,
        "entity_id": str(post.entity_id),
        # `content` mirrors what the create route accepts, so a thought can
        # grow fields without a new response shape.
        "content": {"text": post.caption or "", "mood": details.get("mood")},
        "privacy_status": post.privacy_status,
        "date_posted": post.date_posted,
        "expires_at": post.expires_at,
        # The viewer's own reaction (an emoji id), so the detail sheet shows
        # it picked and a second tap takes it back.
        "my_reaction": my_reaction,
    }
    if author is not None:
        payload["author"] = EntitySerializer(author).data
    return payload


def _latest_live_thoughts(viewer, entity_ids):
    """{entity_id: newest live thought visible to viewer} for these entities."""
    thoughts = (
        _live_visible(viewer, PostKind.THOUGHT)
        .filter(entity_id__in=list(entity_ids))
        .distinct()
        # Newest first, so if two were ever live at once the latest wins -
        # posting a thought ends the previous one, but this should not depend
        # on that having held.
        .order_by("entity_id", "-date_posted")
    )
    latest = {}
    for thought in thoughts:
        latest.setdefault(str(thought.entity_id), thought)
    return latest


def _aware(value):
    """
    Cassandra (cqlengine) and pymongo hand back NAIVE datetimes that are UTC.
    Serialized as-is they carry no offset, so a browser reads them as LOCAL
    time - "8h ago" for a view a minute old in UTC+8. Tagging them UTC fixes
    that, and lets them be compared with Django's aware datetimes.
    """
    if value is None or getattr(value, "tzinfo", None) is not None:
        return value
    return value.replace(tzinfo=dt_timezone.utc)


def _viewers_of(post):
    """
    {entity_id: last viewed_at} for a moment/thought, from the engagement log
    by target_id (the SAI index). The author's own views are never logged.
    """
    rows = (
        UserEngagementLog.objects.filter(target_id=str(post.post_id), activity_type="view")
        .allow_filtering()
        .values_list("user_id", "activity_time")
    )
    latest = {}
    for user_id, viewed_at in rows:
        key = str(user_id)
        viewed_at = _aware(viewed_at)
        if key not in latest or viewed_at > latest[key]:
            latest[key] = viewed_at
    latest.pop(str(post.entity_id), None)
    return latest


def _repliers_of(post):
    """
    {entity_id: latest reply time} for everyone who replied to this
    moment/thought in a chat.
    """
    try:
        rows = Message._get_collection().aggregate(
            [
                {
                    "$match": {
                        "replyingTo.type": post.on_feed,
                        "replyingTo.id": str(post.post_id),
                        "isDeleted": {"$ne": True},
                    }
                },
                {"$group": {"_id": "$sender", "at": {"$max": "$messageDate"}}},
            ]
        )
        return {str(row["_id"]): _aware(row.get("at")) for row in rows}
    except Exception:
        # Enrichment, not the list itself: a Mongo hiccup shows no "replied"
        # marks rather than failing the viewers sheet.
        logger.exception("reply lookup failed for %s", post.post_id)
        return {}


class MomentTrayView(APIView):
    """
    GET moments/tray/ - the Moments board: one entry per author with live
    moments, and each author's newest moment as the tile's preview.

    Your own entry first (if you have live moments), then everyone else with
    unseen moments before fully-seen ones, newest first within each.
    `new_count` is how many authors have something you have not seen - the
    board's "N new" badge.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        viewer = request.entity
        try:
            moments = list(
                _live_visible(viewer, PostKind.MOMENT)
                .filter(entity_id__in=_circle_ids(viewer))
                .prefetch_related("references")
                .distinct()
                .order_by("date_posted")
            )

            by_author = {}
            for moment in moments:
                by_author.setdefault(str(moment.entity_id), []).append(moment)

            seen = _seen_post_ids(viewer, [m.post_id for m in moments])
            entities = _entities_by_id(by_author.keys())

            shared_previews = _shared_previews(
                viewer, [authored[-1] for authored in by_author.values()]
            )

            tray = []
            for entity_id, authored in by_author.items():
                entity = entities.get(entity_id)
                if entity is None:
                    continue
                is_self = entity_id == str(viewer.id)
                # Your own moments are never "unseen" to you: the worker does
                # not log an author viewing their own post.
                unseen = [] if is_self else [m for m in authored if m.post_id not in seen]
                newest = authored[-1]
                tray.append(
                    {
                        "entity": EntitySerializer(entity).data,
                        "is_self": is_self,
                        "moment_count": len(authored),
                        "unseen_count": len(unseen),
                        "has_unseen": bool(unseen),
                        # Where the viewer should open this author: their first
                        # unseen moment, or the first one when all are seen.
                        "start_post_id": (unseen[0] if unseen else authored[0]).post_id,
                        "latest_at": newest.date_posted,
                        "latest": _moment_preview(
                            newest, shared_previews.get(newest.post_id)
                        ),
                    }
                )

            tray.sort(
                key=lambda item: (
                    not item["is_self"],
                    not item["has_unseen"],
                    -item["latest_at"].timestamp(),
                )
            )
            tray = tray[:MAX_TRAY_ENTRIES]

            return Response(
                {
                    "results": tray,
                    "new_count": sum(1 for item in tray if item["has_unseen"]),
                    "total": len(tray),
                }
            )
        except Exception as e:
            logger.exception("MomentTrayView.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MomentStatusView(APIView):
    """
    GET moments/status/?entity_ids=a,b,c - for drawing avatar rings anywhere
    (post headers, a profile): which of these entities have a live moment the
    viewer may see, and whether any of it is unseen. One call per page of
    avatars, never one per avatar.

    Returns {"results": {entity_id: {"has_moment", "has_unseen",
    "start_post_id"}}}; an entity with nothing live is absent.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        viewer = request.entity
        try:
            entity_ids = _parse_entity_ids(request)
            if not entity_ids:
                return Response({"results": {}})

            moments = list(
                _live_visible(viewer, PostKind.MOMENT)
                .filter(entity_id__in=entity_ids)
                .distinct()
                .order_by("date_posted")
                .values_list("post_id", "entity_id")
            )
            seen = _seen_post_ids(viewer, [post_id for post_id, _ in moments])

            results = {}
            for post_id, entity_id in moments:
                key = str(entity_id)
                entry = results.setdefault(
                    key,
                    {"has_moment": True, "has_unseen": False, "start_post_id": post_id},
                )
                is_self = key == str(viewer.id)
                if not is_self and post_id not in seen and not entry["has_unseen"]:
                    entry["has_unseen"] = True
                    entry["start_post_id"] = post_id

            return Response({"results": results})
        except Exception as e:
            logger.exception("MomentStatusView.get failed")
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

            shared_previews = _shared_previews(viewer, posts)
            results = []
            for post, data in zip(posts, PostSerializer(posts, many=True).data):
                results.append(
                    {
                        **data,
                        "seen": is_self or post.post_id in seen,
                        "shared_preview": shared_previews.get(post.post_id),
                    }
                )

            return Response({"results": results})
        except Exception as e:
            logger.exception("EntityMomentsView.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class _EphemeralSeenView(APIView):
    """
    POST <kind>s/<post_id>/seen/ - record that the viewer saw a moment or a
    thought.

    Written straight away, unlike feed views, which ride along on the next
    feed request: a moment's ring has to turn grey as soon as it is watched.
    It goes through the SAME worker handler (save_viewcache_engagements), so a
    view is logged exactly like a post view and nothing downstream needs to
    know the difference.

    Idempotent per viewer: an already-logged view is not written again, so
    rewatching does not stack rows the viewer list would have to collapse.
    """

    permission_classes = [IsAuthenticated]
    kind = None

    def post(self, request, post_id):
        viewer = request.entity
        try:
            post = get_object_or_404(
                Post, post_id=post_id, on_feed=self.kind, deleted_at=None
            )

            if not is_live(post) or not can_view_post(post, viewer):
                return Response(
                    {"message": f"{self.kind.capitalize()} not available"},
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
            logger.exception("%s seen failed", self.kind)
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MomentSeenView(_EphemeralSeenView):
    kind = PostKind.MOMENT


class ThoughtSeenView(_EphemeralSeenView):
    kind = PostKind.THOUGHT


class _EphemeralViewersView(APIView):
    """
    GET <kind>s/<post_id>/viewers/?filter=all|reacted|replied - who saw a
    moment or thought, what each reacted and whether they replied. Author
    only, and expired ones included (the archive shows them).

    Entity-based: a viewer can be a user or a page, resolved through
    entity_entity like everything else. One row per viewer, at their most
    recent view; someone who reacted or replied is listed even if their view
    was never logged. `totals` counts all three for the sheet's header and tabs.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = MomentPagination
    kind = None

    def get(self, request, post_id):
        viewer = request.entity
        try:
            post = get_object_or_404(
                Post, post_id=post_id, on_feed=self.kind, deleted_at=None
            )

            # 404, not 403, for anyone but the author: the viewer list of
            # somebody else's moment is not something to confirm exists.
            if str(post.entity_id) != str(viewer.id):
                return Response(
                    {"message": f"{self.kind.capitalize()} not available"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            viewed = _viewers_of(post)
            reactions = {
                str(entity_id): (emoji_id, glyph, created_at)
                for entity_id, emoji_id, glyph, created_at in Reaction.objects.filter(
                    post=post
                ).values_list("entity_id", "emoji_id", "emoji__emoji_content", "created_at")
            }
            repliers = _repliers_of(post)

            everyone = set(viewed) | set(reactions) | set(repliers)
            everyone.discard(str(viewer.id))
            blocked = {str(bid) for bid in get_blocked_account_ids(viewer)}
            entities = {
                eid: entity
                for eid, entity in _entities_by_id(everyone).items()
                if eid not in blocked
            }

            rows = []
            for entity_id, entity in entities.items():
                reaction = reactions.get(entity_id)
                reacted_at = _aware(reaction[2]) if reaction else None
                replied_at = repliers.get(entity_id)
                viewed_at = viewed.get(entity_id)
                # What the row's "time ago" shows: the viewer's latest
                # activity - a reply a minute ago beats a view hours ago.
                last_activity_at = max(
                    (t for t in (viewed_at, reacted_at, replied_at) if t),
                    default=None,
                )
                rows.append(
                    {
                        "entity": entity,
                        "viewed_at": viewed_at or reacted_at or replied_at,
                        "last_activity_at": last_activity_at,
                        "reaction": {"emoji_id": reaction[0], "emoji": reaction[1]}
                        if reaction
                        else None,
                        "replied": entity_id in repliers,
                    }
                )

            totals = {
                "views": len(rows),
                "reactions": sum(1 for row in rows if row["reaction"]),
                "replies": sum(1 for row in rows if row["replied"]),
            }

            wanted = request.query_params.get("filter", "all")
            if wanted == "reacted":
                rows = [row for row in rows if row["reaction"]]
            elif wanted == "replied":
                rows = [row for row in rows if row["replied"]]

            rows.sort(key=lambda row: row["last_activity_at"] or post.date_posted, reverse=True)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(rows, request, view=self)
            response = paginator.get_paginated_response(
                [
                    {
                        "entity": EntitySerializer(row["entity"]).data,
                        "viewed_at": row["viewed_at"],
                        "last_activity_at": row["last_activity_at"],
                        "reaction": row["reaction"],
                        "replied": row["replied"],
                    }
                    for row in page
                ]
            )
            response.data["totals"] = totals
            return response
        except Exception as e:
            logger.exception("%s viewers failed", self.kind)
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MomentViewersView(_EphemeralViewersView):
    kind = PostKind.MOMENT


class ThoughtViewersView(_EphemeralViewersView):
    kind = PostKind.THOUGHT


def _own_ephemeral(request, post_id, kind):
    """The acting entity's own, undeleted moment/thought, or None."""
    post = Post.objects.filter(post_id=post_id, on_feed=kind, deleted_at=None).first()
    if post is None or str(post.entity_id) != str(request.entity.id):
        return None
    return post


class MomentDetailView(APIView):
    """
    PUT moments/<post_id>/ - the author changes a moment's audience
    (`privacy_status`: public | connections) or its "allow replies &
    reactions" (`allow_replies`), or archives it now (`archive: true`): its
    timer is ended, so it leaves the board and rings and lands in Archives -
    the same place it would have gone at 24h. The timer never moves otherwise,
    and never forward.
    """

    permission_classes = [IsAuthenticated]

    def put(self, request, post_id):
        try:
            post = _own_ephemeral(request, post_id, PostKind.MOMENT)
            if post is None:
                return Response(
                    {"message": "Moment not available"}, status=status.HTTP_404_NOT_FOUND
                )

            fields = []
            if "privacy_status" in request.data:
                audience = request.data.get("privacy_status")
                if audience not in EPHEMERAL_AUDIENCES:
                    return Response(
                        {"message": "Audience must be public or connections"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                post.privacy_status = audience
                fields.append("privacy_status")

            if "allow_replies" in request.data:
                post.details = {
                    **(post.details or {}),
                    "allow_replies": bool(request.data.get("allow_replies")),
                }
                fields.append("details")

            if request.data.get("archive") is True:
                current = now()
                if post.expires_at is None or post.expires_at > current:
                    post.expires_at = current
                    fields.append("expires_at")

            if fields:
                post.save(update_fields=fields)

            return Response(
                {
                    "status": True,
                    "privacy_status": post.privacy_status,
                    "details": post.details or {},
                    "expires_at": post.expires_at,
                }
            )
        except Exception as e:
            logger.exception("MomentDetailView.put failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ThoughtDetailView(APIView):
    """
    GET thoughts/<post_id>/ - the author's own thought, with how many have
    seen it (the edit screen's "seen by N").

    PUT thoughts/<post_id>/ - edit it IN PLACE: text, mood, audience. The
    timer and the views stay - an edit is the same thought, not a new one.
    Deleting is the ordinary post delete.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, post_id):
        try:
            post = _own_ephemeral(request, post_id, PostKind.THOUGHT)
            if post is None:
                return Response(
                    {"message": "Thought not available"}, status=status.HTTP_404_NOT_FOUND
                )
            return Response(
                {**_thought_payload(post), "views": len(_viewers_of(post))}
            )
        except Exception as e:
            logger.exception("ThoughtDetailView.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def put(self, request, post_id):
        try:
            post = _own_ephemeral(request, post_id, PostKind.THOUGHT)
            if post is None or not is_live(post):
                return Response(
                    {"message": "Thought not available"}, status=status.HTTP_404_NOT_FOUND
                )

            fields = []
            if "text" in request.data:
                text = str(request.data.get("text") or "").strip()
                if not text:
                    return Response(
                        {"message": "A thought cannot be empty"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                # len() of a str counts code points - the composer's count.
                if len(text) > THOUGHT_MAX_LENGTH:
                    return Response(
                        {"message": f"A thought can be at most {THOUGHT_MAX_LENGTH} characters"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                post.caption = text
                fields.append("caption")

            if "mood" in request.data:
                mood = request.data.get("mood")
                if mood is not None and mood not in THOUGHT_MOODS:
                    return Response(
                        {"message": "Unknown mood"}, status=status.HTTP_400_BAD_REQUEST
                    )
                details = {**(post.details or {})}
                if mood is None:
                    details.pop("mood", None)
                else:
                    details["mood"] = mood
                post.details = details
                fields.append("details")

            if "privacy_status" in request.data:
                audience = request.data.get("privacy_status")
                if audience not in EPHEMERAL_AUDIENCES:
                    return Response(
                        {"message": "Audience must be public or connections"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                post.privacy_status = audience
                fields.append("privacy_status")

            if fields:
                post.save(update_fields=fields)

            return Response({"status": True, **_thought_payload(post)})
        except Exception as e:
            logger.exception("ThoughtDetailView.put failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ThoughtsView(APIView):
    """
    GET thoughts/?entity_ids=a,b,c - the live thought of each of those
    entities that has one, for rendering over their avatars. One query for the
    whole list, so a page of avatars costs one request.

    Returns {"results": {entity_id: thought}}; an entity with no live (or no
    visible) thought is simply absent.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        viewer = request.entity
        try:
            entity_ids = _parse_entity_ids(request)
            if not entity_ids:
                return Response({"results": {}})

            latest = _latest_live_thoughts(viewer, entity_ids)
            mine = _my_reactions(viewer, latest.values())
            return Response(
                {
                    "results": {
                        entity_id: _thought_payload(
                            thought, my_reaction=mine.get(thought.post_id)
                        )
                        for entity_id, thought in latest.items()
                    }
                }
            )
        except Exception as e:
            logger.exception("ThoughtsView.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ThoughtsRailView(APIView):
    """
    GET thoughts/rail/ - the Thoughts rail at the top of Messages: your own
    live thought (or `mine: null`, which the rail shows as "add one"), then the
    live thoughts of the people you follow or are connected to, newest first.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        viewer = request.entity
        try:
            latest = _latest_live_thoughts(viewer, _circle_ids(viewer))
            mine = latest.pop(str(viewer.id), None)

            others = sorted(latest.values(), key=lambda t: t.date_posted, reverse=True)[
                :MAX_RAIL_THOUGHTS
            ]
            authors = _entities_by_id({str(t.entity_id) for t in others} | {str(viewer.id)})
            reacted = _my_reactions(viewer, others)

            return Response(
                {
                    "mine": _thought_payload(mine, authors.get(str(viewer.id)))
                    if mine
                    else None,
                    "results": [
                        _thought_payload(
                            thought,
                            authors.get(str(thought.entity_id)),
                            my_reaction=reacted.get(thought.post_id),
                        )
                        for thought in others
                        if str(thought.entity_id) in authors
                    ],
                    # Everyone else you are connected to, after the thoughts -
                    # so the rail is your people, not only whoever posted.
                    "suggestions": _ranked_connections(
                        viewer, exclude=[t.entity_id for t in others]
                    ),
                }
            )
        except Exception as e:
            logger.exception("ThoughtsRailView.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MomentArchiveView(APIView):
    """
    GET archive/moments/ - the acting entity's own EXPIRED moments, newest
    first. Live ones are on the board and the profile ring, not here. The archive's "Moments" tab; its "Feed" tab is the existing
    profile endpoint with archive=true.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = MomentPagination

    def get(self, request):
        viewer = request.entity
        try:
            queryset = (
                _annotated_posts(viewer)
                .filter(
                    entity=viewer,
                    on_feed=PostKind.MOMENT,
                    deleted_at=None,
                    expires_at__lte=now(),
                )
                .order_by("-date_posted")
            )

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(queryset, request, view=self)
            shared_previews = _shared_previews(viewer, page)
            return paginator.get_paginated_response(
                [
                    {**data, "shared_preview": shared_previews.get(post.post_id)}
                    for post, data in zip(page, PostSerializer(page, many=True).data)
                ]
            )
        except Exception as e:
            logger.exception("MomentArchiveView.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
