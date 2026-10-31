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
from entity.models import Entity, Role
from newsfeed.models import Post, PostTag, Comment, Reaction, CommentReaction, Emoji
from user.models import Account
from user.ext_models.mongomodels import Notification, Content, DateInfo
from user.ext_models import mongomodels


def _role(name):
    return Role.objects.get_or_create(name=name, is_default=True)[0]


class AdminNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.models = (
            Entity,
            Role,
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
        for name in ("owner", "admin", "moderator", "member"):
            _role(name)
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
            realm=self.realm, entity=self.admin, added_by=self.admin, role=_role("admin")
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
        self.assertNotIn("community_entity_id", Notification._fields)
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
        self.assertEqual(preview.toUserID, "admin")
        self.assertEqual(preview.fromUserID, "community")
        for row in self.memory.rows:
            self.assertNotIn("community_entity_id", row.to_mongo())
        self.assertEqual(preview.content.details, "1 new activity in your community. Check them out.")
        self.assertEqual(preview.fromUserID, "community")
        self.cooldown.assert_not_called()
        events = [call.args[1]["event"] for call in self.publish.call_args_list]
        self.assertIn("community_notifications_changed", events)
        self.assertIn("community_admin_preview_changed", events)

    def test_each_activity_immediately_replaces_preview_with_saved_unread_total(self):
        record_admin_activity(self.realm, self.notice("one"))
        first = self.previews()[0]
        first_id = first.notificationID
        first_text = first.content.details
        record_admin_activity(self.realm, self.notice("two"))
        second = self.previews()[0]
        self.assertNotEqual(first_id, second.notificationID)
        self.assertTrue(first_text.startswith("1 new activity"))
        self.assertTrue(second.content.details.startswith("2 new activities"))
        record_admin_activity(self.realm, self.notice("three"))
        self.assertEqual(len(self.previews()), 1)
        self.assertTrue(self.previews()[0].content.details.startswith("3 new activities"))
        self.cooldown.assert_not_called()

    def test_replays_do_not_duplicate_inbox_or_bypass_cooldown(self):
        record_admin_activity(self.realm, self.notice("one"))
        self.elapsed = 600
        record_admin_activity(self.realm, self.notice("one"))
        self.assertEqual(len(self.inbox()), 1)
        self.assertEqual(len(self.previews()), 1)
        self.cooldown.assert_not_called()

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
        Member.objects.create(realm=self.realm, entity=other, added_by=self.admin, role=_role("moderator"))
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

    def test_preview_does_not_depend_on_a_redis_cooldown(self):
        self.cooldown.side_effect = AssertionError("Preview must not use cooldown")
        record_admin_activity(self.realm, self.notice("one"))
        self.assertEqual(len(self.inbox()), 1)
        self.assertEqual(len(self.previews()), 1)
        self.cooldown.assert_not_called()

    def test_overlapping_preview_cannot_replace_a_newer_count_in_the_same_millisecond(self):
        from community.tests.notification_memory import NotificationQuery

        original = NotificationQuery.count
        overlap = True

        def count(query):
            nonlocal overlap
            total = original(query)
            if overlap and query.query.get("type") == "community_admin_inbox":
                overlap = False
                record_admin_activity(self.realm, self.notice("two"))
            return total

        with patch.object(NotificationQuery, "count", count):
            record_admin_activity(self.realm, self.notice("one"))
        self.assertEqual(len(self.previews()), 1)
        self.assertEqual(
            self.previews()[0].content.details,
            "2 new activities in your community. Check them out.",
        )

    def test_read_racing_preview_save_hides_it_without_changing_saved_count(self):
        from community.tests.notification_memory import NotificationQuery

        original = NotificationQuery.update_one
        def update(query, *args, **kwargs):
            if kwargs.get("set__type") == "community_admin_activity":
                for row in self.inbox():
                    row.isRead = True
            return original(query, *args, **kwargs)

        with patch.object(NotificationQuery, "update_one", update):
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
        Member.objects.filter(entity=self.admin).update(role=_role("member"))
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
        PostTag.objects.create(post_id="photo", entity=self.destination)
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


    def test_joined_member_appears_in_inbox_and_in_preview_count(self):
        from community import admin_notifications

        member = Member.objects.create(realm=self.realm, entity=self.actor, added_by=self.admin)
        admin_notifications.notify_member_joined(str(member.pk))
        admin_notifications.notify_member_joined(str(member.pk))
        result = self.get_inbox().data["results"]
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "community_member_joined")
        self.assertEqual(result[0]["actor"]["id"], "actor")
        self.assertEqual(result[0]["description"], "Maya Chen joined the community.")
        self.assertTrue(self.previews()[0].content.details.startswith("1 new activity"))

    def test_new_post_projection_rechecks_scope_and_anonymity(self):
        Post.objects.bulk_create([Post(post_id="new-post", entity=self.actor, is_anonymous=True)])
        post = Post.objects.get(pk="new-post")
        PostTag.objects.create(post=post, entity=self.destination)
        notice = self.notice("new-post", anonymous=True)
        notice.update(type="community_post_created", target_id="new-post")
        record_admin_activity(self.realm, notice)
        result = self.get_inbox().data["results"][0]
        self.assertEqual(result["post_id"], "new-post")
        self.assertEqual(result["description"], "An anonymous member posted in the community.")
        self.assertIsNone(result["actor"])
        self.assertTrue(result["is_anonymous"])
        Post.objects.filter(pk="new-post").update(is_anonymous=False)
        self.assertEqual(self.get_inbox().data["results"][0]["actor"]["id"], "actor")
        PostTag.objects.filter(post=post).delete()
        self.assertEqual(self.get_inbox().data["results"], [])

    def test_public_join_records_one_join_activity(self):
        from community.community_views import CommunityJoinView

        request = APIRequestFactory().post("/api/realm/communities/club/join")
        request.entity = self.actor
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with patch("community.community_views._notify_membership_changed"):
            for _ in range(2):
                response = CommunityJoinView.as_view()(request, realm_id="club")
                self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.canonical_ids()), 1)
        self.assertEqual(self.get_inbox().data["results"][0]["type"], "community_member_joined")

    def test_private_request_acceptance_records_join_activity(self):
        self.realm.is_private = True
        Realm.objects.filter(pk=self.realm.pk).update(is_private=True)
        for _ in range(2):
            InviteView()._add_member_if_missing(self.invite, self.admin)
        self.assertEqual(len(self.canonical_ids()), 1)
        self.assertEqual(self.get_inbox().data["results"][0]["actor"]["id"], "actor")

    def test_private_invite_acceptance_records_join_without_messages(self):
        from community.invites import accept_side_effects

        self.invite.kind = "invite"
        self.invite.purpose = "join"
        with patch("community.invites.post_joined_notice") as message:
            for _ in range(2):
                accept_side_effects(self.invite, self.actor)
        message.assert_not_called()
        self.assertEqual(len(self.canonical_ids()), 1)
        self.assertEqual(self.get_inbox().data["results"][0]["type"], "community_member_joined")

    def test_rolled_back_join_never_publishes_a_notification(self):
        from django.db import transaction

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                InviteView()._add_member_if_missing(self.invite, self.admin)
                raise RuntimeError("rollback")
        self.assertEqual(self.canonical_ids(), [])

    def test_manage_invite_uses_selected_duplicate_role_id_and_wire_name(self):
        from community.invites import create_invite, accept_side_effects
        from community.serializers import InviteSerializer, RealmMemberSerializer

        Realm.objects.filter(pk=self.realm.pk).update(type="page")
        self.realm.refresh_from_db()
        selected = Role.objects.create(name="moderator")
        other = Role.objects.create(name="moderator")
        with (
            patch("community.invites.has_permission", return_value=True),
            patch("community.invites.is_blocked", return_value=False),
            patch("community.invites.get_entity_display_username", return_value="Admin"),
            patch("community.invites.notify_invitee"),
        ):
            invite, created = create_invite(
                self.realm, self.admin, target_entity_id=self.actor.pk,
                purpose="manage", role_id=selected.pk,
            )
        self.assertTrue(created)
        self.assertEqual(invite.role_id, selected.pk)
        self.assertNotEqual(invite.role_id, other.pk)
        accept_side_effects(invite, self.actor)
        membership = Member.objects.get(realm=self.realm, entity=self.actor)
        self.assertEqual(membership.role_id, selected.pk)
        for payload in (InviteSerializer(invite).data, RealmMemberSerializer(membership).data):
            self.assertEqual(payload["role"], "moderator")
            self.assertEqual(payload["role_id"], selected.pk)

    def test_manage_acceptance_updates_existing_member_to_exact_selected_role(self):
        from community.invites import accept_side_effects

        selected = Role.objects.create(name="moderator")
        membership = Member.objects.create(
            realm=self.realm, entity=self.actor, added_by=self.admin,
        )
        invite = Invite.objects.create(
            realm=self.realm, created_by=self.admin, target_entity=self.actor,
            purpose="manage", role=selected,
        )
        accept_side_effects(invite, self.actor)
        membership.refresh_from_db()
        self.assertEqual(membership.role_id, selected.pk)

    def test_manage_acceptance_preserves_existing_owner_and_admin_roles(self):
        from community.invites import accept_side_effects
        from community.invite_rules import InviteError

        selected = Role.objects.create(name="moderator")
        for name in ("owner", "admin"):
            with self.subTest(role=name):
                existing = _role(name)
                membership, _ = Member.objects.update_or_create(
                    realm=self.realm, entity=self.actor,
                    defaults={"added_by": self.admin, "role": existing},
                )
                invite = Invite.objects.create(
                    realm=self.realm, created_by=self.admin, target_entity=self.actor,
                    purpose="manage", role=selected,
                )
                with self.assertRaises(InviteError):
                    accept_side_effects(invite, self.actor)
                membership.refresh_from_db()
                self.assertEqual(membership.role_id, existing.pk)

    def test_realm_self_role_annotation_uses_exact_owner_and_denies_ambiguous_ids(self):
        from community.annotations import my_role_annotation, my_role_id_annotation
        from entity.roles import realm_owner_role_id

        selected = Role.objects.create(name="owner")
        default_card = Realm.objects.annotate(
            my_role_id=my_role_id_annotation(self.destination),
        ).get(pk=self.realm.pk)
        self.assertEqual(default_card.my_role_id, _role("owner").pk)
        Member.objects.filter(realm=self.realm, entity=self.admin).update(role=selected)
        def card():
            return Realm.objects.annotate(
                my_role=my_role_annotation(self.destination),
                my_role_id=my_role_id_annotation(self.destination),
            ).get(pk=self.realm.pk)
        self.assertEqual(card().my_role, "owner")
        self.assertEqual(card().my_role_id, selected.pk)
        self.assertEqual(realm_owner_role_id(self.realm), selected.pk)
        Member.objects.create(
            realm=self.realm, entity=self.actor, added_by=self.admin, role=_role("owner"),
        )
        self.assertIsNone(card().my_role_id)
        self.assertIsNone(realm_owner_role_id(self.realm))


    def test_join_and_follow_invites_never_store_a_supplied_role(self):
        from community.invites import create_invite

        for realm_type, purpose in (("group", "join"), ("page", "follow")):
            with self.subTest(purpose=purpose):
                Realm.objects.filter(pk=self.realm.pk).update(type=realm_type)
                self.realm.refresh_from_db()
                with (
                    patch("community.invites.has_permission", return_value=True),
                    patch("community.invites.is_blocked", return_value=False),
                    patch("community.invites.accepted_follow_exists", return_value=False),
                    patch("community.invites.get_entity_display_username", return_value="Admin"),
                    patch("community.invites.notify_invitee"),
                ):
                    invite, _ = create_invite(
                        self.realm, self.admin, target_entity_id=self.actor.pk,
                        purpose=purpose, role_id=_role("admin").pk,
                    )
                self.assertIsNone(invite.role_id)

    def test_pending_manage_invite_with_different_role_id_returns_conflict(self):
        from community.invites import create_invite
        from community.invite_rules import InviteError

        Realm.objects.filter(pk=self.realm.pk).update(type="page")
        self.realm.refresh_from_db()
        first = Role.objects.create(name="moderator")
        second = Role.objects.create(name="moderator")
        Invite.objects.create(
            realm=self.realm, created_by=self.admin, target_entity=self.actor,
            purpose="manage", role=first,
        )
        with (
            patch("community.invites.has_permission", return_value=True),
            patch("community.invites.is_blocked", return_value=False),
            self.assertRaises(InviteError) as caught,
        ):
            create_invite(
                self.realm, self.admin, target_entity_id=self.actor.pk,
                purpose="manage", role_id=second.pk,
            )
        self.assertEqual(caught.exception.status_code, 409)


    def test_switchable_pages_and_session_authority_follow_exact_role_tier(self):
        from entity.services.acting import can_act_as_entity
        from user.entity_switch_views import SwitchablePages
        from user.views import DeviceSessionList

        Realm.objects.filter(pk=self.realm.pk).update(type="page")
        selected = Role.objects.create(name="admin")
        Member.objects.filter(realm=self.realm, entity=self.admin).update(role=selected)
        account = SimpleNamespace(entity=self.admin, entity_id=self.admin.pk, is_authenticated=True)
        request = APIRequestFactory().get("/api/user/entity/pages")
        force_authenticate(request, user=account)
        response = SwitchablePages.as_view()(request)
        self.assertEqual(response.status_code, 200)
        pages = response.data["result"]["pages"]
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["role"], "admin")
        self.assertEqual(pages[0]["role_id"], selected.pk)
        self.assertTrue(can_act_as_entity(account, self.destination))
        self.assertIn(str(self.destination.pk), DeviceSessionList()._allowed_entity_ids(self.admin))
        Member.objects.filter(realm=self.realm, entity=self.admin).update(role=_role("moderator"))
        self.assertFalse(can_act_as_entity(account, self.destination))
        self.assertEqual(DeviceSessionList()._allowed_entity_ids(self.admin), [str(self.admin.pk)])
