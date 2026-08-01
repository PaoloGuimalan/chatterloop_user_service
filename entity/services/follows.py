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
    """
    qs = (
        Follow.objects.filter(followee_id=entity_id)
        .order_by("-interaction_score", "-last_interaction_at")
        .values_list("follower_id", flat=True)
    )
    if limit:
        qs = qs[:limit]
    return [str(fid) for fid in qs]


def get_profile_relationship_state(viewer_entity, target_entity):

    connection_qs = (
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

    connection_record = connection_qs.first()

    is_connection_present = connection_record is not None
    connection_id = connection_record.connection_id if connection_record else None
    is_user_connection_initiator = (
        connection_record.action_by_id == viewer_entity.id
        if connection_record
        else None
    )

    is_follower = (
        Follow.objects.filter(
            follower=viewer_entity,
            followee=target_entity,
        ).exists()
        if isinstance(viewer_entity, Entity) and isinstance(target_entity, Entity)
        else False
    )

    # Resolve privacy status via OneToOne relations (users or realms)
    is_private = False
    if hasattr(target_entity, "users"):
        is_private = target_entity.users.is_private
    elif hasattr(target_entity, "realms"):
        is_private = target_entity.realms.is_private

    connection_list = list(connection_qs)
    is_connection_handshaked = len(connection_list) == 2 and all(
        conn.status for conn in connection_list
    )

    # Define access criteria based on entity type rules
    is_self = viewer_entity == target_entity
    has_relationship = is_connection_handshaked or is_follower

    if is_self:
        can_view = True
    elif is_private:
        can_view = has_relationship
    else:
        can_view = True

    return {
        "connection_exists": connection_qs,
        "connection_record": connection_record,
        "connection_id": connection_id,
        "is_connection_present": is_connection_present,
        "is_connection_handshaked": is_connection_handshaked,
        "is_user_connection_initiator": is_user_connection_initiator,
        "is_follower": is_follower,
        "is_private": is_private,
        "can_view": can_view,
    }


def follow_entity(follower, followee, backfill=True):
    """
    Create the follow edge and seed the follower's feed with the followee's
    recent posts.

    Idempotent: re-following is a no-op and does NOT re-backfill, so the
    auto-follow on a contact request followed by the auto-follow on accept
    cannot double-write feed rows.

    Returns True when a new edge was created.
    """
    if follower is None or followee is None:
        return False
    if str(getattr(follower, "id", follower)) == str(getattr(followee, "id", followee)):
        return False  # nobody follows themselves

    _, created = Follow.objects.get_or_create(follower=follower, followee=followee)

    if created and backfill:
        from newsfeed.helpers.query_functions import backfill_new_friend_feed

        backfill_new_friend_feed(follower.id, followee.id)

    return created


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
