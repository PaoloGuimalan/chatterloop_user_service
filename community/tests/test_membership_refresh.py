"""Request transitions invalidate the correct realm views after commit."""
# ruff: noqa: E402

import os
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from community.views import InviteView
from community.community_views import CommunityLeaveView
from entity.models import Entity
from rest_framework.test import APIRequestFactory, force_authenticate


class MembershipRefreshTests(unittest.TestCase):
    def test_cancelled_community_request_notifies_managers_after_commit(self):
        entity = Entity(id="requester", type="user")
        realm = SimpleNamespace(type="community", realm_id="club")
        request = APIRequestFactory().post("/api/realm/communities/club/leave")
        request.entity = entity
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with (
            patch("community.community_views.get_object_or_404", return_value=realm),
            patch("community.community_views.Member.objects.filter") as members,
            patch("community.community_views.Invite.objects.filter") as invites,
            patch(
                "community.community_views.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "community.community_views.transaction.on_commit",
                side_effect=lambda callback: callback(),
            ),
            patch.object(InviteView, "_notify_requests_changed") as notify,
        ):
            members.return_value.first.return_value = None
            invites.return_value.update.return_value = 1
            response = CommunityLeaveView.as_view()(request, realm_id="club")
        self.assertEqual(response.status_code, 200)
        notify.assert_called_once_with(realm)

    def test_server_approval_retains_channel_membership_side_effects(self):
        entity = Entity(id="requester", type="user")
        realm = SimpleNamespace(type="server", realm_id="server")
        invite = SimpleNamespace(
            kind="request",
            status="pending",
            realm=realm,
            target_entity=entity,
            target_entity_id=entity.pk,
            created_by_id=entity.pk,
            save=Mock(),
        )
        request = APIRequestFactory().patch(
            "/api/realm/invites", {"invite_token": "token", "status": "accepted"}
        )
        request.entity = entity
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with (
            patch("community.views.get_object_or_404", return_value=invite),
            patch("community.views.has_permission", return_value=True),
            patch("community.views.transaction.atomic", return_value=nullcontext()),
            patch(
                "community.views.transaction.on_commit",
                side_effect=lambda callback: callback(),
            ),
            patch("community.views.accept_side_effects") as accept,
            patch.object(InviteView, "_serialize_invite", return_value={}),
            patch.object(InviteView, "_notify_requests_changed"),
            patch.object(InviteView, "_notify_members_changed") as notify,
            patch.object(InviteView, "_notify_access_changed"),
        ):
            response = InviteView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        accept.assert_called_once_with(invite, entity)
        notify.assert_called_once_with(realm)

    def test_servers_use_realm_request_signals(self):
        view = InviteView()
        realm = SimpleNamespace(type="server", realm_id="server")
        with (
            patch.object(view, "_realm_admin_recipient_ids", return_value={"admin"}),
            patch.object(view, "_publish_event") as publish,
        ):
            view._notify_requests_changed(realm)
        self.assertEqual(publish.call_args.args[1], "realm_requests_changed")
        self.assertEqual(publish.call_args.args[2]["realm_id"], "server")

    def test_request_approval_and_withdrawal_refresh_managers(self):
        for status in ("accepted", "revoked"):
            with self.subTest(status=status):
                entity = Entity(id="requester", type="user")
                realm = SimpleNamespace(type="community", realm_id="club")
                invite = SimpleNamespace(
                    pk="request",
                    kind="request",
                    status="pending",
                    realm=realm,
                    target_entity=entity,
                    target_entity_id=entity.id,
                    created_by_id=entity.id,
                    save=Mock(),
                )
                request = APIRequestFactory().patch(
                    "/api/realm/invites", {"invite_token": "token", "status": status}
                )
                request.entity = entity
                force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
                with (
                    patch("community.views.get_object_or_404", return_value=invite),
                    patch("community.views.has_permission", return_value=True),
                    patch(
                        "community.views.transaction.atomic", return_value=nullcontext()
                    ),
                    patch(
                        "community.views.transaction.on_commit",
                        side_effect=lambda callback: callback(),
                    ),
                    patch.object(InviteView, "_add_member_if_missing"),
                    patch.object(InviteView, "_serialize_invite", return_value={}),
                    patch.object(InviteView, "_notify_requests_changed") as requests,
                    patch.object(InviteView, "_notify_members_changed") as members,
                    patch.object(InviteView, "_notify_access_changed"),
                    patch(
                        "community.admin_notifications.settle_join_requests"
                    ) as settle,
                ):
                    response = InviteView.as_view()(request)
                self.assertEqual(response.status_code, 200)
                requests.assert_called_once_with(realm)
                self.assertEqual(members.call_count, 1 if status == "accepted" else 0)
                settle.assert_called_once_with(["request"])
