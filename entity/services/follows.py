"""
Follow lifecycle + the feed side effects that hang off it.

The newsfeed is fan-out-on-write: NewsfeedIndex rows are keyed by `bucket`
(the VIEWER's entity id), and fetch_friends_posts() reads that bucket. So
"whose posts land in my feed" is decided entirely by who writes into my
bucket - which is now the FOLLOW graph rather than the connection graph.

Following is directional and a superset of connecting (sending a contact
request and accepting one both auto-follow), so a connection still fills the
feed - it just does so by way of the follow it creates, and unfollowing is
what empties it again.

Everything feed-related is deferred-imported inside the functions: this
module is imported by user.views and community.views, while the newsfeed
helpers import back into user.models, so a module-scope import would close a
cycle.
"""

from django.db.models import Q, F

from entity.models import Follow, Connection, Entity
from ..utils import entity_side_is_visible


def get_follower_ids(entity_id, limit=500):
    """
    Entity ids that follow `entity_id` - i.e. the buckets a new post or a
    comment bump should fan out into.

    Ordered by interaction score so a capped fan-out reaches the most
    engaged followers first, mirroring what get_ranked_connections did for
    the connection-based version.

    Pending (status=False) follows are excluded: a follow request that has
    not been approved yet must not seed the requester's feed, which is the
    whole point of gating a private profile.
    """
    qs = (
        Follow.objects.filter(followee_id=entity_id, status=True)
        .order_by("-interaction_score", "-last_interaction_at")
        .values_list("follower_id", flat=True)
    )
    if limit:
        qs = qs[:limit]
    return [str(fid) for fid in qs]


def entity_is_private(entity):
    """
    True only for a private USER profile.

    Privacy is a person-level setting: Realm.is_private already means
    "invite-only group" and is enforced by the community join/invite rules,
    so a realm is never treated as a private profile here. Reading it as one
    hid every private group's own feed from its members, since membership is
    a Member row and never a Follow.
    """
    if entity is None:
        return False

    account = getattr(entity, "users", None)
    return bool(account and account.is_private)


def accepted_follow_exists(follower, followee):
    """Approved follow edge only - a pending request does not count."""
    if not isinstance(follower, Entity) or not isinstance(followee, Entity):
        return False

    return Follow.objects.filter(
        follower=follower, followee=followee, status=True
    ).exists()


def get_profile_relationship_state(viewer_entity, target_entity):
    """
    Everything a profile surface needs to know about how `viewer_entity`
    relates to `target_entity`, in one place, so the profile header, the
    profile feed and the diary summary can never disagree with each other.

    `viewer_entity` is None for a guest, and either side may be a realm -
    all of it degrades to "no relationship" rather than raising.

    ACCESS RULE (`can_view`): a private profile is readable by yourself, by
    an accepted connection, and by an APPROVED follower. A pending follow
    request grants nothing, which is what makes the gate hold - see
    follow_entity(), where a request against a private profile is created
    with status=False instead of auto-approving.

    NOTE: a "single" connection is stored as two mirrored rows sharing the
    same connection_id (one per direction), so the Q() below matches both.
    Ordered by action_date so we deterministically land on the row created
    first, i.e. the actual requester's row - otherwise which row comes back
    is arbitrary and is_user_connection_initiator flips depending on query
    order.
    """
    connection_list = list(
        Connection.objects.select_related("action_by", "involved_entity")
        .filter(
            Q(action_by=viewer_entity, involved_entity=target_entity)
            | Q(action_by=target_entity, involved_entity=viewer_entity),
            ~Q(action_by=F("involved_entity")),
            entity_side_is_visible("action_by"),
            entity_side_is_visible("involved_entity"),
        )
        .order_by("action_date")
    )

    connection_record = connection_list[0] if connection_list else None

    is_connection_present = connection_record is not None
    connection_id = connection_record.connection_id if connection_record else None
    is_user_connection_initiator = (
        connection_record.action_by_id == getattr(viewer_entity, "id", None)
        if connection_record
        else None
    )

    # Both mirrored rows are flipped together on accept (UserContacts.put
    # updates by connection_id), so "every row is True" is the handshake.
    # Guarded on the list being non-empty so no rows never reads as accepted.
    is_connection_handshaked = bool(connection_list) and all(
        conn.status for conn in connection_list
    )

    follow_record = (
        Follow.objects.filter(follower=viewer_entity, followee=target_entity).first()
        if isinstance(viewer_entity, Entity) and isinstance(target_entity, Entity)
        else None
    )
    # is_follower stays the "you follow them" flag the clients already read,
    # so it must mean an ESTABLISHED follow. A pending request is reported
    # separately as is_follow_pending, which is what drives the "Requested"
    # button state.
    is_follower = bool(follow_record and follow_record.status)
    is_follow_pending = bool(follow_record and not follow_record.status)

    is_private = entity_is_private(target_entity)

    is_self = viewer_entity is not None and viewer_entity == target_entity
    has_relationship = is_connection_handshaked or is_follower

    if is_self:
        can_view = True
    elif is_private:
        can_view = has_relationship
    else:
        can_view = True

    return {
        "connection_record": connection_record,
        "connection_id": connection_id,
        "is_connection_present": is_connection_present,
        "is_connection_handshaked": is_connection_handshaked,
        "is_user_connection_initiator": is_user_connection_initiator,
        "is_follower": is_follower,
        "is_follow_pending": is_follow_pending,
        "is_private": is_private,
        "is_self": is_self,
        "can_view": can_view,
    }


