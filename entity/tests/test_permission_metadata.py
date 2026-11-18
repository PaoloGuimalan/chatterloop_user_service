from types import SimpleNamespace

from django.db import connection
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from community.permission_views import RealmPermissionsView
from entity.models import EncryptionPurpose, PermissionCatalogEntry
from entity.tests.test_realm_role_policy import RealmRolePolicyFixture


class PermissionMetadataSchemaTests(SimpleTestCase):
    def test_display_names_do_not_change_unrelated_encryption_purpose_labels(self):
        self.assertEqual(str(EncryptionPurpose(code='diary')), 'diary')
        self.assertEqual(str(PermissionCatalogEntry(code='realm.post.submit', name='Submit community posts')),
                         'Submit community posts')

    def test_catalog_uses_code_and_a_separate_display_name(self):
        fields = {field.name for field in PermissionCatalogEntry._meta.fields}
        self.assertIn('code', fields)
        self.assertIn('name', fields)
        self.assertNotIn('codename', fields)
        self.assertEqual(PermissionCatalogEntry._meta.get_field('code').column, 'code')


class PermissionMetadataApiTests(RealmRolePolicyFixture, TestCase):
    def test_new_active_realm_permissions_are_selectable_without_changing_client_definitions(self):
        PermissionCatalogEntry.objects.create(code='realm.poll.manage', name='Manage polls',
                                              description='Manage community polls.', scope='realm')
        PermissionCatalogEntry.objects.create(code='realm.poll.disabled', scope='realm', is_active=False)
        PermissionCatalogEntry.objects.create(code='polls.global', scope='global')
        request = APIRequestFactory().get('/permissions')
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        request.entity = self.actor
        response = RealmPermissionsView.as_view()(request, realm_id=self.realm.pk)
        catalog = {row['code']: row for row in response.data['result']['catalog']}
        self.assertTrue(catalog['realm.poll.manage']['grantable'])
        self.assertEqual(catalog['realm.poll.manage']['name'], 'Manage polls')
        self.assertNotIn('realm.poll.disabled', catalog)
        self.assertNotIn('polls.global', catalog)

    def test_catalog_and_roles_expose_names_without_changing_permission_identity(self):
        entry = self.catalog['realm.post.submit']
        entry.name = 'Submit community posts'
        entry.description = 'Submit posts for review when required.'
        entry.save()
        request = APIRequestFactory().get('/permissions')
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        request.entity = self.actor
        response = RealmPermissionsView.as_view()(request, realm_id=self.realm.pk)
        self.assertEqual(response.status_code, 200)
        data = response.data['result']
        row = next(row for row in data['catalog'] if row['permission'] == 'realm.post.submit')
        self.assertEqual(row.get('code'), 'realm.post.submit')
        self.assertEqual(row.get('name'), 'Submit community posts')
        self.assertEqual(row['description'], entry.description)
        member = next(role for role in data['roles'] if role['id'] == self.roles['member'].pk)
        self.assertIn('realm.post.submit', member['permissions'])
        self.assertIn({'code': 'realm.post.submit', 'name': entry.name,
                       'description': entry.description}, member['permission_details'])
        with connection.cursor() as cursor:
            columns = {c.name for c in connection.introspection.get_table_description(cursor, 'entity_permission_catalog')}
        self.assertIn('code', columns)
        self.assertNotIn('codename', columns)

    def test_new_realm_default_role_names_are_display_names(self):
        self.assertEqual({role.code: role.name for role in self.realm.owned_roles.filter(
            code__in=['owner', 'admin', 'member'])},
            {'owner': 'Owner', 'admin': 'Admin', 'member': 'Member'})
