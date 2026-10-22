import base64
import hashlib
import uuid

from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from entity.encryption_contract import KDF_PARAMETERS, verification_aad
from entity.encryption_serializers import CreateConfigurationSerializer, EncryptionConfigurationSerializer
from entity.models import EncryptionConfiguration, EncryptionPurpose, Entity
from entity.encryption_views import PersonalConfigurationsView
from entity.services.encryption import (
    activate_configuration, deactivate_configuration, get_personal_configurations,
    register_configuration, rename_configuration, EncryptionError,
)
from user.models import Account


def account():
    return Account.objects.create(
        entity=Entity.objects.create(type="user"), username=str(uuid.uuid4()),
        first_name="Encryption", last_name="Test", email=f"{uuid.uuid4()}@example.com",
    )


def payload(owner, scope_version):
    token = bytes(range(32))
    return {
        "id": str(uuid.uuid4()), "purpose": "diary", "name": "Personal passphrase",
        "expected_scope_version": scope_version, "format_version": 1,
        "kdf_algorithm": "argon2id", "kdf_parameters": KDF_PARAMETERS,
        "salt": base64.b64encode(bytes(16)).decode(), "cipher_algorithm": "aes-256-gcm",
        "verification_nonce": base64.b64encode(bytes(12)).decode(),
        "verification_ciphertext": base64.b64encode(bytes(32)).decode(),
        "verification_digest": hashlib.sha256(token).hexdigest(),
    }


