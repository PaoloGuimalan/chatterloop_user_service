"""Member-only community activity notices with no anonymous author identity."""

import logging
from datetime import datetime

from django.db import transaction

from community.models import Member
from community.posts import community_for_post
from entity.utils import get_entity_display_username

logger = logging.getLogger(__name__)


def notification_description(
    *, community_entity_id, actor_name, is_anonymous, verb, post_id, anchor=None,
    actor_entity_id=None,
):
    """Public notification data; ownership is deliberately absent."""
    subject = "An anonymous member" if is_anonymous else actor_name
    return {
        "fromUserID": str(community_entity_id if is_anonymous or not actor_entity_id else actor_entity_id),
        "community_entity_id": str(community_entity_id),
        "is_anonymous": bool(is_anonymous),
        "content_details": f"{subject} {verb} in your community.",
        "target_type": "post",
        "target_id": str(post_id),
        "target_anchor": anchor,
    }


def notify_community_activity(
    post,
    actor,
    reference_id,
    recipients,
    *,
    is_anonymous=False,
    verb="commented",
    anchor=None,
    notification_type="community_activity",
    headline="Community activity",
):
    """Return whether this is a community action; schedule safe delivery if so.

    Re-read membership after SQL commit. The notification reader and worker
    also revalidate access at delivery/read time so a queued notice cannot
    survive a subsequent membership removal.
    """
    if community_for_post(post) is None:
        return False
    post_id = str(post.pk)
    recipient_ids = {
        str(item) for item in recipients if item and str(item) != str(actor.pk)
    }

    def deliver():
        from newsfeed.models import CommentReaction, Post, Reaction
        from user.services.mongohelpers import NotificationService
        from user_service.services.push import activity_payload
        from user_service.services.rabbitmq import Queues, RabbitMQClient
        from user_service.services.redis import RedisPubSubClient

        try:
            current = Post.objects.filter(
                pk=post_id, deleted_at=None, is_archived=False
            ).first()
            realm = community_for_post(current) if current is not None else None
            if realm is None or not realm.is_active:
                return
            reaction_model = {
                "post_reaction": Reaction,
                "comment_reaction": CommentReaction,
            }.get(notification_type)
            anonymous = is_anonymous
            if reaction_model is not None:
                # A previous on_commit callback may run after a newer edit or
                # removal. Project current identity and never revive deletion.
                reaction = reaction_model.objects.filter(pk=reference_id).first()
                if reaction is None:
                    return
                anonymous = reaction.is_anonymous
            actor_name = "" if anonymous else get_entity_display_username(actor)
            members = Member.objects.filter(
                realm=realm, entity_id__in=recipient_ids
            ).values_list("entity_id", flat=True)
            metadata = {
                "post_id": post_id,
                "community_entity_id": str(realm.entity_id),
                "is_anonymous": bool(anonymous),
            }
            description = notification_description(
                community_entity_id=realm.entity_id,
                actor_name=actor_name,
                actor_entity_id=actor.pk,
                is_anonymous=anonymous,
                verb=verb,
                post_id=post_id,
                anchor=anchor,
            )
            for recipient_id in set(map(str, members)):
                service = NotificationService()
                notification = dict(
                    referenceID=str(reference_id),
                    toUserID=recipient_id,
                    content_headline=headline,
                    type=notification_type,
                    **description,
                )
                if reaction_model is not None:
                    service.upsert_community_reaction(**notification)
                else:
                    service.add_notification(
                        **notification, referenceStatus=True, isRead=False
                    )
                RedisPubSubClient.publish_json(
                    f"events_{recipient_id}",
                    {
                        "logType": None,
                        "pod": "podless",
                        "event": "notifications",
                        "message": {
                            "status": True,
                            "auth": True,
                            "message": description["content_details"],
                            "result": metadata,
                        },
                        "dateTime": datetime.now().isoformat(),
                    },
                )
                # Push text is generic even for a named contribution; the
                # worker rechecks current membership using community_post_id.
                route = f"/post/{post_id}" + (f"?anchor={anchor}" if anchor else "")
                payload = activity_payload(
                    [recipient_id],
                    type="community_activity",
                    title="Community activity",
                    body="New activity in your community.",
                    route=route,
                )
                payload["community_post_id"] = post_id
                payload["data"].update(
                    post_id=post_id,
                    community_entity_id=str(realm.entity_id),
                    is_anonymous=str(bool(anonymous)).lower(),
                )
                RabbitMQClient.publish(Queues.SEND_PUSH, payload)
        except Exception:
            logger.exception("Community activity delivery failed for post %s", post_id)

    transaction.on_commit(deliver)
    return True
