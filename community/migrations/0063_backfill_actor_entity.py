"""Backfill actor_entity for community membership and realm-follow rows.

Membership and follows are intrinsically performed by a human, so actor_entity
is always the user entity. Self-sufficient (ensures user entities exist first);
reverse is a no-op.
"""

from django.db import migrations


ENSURE_USER_ENTITIES = """
INSERT INTO entity (entity_id, entity_type, source_type, source_id, created_at, updated_at)
SELECT DISTINCT 'entity:user:' || ua.id, 'user', 'user.account', ua.id, now(), now()
FROM user_account ua
ON CONFLICT DO NOTHING;
"""

BACKFILL_MEMBER = """
UPDATE community_member
SET actor_entity_id = 'entity:user:' || account_id
WHERE actor_entity_id IS NULL;
"""

BACKFILL_REALMFOLLOW = """
UPDATE community_realmfollow
SET actor_entity_id = 'entity:user:' || follower_id
WHERE actor_entity_id IS NULL;
"""

NOOP = "SELECT 1;"


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0062_member_actor_entity_realmfollow_actor_entity_and_more"),
        ("entity", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(ENSURE_USER_ENTITIES, reverse_sql=NOOP),
        migrations.RunSQL(BACKFILL_MEMBER, reverse_sql=NOOP),
        migrations.RunSQL(BACKFILL_REALMFOLLOW, reverse_sql=NOOP),
    ]
