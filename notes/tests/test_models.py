import os
import unittest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notes.tests.settings")
import django
django.setup()

from entity.models import Entity
from entity.permissions import Permission


class ModelContractTests(unittest.TestCase):
    def test_model_names_use_the_notes_app_prefix(self):
        from notes.models import Entry, Attachment
        self.assertEqual(Entry._meta.db_table, "notes_entry")
        self.assertEqual(Attachment._meta.db_table, "notes_attachment")
        self.assertIs(Attachment._meta.get_field("note").remote_field.model, Entry)

    def test_note_uses_entity_and_private_version_defaults(self):
        from notes.models import Entry
        note = Entry(entity=Entity(id="entity-1", type="realm"), title="Plan", content="<p>Text</p>")
        self.assertEqual(note.entity_id, "entity-1")
        self.assertTrue(note.is_private)
        self.assertEqual(note.version, 1)
        self.assertNotIn("tags", {f.name for f in Entry._meta.get_fields()})

    def test_notes_module_is_discoverable(self):
        self.assertIn("module.notes.access", Permission.ENTITY_TYPE_SCOPED)

    def test_attachment_delete_follows_note(self):
        from django.db.models import CASCADE
        from notes.models import Attachment
        self.assertIs(Attachment._meta.get_field("note").remote_field.on_delete, CASCADE)

