import base64
import json
import uuid
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.db import connection, models
from django.db.migrations.loader import MigrationLoader
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.exceptions import ValidationError

from diary.models import Entry
from diary.serializers import EntrySerializer
from diary.views import DiaryCRUDView, DiaryListView
from entity.encryption_contract import KDF_PARAMETERS
from entity.models import EncryptionConfiguration, Entity
from entity.services.encryption import get_personal_configurations
from user.models import Account


class DiaryEncryptionTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.owner = self.account()
        self.configuration = self.configuration_for(self.owner)

    def account(self):
        return Account.objects.create(
            entity=Entity.objects.create(type="user"), first_name="Diary",
            last_name="Test", email=f"{uuid.uuid4()}@example.com", is_active=True,
        )

    def configuration_for(self, account, active=True):
        return EncryptionConfiguration.objects.create(
            entity=account.entity, user=account, purpose="diary", name="Everyday",
            is_active=active, format_version=1, kdf_algorithm="argon2id",
            kdf_parameters=KDF_PARAMETERS, salt=base64.b64encode(bytes(16)).decode(),
            cipher_algorithm="aes-256-gcm", verification_nonce=base64.b64encode(bytes(12)).decode(),
            verification_ciphertext=base64.b64encode(bytes(32)).decode(), verification_digest="0" * 64,
        )

    def payload(self, configuration=None):
        configuration = configuration or self.configuration
        binding = str(uuid.uuid4())
        def envelope(nonce):
            return json.dumps({"v": 1, "configuration_id": str(configuration.pk),
                               "entry_binding": binding, "nonce": base64.b64encode(bytes([nonce]) * 12).decode(),
                               "ciphertext": base64.b64encode(bytes(32)).decode()}, separators=(",", ":"))
        return {"title": envelope(1), "content": envelope(2), "entry_date": "2026-10-20",
                "is_private": True, "is_encrypted": True, "tags": [], "attachments": [], "mood": None}

    def post(self, payload):
        request = self.factory.post("/api/diary/entry/", payload, format="json")
        force_authenticate(request, user=self.owner)
        return DiaryCRUDView.as_view()(request)

    def test_encrypted_title_and_content_round_trip_without_preview_fetch(self):
        payload = self.payload()
        with patch("diary.serializers.extract_first_url_from_html", return_value=None) as extract:
            response = self.post(payload)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertIs(response.data.get("is_encrypted"), True)
            self.assertEqual(response.data["title"], payload["title"])
            self.assertEqual(response.data["content"], payload["content"])
            self.assertIsNone(response.data["link_preview"])
            extract.assert_not_called()
        saved = Entry.objects.get()
        self.assertTrue(saved.is_encrypted)
        self.assertEqual(saved.title, payload["title"])

    def test_legacy_plaintext_remains_readable_and_defaults_to_unencrypted(self):
        self.configuration.is_active = False
        self.configuration.save(update_fields=["is_active"])
        response = self.post({"title": "An older day", "content": "<p>Still readable</p>",
                              "entry_date": "2026-10-19", "is_private": True, "tags": [], "attachments": []})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIs(response.data.get("is_encrypted"), False)
        self.assertEqual(response.data["content"], "<p>Still readable</p>")

    def test_rejects_wrong_owner_inactive_and_unsupported_configuration(self):
        foreign = self.configuration_for(self.account())
        inactive = self.configuration_for(self.owner, active=False)
        unsupported = self.configuration_for(self.account())
        unsupported.user = self.owner
        unsupported.entity = self.owner.entity
        unsupported.purpose = "notes"
        unsupported.save()
        for row in [foreign, inactive, unsupported]:
            with self.subTest(configuration=str(row.pk)):
                response = self.post(self.payload(row))
                self.assertEqual(response.status_code, 400)
        self.assertEqual(Entry.objects.count(), 0)

    def test_rejects_missing_unknown_or_mixed_envelopes(self):
        for alteration in ["plaintext", "version", "binding", "configuration", "nonce", "base64", "extra", "same_nonce"]:
            payload = self.payload()
            content = json.loads(payload["content"])
            if alteration == "plaintext":
                payload["content"] = "<p>plaintext must not be stored as encrypted</p>"
            elif alteration == "version": content["v"] = 2
            elif alteration == "binding": content["entry_binding"] = str(uuid.uuid4())
            elif alteration == "configuration": content["configuration_id"] = str(uuid.uuid4())
            elif alteration == "nonce": content["nonce"] = "AA=="
            elif alteration == "base64": content["ciphertext"] = "not base64!"
            elif alteration == "extra": content["passphrase"] = "must never be accepted"
            elif alteration == "same_nonce": content["nonce"] = json.loads(payload["title"])["nonce"]
            if alteration != "plaintext": payload["content"] = json.dumps(content)
            with self.subTest(alteration=alteration):
                self.assertEqual(self.post(payload).status_code, 400)
        self.assertFalse(Entry.objects.exists())

    def test_encryption_marker_must_be_boolean_and_cannot_be_omitted_for_envelopes(self):
        for marker in ["true", 1, None, False]:
            payload = self.payload()
            payload["is_encrypted"] = marker
            with self.subTest(marker=marker):
                self.assertEqual(self.post(payload).status_code, 400)
        payload = self.payload()
        del payload["is_encrypted"]
        self.assertEqual(self.post(payload).status_code, 400)
        self.assertFalse(Entry.objects.exists())

    def test_encrypted_entries_must_be_private(self):
        payload = self.payload()
        payload["is_private"] = False
        self.assertEqual(self.post(payload).status_code, 400)
        self.assertFalse(Entry.objects.exists())

    def test_plaintext_title_retains_original_limit(self):
        self.configuration.is_active = False
        self.configuration.save(update_fields=["is_active"])
        payload = {"title": "x" * 256, "content": "body", "entry_date": "2026-10-20",
                   "is_private": True, "tags": [], "attachments": []}
        self.assertEqual(self.post(payload).status_code, 400)

    def test_active_configuration_prevents_plaintext_writes_from_old_clients(self):
        response = self.post({"title": "My private day", "content": "<p>Private thoughts</p>",
                              "entry_date": "2026-10-20", "is_private": True, "tags": [], "attachments": []})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Entry.objects.exists())

    def test_capability_is_advertised_only_with_diary_backend_support(self):
        result = get_personal_configurations(self.owner)
        self.assertEqual(result.get("consumer_capabilities"), {"diary": {"format_version": 1}})

    def test_list_and_detail_are_not_cacheable(self):
        create = self.post(self.payload())
        self.assertEqual(create.status_code, 200, create.data)
        for view, kwargs, url in [(DiaryListView, {}, "/api/diary/entries/"),
                                  (DiaryCRUDView, {"entry_id": str(create.data["id"])}, "/api/diary/entry/id/")]:
            request = self.factory.get(url)
            force_authenticate(request, user=self.owner)
            response = view.as_view()(request, **kwargs)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get("Cache-Control"), "no-store")

    def test_serializer_cannot_replace_ciphertext_with_plaintext(self):
        # A second write path must enforce the same envelope rules as the API.
        payload = self.payload()
        response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.data)
        entry = Entry.objects.get(pk=response.data["id"])
        serializer = EntrySerializer(entry, data={"content": "plaintext"}, partial=True)
        self.assertFalse(serializer.is_valid())

    def test_owner_can_still_read_entry_using_now_inactive_configuration(self):
        response = self.post(self.payload())
        self.configuration.is_active = False
        self.configuration.save(update_fields=["is_active"])
        request = self.factory.get("/api/diary/entry/id/")
        force_authenticate(request, user=self.owner)
        detail = DiaryCRUDView.as_view()(request, entry_id=str(response.data["id"]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["title"], response.data["title"])

    def test_other_users_never_receive_encrypted_fields_even_if_private_flag_is_wrong(self):
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 200)
        Entry.objects.filter(pk=response.data["id"]).update(is_private=False)
        request = self.factory.get("/api/diary/entry/id/")
        force_authenticate(request, user=self.account())
        detail = DiaryCRUDView.as_view()(request, entry_id=str(response.data["id"]))
        self.assertEqual(detail.status_code, 404)
        self.assertNotIn(response.data["title"], str(detail.data))

    def test_non_text_fields_return_validation_errors_instead_of_server_errors(self):
        for field in ["title", "content"]:
            payload = self.payload()
            payload[field] = {"unexpected": "object"}
            with self.subTest(field=field):
                self.assertEqual(self.post(payload).status_code, 400)

    def test_serializer_cannot_move_ciphertext_to_a_different_owner(self):
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 200)
        entry = Entry.objects.get(pk=response.data["id"])
        serializer = EntrySerializer(entry, data={"account": str(self.account().pk)}, partial=True)
        self.assertFalse(serializer.is_valid())

    def test_stale_plaintext_serializer_cannot_downgrade_an_entry_encrypted_concurrently(self):
        stale_entry = Entry.objects.create(account=self.owner, title="Legacy", content="Original", entry_date="2026-10-19")
        serializer = EntrySerializer(stale_entry, data={"title": "Changed", "content": "Plain edit"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        payload = self.payload()
        Entry.objects.filter(pk=stale_entry.pk).update(title=payload["title"], content=payload["content"], is_encrypted=True)
        with self.assertRaises(ValidationError):
            serializer.save()
        stale_entry.refresh_from_db()
        self.assertTrue(stale_entry.is_encrypted)
        self.assertEqual(stale_entry.content, payload["content"])


class DiaryEncryptionMigrationTests(TransactionTestCase):
    @override_settings(MIGRATION_MODULES={})
    def test_existing_titles_and_contents_survive_migration_and_remain_unencrypted(self):
        loader = MigrationLoader(None)
        migration = loader.get_migration("diary", "0003_entry_encryption")
        before = loader.project_state()
        before.remove_field("diary", "entry", "is_encrypted")
        before.alter_field("diary", "entry", "title", models.CharField(max_length=255, blank=True), True)
        owner = Account.objects.create(entity=Entity.objects.create(type="user"),
                                       first_name="Migration", last_name="Test", email="migration@example.com")
        with connection.schema_editor() as schema_editor:
            migration.unapply(before, schema_editor)
        try:
            old_entry = before.apps.get_model("diary", "Entry").objects.create(
                account_id=owner.pk, title="A day before encryption", content="<p>My original words.</p>",
                entry_date="2026-10-19",
            )
        finally:
            with connection.schema_editor() as schema_editor:
                migration.apply(before, schema_editor)
        entry = Entry.objects.get(pk=old_entry.pk)
        self.assertEqual(entry.title, "A day before encryption")
        self.assertEqual(entry.content, "<p>My original words.</p>")
        self.assertFalse(entry.is_encrypted)
