import datetime

from django.db import migrations


def seed_privacy_policy(apps, schema_editor):
    PolicyDocument = apps.get_model("core", "PolicyDocument")
    PolicyDocument.objects.create(
        document_type="privacy",
        version="1.0",
        document_url="/privacy.html",
        effective_date=datetime.datetime(2026, 6, 22, tzinfo=datetime.timezone.utc),
    )


def unseed_privacy_policy(apps, schema_editor):
    PolicyDocument = apps.get_model("core", "PolicyDocument")
    PolicyDocument.objects.filter(document_type="privacy", version="1.0").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_policydocument"),
    ]

    operations = [
        migrations.RunPython(seed_privacy_policy, unseed_privacy_policy),
    ]
