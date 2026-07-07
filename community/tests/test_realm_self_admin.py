import uuid

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Realm
from community.views import MyRealms, TopRealms
from entity.models import Entity
from user.models import Account


def _make_entity(entity_type="user"):
    return Entity.objects.create(type=entity_type)


def _make_account(entity):
    return Account.objects.create(
        entity=entity,
        first_name="Test",
        last_name="User",
        email=f"{uuid.uuid4()}@example.com",
        is_active=True,
        is_verified=True,
    )


def _make_page_realm(created_by):
    realm_entity = _make_entity(entity_type="realm")
    return Realm.objects.create(
        entity=realm_entity,
        name="Test Page",
        created_by=created_by,
        type="page",
    )


class RealmSelfAdminAnnotationTests(TestCase):
    """
    Once switched to act as a page, the JWT's entity claim (request.entity)
    becomes the page's own Entity - which can never appear as a Member row
    of its own realm (Member rows only ever represent personal accounts).
    MyRealms.get()/TopRealms.get()'s is_admin/is_member annotations queried
    Member directly by request.entity, so a switched page wrongly saw
    itself as neither an admin nor a member of its own realm. Fixed by also
    matching Realm.entity == request.entity.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.personal_entity = _make_entity()
        self.account = _make_account(self.personal_entity)
        self.page_realm = _make_page_realm(created_by=self.personal_entity)

    def _get(self, view_cls, entity, query=""):
        request = self.factory.get(f"/api/community/{query}")
        force_authenticate(request, user=self.account)
        request.entity = entity
        response = view_cls.as_view()(request)
        response.render()
        return response

    def test_my_realms_sees_self_as_admin_and_member_when_switched(self):
        response = self._get(MyRealms, self.page_realm.entity, query="?type=page")
        self.assertEqual(response.status_code, 200)
        matching = [
            r for r in response.data["results"] if r["id"] == self.page_realm.id
        ]
        self.assertEqual(len(matching), 1)
        self.assertTrue(matching[0]["is_admin"])
        self.assertTrue(matching[0]["is_member"])

    def test_top_realms_sees_self_as_admin_when_switched(self):
        response = self._get(TopRealms, self.page_realm.entity, query="?type=page")
        self.assertEqual(response.status_code, 200)
        matching = [
            r for r in response.data["results"] if r["id"] == self.page_realm.id
        ]
        self.assertEqual(len(matching), 1)
        self.assertTrue(matching[0]["is_admin"])

    def test_unrelated_page_not_seen_as_admin(self):
        other_page = _make_page_realm(created_by=self.personal_entity)
        response = self._get(MyRealms, self.page_realm.entity, query="?type=page")
        self.assertEqual(response.status_code, 200)
        matching = [
            r for r in response.data["results"] if r["id"] == other_page.id
        ]
        self.assertEqual(len(matching), 0)
