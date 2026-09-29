"""
Publishing side of push notifications.

Sending happens in the Go worker_service (internal/services/rabbitmq/push.go,
queue "send_push"): it resolves which of the recipients' devices are offline,
talks to FCM, and retires the tokens FCM rejects. This only DESCRIBES the
notification - the same job server/reusables/hooks/pushnotification.js
publishes from Node, so a push reads the same whichever service raised it.

The payload is the contract the mobile app renders
(chatterloop_app/lib/core/notifications/push_payload.dart). Only the activity
shape is here: chat messages are raised by Node, which owns messaging.
"""

from django.db import transaction

from user_service.services.rabbitmq import Queues, RabbitMQClient
from user_service.services.redis import RedisPubSubClient

# Must match the mobile app's channel ids exactly (notification_renderer.dart)
# and the worker's ChannelActivity. The _v2 suffix is not cosmetic: a channel's
# sound and importance are locked at creation, so changing either needed a new
# id.
CHANNEL_ACTIVITY = "chatterloop_activity_v2"


def _string_data(data):
    """
    FCM rejects the WHOLE message when any data value is not a string, and the
    worker decodes `data` as map[string]string - so a stray int or None here
    fails the job rather than just that field. Empty values are dropped rather
    than sent as "".
    """
    return {
        key: str(value)
        for key, value in data.items()
        if value is not None and value != ""
    }


def activity_payload(
    receivers,
    type="activity",
    title="",
    body="",
    route="",
    image_url="",
    sender_avatar_url="",
):
    """
    The send_push job for one activity notification, or None when there is no
    one to send it to.

    The app renders every non-message push generically from title/body and
    never branches on [type], so a new kind of alert needs no mobile release.

    [route] is an in-app path, honoured only when it starts with one of the
    app's allowlisted prefixes (PushPayload.allowedRoutePrefixes); anything
    else - or nothing - opens the notifications screen.

    Thumbnails are content-driven and blank when neither is supplied:
      image_url         -> square, shown expanded: the thing reacted to
      sender_avatar_url -> circular: who did it
    Both are downloaded on the device before the notification can be posted,
    so only ever pass a still image, never a video.
    """
    entity_ids = [str(receiver) for receiver in receivers if receiver]
    if not entity_ids:
        return None

    return {
        "entity_ids": entity_ids,
        "tokens": [],
        "channel": CHANNEL_ACTIVITY,
        # Only consulted when os_rendered - the app builds its own text from
        # `data` otherwise.
        "title": title,
        "body": body,
        "tag": "",
        "image_url": image_url or "",
        "os_rendered": False,
        "data": _string_data(
            {
                "type": type,
                "title": title,
                "body": body,
                "route": route,
                "imageUrl": image_url,
                "senderAvatarUrl": sender_avatar_url,
            }
        ),
    }


def send_activity(receivers, cooldown_key=None, cooldown_seconds=None, **kwargs):
    """
    Queue an activity push for [receivers] (entity ids). Takes the same
    arguments as activity_payload.

    With [cooldown_key], at most one push per key goes out per
    [cooldown_seconds]; the rest are dropped HERE, before publishing, so a
    burst never reaches the queue at all. The key is claimed at commit time
    rather than now, so a write that rolls back doesn't use up the window
    without having pushed anything.

    Deferred to the surrounding transaction's commit, so a write that rolls
    back never notifies anyone; outside a transaction it publishes at once.
    Best-effort like every publish here - a broker outage costs the push, never
    the request.
    """
    # A key with no ttl would be SET without an expiry - silencing that key's
    # pushes forever rather than for a window.
    if cooldown_key and not cooldown_seconds:
        raise ValueError("cooldown_key needs a positive cooldown_seconds")

    payload = activity_payload(receivers, **kwargs)
    if payload is None:
        return

    def publish():
        if cooldown_key and not RedisPubSubClient.acquire_push_cooldown(
            cooldown_key, cooldown_seconds
        ):
            return
        RabbitMQClient.publish(Queues.SEND_PUSH, payload)

    transaction.on_commit(publish)
