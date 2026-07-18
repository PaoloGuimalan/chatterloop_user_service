import uuid

from django.test import TestCase
from django.utils.timezone import now
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Member, Realm
from entity.models import Entity
from entity.permissions import MemberRole
from user.entity_switch_views import EntitySwitch, EntitySwitchBack
from user.ext_models.mongomodels import Session
from user.models import Account
from user.utils.jwt_tools import JWTTools


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


class EntitySwitchTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.personal_entity = _make_entity()
        self.account = _make_account(self.personal_entity)
        self.device_token = f"test-device-{uuid.uuid4()}"

        self.page_realm = _make_page_realm(created_by=self.personal_entity)

        self._created_session_entity_ids = []
        self.addCleanup(self._cleanup_sessions)

    def _cleanup_sessions(self):
        Session.objects(deviceToken=self.device_token).delete()

    def _post(self, view_cls, data, entity):
        request = self.factory.post(
            "/api/user/entity/switch",
            data,
            format="json",
            HTTP_DEVICE_TOKEN=self.device_token,
        )
        force_authenticate(request, user=self.account)
        request.entity = entity
        response = view_cls.as_view()(request)
        response.render() if hasattr(response, "render") else None
        return response

    def test_non_member_rejected(self):
        response = self._post(
            EntitySwitch, {"realm_id": self.page_realm.id}, self.personal_entity
        )
        self.assertEqual(response.status_code, 403)

    def test_plain_member_rejected(self):
        Member.objects.create(
            entity=self.personal_entity,
            realm=self.page_realm,
            added_by=self.personal_entity,
            role=MemberRole.MEMBER,
            date_joined=now(),
        )
        response = self._post(
            EntitySwitch, {"realm_id": self.page_realm.id}, self.personal_entity
        )
        self.assertEqual(response.status_code, 403)

    def test_non_page_realm_rejected(self):
        server_entity = _make_entity(entity_type="realm")
        server_realm = Realm.objects.create(
            entity=server_entity,
            name="Test Server",
            created_by=self.personal_entity,
            type="server",
        )
        Member.objects.create(
            entity=self.personal_entity,
            realm=server_realm,
            added_by=self.personal_entity,
            role=MemberRole.OWNER,
            date_joined=now(),
        )
        response = self._post(
            EntitySwitch, {"realm_id": server_realm.id}, self.personal_entity
        )
        self.assertEqual(response.status_code, 400)

    def test_owner_can_switch_and_session_is_created(self):
        Member.objects.create(
            entity=self.personal_entity,
            realm=self.page_realm,
            added_by=self.personal_entity,
            role=MemberRole.OWNER,
            date_joined=now(),
        )

        self.assertFalse(
            Session.objects(
                deviceToken=self.device_token,
                entityID=str(self.page_realm.entity_id),
            ).count()
            > 0
        )

        response = self._post(
            EntitySwitch, {"realm_id": self.page_realm.id}, self.personal_entity
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["status"])

        active_entity = response.data["result"]["active_entity"]
        self.assertEqual(active_entity["id"], str(self.page_realm.entity_id))
        self.assertEqual(active_entity["type"], "realm")

        # The critical session-check regression: the new entity claim must
        # have a corresponding session document, or the very next
        # authenticated request would fail with "Device not logged in."
        self.assertTrue(
            Session.objects(
                deviceToken=self.device_token,
                entityID=str(self.page_realm.entity_id),
            ).count()
            > 0
        )

        decoded = JWTTools.decoder(response.data["result"]["authtoken"])
        self.assertEqual(decoded["entity"], str(self.page_realm.entity_id))
        self.assertEqual(decoded["userID"], str(self.account.id))

    def test_admin_can_switch(self):
        Member.objects.create(
            entity=self.personal_entity,
            realm=self.page_realm,
            added_by=self.personal_entity,
            role=MemberRole.ADMIN,
            date_joined=now(),
        )
        response = self._post(
            EntitySwitch, {"realm_id": self.page_realm.id}, self.personal_entity
        )
        self.assertEqual(response.status_code, 200)

    def test_direct_page_to_page_switch_allowed(self):
        # Membership validated against the account's personal entity, so
        # switching should work even when currently acting as another page.
        other_page = _make_page_realm(created_by=self.personal_entity)
        Member.objects.create(
            entity=self.personal_entity,
            realm=self.page_realm,
            added_by=self.personal_entity,
            role=MemberRole.OWNER,
            date_joined=now(),
        )
        Member.objects.create(
            entity=self.personal_entity,
            realm=other_page,
            added_by=self.personal_entity,
            role=MemberRole.OWNER,
            date_joined=now(),
        )

        first_switch = self._post(
            EntitySwitch, {"realm_id": self.page_realm.id}, self.personal_entity
        )
        self.assertEqual(first_switch.status_code, 200)

        # Now "currently acting as" self.page_realm's entity, switch directly
        # to other_page without switching back to the personal entity first.
        second_switch = self._post(
            EntitySwitch, {"realm_id": other_page.id}, self.page_realm.entity
        )
        self.assertEqual(second_switch.status_code, 200)
        self.assertEqual(
            second_switch.data["result"]["active_entity"]["id"],
            str(other_page.entity_id),
        )

    def test_switch_back_restores_personal_entity(self):
        Member.objects.create(
            entity=self.personal_entity,
            realm=self.page_realm,
            added_by=self.personal_entity,
            role=MemberRole.OWNER,
            date_joined=now(),
        )
        self._post(EntitySwitch, {"realm_id": self.page_realm.id}, self.personal_entity)

        response = self._post(EntitySwitchBack, {}, self.page_realm.entity)
        self.assertEqual(response.status_code, 200)

        decoded = JWTTools.decoder(response.data["result"]["authtoken"])
        self.assertEqual(decoded["entity"], str(self.personal_entity.id))

    def test_switch_back_when_already_self_rejected(self):
        response = self._post(EntitySwitchBack, {}, self.personal_entity)
        self.assertEqual(response.status_code, 400)
