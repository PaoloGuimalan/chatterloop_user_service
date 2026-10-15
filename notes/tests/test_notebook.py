"""Notebook contracts without connecting to SQL or upload services."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notes.tests.settings")
import django
django.setup()

from django.http import Http404
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate
from entity.models import Entity
from notes.models import Entry
from notes.serializers import NoteReadSerializer, NoteWriteSerializer
from notes.services import notes as service


class NotebookValidationTests(unittest.TestCase):
    def test_create_accepts_either_visible_field_and_sanitizes_body(self):
        for data in ({"title": "Plan"}, {"content": "<p>Text</p>"},
                     {"title": "", "content": "Text"},
                     {"title": "Plan", "content": "<script>evil()</script><p><br></p>"}):
            with self.subTest(data=data):
                serializer = NoteWriteSerializer(data=data)
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertNotIn("evil", serializer.validated_data.get("content", ""))

    def test_create_rejects_combined_invisible_content(self):
        for data in ({}, {"title": "", "content": ""},
                     {"title": " \u200b", "content": "<p>&nbsp;<br></p>"},
                     {"content": "<script>evil()</script>"}):
            with self.subTest(data=data):
                self.assertFalse(NoteWriteSerializer(data=data).is_valid())

    def update(self, note, changes, expected_version=1):
        with patch("notes.services.notes.get_object_or_404", return_value=note), \
             patch.object(Entry, "save") as save:
            result = service.update_note.__wrapped__(
                note_id=note.id, entity=Entity(id="owner"), expected_version=expected_version,
                changes=changes, actor=SimpleNamespace(id="actor"))
            save.assert_called_once()
            return result

    def test_patch_accepts_blank_body_with_retained_title(self):
        note = Entry(title="Plan", content="<p>Text</p>", search_text="Text")
        updated = self.update(note, {"content": "<p><br></p>"})
        self.assertEqual(updated.title, "Plan")
        self.assertEqual(updated.search_text, "")
        self.assertEqual(updated.version, 2)

    def test_patch_accepts_blank_title_with_retained_body(self):
        note = Entry(title="Plan", content="<p>Text</p>", search_text="Text")
        updated = self.update(note, {"title": ""})
        self.assertEqual((updated.title, updated.content), ("", "<p>Text</p>"))

    def test_patch_rejects_empty_merged_note_before_mutating_or_attachments(self):
        for note, changes in ((Entry(title="Plan", content=""), {"title": ""}),
                              (Entry(title="", content="Text"), {"content": "<p><br></p>"})):
            with self.subTest(changes=changes), \
                 patch("notes.services.notes.get_object_or_404", return_value=note), \
                 patch.object(Entry, "save") as save, \
                 patch("notes.services.attachments.replace_note_attachments") as attachments:
                original = (note.title, note.content, note.version)
                with self.assertRaises(ValidationError):
                    service.update_note.__wrapped__(note_id=note.id, entity=Entity(id="owner"),
                        expected_version=1, changes={**changes, "attachments": []}, actor=SimpleNamespace(id="actor"))
                self.assertEqual((note.title, note.content, note.version), original)
                save.assert_not_called()
                attachments.assert_not_called()

    def test_stale_patch_is_a_conflict_even_if_payload_would_empty_the_note(self):
        note = Entry(title="Plan", content="", version=2)
        with self.assertRaises(service.VersionConflict):
            self.update(note, {"title": ""}, expected_version=1)

    def test_content_patch_preserves_sort_and_attachment_handling(self):
        note = Entry(title="Plan", content="Text", sort=9)
        actor = SimpleNamespace(id="actor")
        with patch("notes.services.notes.get_object_or_404", return_value=note), \
             patch.object(Entry, "save") as save, \
             patch("notes.services.attachments.replace_note_attachments") as attachments:
            service.update_note.__wrapped__(note_id=note.id, entity=Entity(id="owner"),
                expected_version=1, changes={"title": "Updated", "attachments": []}, actor=actor)
            self.assertEqual((note.sort, note.version), (9, 2))
            self.assertNotIn("sort", save.call_args.kwargs["update_fields"])
            attachments.assert_called_once_with(note=note, requested=[], actor=actor)

    def test_partial_serializer_defers_combined_validation_until_locked_merge(self):
        serializer = NoteWriteSerializer(data={"content": "", "version": 1}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_read_includes_sort_and_write_cannot_spoof_it(self):
        note = Entry(title="Plan", content="")
        note.sort = 7
        note._prefetched_objects_cache = {"attachments": []}
        self.assertEqual(NoteReadSerializer(note).data["sort"], 7)
        self.assertFalse(NoteWriteSerializer(data={"title": "Plan", "sort": 99}).is_valid())


class NotebookOrderingTests(unittest.TestCase):
    def test_query_uses_sort_with_stable_id_tiebreaker(self):
        # Compile SQL only, using an unconnected backend instead of the dummy
        # backend (which intentionally cannot even quote identifiers).
        from django.db.backends.sqlite3.base import DatabaseWrapper
        compiler = Entry.objects.filter(entity_id="owner").query.get_compiler(
            connection=DatabaseWrapper({"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}))
        sql, _ = compiler.as_sql()
        self.assertIn('"sort" DESC', sql)
        self.assertIn('"id" DESC', sql)

    def test_list_explicit_order_uses_sort(self):
        from notes.views import NoteListView
        from rest_framework.response import Response
        request = APIRequestFactory().get("/notes/", {"search": "Plan"})
        with patch("notes.views.require_notes_access", return_value=Entity(id="owner")), \
             patch("notes.views.Entry.objects") as objects, \
             patch("notes.views.NotePagination") as pagination:
            queryset = objects.filter.return_value.prefetch_related.return_value.filter.return_value
            pagination.return_value.paginate_queryset.return_value = []
            pagination.return_value.get_paginated_response.return_value = Response({"results": []})
            self.assertEqual(NoteListView.as_view()(request).status_code, 200)
            queryset.order_by.assert_called_once_with("-sort", "-id")

    def test_create_allocates_entity_max_plus_one_after_lock(self):
        for maximum, expected in ((None, 1), (41, 42)):
            with self.subTest(maximum=maximum), patch("entity.models.Entity.objects") as entities, \
                 patch("notes.services.notes.Entry.objects") as entries:
                events = []
                entities.select_for_update.return_value.get.side_effect = lambda **kw: events.append("entity-locked")
                def aggregate(**kwargs):
                    self.assertEqual(events, ["entity-locked"])
                    return {"max_sort": maximum}
                entries.filter.return_value.aggregate.side_effect = aggregate
                entries.create.side_effect = lambda **kwargs: Entry(**kwargs)
                note = service.create_note.__wrapped__(entity=Entity(id="owner"), title="Plan", actor=SimpleNamespace(id="actor"))
                self.assertEqual((note.sort, note.title, note.content), (expected, "Plan", ""))
                entities.select_for_update.return_value.get.assert_called_once_with(pk="owner")
                entries.filter.assert_called_once_with(entity=Entity(id="owner"))

    def reorder(self, note_id, before_id, rows):
        with patch("entity.models.Entity.objects") as entities, \
             patch("notes.services.notes.Entry.objects") as entries:
            def filter_notes(**kwargs):
                entities.select_for_update.return_value.get.assert_called_once_with(pk="owner")
                self.assertEqual(kwargs, {"entity": Entity(id="owner")})
                return entries.filter.return_value
            entries.filter.side_effect = filter_notes
            entries.filter.return_value.select_for_update.return_value.order_by.return_value = rows
            result = service.reorder_notes.__wrapped__(entity=Entity(id="owner"), note_id=note_id, before_id=before_id)
            entries.bulk_update.assert_called_once()
            written, fields = entries.bulk_update.call_args.args
            self.assertCountEqual(written, rows)
            self.assertEqual(fields, ["sort"])
            return result

    def rows(self):
        rows = [Entry(id=UUID(int=i), entity_id="owner", title=str(i), content="", version=3)
                for i in (1, 2, 3)]
        for i, note in enumerate(rows):
            note.sort = 9 - i
        return rows

    def test_reorder_before_returns_complete_order_without_content_writes(self):
        rows = self.rows()
        timestamps = [(n.updated_at, n.version) for n in rows]
        result = self.reorder(rows[2].id, rows[0].id, rows)
        self.assertEqual(result, [{"id": str(UUID(int=3)), "sort": 3},
                                  {"id": str(UUID(int=1)), "sort": 2},
                                  {"id": str(UUID(int=2)), "sort": 1}])
        self.assertEqual([(n.updated_at, n.version) for n in rows], timestamps)

    def test_reorder_null_moves_to_end_and_self_preserves_order(self):
        for before, expected in ((None, [2, 3, 1]), (UUID(int=1), [1, 2, 3])):
            with self.subTest(before=before):
                result = self.reorder(UUID(int=1), before, self.rows())
                self.assertEqual([UUID(item["id"]).int for item in result], expected)

    def test_foreign_or_missing_note_and_target_are_hidden(self):
        for note_id, before_id in ((UUID(int=4), None), (UUID(int=1), UUID(int=4))):
            with self.subTest(note_id=note_id, before_id=before_id), self.assertRaises(Http404):
                self.reorder(note_id, before_id, self.rows())

    def test_reorder_endpoint_validates_and_uses_entity_authority(self):
        from django.urls import resolve
        view = resolve("/notes/reorder/").func
        actor = SimpleNamespace(is_authenticated=True, id="actor")
        entity = Entity(id="owner")
        for payload in ({}, {"note_id": "bad", "before_id": None},
                        {"note_id": str(UUID(int=1))},
                        {"note_id": str(UUID(int=1)), "before_id": None, "sort": 7}):
            with self.subTest(payload=payload), patch("notes.views.require_notes_access", return_value=entity):
                self.assertEqual(view(APIRequestFactory().post("/notes/reorder/", payload, format="json")).status_code, 400)
        request = APIRequestFactory().post("/notes/reorder/", {"note_id": str(UUID(int=1)), "before_id": None}, format="json")
        force_authenticate(request, user=actor)
        with patch("notes.views.require_notes_access", return_value=entity) as authority, \
             patch("notes.views.reorder_notes", return_value=[{"id": str(UUID(int=1)), "sort": 1}]) as reorder:
            response = view(request)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, {"order": [{"id": str(UUID(int=1)), "sort": 1}]})
            self.assertEqual(response["Cache-Control"], "no-store")
            authority.assert_called_once()
            reorder.assert_called_once_with(entity=entity, note_id=UUID(int=1), before_id=None)
        self.assertEqual(view(APIRequestFactory().post("/notes/reorder/", {}, format="json")).status_code, 401)


class NotebookMigrationTests(unittest.TestCase):
    def test_source_migration_matches_notes_models_without_applying_schema(self):
        from importlib import import_module
        from django.db.migrations.state import ModelState, ProjectState
        initial = import_module("notes.migrations.0001_initial").Migration("0001_initial", "notes")
        migration = import_module("notes.migrations.0002_notebook_sort").Migration("0002_notebook_sort", "notes")
        state = migration.mutate_state(initial.mutate_state(ProjectState()))
        migrated = state.models[("notes", "entry")]
        model = ModelState.from_model(Entry)
        self.assertEqual(set(migrated.fields), set(model.fields))
        for name in ("title", "content", "sort"):
            self.assertEqual(migrated.fields[name].deconstruct(), model.fields[name].deconstruct())
        self.assertEqual(migrated.options, model.options)

    def test_backfill_preserves_previous_display_order_per_entity(self):
        from importlib import import_module
        migration = import_module("notes.migrations.0002_notebook_sort")
        # Hand-ordered oldest to newest; equal timestamps use ascending id,
        # so descending sort retains the old descending timestamp/id order.
        rows = [SimpleNamespace(id="a1", entity_id="a"), SimpleNamespace(id="a2", entity_id="a"),
                SimpleNamespace(id="a3", entity_id="a"), SimpleNamespace(id="b1", entity_id="b")]
        historical = Mock()
        queryset = historical.objects.using.return_value
        queryset.order_by.return_value.only.return_value.iterator.return_value = iter(rows)
        apps = Mock()
        apps.get_model.return_value = historical
        migration.backfill_sort(apps, SimpleNamespace(connection=SimpleNamespace(alias="archive")))
        apps.get_model.assert_called_once_with("notes", "Entry")
        historical.objects.using.assert_called_once_with("archive")
        queryset.order_by.assert_called_once_with("entity_id", "updated_at", "id")
        self.assertEqual([(row.id, row.sort) for row in rows], [("a1", 1), ("a2", 2), ("a3", 3), ("b1", 1)])
        queryset.bulk_update.assert_called_once_with(rows, ["sort"], batch_size=1000)
