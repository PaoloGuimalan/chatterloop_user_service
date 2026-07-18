import uuid

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Realm
from entity.models import Entity
from user.models import Account
from user.views import UserAuthentication


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


def _make_page_realm(created_by, slug):
    realm_entity = _make_entity(entity_type="realm")
    return Realm.objects.create(
        entity=realm_entity,
        name="Test Page",
        created_by=created_by,
        type="page",
        slug=slug,
    )


class RealmProfileSelfAdminTests(TestCase):
    """
    UserAuthentication.get()'s realm-profile branch (used by
    ProfileContainer.tsx/RealmProfile.tsx to render the "Manage Realm"
    entry point) had the same self-realm-entity bug as MyRealms/TopRealms:
    once switched to act as a page, request.entity is the page's own
    Entity, which can never appear as a Member row of its own realm, so
    is_admin/is_member were always wrongly False when viewing your own
    page's profile while acting as it.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.personal_entity = _make_entity()
        self.account = _make_account(self.personal_entity)
        self.page_realm = _make_page_realm(
            created_by=self.personal_entity, slug="test-page-self-admin"
        )

    def _get(self, username, entity, transaction_type=None):
        query = f"?type={transaction_type}" if transaction_type else ""
        request = self.factory.get(f"/api/user/{username}{query}")
        force_authenticate(request, user=self.account)
        request.entity = entity
        response = UserAuthentication.as_view()(request, username=username)
        response.render()
        return response

    def test_own_page_profile_shows_is_admin_when_switched(self):
        response = self._get(self.page_realm.slug, self.page_realm.entity)
        self.assertEqual(response.status_code, 200)
        result = response.data["data"]
        self.assertTrue(result["is_admin"])
        self.assertTrue(result["is_member"])

    def test_own_page_manage_lookup_shows_is_admin_when_switched(self):
        response = self._get(
            self.page_realm.realm_id, self.page_realm.entity, transaction_type="manage"
        )
        self.assertEqual(response.status_code, 200)
        result = response.data["data"]
        self.assertTrue(result["is_admin"])

    def test_unrelated_page_not_seen_as_admin(self):
        other_page = _make_page_realm(
            created_by=self.personal_entity, slug="other-page-self-admin"
        )
        response = self._get(other_page.slug, self.page_realm.entity)
        self.assertEqual(response.status_code, 200)
        result = response.data["data"]
        self.assertFalse(result["is_admin"])
        self.assertFalse(result["is_member"])
