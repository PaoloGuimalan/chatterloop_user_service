import uuid
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from entity.models import Entity
from user.backends import AutheticationBackend
from user.models import Account


def _make_incomplete_account():
    entity = Entity.objects.create(type="user")
    return Account.objects.create(
        entity=entity,
        first_name="Test",
        last_name="User",
        email=f"{uuid.uuid4()}@example.com",
        is_active=True,
        is_verified=True,
        birthdate=None,
        gender=None,
    )


class ComplianceExemptionTests(TestCase):
    """
    A freshly third-party-registered (or newly registered) account has no
    birthdate/gender yet - that's exactly what the Complete Profile page
    exists to collect. But the app shell around that page needs
    allowed_modules/active_entity_context to render (see MyAllowedModules),
    so that endpoint must stay reachable even while the profile is
    incomplete - it was missing from COMPLIANCE_EXEMPT_VIEW_NAMES, causing
    every request to it to 403 with PROFILE_INCOMPLETE right as the Complete
    Profile page loaded.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.backend = AutheticationBackend()
        self.account = _make_incomplete_account()

    def _request_for_view(self, view_name):
        request = self.factory.get("/")
        request.resolver_match = SimpleNamespace(view_name=view_name)
        return request

    def test_allowed_modules_exempt_for_incomplete_profile(self):
        request = self._request_for_view("api-entity:my-allowed-modules")
        # Should not raise, even though the account has no birthdate/gender.
        self.backend._check_compliance(request, self.account)

    def test_non_exempt_view_still_blocks_incomplete_profile(self):
        request = self._request_for_view("api-newsfeed:some-other-view")
        with self.assertRaises(PermissionDenied):
            self.backend._check_compliance(request, self.account)
