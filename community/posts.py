"""Community destinations, current audience and safe contribution projections."""

from django.db.models import BooleanField, Count, Exists, OuterRef, Q, Subquery, Value
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


def viewer_reaction_identity_annotations(viewer, *, comment=False):
    """Nullable identity of only the viewing user/page's own reaction."""
    from newsfeed.models import CommentReaction, Reaction

    if viewer is None:
        identity = Value(None, output_field=BooleanField())
    else:
        model, target = (CommentReaction, "comment") if comment else (Reaction, "post")
        identity = Subquery(
            model.objects.filter(**{target: OuterRef("pk"), "entity": viewer}).values(
                "is_anonymous"
            )[:1],
            output_field=BooleanField(),
        )
    return {"entity_reaction_is_anonymous": identity}


def viewer_reaction_identity(instance, viewer):
    """Use the list annotation, with a scoped fallback for direct/saved reads."""
    from newsfeed.models import CommentReaction, Post, Reaction

    if viewer is None:
        return None
    if hasattr(instance, "entity_reaction_is_anonymous"):
        return instance.entity_reaction_is_anonymous
    model, target = (
        (Reaction, "post")
        if isinstance(instance, Post)
        else (CommentReaction, "comment")
    )
    return (
        model.objects.filter(**{target: instance, "entity": viewer})
        .values_list("is_anonymous", flat=True)
        .first()
    )


def is_member(realm, entity):
    return (
        entity is not None
        and Member.objects.filter(realm=realm, entity=entity).exists()
    )


def profile_anonymity_filter(viewer_id, profile_owner_id):
    """Anonymous authorship belongs on a profile only in its owner's view."""
    visible = Q(is_anonymous=False)
    if viewer_id and str(viewer_id) == str(profile_owner_id):
        visible |= Q(entity_id=viewer_id)
    return visible


def community_home_filter(viewer_id, connection_ids):
    """Own community posts surface at Home only after a connection interacts."""
    from newsfeed.models import Comment, PostTag, Reaction

    destination = PostTag.objects.filter(
        post_id=OuterRef("pk"), entity__realms__type="community"
    )
    comments = Comment.objects.filter(
        post_id=OuterRef("pk"),
        entity_id__in=connection_ids,
        is_anonymous=False,
        deleted_at=None,
    )
    reactions = Reaction.objects.filter(
        post_id=OuterRef("pk"), entity_id__in=connection_ids, is_anonymous=False
    )
    return (
        ~Q(entity_id=viewer_id)
        | ~Q(Exists(destination))
        | Q(Exists(comments))
        | Q(Exists(reactions))
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
        "is_verified": realm.is_verified,
        "is_private": realm.is_private,
        "is_member": realm.joined
        if hasattr(realm, "joined")
        else is_member(realm, viewer),
    }
