from entity.tests.permission_fixtures import create_permission
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase
from rest_framework.exceptions import APIException, PermissionDenied
from community.invites import create_invite, claim_pending_invites
from community.models import Invite, Member, Approvals
from entity.models import Entity, EntityPermission, PermissionCatalogEntry, Role
from entity.tests.test_realm_role_policy import RealmRolePolicyFixture
from user.models import Account


class InviteReviewTests(RealmRolePolicyFixture, TestCase):
    def setUp(self):
        super().setUp()
        for code in ("realm.invite.create", "realm.invite.publish", "realm.invite.review", "realm.invite.revoke",
                     "realm.request.approve", "realm.request.decline"):
            self.catalog[code] = create_permission(code=code, scope="realm")

        self.target = Entity.objects.create(type="user")
        self.target.refresh_from_db()
        self.account = Account.objects.create(entity=self.target, username="recipient", email="recipient@example.invalid", is_active=True)
        self.notice = self.enterContext(patch("community.invites.notify_invitee"))
        self.email = self.enterContext(patch("community.invites.email_invite"))
        self.enterContext(patch("community.invites.get_entity_display_username", return_value="Owner"))
        self.enterContext(patch("community.invites.is_blocked", return_value=False))
        self.enterContext(patch("community.admin_notifications.notify_member_joined"))
        self.settle = self.enterContext(patch("community.admin_notifications.settle_join_requests"))
        self.enterContext(patch("community.admin_notifications.settle_review_notices"))
        self.refresh = {}
        for method in ("_notify_members_changed", "_notify_requests_changed", "_notify_access_changed"):
            self.refresh[method] = self.enterContext(patch(f"community.views.InviteView.{method}"))

    def create(self):
        return create_invite(self.realm, self.actor, target_email=self.account.email)[0]

    def transition(self, invite, action, **kwargs):
        from community.invite_review import transition_invite
        return transition_invite(actor=kwargs.pop("actor", self.actor), account=kwargs.pop("account", None),
            invite_id=invite.pk, action=action, **kwargs)

    def restrict_delivery(self):
        self.roles["admin"].permissions.add(self.catalog["realm.invite.create"], self.catalog["realm.invite.review"])
        self.roles["admin"].permissions.remove(self.catalog["realm.invite.publish"])
        Member.objects.filter(entity=self.actor, realm=self.realm).update(role=self.roles["admin"])

    def test_inviter_can_revoke_an_awaiting_approval_invite_through_existing_endpoint(self):
        from community.views import InviteView
        from rest_framework.test import APIRequestFactory, force_authenticate
        self.restrict_delivery()
        invite = self.create()
        request = APIRequestFactory().patch('/invites', {
            'invite_token': invite.invite_token, 'status': 'revoked'}, format='json')
        request.entity = self.actor
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        response = InviteView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        invite.refresh_from_db()
        self.assertEqual(invite.status, 'revoked')
        self.notice.assert_not_called()

    def test_direct_delivery_remains_approved_and_happens_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            invite = self.create()
            self.assertEqual(self.notice.call_count, 0)
            self.assertEqual(self.email.call_count, 0)
        self.assertEqual(invite.status, "pending")
        self.notice.assert_called_once()
        self.email.assert_called_once()

    def test_reviewed_invite_has_no_delivery_or_usable_recipient_token(self):
        self.restrict_delivery()
        with self.captureOnCommitCallbacks(execute=True):
            invite = self.create()
        self.assertEqual(invite.status, "for_approval")
        self.notice.assert_not_called()
        self.email.assert_not_called()
        with self.assertRaises(APIException) as error:
            self.transition(invite, "accepted", actor=self.target, account=self.account)
        self.assertEqual(error.exception.status_code, 404)

    def test_email_claim_does_not_deliver_unreviewed_invites(self):
        self.restrict_delivery()
        invite = self.create()
        Invite.objects.filter(pk=invite.pk).update(target_entity=None)
        self.assertEqual(claim_pending_invites(self.account), 0)
        self.notice.assert_not_called()

    def test_inviter_cannot_approve_own_restricted_invitation(self):
        self.restrict_delivery()
        invite = self.create()
        with self.assertRaises(PermissionDenied):
            self.transition(invite, "approve", expected_status="for_approval")

    def test_review_approval_delivers_without_joining_and_rejection_is_not_decline(self):
        self.restrict_delivery()
        invite = self.create()
        reviewer = Entity.objects.create(type="user")
        Member.objects.create(entity=reviewer, realm=self.realm, added_by=self.actor, role=self.roles["admin"])
        self.roles["admin"].permissions.add(self.catalog["realm.invite.review"])
        with self.captureOnCommitCallbacks(execute=True):
            self.transition(invite, "approve", actor=reviewer, expected_status="for_approval")
        invite.refresh_from_db()
        self.assertEqual(invite.status, "pending")
        self.assertFalse(Member.objects.filter(realm=self.realm, entity=self.target).exists())
        self.notice.assert_called_once()
        self.email.assert_called_once()

    def test_recipient_acceptance_and_request_approval_create_only_the_target_member(self):
        invite = self.create()
        self.transition(invite, "accepted", actor=self.target, account=self.account)
        self.assertEqual(Member.objects.filter(realm=self.realm, entity=self.target).count(), 1)
        with self.assertRaises(APIException) as error:
            self.transition(invite, "accepted", actor=self.target, account=self.account)
        self.assertEqual(error.exception.status_code, 409)

    def test_join_request_keeps_reviewer_attribution_and_joins_only_the_requester(self):
        invite = Invite.objects.create(realm=self.realm, kind="request", target_entity=self.target, created_by=self.target)
        self.transition(invite, "approve", expected_status="pending")
        invite.refresh_from_db()
        member = Member.objects.get(realm=self.realm, entity=self.target)
        self.assertEqual(str(invite.accepted_by_entity_id), str(self.actor.pk))
        self.assertEqual(str(member.added_by_id), str(self.actor.pk))

    def test_disabled_recipient_cannot_accept_a_previously_delivered_invite(self):
        invite = self.create()
        Account.objects.filter(pk=self.account.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.transition(invite, "accepted", actor=self.target, account=self.account)
        self.assertFalse(Member.objects.filter(entity=self.target, realm=self.realm).exists())

    def test_server_approval_retains_channel_membership_side_effects(self):
        from community.models import Realm
        self.realm.type = "server"
        self.realm.save(update_fields=["type"])
        channels = [Realm.objects.create(entity=Entity.objects.create(type="realm"), created_by=self.actor,
                    name=kind, type=kind, parent=self.realm, is_private=private)
                    for kind, private in (("channel", False), ("voice", False), ("channel", True))]
        invite = Invite.objects.create(realm=self.realm, kind="request", target_entity=self.target, created_by=self.target)
        with patch("community.invites.post_joined_notice") as joined:
            self.transition(invite, "accepted")
        self.assertEqual(set(Member.objects.filter(entity=self.target).values_list("realm_id", flat=True)),
                         {self.realm.pk, channels[0].pk, channels[1].pk})
        joined.assert_called_once_with(channels[0].realm_id, self.target)

    def test_request_approval_and_withdrawal_refresh_managers(self):
        for action in ("accepted", "revoked"):
            for mock in self.refresh.values():
                mock.reset_mock()
            self.settle.reset_mock()
            invite = Invite.objects.create(realm=self.realm, kind="request", target_entity=self.target, created_by=self.target)
            with self.captureOnCommitCallbacks(execute=True):
                self.transition(invite, action, actor=self.actor if action == "accepted" else self.target)
                for mock in self.refresh.values():
                    mock.assert_not_called()
            self.refresh["_notify_requests_changed"].assert_called_once_with(self.realm)
            self.assertEqual(self.refresh["_notify_members_changed"].call_count, 1 if action == "accepted" else 0)
            self.refresh["_notify_access_changed"].assert_called_once_with(str(self.target.pk), self.realm)
            self.settle.assert_called_once_with([str(invite.pk)])
            Member.objects.filter(entity=self.target, realm=self.realm).delete()

    def test_cancelled_community_request_notifies_managers_after_commit(self):
        from community.community_views import CommunityLeaveView
        from rest_framework.test import APIRequestFactory, force_authenticate
        invite = Invite.objects.create(realm=self.realm, kind="request", target_entity=self.target, created_by=self.target)
        request = APIRequestFactory().post("/communities/leave", {"cancel_request": True}, format="json")
        request.entity = self.target
        force_authenticate(request, user=self.account)
        with self.captureOnCommitCallbacks(execute=True):
            response = CommunityLeaveView.as_view()(request, realm_id=self.realm.pk)
            self.refresh["_notify_requests_changed"].assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.refresh["_notify_requests_changed"].assert_called_once_with(self.realm)
        self.settle.assert_called_once_with([str(invite.pk)])
        invite.refresh_from_db()
        self.assertEqual(invite.status, "revoked")

    def test_management_invites_support_scoped_roles_without_delegation_bypass(self):
        self.realm.type = "page"
        self.realm.save(update_fields=["type"])
        role = Role.objects.create(name="Community liaison", realm=self.realm)
        role.permissions.add(self.catalog["realm.member.view"])
        invite, _ = create_invite(self.realm, self.actor, target_email=self.account.email, purpose="manage", role_id=role.pk)
        self.assertEqual(invite.role_id, role.pk)
        self.transition(invite, "accepted", actor=self.target, account=self.account)
        self.assertEqual(Member.objects.get(entity=self.target, realm=self.realm).role_id, role.pk)

    def test_invitations_cannot_indirectly_delegate_custom_administration(self):
        self.realm.type = "page"
        self.realm.save(update_fields=["type"])
        Member.objects.filter(entity=self.actor, realm=self.realm).update(role=self.roles["admin"])
        self.roles["admin"].permissions.add(self.catalog["realm.invite.create"])
        role = Role.objects.create(name="Harmless display name", realm=self.realm)
        role.permissions.add(self.catalog["realm.roles.manage"])
        with self.assertRaises(PermissionDenied):
            create_invite(self.realm, self.actor, target_email=self.account.email, purpose="manage", role_id=role.pk)

    def test_restricted_invitation_enters_review_without_environment_opt_in(self):
        self.restrict_delivery()
        invite = self.create()
        self.assertEqual(invite.status, "for_approval")
        self.notice.assert_not_called()
        self.email.assert_not_called()

    def test_unreviewed_invitation_token_cannot_open_the_recipient_screen(self):
        from community.views import InviteView
        from rest_framework.test import APIRequestFactory, force_authenticate
        self.restrict_delivery()
        invite = self.create()
        request = APIRequestFactory().get("/invites", {"invite_token": invite.invite_token})
        force_authenticate(request, user=self.account)
        request.entity = self.target
        response = InviteView.as_view()(request)
        self.assertEqual(response.status_code, 404)

    def test_approvals_sections_reuse_invite_records_and_transition_service(self):
        from community.approval_views import CommunityApprovalsView, CommunityInviteReviewView
        from rest_framework.test import APIRequestFactory, force_authenticate
        self.restrict_delivery()
        invite = self.create()
        request_row = Invite.objects.create(realm=self.realm, kind="request", target_entity=self.target, created_by=self.target)
        reviewer = Entity.objects.create(type="user")
        Member.objects.create(entity=reviewer, realm=self.realm, added_by=self.actor, role=self.roles["admin"])
        self.roles["admin"].permissions.add(self.catalog["realm.invite.review"], self.catalog["realm.request.approve"])
        for kind, row in (("invites", invite), ("joins", request_row)):
            request = APIRequestFactory().get("/approvals", {"kind": kind})
            request.entity = reviewer
            force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
            response = CommunityApprovalsView.as_view()(request, realm_id=self.realm.pk)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["results"][0]["id"], str(row.pk))
            body = {"action": "approve", "expected_status": "for_approval" if kind == "invites" else "pending"}
            request = APIRequestFactory().post("/approvals", body, format="json")
            request.entity = reviewer
            force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
            response = CommunityInviteReviewView.as_view()(request, realm_id=self.realm.pk, kind=kind, invite_id=row.pk)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(Member.objects.filter(realm=self.realm, entity=self.target).count(), 1)

    def test_review_delivery_failure_keeps_a_retryable_audit(self):
        from community.invite_review import deliver_reviewed_invitation
        self.restrict_delivery()
        invite = self.create()
        reviewer = Entity.objects.create(type="user")
        Member.objects.create(entity=reviewer, realm=self.realm, added_by=self.actor, role=self.roles["admin"])
        self.roles["admin"].permissions.add(self.catalog["realm.invite.review"])
        self.notice.side_effect = RuntimeError("Notification transport offline")
        with self.captureOnCommitCallbacks(execute=True):
            self.transition(invite, "approve", actor=reviewer, expected_status="for_approval")
        decision = Approvals.objects.filter(target_id=str(invite.pk), action="approve").get()
        self.assertEqual(decision.delivery_state["status"], "pending")
        self.notice.side_effect = None
        self.assertTrue(deliver_reviewed_invitation(decision.pk))
        self.assertTrue(deliver_reviewed_invitation(decision.pk))
        decision.refresh_from_db()
        self.assertEqual(decision.delivery_state["status"], "complete")
        self.assertEqual(self.notice.call_count, 2)
        self.email.assert_called_once()

    def test_invite_review_uses_status_and_rejection_cannot_be_used_by_recipient(self):
        self.assertNotIn("approval", {f.name for f in Invite._meta.fields})
        self.assertEqual(Approvals._meta.db_table, "community_approvals")
        self.restrict_delivery()
        invite = self.create()
        reviewer = Entity.objects.create(type="user")
        Member.objects.create(entity=reviewer, realm=self.realm, added_by=self.actor, role=self.roles["owner"])
        self.transition(invite, "reject", actor=reviewer, expected_status="for_approval")
        invite.refresh_from_db()
        self.assertEqual(invite.status, "rejected")
        with self.assertRaises(APIException):
            self.transition(invite, "accepted", actor=self.target, account=self.account)
        self.assertFalse(Member.objects.filter(entity=self.target, realm=self.realm).exists())
