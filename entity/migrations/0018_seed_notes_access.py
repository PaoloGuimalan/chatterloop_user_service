from django.db import migrations


def seed(apps, schema_editor):
    catalog = apps.get_model("entity", "PermissionCatalogEntry")
    defaults = apps.get_model("entity", "EntityTypeDefaultPermission")
    permission, _ = catalog.objects.update_or_create(
        codename="module.notes.access",
        defaults={"scope": "entity_type", "description": "Manage the active entity's notes", "is_active": True},
    )
    for entity_type in ("user", "realm"):
        defaults.objects.get_or_create(entity_type=entity_type, permission=permission)


def unseed(apps, schema_editor):
    apps.get_model("entity", "PermissionCatalogEntry").objects.filter(codename="module.notes.access").delete()


class Migration(migrations.Migration):
    dependencies = [("entity", "0017_token_rate_limit_int_token_rate_limit_type"), ("notes", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
