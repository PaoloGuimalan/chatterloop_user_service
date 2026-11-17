"""Review notices reuse the existing canonical inbox and personal notification rows."""
from community.admin_notifications import record_admin_activity, settle_review_notices, _upsert_notice, _publish_event
from community.models import Member
from entity.utils import get_entity_name


def deliver_review_notices(decision, post, realm, *, first_publication=False):
    author_id = str(post.entity_id)
    subject = "An anonymous member" if post.is_anonymous else get_entity_name(post.entity)
    sender = str(realm.entity_id) if post.is_anonymous else author_id
    if decision.action in ("submit", "edit"):
        record_admin_activity(realm, dict(referenceID=str(decision.pk), toUserID=str(realm.entity_id),
            fromUserID=sender, is_anonymous=post.is_anonymous, type="community_post_pending",
            referenceStatus=False,
            content_headline="Post awaiting approval", content_details=f"{subject} submitted a post for approval.",
            target_type="community_approvals", target_id=str(realm.pk), target_anchor="posts"), actor_id=author_id)
    if first_publication:
        record_admin_activity(realm, dict(referenceID=str(post.pk), toUserID=str(realm.entity_id),
            fromUserID=sender, is_anonymous=post.is_anonymous, type="community_post_created",
            content_headline="New community post", content_details=f"{subject} created a post in the community.",
            target_type="post", target_id=str(post.pk)), actor_id=author_id)
        recipients = list(Member.objects.filter(realm=realm, entity__type="user",
            entity_id__in=post.tagging.values_list("entity_id", flat=True)).exclude(entity_id=post.entity_id)
            .values_list("entity_id", flat=True))
        for recipient in recipients:
            _upsert_notice(referenceID=str(post.pk), toUserID=str(recipient), fromUserID=sender,
                is_anonymous=post.is_anonymous, type="tag_notification", content_headline="Tagged post",
                content_details=f"{subject} tagged you in a post in the community.", target_type="post", target_id=str(post.pk))
            _publish_event(realm, recipient, "notifications")
    if decision.action in ("approve", "reject", "takedown"):
        settle_review_notices(realm, 'post', post.pk, through_version=decision.content_version)
        message = {"approve": "Your post in the community was approved.",
                   "reject": "Your submission to the community was not approved.",
                   "takedown": "Your post in the community was taken down by its administrators."}[decision.action]
        _upsert_notice(referenceID=str(decision.pk), toUserID=author_id, fromUserID=str(realm.entity_id),
            is_anonymous=False, type="community_post_decision", content_headline="Community post decision",
            content_details=message, target_type="realm_review", target_id=str(decision.pk))
        _publish_event(realm, author_id, "notifications", metadata={
            'type': 'community_post_decision', 'review_decision_id': str(decision.pk)})
        from user_service.services.push import activity_payload
        from user_service.services.rabbitmq import Queues, RabbitMQClient
        payload = activity_payload([author_id], type='community_post_decision', title='Community post decision',
            body='Your community post was reviewed.', route=f'/realm-review/{decision.pk}')
        payload['tag'] = f'community-decision-{decision.pk}'
        payload['data']['reviewDecisionID'] = str(decision.pk)
        if not RabbitMQClient.publish(Queues.SEND_PUSH, payload):
            raise RuntimeError('Community decision push could not be queued')
