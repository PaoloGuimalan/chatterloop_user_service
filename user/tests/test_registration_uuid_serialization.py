import uuid

from django.test import TestCase
from rest_framework.test import APIRequestFactory

from core.models import TPAuthentication
from user.ext_models.mongomodels import Session
from user.models import Account
from user.utils.jwt_tools import JWTTools
from user.views import ThirdPartyAuthentication, UserAccountManagement


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

        # Google's OAuth already established email ownership - no separate
        # code-verification step for this path.
        self.assertTrue(Account.objects.get(email=email).is_verified)

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

        # Manual registration must go through CodeVerification - the account
        # should NOT be marked verified just from filling out the form.
        self.assertFalse(Account.objects.get(email=email).is_verified)
