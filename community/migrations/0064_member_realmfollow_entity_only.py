"""Member and RealmFollow become entity-only: actor_entity is the authoritative
actor (non-null, unique with realm), and the legacy account/follower FKs are
dropped. The entity table now holds the connection to the underlying account.
Data is already backfilled (community/0063), so non-null is safe.
"""

import django.db.models.deletion
from django.db import migrations, models


# Backfill any rows still missing actor_entity (e.g. memberships created by the
# Node layer after the Stage-1 backfill), ensuring the entity exists first.
ENSURE_AND_FILL = """
INSERT INTO entity (entity_id, entity_type, source_type, source_id, account_id, created_at, updated_at)
SELECT DISTINCT 'entity:user:' || cm.account_id, 'user', 'user.account', cm.account_id, cm.account_id, now(), now()
FROM community_member cm WHERE cm.account_id IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO entity (entity_id, entity_type, source_type, source_id, account_id, created_at, updated_at)
SELECT DISTINCT 'entity:user:' || rf.follower_id, 'user', 'user.account', rf.follower_id, rf.follower_id, now(), now()
FROM community_realmfollow rf WHERE rf.follower_id IS NOT NULL
ON CONFLICT DO NOTHING;

UPDATE community_member SET actor_entity_id = 'entity:user:' || account_id WHERE actor_entity_id IS NULL;
UPDATE community_realmfollow SET actor_entity_id = 'entity:user:' || follower_id WHERE actor_entity_id IS NULL;
"""

NOOP = "SELECT 1;"


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0063_backfill_actor_entity"),
        ("entity", "0002_entity_account_realm"),
    ]

    operations = [
        migrations.RunSQL(ENSURE_AND_FILL, reverse_sql=NOOP),
        migrations.AlterField(
            model_name="member",
            name="actor_entity",
            field=models.ForeignKey(
                db_index=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="memberships",
                to="entity.entity",
            ),
        ),
        migrations.AlterField(
            model_name="realmfollow",
            name="actor_entity",
            field=models.ForeignKey(
                db_index=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="realm_follows",
                to="entity.entity",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="member",
            unique_together={("actor_entity", "realm")},
        ),
        migrations.AlterUniqueTogether(
            name="realmfollow",
            unique_together={("actor_entity", "realm")},
        ),
        migrations.RemoveField(model_name="member", name="account"),
        migrations.RemoveField(model_name="realmfollow", name="follower"),
    ]
