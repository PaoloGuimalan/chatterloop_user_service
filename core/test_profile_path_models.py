import uuid
import io
import json
from unittest.mock import patch
from django.core.management import call_command, CommandError
from django.test import TestCase
from core.profile_paths import ProfilePathError
from entity.models import Entity
from user.models import Account
from community.models import Realm
from bot.models import Bot


class ProfilePathModelTests(TestCase):
    def account(self, username="normal-person"):
        return Account.objects.create(entity=Entity.objects.create(type="user"), username=username,
                                      first_name="Test", last_name="Person", email=f"{uuid.uuid4()}@example.com")

    def test_account_rejects_reserved_creation_and_rename(self):
        with self.assertRaises(ProfilePathError):
            self.account("STUDIO")
        account = self.account()
        account.username = "settings"
        with self.assertRaises(ProfilePathError):
            account.save()
        account.refresh_from_db()
        self.assertEqual(account.username, "normal-person")

    def test_generated_username_is_validated_before_insert(self):
        with patch("user.models.random.shuffle", lambda letters: letters.__setitem__(slice(None), "studio")):
            with self.assertRaises(ProfilePathError):
                self.account("")

    def test_realm_and_bot_reject_reserved_names_on_create_and_rename(self):
        owner = self.account().entity
        realm = Realm(entity=Entity.objects.create(type="realm"), name="Page", created_by=owner, type="page", slug="studio")
        with self.assertRaises(ProfilePathError):
            realm.save()
        realm.slug = "my-studio"
        realm.save()
        realm.slug = "scenes"
        with self.assertRaises(ProfilePathError):
            realm.save()
        bot = Bot(entity=Entity.objects.create(type="bot"), name="Bot", handle="login")
        with self.assertRaises(ProfilePathError):
            bot.save()
        bot.handle = "helper"
        bot.save()
        bot.handle = "studios"
        with self.assertRaises(ProfilePathError):
            bot.save()

    def test_existing_collision_can_receive_unrelated_edits_but_new_claims_cannot(self):
        account = self.account()
        # Simulate a collision already in the database before this deployment.
        Account.objects.filter(pk=account.pk).update(username="studio")
        account.refresh_from_db()
        account.first_name = "Changed"
        account.save()
        account.refresh_from_db()
        self.assertEqual(account.first_name, "Changed")
        account.username = "studios"
        with self.assertRaises(ProfilePathError):
            account.save()

    def test_collision_audit_is_read_only_and_can_fail_a_rollout_check(self):
        account = self.account()
        Account.objects.filter(pk=account.pk).update(username="STUDIO")
        output = io.StringIO()
        call_command("audit_reserved_profile_paths", stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["collisions"][0]["entityId"], str(account.entity_id))
        with self.assertRaises(CommandError):
            call_command("audit_reserved_profile_paths", fail_on_collision=True, stdout=io.StringIO())
        account.refresh_from_db()
        self.assertEqual(account.username, "STUDIO")

