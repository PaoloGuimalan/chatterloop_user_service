import datetime
import uuid

import django.utils.timezone
from django.db import migrations, models


def seed_initial_policy_documents(apps, schema_editor):
    PolicyDocument = apps.get_model("core", "PolicyDocument")
    PolicyDocument.objects.create(
        document_type="terms",
        version="1.0",
        document_url="/terms.html",
        effective_date=datetime.datetime(2026, 6, 22, tzinfo=datetime.timezone.utc),
    )


def unseed_initial_policy_documents(apps, schema_editor):
    PolicyDocument = apps.get_model("core", "PolicyDocument")
    PolicyDocument.objects.filter(document_type="terms", version="1.0").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PolicyDocument",
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
                ("document_url", models.CharField(max_length=500)),
                (
                    "effective_date",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "created_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
            ],
            options={
                "ordering": ["-effective_date"],
            },
        ),
        migrations.RunPython(
            seed_initial_policy_documents, unseed_initial_policy_documents
        ),
    ]
