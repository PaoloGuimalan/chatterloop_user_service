# STATE-ONLY: adopts Block and Report (from the user app) into the entity app.
# Both were already entity-keyed on every side, so this is a pure ownership
# move - database_operations is empty and each model pins db_table to its
# ORIGINAL physical table (user_block / user_report). Nothing is created,
# dropped, or copied.
#
# Field definitions here mirror the models EXACTLY as they were before the
# move. The renames to entity_block / entity_report land in 0013, and the new
# "realm" target type plus the report indexes land in 0014, so each step stays
# independently reversible.

import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entity", "0011_follow_status"),
        ("user", "0006_delete_block_report"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="Block",
                    fields=[
                        (
                            "id",
                            models.CharField(
                                default=uuid.uuid4,
                                max_length=150,
                                primary_key=True,
                                serialize=False,
                                unique=True,
                            ),
                        ),
                        (
                            "created_at",
                            models.DateTimeField(
                                default=django.utils.timezone.now
                            ),
                        ),
                        (
                            "blocked",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="blocked_by",
                                to="entity.entity",
                            ),
                        ),
                        (
                            "blocker",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="blocks_made",
                                to="entity.entity",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "user_block",
                        "unique_together": {("blocker", "blocked")},
                    },
                ),
                migrations.CreateModel(
                    name="Report",
                    fields=[
                        (
                            "id",
                            models.CharField(
                                default=uuid.uuid4,
                                max_length=150,
                                primary_key=True,
                                serialize=False,
                                unique=True,
                            ),
                        ),
                        (
                            "target_type",
                            models.CharField(
                                choices=[
                                    ("user", "User"),
                                    ("post", "Post"),
                                    ("comment", "Comment"),
                                    ("message", "Message"),
                                ],
                                max_length=20,
                            ),
                        ),
                        (
                            "target_id",
                            models.CharField(blank=True, max_length=150, null=True),
                        ),
                        (
                            "reason",
                            models.CharField(
                                choices=[
                                    ("spam", "Spam"),
                                    ("harassment", "Harassment or bullying"),
                                    ("hate_speech", "Hate speech"),
                                    (
                                        "violence",
                                        "Violence or dangerous behavior",
                                    ),
                                    ("nudity", "Nudity or sexual content"),
                                    (
                                        "csae",
                                        "Child sexual abuse or exploitation",
                                    ),
                                    ("impersonation", "Impersonation"),
                                    ("misinformation", "Misinformation"),
                                    ("other", "Other"),
                                ],
                                max_length=20,
                            ),
                        ),
                        ("description", models.TextField(blank=True, default="")),
                        (
                            "status",
                            models.CharField(
                                choices=[
                                    ("pending", "Pending"),
                                    ("reviewed", "Reviewed"),
                                    ("actioned", "Actioned"),
                                    ("dismissed", "Dismissed"),
                                ],
                                default="pending",
                                max_length=20,
                            ),
                        ),
                        (
                            "created_at",
                            models.DateTimeField(
                                default=django.utils.timezone.now
                            ),
                        ),
                        (
                            "reviewed_at",
                            models.DateTimeField(blank=True, null=True),
                        ),
                        (
                            "reported_entity",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="reports_received",
                                to="entity.entity",
                            ),
                        ),
                        (
                            "reporter",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="reports_filed",
                                to="entity.entity",
                            ),
                        ),
                        (
                            "reviewed_by",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="reports_reviewed",
                                to="entity.entity",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "user_report",
                        "ordering": ["-created_at"],
                    },
                ),
            ],
        ),
    ]
