# Adds the "realm" target type (pages, servers, groups - anything backed by a
# community.Realm) and the two indexes the moderation queue reads on.
#
# The choices change is Python-level validation only and emits no SQL; the
# AddIndex operations are the real work here.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entity", "0013_alter_block_table_alter_report_table"),
    ]

    operations = [
        migrations.AlterField(
            model_name="report",
            name="target_type",
            field=models.CharField(
                choices=[
                    ("user", "User"),
                    ("realm", "Realm"),
                    ("post", "Post"),
                    ("comment", "Comment"),
                    ("message", "Message"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="report",
            index=models.Index(
                fields=["status", "created_at"],
                name="report_status_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="report",
            index=models.Index(
                fields=["reported_entity", "status"],
                name="report_entity_status_idx",
            ),
        ),
    ]
