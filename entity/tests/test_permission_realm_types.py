from io import StringIO
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from entity.forms import PermissionCatalogForm
from entity.models import PermissionCatalogEntry
from entity.tests.permission_fixtures import create_permission


class PermissionApplicabilityTests(SimpleTestCase):
    def test_one_permission_can_apply_to_multiple_kinds_and_reject_other_kinds(self):
        from entity.services.permission_applicability import permission_applies
        entry = {'scope': 'realm', 'realm_types': ['page', 'server'], 'is_active': True}
        for kind, expected in [('page', True), ('server', True), ('community', False),
                               ('group', False), ('conference', False), ('channel', False), ('voice', False)]:
            with self.subTest(kind=kind):
                self.assertEqual(permission_applies(entry, SimpleNamespace(type=kind, parent_id=None)), expected)

    def test_legacy_child_groups_are_channels_and_invalid_metadata_never_allows(self):
        from entity.services.permission_applicability import effective_realm_kind, permission_applies
        realm = SimpleNamespace(type='group', parent_id='server')
        self.assertEqual(effective_realm_kind(realm), 'channel')
        self.assertTrue(permission_applies({'scope': 'realm', 'realm_types': ['channel']}, realm))
        self.assertFalse(permission_applies({'scope': 'realm', 'realm_types': ['group']}, realm))
        for values in (None, [], 'channel', ['channel', 'unknown'], [42], {}):
            self.assertFalse(permission_applies({'scope': 'realm', 'realm_types': values}, realm))
        self.assertFalse(permission_applies({'scope': 'realm'}, realm))
        self.assertFalse(permission_applies({'scope': 'realm', 'realm_types': ['page']}, None))
        for scope in ('global', 'entity_type'):
            self.assertTrue(permission_applies({'scope': scope, 'realm_types': None}, realm))



class PermissionRealmTypesFormTests(TestCase):
    def setUp(self):
        from unittest.mock import patch
        for method in ('_redis_get', '_redis_set', '_redis_delete'):
            self.enterContext(patch('entity.services.permission_catalog_cache.' + method, return_value=None))

    def test_active_realm_form_requires_valid_selections(self):
        for values in ([], 'page', ['unknown']):
            form = PermissionCatalogForm(data={'code': 'custom.test', 'scope': 'realm', 'is_active': True, 'realm_types': values})
            self.assertFalse(form.is_valid())
            self.assertIn('realm_types', form.errors)
        for scope, active in [('global', True), ('realm', False)]:
            form = PermissionCatalogForm(data={'code': 'custom.test', 'scope': scope, 'is_active': active})
            self.assertTrue(form.is_valid(), form.errors)

    def test_admin_round_trips_multiple_selections_without_changing_identity(self):
        entry = create_permission(code='custom.multiple', scope='realm', realm_types=['page'])
        form = PermissionCatalogForm(data={'code': entry.code, 'name': 'Use feature', 'scope': 'realm',
                                          'is_active': True, 'realm_types': ['server', 'page', 'page']}, instance=entry)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.pk, entry.pk)
        saved.refresh_from_db()
        self.assertEqual(list(saved.realm_types.values_list('code', flat=True)), ['page', 'server'])
        self.assertEqual({str(key) for key, _ in form.fields['realm_types'].choices},
                         {'page', 'community', 'server', 'conference', 'group', 'channel', 'voice'})

    def test_read_only_audit_reports_invalid_metadata_custom_codes_and_unknown_realm_types(self):
        from community.models import Realm
        from entity.models import Entity, Role
        actor = Entity.objects.create(type='user')
        realm = Realm.objects.create(type='unknown-kind', name='Legacy', created_by=actor,
                                     entity=Entity.objects.create(type='realm'))
        role = Role.objects.create(realm=realm, name='Legacy manager')
        entry = create_permission(code='custom.feature', scope='realm', realm_types=[])
        role.permissions.add(entry)
        snapshot = list(PermissionCatalogEntry.objects.order_by('pk').values())
        output = StringIO()
        call_command('audit_permission_realm_types', stdout=output)
        self.assertIn(entry.code, output.getvalue())
        self.assertIn('invalid', output.getvalue().lower())
        self.assertIn('Unknown realm type: ' + realm.pk, output.getvalue())
        self.assertIn('Ineffective grant: role=' + str(role.pk), output.getvalue())
        self.assertEqual(list(PermissionCatalogEntry.objects.order_by('pk').values()), snapshot)

    def test_role_admin_saves_only_visible_grants_and_retains_hidden_links(self):
        from entity.forms import RoleForm
        from community.models import Realm
        from entity.models import Entity
        actor = Entity.objects.create(type='user')
        realm = Realm.objects.create(type='page', name='Page', created_by=actor,
                                     entity=Entity.objects.create(type='realm'))
        role = realm.owned_roles.get(code='member')
        hidden = PermissionCatalogEntry.objects.get(code='realm.post.submit')
        retired = create_permission(code='custom.retired', scope='realm',
                                                        is_active=False, realm_types=['page'])
        role.permissions.add(hidden, retired)
        form = RoleForm(data={'realm': realm.pk, 'name': 'Contributors', 'permissions': []}, instance=role)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(set(role.permissions.values_list('pk', flat=True)), {hidden.pk, retired.pk})
        forbidden = RoleForm(data={'realm': realm.pk, 'name': 'Contributors', 'permissions': [hidden.pk]}, instance=role)
        self.assertFalse(forbidden.is_valid())

    def test_relation_edits_invalidate_warm_catalog_immediately_and_after_commit(self):
        from unittest.mock import patch
        from community.models import Type
        from entity.services.permission_catalog_cache import get_catalog
        entry = PermissionCatalogEntry.objects.get(code='realm.identity.act')
        cache = {}
        with patch('entity.services.permission_catalog_cache._redis_get', side_effect=cache.get), \
             patch('entity.services.permission_catalog_cache._redis_set', side_effect=cache.__setitem__), \
             patch('entity.services.permission_catalog_cache._redis_delete', side_effect=lambda key: cache.pop(key, None)):
            self.assertEqual(get_catalog()[entry.code]['realm_types'], ['page'])
            with self.captureOnCommitCallbacks(execute=True):
                entry.realm_types.set(Type.objects.filter(code__in=['page', 'server']))
                self.assertNotIn('permcache:catalog:v4', cache)
                cache['permcache:catalog:v4'] = '{}'
            self.assertEqual(get_catalog()[entry.code]['realm_types'], ['page', 'server'])
