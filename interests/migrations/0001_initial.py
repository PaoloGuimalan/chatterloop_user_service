import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("diary", "0001_initial"),
        ("entity", "0006_seed_entity_type_module_permissions"),
    ]

    operations = [
        # State-only: Interest is diary.Tag, renamed and moved to this app.
        # No database_operations - the physical table (diary_tag) already
        # exists exactly as declared here; this migration only changes
        # Django's bookkeeping of which app/model owns it. Deliberately
        # matches Tag's CURRENT physical shape exactly (just id/name) -
        # normalized_name/parent/created_at are added for real in
        # 0002_interest_hierarchy_and_normalization.py, not here.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="Interest",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        ("name", models.CharField(max_length=50, unique=True)),
                    ],
                    options={"db_table": "diary_tag"},
                ),
            ],
        ),
        # State-only: explicit through-model replacing the implicit M2M that
        # used to point Entry.tags at diary.Tag. db_column pins this onto
        # the exact existing physical columns of diary_entry_tags - no data
        # migration needed, Entry.tags is repointed onto this in
        # diary/migrations/0002_move_tags_to_interests.py.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="EntryTagLink",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "entry",
                            models.ForeignKey(
                                db_column="entry_id",
                                on_delete=django.db.models.deletion.CASCADE,
                                to="diary.entry",
                            ),
                        ),
                        (
                            "interest",
                            models.ForeignKey(
                                db_column="tag_id",
                                on_delete=django.db.models.deletion.CASCADE,
                                to="interests.interest",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "diary_entry_tags",
                        # Already exists physically (created by diary's
                        # original implicit-M2M migration) - declared here
                        # only so migration STATE knows about it too. Kept
                        # inside this same state-only CreateModel (not a
                        # separate real AddConstraint) specifically so
                        # nothing tries to re-create it for real.
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("entry", "interest"),
                                name="diary_entry_tags_entry_id_tag_id_b19ded5a_uniq",
                            ),
                        ],
                    },
                ),
            ],
        ),
        # Brand-new tables from here on - ordinary, real DDL.
        migrations.CreateModel(
            name="EntityInterest",
            fields=[
                (
                    "id",
                    models.CharField(
                        default=uuid.uuid4, max_length=40, primary_key=True, serialize=False
                    ),
                ),
                ("effect", models.CharField(choices=[("grant", "Grant"), ("deny", "Deny")], max_length=10)),
                ("reason", models.TextField(blank=True, default=None, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="interest_overrides_created",
                        to="entity.entity",
                    ),
                ),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="interest_overrides",
                        to="entity.entity",
                    ),
                ),
                (
                    "interest",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entity_overrides",
                        to="interests.interest",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="entityinterest",
            index=models.Index(fields=["entity", "interest"], name="interests_e_entity__ca2c89_idx"),
        ),
        migrations.AddConstraint(
            model_name="entityinterest",
            constraint=models.UniqueConstraint(
                fields=("entity", "interest"), name="unique_entity_interest_scope"
            ),
        ),
        migrations.CreateModel(
            name="EntityInterestAffinity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("score", models.FloatField(db_index=True, default=0.0)),
                ("last_bumped_at", models.DateTimeField(auto_now=True)),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="interest_affinities",
                        to="entity.entity",
                    ),
                ),
                (
                    "interest",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entity_affinities",
                        to="interests.interest",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="entityinterestaffinity",
            constraint=models.UniqueConstraint(
                fields=("entity", "interest"), name="unique_entity_interest_affinity"
            ),
        ),
        migrations.CreateModel(
            name="InterestTrendingScore",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("score", models.FloatField(db_index=True, default=0.0)),
                ("recent_activity_boost", models.FloatField(default=1.0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "interest",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="trending_score",
                        to="interests.interest",
                    ),
                ),
            ],
        ),
    ]
