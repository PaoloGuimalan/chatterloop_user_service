"""Discovery exposes metadata while private participation stays approval-only."""

# Models require the offline Django registry before imports.
# ruff: noqa: E402
import os
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from community.community_views import (
    CommunitiesView,
    CommunityDetailView,
    CommunityJoinView,
    CommunityLeaveView,
)
from community.models import Realm
from entity.models import Entity


class CommunityDiscoveryGuestTests(unittest.TestCase):
    def test_discovered_private_community_still_requires_join_approval(self):
        realm = Realm(id="club", realm_id="club", type="community", is_private=True)
        request = APIRequestFactory().post("/api/realm/communities/club/join", {})
        request.entity = Entity(id="viewer", type="user")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with (
            patch(
                "community.community_views.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch("community.community_views.get_object_or_404", return_value=realm),
            patch("community.community_views.Realm.objects.filter") as realms,
            patch("community.community_views.Member.objects.filter") as members,
            patch("community.community_views.Member.objects.create") as create_member,
            patch(
                "community.views.InviteView._request_access",
                return_value=Response({"request_pending": True}),
            ) as request_access,
        ):
            realms.return_value.exists.return_value = False
            members.return_value.exists.return_value = False
            response = CommunityJoinView.as_view()(request, realm_id="club")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["request_pending"])
        request_access.assert_called_once()
        create_member.assert_not_called()

    def test_owner_leave_failure_is_not_a_realm_id_field_error(self):
        request = APIRequestFactory().post("/api/realm/communities/club/leave", {})
        request.entity = Entity(id="owner", type="user")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with (
            patch(
                "community.community_views.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "community.community_views.get_object_or_404",
                return_value=Realm(id="club"),
            ),
            patch("community.community_views.Member.objects.filter") as members,
        ):
            members.return_value.first.return_value = SimpleNamespace(role="owner")
            response = CommunityLeaveView.as_view()(request, realm_id="club")
        self.assertEqual(response.status_code, 400)
        self.assertIsInstance(response.data, list)
        self.assertIn("Transfer ownership", str(response.data))
        self.assertNotIn("realm_id", str(response.data))

    def test_discover_keeps_private_communities_in_the_query(self):
        request = APIRequestFactory().get("/api/realm/communities")
        request.entity = Entity(id="viewer", type="user")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with patch("community.views.Pagination") as paginator:
            paginator.return_value.paginate_queryset.return_value = []
            paginator.return_value.get_paginated_response.return_value = Response([])
            response = CommunitiesView.as_view()(request)
        queryset = paginator.return_value.paginate_queryset.call_args.args[0]
        # Metadata discovery must not add a public-or-already-joined WHERE gate.
        self.assertNotIn("is_private", str(queryset.query.where))
        self.assertEqual(response.status_code, 200)

    def test_guest_can_read_profile_metadata_without_an_auth_token(self):
        for private in (False, True):
            with self.subTest(private=private):
                realm = Realm(
                    id="club",
                    realm_id="club",
                    entity_id="destination",
                    type="community",
                    name="Garden Club",
                    is_private=private,
                )
                realm.joined = False
                realm.member_count = 3
                realm.my_role = None
                realm.request_pending = False
                request = APIRequestFactory().get("/api/realm/communities/garden")
                with patch(
                    "community.community_views.get_object_or_404", return_value=realm
                ):
                    response = CommunityDetailView.as_view()(request, realm_id="garden")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["result"]["name"], "Garden Club")
                self.assertFalse(response.data["result"]["is_member"])
                self.assertIsNone(response.data["result"]["my_role"])
