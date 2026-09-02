# Creates entity_token - the credential a non-human entity authenticates with.
#
# The model is named Token rather than EntityToken so that Django's default
# app_model table name IS `entity_token`; 0013 established that this app does
# not pin db_table, so the model name is the only lever on the physical name.
# That name is typed by hand in the Node repo's verifier, which reads this
# table directly the same way permissionChecker.js reads
# entity_entitypermission - so it is worth having it read well.

import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entity", "0014_report_realm_target"),
        ("community", "0007_delete_realmfollow"),
    ]

    operations = [
        migrations.CreateModel(
            name="Token",
            fields=[
                (
                    "id",
                    models.CharField(
                        default=uuid.uuid4,
                        max_length=40,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True, default="")),
                ("prefix", models.CharField(db_index=True, max_length=16, unique=True)),
                ("token_hash", models.CharField(max_length=64)),
                ("scopes", models.JSONField(blank=True, default=list)),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tokens",
                        to="entity.entity",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tokens_created",
                        to="entity.entity",
                    ),
                ),
                (
                    "realm",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entity_tokens",
                        to="community.realm",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="token",
            index=models.Index(
                fields=["entity", "is_active"], name="entity_token_owner_idx"
            ),
        ),
    ]
