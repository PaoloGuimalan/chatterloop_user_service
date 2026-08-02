"""
Realtime nudges for relationship changes.

When one side answers a request, the OTHER side may well be sitting on the
answerer's profile page watching a stale "Requested" / "Pending" button. The
notification SSE already tells them something happened, but it carries no
subject, so a profile page cannot tell whether the event concerns the profile
it is showing.

This publishes a targeted event that names the counterpart, so a client can
decide "that's the profile I'm looking at" and refetch just that - rather than
every open page reloading on every notification.

Best-effort by design: the relationship change is already committed before
this runs, so a Redis hiccup must never turn a successful accept into a 500.
"""

from datetime import datetime

from django.db import transaction

from user_service.services.redis import RedisPubSubClient

PROFILE_RELATIONSHIP_EVENT = "profile_relationship_updated"


def publish_profile_relationship_update(to_entity_id, counterpart_entity_id, reason):
    """
    Tell `to_entity_id` that its relationship with `counterpart_entity_id`
    just changed.

    `reason` is advisory - clients refetch rather than patch state from it,
    because the profile payload computes can_view/is_follower/connection
    together and they must not be updated piecemeal.

    Deferred to COMMIT. This event makes the receiver refetch immediately,
    and every caller runs inside a transaction (accepting or removing a
    connection is one atomic block), so publishing inline raced the commit:
    the refetch landed on a different connection and read the pre-change
    row. That showed up as a profile whose posts had already emptied - the
    connection rows were gone - while its button still read "Following",
    because the follow teardown had not committed yet.

    on_commit outside an atomic block simply runs the callable immediately,
    so this is safe for callers that are not in a transaction.
    """
    if not to_entity_id or not counterpart_entity_id:
        return

    transaction.on_commit(
        lambda: _publish(to_entity_id, counterpart_entity_id, reason)
    )


def _publish(to_entity_id, counterpart_entity_id, reason):
    try:
        RedisPubSubClient.publish_json(
            f"events_{to_entity_id}",
            {
                "logType": None,
                "pod": "podless",
                "event": PROFILE_RELATIONSHIP_EVENT,
                "message": {
                    "status": True,
                    "auth": True,
                    "message": reason,
                    "result": {
                        "entity_id": str(counterpart_entity_id),
                        "reason": reason,
                    },
                },
                "dateTime": datetime.now().isoformat(),
            },
        )
    except Exception as err:
        print(f"Failed to publish profile relationship update: {err}")
