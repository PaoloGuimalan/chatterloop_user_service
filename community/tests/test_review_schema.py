from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from community import models
from entity.tests.test_realm_role_policy import RealmRolePolicyFixture
from newsfeed.models import Post


class ReviewSchemaTests(RealmRolePolicyFixture, TestCase):
    def test_posts_default_approved_and_invites_use_status(self):
        post = Post.objects.create(entity=self.actor, file_type="text", content_type="text")
        invite = models.Invite.objects.create(realm=self.realm, created_by=self.actor)
        self.assertEqual(post.approval, 1)
        self.assertEqual(invite.status, "pending")
        self.assertNotIn("approval", {f.name for f in models.Invite._meta.fields})
        with self.assertRaises(IntegrityError), transaction.atomic():
            Post.objects.filter(pk=post.pk).update(approval=3)

    def test_decision_audit_is_immutable_but_delivery_progress_can_be_saved(self):
        decision = models.Approvals.objects.create(realm=self.realm, actor=self.actor,
            target_kind="post", target_id="post", action="reject", previous_state={"approval": 0},
            new_state={"approval": 2}, reason="Outside the community topic")
        decision.delivery_state = {"author_notice": "sent"}
        decision.save(update_fields=["delivery_state"])
        decision.reason = "Changed reason"
        with self.assertRaises(ValidationError):
            decision.save()
        decision.refresh_from_db()
        self.assertEqual(decision.reason, "Outside the community topic")
        self.assertEqual(decision.delivery_state, {"author_notice": "sent"})
