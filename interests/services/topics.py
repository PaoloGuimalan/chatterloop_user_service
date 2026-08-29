"""
Building a topic ROW - the shape every surface that lists interests renders.

Three surfaces now list topics rather than one: the Popular Topics widget
(PopularTopicsView), the searchable/paginated topic directory behind Explore's
"Popular Topics · See all" and its Topics filter (TopicListView), and the Topics
section of the search overview (entity.search_views.SearchOverviewV2). They all
draw the same row - name, hashtag slug, category, participant faces - and they
all have to answer the same privacy question the same way, so the answer lives
here once instead of three times.

WHY THE VISIBILITY RULE IS NOT OPTIONAL
---------------------------------------
A row carries a post COUNT and PARTICIPANT FACES, and both are derived from
posts. Counting every post that carries an interest would let a discovery
surface report on private ones: a topic's count rising, or somebody's face
appearing under it, tells you they posted about it even when their post is
connections-only or an explicit allow-list you are not on.

So counts and faces are computed against visible_posts_filter(viewer) - the
same predicate the feed itself uses - and never against the raw link table.
Two viewers can legitimately see different counts for the same topic, because
they can see different posts.

The trending SCORE stays global. It is an aggregate over the whole platform
with no per-post attribution, which is what makes it safe to rank on, and
per-viewer ranking would be a different (and much more expensive) feature.
"""

from django.db.models import (
    Case,
    Count,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from interests.models import Interest, PostInterestLink, normalize_key
from entity.utils import get_entity_name, get_entity_profile_picture
from newsfeed.models import Post
from newsfeed.services.post_visibility import visible_posts_filter

# Rows pulled to build the face stacks. Faces need the RECENT posters, and
# taking three per topic in SQL means a window function and a subquery wrapper
# for what is at most a page of small groups. One bounded, ordered read grouped
# in Python is cheaper to run and far cheaper to read. The cap exists so a
# single very active topic cannot drag the whole query.
FACE_SCAN_LIMIT = 500
FACES_PER_TOPIC = 3


def visible_posts(entity):
    """The posts `entity` may read - the feed's own rule, nothing narrower."""
    return Post.objects.filter(
        visible_posts_filter(entity), deleted_at=None, is_archived=False
    )


def post_counts(interest_ids, visible):
    """interest_id -> number of VISIBLE posts filed under it.

    Absent from the mapping means zero, which is also the signal callers use to
    drop a topic: a row that opens onto an empty list is worse than no row.
    """
    if not interest_ids:
        return {}

    return {
        row["interest_id"]: row["total"]
        for row in PostInterestLink.objects.filter(
            interest_id__in=interest_ids, post__in=visible
        )
        .values("interest_id")
        .annotate(total=Count("post_id", distinct=True))
    }


def faces_for(interest_ids, visible, per_topic=FACES_PER_TOPIC):
    """interest_id -> up to `per_topic` recent participants.

    Deduplicated by entity, so a topic three of whose recent posts are by the
    same person shows three different people where three exist rather than the
    same avatar repeated.
    """
    if not interest_ids:
        return {}

    rows = (
        PostInterestLink.objects.filter(interest_id__in=interest_ids, post__in=visible)
        .select_related("post__entity__users", "post__entity__realms")
        .order_by("-post__date_posted")[:FACE_SCAN_LIMIT]
    )

    faces = {}
    seen = {}

    for link in rows:
        bucket = faces.setdefault(link.interest_id, [])
        if len(bucket) >= per_topic:
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
                # client shows while the image loads, and the one it falls back
                # to permanently if the image 404s.
                "initials": initials(name),
            }
        )

    return faces


def initials(name):
    """Up to two initials for an avatar bubble."""
    parts = [part for part in (name or "").split(" ") if part]
    return "".join(part[0] for part in parts[:2]).upper() or "?"


