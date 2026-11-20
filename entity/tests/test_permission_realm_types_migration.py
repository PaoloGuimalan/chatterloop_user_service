from contextlib import redirect_stdout
from importlib import import_module
from io import StringIO
from types import SimpleNamespace

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PermissionRealmTypesMigrationTests(TransactionTestCase):
    def migrate(self, targets=None):
        executor = MigrationExecutor(connection)
        targets = targets or executor.loader.graph.leaf_nodes()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_backfill_keeps_ids_and_relationships_and_is_idempotent(self):
        old = self.migrate([('entity', '0028_permission_display_metadata')])
        self.addCleanup(self.migrate)
        catalog = old.get_model('entity', 'PermissionCatalogEntry')
        # TransactionTestCase flushes seed data between migration tests. Build
        # this upgrade's pre-existing catalog explicitly, independent of order.
        module = import_module('entity.migrations.0029_permission_realm_types')
        for code in module.REALM_TYPES_BY_CODE:
            catalog.objects.get_or_create(code=code, defaults={'scope': 'realm'})
        catalog.objects.get_or_create(code='realm.server.create', defaults={'scope': 'global'})
        catalog.objects.filter(code='realm.identity.act').update(is_active=False)
        custom = catalog.objects.create(code='custom.workflow', scope='realm', name='Custom workflow')
        entity = old.get_model('entity', 'Entity')
        actor = entity.objects.create(type='user')
        realm = old.get_model('community', 'Realm').objects.create(
            id='applicability-realm', realm_id='applicability-realm', name='Club', type='community',
            entity=entity.objects.create(type='realm'), created_by=actor)
        role = old.get_model('entity', 'Role').objects.create(realm=realm, code='admin', name='Admin', is_default=True)
        grant = old.get_model('entity', 'RolePermission').objects.create(role=role, permission=custom)
        member = old.get_model('community', 'Member').objects.create(realm=realm, entity=actor, role=role, added_by=actor)
        override = old.get_model('entity', 'EntityPermission').objects.create(
            realm=realm, entity=actor, permission=custom, effect='deny')
        ids = dict(catalog.objects.values_list('code', 'pk'))
        with redirect_stdout(StringIO()):
            current = self.migrate()
        new_catalog = current.get_model('entity', 'PermissionCatalogEntry')
        self.assertEqual(dict(new_catalog.objects.values_list('code', 'pk')), ids)
        self.assertEqual(list(new_catalog.objects.get(code='realm.identity.act').realm_types.values_list('code', flat=True)), ['page'])
        self.assertFalse(new_catalog.objects.get(code='realm.identity.act').is_active)
        self.assertEqual(list(new_catalog.objects.get(code='realm.server.create').realm_types.values_list('code', flat=True)), [])
        self.assertEqual(list(new_catalog.objects.get(pk=custom.pk).realm_types.values_list('code', flat=True)),
                         ['channel', 'community', 'conference', 'group', 'page', 'server', 'voice'])
        self.assertEqual(current.get_model('entity', 'RolePermission').objects.get(pk=grant.pk).permission_id, custom.pk)
        self.assertEqual(current.get_model('entity', 'EntityPermission').objects.get(pk=override.pk).effect, 'deny')
        self.assertEqual(current.get_model('community', 'Member').objects.get(pk=member.pk).role_id, role.pk)
        module = import_module('entity.migrations.0029_permission_realm_types')
        self.assertEqual(set(module.REALM_TYPES_BY_CODE), set(new_catalog.objects.filter(
            scope='realm').exclude(code='custom.workflow').values_list('code', flat=True)))
        self.assertEqual(len(module.REALM_TYPES_BY_CODE), 25)
        snapshot = list(new_catalog.objects.order_by('pk').values())
        through = new_catalog._meta.get_field('realm_types').remote_field.through
        links = list(through.objects.order_by('pk').values())
        with redirect_stdout(StringIO()):
            module.seed_realm_types(current, SimpleNamespace(connection=connection))
        self.assertEqual(list(new_catalog.objects.order_by('pk').values()), snapshot)
        self.assertEqual(list(through.objects.order_by('pk').values()), links)
        # Restoring the reference seed also makes repeat upgrades safe after an
        # isolated migration-test flush or an incomplete reference-data restore.
        through.objects.all().delete()
        current.get_model('community', 'Type').objects.all().delete()
        with redirect_stdout(StringIO()):
            module.seed_realm_types(current, SimpleNamespace(connection=connection))
        self.assertEqual(set(current.get_model('community', 'Type').objects.values_list('code', flat=True)),
                         set(module.ALL_TYPES))
        self.assertEqual(list(new_catalog.objects.order_by('pk').values()), snapshot)
        self.assertEqual(set(new_catalog.objects.get(pk=custom.pk).realm_types.values_list('code', flat=True)),
                         set(module.ALL_TYPES))
