import uuid

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from entity.models import Entity
from interests.models import EntityInterest, Interest
from interests.views import EntityInterestOverrideListView, EntityInterestOverrideView
from user.models import Account


def _make_entity(entity_type="user"):
    # Entity.id defaults to uuid.uuid4 - a freshly created instance holds a
    # raw uuid.UUID object in memory until re-fetched from the DB (which
    # returns a plain string). Re-fetching here avoids comparing a UUID
    # object against a string when a view fetches request.entity fresh
    # (as it always does in production, via the auth backend).
    entity = Entity.objects.create(type=entity_type)
    return Entity.objects.get(pk=entity.pk)


def _make_account(entity):
    return Account.objects.create(
        entity=entity,
        first_name="Test",
        last_name="User",
        email=f"{uuid.uuid4()}@example.com",
        is_active=True,
        is_verified=True,
    )


class EntityInterestOverrideViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.entity = _make_entity()
        self.account = _make_account(self.entity)
        self.hiking = Interest.objects.create(name="Hiking")

    def _post(self, entity, data):
        request = self.factory.post("/api/interests/overrides/create/", data, format="json")
        force_authenticate(request, user=self.account)
        request.entity = entity
        response = EntityInterestOverrideView.as_view()(request)
        response.render()
        return response

    def _delete(self, entity, override_id):
        request = self.factory.delete(f"/api/interests/overrides/{override_id}/")
        force_authenticate(request, user=self.account)
        request.entity = entity
        response = EntityInterestOverrideView.as_view()(request, override_id=override_id)
        response.render()
        return response

    def _list(self, entity):
        request = self.factory.get("/api/interests/overrides/")
        force_authenticate(request, user=self.account)
        request.entity = entity
        response = EntityInterestOverrideListView.as_view()(request)
        response.render()
        return response

    def test_create_grant_override(self):
        response = self._post(self.entity, {"interest_id": self.hiking.id, "effect": "grant"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EntityInterest.objects.filter(entity=self.entity, interest=self.hiking, effect="grant").exists())

    def test_create_by_interest_name_gets_or_creates(self):
        response = self._post(self.entity, {"interest_name": "Camping", "effect": "deny"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Interest.objects.filter(normalized_name="camping").exists())

    def test_invalid_effect_rejected(self):
        response = self._post(self.entity, {"interest_id": self.hiking.id, "effect": "maybe"})
        self.assertEqual(response.status_code, 400)

    def test_list_only_returns_own_overrides(self):
        other_entity = _make_entity()
        EntityInterest.objects.create(entity=self.entity, interest=self.hiking, effect="grant")
        EntityInterest.objects.create(entity=other_entity, interest=self.hiking, effect="deny")

        response = self._list(self.entity)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]), 1)

    def test_delete_own_override(self):
        override = EntityInterest.objects.create(entity=self.entity, interest=self.hiking, effect="grant")
        response = self._delete(self.entity, override.id)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(EntityInterest.objects.filter(id=override.id).exists())

    def test_cannot_delete_another_entitys_override(self):
        other_entity = _make_entity()
        override = EntityInterest.objects.create(entity=other_entity, interest=self.hiking, effect="grant")
        response = self._delete(self.entity, override.id)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(EntityInterest.objects.filter(id=override.id).exists())