def serialize_topic(interest, score, posts, faces):
    """One row, in the shape both clients parse.

    `interest` must arrive with its parent selected - every caller here builds
    its queryset with select_related("parent") - or this issues a query per row.
    """
    return {
        "id": interest.id,
        "name": interest.name,
        # The hashtag form, so a client can render "#northedsa" and a tap can
        # round-trip to the same interest. This is the normalized key, which is
        # exactly what a hashtag normalises to - see interests.services.hashtags.
        "slug": interest.normalized_name,
        # The taxonomy parent is the category. An interest with no parent is not
        # an error - discovery creates orphans that seed_taxonomy has not
        # adopted yet - so it falls back rather than being hidden.
        "category": interest.parent.name if interest.parent_id else "General",
        "score": round(score or 0.0, 3),
        "posts": posts,
        "faces": faces,
    }


def serialize_topics(interests, scores, viewer, drop_empty=True, limit=None):
    """`interests` -> rows, in the order given, with counts and faces attached.

    `scores` maps interest_id -> trending score; a missing id scores 0, which is
    correct for an interest that has never been ranked (nothing has decayed onto
    it yet) and lets callers pass a queryset that never joined the score table.

    `drop_empty` removes topics with no visible post FOR THIS VIEWER. Order is
    never rearranged here, so dropping demotes nothing - it only skips rows that
    would open onto an empty list.

    `limit` is applied AFTER that filter and BEFORE faces are fetched, which is
    the whole reason it is a parameter rather than the caller's slice: the
    Popular Topics widget over-fetches candidates six-to-one to survive the
    filter, and slicing afterwards would have built face stacks for forty-odd
    topics to show eight.
    """
    interests = list(interests)
    if not interests:
        return []

    ids = [interest.id for interest in interests]
    visible = visible_posts(viewer)
    counts = post_counts(ids, visible)

    if drop_empty:
        interests = [interest for interest in interests if counts.get(interest.id)]

    if limit is not None:
        interests = interests[:limit]

    if not interests:
        return []
    ids = [interest.id for interest in interests]

    faces = faces_for(ids, visible)

    return [
        serialize_topic(
            interest,
            scores.get(interest.id, 0.0),
            counts.get(interest.id, 0),
            faces.get(interest.id, []),
        )
        for interest in interests
    ]


def build_topic_queryset(viewer, query=None):
    """The topic DIRECTORY, ranked - Explore's "See all" list and its Tags filter.

    Two orderings, one queryset:

      no query - the popularity ranking, unbounded. Same order as the Popular
                 Topics widget, without its eight-row cap, so "See all" opens
                 onto more of the list the widget was showing the top of.

      a query  - relevance first, popularity second. A hit whose key IS the
                 query outranks one that merely starts with it, which outranks
                 one that merely contains it; inside a tier the busier topic
                 wins. Typing "sunset" therefore lands #sunset above
                 #sunsetseries above #goldensunset, rather than in score order
                 across all three.

    EMPTY TOPICS ARE EXCLUDED IN SQL, not after paging. The visible-post test is
    the same one counts and faces use, and a topic nobody may read has no row -
    but doing that in Python would shorten pages unpredictably and make DRF's
    `count` a lie, which is exactly what an infinite scroll cannot survive.
    """
    queryset = Interest.objects.select_related("parent", "trending_score").annotate(
        has_visible_post=Exists(
            PostInterestLink.objects.filter(
                interest=OuterRef("pk"), post__in=visible_posts(viewer)
            )
        ),
        trending=Coalesce(F("trending_score__score"), Value(0.0)),
    )

    term = (query or "").strip()
    if not term:
        return queryset.filter(has_visible_post=True).order_by(
            "-trending", "normalized_name"
        )

    # Matched on BOTH forms: the key ("northedsa") catches somebody typing the
    # hashtag they saw, the readable name ("north edsa") catches somebody typing
    # the words. A leading "#" is how the hashtag is written, never part of what
    # is stored, so it is stripped before either comparison.
    key = normalize_key(term.lstrip("#"))
    return (
        queryset.filter(
            Q(normalized_name__icontains=key) | Q(name__icontains=term.lstrip("#")),
            has_visible_post=True,
        )
        .annotate(
            match_rank=Case(
                When(normalized_name=key, then=Value(0)),
                When(normalized_name__startswith=key, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        .order_by("match_rank", "-trending", "normalized_name")
    )