class EncryptionPurposeTests(TestCase):
    def setUp(self):
        self.owner = account()
        EncryptionPurpose.objects.get_or_create(code="diary")

    def group(self):
        return next(group for group in get_personal_configurations(self.owner)["purposes"] if group["purpose"] == "diary")

    def register(self):
        data = payload(self.owner, self.group()["scope_version"])
        serializer = CreateConfigurationSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        group, created = register_configuration(self.owner, serializer.validated_data)
        self.assertTrue(created)
        return EncryptionConfiguration.objects.get(pk=data["id"])

    def test_registration_keeps_legacy_wire_contract_and_links_diary_model(self):
        row = self.register()
        self.assertTrue(EncryptionConfiguration._meta.get_field("purposes").many_to_many)
        self.assertEqual(list(row.purposes.values_list("code", flat=True)), ["diary"])
        serialized = EncryptionConfigurationSerializer(row).data
        self.assertEqual(serialized["purpose"], "diary")
        self.assertEqual(serialized["purposes"], ["diary"])
        self.assertEqual(serialized["verification_aad"], verification_aad(row))
        self.assertNotIn("verification_digest", serialized)

    def test_linking_another_purpose_keeps_verification_and_diary_membership(self):
        row = self.register()
        original_aad = verification_aad(row)
        purpose_model = row.purposes.model
        future = purpose_model.objects.create(code="future_feature")
        row.purposes.add(future)
        row.refresh_from_db()
        self.assertEqual(verification_aad(row), original_aad)
        self.assertEqual(set(row.purposes.values_list("code", flat=True)), {"diary", "future_feature"})
        self.assertEqual(len(self.group()["configurations"]), 1)
        # Future feature support is not advertised by adding a catalog record.
        self.assertEqual(get_personal_configurations(self.owner)["consumer_capabilities"], {"diary": {"format_version": 1}})

    def test_membership_drives_diary_listing_not_immutable_verification_context(self):
        row = self.register()
        row.purposes.clear()
        self.assertEqual(self.group()["configurations"], [])
        self.assertIn('"diary"', verification_aad(row))

    def test_existing_activation_rename_and_deactivation_work(self):
        row = self.register()
        proof = base64.b64encode(bytes(range(32))).decode()
        group = activate_configuration(self.owner, row.pk, {
            "expected_scope_version": self.group()["scope_version"], "target_verification_proof": proof,
        })
        self.assertEqual(group["active_configuration_id"], str(row.pk))
        group = rename_configuration(self.owner, row.pk, {
            "expected_scope_version": group["scope_version"], "name": "Renamed",
        })
        self.assertEqual(group["configurations"][0]["name"], "Renamed")
        group = deactivate_configuration(self.owner, row.pk, {
            "expected_scope_version": group["scope_version"], "active_verification_proof": proof,
        })
        self.assertIsNone(group["active_configuration_id"])

    def test_dynamic_catalog_codes_can_be_registered_without_a_code_change(self):
        EncryptionPurpose.objects.create(code="project.private_notes")
        group = next(item for item in get_personal_configurations(self.owner)["purposes"] if item["purpose"] == "project.private_notes")
        data = payload(self.owner, group["scope_version"])
        data["purpose"] = "project.private_notes"
        serializer = CreateConfigurationSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        registered, _ = register_configuration(self.owner, serializer.validated_data)
        self.assertEqual(registered["purpose"], "project.private_notes")
        self.assertEqual(registered["configurations"][0]["purposes"], ["project.private_notes"])
        self.assertEqual(registered["configurations"][0]["verification_purpose"], "project.private_notes")

    def test_unregistered_codes_duplicates_and_missing_origin_are_rejected(self):
        for purposes in [["unknown"], ["diary", "diary"], []]:
            data = payload(self.owner, self.group()["scope_version"])
            data["purposes"] = purposes
            self.assertFalse(CreateConfigurationSerializer(data=data).is_valid())

    def test_registration_can_link_multiple_dynamic_purposes(self):
        EncryptionPurpose.objects.create(code="project.private_notes")
        data = payload(self.owner, self.group()["scope_version"])
        data["purposes"] = ["project.private_notes", "diary"]
        serializer = CreateConfigurationSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        group, _ = register_configuration(self.owner, serializer.validated_data)
        self.assertEqual(group["configurations"][0]["purposes"], ["diary", "project.private_notes"])

    def test_only_one_configuration_can_be_active_for_an_owner(self):
        first, second = self.register(), self.register()
        first.is_active = True
        first.save(update_fields=["is_active"])
        second.is_active = True
        with self.assertRaises(IntegrityError), transaction.atomic():
            second.save(update_fields=["is_active"])

    def test_feature_get_returns_only_its_purpose_and_memberships(self):
        diary_row = self.register()
        other = self.register()
        purpose_model = other.purposes.model
        other.purposes.set([purpose_model.objects.create(code="future_feature")])
        request = APIRequestFactory().get("/personal/", {"purpose": "diary"})
        force_authenticate(request, user=self.owner)
        response = PersonalConfigurationsView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        groups = response.data["result"]["purposes"]
        self.assertEqual([group["purpose"] for group in groups], ["diary"])
        self.assertEqual([row["id"] for row in groups[0]["configurations"]], [str(diary_row.pk)])
        self.assertEqual(len(get_personal_configurations(self.owner)["purposes"]), 2)

    def test_unknown_or_empty_get_purpose_never_falls_back_to_all_configurations(self):
        self.register()
        for purpose in ["", "notes", "DIARY"]:
            with self.subTest(purpose=purpose):
                request = APIRequestFactory().get("/personal/", {"purpose": purpose})
                force_authenticate(request, user=self.owner)
                response = PersonalConfigurationsView.as_view()(request)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["code"], "unsupported_purpose")

    def test_disabled_purpose_remains_readable_but_rejects_registration_and_activation(self):
        row = self.register()
        purpose = EncryptionPurpose.objects.get(code="diary")
        purpose.is_active = False
        purpose.save(update_fields=["is_active"])
        group = self.group()
        self.assertIs(group["is_active"], False)
        self.assertEqual(group["configurations"][0]["id"], str(row.pk))
        data = payload(self.owner, group["scope_version"])
        serializer = CreateConfigurationSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with self.assertRaises(EncryptionError) as error:
            register_configuration(self.owner, serializer.validated_data)
        self.assertEqual(error.exception.code, "purpose_inactive")
        with self.assertRaises(EncryptionError) as error:
            activate_configuration(self.owner, row.pk, {
                "expected_scope_version": group["scope_version"],
                "target_verification_proof": base64.b64encode(bytes(range(32))).decode(),
            })
        self.assertEqual(error.exception.code, "purpose_inactive")

    def test_catalog_switch_invalidates_pending_confirmations(self):
        before = self.group()["scope_version"]
        EncryptionPurpose.objects.filter(pk="diary").update(is_active=False)
        self.assertNotEqual(self.group()["scope_version"], before)

    def test_cross_purpose_activation_requires_the_current_global_passphrase(self):
        first = self.register()
        proof = base64.b64encode(bytes(range(32))).decode()
        activate_configuration(self.owner, first.pk, {
            "expected_scope_version": self.group()["scope_version"], "target_verification_proof": proof,
        })
        EncryptionPurpose.objects.create(code="project.notes")
        group = get_personal_configurations(self.owner, "project.notes")["purposes"][0]
        data = payload(self.owner, group["scope_version"])
        data["purpose"] = "project.notes"
        serializer = CreateConfigurationSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        group, _ = register_configuration(self.owner, serializer.validated_data)
        activation = {"expected_scope_version": group["scope_version"], "purpose": "project.notes", "target_verification_proof": proof}
        with self.assertRaises(EncryptionError) as error:
            activate_configuration(self.owner, data["id"], activation)
        self.assertEqual(error.exception.code, "unlock_failed")
        activation["active_verification_proof"] = proof
        result = activate_configuration(self.owner, data["id"], activation)
        self.assertEqual(result["active_configuration_id"], data["id"])
        self.assertIsNone(self.group()["active_configuration_id"])
        self.assertEqual(EncryptionConfiguration.objects.filter(user=self.owner, is_active=True).count(), 1)

    def test_enabling_the_purpose_restores_existing_configuration_without_changing_crypto(self):
        row = self.register()
        aad = verification_aad(row)
        EncryptionPurpose.objects.filter(pk="diary").update(is_active=False)
        self.assertFalse(self.group()["is_active"])
        EncryptionPurpose.objects.filter(pk="diary").update(is_active=True)
        self.assertTrue(self.group()["is_active"])
        self.assertEqual(self.group()["configurations"][0]["id"], str(row.pk))
        self.assertEqual(verification_aad(row), aad)


