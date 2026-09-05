"""
Realtime activity on ONE post.

The notification stream (`events_<entity_id>`) is addressed to a PERSON: it
says "something happened that concerns you". A post's comment section needs
the other axis - "something happened on this post", addressed to whoever is
currently looking at it, whether or not it concerns them. Those are different
audiences, so this is a different channel rather than another event on that
one: a comment on a post you are reading is nothing to do with your
notifications, and a comment on a post you are not reading has no business
waking the comment section you do have open.

CHANNEL      `post_<post_id>` - one per post, mirroring how the notification
             stream names its channel after the entity it belongs to.
SSE EVENT    `post_activity` - constant. The stream is already scoped to one
             post by its channel, so naming the event after the post id too
             would only force clients to build listener names at runtime.
BODY         {post_id, event_type, ...}, where `event_type` says what kind of
             activity it was:

               comment   a comment (or reply) was created
               typing    someone is typing in a comment box - `parent_id`
                         says which one, exactly as it does on `comment`
               reaction  a reaction on the post, or on one of its comments,
                         was added, swapped or removed - `target_type` says
                         which
               share     reserved - not published yet

             Clients switch on `event_type`, so the reserved value can start
             being published without a client change; an unknown value must be
             ignored rather than treated as an error.

The body carries IDs and the actor's identity, never comment text. A
subscriber refetches through the normal comments GET, which enforces
`can_view_post` - so the audience of the content stays exactly what it was,
and this channel only ever says that there is something new to fetch.

`typing` is published by the Node server (server/routes/posts/index.js), which
also hosts the SSE endpoint that bridges this channel to clients. Both
publishers write the shape documented here; changing it means changing both.

Best-effort by design: a Redis hiccup must never turn a successful comment
into a 500.
"""

import logging
from datetime import datetime

from entity.utils import (
    get_entity_name,
    get_entity_profile_path,
)
from user_service.services.redis import RedisPubSubClient

logger = logging.getLogger(__name__)

POST_ACTIVITY_EVENT = "post_activity"

# Values `event_type` may take. Only the first two are published today; the
# other two are part of the contract so adding them later is a publisher
# change rather than a client one.
ACTIVITY_COMMENT = "comment"
ACTIVITY_TYPING = "typing"
ACTIVITY_REACTION = "reaction"
ACTIVITY_SHARE = "share"

# What a "reaction" event was aimed at. The post's tallies and a comment's
# tallies are different rows behind different endpoints, so a client cannot
# act on the event without being told which.
TARGET_POST = "post"
TARGET_COMMENT = "comment"


def post_activity_channel(post_id):
    return f"post_{post_id}"


def _actor(entity):
    """
    Who did it, in the shape the clients render.

    `entity_id` is what a client compares against its own to recognise its
    OWN echo - it has already applied that change optimistically, so acting
    on the event again would double-count it.
    """
    if entity is None:
        return None

    return {
        "entity_id": str(entity.id),
        "handle": get_entity_profile_path(entity),
        "name": get_entity_name(entity),
        "type": entity.type,
    }


def publish_post_activity(post_id, event_type, actor=None, **fields):
    """
    Announce activity on `post_id` to whoever has that post open.

    Deferred to COMMIT, for the same reason
    `RedisPubSubClient.publish_json_on_commit` exists: this event makes the
    receiver come back and READ the rows the caller is still writing. Sent
    inline from inside `transaction.atomic()`, the refetch lands on another
    connection and reads the pre-insert state - the comment section would
    flash and show nothing new.

    Outside a transaction on_commit runs immediately, so callers do not have
    to be in one.
    """
    if not post_id:
        return

    body = {
        "post_id": str(post_id),
        "event_type": event_type,
        **fields,
    }

    if actor is not None:
        body["entity"] = actor

    try:
        RedisPubSubClient.publish_json_on_commit(
            post_activity_channel(post_id),
            {
                "logType": None,
                "pod": "podless",
                "event": POST_ACTIVITY_EVENT,
                "message": {
                    "status": True,
                    "auth": True,
                    "message": event_type,
                    "result": body,
                },
                "dateTime": datetime.now().isoformat(),
            },
        )
    except Exception:
        # The activity itself already happened and is committed. Nobody's
        # comment should fail because the announcement did.
        logger.exception("Failed to publish %s activity on post %s", event_type, post_id)


def publish_post_reaction(post_id, entity, action):
    """
    Somebody reacted to the POST itself.

    Rides the same channel as comments because it has the same audience: a
    reader sitting on the post, watching a reaction count that has just gone
    stale. The post author's own notification is a separate publish on their
    entity channel and stays where it is - that one is about them, this one is
    about the post.

    `action` is "added" | "updated" | "removed". Clients refetch the tallies
    rather than deriving them from it, because they cannot know which emoji
    row moved or whether a concurrent reaction landed in between - so this is
    descriptive, not something to apply.
    """
    publish_post_activity(
        post_id,
        ACTIVITY_REACTION,
        actor=_actor(entity),
        target_type=TARGET_POST,
        action=action,
    )


def publish_comment_reaction(comment, entity, action):
    """
    Somebody reacted to one of the post's COMMENTS.

    Same event as the post reaction above, aimed one level down: the client
    refetches that comment's tallies rather than the post's. Published to the
    POST's channel, not a comment-specific one - the reader subscribed to a
    post, and a channel per comment would mean one subscription per row on
    screen.
    """
    publish_post_activity(
        comment.post_id,
        ACTIVITY_REACTION,
        actor=_actor(entity),
        target_type=TARGET_COMMENT,
        comment_id=str(comment.comment_id),
        action=action,
    )


def publish_comment_created(comment, entity):
    """
    A comment or reply was just created on `comment.post`.

    `parent_id` is the thread the row actually landed in, which is not
    necessarily the comment the author aimed at - replying to a reply
    re-parents onto the top-level ancestor (see CommentsView.post). The
    client uses it to decide WHICH list to refetch: null means the top-level
    list, anything else means that thread, and a thread nobody has expanded
    is nothing to refetch at all.
    """
    publish_post_activity(
        comment.post_id,
        ACTIVITY_COMMENT,
        actor=_actor(entity),
        comment_id=str(comment.comment_id),
        parent_id=(
            str(comment.parent_comment_id) if comment.parent_comment_id else None
        ),
    )
