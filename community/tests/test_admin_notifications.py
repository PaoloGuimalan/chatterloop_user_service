"""Community management notices with real SQL and inert Mongo/event boundaries."""

# ruff: noqa: E402
import os
import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from django.db import connection
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from bot.models import Bot
from community.models import Invite, Member, Realm
from community.community_views import CommunityLeaveView
from community.views import InviteView
from community.admin_notifications import (
    CommunityNotificationsView,
    notify_join_request,
    settle_join_requests,
    record_admin_activity,
    acknowledge_activity,
)
from community.notifications import notification_description
from community.tests.notification_memory import NotificationMemory
from entity.models import Entity
from newsfeed.models import Post, PostTag, Comment, Reaction, CommentReaction, Emoji
from user.models import Account
from user.ext_models.mongomodels import (
    Notification, CommunityAdminReceipt, CommunityAdminDelivery, Content, DateInfo,
)


class AdminNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.models = (
            Entity,
            Account,
            Bot,
            Realm,
            Member,
            Invite,
            Post,
            PostTag,
            Comment,
            Emoji,
            Reaction,
            CommentReaction,
        )
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
        self.enterContext(override_settings(ALLOWED_HOSTS=["testserver"]))
        self.memory = NotificationMemory()
        self.enterContext(patch.object(Notification, "objects", self.memory))
        self.receipts = NotificationMemory(CommunityAdminReceipt)
        self.deliveries = NotificationMemory(CommunityAdminDelivery)
        self.enterContext(patch.object(CommunityAdminReceipt, "objects", self.receipts))
        self.enterContext(patch.object(CommunityAdminDelivery, "objects", self.deliveries))
        self.enterContext(
            patch("community.admin_notifications.RedisPubSubClient.publish_json")
        )
        self.admin, self.actor, self.destination = [
            Entity.objects.create(id=key, type=kind)
            for key, kind in (
                ("admin", "user"),
                ("actor", "user"),
                ("community", "realm"),
            )
        ]
        Account.objects.bulk_create(
            [
                Account(
                    entity=self.actor,
                    username="maya",
                    email="maya@example.invalid",
                    first_name="Maya",
                    last_name="Chen",
                )
            ]
        )
        self.realm = Realm(
            id="club",
            realm_id="club",
            entity=self.destination,
            created_by=self.admin,
            name="Garden Club",
            slug="garden-club",
            type="community",
        )
        Realm.objects.bulk_create([self.realm])
        Member.objects.create(
            realm=self.realm, entity=self.admin, added_by=self.admin, role="admin"
        )
        self.invite = Invite.objects.create(
            realm=self.realm,
            target_entity=self.actor,
            created_by=self.actor,
            kind="request",
        )

    def tearDown(self):
        with connection.constraint_checks_disabled():
            for model in reversed(self.models):
                with connection.cursor() as cursor:
                    cursor.execute(f'DELETE FROM "{model._meta.db_table}"')

    def notice(self, reference="comment", anonymous=False):
        return dict(
            referenceID=reference,
            toUserID="community",
            content_headline="Post Comment",
            type="post_comment",
            **notification_description(
                community_entity_id="community",
                actor_name="@maya",
                actor_entity_id="actor",
                is_anonymous=anonymous,
                verb="commented",
                post_id="photo",
                anchor=reference,
                post_author_id="community",
                recipient_id="community",
            ),
        )

    def test_join_requests_reach_inbox_immediately_and_queue_only_generic_personal_preview(self):
        notify_join_request(self.invite)
        notify_join_request(self.invite)
        self.assertEqual(len(self.memory.rows), 1)
        inbox = self.memory.rows[0]
        self.assertEqual(inbox.toUserID, "community")
        self.assertEqual(inbox.fromUserID, "actor")
        self.assertIn("wants to join Garden Club", inbox.content.details)
        self.assertFalse(inbox.isRead)
        self.assertEqual(len(self.receipts.rows), 1)
        self.assertEqual(self.receipts.rows[0].admin_entity_id, "admin")
        self.assertFalse(self.receipts.rows[0].is_read)
        self.assertIsNotNone(self.deliveries.rows[0].due_at)
        settle_join_requests([str(self.invite.pk)])
        self.assertTrue(inbox.isRead)
        self.assertTrue(self.receipts.rows[0].is_read)

    def test_activity_receipts_are_deduplicated_without_live_personal_summary_writes(self):
        record_admin_activity(self.realm, self.notice("one"))
        record_admin_activity(self.realm, self.notice("one"))
        record_admin_activity(self.realm, self.notice("two", anonymous=True))
        self.assertFalse(any(row.type == "community_admin_activity" for row in self.memory.rows))
        self.assertEqual(len(self.receipts.rows), 2)
        state = self.deliveries.rows[0]
        self.assertEqual(state.revision, 2)
        first_due = state.due_at
        for reference in ("three", "four", "five"):
            record_admin_activity(self.realm, self.notice(reference))
        self.assertEqual(len(self.receipts.rows), 5)
        self.assertEqual(state.due_at, first_due)
        self.assertEqual(state.generation, 1)

    def test_acknowledgement_cannot_clear_an_activity_arriving_during_loading(self):
        record_admin_activity(self.realm, self.notice("one"))
        loaded = self.memory.rows[0].notificationID
        record_admin_activity(self.realm, self.notice("two"))
        self.assertTrue(acknowledge_activity(self.realm, self.admin, 1, [loaded]))
        self.assertEqual(sum(not row.is_read for row in self.receipts.rows), 1)
        record_admin_activity(self.realm, self.notice("three"))
        self.assertEqual(sum(not row.is_read for row in self.receipts.rows), 2)

    def get_inbox(self, permitted=True, method="get", data=None):
        request = getattr(APIRequestFactory(), method)(
            "/api/realm/communities/club/notifications", data or {}, format="json"
        )
        request.entity = self.admin
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with patch(
            "community.admin_notifications.has_permission", return_value=permitted
        ):
            return CommunityNotificationsView.as_view()(request, realm_id="club")

    def test_inbox_requires_a_current_manager_and_permission(self):
        notify_join_request(self.invite)
        self.assertEqual(self.get_inbox(permitted=False).status_code, 403)
        Member.objects.filter(entity=self.admin).update(role="member")
        self.assertEqual(self.get_inbox().status_code, 403)

    def test_inbox_rechecks_current_activity_anonymity_and_community_authorship(self):
        Post.objects.bulk_create(
            [
                Post(
                    post_id="photo",
                    entity=self.destination,
                    from_system=True,
                    details={"community_media_update": True},
                    file_type="photo",
                    content_type="photo",
                )
            ]
        )
        Comment.objects.bulk_create(
            [
                Comment(
                    comment_id="comment",
                    post_id="photo",
                    entity=self.actor,
                    text="Hello",
                )
            ]
        )
        record_admin_activity(self.realm, self.notice())
        response = self.get_inbox()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["actor"]["id"], "actor")
        self.assertEqual(response.data["activity_revision"], 1)
        Comment.objects.filter(pk="comment").update(is_anonymous=True)
        item = self.get_inbox().data["results"][0]
        self.assertIsNone(item["actor"])
        self.assertTrue(item["is_anonymous"])
        self.assertNotIn("maya", str(item))
        Post.objects.filter(pk="photo").update(entity=self.actor)
        self.assertEqual(self.get_inbox().data["results"], [])

    def test_media_activity_reaches_management_without_an_individual_push(self):
        from community.notifications import notify_community_activity

        post = Post(
            post_id="photo",
            entity=self.destination,
            from_system=True,
            details={"community_media_update": True},
            file_type="photo",
            content_type="photo",
        )
        Post.objects.bulk_create([post])
        PostTag.objects.create(post=post, entity=self.destination)
        Comment.objects.bulk_create(
            [
                Comment(
                    comment_id="comment",
                    post=post,
                    entity=self.actor,
                    text="Hello",
                    is_anonymous=True,
                )
            ]
        )
        with patch("user_service.services.rabbitmq.RabbitMQClient.publish") as push:
            self.assertTrue(
                notify_community_activity(
                    post,
                    self.actor,
                    "comment",
                    [self.destination.pk],
                    is_anonymous=True,
                    notification_type="post_comment",
                    anchor="comment",
                )
            )
        push.assert_not_called()
        canonical = next(row for row in self.memory.rows if row.type == "post_comment")
        self.assertEqual(canonical.toUserID, "community")
        self.assertEqual(canonical.fromUserID, "community")
        self.assertNotIn("maya", str(canonical.to_mongo()))
        self.assertEqual(len(self.receipts.rows), 1)
        self.assertFalse(any(row.toUserID == "admin" for row in self.memory.rows))

    def test_request_cancellation_settles_both_inbox_and_personal_projection(self):
        notify_join_request(self.invite)
        request = APIRequestFactory().post(
            "/api/realm/communities/club/leave", {"cancel_request": True}, format="json"
        )
        request.entity = self.actor
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with patch.object(InviteView, "_notify_requests_changed"):
            response = CommunityLeaveView.as_view()(request, realm_id="club")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            all(row.referenceStatus and row.isRead for row in self.memory.rows)
        )
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, "revoked")

    def test_each_administrators_unread_count_is_independent(self):
        other = Entity.objects.create(id="other-admin", type="user")
        Member.objects.create(realm=self.realm, entity=other, added_by=self.admin, role="moderator")
        record_admin_activity(self.realm, self.notice("one"))
        record_admin_activity(self.realm, self.notice("two"))
        loaded = [row.notificationID for row in self.memory.rows]
        acknowledge_activity(self.realm, self.admin, 2, loaded)
        for reference in ("three", "four", "five"):
            record_admin_activity(self.realm, self.notice(reference))
        def unread(actor):
            return sum(not row.is_read for row in self.receipts.rows if row.admin_entity_id == actor)
        self.assertEqual(unread("admin"), 3)
        self.assertEqual(unread("other-admin"), 5)

    def test_window_deadline_is_five_minutes_and_inbox_events_are_immediate_only(self):
        now = datetime(2026, 10, 27, 0, 0)
        with patch("community.admin_notifications.datetime") as clock:
            clock.utcnow.return_value = now
            clock.now.return_value = now
            with patch("community.admin_notifications.RedisPubSubClient.publish_json") as publish:
                record_admin_activity(self.realm, self.notice("one"))
        self.assertEqual(self.deliveries.rows[0].due_at, now + timedelta(minutes=5))
        events = [call.args[1]["event"] for call in publish.call_args_list]
        self.assertEqual(events, ["community_notifications_changed"])

    def test_read_hides_only_fully_covered_preview_and_never_changes_saved_content(self):
        record_admin_activity(self.realm, self.notice("one"))
        record_admin_activity(self.realm, self.notice("two"))
        for receipt in self.receipts.rows:
            receipt.preview_id = "batch-1"
        preview = Notification(
            notificationID="preview", referenceID="community", toUserID="admin",
            fromUserID="community", community_entity_id="community",
            type="community_admin_activity", activity_count=2, activity_revision=1,
            activity_delivery_id="batch-1", activity_snapshot_version=1,
            content=Content(headline="Activity", details="2 new activities, saved."),
            date=DateInfo(date="2026-10-27T00:05:00Z"),
        )
        self.memory.rows.append(preview)
        ids = [row.notificationID for row in self.memory.rows if row.type == "post_comment"]
        acknowledge_activity(self.realm, self.admin, 1, ids[:1])
        self.assertFalse(preview.isRead)
        acknowledge_activity(self.realm, self.admin, 1, ids[1:])
        self.assertTrue(preview.isRead)
        self.assertEqual(preview.activity_count, 2)
        self.assertEqual(preview.content.details, "2 new activities, saved.")

    def test_read_transport_validates_exact_ids_and_legacy_reads_cannot_clear_unseen_activity(self):
        record_admin_activity(self.realm, self.notice("one"))
        response = self.get_inbox(method="post", data={"activity_revision": 1})
        self.assertFalse(response.data["acknowledged"])
        self.assertFalse(self.receipts.rows[0].is_read)
        for value in ("one", [None], ["x" * 201], ["x"] * 1001):
            response = self.get_inbox(method="post", data={"activity_revision": 1, "notification_ids": value})
            self.assertEqual(response.status_code, 400)
        response = self.get_inbox(method="post", data={"activity_revision": 1,
                                   "notification_ids": [self.memory.rows[0].notificationID]})
        self.assertTrue(response.data["acknowledged"])
        self.assertTrue(self.receipts.rows[0].is_read)

    def test_read_before_receipt_creation_is_retained(self):
        from community import admin_notifications as module

        original = module._queue_receipt

        def read_then_queue(realm, recipient, notification_id):
            acknowledge_activity(realm, self.admin, 0, [notification_id])
            original(realm, recipient, notification_id)

        with patch.object(module, "_queue_receipt", side_effect=read_then_queue):
            record_admin_activity(self.realm, self.notice("one"))
        self.assertEqual(len(self.receipts.rows), 1)
        self.assertTrue(self.receipts.rows[0].is_read)
        self.assertEqual(len(self.deliveries.rows), 0)

    def test_read_intent_cannot_be_created_for_another_community_or_unknown_notice(self):
        record_admin_activity(self.realm, self.notice("one"))
        notice = self.memory.rows[0]
        notice.toUserID = "another-community"
        acknowledge_activity(self.realm, self.admin, 0, [notice.notificationID, "unknown"])
        self.assertFalse(self.receipts.rows[0].is_read)
