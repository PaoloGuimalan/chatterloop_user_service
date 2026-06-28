"""Backfill actor_entity / acted_by_user for newsfeed interaction rows.

Self-sufficient: idempotently ensures the referenced Entity rows exist before
assigning them, so it is correct whether or not `backfill_entities` has run.
Reverse is a no-op (the deploy-last migration drops the source columns).
"""

from django.db import migrations


# Ensure a user entity exists for every account, and a realm entity for every
# realm, then point each interaction row at the right actor.
ENSURE_USER_ENTITIES = """
INSERT INTO entity (entity_id, entity_type, source_type, source_id, created_at, updated_at)
SELECT DISTINCT 'entity:user:' || ua.id, 'user', 'user.account', ua.id, now(), now()
FROM user_account ua
ON CONFLICT DO NOTHING;
"""

ENSURE_REALM_ENTITIES = """
INSERT INTO entity (entity_id, entity_type, source_type, source_id, created_at, updated_at)
SELECT DISTINCT 'entity:realm:' || cr.realm_id, 'realm', 'community.realm', cr.realm_id, now(), now()
FROM community_realm cr
ON CONFLICT DO NOTHING;
"""

# Posts: realm posts -> realm entity, else user entity; acted_by_user = author.
BACKFILL_POST = """
UPDATE newsfeed_post p
SET actor_entity_id = CASE
        WHEN p.author_realm_id IS NOT NULL
            THEN 'entity:realm:' || cr.realm_id
        ELSE 'entity:user:' || p.user_id
    END,
    acted_by_user_id = p.user_id
FROM (SELECT id, realm_id FROM community_realm) cr
WHERE (p.author_realm_id = cr.id OR p.author_realm_id IS NULL)
  AND p.actor_entity_id IS NULL;
"""

# Reactions / comments have no realm authorship yet -> user entity.
BACKFILL_REACTION = """
UPDATE newsfeed_reaction
SET actor_entity_id = 'entity:user:' || user_id,
    acted_by_user_id = user_id
WHERE actor_entity_id IS NULL;
"""

BACKFILL_COMMENT = """
UPDATE newsfeed_comment
SET actor_entity_id = 'entity:user:' || user_id,
    acted_by_user_id = user_id
WHERE actor_entity_id IS NULL;
"""

# Saves are intrinsically the human.
BACKFILL_POSTSAVE = """
UPDATE newsfeed_postsave
SET actor_entity_id = 'entity:user:' || user_id
WHERE actor_entity_id IS NULL;
"""

NOOP = "SELECT 1;"


class Migration(migrations.Migration):

    dependencies = [
        ("newsfeed", "0057_comment_acted_by_user_comment_actor_entity_and_more"),
        ("entity", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(ENSURE_USER_ENTITIES, reverse_sql=NOOP),
        migrations.RunSQL(ENSURE_REALM_ENTITIES, reverse_sql=NOOP),
        migrations.RunSQL(BACKFILL_POST, reverse_sql=NOOP),
        migrations.RunSQL(BACKFILL_REACTION, reverse_sql=NOOP),
        migrations.RunSQL(BACKFILL_COMMENT, reverse_sql=NOOP),
        migrations.RunSQL(BACKFILL_POSTSAVE, reverse_sql=NOOP),
    ]
