"""Eager loading shared by Home, profile/community and direct post reads."""

from django.db.models import Prefetch

from newsfeed.models import PostTag


def load_post_cards(queryset):
    """Load exactly the relations consumed by PostSerializer in bounded queries.

    This only changes hydration. The caller still applies its own audience,
    ordering, pagination and viewer annotations before evaluating the queryset.
    """
    return queryset.select_related(
        "entity__users", "entity__realms", "entity__bots", "score", "map_info"
    ).prefetch_related(
        Prefetch(
            "tagging",
            queryset=PostTag.objects.select_related(
                "entity__users", "entity__realms", "entity__bots"
            ),
        ),
        "privacy_users",
        "references",
        "preview",
        "interests",
    )
