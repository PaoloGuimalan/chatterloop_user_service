"""Catalog fixtures with the same applicability metadata as migrated installations."""
from importlib import import_module

def create_permission(**values):
    from entity.models import PermissionCatalogEntry
    from community.models import Type
    if values.get('scope') == 'realm' and 'realm_types' not in values:
        values['realm_types'] = import_module(
            'entity.migrations.0029_permission_realm_types').REALM_TYPES_BY_CODE.get(
                values['code'], ['community'])
    kinds = values.pop('realm_types', [])
    entry = PermissionCatalogEntry.objects.create(**values)
    entry.realm_types.set([Type.objects.get_or_create(code=kind)[0] for kind in kinds])
    return entry
