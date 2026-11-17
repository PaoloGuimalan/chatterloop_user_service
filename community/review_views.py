"""Immutable community decisions; never an exception to ordinary post reads."""
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from community.models import Approvals
from community.post_review import content_version
from community.posts import prime_community_memberships
from community.review_serializers import decision_payload
from community.role_policy import require_realm_actor, realm_capabilities
from newsfeed.models import Post
from newsfeed.serializers import PostSerializer, RealmPreviewSerializer
from newsfeed.services.post_cards import read_only_post_cards, unavailable_post_card


def serialize_review_decision(decision, request):
    post = read_only_post_cards(Post.objects.filter(pk=decision.target_id)).first()
    actor = getattr(request, "entity", None)
    is_owner = post is not None and actor is not None and str(post.entity_id) == str(actor.pk)
    if not is_owner:
        try:
            require_realm_actor(actor, decision.realm)
            if not realm_capabilities(actor, decision.realm)["can_review_posts"]:
                raise PermissionDenied()
        except PermissionDenied as exc:
            raise NotFound("Review decision not available") from exc
    payload = None
    current_version = content_version(post) if post is not None else None
    # Destinations cannot normally change. Fail closed on an inconsistent row
    # rather than letting this realm's reviewer read another realm's content.
    same_realm = post is not None and any(str(tag.entity_id) == str(decision.realm.entity_id) for tag in post.tagging.all())
    if same_realm:
        cache = {}
        prime_community_memberships([post], actor, cache)
        payload = PostSerializer(post, context={"request": request, "community_memberships": cache,
            "suppress_link_previews": post.deleted_at is not None or (post.is_archived and not is_owner)}).data
        payload.update(type="post", read_only=True, is_removed=post.deleted_at is not None)
        if post.deleted_at is not None or (post.is_archived and not is_owner):
            payload = unavailable_post_card(payload)
        else:
            from newsfeed.moment_views import _shared_previews
            payload["shared_preview"] = _shared_previews(actor, [post], membership_cache=cache).get(post.pk)
    audit = decision_payload(decision)
    return {"source": "community", "decision": audit,
        "community": RealmPreviewSerializer(decision.realm).data,
        "viewer_is_owner": is_owner, "content": payload,
        "content_is_current": True,
        "current_content_version": current_version.isoformat() if current_version else None,
        "matches_reviewed_version": current_version is not None and current_version == decision.content_version,
        "moderation": {"source": "community", "verdict": decision.action, "reason": decision.reason,
            "removed": decision.new_state.get("approval") == 2, "enforced_at": audit["created_at"]}}


class RealmReviewDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, decision_id):
        decision = get_object_or_404(Approvals.objects.select_related("realm"), pk=decision_id, target_kind="post")
        return Response({"status": True, "data": serialize_review_decision(decision, request)})
