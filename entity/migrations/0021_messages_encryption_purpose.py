from django.db import migrations


def add_messages_purpose(apps, schema_editor):
    purpose = apps.get_model("entity", "EncryptionPurpose")
    purpose.objects.using(schema_editor.connection.alias).get_or_create(code="messages")


class Migration(migrations.Migration):
    dependencies = [("entity", "0020_encryption_purposes")]

    operations = [migrations.RunPython(add_messages_purpose, migrations.RunPython.noop)]
