from importlib import import_module, util
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.test import TestCase

from community.models import Member, Realm
from entity.models import Entity, EntityPermission, PermissionCatalogEntry
from entity.roles import get_default_role
from entity.role_templates import template_permissions
from entity.services.permission_resolver import has_permission


class LifecycleCatalogTests(TestCase):
    def seed(self):
        name = 'entity.migrations.0030_realm_lifecycle_permissions'
        self.assertIsNotNone(util.find_spec(name), 'Lifecycle permission seed is missing')
        import_module(name).seed_lifecycle_permissions(apps, SimpleNamespace(connection=connection))

    def setUp(self):
        self.actor = Entity.objects.create(type='user')

    def realm(self, kind):
        return Realm.objects.create(entity=Entity.objects.create(type='realm'),
            created_by=self.actor, name=kind, type=kind)

    def test_owner_lifecycle_is_intrinsic_even_with_deny(self):
        self.seed()
        for kind in ('community', 'page', 'server', 'group', 'conference', 'channel', 'voice'):
            with self.subTest(kind=kind):
                realm = self.realm(kind)
                Member.objects.create(entity=self.actor, realm=realm, added_by=self.actor,
                    role=get_default_role('owner', realm=realm))
                for code in ('realm.deactivate', 'realm.reactivate'):
                    permission = PermissionCatalogEntry.objects.get(code=code)
                    EntityPermission.objects.create(entity=self.actor, realm=realm,
                                                    permission=permission, effect='deny')
                    self.assertTrue(has_permission(self.actor, code, realm, fresh=True))

    def test_admin_member_templates_do_not_gain_lifecycle_powers(self):
        self.seed()
        for kind in ('community', 'page', 'server', 'group', 'conference', 'channel', 'voice'):
            realm = self.realm(kind)
            for code in ('admin', 'member'):
                self.assertFalse({'realm.deactivate', 'realm.reactivate'} & template_permissions(code, kind))
                Member.objects.filter(entity=self.actor, realm=realm).delete()
                Member.objects.create(entity=self.actor, realm=realm, added_by=self.actor,
                    role=get_default_role(code, realm=realm))
                self.assertFalse(has_permission(self.actor, 'realm.deactivate', realm, fresh=True))

    def test_catalog_seed_preserves_existing_grants(self):
        self.seed()
        realm = self.realm('community')
        role = get_default_role('member', realm=realm)
        permission = PermissionCatalogEntry.objects.get(code='realm.reactivate')
        role.permissions.add(permission)
        EntityPermission.objects.create(entity=self.actor, realm=realm, permission=permission, effect='deny')
        permission_id, grant_id = permission.pk, role.permissions.through.objects.get(role=role).pk
        self.seed()
        self.assertEqual(PermissionCatalogEntry.objects.get(code='realm.reactivate').pk, permission_id)
        self.assertEqual(role.permissions.through.objects.get(role=role).pk, grant_id)
        self.assertEqual(EntityPermission.objects.filter(entity=self.actor, permission=permission).count(), 1)
        self.assertEqual(set(permission.realm_types.values_list('code', flat=True)),
                         {'community', 'page', 'server', 'group', 'conference', 'channel', 'voice'})

    def test_seed_rejects_scope_collision_without_rewriting_existing_permission(self):
        entry = PermissionCatalogEntry.objects.create(code='realm.deactivate', scope='global',
                                                      name='Existing capability')
        with self.assertRaisesRegex(ValueError, 'scope'):
            self.seed()
        entry.refresh_from_db()
        self.assertEqual(entry.scope, 'global')
        self.assertEqual(entry.name, 'Existing capability')
        self.assertFalse(PermissionCatalogEntry.objects.filter(code='realm.reactivate').exists())

    def test_previous_warm_catalog_cannot_hide_lifecycle_permissions_after_deployment(self):
        self.seed()
        realm = self.realm('community')
        Member.objects.create(entity=self.actor, realm=realm, added_by=self.actor,
                              role=get_default_role('owner', realm=realm))
        with patch('entity.services.permission_catalog_cache._redis_get',
                   side_effect=lambda key: '{}' if key == 'permcache:catalog:v4' else None):
            try:
                allowed = has_permission(self.actor, 'realm.reactivate', realm)
            except ValueError as error:
                self.fail(f'Previously warm catalog hid the deployed permission: {error}')
            self.assertTrue(allowed)
