# Physical rename: user_block -> entity_block, user_report -> entity_report.
#
# table=None drops the pinned db_table from 0012 and lets Django fall back to
# its default name for this app/model, which emits an ALTER TABLE ... RENAME.
# Same shape as 0008, which renamed the Connection/Follow tables after their
# move. Nothing outside this service reads either table (the Node server has
# no block/report code path), so no cross-repo coordination is needed.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("entity", "0012_block_report"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="block",
            table=None,
        ),
        migrations.AlterModelTable(
            name="report",
            table=None,
        ),
    ]
