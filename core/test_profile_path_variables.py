import importlib
from types import SimpleNamespace
from unittest.mock import patch
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import OperationalError, connection
from django.test import TestCase
from core.models import Variable
from user.models import Account
from community.models import Realm
from bot.models import Bot
from entity.models import Entity
from core.profile_paths import ProfilePathError, validate_profile_path


class DynamicProfilePathTests(TestCase):
    def configure(self, paths):
        Variable.objects.update_or_create(key="reserved_profile_paths", defaults={"value": {"paths": paths}})

    def test_database_edits_add_and_remove_reservations_without_restart(self):
        self.configure([" STUDIO ", " ｌａｕｎｃｈｐａｄ "])
        with self.assertRaises(ProfilePathError):
            validate_profile_path("Launchpad")
        Variable.objects.filter(key="reserved_profile_paths").update(value={"paths": ["nextfeature"]})
        validate_profile_path("launchpad")
        validate_profile_path("studio")
        with self.assertRaises(ProfilePathError):
            validate_profile_path("nextfeature", "slug")

    def test_empty_list_is_an_explicit_override(self):
        self.configure([])
        validate_profile_path("studio")
        Variable.objects.get(key="reserved_profile_paths").full_clean()

    def test_unicode_case_variants_match_the_same_policy(self):
        self.configure(["Σ", "STRAẞE"])
        for value in ("σ", "ς", "straße", "STRASSE"):
            with self.subTest(value=value), self.assertRaises(ProfilePathError):
                validate_profile_path(value)

    def test_all_entity_model_writers_use_the_database_setting(self):
        self.configure(["launchpad"])
        entity = Entity.objects.create(type="user")
        owner = Account.objects.create(entity=entity, username="owner", first_name="Owner", email="owner@example.com")
        candidates = [
            Account(entity=entity, username="launchpad", first_name="Person", email="other@example.com"),
            Realm(entity=entity, name="Page", type="page", slug="launchpad", created_by=owner.entity),
            Bot(entity=entity, name="Bot", handle="launchpad"),
        ]
        for candidate in candidates:
            with self.subTest(model=type(candidate).__name__), self.assertRaises(ProfilePathError):
                candidate.save()

    def test_missing_malformed_and_unavailable_settings_keep_default_gate(self):
        for value in (None, {"paths": "studio"}, {"paths": ["bad/path"]}, {"paths": [42]}):
            Variable.objects.filter(key="reserved_profile_paths").delete()
            if value is not None:
                Variable.objects.create(key="reserved_profile_paths", value=value)
            with self.assertRaises(ProfilePathError):
                validate_profile_path("studio")
        with patch.object(Variable.objects, "filter", side_effect=OperationalError("offline")):
            with self.assertRaises(ProfilePathError):
                validate_profile_path("studio")

    def test_admin_model_validation_rejects_invalid_policy_but_other_variables_are_unchanged(self):
        for value in ({"paths": "studio"}, {"paths": ["."]}, {"paths": ["studi\to"]}):
            with self.assertRaises(ValidationError):
                Variable(key="reserved_profile_paths", value=value).full_clean()
        Variable(key="unrelated_setting", value={"anything": True}).full_clean()

    def test_data_migration_seeds_once_and_preserves_operator_changes(self):
        migration = importlib.import_module("core.migrations.0006_seed_reserved_profile_paths")
        migration.seed(apps, SimpleNamespace(connection=connection))
        self.assertIn("studio", Variable.objects.get(key="reserved_profile_paths").value["paths"])
        self.configure(["operator-choice"])
        migration.seed(apps, SimpleNamespace(connection=connection))
        self.assertEqual(Variable.objects.get(key="reserved_profile_paths").value, {"paths": ["operator-choice"]})
