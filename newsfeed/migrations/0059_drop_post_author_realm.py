"""Drop Post.author_realm — actor_entity is the author (the realm itself when a
realm posts). Backfill actor_entity from author_realm for any rows still missing
it (e.g. posts the Node path created before actor wiring) before dropping.
"""

from django.db import migrations


BACKFILL = """
-- Ensure a realm entity exists for every author_realm referenced.
INSERT INTO entity (entity_id, entity_type, source_type, source_id, realm_id, created_at, updated_at)
SELECT DISTINCT 'entity:realm:' || cr.realm_id, 'realm', 'community.realm', cr.realm_id, cr.id, now(), now()
FROM newsfeed_post p JOIN community_realm cr ON cr.id = p.author_realm_id
WHERE p.author_realm_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- Realm posts missing actor_entity -> the realm entity.
UPDATE newsfeed_post p
SET actor_entity_id = 'entity:realm:' || cr.realm_id,
    acted_by_user_id = COALESCE(p.acted_by_user_id, p.user_id)
FROM community_realm cr
WHERE p.author_realm_id = cr.id AND p.actor_entity_id IS NULL;

-- Remaining (user) posts missing actor_entity -> the user entity.
UPDATE newsfeed_post
SET actor_entity_id = 'entity:user:' || user_id,
    acted_by_user_id = COALESCE(acted_by_user_id, user_id)
WHERE actor_entity_id IS NULL;
"""

NOOP = "SELECT 1;"


class Migration(migrations.Migration):

    dependencies = [
        ("newsfeed", "0058_backfill_actor_entity"),
        ("entity", "0002_entity_account_realm"),
    ]

    operations = [
        migrations.RunSQL(BACKFILL, reverse_sql=NOOP),
        migrations.RemoveField(model_name="post", name="author_realm"),
    ]
