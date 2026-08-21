import uuid

import bcrypt
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from core.models import TPAuthentication
from entity.models import Entity
from user.ext_models.mongomodels import Session
from user.models import Account
from user.utils.jwt_tools import JWTTools
from user.views import ThirdPartyAuthentication, UserAccountManagement, UserAuthentication


def _hash(raw_password):
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


class RegistrationUuidSerializationTests(TestCase):
    """
    Entity.id and Account.id are CharFields with default=uuid.uuid4 - the
    default is a callable that returns a raw uuid.UUID object, assigned to
    the in-memory model instance at creation time. A freshly created object
    (never re-fetched from the DB, where it would come back as a plain str)
    therefore holds a real UUID object on `.id`, not a string. PyJWT's
    encoder has no UUID support, so splicing that raw value straight into a
    jwt.encoder(...) payload - as both the third-party auto-registration
    path and the regular email registration path did - raised "Object of
    type UUID is not JSON serializable" on every brand-new signup.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.device_token = f"device-{uuid.uuid4()}"
        self._created_entity_ids = []

    def tearDown(self):
        Session.objects(deviceToken=self.device_token).delete()

    def test_third_party_auto_registration_does_not_crash(self):
        service_id = f"test-azp-{uuid.uuid4()}"
        TPAuthentication.objects.create(service_id=service_id, service_type="google")
        email = f"{uuid.uuid4()}@example.com"

        token = JWTTools.encoder(
            {
                "azp": service_id,
                "email": email,
                "given_name": "Test",
                "family_name": "User",
            }
        )

        request = self.factory.post(
            "/api/user/tp_auth",
            {"token": token},
            format="json",
            HTTP_DEVICE_TOKEN=self.device_token,
        )
        response = ThirdPartyAuthentication.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["status"])
        self.assertIn("usertoken", response.data["result"])
        self.assertIn("authtoken", response.data["result"])

        # allowed_modules/active_entity/personal_entity_id are merged
        # directly into this response now (see
        # resolve_allowed_modules_and_context) - the frontend no longer
        # needs a separate follow-up call to /api/entity/me/modules.
        self.assertIn("allowed_modules", response.data["result"])
        self.assertIn("active_entity", response.data["result"])
        account = Account.objects.get(email=email)
        self.assertEqual(response.data["result"]["personal_entity_id"], str(account.entity_id))
        self.assertEqual(response.data["result"]["active_entity"]["type"], "user")

        # Google's OAuth already established email ownership - no separate
        # code-verification step for this path.
        self.assertTrue(account.is_verified)

    def test_third_party_auto_registration_with_mononym(self):
        """
        Google omits `family_name` entirely for single-name (mononym)
        profiles. The auto-registration path used to split `given_name` on
        spaces and index [1] unconditionally, so a one-word name raised
        IndexError ("list index out of range") - surfaced to the client as a
        500. Because the account is never created, retrying reads as a
        failure on BOTH "login" and "sign up": every attempt re-enters this
        same registration branch.
        """
        service_id = f"test-azp-{uuid.uuid4()}"
        TPAuthentication.objects.create(service_id=service_id, service_type="google")
        email = f"{uuid.uuid4()}@example.com"

        token = JWTTools.encoder(
            {
                "azp": service_id,
                "email": email,
                "given_name": "Madonna",
            }
        )

        request = self.factory.post(
            "/api/user/tp_auth",
            {"token": token},
            format="json",
            HTTP_DEVICE_TOKEN=self.device_token,
        )
        response = ThirdPartyAuthentication.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["status"])

        account = Account.objects.get(email=email)
        self.assertEqual(account.first_name, "Madonna")
        # last_name is non-null on Account, so it takes the same "N/A"
        # sentinel create_user() already uses for an absent middle_name.
        self.assertEqual(account.last_name, "N/A")

    def test_third_party_auto_registration_with_multiword_given_name(self):
        """
        Some Google accounts have no `family_name` but pack the full name
        into `given_name`. Splitting off the LAST word keeps multi-word
        given names intact instead of dropping everything after the second
        word, as the old split(" ")[0]/[1] did.
        """
        service_id = f"test-azp-{uuid.uuid4()}"
        TPAuthentication.objects.create(service_id=service_id, service_type="google")
        email = f"{uuid.uuid4()}@example.com"

        token = JWTTools.encoder(
            {
                "azp": service_id,
                "email": email,
                "given_name": "Juan Miguel Dela Cruz",
            }
        )

        request = self.factory.post(
            "/api/user/tp_auth",
            {"token": token},
            format="json",
            HTTP_DEVICE_TOKEN=self.device_token,
        )
        response = ThirdPartyAuthentication.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 200)

        account = Account.objects.get(email=email)
        self.assertEqual(account.first_name, "Juan Miguel Dela")
        self.assertEqual(account.last_name, "Cruz")

    def test_regular_registration_does_not_crash(self):
        email = f"{uuid.uuid4()}@example.com"
        request = self.factory.post(
            "/api/user/me",
            {
                "firstName": "Test",
                "lastName": "User",
                "email": email,
                "password": "Sup3rSecret!",
                "gender": "male",
                "agreedToTerms": True,
                "birthday": 1,
                "birthmonth": 1,
                "birthyear": 2000,
            },
            format="json",
            HTTP_DEVICE_TOKEN=self.device_token,
        )
        response = UserAccountManagement.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["status"])
        self.assertIn("usertoken", response.data)
        self.assertIn("authtoken", response.data)

        # allowed_modules/active_entity/personal_entity_id are merged
        # directly into this response now (top-level, matching this
        # endpoint's existing flat shape - not nested under "result" like
        # the other two auth-building endpoints).
        self.assertIn("allowed_modules", response.data)
        self.assertIn("active_entity", response.data)
        account = Account.objects.get(email=email)
        self.assertEqual(response.data["personal_entity_id"], str(account.entity_id))
        self.assertEqual(response.data["active_entity"]["type"], "user")

        # Manual registration must go through CodeVerification - the account
        # should NOT be marked verified just from filling out the form.
        self.assertFalse(account.is_verified)


class LoginAllowedModulesMergeTests(TestCase):
    """
    UserAuthentication.post() (login) now merges allowed_modules/
    active_entity/personal_entity_id directly into its own response -
    the webapp no longer makes a separate follow-up call to
    /api/entity/me/modules after login (see LoginRequest in requests.ts).
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.device_token = f"device-{uuid.uuid4()}"
        self.email = f"{uuid.uuid4()}@example.com"
        self.password = "Sup3rSecret!"
        entity = Entity.objects.create(type="user")
        self.account = Account.objects.create(
            entity=entity,
            first_name="Test",
            last_name="User",
            email=self.email,
            is_active=True,
            is_verified=True,
        )
        self.account.password = _hash(self.password)
        self.account.save()

    def tearDown(self):
        Session.objects(deviceToken=self.device_token).delete()

    def test_login_response_includes_allowed_modules_and_active_entity(self):
        request = self.factory.post(
            "/api/user/auth",
            {"email_username": self.email, "password": self.password},
            format="json",
            HTTP_DEVICE_TOKEN=self.device_token,
        )
        response = UserAuthentication.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["status"])
        result = response.data["result"]
        self.assertIn("allowed_modules", result)
        self.assertIn("active_entity", result)
        self.assertEqual(result["active_entity"]["type"], "user")
        self.assertEqual(result["personal_entity_id"], str(self.account.entity_id))
