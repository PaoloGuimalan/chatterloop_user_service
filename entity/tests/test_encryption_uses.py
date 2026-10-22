import base64
from importlib import import_module

from django.apps import apps
from django.db import connection
from django.core.cache import caches
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from entity.encryption_contract import verification_aad
from entity.encryption_serializers import CreateConfigurationSerializer
from entity.models import EncryptionConfiguration, EncryptionPurpose
from entity.services.encryption import get_personal_configurations, register_configuration
from entity.tests.test_encryption_purposes import account, payload


@override_settings(
    ROOT_URLCONF="entity.encryption_urls",
    CACHES={alias: {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": alias}
            for alias in ("default", "encryption_mutations")},
)
class EncryptionUsesTests(TestCase):
    def setUp(self):
        caches["encryption_mutations"].clear()
        self.owner = account()
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        EncryptionPurpose.objects.get_or_create(code="diary")
        EncryptionPurpose.objects.get_or_create(code="messages")
        group = get_personal_configurations(self.owner, "diary")["purposes"][0]
        data = payload(self.owner, group["scope_version"])
        serializer = CreateConfigurationSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        register_configuration(self.owner, serializer.validated_data)
        self.row = EncryptionConfiguration.objects.get(pk=data["id"])
        self.proof = base64.b64encode(bytes(range(32))).decode()

    def change(self, purposes, **overrides):
        data = {"purposes": purposes, "target_verification_proof": self.proof,
                "expected_scope_version": get_personal_configurations(self.owner).get("scope_version", "0" * 64)}
        data.update(overrides)
        return self.client.post(f"/personal/{self.row.pk}/uses/", data, format="json")

    def test_add_and_remove_uses_preserves_verification_and_activation(self):
        self.row.is_active = True
        self.row.save(update_fields=["is_active"])
        original = verification_aad(self.row)
        salt, ciphertext = self.row.salt, self.row.verification_ciphertext
        response = self.change(["diary", "messages"])
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.assertEqual(set(self.row.purposes.values_list("code", flat=True)), {"diary", "messages"})
        response = self.change(["messages"])
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.row.refresh_from_db()
        self.assertTrue(self.row.is_active)
        self.assertEqual(verification_aad(self.row), original)
        self.assertEqual((self.row.salt, self.row.verification_ciphertext), (salt, ciphertext))
        self.assertEqual(self.row.revision, 3)
        self.assertEqual(get_personal_configurations(self.owner, "diary")["purposes"][0]["configurations"], [])
        self.assertNotIn("configurations", get_personal_configurations(self.owner, "diary"))

    def test_removing_all_uses_keeps_row_manageable_in_settings(self):
        self.row.is_active = True
        self.row.save(update_fields=["is_active"])
        response = self.change([])
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        settings = response.data["result"]
        self.assertEqual(settings["configurations"][0]["purposes"], [])
        self.assertEqual(settings["configurations"][0]["id"], str(self.row.pk))
        response = self.client.patch(f"/personal/{self.row.pk}/", {
            "name": "Kept for old entries", "expected_scope_version": settings["scope_version"],
        }, format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        response = self.client.post(f"/personal/{self.row.pk}/deactivate/", {
            "active_verification_proof": self.proof,
            "expected_scope_version": response.data["result"]["scope_version"],
        }, format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        self.row.refresh_from_db()
        self.assertEqual(self.row.name, "Kept for old entries")
        self.assertFalse(self.row.is_active)
        response = self.client.post(f"/personal/{self.row.pk}/activate/", {
            "target_verification_proof": self.proof,
            "expected_scope_version": response.data["result"]["scope_version"],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "unsupported_purpose")
        self.assertEqual(self.change(["messages"]).status_code, 200)

    def test_wrong_missing_proof_stale_and_foreign_requests_do_not_change_membership(self):
        response = self.change(["messages"], target_verification_proof=base64.b64encode(bytes(32)).decode())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "unlock_failed")
        response = self.change(["messages"], expected_scope_version="0" * 64)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "stale_scope")
        settings = get_personal_configurations(self.owner)
        response = self.client.post(f"/personal/{self.row.pk}/uses/", {
            "purposes": [], "expected_scope_version": settings.get("scope_version", "0" * 64),
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.client.force_authenticate(user=account())
        self.assertEqual(self.change([]).status_code, 404)
        self.assertEqual(list(self.row.purposes.values_list("code", flat=True)), ["diary"])

    def test_unknown_duplicate_and_newly_disabled_purposes_are_rejected(self):
        for purposes in [["unknown"], ["diary", "diary"]]:
            response = self.change(purposes)
            self.assertEqual(response.status_code, 400)
        EncryptionPurpose.objects.filter(pk="messages").update(is_active=False)
        response = self.change(["diary", "messages"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "purpose_inactive")
        EncryptionPurpose.objects.filter(pk="diary").update(is_active=False)
        self.assertEqual(self.change(["diary"]).status_code, 200)
        self.assertEqual(self.change([]).status_code, 200)

    def test_original_config_can_be_fetched_by_owner_after_use_removed_but_not_when_purpose_paused(self):
        self.row.purposes.clear()
        url = f"/personal/{self.row.pk}/?purpose=diary"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, getattr(response, "data", response.content))
        config = response.data["result"]["configuration"]
        self.assertEqual(config["id"], str(self.row.pk))
        self.assertEqual(config["purposes"], [])
        self.assertNotIn("verification_digest", config)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.client.get(f"/personal/{self.row.pk}/").status_code, 400)
        EncryptionPurpose.objects.filter(pk="diary").update(is_active=False)
        self.assertEqual(self.client.get(url).status_code, 409)
        self.client.force_authenticate(user=account())
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_messages_catalog_seed_creates_use_without_reenabling_a_paused_use(self):
        migration = import_module("entity.migrations.0021_messages_encryption_purpose")
        EncryptionPurpose.objects.filter(pk="messages").delete()
        migration.add_messages_purpose(apps, connection.schema_editor())
        self.assertTrue(EncryptionPurpose.objects.get(pk="messages").is_active)
        EncryptionPurpose.objects.filter(pk="messages").update(is_active=False)
        migration.add_messages_purpose(apps, connection.schema_editor())
        self.assertFalse(EncryptionPurpose.objects.get(pk="messages").is_active)


