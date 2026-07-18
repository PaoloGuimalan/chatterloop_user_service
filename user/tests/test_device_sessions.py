import uuid

from django.test import TestCase
from django.utils.timezone import now
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Member, Realm
from entity.models import Entity
from entity.permissions import MemberRole
from user.ext_models.mongomodels import Session
from user.models import Account
from user.views import DeviceSessionList, UserAuthentication


def _make_entity(entity_type="user"):
    return Entity.objects.create(type=entity_type)


def _make_account(entity, email=None):
    return Account.objects.create(
        entity=entity,
        first_name="Test",
        last_name="User",
        email=email or f"{uuid.uuid4()}@example.com",
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


def _make_session(entity_id, device_token, session_id=None, **overrides):
    session = Session(
        sessionID=session_id or str(uuid.uuid4()),
        entityID=str(entity_id),
        userAgent="Mozilla/5.0 (test)",
        deviceType=overrides.get("deviceType", "desktop"),
        deviceToken=device_token,
        status=overrides.get("status", False),
        browser=overrides.get("browser", "Chrome"),
        os=overrides.get("os", "Windows"),
        ip=overrides.get("ip", "127.0.0.1"),
        lastSeen=now(),
    )
    session.save()
    return session


class DeviceSessionListTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.personal_entity = _make_entity()
        self.account = _make_account(self.personal_entity)
        self.device_token = f"device-{uuid.uuid4()}"
        self._session_ids_to_clean = []
        self.addCleanup(self._cleanup_sessions)

    def _cleanup_sessions(self):
        Session.objects(entityID=str(self.personal_entity.id)).delete()

    def _get(self, entity, device_token=None):
        request = self.factory.get(
            "/api/user/devices",
            HTTP_DEVICE_TOKEN=device_token or self.device_token,
        )
        force_authenticate(request, user=self.account)
        request.entity = entity
        response = DeviceSessionList.as_view()(request)
        response.render()
        return response

    def _delete(self, entity, session_id):
        request = self.factory.delete(
            "/api/user/devices",
            {"sessionID": session_id},
            format="json",
        )
        force_authenticate(request, user=self.account)
        request.entity = entity
        response = DeviceSessionList.as_view()(request)
        response.render()
        return response

    def test_list_returns_only_personal_entity_rows(self):
        _make_session(self.personal_entity.id, self.device_token)

        page_realm = _make_page_realm(created_by=self.personal_entity)
        _make_session(page_realm.entity_id, self.device_token)

        response = self._get(self.personal_entity)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(
            response.data["data"][0]["sessionID"] is not None, True
        )

    def test_is_current_device_flag(self):
        other_device_token = f"device-{uuid.uuid4()}"
        _make_session(self.personal_entity.id, self.device_token)
        _make_session(self.personal_entity.id, other_device_token)

        response = self._get(self.personal_entity, device_token=self.device_token)
        self.assertEqual(response.status_code, 200)
        current_flags = {
            row["is_current_device"] for row in response.data["data"]
        }
        self.assertIn(True, current_flags)
        self.assertEqual(
            sum(1 for row in response.data["data"] if row["is_current_device"]),
            1,
        )

    def test_revoke_deletes_rows_across_entities_sharing_device_token(self):
        personal_session = _make_session(self.personal_entity.id, self.device_token)

        page_realm = _make_page_realm(created_by=self.personal_entity)
        Member.objects.create(
            entity=self.personal_entity,
            realm=page_realm,
            added_by=self.personal_entity,
            role=MemberRole.OWNER,
            date_joined=now(),
        )
        _make_session(page_realm.entity_id, self.device_token)

        response = self._delete(self.personal_entity, personal_session.sessionID)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            Session.objects(deviceToken=self.device_token).count(), 0
        )

    def test_revoke_does_not_touch_other_accounts_session(self):
        other_entity = _make_entity()
        _make_account(other_entity, email=f"{uuid.uuid4()}@example.com")

        my_session = _make_session(self.personal_entity.id, self.device_token)
        _make_session(other_entity.id, self.device_token)

        response = self._delete(self.personal_entity, my_session.sessionID)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            Session.objects(entityID=str(other_entity.id)).count(), 1
        )
        Session.objects(entityID=str(other_entity.id)).delete()

    def test_revoke_other_accounts_session_id_404s(self):
        other_entity = _make_entity()
        other_session = _make_session(other_entity.id, f"device-{uuid.uuid4()}")

        response = self._delete(self.personal_entity, other_session.sessionID)
        self.assertEqual(response.status_code, 404)

        self.assertEqual(
            Session.objects(sessionID=other_session.sessionID).count(), 1
        )
        Session.objects(entityID=str(other_entity.id)).delete()


class LoginSessionDedupTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.personal_entity = _make_entity()
        self.email = f"{uuid.uuid4()}@example.com"
        self.password = "Sup3rSecret!"
        self.account = _make_account(self.personal_entity, email=self.email)
        self.account.password = _hash(self.password)
        self.account.save()
        self.device_token = f"device-{uuid.uuid4()}"
        self.addCleanup(self._cleanup_sessions)

    def _cleanup_sessions(self):
        Session.objects(deviceToken=self.device_token).delete()

    def _login(self):
        request = self.factory.post(
            "/api/user/auth",
            {"email_username": self.email, "password": self.password},
            format="json",
            HTTP_DEVICE_TOKEN=self.device_token,
        )
        response = UserAuthentication.as_view()(request)
        response.render()
        return response

    def test_repeat_login_does_not_duplicate_session(self):
        first = self._login()
        self.assertEqual(first.status_code, 200)
        second = self._login()
        self.assertEqual(second.status_code, 200)

        self.assertEqual(
            Session.objects(
                deviceToken=self.device_token, entityID=str(self.personal_entity.id)
            ).count(),
            1,
        )


def _hash(raw_password):
    import bcrypt

    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode(
        "utf-8"
    )
