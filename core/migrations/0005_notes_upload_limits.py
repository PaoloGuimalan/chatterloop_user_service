from django.db import migrations


def seed(apps, schema_editor):
    Variable = apps.get_model("core", "Variable")
    row, _ = Variable.objects.get_or_create(key="upload_limits", defaults={"value": {}, "description": "Upload limits by feature"})
    limits = dict(row.value) if isinstance(row.value, dict) else {}
    if "notes" not in limits:
        limits["notes"] = {"maxMB": 100, "types": ["*"]}
        row.value = limits
        row.save(update_fields=["value", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("core", "0004_update")]
    # Preserve admin-configured limits when reversing; schema rollback is not a release rollback.
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
