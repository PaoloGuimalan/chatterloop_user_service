"""
Which post KINDS (newsfeed_post.on_feed) each read path returns, and whether a
post is still live.

Moments and thoughts share newsfeed_post with feed posts, so every reader of
that table has to say which kinds it serves - otherwise a moment would turn up
on a profile grid or in search the day the first one is created. These lists
are that decision, in one place: wiring moments into the feed is appending
PostKind.MOMENT to FEED_KINDS, nothing else.

Only LIST reads need a kind list. Reads of one post by id (comments,
reactions, bot threads, moderation, reporting, deletion, export) work on every
kind on purpose - a moment has comments and reactions like any post. Node and
the worker hold the kind VALUES (PostKind), not these lists: nothing outside
this service lists posts.
"""

from django.db.models import Q
from django.utils.timezone import now

from newsfeed.models import PostKind


# The main feed - both the fanned-out (friends) and the trending candidates.
FEED_KINDS = [PostKind.FEED]
TRENDING_KINDS = [PostKind.FEED]

# A profile's post grid, and the author's archive "Feed" tab.
PROFILE_KINDS = [PostKind.FEED]

# The single-post page. The author can additionally open their OWN posts of any
# kind, expired included - that is how the archive opens an expired moment.
PREVIEW_KINDS = [PostKind.FEED]

# Post search, and the interest/topic pages.
SEARCH_KINDS = [PostKind.FEED]
TOPIC_KINDS = [PostKind.FEED]

# The saved-posts list.
SAVED_KINDS = [PostKind.FEED]


def live_filter(prefix=""):
    """
    Not expired. Feed posts have no expires_at and are always live; moments and
    thoughts stop being live 24h after posting. Deletion is NOT part of this -
    every caller already filters deleted_at itself, some deliberately not.

    `prefix` is for filtering through a relation, e.g. live_filter("post__").
    """
    return Q(**{f"{prefix}expires_at__isnull": True}) | Q(
        **{f"{prefix}expires_at__gt": now()}
    )


def live_kinds_filter(kinds, prefix=""):
    """Of one of `kinds`, and live."""
    return Q(**{f"{prefix}on_feed__in": list(kinds)}) & live_filter(prefix)


def is_live(post):
    return post.expires_at is None or post.expires_at > now()
