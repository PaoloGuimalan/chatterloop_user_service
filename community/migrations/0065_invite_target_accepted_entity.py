"""Invite target/accepted become entity-based: target_user -> target_entity,
accepted_by_user -> accepted_by_entity (FK Entity). Backfill from the old
account FKs, then drop them. created_by stays an account (audit)."""

import django.db.models.deletion
from django.db import migrations, models


BACKFILL = """
INSERT INTO entity (entity_id, entity_type, source_type, source_id, account_id, created_at, updated_at)
SELECT DISTINCT 'entity:user:' || target_user_id, 'user', 'user.account', target_user_id, target_user_id, now(), now()
FROM community_invite WHERE target_user_id IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO entity (entity_id, entity_type, source_type, source_id, account_id, created_at, updated_at)
SELECT DISTINCT 'entity:user:' || accepted_by_user_id, 'user', 'user.account', accepted_by_user_id, accepted_by_user_id, now(), now()
FROM community_invite WHERE accepted_by_user_id IS NOT NULL
ON CONFLICT DO NOTHING;

UPDATE community_invite SET target_entity_id = 'entity:user:' || target_user_id WHERE target_user_id IS NOT NULL;
UPDATE community_invite SET accepted_by_entity_id = 'entity:user:' || accepted_by_user_id WHERE accepted_by_user_id IS NOT NULL;
"""

NOOP = "SELECT 1;"


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0064_member_realmfollow_entity_only"),
        ("entity", "0002_entity_account_realm"),
    ]

    operations = [
        migrations.AddField(
            model_name="invite",
            name="target_entity",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="realm_invites_targeted",
                to="entity.entity",
            ),
        ),
        migrations.AddField(
            model_name="invite",
            name="accepted_by_entity",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="realm_invites_accepted",
                to="entity.entity",
            ),
        ),
        migrations.RunSQL(BACKFILL, reverse_sql=NOOP),
        migrations.RemoveField(model_name="invite", name="target_user"),
        migrations.RemoveField(model_name="invite", name="accepted_by_user"),
    ]
