"""Canonical community profile paths against isolated SQLite, no migrations."""

# Initialize the offline registry before importing application views/models.
# ruff: noqa: E402

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from django.db import connection
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Invite, Member, Realm
from community.views import InviteView
from community.community_views import CommunityLeaveView
from entity.models import Entity, Follow, Role
from user.views import UserAuthentication


def _role(name):
    return Role.objects.get_or_create(name=name, is_default=True)[0]


class CommunityProfilePathTests(unittest.TestCase):
    def test_cancelling_a_community_request_never_removes_an_approved_member(self):
        Member.objects.create(realm=self.realm, entity=self.viewer, added_by=self.viewer, role=_role("member"))
        request = APIRequestFactory().post("/api/realm/communities/club/leave", {"cancel_request": True}, format="json")
        request.entity = self.viewer
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        response = CommunityLeaveView.as_view()(request, realm_id=self.realm.pk)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_member"])
        self.assertTrue(Member.objects.filter(realm=self.realm, entity=self.viewer).exists())

    def test_server_request_cancellation_only_revokes_the_callers_pending_request(self):
        Realm.objects.filter(pk=self.realm.pk).update(type="server")
        other = Entity.objects.create(id="other", type="user")
        own = Invite.objects.create(
            realm=self.realm,
            target_entity=self.viewer,
            created_by=self.viewer,
            kind="request",
            status="pending",
        )
        theirs = Invite.objects.create(
            realm=self.realm,
            target_entity=other,
            created_by=other,
            kind="request",
            status="pending",
        )
        request = APIRequestFactory().patch(
            "/api/realm/invites",
            {
                "realm_id": self.realm.realm_id,
                "status": "revoked",
                "target_entity_id": other.pk,
            },
        )
        request.entity = self.viewer
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with (
            patch.object(InviteView, "_notify_requests_changed") as notify,
            patch.object(InviteView, "_notify_access_changed"),
        ):
            response = InviteView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        own.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(own.status, "revoked")
        self.assertEqual(theirs.status, "pending")
        notify.assert_called_once()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.models = (Entity, Role, Realm, Member, Follow, Invite)
        with connection.schema_editor() as editor:
            for model in cls.models:
                editor.create_model(model)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as editor:
            for model in reversed(cls.models):
                editor.delete_model(model)
        super().tearDownClass()

    def setUp(self):
        self.viewer = Entity.objects.create(id="viewer", type="user")
        destination = Entity.objects.create(id="destination", type="realm")
        self.realm = Realm(
            id="123456789012345",
            realm_id="123456789012345",
            entity=destination,
            created_by=self.viewer,
            name="Community with no slug",
            type="community",
            slug=None,
            is_private=True,
        )
        Realm.objects.bulk_create([self.realm])

    def tearDown(self):
        with connection.cursor() as cursor:
            for model in reversed(self.models):
                cursor.execute(f'DELETE FROM "{model._meta.db_table}"')

    def get_profile(self, path, **params):
        request = APIRequestFactory().get(f"/api/user/{path}/", params)
        request.entity = self.viewer
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with (
            patch("user.views.Account.objects.filter", return_value=[]),
            patch("user.views.Bot.objects.filter") as bots,
            patch("user.views.Connection.objects.filter") as connections,
            patch("user.views.save_profile_visit"),
            patch("user.views.RabbitMQClient.publish_on_commit"),
        ):
            bots.return_value.first.return_value = None
            connections.return_value.order_by.return_value.first.return_value = None
            return UserAuthentication.as_view()(request, username=path)

    def test_slugless_community_id_resolves_private_metadata_with_current_membership(
        self,
    ):
        response = self.get_profile(self.realm.realm_id)
        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertEqual(data["realm_id"], self.realm.realm_id)
        self.assertEqual(data["type"], "community")
        self.assertIsNone(data["slug"])
        self.assertTrue(data["is_private"])
        self.assertFalse(data["is_member"])
        self.assertFalse(data["is_admin"])
        self.assertIsNone(data["my_role"])
        self.assertIsNone(data["my_role_id"])

        Member.objects.bulk_create(
            [
                Member(
                    entity=self.viewer,
                    realm=self.realm,
                    added_by=self.viewer,
                    role=_role("member"),
                )
            ]
        )
        joined = self.get_profile(self.realm.realm_id).data["data"]
        self.assertTrue(joined["is_member"])
        self.assertFalse(joined["is_admin"])
        self.assertEqual(joined["my_role"], "member")
        self.assertEqual(joined["my_role_id"], _role("member").pk)
        Member.objects.filter(realm=self.realm).delete()
        self.assertFalse(
            self.get_profile(self.realm.realm_id).data["data"]["is_member"]
        )

    def test_existing_slug_resolution_and_page_id_restriction_remain(self):
        Realm.objects.filter(pk=self.realm.pk).update(slug="garden-community")
        self.assertEqual(self.get_profile("garden-community").status_code, 200)
        Realm.objects.filter(pk=self.realm.pk).update(type="page")
        self.assertEqual(self.get_profile("garden-community").status_code, 200)
        self.assertEqual(self.get_profile(self.realm.realm_id).status_code, 404)
        managed = self.get_profile(self.realm.realm_id, type="manage")
        self.assertEqual(managed.status_code, 200)
        self.assertFalse(managed.data["data"]["is_admin"])
        self.assertFalse(managed.data["data"]["is_member"])

    def test_private_server_profile_reports_only_the_viewers_pending_request(self):
        Realm.objects.filter(pk=self.realm.pk).update(type="server")
        invite = Invite.objects.create(
            realm=self.realm,
            target_entity=self.viewer,
            created_by=self.viewer,
            kind="request",
            status="pending",
        )
        self.assertTrue(
            self.get_profile(self.realm.realm_id, type="manage").data["data"][
                "request_pending"
            ]
        )
        invite.status = "revoked"
        invite.save(update_fields=["status"])
        self.assertFalse(
            self.get_profile(self.realm.realm_id, type="manage").data["data"][
                "request_pending"
            ]
        )