class EncryptionPurposeMigrationTests(TransactionTestCase):
    @override_settings(MIGRATION_MODULES={})
    def test_existing_diary_configuration_and_verification_survive_migration(self):
        loader = MigrationLoader(None)
        migration = loader.get_migration("entity", "0020_encryption_purposes")
        before = loader.project_state()
        # Reconstruct the state immediately before this migration while leaving
        # other apps' current tables intact.
        before.remove_field("entity", "encryptionconfiguration", "purposes")
        before.remove_model("entity", "encryptionpurpose")
        from django.db import models
        before.rename_field("entity", "encryptionconfiguration", "verification_purpose", "purpose")
        before.alter_field("entity", "encryptionconfiguration", "purpose",
                           models.CharField(max_length=40, choices=[("diary", "diary")]), True)
        before.models["entity", "encryptionconfiguration"].options["constraints"] = [
            models.UniqueConstraint(fields=["entity", "user", "purpose"], condition=models.Q(is_active=True), name="unique_active_encryption_scope")
        ]
        before.models["entity", "encryptionconfiguration"].options["indexes"] = [
            models.Index(fields=["entity", "user", "purpose"], name="encryption_owner_purpose_idx")
        ]
        owner = account()
        with connection.schema_editor() as editor:
            migration.unapply(before, editor)
        try:
            old_model = before.apps.get_model("entity", "EncryptionConfiguration")
            values = payload(owner, "0" * 64)
            values.pop("expected_scope_version")
            row = old_model.objects.create(entity_id=owner.entity_id, user_id=owner.pk, is_active=True, **values)
            original_aad = '["chatterloop.encryption.verification",1,"%s","%s","%s","diary"]' % (row.pk, owner.entity_id, owner.pk)
        finally:
            with connection.schema_editor() as editor:
                migration.apply(before, editor)
        migrated = EncryptionConfiguration.objects.get(pk=row.pk)
        self.assertEqual(list(migrated.purposes.values_list("code", flat=True)), ["diary"])
        self.assertEqual(migrated.verification_ciphertext, values["verification_ciphertext"])
        self.assertEqual(migrated.salt, values["salt"])
        self.assertTrue(migrated.is_active)
        self.assertEqual(verification_aad(migrated), original_aad)
