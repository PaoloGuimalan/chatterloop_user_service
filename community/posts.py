"""Community destinations, current audience and safe contribution projections."""

from django.db.models import Count, Exists, OuterRef, Q
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from community.models import Member, Realm
from community.post_policy import can_contribute, can_read, validate_anonymity


def community_for_post(post):
    # Prefetch-aware so card serialization does not add a destination lookup
    # per post when the caller loaded tagging__entity__realms.
    if hasattr(post, "_community_destination"):
        return post._community_destination
    realms = [getattr(tag.entity, "realms", None) for tag in post.tagging.all()]
    communities = list(
        {
            realm.pk: realm for realm in realms if realm and realm.type == "community"
        }.values()
    )
    if len(communities) > 1:
        raise NotFound("Post not available")
    post._community_destination = communities[0] if communities else None
    return post._community_destination


def is_member(realm, entity):
    return (
        entity is not None
        and Member.objects.filter(realm=realm, entity=entity).exists()
    )


def community_audience(viewer):
    """(has community destination, readable community destination), for Post Qs.

    Correlated EXISTS avoids join-negation errors: an ordinary tag on a
    private community post must never make it count as a noncommunity post.
    """
    from newsfeed.models import PostTag

    tags = PostTag.objects.filter(
        post_id=OuterRef("pk"), entity__realms__type="community"
    )
    allowed = Q(entity__realms__is_active=True, entity__realms__is_private=False)
    if viewer is not None:
        allowed |= Q(
            entity__realms__is_active=True, entity__realms__member__entity=viewer
        )
    inaccessible = tags.exclude(allowed)
    multiple_destinations = (
        tags.values("post_id")
        .annotate(destination_count=Count("entity_id", distinct=True))
        .filter(destination_count__gt=1)
    )
    # Fail closed if a malformed legacy post carries multiple destinations.
    # Every community destination must be readable, and only feed posts qualify.
    return Q(Exists(tags)), (
        ~Q(Exists(inaccessible)) & ~Q(Exists(multiple_destinations)) & Q(on_feed="feed")
    )


def can_read_community(realm, viewer):
    return can_read(
        is_private=realm.is_private,
        is_active=realm.is_active,
        is_member=is_member(realm, viewer),
    )


def assert_post_access(post, request, *, contribute=False):
    """Check current membership at every direct read and mutation boundary."""
    from newsfeed.services.post_visibility import can_view_post

    viewer = getattr(request, "entity", None)
    if post.deleted_at is not None or not can_view_post(post, viewer):
        raise NotFound("Post not available")
    realm = community_for_post(post)
    if (
        realm is not None
        and contribute
        and not can_contribute(
            is_member=is_member(realm, viewer), is_active=realm.is_active
        )
    ):
        raise PermissionDenied("Join this community before contributing")
    return realm


def request_anonymity(request, post, *, default=False):
    try:
        return validate_anonymity(
            request.data.get("is_anonymous", default),
            is_community=community_for_post(post) is not None,
        )
    except ValueError as exc:
        raise ValidationError({"is_anonymous": str(exc)}) from exc


def assert_removable_tag(post, tagged_id):
    realm = community_for_post(post)
    if realm is not None and str(realm.entity_id) == str(tagged_id):
        raise ValidationError(
            {"entity_id": "A community destination cannot be removed"}
        )


def assert_anonymous_upload(upload, post, *, is_anonymous):
    if upload is None or not is_anonymous:
        return
    realm = community_for_post(post)
    prefix = f"uploads/communities/{realm.entity_id}/" if realm else None
    if not prefix or not (upload.key or "").startswith(prefix):
        raise ValidationError(
            {
                "attachment": "Upload anonymous media for this community before attaching it"
            }
        )


def community_metadata(realm, viewer):
    return {
        "realm_id": str(realm.pk),
        "entity_id": str(realm.entity_id),
        "name": realm.name,
        "slug": realm.slug,
        "profile": realm.profile,
        "is_private": realm.is_private,
        "is_member": realm.joined
        if hasattr(realm, "joined")
        else is_member(realm, viewer),
    }
