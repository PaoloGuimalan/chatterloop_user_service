"""
Content (post) search for the redesigned Search page.

Lives in its own module because TWO views consume it: the paginated
/api/newsfeed/search/v2/posts/ endpoint (the Content "See all" infinite
scroll) and entity/search_views.py's overview endpoint (the single
page-init call), which deferred-imports it to avoid the entity<->newsfeed
import cycle (same trick as entity/services/follows.py).

Ranking: PostScore.ranking_score DESC (indexed), then recency. Posts with
no PostScore row rank as 0.0 rather than being dropped or erroring - the
score row is written lazily on engagement, so brand-new posts don't have
one yet.
"""

from django.db.models import FloatField, Value
from django.db.models.functions import Coalesce

from entity.utils import entity_side_is_visible
from newsfeed.models import Post
from newsfeed.services.post_visibility import visible_posts_filter


def build_post_search_queryset(entity, query, blocked_ids):
    """
    Public, live posts whose caption matches `query`, ranked so relevant
    items land on top.

    Visibility mirrors what the feed itself enforces: the searcher's own
    post audience (visible_posts_filter - public, plus their own posts and
    their connections' connections-only ones), not archived/deleted, author
    still a usable entity (active + verified user OR active realm -
    entity_side_is_visible), and nobody in a block relationship with the
    searcher in either direction.

    Search deliberately shares the ONE audience definition rather than
    hard-coding privacy_status="public": doing the latter meant a private
    profile's posts were still searchable the moment they were public-by-
    default, and it would have silently hidden connections-only posts from
    the very people allowed to read them.

    NOTE: from_system is deliberately NOT filtered - despite the name, the
    Node createpost route stamps from_system=TRUE on every ordinary post
    ("posted from the system app"), so excluding it would empty the results.

    select_related pulls the author's account/realm and the score row in
    the same query - all three are 1:1 off Post/Entity, so this stays a
    plain LEFT JOIN with no fan-out (same trick as UserContacts.get).
    """
    return (
        Post.objects.filter(
            entity_side_is_visible("entity"),
            visible_posts_filter(entity),
            caption__icontains=query,
            is_archived=False,
            deleted_at__isnull=True,
        )
        .exclude(entity_id__in=blocked_ids)
        .distinct()
        .select_related("entity", "entity__users", "entity__realms", "score")
        .annotate(
            rank=Coalesce("score__ranking_score", Value(0.0), output_field=FloatField())
        )
        .order_by("-rank", "-date_posted")
    )


def serialize_post_hit(post):
    """
    Lightweight card shape for a search hit - deliberately NOT the full
    PostSerializer: the Content cards only render author/time/caption and
    the two counters, and the client opens the real post through the
    existing /api/newsfeed/preview/<post_id>/ when tapped.
    """
    entity = post.entity
    account = getattr(entity, "users", None)
    realm = getattr(entity, "realms", None)

    if account is not None:
        first = account.first_name or ""
        middle = account.middle_name or ""
        middle = "" if middle == "N/A" else middle
        last = account.last_name or ""
        display_name = " ".join(p for p in [first, middle, last] if p).strip()
        author = {
            "entity_id": str(entity.id),
            "type": "user",
            "display_name": display_name or account.username,
            "handle": account.username,
            "profile": _clean_profile(account.profile),
            "is_verified": bool(account.is_badged),
        }
    elif realm is not None:
        author = {
            "entity_id": str(entity.id),
            "type": "realm",
            "display_name": realm.name or realm.slug or "",
            "handle": realm.slug or realm.realm_id,
            "profile": _clean_profile(realm.profile),
            "is_verified": bool(realm.is_verified),
        }
    else:
        author = {
            "entity_id": str(entity.id),
            "type": entity.type,
            "display_name": str(entity.id),
            "handle": "",
            "profile": None,
            "is_verified": False,
        }

    score = getattr(post, "score", None)

    return {
        "post_id": post.post_id,
        "caption": post.caption or "",
        "content_type": post.content_type,
        "file_type": post.file_type,
        "date_posted": post.date_posted.isoformat() if post.date_posted else None,
        "likes_count": score.likes_count if score else 0,
        "comments_count": score.comments_count if score else 0,
        "author": author,
    }


def _clean_profile(profile):
    # Same normalization rule as EntitySearch: both "no photo" sentinels
    # (user: "none", realm: "N/A") become null so the client has one rule.
    return None if profile in (None, "", "none", "N/A") else profile
