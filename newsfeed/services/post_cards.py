"""Eager loading shared by Home, profile/community and direct post reads."""

from django.db.models import BooleanField, CharField, Prefetch, Value

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


def read_only_post_cards(queryset):
    """Use the ordinary card shape without loading interactive viewer state."""
    return load_post_cards(queryset).annotate(
        entity_reaction=Value(None, output_field=CharField()),
        entity_reaction_is_anonymous=Value(None, output_field=BooleanField()),
        is_saved=Value(False, output_field=BooleanField()))


def unavailable_post_card(payload):
    """The ordinary removed/archived placeholder, without retained media data."""
    return {**payload, "caption": "", "references": [], "link_preview": None,
            "map_info": None, "details": None, "deleted_by": None,
            "shared_preview": None}
