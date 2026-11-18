from types import SimpleNamespace

from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.test import SimpleTestCase, override_settings

from entity.models import Role


@override_settings(INSTALLED_APPS=[
    *settings.INSTALLED_APPS, 'django.contrib.admin.apps.SimpleAdminConfig',
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

    def test_role_list_disambiguates_role_codes_by_their_owning_realm(self):
        columns = self.admin.get_list_display(self.request)
        self.assertIn('code', columns)
        self.assertIn('realm', columns)
        self.assertIn('realm', self.admin.get_list_select_related(self.request))
