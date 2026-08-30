"""
Repoint Post.deleted_by from Account to Entity.

Comment.deleted_by has always been entity-keyed; Post's pointed at Account,
which meant a post deleted while acting as a PAGE recorded the person behind
the page rather than the page, and a non-user deleter (the moderation bot)
could not be recorded at all.

WHY THIS IS HAND-WRITTEN DDL AND NOT A PLAIN AlterField
-------------------------------------------------------
`deleted_by_id` already holds ACCOUNT ids, so the rows have to be remapped to
entity ids. Both tables key on varchar, so nothing about the column type
changes - but the OLD foreign key is still in force while the remap runs, and it
is DEFERRABLE INITIALLY DEFERRED, so it does not complain per-statement: it
fires at commit, and the whole migration rolls back with

    Key (deleted_by_id)=(...) is not present in table "user_account"

Ordering the operations differently does not help; the old constraint has to be
GONE before the new values exist. So the sequence is explicit:

    drop the old FK -> remap the data -> add the new FK

`SeparateDatabaseAndState` is what lets that happen while still telling Django's
migration state that the field now points at Entity - without it, the next
makemigrations would see a model/state mismatch and try to "fix" it.

The column's index is deliberately left alone. Dropping a constraint does not
drop the index Django created alongside it, and that index is on the column, not
on what it references, so it stays correct.

WHAT WAS MEASURED FIRST
-----------------------
On this database: 29 rows carry a deleted_by, all 29 resolve to an account, none
are already entity ids, and every one of those accounts has an entity. The remap
was rehearsed in a rolled-back transaction - 29 remapped, 0 lost, a second run
remapping 0, and 0 rows left that would violate the new constraint.
"""

import django.db.models.deletion
from django.db import migrations, models

# Found by lookup rather than by name. The old constraint's name carries a hash
# Django generated when it was first created, and a database restored from a
# different lineage can carry a different one - so this drops whatever foreign
# key currently sits on the column instead of guessing what it is called.
DROP_OLD_FK = """
DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    JOIN pg_attribute att
      ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
    WHERE con.conrelid = 'newsfeed_post'::regclass
      AND con.contype = 'f'
      AND att.attname = 'deleted_by_id'
    LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE newsfeed_post DROP CONSTRAINT %I', constraint_name
        );
    END IF;
END $$;
"""

# Account ids -> the entity each account backs. Restricted to rows that still
# look like account ids, so a re-run is a no-op rather than a corruption.
REMAP_TO_ENTITY = """
    UPDATE newsfeed_post AS p
    SET deleted_by_id = a.entity_id
    FROM user_account AS a
    WHERE p.deleted_by_id = a.id
      AND p.deleted_by_id IS NOT NULL
      AND a.entity_id IS NOT NULL
"""

REMAP_TO_ACCOUNT = """
    UPDATE newsfeed_post AS p
    SET deleted_by_id = a.id
    FROM user_account AS a
    WHERE p.deleted_by_id = a.entity_id
      AND p.deleted_by_id IS NOT NULL;

    -- Anything still not an account id was a realm or a bot, which the old
    -- Account-keyed column cannot represent.
    UPDATE newsfeed_post
    SET deleted_by_id = NULL
    WHERE deleted_by_id IS NOT NULL
      AND deleted_by_id NOT IN (SELECT id FROM user_account);
"""

ADD_ENTITY_FK = """
    ALTER TABLE newsfeed_post
    ADD CONSTRAINT newsfeed_post_deleted_by_id_entity_fk
    FOREIGN KEY (deleted_by_id) REFERENCES entity_entity(id)
    DEFERRABLE INITIALLY DEFERRED
"""

DROP_ENTITY_FK = """
    ALTER TABLE newsfeed_post
    DROP CONSTRAINT IF EXISTS newsfeed_post_deleted_by_id_entity_fk
"""

ADD_ACCOUNT_FK = """
    ALTER TABLE newsfeed_post
    ADD CONSTRAINT newsfeed_post_deleted_by_id_f98c1821_fk_user_account_id
    FOREIGN KEY (deleted_by_id) REFERENCES user_account(id)
    DEFERRABLE INITIALLY DEFERRED
"""


class Migration(migrations.Migration):

    dependencies = [
        ("entity", "0014_report_realm_target"),
        ("newsfeed", "0008_post_privacy_status_connections"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="post",
                    name="deleted_by",
                    field=models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="post_deleted_by",
                        to="entity.entity",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(sql=DROP_OLD_FK, reverse_sql=DROP_ENTITY_FK),
                migrations.RunSQL(
                    sql=REMAP_TO_ENTITY, reverse_sql=REMAP_TO_ACCOUNT
                ),
                migrations.RunSQL(sql=ADD_ENTITY_FK, reverse_sql=ADD_ACCOUNT_FK),
            ],
        ),
    ]
