"""Authorized review/submission envelopes around the existing post card serializer."""
from community.post_review import content_version
from newsfeed.serializers import PostSerializer


def decision_payload(decision):
    return {"id": str(decision.pk), "action": decision.action, "target_kind": decision.target_kind,
            "target_id": decision.target_id, "previous_state": decision.previous_state,
            "new_state": decision.new_state, "reason": decision.reason,
            "content_version": decision.content_version.isoformat() if decision.content_version else None,
            "created_at": decision.created_at.isoformat()}


def submission_payload(post, request, decision=None, *, context=None):
    version = content_version(post)
    current = decision and decision.content_version == version and decision.new_state.get("approval") == post.approval
    return {"post": PostSerializer(post, context=context or {"request": request}).data,
            "approval": post.approval, "content_version": version.isoformat(),
            "decision": decision_payload(decision) if current else None}
