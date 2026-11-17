from unittest.mock import patch
from django.test import TestCase
from community.models import Approvals
from community.review_delivery import publish_review_effects
from entity.tests.test_realm_role_policy import RealmRolePolicyFixture
from newsfeed.models import Post, PostReference, PostScore, PostTag
from user_service.services.rabbitmq import Queues


class ReviewDeliveryTests(RealmRolePolicyFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.post = Post.objects.create(entity=self.actor, approval=1, file_type="text", content_type="text")
        PostTag.objects.create(post=self.post, entity=self.realm.entity)
        self.decision = Approvals.objects.create(realm=self.realm, actor=self.actor, target_kind="post",
            target_id=self.post.pk, action="approve", previous_state={"approval": 0}, new_state={"approval": 1},
            content_version=self.post.date_posted, delivery_state={"first_publication": True, "status": "pending"})
        self.notices = self.enterContext(patch("community.review_delivery.deliver_review_notices"))
        self.log = self.enterContext(patch("community.review_delivery.log_approved_share"))

    def test_broker_failure_keeps_committed_work_and_retry_skips_sent_stage(self):
        sent = []
        def publish(queue, payload):
            sent.append(queue)
            return len(sent) != 2
        with patch("community.review_delivery.RabbitMQClient.publish", side_effect=publish):
            self.assertFalse(publish_review_effects(self.decision.pk))
        self.decision.refresh_from_db()
        self.assertEqual(self.decision.delivery_state["score"], "sent")
        with patch("community.review_delivery.RabbitMQClient.publish", return_value=True) as retry:
            self.assertTrue(publish_review_effects(self.decision.pk))
            self.assertNotIn(Queues.CREATE_POST_SCORE_FOR_NEW_POST, [call.args[0] for call in retry.call_args_list])
        self.decision.refresh_from_db()
        self.assertEqual(self.decision.delivery_state["status"], "complete")

    def test_share_counter_is_applied_once_even_when_refresh_publish_fails(self):
        source = Post.objects.create(entity=self.actor, file_type="text", content_type="text")
        PostScore.objects.create(post=source, shares_count=3)
        Post.objects.filter(pk=self.post.pk).update(is_shared=True)
        PostReference.objects.create(post=self.post, reference=source.pk, reference_media_type="post")
        def publish(queue, payload):
            return queue != Queues.UPDATE_RANKING_SCORE
        with patch("community.review_delivery.RabbitMQClient.publish", side_effect=publish):
            self.assertFalse(publish_review_effects(self.decision.pk))
        self.assertEqual(PostScore.objects.get(post=source).shares_count, 4)
        with patch("community.review_delivery.RabbitMQClient.publish", return_value=True):
            self.assertTrue(publish_review_effects(self.decision.pk))
            self.assertTrue(publish_review_effects(self.decision.pk))
        self.assertEqual(PostScore.objects.get(post=source).shares_count, 4)
        self.assertEqual(self.log.call_count, 1)

    def test_taken_down_post_does_not_send_stale_publication_work(self):
        Post.objects.filter(pk=self.post.pk).update(approval=2)
        with patch("community.review_delivery.RabbitMQClient.publish") as publish:
            self.assertFalse(publish_review_effects(self.decision.pk))
        publish.assert_not_called()
        self.notices.assert_not_called()
        self.decision.refresh_from_db()
        self.assertEqual(self.decision.delivery_state["status"], "obsolete")

    def test_retry_command_is_bounded_and_only_selects_pending_post_decisions(self):
        from io import StringIO
        from django.core.management import call_command
        from django.core.management.base import CommandError
        output = StringIO()
        with patch("community.management.commands.retry_realm_review_delivery.publish_review_effects", return_value=True) as publish:
            call_command("retry_realm_review_delivery", limit=1, stdout=output)
        self.assertEqual(publish.call_count, 1)
        self.assertIn("Processed 1 decisions", output.getvalue())
        with self.assertRaises(CommandError):
            call_command("retry_realm_review_delivery", limit=1001)

    def test_reapproval_repairs_fanout_without_new_score_or_announcement(self):
        self.decision.delivery_state = {"first_publication": False, "status": "pending"}
        self.decision.save(update_fields=["delivery_state"])
        with patch("community.review_delivery.RabbitMQClient.publish", return_value=True) as publish:
            self.assertTrue(publish_review_effects(self.decision.pk))
        self.assertEqual([call.args[0] for call in publish.call_args_list], [Queues.BULK_FANOUT_TO_CACHE])
        self.assertFalse(self.notices.call_args.kwargs["first_publication"])
