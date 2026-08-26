"""
Hand content to the moderation service for moderation and interest tagging.

GATED ON PRESENCE
-----------------
The moderation service announces itself in Redis with a short TTL. When that
key is absent this publishes NOTHING and the service's database scour picks the
content up on its next start. That is the designed path, not a degraded one -
which is why skipping is safe and why nothing here raises.

The check fails CLOSED (see RedisPubSubClient.is_moderation_service_online), so
an unreachable Redis also skips. Publishing into a queue that may have no
consumer would be worse than skipping: the scour never revisits content the
backend believes it already handed off.
"""

import logging
import uuid
from datetime import datetime, timezone

from user_service.services.rabbitmq import Queues, RabbitMQClient
from user_service.services.redis import RedisPubSubClient

logger = logging.getLogger(__name__)

# Resolved here, from the mime the caller already holds, so the moderation
# service never guesses a type from a file extension.
_MEDIA_TOP_LEVELS = ("image", "video", "audio")


def content_type_for(mime):
    top = str(mime or "").split("/")[0]
    return top if top in _MEDIA_TOP_LEVELS else "file"


def queue_comment(comment, attachment_mime=None):
    """Publish one comment - its text, and its attachment if it has one.

    Deferred to COMMIT via publish_on_commit: the moderation service reads
    newsfeed_comment, and publishing inside the transaction is a race it
    usually wins, leaving it to read a row that is not there yet.
    """
    if not RedisPubSubClient.is_moderation_service_online():
        return False

    items = []

    if comment.text and comment.text.strip():
        items.append(
            {
                "target_id": str(comment.comment_id),
                "content_type": "text",
                "text": comment.text,
            }
        )

    if comment.attachment:
        # newsfeed_comment.attachment is a bare URL with no mime column - the
        # post side carries reference_media_type, this one does not. Callers
        # that know the type pass it; otherwise the moderation service resolves
        # it from the Mongo `files` collection by URL.
        items.append(
            {
                "target_id": str(comment.comment_id),
                "content_type": content_type_for(attachment_mime),
                "url": comment.attachment,
                "mime": attachment_mime or "",
            }
        )

    if not items:
        return False

    RabbitMQClient.publish_on_commit(
        Queues.CONTENT_TAGGING,
        {
            "job_id": str(uuid.uuid4()),
            "source_type": "comment",
            "target_id": str(comment.comment_id),
            "entity_id": str(comment.entity_id) if comment.entity_id else None,
            # Comments are moderated, not merely contextualised.
            "strict": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
        },
    )
    return True
