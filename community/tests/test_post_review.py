from entity.tests.permission_fixtures import create_permission
from datetime import timedelta
from unittest.mock import patch
from types import SimpleNamespace
from django.test import TestCase
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from django.utils.timezone import now
from community.models import Member, Approvals
from entity.models import Entity, EntityPermission, PermissionCatalogEntry, Role
from entity.tests.test_realm_role_policy import RealmRolePolicyFixture
from newsfeed.models import Post, PostTag


class PostReviewTests(RealmRolePolicyFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.author = Entity.objects.create(type="user")
        self.author.refresh_from_db()
        Member.objects.create(entity=self.author, realm=self.realm, added_by=self.actor, role=self.roles["member"])
        self.catalog["posts.create"] = create_permission(code="posts.create", scope="global")
        self.catalog["realm.post.takedown"] = create_permission(code="realm.post.takedown", scope="realm")
        self.post = Post.objects.create(entity=self.author, approval=0, caption="Pending", file_type="text", content_type="text")
        PostTag.objects.create(post=self.post, entity=self.realm.entity)
        self.delivery = self.enterContext(patch("community.review_delivery.publish_review_effects"))

    def review(self, action="approve", **kwargs):
        from community.post_review import review_post
        return review_post(actor=self.actor, realm_id=self.realm.pk, post_id=self.post.pk, action=action,
            expected_approval=kwargs.pop("expected_approval", self.post.approval),
            expected_version=kwargs.pop("expected_version", (self.post.updated_at or self.post.date_posted).isoformat()), **kwargs)

    def test_approval_preserves_content_timestamp_and_retries_same_decision(self):
        original = self.post.date_posted
        with self.captureOnCommitCallbacks(execute=True):
            decision = self.review()
        self.post.refresh_from_db()
        self.assertEqual(self.post.approval, 1)
        self.assertEqual(self.post.date_posted, original)
        self.assertIsNone(self.post.updated_at)
        self.assertEqual(self.delivery.call_count, 1)
        retry = self.review(expected_approval=0)
        self.assertEqual(retry.pk, decision.pk)
        self.assertEqual(Approvals.objects.count(), 1)

    def test_reject_requires_reason_and_records_immutable_current_version(self):
        with self.assertRaises(ValidationError):
            self.review("reject")
        decision = self.review("reject", reason="Please stay on topic.")
        self.post.refresh_from_db()
        self.assertEqual(self.post.approval, 2)
        self.assertEqual(decision.reason, "Please stay on topic.")
        self.assertEqual(decision.content_version, self.post.date_posted)

    def test_stale_state_or_content_version_conflicts(self):
        for kwargs in ({"expected_approval": 2}, {"expected_version": (now()+timedelta(days=1)).isoformat()}):
            with self.subTest(kwargs=kwargs), self.assertRaises(APIException) as error:
                self.review(**kwargs)
            self.assertEqual(error.exception.status_code, 409)
        self.assertFalse(Approvals.objects.exists())

    def test_takedown_and_reapproval_do_not_reannounce_or_change_timestamp(self):
        self.review()
        self.post.refresh_from_db()
        self.review("takedown", reason="Community rule violation")
        self.post.refresh_from_db()
        decision = self.review()
        self.assertFalse(decision.delivery_state["first_publication"])
        self.assertEqual(decision.new_state["approval"], 1)

    def test_reviewer_cannot_self_approve_without_direct_publication(self):
        Member.objects.filter(entity=self.author, realm=self.realm).update(role=self.roles["moderator"])
        self.roles["moderator"].permissions.add(self.catalog["realm.post.review"])
        self.actor = self.author
        with self.assertRaises(PermissionDenied):
            self.review()

    def test_publication_revalidates_author_membership_and_platform_restriction(self):
        EntityPermission.objects.create(entity=self.author, permission=self.catalog["posts.create"], effect="deny")
        with self.assertRaises(PermissionDenied):
            self.review()
        EntityPermission.objects.all().delete()
        Member.objects.filter(entity=self.author, realm=self.realm).delete()
        with self.assertRaises(PermissionDenied):
            self.review()

    def test_publication_uses_realm_grants_without_environment_opt_in(self):
        from community.post_review import submission_approval
        self.assertEqual(submission_approval(self.author, None), 1)
        self.assertEqual(submission_approval(self.author, self.realm), 1)
        self.roles["member"].permissions.remove(self.catalog["realm.post.publish"])
        self.assertEqual(submission_approval(self.author, self.realm), 0)
        self.roles["member"].permissions.remove(self.catalog["realm.post.submit"])
        with self.assertRaises(PermissionDenied):
            submission_approval(self.author, self.realm)

    def test_tags_must_still_be_members_when_publication_is_reviewed(self):
        outsider = Entity.objects.create(type="user")
        PostTag.objects.create(post=self.post, entity=outsider)
        with self.assertRaises(ValidationError):
            self.review()

    def edit(self, fields):
        from newsfeed.services.post_editing import update_post
        return update_post(request=SimpleNamespace(entity=self.author, user=SimpleNamespace(id="account")),
                           post_id=self.post.pk, fields=fields)

    def test_restricted_material_edit_returns_whole_post_to_review(self):
        self.review()
        EntityPermission.objects.create(entity=self.author, realm=self.realm,
            permission=self.catalog["realm.post.publish"], effect="deny")
        previous_decisions = list(Approvals.objects.values_list('pk',flat=True))
        post, changed = self.edit({"caption": "A materially edited post"})
        self.assertTrue(changed)
        self.assertEqual(post.approval, 0)
        self.assertEqual(post.caption, "A materially edited post")
        # Verify the newly written audit row even when the host clock gives
        # consecutive decisions the same timestamp (UUID order is arbitrary).
        decision = Approvals.objects.exclude(pk__in=previous_decisions).get()
        self.assertEqual(decision.action, "edit")
        self.assertEqual(decision.previous_state["approval"], 1)
        self.assertTrue(decision.new_state["published"])

    def test_hidden_submissions_are_read_only_and_archive_does_not_trigger_review(self):
        for state in (0, 2):
            Post.objects.filter(pk=self.post.pk).update(approval=state)
            for fields in ({"caption": "Changed"}, {"is_archived": True}):
                with self.subTest(state=state, fields=fields), self.assertRaises(PermissionDenied):
                    self.edit(fields)
        Post.objects.filter(pk=self.post.pk).update(approval=1)
        post, changed = self.edit({"is_archived": True})
        self.assertTrue(changed)
        self.assertEqual(post.approval, 1)
        self.assertFalse(Approvals.objects.exists())

    def api(self, method="get", body=None, actor=None):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from community.approval_views import CommunityApprovalsView, CommunityPostReviewView
        request = getattr(APIRequestFactory(), method)("/approvals", body or {}, format="json")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        request.entity = actor or self.actor
        return (CommunityApprovalsView.as_view()(request, realm_id=self.realm.pk) if method == "get" else
                CommunityPostReviewView.as_view()(request, realm_id=self.realm.pk, post_id=self.post.pk))

    def test_review_api_uses_existing_post_cards_and_server_capability_guards(self):
        response = self.api()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["post"]["post_id"], self.post.pk)
        self.assertEqual(response.data["results"][0]["approval"], 0)
        self.assertEqual(self.api(actor=self.author).status_code, 403)
        response = self.api("post", {"action": "approve", "expected_approval": 0,
            "expected_version": self.post.date_posted.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.api().data["results"], [])

    def test_notification_target_filters_before_pagination_without_broadening_reviewer_scope(self):
        other = Post.objects.create(entity=self.author, approval=0, file_type='text', content_type='text')
        PostTag.objects.create(post=other, entity=self.realm.entity)
        response = self.api(body={'target_id': self.post.pk})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['post']['post_id'], self.post.pk)
        self.assertEqual(response.data['counts']['posts'], 2)
        self.assertEqual(self.api(body={'target_id': self.post.pk}, actor=self.author).status_code, 403)

    def test_restricted_archived_edit_cannot_create_a_submission_that_cannot_be_restored(self):
        self.review()
        EntityPermission.objects.create(entity=self.author, realm=self.realm,
            permission=self.catalog["realm.post.publish"], effect="deny")
        Post.objects.filter(pk=self.post.pk).update(is_archived=True)
        with self.assertRaises(ValidationError):
            self.edit({"caption":"Changed while archived"})
        self.post.refresh_from_db()
        self.assertEqual(self.post.approval, 1)

    def test_restricted_material_edit_cannot_archive_in_the_same_request(self):
        self.review()
        EntityPermission.objects.create(entity=self.author, realm=self.realm,
            permission=self.catalog["realm.post.publish"], effect="deny")
        with self.assertRaises(ValidationError):
            self.edit({"caption": "Edited and archived", "is_archived": True})
        self.post.refresh_from_db()
        self.assertEqual(self.post.approval, 1)
        self.assertFalse(self.post.is_archived)
        self.assertNotEqual(self.post.caption, "Edited and archived")

    def test_material_edit_version_advances_even_when_the_clock_has_not_ticked(self):
        self.review()
        with patch("newsfeed.services.post_editing.now", return_value=self.post.date_posted):
            post, changed = self.edit({"caption":"Changed in the same clock tick"})
        self.assertTrue(changed)
        self.assertGreater(post.updated_at, self.post.date_posted)
