"""Community post transitions, serialized with realm policy and membership writes."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware, now
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from community.models import Member, Realm, Approvals
from community.posts import community_for_post
from community.role_policy import require_realm_actor
from entity.permissions import Permission
from entity.services.permission_resolver import has_active_permission as has_permission
from newsfeed.models import Post, PostTag
from newsfeed.services.post_visibility import can_view_post


class ReviewConflict(APIException):
    status_code = 409
    default_detail = "This submission changed. Refresh before reviewing it."


def content_version(post):
    return post.updated_at or post.date_posted


def lock_post_realm(post_id):
    ids = list(PostTag.objects.filter(post_id=post_id, entity__realms__type="community")
               .values_list("entity__realms__pk", flat=True).distinct()[:2])
    if len(ids) > 1:
        raise NotFound("Post not available")
    return get_object_or_404(Realm.objects.select_for_update(), pk=ids[0]) if ids else None


def submission_approval(actor, realm):
    if realm is None:
        return 1
    require_realm_actor(actor, realm)
    if not has_permission(actor, Permission.POSTS_CREATE, fresh=True):
        raise PermissionDenied("You cannot publish posts right now.")
    if not has_permission(actor, Permission.REALM_POST_SUBMIT, realm, fresh=True):
        raise PermissionDenied("You cannot submit posts in this community.")
    if has_permission(actor, Permission.REALM_POST_PUBLISH, realm, fresh=True):
        return 1
    return 0


def validate_publication(post, realm):
    """Recheck source/tag/author eligibility on the version being approved."""
    if str(post.entity_id) != str(realm.entity_id):
        require_realm_actor(post.entity, realm)
        if not has_permission(post.entity, Permission.REALM_POST_SUBMIT, realm, fresh=True):
            raise PermissionDenied("The author can no longer submit posts here.")
    if not has_permission(post.entity, Permission.POSTS_CREATE, fresh=True):
        raise PermissionDenied("The author cannot publish posts right now.")
    if post.is_archived or post.deleted_at is not None or (post.expires_at and post.expires_at <= now()):
        raise ValidationError("This post is no longer eligible for publication.")
    tagged = set(map(str, post.tagging.exclude(entity_id=realm.entity_id).values_list("entity_id", flat=True)))
    members = set(map(str, Member.objects.filter(realm=realm, entity_id__in=tagged).values_list("entity_id", flat=True)))
    if tagged != members:
        raise ValidationError("Everyone tagged must still be a member of this community.")
    if post.is_shared:
        source_ids = list(post.references.values_list("reference", flat=True))
        sources = list(Post.objects.filter(pk__in=source_ids, deleted_at=None, is_archived=False)
                       .prefetch_related("tagging__entity__realms").order_by("pk"))
        if len(sources) != len(set(source_ids)) or not sources:
            raise ValidationError("The shared post is no longer available.")
        for source in sources:
            destination = community_for_post(source)
            if not can_view_post(source, post.entity) or (destination and destination.is_private and destination.pk != realm.pk):
                raise ValidationError("The shared post is no longer available here.")


def _version(value):
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        parsed = None
    if parsed is None or not hasattr(parsed, "tzinfo") or not is_aware(parsed):
        raise ValidationError({"expected_version": "Send the current content version with timezone."})
    return parsed


@transaction.atomic
def review_post(*, actor, realm_id, post_id, action, expected_approval, expected_version, reason=""):
    from community.review_delivery import publish_review_effects

    if action not in ("approve", "reject", "takedown"):
        raise ValidationError({"action": "Choose approve, reject or takedown."})
    if type(expected_approval) is not int or expected_approval not in (0, 1, 2):
        raise ValidationError({"expected_approval": "Send the current approval state."})
    version = _version(expected_version)
    if not isinstance(reason, str) or len(reason) > 5000:
        raise ValidationError({"reason": "Use a reason of at most 5000 characters."})
    reason = reason.strip()
    if action in ("reject", "takedown") and not reason:
        raise ValidationError({"reason": "Explain this decision to the author."})
    realm = get_object_or_404(Realm.objects.select_for_update(), pk=realm_id, type="community", is_active=True)
    require_realm_actor(actor, realm)
    permission = Permission.REALM_POST_TAKEDOWN if action == "takedown" else Permission.REALM_POST_REVIEW
    if not has_permission(actor, permission, realm, fresh=True):
        raise PermissionDenied("You cannot review this post.")
    post = get_object_or_404(Post.objects.select_for_update(of=("self",)).select_related("entity"), pk=post_id, deleted_at=None)
    destination = community_for_post(post)
    if destination is None or destination.pk != realm.pk:
        raise NotFound("Post not available")
    if content_version(post) != version:
        raise ReviewConflict()
    desired = 1 if action == "approve" else 2
    previous = Approvals.objects.filter(realm=realm, target_kind="post", target_id=post.pk).first()
    # A repeated request returns its original audit only; a conflicting review is stale.
    if post.approval != expected_approval:
        if (previous and previous.action == action and previous.actor_id == str(actor.pk) and
            previous.content_version == version and previous.reason == reason and
            previous.previous_state.get("approval") == expected_approval and previous.new_state.get("approval") == post.approval):
            transaction.on_commit(lambda: publish_review_effects(str(previous.pk)), robust=True)
            return previous
        raise ReviewConflict()
    allowed = {"approve": (0, 2), "reject": (0,), "takedown": (1,)}
    if post.approval not in allowed[action]:
        raise ReviewConflict()
    if str(post.entity_id) == str(actor.pk) and not has_permission(actor, Permission.REALM_POST_PUBLISH, realm, fresh=True):
        raise PermissionDenied("You cannot review your own restricted submission.")
    if desired == 1:
        validate_publication(post, realm)
    published = post.approval == 1 or Approvals.objects.filter(
        realm=realm, target_kind="post", target_id=post.pk, new_state__published=True).exists()
    # Legacy unapproved rows without submission history are conservatively treated as published.
    if post.approval == 2 and previous is None:
        published = True
    first_publication = desired == 1 and not published
    decision = Approvals.objects.create(realm=realm, actor=actor, target_kind="post", target_id=post.pk,
        action=action, previous_state={"approval": post.approval, "published": published},
        new_state={"approval": desired, "published": published or desired == 1}, reason=reason,
        content_version=version, delivery_state={"first_publication": first_publication, "status": "pending"})
    Post.objects.filter(pk=post.pk).update(approval=desired)
    transaction.on_commit(lambda: publish_review_effects(str(decision.pk)), robust=True)
    return decision
