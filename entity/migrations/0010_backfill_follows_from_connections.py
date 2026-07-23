# Backfills Follow edges for connections that already existed before
# auto-follow was introduced.
#
# The newsfeed is fan-out-on-write keyed on the FOLLOW graph. Auto-follow only
# fires on new requests/accepts, so without this every pre-existing connection
# has no follow edge - and the author's posts would stop reaching people they
# are already connected to. Measured on prod before writing this: 39 accepted
# connection directions, 4 follow rows, 36 directions with no edge.
#
# Deliberately does NOT touch Cassandra/NewsfeedIndex. Existing feed rows were
# already written by the old connection-based fan-out and stay valid; this only
# has to make FUTURE fan-out reach the same audience. Backfilling the index
# itself would be a slow, failure-prone loop over an external store inside a
# migration.

from django.db import migrations


def backfill_follows(apps, schema_editor):
    Connection = apps.get_model("entity", "Connection")
    Follow = apps.get_model("entity", "Follow")

    existing = set(
        Follow.objects.values_list("follower_id", "followee_id")
    )

    # A connection is stored as two mirrored rows (one per direction), so
    # iterating accepted rows covers both sides of every accepted connection.
    pairs = set()
    for row in Connection.objects.filter(status=True).values_list(
        "action_by_id", "involved_entity_id"
    ):
        follower_id, followee_id = row
        if follower_id and followee_id and follower_id != followee_id:
            pairs.add((follower_id, followee_id))

    # Pending connections: only the REQUESTER follows, mirroring what
    # /contacts POST now does at request time. The requester is the action_by
    # of the earliest row in the connection_id group - the same rule the
    # profile and search endpoints use to identify the initiator.
    pending = Connection.objects.filter(status=False).order_by(
        "connection_id", "action_date"
    ).values_list("connection_id", "action_by_id", "involved_entity_id")

    seen_connection_ids = set()
    for connection_id, action_by_id, involved_entity_id in pending:
        if connection_id in seen_connection_ids:
            continue
        seen_connection_ids.add(connection_id)
        if (
            action_by_id
            and involved_entity_id
            and action_by_id != involved_entity_id
        ):
            pairs.add((action_by_id, involved_entity_id))

    to_create = [
        Follow(follower_id=f, followee_id=t)
        for (f, t) in pairs
        if (f, t) not in existing
    ]

    if to_create:
        # ignore_conflicts so a concurrent follow racing this migration hits
        # the (follower, followee) unique constraint harmlessly.
        Follow.objects.bulk_create(to_create, batch_size=500, ignore_conflicts=True)


def noop_reverse(apps, schema_editor):
    """
    Not reversible in a meaningful way: once backfilled, an auto-created edge
    is indistinguishable from one the user made deliberately, so removing them
    would delete real follows. Left as a no-op rather than guessing.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("entity", "0009_follow_followee"),
    ]

    operations = [
        migrations.RunPython(backfill_follows, noop_reverse),
    ]
