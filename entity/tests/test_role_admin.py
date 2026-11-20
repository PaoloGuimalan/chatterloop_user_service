from types import SimpleNamespace

from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.test import SimpleTestCase, TestCase, override_settings

from entity.models import Role


@override_settings(INSTALLED_APPS=[
    *(app for app in settings.INSTALLED_APPS if not app.startswith('django.contrib.admin')),
    'django.contrib.admin.apps.SimpleAdminConfig',
])
class RoleAdminVisibilityTests(SimpleTestCase):
    def setUp(self):
        from entity.admin import RoleAdmin
        self.admin = RoleAdmin(Role, AdminSite())
        self.request = SimpleNamespace(user=SimpleNamespace(has_perm=lambda permission: True))

    def test_role_code_is_visible_but_not_editable_in_the_admin_form(self):
        self.assertIn('code', self.admin.get_fields(self.request))
        self.assertIn('code', self.admin.get_readonly_fields(self.request))
        self.assertNotIn('code', self.admin.get_form(self.request).base_fields)
        self.assertFalse(Role._meta.get_field('code').editable)

    def test_role_admin_passes_django_checks_and_keeps_the_custom_permission_field(self):
        from django.forms import ModelMultipleChoiceField
        self.assertEqual(self.admin.check(), [])
        self.assertIn('permissions', self.admin.get_fields(self.request))
        field = self.admin.get_form(self.request).base_fields['permissions']
        self.assertIsInstance(field, ModelMultipleChoiceField)

    def test_role_list_disambiguates_role_codes_by_their_owning_realm(self):
        columns = self.admin.get_list_display(self.request)
        self.assertIn('code', columns)
        self.assertIn('realm', columns)
        self.assertIn('realm', self.admin.get_list_select_related(self.request))

    def test_realm_types_are_readable_in_admin_and_catalog_uses_normal_multiselect(self):
        from community.admin import TypeAdmin
        from community.models import Type
        from entity.admin import PermissionCatalogAdmin
        from entity.models import PermissionCatalogEntry
        from django.forms import ModelMultipleChoiceField
        types_admin = TypeAdmin(Type, AdminSite())
        self.assertEqual(types_admin.label(Type(code='community')), 'Community')
        self.assertFalse(types_admin.has_add_permission(self.request))
        self.assertFalse(types_admin.has_delete_permission(self.request))
        catalog_admin = PermissionCatalogAdmin(PermissionCatalogEntry, AdminSite())
        field = catalog_admin.get_form(self.request).base_fields['realm_types']
        self.assertIsInstance(field, ModelMultipleChoiceField)
        self.assertIs(field.queryset.model, Type)


@override_settings(INSTALLED_APPS=[
    *(app for app in settings.INSTALLED_APPS if not app.startswith('django.contrib.admin')),
    'django.contrib.admin.apps.SimpleAdminConfig',
])
class RoleAdminFormSaveTests(TestCase):
    def setUp(self):
        from unittest.mock import patch
        for name in ('_redis_get', '_redis_set', '_redis_delete'):
            self.enterContext(patch('entity.services.permission_catalog_cache.' + name, return_value=None))

    def test_admin_generated_form_keeps_permission_editing_and_preserves_hidden_grants(self):
        from community.models import Realm
        from entity.admin import RoleAdmin
        from entity.models import Entity, PermissionCatalogEntry
        actor = Entity.objects.create(type='user')
        realm = Realm.objects.create(type='page', name='Page', created_by=actor,
                                     entity=Entity.objects.create(type='realm'))
        admin = RoleAdmin(Role, AdminSite())
        request = SimpleNamespace(user=SimpleNamespace(has_perm=lambda permission: True))
        visible = PermissionCatalogEntry.objects.get(code='realm.member.view')
        hidden = PermissionCatalogEntry.objects.get(code='realm.post.submit')
        form = admin.get_form(request)(data={
            'realm': realm.pk, 'name': 'Contributors', 'permissions': [visible.pk]})
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertEqual(set(role.permissions.values_list('pk', flat=True)), {visible.pk})
        role.permissions.add(hidden)
        form = admin.get_form(request, obj=role)(
            data={'name': 'Editors', 'permissions': []}, instance=role)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        role.refresh_from_db()
        self.assertEqual(role.name, 'Editors')
        self.assertEqual(set(role.permissions.values_list('pk', flat=True)), {hidden.pk})
