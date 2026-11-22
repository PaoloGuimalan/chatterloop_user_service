"""Lifecycle catalog only: never changes existing role grants or inactive rows."""
from django.db import migrations

ALL_TYPES = ('community', 'page', 'server', 'group', 'conference', 'channel', 'voice')
PERMISSIONS = (
    ('realm.deactivate', 'Deactivate realm',
     'Deactivate this realm for everyone while preserving its data.'),
    ('realm.reactivate', 'Reactivate realm',
     'Restore a voluntarily deactivated realm without bypassing parent or expiry restrictions.'),
)


def seed_lifecycle_permissions(apps, schema_editor):
    alias = schema_editor.connection.alias
    Catalog = apps.get_model('entity', 'PermissionCatalogEntry')
    Type = apps.get_model('community', 'Type')
    existing = Catalog.objects.using(alias).filter(code__in=[row[0] for row in PERMISSIONS])
    if existing.exclude(scope='realm').exists():
        raise ValueError('Lifecycle permission code has an incompatible scope; review it before seeding.')
    kinds = [Type.objects.using(alias).get_or_create(code=kind)[0] for kind in ALL_TYPES]
    for code, name, description in PERMISSIONS:
        entry, _ = Catalog.objects.using(alias).update_or_create(code=code, defaults={
            'name': name, 'description': description, 'scope': 'realm', 'is_active': True})
        entry.realm_types.set(kinds)


class Migration(migrations.Migration):
    dependencies = [('entity', '0029_permission_realm_types'), ('community', '0016_realm_lifecycle')]
    operations = [migrations.RunPython(seed_lifecycle_permissions, migrations.RunPython.noop)]
