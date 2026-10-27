"""Community management notices with real SQL and inert Mongo/event boundaries."""

# ruff: noqa: E402
import os
import unittest
from types import SimpleNamespace
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
from user.ext_models.mongomodels import Notification, Content, DateInfo
from user.ext_models import mongomodels


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
        self.enterContext(patch.object(Notification, "save", self.memory.save_document))
        self.publish = self.enterContext(
            patch("community.admin_notifications.RedisPubSubClient.publish_json")
        )
        self.enterContext(patch("community.admin_notifications.has_permission", return_value=True))
        self.elapsed = 0
        self.cooldowns = {}

        def cooldown(key, ttl):
            if self.cooldowns.get(key, -1) > self.elapsed:
                return False
            self.cooldowns[key] = self.elapsed + ttl
            return True

        self.cooldown = self.enterContext(patch(
            "community.admin_notifications.RedisPubSubClient.acquire_push_cooldown",
            side_effect=cooldown,
        ))
        self.enterContext(patch("community.admin_notifications._timestamp",
                               return_value="2026-10-27T00:00:00.000Z", create=True))
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

    def previews(self, admin="admin"):
        return [row for row in self.memory.rows
                if row.type == "community_admin_activity" and row.toUserID == admin]

    def inbox(self, admin="admin"):
        return [row for row in self.memory.rows
                if row.type == "community_admin_inbox" and row.toUserID == admin]

    def canonical_ids(self):
        return [row.notificationID for row in self.memory.rows
                if row.toUserID == "community"]

    def test_reuses_notification_schema_without_extra_mongo_documents(self):
        for name in ("CommunityAdminReceipt", "CommunityAdminDelivery"):
            self.assertFalse(hasattr(mongomodels, name))
        for field in ("preview_key", "activity_count", "activity_revision",
                      "activity_snapshot_version", "activity_delivery_id",
                      "activity_delivery_version"):
            self.assertNotIn(field, Notification._fields)

    def test_activity_is_immediate_and_personal_preview_saves_fixed_content(self):
        record_admin_activity(self.realm, self.notice("one"))
        self.assertEqual(len(self.canonical_ids()), 1)
        self.assertEqual(len(self.inbox()), 1)
        self.assertFalse(self.inbox()[0].isRead)
        preview = self.previews()[0]
        self.assertEqual(preview.content.details, "1 new activity in your community. Check them out.")
        self.assertEqual(preview.fromUserID, "community")
        self.assertEqual(self.cooldown.call_args.args[1], 300)
        events = [call.args[1]["event"] for call in self.publish.call_args_list]
        self.assertIn("community_notifications_changed", events)
        self.assertIn("community_admin_preview_changed", events)

    def test_redis_cooldown_replaces_one_preview_without_rewriting_earlier_content(self):
        record_admin_activity(self.realm, self.notice("one"))
        first = self.previews()[0]
        self.elapsed = 120
        record_admin_activity(self.realm, self.notice("two"))
        self.assertEqual(self.previews(), [first])
        self.assertTrue(first.content.details.startswith("1 new activity"))
        self.elapsed = 300
        record_admin_activity(self.realm, self.notice("three"))
        self.assertEqual(len(self.previews()), 1)
        latest = self.previews()[0]
        self.assertNotEqual(latest.notificationID, first.notificationID)
        self.assertEqual(latest.content.details, "3 new activities in your community. Check them out.")
        self.assertTrue(first.content.details.startswith("1 new activity"))

    def test_replays_do_not_duplicate_inbox_or_bypass_cooldown(self):
        record_admin_activity(self.realm, self.notice("one"))
        self.elapsed = 600
        record_admin_activity(self.realm, self.notice("one"))
        self.assertEqual(len(self.inbox()), 1)
        self.assertEqual(len(self.previews()), 1)
        self.assertEqual(self.cooldown.call_count, 1)

    def test_join_requests_use_the_same_generic_preview(self):
        notify_join_request(self.invite)
        notify_join_request(self.invite)
        self.assertEqual(len(self.inbox()), 1)
        self.assertEqual(len(self.previews()), 1)
        self.assertFalse(any(row.type == "community_join_request" and row.toUserID == "admin"
                             for row in self.memory.rows))
        settle_join_requests([str(self.invite.pk)])
        self.assertTrue(self.inbox()[0].isRead)
        self.assertTrue(self.previews()[0].isRead)
        self.assertTrue(self.previews()[0].content.details.startswith("1 new activity"))

    def test_each_admin_counts_their_own_unread_notifications(self):
        other = Entity.objects.create(id="other-admin", type="user")
        Member.objects.create(realm=self.realm, entity=other, added_by=self.admin, role="moderator")
        for reference in ("one", "two"):
            record_admin_activity(self.realm, self.notice(reference))
        acknowledge_activity(self.realm, self.admin, 0, self.canonical_ids())
        for reference in ("three", "four"):
            record_admin_activity(self.realm, self.notice(reference))
        self.elapsed = 300
        record_admin_activity(self.realm, self.notice("five"))
        self.assertTrue(self.previews()[0].content.details.startswith("3 new activities"))
        self.assertTrue(self.previews("other-admin")[0].content.details.startswith("5 new activities"))

    def test_exact_id_reads_preserve_activity_arriving_after_the_loaded_page(self):
        record_admin_activity(self.realm, self.notice("one"))
        loaded = self.canonical_ids()
        record_admin_activity(self.realm, self.notice("two"))
        self.assertTrue(acknowledge_activity(self.realm, self.admin, 0, loaded))
        self.assertEqual(sum(not row.isRead for row in self.inbox()), 1)
        self.assertFalse(self.previews()[0].isRead)
        # The later item is outside an older preview's saved time window.
        self.inbox()[1].date.date = "2026-10-27T00:01:00.000Z"
        acknowledge_activity(self.realm, self.admin, 0, loaded)
        self.assertTrue(self.previews()[0].isRead)

    def test_legacy_canonical_read_intent_prevents_later_unread_copy(self):
        from community.admin_notifications import _upsert_notice

        _upsert_notice(**self.notice("one"))
        acknowledge_activity(self.realm, self.admin, 0, self.canonical_ids())
        record_admin_activity(self.realm, self.notice("one"))
        self.assertEqual(len(self.inbox()), 1)
        self.assertTrue(self.inbox()[0].isRead)
        self.assertEqual(self.previews(), [])

    def test_redis_outage_never_blocks_inbox_and_never_writes_zero_preview(self):
        self.cooldown.side_effect = None
        self.cooldown.return_value = False
        record_admin_activity(self.realm, self.notice("one"))
        self.assertEqual(len(self.inbox()), 1)
        self.assertEqual(self.previews(), [])
        self.assertEqual(len(self.canonical_ids()), 1)

    def test_read_racing_preview_save_hides_it_without_changing_saved_count(self):
        def save(document, *args, **kwargs):
            for row in self.inbox():
                row.isRead = True
            return self.memory.save(document)

        with patch.object(Notification, "save", save):
            record_admin_activity(self.realm, self.notice("one"))
        preview = self.previews()[0]
        self.assertTrue(preview.isRead)
        self.assertTrue(preview.content.details.startswith("1 new activity"))

    def test_anonymous_activity_stores_no_actor_identity(self):
        record_admin_activity(self.realm, self.notice("one", anonymous=True))
        for row in self.memory.rows:
            self.assertEqual(row.fromUserID, "community")
            self.assertNotIn("maya", str(row.to_mongo()))

    def test_cancellation_updates_inbox_and_personal_preview(self):
        notify_join_request(self.invite)
        request = APIRequestFactory().post(
            "/api/realm/communities/club/leave", {"cancel_request": True}, format="json"
        )
        request.entity = self.actor
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with patch.object(InviteView, "_notify_requests_changed"):
            response = CommunityLeaveView.as_view()(request, realm_id="club")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.inbox()[0].isRead)
        self.assertTrue(self.previews()[0].isRead)

    def test_replacing_preview_preserves_ordinary_notices_and_other_admins(self):
        other = Notification(
            notificationID="ordinary", referenceID="follow", toUserID="admin",
            fromUserID="actor", type="follow", content=Content(headline="Follow", details="Follow"),
            date=DateInfo(date="2026-10-27T00:00:00.000Z"),
        )
        self.memory.rows.append(other)
        record_admin_activity(self.realm, self.notice("one"))
        self.elapsed = 300
        record_admin_activity(self.realm, self.notice("two"))
        self.assertIn(other, self.memory.rows)
        self.assertEqual(len(self.previews()), 1)

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
        self.assertEqual(response.data["activity_revision"], 0)
        Comment.objects.filter(pk="comment").update(is_anonymous=True)
        item = self.get_inbox().data["results"][0]
        self.assertIsNone(item["actor"])
        self.assertTrue(item["is_anonymous"])
        self.assertNotIn("maya", str(item))
        Post.objects.filter(pk="photo").update(entity=self.actor)
        self.assertEqual(self.get_inbox().data["results"], [])

