import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_policydocument"),
        ("user", "0077_alter_connection_connection_id_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserConsent",
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
                    "document_type",
                    models.CharField(
                        choices=[
                            ("terms", "Terms and Conditions"),
                            ("privacy", "Privacy Policy"),
                        ],
                        max_length=20,
                    ),
                ),
                ("version", models.CharField(max_length=50)),
                (
                    "accepted_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "ip_address",
                    models.GenericIPAddressField(blank=True, null=True),
                ),
                (
                    "user_agent",
                    models.CharField(blank=True, max_length=500, null=True),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="consents",
                        to="user.account",
                    ),
                ),
            ],
            options={
                "ordering": ["-accepted_at"],
            },
        ),
    ]
