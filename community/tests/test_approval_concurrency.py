"""Real PostgreSQL row-lock contracts; never substitute SQLite for this suite."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import time
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from community.models import Member, Realm
from entity.models import Entity, PermissionCatalogEntry, Role
from entity.tests.test_realm_role_policy import RealmRolePolicyFixture


@skipUnless(connection.vendor == "postgresql", "Requires explicit disposable PostgreSQL settings")
class RealmPolicyConcurrencyTests(RealmRolePolicyFixture, TransactionTestCase):
    def create_role_in_another_connection(self, pids):
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.put(cursor.fetchone()[0])
            return self.request("post", {"realm_id": self.realm.pk, "name": "Concurrent member preset"})
        finally:
            connections["default"].close()

    def wait_for_database_lock(self, pid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_stat_clear_snapshot()")
                cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid])
                row = cursor.fetchone()
            if row and row[0] == "Lock":
                return True
            time.sleep(0.01)
        return False

    def test_member_preset_waits_for_committing_local_permission_changes(self):
        pids = Queue()
        with ThreadPoolExecutor(max_workers=1) as threads:
            with transaction.atomic():
                realm = Realm.objects.select_for_update().get(pk=self.realm.pk)
                self.roles["member"].permissions.remove(self.catalog["realm.post.publish"])
                future = threads.submit(self.create_role_in_another_connection, pids)
                self.assertTrue(self.wait_for_database_lock(pids.get(timeout=5)), "Role creation must wait for the realm policy transaction")
            response = future.result(timeout=10)
        self.assertEqual(response.status_code, 201)
        role = Role.objects.get(pk=response.data["result"]["id"])
        self.assertFalse(role.permissions.filter(codename="realm.post.publish").exists())

    def test_waiting_role_creation_rechecks_actor_authority_after_demotion(self):
        pids = Queue()
        with ThreadPoolExecutor(max_workers=1) as threads:
            with transaction.atomic():
                Realm.objects.select_for_update().get(pk=self.realm.pk)
                member = Member.objects.get(entity=self.actor, realm=self.realm)
                member.role = self.roles["member"]
                member.save(update_fields=["role"])
                future = threads.submit(self.create_role_in_another_connection, pids)
                self.assertTrue(self.wait_for_database_lock(pids.get(timeout=5)))
            response = future.result(timeout=10)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Role.objects.filter(realm=self.realm, name="Concurrent member preset").exists())

    def post_fixture(self):
        from newsfeed.models import Post, PostTag
        author = Entity.objects.create(type="user")
        Member.objects.create(entity=author, realm=self.realm, added_by=self.actor, role=self.roles["member"])
        PermissionCatalogEntry.objects.create(codename="posts.create", scope="global")
        post = Post.objects.create(entity=author, approval=0, file_type="text", content_type="text")
        PostTag.objects.create(post=post, entity=self.realm.entity)
        return post

    def review_in_another_connection(self, pids, post, actor, action):
        from community.post_review import review_post
        from rest_framework.exceptions import APIException
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.put(cursor.fetchone()[0])
            try:
                return review_post(actor=actor, realm_id=self.realm.pk, post_id=post.pk, action=action,
                    expected_approval=0, expected_version=post.date_posted.isoformat(), reason="Review" if action == "reject" else "")
            except APIException as exc:
                return exc.status_code
        finally:
            connections["default"].close()

    def test_two_reviewers_cannot_make_conflicting_terminal_decisions(self):
        from community.models import Approvals
        post = self.post_fixture()
        second = Entity.objects.create(type="user")
        Member.objects.create(entity=second, realm=self.realm, added_by=self.actor, role=self.roles["admin"])
        pids = Queue()
        with patch("community.review_delivery.publish_review_effects"), ThreadPoolExecutor(max_workers=2) as threads:
            with transaction.atomic():
                Realm.objects.select_for_update().get(pk=self.realm.pk)
                jobs = [threads.submit(self.review_in_another_connection, pids, post, actor, action)
                        for actor, action in ((self.actor, "approve"), (second, "reject"))]
                for _ in jobs:
                    self.assertTrue(self.wait_for_database_lock(pids.get(timeout=5)))
            results = [job.result(timeout=10) for job in jobs]
        self.assertEqual(sum(result == 409 for result in results), 1)
        self.assertEqual(Approvals.objects.count(), 1)

    def test_review_waiting_for_edit_rejects_the_old_content_version(self):
        from django.utils.timezone import now
        from newsfeed.models import Post
        post = self.post_fixture()
        pids = Queue()
        with patch("community.review_delivery.publish_review_effects"), ThreadPoolExecutor(max_workers=1) as threads:
            with transaction.atomic():
                Realm.objects.select_for_update().get(pk=self.realm.pk)
                Post.objects.filter(pk=post.pk).update(caption="New version", updated_at=now())
                job = threads.submit(self.review_in_another_connection, pids, post, self.actor, "approve")
                self.assertTrue(self.wait_for_database_lock(pids.get(timeout=5)))
            self.assertEqual(job.result(timeout=10), 409)

    def invitation_fixture(self, *, pending=False, request=False):
        from community.models import Invite
        from community.invites import create_invite
        from user.models import Account
        for code in ("realm.invite.create", "realm.invite.publish", "realm.invite.review", "realm.invite.revoke", "realm.request.approve", "realm.request.decline"):
            self.catalog[code] = PermissionCatalogEntry.objects.create(codename=code, scope="realm")
        for path in ("community.invites.notify_invitee", "community.invites.email_invite", "community.invites.settle_invite_notifications",
                     "community.admin_notifications.notify_member_joined", "community.admin_notifications.settle_join_requests",
                     "community.invite_review.notify_invite_review_activity", "community.views.InviteView._notify_members_changed",
                     "community.views.InviteView._notify_requests_changed", "community.views.InviteView._notify_access_changed"):
            self.enterContext(patch(path))
        self.enterContext(patch("community.invites.get_entity_display_username", return_value="Owner"))
        self.enterContext(patch("community.invites.is_blocked", return_value=False))
        target = Entity.objects.create(type="user")
        Account.objects.create(entity=target, username="recipient", email="recipient@example.invalid", is_active=True)
        if request:
            return Invite.objects.create(realm=self.realm, kind="request", target_entity=target, created_by=target), target
        if pending:
            self.roles["admin"].permissions.add(self.catalog["realm.invite.create"])
            self.roles["admin"].permissions.remove(self.catalog["realm.invite.publish"])
            Member.objects.filter(entity=self.actor, realm=self.realm).update(role=self.roles["admin"])
        invite, _ = create_invite(self.realm, self.actor, target_entity_id=target.pk)
        return invite, target

    def invitation_in_another_connection(self, pids, invite, actor, action, expected_status=None):
        from community.invite_review import transition_invite
        from rest_framework.exceptions import APIException
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.put(cursor.fetchone()[0])
            try:
                return transition_invite(actor=actor, invite_id=invite.pk, action=action, expected_status=expected_status)
            except APIException as exc:
                return exc.status_code
        finally:
            connections["default"].close()

    def race_invitation(self, invite, actions):
        pids = Queue()
        with ThreadPoolExecutor(max_workers=2) as threads:
            with transaction.atomic():
                Realm.objects.select_for_update().get(pk=self.realm.pk)
                jobs = [threads.submit(self.invitation_in_another_connection, pids, invite, *action) for action in actions]
                for _ in jobs:
                    self.assertTrue(self.wait_for_database_lock(pids.get(timeout=5)))
            return [job.result(timeout=10) for job in jobs]

    def test_revoke_vs_accept(self):
        invite, target = self.invitation_fixture()
        result = self.race_invitation(invite, [(self.actor, "revoked"), (target, "accepted")])
        self.assertEqual(sum(item == 409 for item in result), 1)
        invite.refresh_from_db()
        self.assertEqual(Member.objects.filter(realm=self.realm, entity=target).count(), int(invite.status == "accepted"))

    def test_review_vs_accept(self):
        invite, target = self.invitation_fixture(pending=True)
        reviewer = Entity.objects.create(type="user")
        self.roles["admin"].permissions.add(self.catalog["realm.invite.review"])
        Member.objects.create(entity=reviewer, realm=self.realm, added_by=self.actor, role=self.roles["admin"])
        result = self.race_invitation(invite, [(reviewer, "approve", "for_approval"), (target, "accepted")])
        self.assertFalse(isinstance(result[0], int))
        invite.refresh_from_db()
        self.assertIn(invite.status, ("pending", "accepted"))
        self.assertIn(result[1] if isinstance(result[1], int) else 200, (200, 404))
        self.assertEqual(Member.objects.filter(realm=self.realm, entity=target).count(), int(invite.status == "accepted"))

    def test_cancel_request_vs_approve(self):
        invite, target = self.invitation_fixture(request=True)
        result = self.race_invitation(invite, [(self.actor, "accepted"), (target, "revoked")])
        self.assertEqual(sum(item == 409 for item in result), 1)
        invite.refresh_from_db()
        self.assertEqual(Member.objects.filter(realm=self.realm, entity=target).count(), int(invite.status == "accepted"))