def follow_entity(follower, followee, backfill=True):
    """
    Create the follow edge and, when it is immediately approved, seed the
    follower's feed with the followee's recent posts.

    A follow of a PRIVATE user profile is created pending (status=False) and
    backfills nothing: the followee has to approve it through
    approve_follow_request() before any of their content moves. Public
    targets - and every realm - auto-approve, so the ordinary path is
    unchanged.

    This is the load-bearing half of the private-profile gate. can_view
    counts an approved follower as having access, so if this auto-approved
    against a private target then anyone could unlock a private profile by
    tapping Follow.

    Idempotent: re-following is a no-op and does NOT re-backfill, so the
    auto-follow on a contact request followed by the auto-follow on accept
    cannot double-write feed rows. It also never silently upgrades a pending
    row to approved - only the followee can do that.

    Returns (created, is_pending) so callers can word their notification and
    their response ("Followed" vs "Requested").
    """
    if follower is None or followee is None:
        return False, False
    if str(getattr(follower, "id", follower)) == str(getattr(followee, "id", followee)):
        return False, False  # nobody follows themselves

    needs_approval = entity_is_private(followee)

    record, created = Follow.objects.get_or_create(
        follower=follower,
        followee=followee,
        defaults={"status": not needs_approval},
    )

    is_pending = not record.status

    if created and not is_pending and backfill:
        from newsfeed.helpers.query_functions import backfill_new_friend_feed

        backfill_new_friend_feed(follower.id, followee.id)

    return created, is_pending


def approve_follow_request(follower, followee):
    """
    Followee-side accept of a pending follow. Flips the edge to approved and
    runs the feed backfill that follow_entity deliberately skipped, so the
    new follower's bucket catches up on what they missed while waiting.

    Returns True when a pending request was actually approved - a re-tap on
    an already-approved (or absent) request is a no-op, not an error.
    """
    if follower is None or followee is None:
        return False

    updated = Follow.objects.filter(
        follower=follower, followee=followee, status=False
    ).update(status=True)

    if not updated:
        return False

    from newsfeed.helpers.query_functions import backfill_new_friend_feed

    backfill_new_friend_feed(follower.id, followee.id)

    return True


def decline_follow_request(follower, followee):
    """
    Followee-side reject. Drops the pending row entirely rather than keeping
    a declined tombstone, so the requester can ask again later and the
    unique_together does not block them forever.

    Only ever touches a PENDING row: declining must not be usable to remove
    an established follower (that is the separate remove-follower path), and
    an approved edge is left alone.
    """
    if follower is None or followee is None:
        return False

    deleted, _ = Follow.objects.filter(
        follower=follower, followee=followee, status=False
    ).delete()

    return bool(deleted)


def link_follows_for_connection(entity_a, entity_b):
    """
    Bring both follow directions up to APPROVED because the two entities just
    became connections.

    Accepting a contact request settles the same question a follow request
    asks, so anything still pending between the pair is approved outright
    rather than left for a second, redundant approval. Concretely this covers
    the requester's follow, which follow_entity() parked as pending when the
    target turned out to be private - without this, accepting their contact
    request would leave them connected but still feed-less.

    Symmetric on purpose: a connection is mutual, so both buckets get seeded.
    """
    if entity_a is None or entity_b is None:
        return

    for follower, followee in ((entity_a, entity_b), (entity_b, entity_a)):
        record, created = Follow.objects.get_or_create(
            follower=follower, followee=followee, defaults={"status": True}
        )

        if created:
            from newsfeed.helpers.query_functions import backfill_new_friend_feed

            backfill_new_friend_feed(follower.id, followee.id)
        elif not record.status:
            approve_follow_request(follower, followee)


def unfollow_entity(follower, followee):
    """
    Drop the follow edge and pull the followee's fanned-out posts back out of
    the follower's feed bucket.

    Note this is the ONLY thing that empties a feed bucket now. Unfriending
    deliberately does not: you can be connected to someone, unfriend them and
    still follow them, and in that case their posts should keep arriving.

    Returns True when an edge was actually removed.
    """
    if follower is None or followee is None:
        return False

    deleted, _ = Follow.objects.filter(follower=follower, followee=followee).delete()

    if deleted:
        from newsfeed.helpers.query_functions import remove_feed_on_unfriend

        remove_feed_on_unfriend(follower.id, str(followee.id))

    return bool(deleted)


def purge_between(a, b):
    """
    Tear down the relationship between two entities in BOTH directions: the
    follow edges either way, and each side's feed bucket of the other's
    posts.

    This is what removing a connection does - a connection is mutual, so
    purging it is mutual too. Contrast unfollow_entity(), which is
    deliberately one-directional.

    The feed clearing here is UNCONDITIONAL, not gated on having deleted a
    follow row the way unfollow_entity() is. The two can disagree: someone
    may have unfollowed manually at some point, leaving no follow edge while
    the posts that edge seeded are still sitting in their bucket. Keying the
    cleanup off the edge would strand those rows in the feed forever.
    """
    if a is None or b is None:
        return

    from newsfeed.helpers.query_functions import remove_feed_on_unfriend

    # Both edges in one statement rather than two filtered deletes.
    Follow.objects.filter(
        Q(follower=a, followee=b) | Q(follower=b, followee=a)
    ).delete()

    remove_feed_on_unfriend(a.id, str(b.id))
    remove_feed_on_unfriend(b.id, str(a.id))
