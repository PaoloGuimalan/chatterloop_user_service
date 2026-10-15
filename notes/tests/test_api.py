import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notes.tests.settings")
import django
django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate
from notes.models import Entry

USER = SimpleNamespace(is_authenticated=True, id="account-1", entity_id="entity-1")


class ApiTests(unittest.TestCase):
    def test_attachment_response_returns_the_completed_upload_url(self):
        from notes.models import Attachment
        from notes.serializers import NoteAttachmentSerializer
        attachment = Attachment(note_id="11111111-1111-4111-8111-111111111111", file_id="FILE_1", file_name="Plan.pdf", file_type="application/pdf", url="https://media.example/uploads/notes/plan.pdf")
        data = NoteAttachmentSerializer(attachment).data
        self.assertEqual(data["url"], attachment.url)
        self.assertNotIn("delivery_path", data)

    def test_attachment_payload_accepts_uploaded_url_like_diary(self):
        from notes.serializers import NoteWriteSerializer
        serializer = NoteWriteSerializer(data={"title": "Plan", "content": "Text", "attachments": [{
            "url": "https://media.example/uploads/notes/plan.pdf", "file_id": "client-id",
            "file_name": "client-name", "file_type": "client-type"}]})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["attachments"], [{"url": "https://media.example/uploads/notes/plan.pdf"}])

    def test_write_payload_rejects_owner_and_tags(self):
        from notes.serializers import NoteWriteSerializer
        for field in ("entity", "tags", "mood", "entry_date", "created_at"):
            serializer = NoteWriteSerializer(data={"title": "Plan", "content": "Text", field: "x"})
            self.assertFalse(serializer.is_valid(), field)

    def test_validation_title_body_and_version(self):
        from notes.serializers import NoteWriteSerializer, VersionSerializer
        for data in ({"title": "x" * 256}, {"is_private": "false"}):
            serializer = NoteWriteSerializer(data={"title": "Plan", "content": "Text", **data})
            self.assertFalse(serializer.is_valid())
        for value in (0, -1, True, "1", 1.5):
            self.assertFalse(VersionSerializer(data={"version": value}).is_valid())

    def test_private_public_and_module_permission(self):
        from notes.permissions import can_manage_note, can_read_note
        note = Entry(entity_id="entity-1", title="Plan", content="Text")
        request = SimpleNamespace(user=USER, entity=SimpleNamespace(id="entity-1"))
        with patch("notes.permissions.has_permission", return_value=True):
            self.assertTrue(can_manage_note(request, note))
            request.entity.id = "entity-2"
            self.assertFalse(can_read_note(request, note))
            note.is_private = False
            self.assertTrue(can_read_note(request, note))
        with patch("notes.permissions.has_permission", return_value=False):
            request.entity.id = "entity-1"
            note.is_private = True
            self.assertFalse(can_read_note(request, note))

    def test_detail_public_anonymous_and_private_404(self):
        from notes.views import NoteDetailView
        note = Entry(entity_id="entity-1", title="Plan", content="<p>Text</p>", is_private=False)
        with patch("notes.views.get_object_or_404", return_value=note), patch("notes.views.NoteReadSerializer") as serializer:
            serializer.return_value.data = {"title": "Plan"}
            response = NoteDetailView.as_view()(APIRequestFactory().get("/notes/"), note_id=note.id)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Cache-Control"], "no-store")
            note.is_private = True
            response = NoteDetailView.as_view()(APIRequestFactory().get("/notes/"), note_id=note.id)
            self.assertEqual(response.status_code, 404)

    def test_stale_version_rejected_before_mutation(self):
        from notes.services.notes import check_version, VersionConflict
        note = Entry(version=3)
        with self.assertRaises(VersionConflict):
            check_version(note, 2)
        self.assertEqual(note.version, 3)
        check_version(note, 3)

    def test_list_is_scoped_to_active_entity(self):
        from notes.views import NoteListView
        request = APIRequestFactory().get("/api/notes/notes/", {"search": "Plan"})
        force_authenticate(request, user=USER)
        request.entity = SimpleNamespace(id="page-1")
        with patch("notes.views.require_notes_access", return_value=request.entity), patch("notes.views.Entry.objects") as objects, patch("notes.views.NotePagination") as pagination:
            pagination.return_value.paginate_queryset.return_value = []
            from rest_framework.response import Response
            pagination.return_value.get_paginated_response.return_value = Response({"results": []})
            response = NoteListView.as_view()(request)
            self.assertEqual(response.status_code, 200)
            objects.filter.assert_called_once_with(entity=request.entity)

