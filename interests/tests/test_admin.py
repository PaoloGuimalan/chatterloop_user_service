from django.contrib.admin.sites import AdminSite
from django.test import TestCase

from interests.admin import InterestAdmin
from interests.models import Interest


class InterestAdminDeletePermissionTests(TestCase):
    """
    Interests are shared, globally-referenced vocabulary - the admin UI is
    the only remaining place a global delete could happen (no API endpoint
    deletes Interest; diary tag removal only unlinks EntryTagLink). This
    locks that down.
    """

    def test_delete_permission_is_always_denied(self):
        admin_instance = InterestAdmin(Interest, AdminSite())
        self.assertFalse(admin_instance.has_delete_permission(request=None))

        interest = Interest.objects.create(name="Hiking")
        self.assertFalse(admin_instance.has_delete_permission(request=None, obj=interest))
