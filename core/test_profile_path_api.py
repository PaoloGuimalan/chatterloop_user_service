"""Exercise the real view methods without starting Redis/Mongo/Cassandra clients."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from django.test import TestCase
from rest_framework.response import Response
from rest_framework import status
from core.profile_paths import ProfilePathError, validate_profile_path
from core.models import Variable


def method(relative, class_name, method_name, **context):
    tree = ast.parse((Path(__file__).parents[1] / relative).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method_name)
    scope = dict(Response=Response, status=status, ProfilePathError=ProfilePathError,
                 validate_profile_path=validate_profile_path, logger=Mock(), **context)
    exec(compile(ast.Module(body=[node], type_ignores=[]), relative, "exec"), scope)
    return scope[method_name]


class ProfilePathAPITests(TestCase):
    def test_native_account_update_uses_the_current_variable(self):
        Variable.objects.create(key="reserved_profile_paths", value={"paths": ["launchpad"]})
        account = SimpleNamespace(username="person", is_private=False)
        put = method("user/views.py", "UserAccountManagement", "put")
        response = put(SimpleNamespace(request=SimpleNamespace(user=account)), SimpleNamespace(data={"username": "LAUNCHPAD"}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "RESERVED_PATH")
        self.assertEqual(account.username, "person")

    def test_native_account_update_gets_actionable_400(self):
        account = SimpleNamespace(username="person", is_private=False)
        put = method("user/views.py", "UserAccountManagement", "put")
        response = put(SimpleNamespace(request=SimpleNamespace(user=account)),
                       SimpleNamespace(data={"username": "Studio"}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "RESERVED_PATH")
        self.assertEqual(response.data["field"], "username")
        self.assertEqual(account.username, "person")

    def test_native_realm_bulk_update_is_gated_before_update(self):
        queryset = Mock()
        realm = SimpleNamespace(slug="my-page")
        put = method("community/views.py", "MyRealms", "put", Realm=queryset,
                     get_object_or_404=lambda *a, **k: realm,
                     has_permission=lambda *a, **k: True, Permission=SimpleNamespace(REALM_UPDATE="update"))
        request = SimpleNamespace(user=object(), entity=object(), data={"realm_id": "realm", "fields": {"slug": "settings"}})
        response = put(SimpleNamespace(request=request), request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "RESERVED_PATH")
        queryset.objects.filter.assert_not_called()

