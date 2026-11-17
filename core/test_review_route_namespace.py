import importlib
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock

class ReviewRouteNamespaceMigrationTests(TestCase):
    def apply(self, value):
        module = importlib.import_module('core.migrations.0007_reserve_realm_review_path')
        row = SimpleNamespace(value=value, save=MagicMock())
        model = MagicMock()
        model.objects.using.return_value.select_for_update.return_value.filter.return_value.first.return_value = row
        apps = MagicMock()
        apps.get_model.return_value = model
        module.reserve_review_route(apps, SimpleNamespace(connection=SimpleNamespace(alias='test')))
        return row

    def test_adds_route_without_overwriting_operator_paths_or_other_settings(self):
        row = self.apply({'paths': ['custom', 'login'], 'version': 9, 'owner_setting': True})
        self.assertEqual(row.value, {'paths': ['custom', 'login', 'realm-review'], 'version': 9, 'owner_setting': True})
        row.save.assert_called_once_with(update_fields=['value'])

    def test_existing_normalized_reservation_is_not_duplicated(self):
        row = self.apply({'paths': ['REALM-REVIEW', 'custom']})
        row.save.assert_not_called()
