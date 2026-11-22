"""Keep permanent cleanup aligned with the real model relationships."""
import json
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase


class LifecycleCleanupInventoryTests(SimpleTestCase):
    def test_every_relevant_model_reference_has_an_explicit_cleanup_policy(self):
        path = Path(__file__).resolve().parents[3] / 'worker_service/internal/services/realmlifecycle/schema_inventory.json'
        if not path.exists():
            self.skipTest('Cross-service inventory check requires the worker checkout.')
        manifest = json.loads(path.read_text(encoding='utf-8'))
        targets = {entry['target'] for entry in manifest}
        actual = set()
        for model in apps.get_models(include_auto_created=True):
            if not model._meta.managed or model._meta.proxy:
                continue
            for field in model._meta.local_fields:
                target = getattr(getattr(field, 'remote_field', None), 'model', None)
                if target and target._meta.db_table in targets:
                    actual.add((model._meta.db_table, field.column, target._meta.db_table, field.target_field.column))
        expected = {(entry['table'], entry['column'], entry['target'], entry['target_column']) for entry in manifest}
        self.assertEqual(actual, expected, 'Classify new relationships before changing permanent cleanup.')
        self.assertTrue(all(entry.get('policy') for entry in manifest))
