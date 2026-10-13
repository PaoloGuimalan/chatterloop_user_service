import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notes.tests.settings")
import django
django.setup()


def record(**changes):
    return SimpleNamespace(fileID="FILE_1", version=2, status="ready", purpose="notes", ownerAccount="account-1",
                           ownerEntity="entity-1", name="Plan.pdf", mime="application/pdf",
                           fileDetails={"data": "https://media.example/uploads/notes/plan.pdf"}, **changes)


class AttachmentTests(unittest.TestCase):
    def test_upload_is_resolved_by_returned_bucket_url(self):
        from notes.services.attachments import resolve_note_upload
        url = "https://media.example/uploads/notes/plan.pdf"
        with patch("notes.services.attachments.UploadedFile.objects") as objects:
            objects.return_value.first.return_value = record()
            found = resolve_note_upload(url=url, actor_account_id="account-1", entity_id="entity-1")
            self.assertEqual(found.fileID, "FILE_1")
            objects.assert_called_once_with(version=2, fileDetails__data=url)

    def test_upload_entity_account_purpose_and_state_must_match(self):
        from notes.services.attachments import resolve_note_upload
        good = record()
        for field, wrong in (("ownerAccount", "other"), ("ownerEntity", "other"), ("purpose", "diary"), ("status", "pending")):
            bad = SimpleNamespace(**{**vars(good), field: wrong})
            with self.subTest(field=field), patch("notes.services.attachments.UploadedFile.objects") as objects:
                objects.return_value.first.return_value = bad
                from rest_framework.exceptions import ValidationError
                with self.assertRaises(ValidationError):
                    resolve_note_upload(url="https://media.example/uploads/notes/plan.pdf", actor_account_id="account-1", entity_id="entity-1")
        with patch("notes.services.attachments.UploadedFile.objects") as objects:
            objects.return_value.first.return_value = good
            self.assertEqual(resolve_note_upload(url="https://media.example/uploads/notes/plan.pdf", actor_account_id="account-1", entity_id="entity-1").name, "Plan.pdf")

    def test_lookup_outage_is_retryable(self):
        from notes.services.attachments import resolve_note_upload, AttachmentUnavailable
        with patch("notes.services.attachments.UploadedFile.objects", side_effect=OSError("offline")), self.assertLogs("notes.services.attachments", level="ERROR"):
            with self.assertRaises(AttachmentUnavailable):
                resolve_note_upload(url="https://media.example/uploads/notes/plan.pdf", actor_account_id="account-1", entity_id="entity-1")

    def test_retain_another_managers_existing_attachment(self):
        from notes.services.attachments import prepare_attachments
        note = SimpleNamespace(entity_id="entity-1", attachments=Mock())
        existing = SimpleNamespace(id="a1", file_id="FILE_1", url="https://media.example/uploads/notes/plan.pdf")
        note.attachments.all.return_value = [existing]
        keep, add, remove = prepare_attachments(note=note, requested=[{"id": "a1"}], actor=SimpleNamespace(id="new-manager"))
        self.assertEqual(keep, [existing])
        self.assertEqual(add, [])
        self.assertEqual(remove, [])

    def test_existing_url_can_be_retained_by_another_manager(self):
        from notes.services.attachments import prepare_attachments
        note = SimpleNamespace(entity_id="entity-1", attachments=Mock())
        existing = SimpleNamespace(id="a1", file_id="FILE_1", url="https://media.example/uploads/notes/plan.pdf")
        note.attachments.all.return_value = [existing]
        with patch("notes.services.attachments.resolve_note_upload", side_effect=AssertionError("Retaining is not a new upload")):
            self.assertEqual(prepare_attachments(note=note, requested=[{"url": existing.url}], actor=SimpleNamespace(id="another-manager")), ([existing], [], []))

    def test_foreign_attachment_id_rejected(self):
        from notes.services.attachments import prepare_attachments
        from rest_framework.exceptions import ValidationError
        note = SimpleNamespace(entity_id="entity-1", attachments=Mock())
        note.attachments.all.return_value = []
        with self.assertRaises(ValidationError):
            prepare_attachments(note=note, requested=[{"id": "foreign"}], actor=SimpleNamespace(id="manager"))

    def test_empty_list_removes_all_and_duplicate_file_rejected(self):
        from notes.services.attachments import prepare_attachments
        from rest_framework.exceptions import ValidationError
        note = SimpleNamespace(entity_id="entity-1", attachments=Mock())
        existing = SimpleNamespace(id="a1", file_id="FILE_1", url="https://media.example/uploads/notes/plan.pdf")
        note.attachments.all.return_value = [existing]
        self.assertEqual(prepare_attachments(note=note, requested=[], actor=SimpleNamespace(id="manager")), ([], [], [existing]))
        with patch("notes.services.attachments.resolve_note_upload", return_value=record()), self.assertRaises(ValidationError):
            prepare_attachments(note=note, requested=[{"id": "a1"}, {"url": "https://media.example/uploads/notes/other.pdf"}], actor=SimpleNamespace(id="manager"))
