"""Pending PostgreSQL integration checks. Run only after explicit test-schema approval.

Use Django's runner with isolated project settings; do not run this module with
the database-free unit settings. The runner creates/applies a test schema.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from entity.models import Entity
from notes.models import Entry, Attachment
from notes.views import NoteListView, NoteDetailView
from notes.services.notes import update_note, VersionConflict


class NotesPersistenceTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.entities = [Entity.objects.create(type=kind) for kind in ("user", "realm", "realm")]
        self.actor = SimpleNamespace(is_authenticated=True, id="unit-account", entity_id=self.entities[0].id)
        # Persistence tests isolate the current-membership query; test_acting
        # covers it separately, and end-to-end membership tests need Realm fixtures.
        self.authority = patch("notes.permissions.can_act_as_entity", return_value=True)
        self.authority.start()
        self.addCleanup(self.authority.stop)
        self.permission = patch("notes.permissions.has_permission", return_value=True)
        self.permission.start()
        self.addCleanup(self.permission.stop)

    def request(self, method, data=None, entity=None):
        request = getattr(self.factory, method)("/api/notes/notes/", data or {}, format="json")
        force_authenticate(request, user=self.actor)
        request.entity = entity or self.entities[0]
        return request

    def test_account_page_realm_isolation_and_public_visibility(self):
        for i, entity in enumerate(self.entities):
            Entry.objects.create(entity=entity, title=f"Plan {i}", content="<p>Text</p>", search_text="Text")
        for entity in self.entities:
            response = NoteListView.as_view()(self.request("get", entity=entity))
            self.assertEqual(response.data["count"], 1)
            self.assertEqual(response.data["results"][0]["entity_id"], entity.id)
        note = Entry.objects.filter(entity=self.entities[0]).get()
        response = NoteDetailView.as_view()(self.factory.get("/"), note_id=note.id)
        self.assertEqual(response.status_code, 404)
        note.is_private = False
        note.save()
        response = NoteDetailView.as_view()(self.factory.get("/"), note_id=note.id)
        self.assertEqual(response.status_code, 200)

    def test_create_edit_search_stale_delete_and_entity_spoofing(self):
        response = NoteListView.as_view()(self.request("post", {"title": "Plan", "content": "<p>你好</p>"}))
        self.assertEqual(response.status_code, 201)
        note_id = response.data["id"]
        edited = NoteDetailView.as_view()(self.request("patch", {"version": 1, "title": "Revised"}), note_id=note_id)
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.data["version"], 2)
        stale = NoteDetailView.as_view()(self.request("delete", {"version": 1}), note_id=note_id)
        self.assertEqual(stale.status_code, 409)
        foreign = NoteDetailView.as_view()(self.request("patch", {"version": 2, "title": "Bad"}, entity=self.entities[1]), note_id=note_id)
        self.assertEqual(foreign.status_code, 404)
        spoof = NoteListView.as_view()(self.request("post", {"title": "Plan", "content": "Text", "entity": self.entities[1].id}))
        self.assertEqual(spoof.status_code, 400)
        found = NoteListView.as_view()(self.request("get", {"search": "你好"}))
        self.assertEqual(found.data["count"], 1)
        self.assertEqual(Entry.objects.get(id=note_id).title, "Revised")

    def test_failed_attachment_update_rolls_back_content_and_version(self):
        note = Entry.objects.create(entity=self.entities[0], title="Plan", content="Text")
        response = NoteDetailView.as_view()(self.request("patch", {"version": 1, "title": "Changed", "attachments": [{"id": str(uuid.uuid4())}]}), note_id=note.id)
        self.assertEqual(response.status_code, 400)
        note.refresh_from_db()
        self.assertEqual((note.title, note.version), ("Plan", 1))

    def test_current_delete_cascades_attachments(self):
        note = Entry.objects.create(entity=self.entities[0], title="Plan", content="Text")
        attachment = Attachment.objects.create(note=note, file_id="FILE_1", file_name="Plan.pdf", file_type="application/pdf", url="https://media.example/uploads/notes/a/Plan.pdf")
        with patch("notes.services.attachments.release_attachments"):
            response = NoteDetailView.as_view()(self.request("delete", {"version": 1}), note_id=note.id)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Entry.objects.filter(id=note.id).exists())
        self.assertFalse(Attachment.objects.filter(id=attachment.id).exists())


class NotesConcurrencyTests(TransactionTestCase):
    def test_two_writers_same_version_only_one_succeeds(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks")
        entity = Entity.objects.create(type="realm")
        note = Entry.objects.create(entity=entity, title="Plan", content="Text")
        barrier = Barrier(2)

        def save(title):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                update_note(note_id=note.id, entity=entity, expected_version=1,
                            changes={"title": title}, actor=SimpleNamespace(id="actor"))
                return "saved"
            except VersionConflict:
                return "conflict"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, ["First", "Second"]))
        self.assertCountEqual(results, ["saved", "conflict"])
        note.refresh_from_db()
        self.assertEqual(note.version, 2)
