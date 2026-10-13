import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notes.tests.settings")
import django
django.setup()


class ActingTests(unittest.TestCase):
    def test_managed_nonpage_realms_are_switchable(self):
        from entity.services.acting import is_switchable_realm
        for kind in ("page", "server", "group", "conference", "channel", "voice"):
            self.assertTrue(is_switchable_realm(SimpleNamespace(type=kind, is_active=True)))
        self.assertFalse(is_switchable_realm(SimpleNamespace(type="server", is_active=False)))

    def test_personal_and_current_realm_manager_only(self):
        from entity.services.acting import can_act_as_entity
        account = SimpleNamespace(entity_id="personal-1")
        self.assertTrue(can_act_as_entity(account, SimpleNamespace(id="personal-1", type="user")))
        self.assertFalse(can_act_as_entity(account, SimpleNamespace(id="other-user", type="user")))
        member = Mock()
        with patch("entity.services.acting.apps.get_model", return_value=member):
            member.objects.filter.return_value.exists.return_value = True
            self.assertTrue(can_act_as_entity(account, SimpleNamespace(id="realm-1", type="realm")))
            member.objects.filter.assert_called_with(entity_id="personal-1", realm__entity_id="realm-1", realm__is_active=True, role__in=("owner", "admin"))
            member.objects.filter.return_value.exists.return_value = False
            self.assertFalse(can_act_as_entity(account, SimpleNamespace(id="realm-1", type="realm")))

    def test_removed_manager_cannot_manage_or_read_private_notes(self):
        from notes.permissions import can_manage_note, require_notes_access
        from rest_framework.exceptions import PermissionDenied
        actor = SimpleNamespace(entity_id="personal-1", is_authenticated=True)
        request = SimpleNamespace(user=actor, entity=SimpleNamespace(id="realm-1", type="realm"))
        with patch("notes.permissions.has_permission", return_value=True), patch("entity.services.acting.apps.get_model") as model:
            model.return_value.objects.filter.return_value.exists.return_value = False
            self.assertFalse(can_manage_note(request, SimpleNamespace(entity_id="realm-1")))
            with self.assertRaises(PermissionDenied):
                require_notes_access(request)
