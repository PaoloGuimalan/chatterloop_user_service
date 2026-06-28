"""Give Entity concrete account/realm links so the entity row holds the
connection to its backing record (enables clean ORM/SQL joins from the
interaction tables that now key on actor_entity)."""

import django.db.models.deletion
from django.db import migrations, models


# account_id = the user entity's account (source_id is the account uuid);
# realm_id = the realm entity's Realm.id, mapped from realm_id (source_id).
BACKFILL = """
UPDATE entity SET account_id = source_id WHERE entity_type = 'user';
UPDATE entity e SET realm_id = cr.id
FROM community_realm cr
WHERE e.entity_type = 'realm' AND cr.realm_id = e.source_id;
"""

NOOP = "SELECT 1;"


class Migration(migrations.Migration):

    dependencies = [
        ("entity", "0001_initial"),
        ("user", "0082_alter_connection_connection_id_and_more"),
        ("community", "0063_backfill_actor_entity"),
    ]

    operations = [
        migrations.AddField(
            model_name="entity",
            name="account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="entity",
                to="user.account",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="realm",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="entity",
                to="community.realm",
            ),
        ),
        migrations.RunSQL(BACKFILL, reverse_sql=NOOP),
    ]
