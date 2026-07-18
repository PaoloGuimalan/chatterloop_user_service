import uuid

from django.test import TestCase
from django_redis import get_redis_connection
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Realm
from entity.models import Entity
from entity.services.permission_catalog_cache import (
    CATALOG_CACHE_KEY,
    ENTITY_TYPE_MATRIX_CACHE_KEY,
    ROLE_MATRIX_CACHE_KEY,
)
from entity.views import MyAllowedModules
from user.models import Account


def _clear_permission_cache():
    conn = get_redis_connection("default")
    conn.delete(CATALOG_CACHE_KEY, ROLE_MATRIX_CACHE_KEY, ENTITY_TYPE_MATRIX_CACHE_KEY)


class MyAllowedModulesViewTests(TestCase):
    def setUp(self):
        _clear_permission_cache()
        self.addCleanup(_clear_permission_cache)
        self.factory = APIRequestFactory()

    def _get(self, entity, account):
        request = self.factory.get("/api/entity/me/modules")
        force_authenticate(request, user=account)
        request.entity = entity
        return MyAllowedModules.as_view()(request)

    def test_user_entity_sees_personal_modules_only(self):
        user_entity = Entity.objects.create(type="user")
        account = Account.objects.create(
            entity=user_entity,
            first_name="Test",
            last_name="User",
            email=f"{uuid.uuid4()}@example.com",
            is_active=True,
            is_verified=True,
        )
        response = self._get(user_entity, account)
        self.assertEqual(response.status_code, 200)
        result = response.data["result"]
        allowed = result["allowed_modules"]
        self.assertIn("module.diary.access", allowed)
        self.assertIn("module.poke.access", allowed)
        self.assertNotIn("module.page_dashboard.access", allowed)

        self.assertEqual(result["active_entity"]["type"], "user")
        self.assertEqual(result["active_entity"]["profile"], account.profile)
        self.assertEqual(result["personal_entity_id"], str(account.entity_id))

    def test_realm_entity_sees_page_modules_and_resolves_context(self):
        owner_entity = Entity.objects.create(type="user")
        account = Account.objects.create(
            entity=owner_entity,
            first_name="Page",
            last_name="Owner",
            email=f"{uuid.uuid4()}@example.com",
            is_active=True,
            is_verified=True,
        )
        realm_entity = Entity.objects.create(type="realm")
        realm = Realm.objects.create(
            entity=realm_entity,
            name="Neon Systems",
            created_by=owner_entity,
            type="page",
        )
        realm.slug = "neon-systems"
        realm.profile = "https://cdn.example.com/neon.png"
        realm.save()

        response = self._get(realm_entity, account)
        self.assertEqual(response.status_code, 200)
        result = response.data["result"]
        allowed = result["allowed_modules"]
        self.assertIn("module.page_dashboard.access", allowed)
        self.assertIn("module.poke.access", allowed)
        self.assertNotIn("module.diary.access", allowed)

        active_entity = result["active_entity"]
        self.assertEqual(active_entity["type"], "realm")
        self.assertEqual(active_entity["realm_type"], "page")
        self.assertEqual(active_entity["realm_id"], realm.id)
        self.assertEqual(active_entity["name"], "Neon Systems")
        self.assertEqual(active_entity["slug"], "neon-systems")
        self.assertEqual(active_entity["profile"], "https://cdn.example.com/neon.png")
        self.assertEqual(result["personal_entity_id"], str(owner_entity.id))
