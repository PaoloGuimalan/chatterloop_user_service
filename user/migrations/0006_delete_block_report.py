# STATE-ONLY: Block and Report moved to the entity app. The physical tables
# (user_block / user_report) are deliberately NOT dropped here - the models are
# only unregistered from this app's migration state and re-registered under
# entity/0012, which pins db_table to those same tables. No data is touched.
#
# The physical rename to entity_block / entity_report is a separate migration
# (entity/0013) so this move stays independently reversible.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0005_account_is_private"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(
                    name="Block",
                ),
                migrations.DeleteModel(
                    name="Report",
                ),
            ],
        ),
    ]
