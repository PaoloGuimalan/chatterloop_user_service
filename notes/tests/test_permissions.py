import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notes.tests.settings")
import django
django.setup()
from entity.services.permission_resolver import has_permission


class ModulePermissionTests(unittest.TestCase):
    def test_account_page_realm_grants_and_bot_denial(self):
        scope = "entity.services.permission_resolver."
        with patch(scope + "get_catalog", return_value={"module.notes.access": {"scope": "entity_type", "is_active": True}}), \
             patch(scope + "get_entity_type_matrix", return_value={"user": ["module.notes.access"], "realm": ["module.notes.access"]}), \
             patch(scope + "_active_override", return_value=None):
            for kind in ("user", "realm"):
                self.assertTrue(has_permission(SimpleNamespace(type=kind), "module.notes.access"))
            self.assertFalse(has_permission(SimpleNamespace(type="bot"), "module.notes.access"))

    def test_explicit_deny_blocks_default_notes_access(self):
        scope = "entity.services.permission_resolver."
        with patch(scope + "get_catalog", return_value={"module.notes.access": {"scope": "entity_type", "is_active": True}}), \
             patch(scope + "_active_override", return_value=SimpleNamespace(effect="deny")):
            self.assertFalse(has_permission(SimpleNamespace(type="realm"), "module.notes.access"))
