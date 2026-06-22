import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("user", "0078_userconsent"),
    ]

    operations = [
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
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "blocked",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="blocked_by",
                        to="user.account",
                    ),
                ),
                (
                    "blocker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="blocks_made",
                        to="user.account",
                    ),
                ),
            ],
            options={
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
                            ("violence", "Violence or dangerous behavior"),
                            ("nudity", "Nudity or sexual content"),
                            ("csae", "Child sexual abuse or exploitation"),
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
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "reported_user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="reports_received",
                        to="user.account",
                    ),
                ),
                (
                    "reporter",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="reports_filed",
                        to="user.account",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="reports_reviewed",
                        to="user.account",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
