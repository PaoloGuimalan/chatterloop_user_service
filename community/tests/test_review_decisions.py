from datetime import timedelta
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


class ReviewDecisionTests(RealmRolePolicyFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch("newsfeed.serializers.get_preview", return_value=None))
        self.author = Entity.objects.create(type="user")
        self.post = Post.objects.create(entity=self.author, approval=2, caption="Rejected caption", file_type="text", content_type="text")
        PostTag.objects.create(post=self.post, entity=self.realm.entity)
        self.decision = Approvals.objects.create(realm=self.realm, actor=self.actor,
            target_kind="post", target_id=self.post.pk, action="reject", reason="Outside our topic",
            content_version=self.post.date_posted, previous_state={"approval":0}, new_state={"approval":2})

    def get(self, actor=None):
        views = import_module("community.review_views")
        request = APIRequestFactory().get("/api/entity/realm-review/decision")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        request.entity = actor or self.author
        return views.RealmReviewDetailView.as_view()(request, decision_id=self.decision.pk)

    def test_author_and_current_reviewer_can_read_but_visitors_cannot(self):
        self.assertEqual(self.get().status_code, 200)
        response = self.get(self.actor)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["source"], "community")
        self.assertEqual(response.data["data"]["decision"]["reason"], "Outside our topic")
        self.assertNotIn("categories", response.data["data"]["moderation"])
        visitor = Entity.objects.create(type="user")
        self.assertEqual(self.get(visitor).status_code, 404)
        Member.objects.filter(entity=self.actor, realm=self.realm).delete()
        self.assertEqual(self.get(self.actor).status_code, 404)
        self.assertEqual(self.get().status_code, 200)

    def test_platform_removed_post_keeps_only_a_placeholder_and_the_immutable_reason(self):
        PostReference.objects.create(post=self.post, reference="https://example.invalid/private.jpg", reference_media_type="image")
        Post.objects.filter(pk=self.post.pk).update(deleted_at=now(), details={"private":"removed details"})
        response = self.get()
        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertEqual(data["decision"]["reason"], "Outside our topic")
        self.assertEqual(data["content"]["caption"], "")
        self.assertEqual(data["content"]["references"], [])
        self.assertIsNone(data["content"]["details"])
        self.assertIsNone(data["content"]["link_preview"])
        self.assertTrue(data["content"]["is_removed"])
        self.assertNotIn("private.jpg", str(data))

    def test_changed_content_is_labeled_as_current_and_not_a_historical_snapshot(self):
        Post.objects.filter(pk=self.post.pk).update(caption="Later content", updated_at=self.post.date_posted + timedelta(seconds=1))
        response = self.get()
        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertTrue(data["content_is_current"])
        self.assertFalse(data["matches_reviewed_version"])
        self.assertEqual(data["content"]["caption"], "Later content")
        self.assertEqual(data["decision"]["reason"], "Outside our topic")

    def test_page_decision_belongs_to_the_page_not_its_personal_administrator(self):
        page = self.make_realm("Page")
        Post.objects.filter(pk=self.post.pk).update(entity=page.entity)
        Member.objects.create(entity=self.author, realm=page, added_by=self.actor, role=page.owned_roles.get(code="owner"))
        self.assertEqual(self.get(self.author).status_code, 404)
        self.assertEqual(self.get(page.entity).status_code, 200)

    def test_hard_deleted_post_cannot_be_claimed_by_an_unverified_former_author(self):
        self.post.delete()
        self.assertEqual(self.get().status_code, 404)
        response = self.get(self.actor)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["data"]["content"])
        self.assertEqual(response.data["data"]["decision"]["reason"], "Outside our topic")

    def test_a_historical_decision_does_not_expose_the_authors_archive_to_reviewers(self):
        Post.objects.filter(pk=self.post.pk).update(is_archived=True)
        self.assertEqual(self.get(self.actor).data["data"]["content"]["caption"], "")
        self.assertEqual(self.get().data["data"]["content"]["caption"], "Rejected caption")
