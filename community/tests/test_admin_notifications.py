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
from user.ext_models.mongomodels import Notification


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

    def test_join_request_has_one_inbox_row_and_one_personal_notice(self):
        notify_join_request(self.invite)
        notify_join_request(self.invite)
        self.assertEqual(len(self.memory.rows), 2)
        personal = next(row for row in self.memory.rows if row.toUserID == "admin")
        self.assertEqual(personal.fromUserID, "actor")
        self.assertTrue(personal.content.details.startswith("@maya wants to join"))
        self.assertIn("wants to join Garden Club", personal.content.details)
        self.assertEqual(personal.target.type, "community_notifications")
        self.assertTrue(
            any(
                item["route"] == "/realms/club/notifications"
                for item in personal.redirects
            )
        )
        self.assertFalse(personal.isRead)
        settle_join_requests([str(self.invite.pk)])
        self.assertTrue(
            all(row.referenceStatus and row.isRead for row in self.memory.rows)
        )

    def test_general_activity_is_coalesced_and_counts_only_unread_new_events(self):
        record_admin_activity(self.realm, self.notice("one"))
        record_admin_activity(self.realm, self.notice("one"))
        record_admin_activity(self.realm, self.notice("two", anonymous=True))
        summary = next(
            row for row in self.memory.rows if row.type == "community_admin_activity"
        )
        self.assertEqual(summary.activity_count, 2)
        self.assertEqual(summary.activity_revision, 2)
        self.assertFalse(summary.isRead)
        self.assertEqual(summary.fromUserID, "community")
        self.assertNotIn("maya", summary.content.details)
        summary.isRead = True
        for reference in ("three", "four", "five"):
            record_admin_activity(self.realm, self.notice(reference))
        self.assertEqual(summary.activity_count, 3)
        self.assertFalse(summary.isRead)

    def test_acknowledgement_cannot_clear_an_activity_arriving_during_loading(self):
        record_admin_activity(self.realm, self.notice("one"))
        record_admin_activity(self.realm, self.notice("two"))
        self.assertFalse(acknowledge_activity(self.realm, self.admin, 1))
        summary = next(
            row for row in self.memory.rows if row.type == "community_admin_activity"
        )
        self.assertEqual(summary.activity_count, 2)
        self.assertTrue(acknowledge_activity(self.realm, self.admin, 2))
        self.assertTrue(summary.isRead)
        self.assertEqual(summary.activity_count, 0)
        record_admin_activity(self.realm, self.notice("three"))
        self.assertEqual(summary.activity_count, 1)

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
        self.assertEqual(
            next(
                row
                for row in self.memory.rows
                if row.type == "community_admin_activity"
            ).activity_count,
            1,
        )

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
        Member.objects.create(
            realm=self.realm, entity=other, added_by=self.admin, role="moderator"
        )
        record_admin_activity(self.realm, self.notice("one"))
        record_admin_activity(self.realm, self.notice("two"))
        first = next(
            row
            for row in self.memory.rows
            if row.type == "community_admin_activity" and row.toUserID == "admin"
        )
        second = next(
            row
            for row in self.memory.rows
            if row.type == "community_admin_activity" and row.toUserID == "other-admin"
        )
        first.isRead = True
        for reference in ("three", "four", "five"):
            record_admin_activity(self.realm, self.notice(reference))
        self.assertEqual(first.activity_count, 3)
        self.assertEqual(second.activity_count, 5)
        acknowledge_activity(self.realm, self.admin, first.activity_revision)
        self.assertEqual(second.activity_count, 5)
        self.assertFalse(second.isRead)
