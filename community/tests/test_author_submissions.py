from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase
from django.utils.timezone import now
from rest_framework.test import APIRequestFactory, force_authenticate
from community.models import Approvals, Member
from entity.models import Entity
from entity.tests.test_realm_role_policy import RealmRolePolicyFixture
from newsfeed.models import Post, PostReference, PostTag


class AuthorSubmissionTests(RealmRolePolicyFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch("newsfeed.serializers.get_preview", return_value=None))
        self.realm.is_private = True
        self.realm.save(update_fields=["is_private"])
        self.author = Entity.objects.create(type="user")
        self.post = self.make_post(self.author, state=0, anonymous=True)

    def make_post(self, entity, *, state=0, community=True, anonymous=False, **fields):
        post = Post.objects.create(entity=entity, approval=state, is_anonymous=anonymous,
            file_type="text", content_type="text", caption="Pending caption", **fields)
        if community:
            PostTag.objects.create(post=post, entity=self.realm.entity)
        return post

    def get(self, actor=None, post_id=None, params=None):
        views = import_module("newsfeed.submission_views")
        request = APIRequestFactory().get("/api/newsfeed/submissions", params or {})
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        request.entity = actor or self.author
        return (views.AuthorSubmissionDetailView.as_view()(request, post_id=post_id) if post_id else
                views.AuthorSubmissionsView.as_view()(request))

    def test_list_contains_only_acting_authors_hidden_community_posts(self):
        rejected = self.make_post(self.author, state=2)
        self.make_post(self.author, state=1)
        self.make_post(self.author, state=0, community=False)
        self.make_post(self.actor, state=0)
        removed = self.make_post(self.author, state=2, deleted_at=now())
        response = self.get(params={"author": self.actor.pk})
        self.assertEqual(response.status_code, 200)
        rows = response.data["results"]
        self.assertEqual({row["post"]["post_id"] for row in rows}, {self.post.pk, rejected.pk})
        anonymous = next(row for row in rows if row["post"]["post_id"] == self.post.pk)
        self.assertTrue(anonymous["post"]["is_owner"])
        self.assertIsNone(anonymous["post"]["entity"])
        self.assertTrue(anonymous["read_only"])
        self.assertEqual(self.get(post_id=removed.pk).status_code, 404)

    def test_page_submissions_require_acting_as_that_page_even_for_personal_owner(self):
        page = self.make_realm("Page")
        page.type = "page"
        page.save(update_fields=["type"])
        post = self.make_post(page.entity)
        Member.objects.create(entity=self.actor, realm=page, added_by=self.actor, role=page.owned_roles.get(code="owner"))
        self.assertEqual(self.get(actor=self.actor, post_id=post.pk).status_code, 404)
        self.assertEqual(self.get(actor=page.entity, post_id=post.pk).status_code, 200)
        self.assertEqual(self.get(actor=self.actor).data["results"], [])

    def test_former_member_author_can_see_current_rejection_reason_but_visitors_cannot(self):
        Post.objects.filter(pk=self.post.pk).update(approval=2)
        self.post.refresh_from_db()
        decision = Approvals.objects.create(realm=self.realm, actor=self.actor, target_kind="post", target_id=self.post.pk,
            action="reject", reason="Outside our topic", new_state={"approval":2}, content_version=self.post.date_posted)
        response = self.get(post_id=self.post.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["result"]["decision"]["id"], str(decision.pk))
        self.assertEqual(response.data["result"]["decision"]["reason"], "Outside our topic")
        self.assertEqual(self.get(actor=self.actor, post_id=self.post.pk).status_code, 404)

    def test_approval_or_deletion_immediately_removes_the_author_submission(self):
        Post.objects.filter(pk=self.post.pk).update(approval=1)
        self.assertEqual(self.get().data["results"], [])
        self.assertEqual(self.get(post_id=self.post.pk).status_code, 404)

    def test_shared_submission_does_not_reveal_private_or_archived_source(self):
        for archived in (False, True):
            if archived:
                type(self.realm).objects.filter(pk=self.realm.pk).update(is_private=False)
            source = self.make_post(self.actor, state=1, is_archived=archived)
            shared = self.make_post(self.author, state=0)
            Post.objects.filter(pk=shared.pk).update(file_type="shared_post", content_type="shared_post", is_shared=True)
            PostReference.objects.create(post=shared, reference=source.pk, reference_media_type="shared_post")
            response = self.get(post_id=shared.pk)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.data["result"]["shared_preview"]["available"])
            self.assertNotIn("caption", response.data["result"]["shared_preview"])

    def test_card_serialization_keeps_query_count_bounded_as_the_list_grows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as one:
            self.assertEqual(len(self.get().data["results"]), 1)
        for _ in range(9):
            self.make_post(self.author)
        with CaptureQueriesContext(connection) as ten:
            self.assertEqual(len(self.get().data["results"]), 10)
        self.assertLessEqual(len(ten), len(one) + 1)

    def test_old_rejection_reason_does_not_attach_to_a_newer_submission(self):
        Approvals.objects.create(realm=self.realm, actor=self.actor, target_kind="post", target_id=self.post.pk,
            action="reject", reason="Old reason", new_state={"approval":2}, content_version=self.post.date_posted)
        Post.objects.filter(pk=self.post.pk).update(updated_at=now(), approval=0)
        self.assertIsNone(self.get(post_id=self.post.pk).data["result"]["decision"])

    def test_unauthenticated_requests_are_rejected_before_resolving_submissions(self):
        from newsfeed.submission_views import AuthorSubmissionsView
        response = AuthorSubmissionsView.as_view()(APIRequestFactory().get("/submissions"))
        self.assertIn(response.status_code, (401,403))

    def test_a_public_share_cannot_supply_a_thumbnail_from_a_nested_private_source(self):
        public = self.make_realm("Public")
        hidden = self.make_post(self.actor, state=1)
        PostReference.objects.create(post=hidden, reference="https://example.invalid/secret.jpg", reference_media_type="image")
        outer = self.make_post(self.actor, state=1)
        Post.objects.filter(pk=outer.pk).update(file_type="shared_post", content_type="shared_post", is_shared=True)
        outer.tagging.all().delete()
        PostTag.objects.create(post=outer, entity=public.entity)
        PostReference.objects.create(post=outer, reference=hidden.pk, reference_media_type="shared_post")
        Post.objects.filter(pk=self.post.pk).update(file_type="shared_post", content_type="shared_post", is_shared=True)
        PostReference.objects.create(post=self.post, reference=outer.pk, reference_media_type="shared_post")
        data = self.get(post_id=self.post.pk).data["result"]["shared_preview"]
        self.assertTrue(data["available"])
        self.assertIsNone(data["thumbnail"])
        self.assertNotIn("secret.jpg", str(data))
