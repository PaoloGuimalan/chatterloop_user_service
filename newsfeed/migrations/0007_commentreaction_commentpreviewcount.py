import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Reactions on comments, mirroring Reaction / PreviewCount one level down.

    CommentPreviewCount ships with its unique constraint from the start - the
    post-side table had to be deduped into one retroactively (0006), and there
    is no reason to repeat that.
    """

    dependencies = [
        ("entity", "0010_backfill_follows_from_connections"),
        ("newsfeed", "0006_previewcount_unique_post_emoji"),
    ]

    operations = [
        migrations.CreateModel(
            name="CommentReaction",
            fields=[
                ("reaction_id", models.CharField(default=uuid.uuid4, max_length=40, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("comment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reactions", to="newsfeed.comment")),
                ("emoji", models.ForeignKey(null=True, on_delete=django.db.models.deletion.DO_NOTHING, to="newsfeed.emoji")),
                ("entity", models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to="entity.entity")),
            ],
            options={
                "unique_together": {("comment", "entity")},
            },
        ),
        migrations.CreateModel(
            name="CommentPreviewCount",
            fields=[
                ("preview_id", models.CharField(default=uuid.uuid4, max_length=40, primary_key=True, serialize=False)),
                ("count", models.IntegerField(default=0)),
                ("comment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="preview", to="newsfeed.comment")),
                ("emoji", models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to="newsfeed.emoji")),
            ],
        ),
        migrations.AddConstraint(
            model_name="commentpreviewcount",
            constraint=models.UniqueConstraint(
                fields=("comment", "emoji"),
                name="unique_comment_preview_count_per_emoji",
            ),
        ),
    ]
