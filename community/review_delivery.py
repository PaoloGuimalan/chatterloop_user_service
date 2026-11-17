"""Retryable post-publication transport, tracked on the immutable review's SQL row."""
import logging
import json
import uuid
from django.db import transaction
from django.db.models import F
from community.models import Realm, Approvals
from community.review_notifications import deliver_review_notices
from newsfeed.models import Post, PostScore
from user_service.services.rabbitmq import Queues, RabbitMQClient

logger = logging.getLogger(__name__)


def log_approved_share(post):
    from user.models import UserEngagementLog
    UserEngagementLog.create(log_id=uuid.uuid5(uuid.NAMESPACE_URL, f"community-share:{post.pk}"),
        user_id=str(post.entity_id), activity_time=post.date_posted, time_spent=0.,
        activity_type="share", target_type="post", target_id=str(post.pk),
        metadata=json.dumps({"via": "feed"}), created_at=post.date_posted, updated_at=post.date_posted)


def publish_review_effects(decision_id):
    """Re-read SQL before every delivery; unavailable work stays discoverable."""
    with transaction.atomic():
        reference = Approvals.objects.filter(pk=decision_id).values("realm_id", "target_id").first()
        if reference is None:
            return False
        realm = Realm.objects.select_for_update().get(pk=reference["realm_id"])
        post = Post.objects.select_for_update(of=("self",)).select_related("entity").filter(pk=reference["target_id"]).first()
        decision = Approvals.objects.select_for_update().get(pk=decision_id)
        state = dict(decision.delivery_state)
        if state.get("status") == "complete":
            return True
        if (post is None or not realm.is_active or post.deleted_at is not None or (post.is_archived and post.approval == 1) or
            (post.updated_at or post.date_posted) != decision.content_version or
            post.approval != decision.new_state.get("approval")):
            state["status"] = "obsolete"
            decision.delivery_state = state
            decision.save(update_fields=["delivery_state"])
            return False
        if post.approval == 1:
            stages = [("fanout", Queues.BULK_FANOUT_TO_CACHE, {"current_entity_id": str(post.entity_id),
                "post_data": {"id": str(post.pk), "author_id": str(post.entity_id)}, "type": "fanout"})]
            if state.get("first_publication"):
                stages.insert(0, ("score", Queues.CREATE_POST_SCORE_FOR_NEW_POST, {"post_id": str(post.pk), "date_posted": post.date_posted}))
            for name, queue, payload in stages:
                if state.get(name) == "sent":
                    continue
                if not RabbitMQClient.publish(queue, payload):
                    return False
                state[name] = "sent"
                decision.delivery_state = state
                decision.save(update_fields=["delivery_state"])
            if state.get("first_publication") and post.is_shared:
                sources = sorted(set(post.references.values_list("reference", flat=True)))
                if not state.get("share_scores_applied"):
                    scores = {}
                    for source_id in sources:
                        score = PostScore.objects.select_for_update().filter(post_id=source_id).first()
                        if score is None:
                            source = Post.objects.filter(pk=source_id).first()
                            if source is not None:
                                RabbitMQClient.publish(Queues.CREATE_POST_SCORE_FOR_NEW_POST,
                                    {"post_id": str(source.pk), "date_posted": source.date_posted})
                            return False
                        scores[source_id] = score
                    for source_id, score in scores.items():
                        PostScore.objects.filter(pk=score.pk).update(shares_count=F("shares_count") + 1,
                            recent_update_boost=F("recent_update_boost") + .5)
                    state["share_scores_applied"] = True
                    decision.delivery_state = state
                    decision.save(update_fields=["delivery_state"])
                if state.get("share_refresh") != "sent":
                    for source_id in sources:
                        if not RabbitMQClient.publish(Queues.UPDATE_RANKING_SCORE,
                                {"post_id": source_id, "update_type": "refresh", "is_decrease": False}):
                            return False
                    state["share_refresh"] = "sent"
                if state.get("share_log") != "sent":
                    log_approved_share(post)
                    state["share_log"] = "sent"
        if state.get("notices") != "sent":
            deliver_review_notices(decision, post, realm, first_publication=bool(state.get("first_publication")))
            state["notices"] = "sent"
        state["status"] = "complete"
        decision.delivery_state = state
        decision.save(update_fields=["delivery_state"])
        from community.post_events import notify_post_state_changed
        transaction.on_commit(lambda: notify_post_state_changed(post.pk), robust=True)
        return True
