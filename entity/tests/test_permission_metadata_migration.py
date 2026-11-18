from contextlib import redirect_stdout
from importlib import import_module
from io import StringIO

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from entity.permissions import Permission


class PermissionMetadataMigrationTests(TransactionTestCase):
    def migrate(self, targets=None):
        executor = MigrationExecutor(connection)
        targets = targets or executor.loader.graph.leaf_nodes()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_rename_and_seed_preserve_ids_grants_custom_metadata_and_role_assignments(self):
        old = self.migrate([('entity', '0027_community_permission_catalog')])
        self.addCleanup(self.migrate)
        catalog = old.get_model('entity', 'PermissionCatalogEntry')
        built_in, _ = catalog.objects.get_or_create(codename='realm.post.submit', defaults={'scope': 'realm'})
        catalog.objects.filter(pk=built_in.pk).update(is_active=False)
        custom = catalog.objects.create(codename='custom.private.action', description='Keep this description.', scope='realm')
        entity = old.get_model('entity', 'Entity')
        actor = entity.objects.create(type='user')
        realm = old.get_model('community', 'Realm').objects.create(
            id='metadata-realm', realm_id='metadata-realm', name='Club', type='community',
            entity=entity.objects.create(type='realm'), created_by=actor)
        roles = old.get_model('entity', 'Role')
        admin = roles.objects.create(realm=realm, code='admin', name='admin', is_default=True)
        renamed = roles.objects.create(realm=realm, code='member', name='Contributors', is_default=True)
        custom_role = roles.objects.create(realm=realm, code='writers', name='writers')
        grant = old.get_model('entity', 'RolePermission').objects.create(role=admin, permission=built_in)
        member = old.get_model('community', 'Member').objects.create(entity=actor, realm=realm, role=admin, added_by=actor)
        override = old.get_model('entity', 'EntityPermission').objects.create(
            entity=actor, realm=realm, permission=built_in, effect='deny', reason='Keep this override.')
        ids = dict(catalog.objects.values_list('codename', 'pk'))
        output = StringIO()
        with redirect_stdout(output):
            current = self.migrate()
        new_catalog = current.get_model('entity', 'PermissionCatalogEntry')
        self.assertEqual(dict(new_catalog.objects.values_list('code', 'pk')), ids)
        entry = new_catalog.objects.get(pk=built_in.pk)
        self.assertFalse(entry.is_active)
        self.assertEqual(entry.name, 'Submit community posts')
        self.assertTrue(entry.description)
        self.assertEqual(new_catalog.objects.get(pk=custom.pk).description, 'Keep this description.')
        self.assertIn(custom.codename, output.getvalue())
        self.assertEqual(current.get_model('entity', 'RolePermission').objects.get(pk=grant.pk).permission_id, built_in.pk)
        self.assertEqual(current.get_model('entity', 'EntityPermission').objects.get(pk=override.pk).effect, 'deny')
        self.assertEqual(current.get_model('community', 'Member').objects.get(pk=member.pk).role_id, admin.pk)
        new_roles = current.get_model('entity', 'Role')
        self.assertEqual(new_roles.objects.get(pk=admin.pk).name, 'Admin')
        self.assertEqual(new_roles.objects.get(pk=renamed.pk).name, 'Contributors')
        self.assertEqual(new_roles.objects.get(pk=custom_role.pk).name, 'writers')
        metadata = import_module('entity.migrations.0028_permission_display_metadata').METADATA
        self.assertEqual(set(metadata), Permission.ALL)
        from types import SimpleNamespace
        module = import_module('entity.migrations.0028_permission_display_metadata')
        snapshot = list(new_catalog.objects.order_by('pk').values())
        with redirect_stdout(StringIO()):
            module.seed_metadata(current, SimpleNamespace(connection=connection))
        self.assertEqual(list(new_catalog.objects.order_by('pk').values()), snapshot)
        for code, (name, description) in metadata.items():
            self.assertTrue(name and description, code)
            self.assertNotIn('.', name, code)


class InvitationPolicyMigrationTests(TransactionTestCase):
    def test_catalog_upgrade_preserves_explicit_inviter_delivery(self):
        from datetime import timedelta
        from django.utils.timezone import now
        executor = MigrationExecutor(connection)
        before = [('entity', '0026_role_identity_scope')]
        executor.migrate(before)
        old = executor.loader.project_state(before).apps
        def restore():
            current = MigrationExecutor(connection)
            current.migrate(current.loader.graph.leaf_nodes())
        self.addCleanup(restore)
        entities = old.get_model('entity', 'Entity')
        actor = entities.objects.create(type='user')
        realm = old.get_model('community', 'Realm').objects.create(
            id='inviter-realm', realm_id='inviter-realm', name='Club', type='community',
            entity=entities.objects.create(type='realm'), created_by=actor)
        permission, _ = old.get_model('entity', 'PermissionCatalogEntry').objects.get_or_create(codename='realm.invite.create', defaults={'scope': 'realm'})
        original = old.get_model('entity', 'EntityPermission').objects.create(
            entity=actor, realm=realm, permission=permission, effect='grant', expires_at=now() + timedelta(days=1))
        after = [('entity', '0027_community_permission_catalog')]
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        current = executor.loader.project_state(after).apps
        copied = current.get_model('entity', 'EntityPermission').objects.get(
            entity_id=actor.pk, realm_id=realm.pk, permission__codename='realm.invite.publish')
        self.assertEqual(copied.effect, 'grant')
        self.assertEqual(copied.expires_at, original.expires_at)
