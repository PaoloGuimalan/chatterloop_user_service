"""Database-free checks for the explicit per-entry encryption choice."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notes.tests.settings")
import django
django.setup()

from rest_framework.exceptions import ValidationError
from diary.encryption import validate_entry_encryption


class EncryptionChoiceTests(unittest.TestCase):
    def setUp(self):
        self.owner = SimpleNamespace(entity_id="owner")
        self.configurations = patch("diary.encryption.EncryptionConfiguration.objects.filter")
        self.query = self.configurations.start()
        self.addCleanup(self.configurations.stop)
        self.query.return_value.exists.return_value = True

    def test_explicit_plaintext_choice_allows_private_and_public_entries(self):
        for private in [True, False]:
            validate_entry_encryption(self.owner, "Title", "Body", False, private,
                                      explicit_choice=True)
        self.query.assert_not_called()

    def test_legacy_client_without_choice_is_blocked_when_active(self):
        with self.assertRaises(ValidationError):
            validate_entry_encryption(self.owner, "Title", "Body", False, True)

    def test_legacy_plaintext_still_works_without_an_active_configuration(self):
        self.query.return_value.exists.return_value = False
        validate_entry_encryption(self.owner, "Title", "Body", False, True)

    def test_explicit_choice_does_not_allow_downgrading_an_encrypted_entry(self):
        with self.assertRaises(ValidationError):
            validate_entry_encryption(self.owner, "Title", "Body", False, True,
                                      SimpleNamespace(is_encrypted=True), explicit_choice=True)

    def test_explicit_choice_keeps_plaintext_title_limit(self):
        with self.assertRaises(ValidationError):
            validate_entry_encryption(self.owner, "x" * 256, "Body", False, True,
                                      explicit_choice=True)

    def test_explicit_choice_keeps_strict_boolean_marker(self):
        for marker in ["false", 0, None]:
            with self.subTest(marker=marker), self.assertRaises(ValidationError):
                validate_entry_encryption(self.owner, "Title", "Body", marker, True,
                                          explicit_choice=True)
