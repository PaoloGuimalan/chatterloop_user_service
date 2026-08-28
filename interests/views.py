from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination

from django.db.models import Count, Exists, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from .models import (
    EntityInterest,
    EntityInterestAffinity,
    Interest,
    InterestTrendingScore,
    PostInterestLink,
    normalize_key,
)
from entity.permissions import PermissionEffect
from entity.utils import get_entity_name, get_entity_profile_picture
from newsfeed.models import Post, PostSave, Reaction
from newsfeed.serializers import PostSerializer
from newsfeed.services.post_visibility import visible_posts_filter
import logging

logger = logging.getLogger(__name__)


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class InterestListView(APIView):
    """
    Search/autocomplete over the shared interest vocabulary - generalized
    version of diary.views.TagListView (which now delegates here so
    /api/diary/tags/ keeps working unchanged).
    """

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        try:
            search = request.query_params.get("search", None)
            queryset = Interest.objects.all().order_by("id")

            # The key the manager would store for this input. Everything below
            # compares against it rather than re-deriving normalisation here -
            # a picker that decides "is_new" differently from the code that
            # creates the row is a picker that lies.
            key = normalize_key(search) if search else ""

            if search:
                # Both forms, because the user may type either. "news and
                # culture" matches the display name; "newsandculture" matches
                # the key. Matching only the display name meant a user who
                # typed it without spaces saw no results and was offered a
                # "create new" for an interest that already exists.
                queryset = queryset.filter(
                    Q(name__icontains=search) | Q(normalized_name__contains=key)
                )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(queryset, request, view=self)

            results = [{"id": i.id, "name": i.name} for i in paginated_queryset]
            # Keyed on normalize_key, so spacing cannot make an existing
            # interest look new. Typing "news and culture" used to report
            # is_new=True against the key "newsandculture" - the row was found
            # correctly on save, but the picker had already told the user they
            # were creating something.
            is_new = bool(key) and not Interest.objects.filter(
                normalized_name=key
            ).exists()

            return paginator.get_paginated_response({"list": results, "is_new": is_new})
        except Exception as e:
            logger.exception("InterestListView.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class EntityInterestOverrideListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            entity = request.entity
            overrides = EntityInterest.objects.filter(entity=entity).select_related("interest")
            data = [
                {
                    "id": o.id,
                    "interest": {"id": o.interest.id, "name": o.interest.name},
                    "effect": o.effect,
                    "reason": o.reason,
                    "created_at": o.created_at,
                    "expires_at": o.expires_at,
                }
                for o in overrides
            ]
            return Response({"status": True, "data": data}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("EntityInterestOverrideListView.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class EntityInterestOverrideView(APIView):
    """
    Create/update a grant or deny for the requesting entity, or delete one -
    ownership-scoped to request.entity throughout (never lets one entity
    touch another's overrides).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            entity = request.entity
            interest_id = request.data.get("interest_id")
            interest_name = request.data.get("interest_name")
            effect = request.data.get("effect")
            reason = request.data.get("reason")
            expires_at = request.data.get("expires_at")

            if effect not in (PermissionEffect.GRANT, PermissionEffect.DENY):
                return Response(
                    {"status": False, "message": "effect must be 'grant' or 'deny'"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if interest_id:
                interest = get_object_or_404(Interest, id=interest_id)
            elif interest_name:
                interest, _ = Interest.objects.get_or_create_by_name(interest_name)
            else:
                return Response(
                    {"status": False, "message": "interest_id or interest_name is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            override, _ = EntityInterest.objects.update_or_create(
                entity=entity,
                interest=interest,
                defaults={
                    "effect": effect,
                    "reason": reason,
                    "expires_at": expires_at,
                    "created_by": entity,
                },
            )

            return Response(
                {
                    "status": True,
                    "message": "Interest preference saved",
                    "data": {"id": override.id, "interest": interest.name, "effect": override.effect},
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("EntityInterestOverrideView.post failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def delete(self, request, override_id):
        try:
            override = get_object_or_404(EntityInterest, id=override_id)

            try:
                if override.entity_id != request.entity.id:
                    raise PermissionDenied("You do not own this resource.")
            except PermissionDenied:
                raise

            override.delete()
            return Response(
                {"status": True, "message": "Interest preference removed"},
                status=status.HTTP_200_OK,
            )
        except PermissionDenied:
            raise
        except Exception as e:
            logger.exception("EntityInterestOverrideView.delete failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class MyTopInterestsView(APIView):
    """
    Per-entity interest ranking - "which interests has THIS entity mostly
    interacted with", generalizing diary's existing per-user top_tags
    (DiaryTotalView) from a raw entry count to real engagement weight.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 10))
            affinities = (
                EntityInterestAffinity.objects.filter(entity=request.entity)
                .select_related("interest")
                .order_by("-score")[:limit]
            )
            data = [
                {"interest": {"id": a.interest.id, "name": a.interest.name}, "score": a.score}
                for a in affinities
            ]
            return Response({"status": True, "data": data}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("MyTopInterestsView.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TrendingInterestsView(APIView):
    """
    Global ranking of interests against each other - "what's trending
    platform-wide right now". Unscoped/public, unlike MyTopInterestsView.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 10))
            trending = (
                InterestTrendingScore.objects.select_related("interest").order_by("-score")[:limit]
            )
            data = [
                {"interest": {"id": t.interest.id, "name": t.interest.name}, "score": t.score}
                for t in trending
            ]
            return Response({"status": True, "data": data}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("TrendingInterestsView.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PopularTopicsView(APIView):
    """
    The newsfeed's "Popular Topics" sidebar: what the platform is talking
    about, with who is talking about it.

    Ranked by InterestTrendingScore, which is the decayed ranking rather than a
    lifetime total - cron_service/interest_trending applies the decay, so an
    interest that was busy six weeks ago no longer outranks one that is busy
    now. This endpoint only reads that ranking; it never writes to it.

    PRIVACY IS THE WHOLE DIFFICULTY HERE
    ------------------------------------
    The widget shows a post COUNT and PARTICIPANT FACES, and both are derived
    from posts. Counting every post that carries an interest would let a
    sidebar report on private ones: a topic's count rising, or somebody's face
    appearing under it, tells you they posted about it even when their post is
    connections-only or an explicit allow-list you are not on.

    So counts and faces are computed against visible_posts_filter(viewer) -
    the same predicate the feed itself uses - and NOT against the raw link
    table. Two viewers can therefore see different counts for the same topic,
    which is correct: they can see different posts.

    The trending SCORE is left global on purpose. It is an aggregate over the
    whole platform with no per-post attribution, which is what makes it safe to
    rank on, and per-viewer ranking would be a different (and much more
    expensive) feature.
    """

    permission_classes = [IsAuthenticated]

    # A sidebar, not a directory. The design tops out at 8 rows.
    MAX_LIMIT = 8
    # Ranked interests examined to fill those rows. Trending score is earned
    # from diary entries and comments too, not only posts, so the top of the
    # ranking contains interests with no visible post behind them - a row the
    # widget would render as a topic you cannot open. Candidates are therefore
    # over-fetched and then filtered down to those with something to show.
    CANDIDATE_MULTIPLE = 6
    # Rows pulled to build the face stacks. Faces need the RECENT posters, and
    # taking three per topic in SQL means a window function and a subquery
    # wrapper for what is at most eight small groups. One bounded, ordered read
    # grouped in Python is cheaper to run and far cheaper to read. The cap
    # exists so a single very active topic cannot drag the whole query.
    FACE_SCAN_LIMIT = 500
    FACES_PER_TOPIC = 3

    def get(self, request):
        try:
            entity = getattr(request, "entity", None)

            try:
                limit = int(request.query_params.get("limit", self.MAX_LIMIT))
            except (TypeError, ValueError):
                limit = self.MAX_LIMIT
            limit = max(1, min(limit, self.MAX_LIMIT))

            candidates = list(
                InterestTrendingScore.objects.select_related(
                    "interest", "interest__parent"
                )
                .filter(score__gt=0)
                .order_by("-score", "interest__id")[: limit * self.CANDIDATE_MULTIPLE]
            )
            if not candidates:
                return Response(
                    {"status": True, "data": []}, status=status.HTTP_200_OK
                )

            visible = Post.objects.filter(
                visible_posts_filter(entity), deleted_at=None, is_archived=False
            )

            counts = {
                row["interest_id"]: row["total"]
                for row in PostInterestLink.objects.filter(
                    interest_id__in=[row.interest_id for row in candidates],
                    post__in=visible,
                )
                .values("interest_id")
                .annotate(total=Count("post_id", distinct=True))
            }

            # Order is preserved from the ranking above, so filtering here
            # demotes nothing - it only skips what has nothing to open.
            ranked = [row for row in candidates if counts.get(row.interest_id)][:limit]
            if not ranked:
                return Response(
                    {"status": True, "data": []}, status=status.HTTP_200_OK
                )

            faces = self._faces([row.interest_id for row in ranked], visible)

            data = [
                {
                    "id": row.interest.id,
                    "name": row.interest.name,
                    # The hashtag form, so the widget can render "#northedsa"
                    # and a click can round-trip to the same interest. This is
                    # the normalized key, which is exactly what a hashtag
                    # normalises to - see interests.services.hashtags.
                    "slug": row.interest.normalized_name,
                    # The taxonomy parent is the category. An interest with no
                    # parent is not an error - discovery creates orphans that
                    # seed_taxonomy has not adopted yet - so it falls back
                    # rather than being hidden from the widget.
                    "category": (
                        row.interest.parent.name if row.interest.parent else "General"
                    ),
                    "score": round(row.score, 3),
                    "posts": counts.get(row.interest_id, 0),
                    "faces": faces.get(row.interest_id, []),
                }
                for row in ranked
            ]

            return Response({"status": True, "data": data}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("PopularTopicsView.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _faces(self, interest_ids, visible):
        """interest_id -> up to FACES_PER_TOPIC recent participants.

        Deduplicated by entity, so a topic three of whose recent posts are by
        the same person shows three different people where three exist rather
        than the same avatar repeated.
        """
        rows = (
            PostInterestLink.objects.filter(
                interest_id__in=interest_ids, post__in=visible
            )
            .select_related("post__entity__users", "post__entity__realms")
            .order_by("-post__date_posted")[: self.FACE_SCAN_LIMIT]
        )

        faces = {}
        seen = {}

        for link in rows:
            bucket = faces.setdefault(link.interest_id, [])
            if len(bucket) >= self.FACES_PER_TOPIC:
                continue

            entity = link.post.entity
            entity_seen = seen.setdefault(link.interest_id, set())
            if entity.id in entity_seen:
                continue
            entity_seen.add(entity.id)

            name = get_entity_name(entity)
            bucket.append(
                {
                    "entity_id": str(entity.id),
                    "name": name,
                    "profile": get_entity_profile_picture(entity),
                    # Still sent when a picture exists: it is the fallback the
                    # client shows while the image loads, and the one it falls
                    # back to permanently if the image 404s.
                    "initials": _initials(name),
                }
            )

        return faces


def _initials(name):
    """Up to two initials for an avatar bubble."""
    parts = [part for part in (name or "").split(" ") if part]
    return "".join(part[0] for part in parts[:2]).upper() or "?"


class TopicPostsView(APIView):
    """
    The posts inside one topic - the drill-down behind a Popular Topics row.

    Separate endpoint from PopularTopicsView on purpose. That one answers
    "what is popular", is small, and is fetched once when the feed mounts;
    this one answers "what is in it", is page-sized, and is fetched only when
    somebody opens a topic. Folding the two together would make the sidebar
    carry a page of posts for eight topics nobody has clicked yet.

    Addressed by SLUG (the interest's normalized_name), not by id, so the URL
    a topic row links to is the same string a hashtag normalises to - see
    interests.services.hashtags. "#north-edsa", "#NorthEdsa" and the interest
    "north edsa" all arrive here as "northedsa".

    Visibility is the feed's own rule, applied here for the same reason
    PopularTopicsView applies it to counts: a topic listing must not become a
    way to read posts whose audience you are not in.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request, slug):
        try:
            entity = getattr(request, "entity", None)

            # Normalised rather than trusted: a slug arriving with spacing or
            # casing ("North Edsa") should find the same interest the widget
            # would have linked to, not 404 on a cosmetic difference.
            interest = Interest.objects.filter(
                normalized_name=normalize_key(slug)
            ).first()
            if interest is None:
                return Response(
                    {"status": False, "message": "Topic not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            queryset = (
                Post.objects.select_related("entity", "score")
                .prefetch_related(
                    "tagging", "privacy_users", "references", "map_info", "preview"
                )
                .annotate(
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
                    visible_posts_filter(entity),
                    postinterestlink__interest=interest,
                    deleted_at=None,
                    is_archived=False,
                )
                # visible_posts_filter can join through PostPrivacy, and the
                # link join can match more than once, so without this a post
                # appears twice in the page it is on.
                .distinct()
                # Ranked, then dated. A topic view is a "best of" rather than a
                # timeline - which is what the trending score already means -
                # but two posts with no score yet must still order stably or
                # pagination repeats and drops rows between pages.
                .order_by("-score__ranking_score", "-date_posted", "post_id")
            )

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(queryset, request, view=self)
            serializer = PostSerializer(page, many=True)

            response = paginator.get_paginated_response(serializer.data)
            # The topic itself travels with the first page so the drill-down
            # header does not need a second request to name what it is showing.
            response.data["topic"] = {
                "id": interest.id,
                "name": interest.name,
                "slug": interest.normalized_name,
                "category": (
                    interest.parent.name if interest.parent_id else "General"
                ),
            }
            return response
        except Exception as e:
            logger.exception("TopicPostsView.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
