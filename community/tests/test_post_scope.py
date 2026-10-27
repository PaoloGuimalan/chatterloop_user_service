"""No database access: compile real audience SQL and exercise loaded model objects."""

import os
import sqlite3
import unittest
from contextlib import ExitStack
from itertools import product
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from community.models import Member, Realm
from entity.models import Entity
from newsfeed.models import Comment, Post, Reaction, CommentReaction
from newsfeed.services.post_visibility import can_view_post, visible_posts_filter


class CommunityScopeTests(unittest.TestCase):
    def test_serialization_reuses_membership_only_within_the_same_request(self):
        from community.posts import community_metadata

        realm = Realm(id="club", entity_id="club-entity", name="Club")
        viewer = Entity(id="viewer", type="user")
        other = Entity(id="other", type="user")
        cache = {}
        with patch(
            "community.posts.is_member", side_effect=[True, False, False]
        ) as check:
            self.assertTrue(
                community_metadata(realm, viewer, membership_cache=cache)["is_member"]
            )
            self.assertTrue(
                community_metadata(realm, viewer, membership_cache=cache)["is_member"]
            )
            self.assertFalse(
                community_metadata(realm, other, membership_cache=cache)["is_member"]
            )
            self.assertFalse(
                community_metadata(realm, viewer, membership_cache={})["is_member"]
            )
        self.assertEqual(check.call_count, 3)

    def test_own_community_home_posts_require_named_connection_activity(self):
        from community.posts import community_home_filter

        with sqlite3.connect(":memory:") as db:
            db.executescript("""
                CREATE TABLE newsfeed_post (post_id TEXT, entity_id TEXT);
                CREATE TABLE entity_entity (id TEXT);
                INSERT INTO entity_entity VALUES ('community');
                CREATE TABLE newsfeed_posttag (id TEXT, post_id TEXT, entity_id TEXT);
                CREATE TABLE community_realm (id TEXT, entity_id TEXT, type TEXT);
                CREATE TABLE newsfeed_comment (
                    comment_id TEXT, post_id TEXT, entity_id TEXT,
                    is_anonymous BOOLEAN, deleted_at TEXT
                );
                CREATE TABLE newsfeed_reaction (
                    reaction_id TEXT, post_id TEXT, entity_id TEXT, is_anonymous BOOLEAN
                );
                INSERT INTO newsfeed_post VALUES
                    ('own-community', 'owner'), ('own-timeline', 'owner'), ('own-share', 'owner'),
                    ('other-community', 'other');
                INSERT INTO community_realm VALUES ('realm', 'community', 'community');
                INSERT INTO newsfeed_posttag VALUES
                    ('tag1', 'own-community', 'community'),
                    ('tag2', 'other-community', 'community');
            """)
            for table, actor, anonymous, deleted, visible in (
                (None, None, False, None, False),
                ("comment", "friend", False, None, True),
                ("reaction", "friend", False, None, True),
                ("comment", "stranger", False, None, False),
                ("comment", "friend", True, None, False),
                ("reaction", "friend", True, None, False),
                ("comment", "friend", False, "deleted", False),
            ):
                with self.subTest(
                    table=table, actor=actor, anonymous=anonymous, deleted=deleted
                ):
                    db.execute("DELETE FROM newsfeed_comment")
                    db.execute("DELETE FROM newsfeed_reaction")
                    if table == "comment":
                        db.executemany(
                            "INSERT INTO newsfeed_comment VALUES (?, ?, ?, ?, ?)",
                            [
                                ("activity", target, actor, anonymous, deleted)
                                for target in (
                                    "own-community",
                                    "own-timeline",
                                    "own-share",
                                )
                            ],
                        )
                    elif table == "reaction":
                        db.executemany(
                            "INSERT INTO newsfeed_reaction VALUES (?, ?, ?, ?)",
                            [
                                ("activity", target, actor, anonymous)
                                for target in (
                                    "own-community",
                                    "own-timeline",
                                    "own-share",
                                )
                            ],
                        )
                    query = Post.objects.filter(
                        community_home_filter("owner", ["friend"])
                    ).values_list("post_id", flat=True)
                    sql, params = query.query.sql_with_params()
                    result = {
                        row[0] for row in db.execute(sql.replace("%s", "?"), params)
                    }
                    self.assertEqual("own-community" in result, visible)
                    self.assertEqual("own-timeline" in result, visible)
                    self.assertEqual("own-share" in result, visible)
                    self.assertIn("other-community", result)

    def test_only_profile_owner_can_list_their_own_anonymous_posts(self):
        from community import posts

        predicate = getattr(posts, "profile_anonymity_filter", None)
        self.assertTrue(callable(predicate))
        with sqlite3.connect(":memory:") as db:
            db.execute(
                "CREATE TABLE newsfeed_post (post_id TEXT, entity_id TEXT, is_anonymous BOOLEAN)"
            )
            db.executemany(
                "INSERT INTO newsfeed_post VALUES (?, ?, ?)",
                [
                    ("named", "owner", False),
                    ("own-anonymous", "owner", True),
                    ("other-anonymous-tag", "other", True),
                ],
            )
            for viewer in ("owner", "friend", "connection", "stranger", None):
                with self.subTest(viewer=viewer):
                    query = Post.objects.filter(predicate(viewer, "owner")).values_list(
                        "post_id", flat=True
                    )
                    sql, params = query.query.sql_with_params()
                    visible = {
                        row[0] for row in db.execute(sql.replace("%s", "?"), params)
                    }
                    self.assertEqual(
                        visible,
                        {"named", "own-anonymous"} if viewer == "owner" else {"named"},
                    )
            query = Post.objects.filter(predicate("owner", "other")).values_list(
                "post_id", flat=True
            )
            sql, params = query.query.sql_with_params()
            self.assertEqual(
                {row[0] for row in db.execute(sql.replace("%s", "?"), params)},
                {"named"},
            )

    def test_comment_count_url_requires_current_private_membership(self):
        from newsfeed import views
        from rest_framework.test import APIRequestFactory, force_authenticate

        post = Post(post_id="private-post", entity_id="former", on_feed="feed")
        post._community_destination = Realm(
            id="realm", entity_id="community", type="community", is_private=True
        )
        comment = Comment(comment_id="private-comment", post=post, entity_id="former")
        for viewer, member, expected in (
            ("outsider", False, 404),
            ("former", False, 404),
            ("member", True, 200),
        ):
            with self.subTest(viewer=viewer):
                request = APIRequestFactory().get("/comments/private-comment/reactions")
                request.entity = Entity(id=viewer)
                force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
                with (
                    patch.object(Comment.objects, "select_related") as related,
                    patch.object(Comment.objects, "get", return_value=comment),
                    patch.object(
                        views.CommentPreviewCount.objects, "filter", return_value=[]
                    ),
                    patch.object(views, "CommentPreviewCountSerializer") as serializer,
                    patch("community.posts.is_member", return_value=member),
                ):
                    related.return_value = SimpleNamespace(
                        model=Comment, get=lambda **kwargs: comment
                    )
                    serializer.return_value.data = [{"count": 3}]
                    response = views.CommentReactionsCountView.as_view()(
                        request, comment_id="private-comment"
                    )
                self.assertEqual(response.status_code, expected)
                if member:
                    self.assertEqual(response.data, [{"count": 3}])

    def test_shared_moment_preview_keeps_a_community_author_anonymous(self):
        from newsfeed import moment_views

        post = Post(
            post_id="shared-post", entity=Entity(id="secret-owner"), is_anonymous=True
        )
        post._community_destination = Realm(
            id="realm", entity_id="community", type="community"
        )
        moment = Post(post_id="moment")
        with (
            patch.object(moment_views.Post.objects, "select_related") as query,
            patch.object(
                moment_views,
                "_shared_pointer",
                side_effect=lambda item: "shared-post"
                if item.post_id == "moment"
                else None,
            ),
            patch.object(moment_views, "_first_media", return_value=None),
            patch.object(moment_views, "can_view_post", return_value=True),
            patch.object(moment_views, "EntitySerializer") as serializer,
            patch("community.posts.is_member", return_value=False),
        ):
            query.return_value.prefetch_related.return_value.filter.side_effect = [
                [post],
                [],
            ]
            serializer.return_value.data = {"id": "secret-owner"}
            result = moment_views._shared_previews(Entity(id="viewer"), [moment])[
                "moment"
            ]
        self.assertIsNone(result["author"])
        self.assertTrue(result["is_anonymous"])
        self.assertNotIn("secret-owner", str(result))

    def test_actual_audience_query_keeps_private_posts_out_of_other_feeds(self):
        # Execute the ORM's generated SQL against disposable SQLite fixtures.
        # This never opens an application database or runs a migration.
        with sqlite3.connect(":memory:") as db:
            db.executescript("""
                CREATE TABLE entity_entity (id TEXT PRIMARY KEY);
                CREATE TABLE community_realm (id TEXT, entity_id TEXT, type TEXT, is_active BOOLEAN, is_private BOOLEAN);
                CREATE TABLE community_member (realm_id TEXT, entity_id TEXT);
                CREATE TABLE newsfeed_post (post_id TEXT, entity_id TEXT, privacy_status TEXT, on_feed TEXT);
                CREATE TABLE newsfeed_posttag (post_id TEXT, entity_id TEXT);
                CREATE TABLE newsfeed_postprivacy (post_id TEXT, allowed_entity_id TEXT);
                CREATE TABLE entity_connection (action_by_id TEXT, involved_entity_id TEXT, status BOOLEAN);
                CREATE TABLE user_account (entity_id TEXT, is_active BOOLEAN, is_verified BOOLEAN);
                CREATE TABLE bot_bot (entity_id TEXT, is_active BOOLEAN);
                INSERT INTO entity_entity VALUES ('author'), ('viewer'), ('public'), ('private'), ('tagged-user');
                INSERT INTO community_realm VALUES ('public-realm', 'public', 'community', 1, 0), ('private-realm', 'private', 'community', 1, 1);
                INSERT INTO community_member VALUES ('private-realm', 'viewer');
                INSERT INTO newsfeed_post VALUES
                    ('ordinary-public', 'author', 'public', 'feed'),
                    ('ordinary-private', 'author', 'private', 'feed'),
                    ('community-public', 'author', 'private', 'feed'),
                    ('community-private', 'author', 'public', 'feed'),
                    ('multiple-destinations', 'author', 'public', 'feed');
                INSERT INTO newsfeed_posttag VALUES
                    ('community-public', 'public'),
                    ('community-private', 'private'), ('community-private', 'tagged-user'),
                    ('multiple-destinations', 'public'), ('multiple-destinations', 'private');
            """)

            def visible(viewer):
                query = (
                    Post.objects.filter(visible_posts_filter(viewer))
                    .values_list("post_id", flat=True)
                    .query
                )
                sql, params = query.sql_with_params()
                return {row[0] for row in db.execute(sql.replace("%s", "?"), params)}

            self.assertEqual(
                visible(Entity(id="viewer")),
                {"ordinary-public", "community-public", "community-private"},
            )
            self.assertEqual(
                visible(Entity(id="author")),
                {"ordinary-public", "ordinary-private", "community-public"},
            )
            self.assertEqual(visible(None), {"ordinary-public", "community-public"})
            db.execute("DELETE FROM community_member")
            self.assertEqual(
                visible(Entity(id="viewer")), {"ordinary-public", "community-public"}
            )

    def test_anonymous_uploads_do_not_publish_account_scoped_paths(self):
        from community.posts import assert_anonymous_upload
        from rest_framework.exceptions import ValidationError

        realm = Realm(id="realm", entity_id="community", type="community")
        with patch("community.posts.community_for_post", return_value=realm):
            with self.assertRaises(ValidationError):
                assert_anonymous_upload(
                    SimpleNamespace(key="uploads/comments/account/photo.png"),
                    Post(),
                    is_anonymous=True,
                )
            assert_anonymous_upload(
                SimpleNamespace(key="uploads/communities/community/random/photo.png"),
                Post(),
                is_anonymous=True,
            )

    def test_reaction_identity_defaults_to_its_existing_mode(self):
        from community.posts import request_anonymity

        with patch(
            "community.posts.community_for_post", return_value=Realm(type="community")
        ):
            self.assertTrue(
                request_anonymity(SimpleNamespace(data={}), Post(), default=True)
            )
            self.assertFalse(
                request_anonymity(
                    SimpleNamespace(data={"is_anonymous": False}), Post(), default=True
                )
            )

    def test_anonymous_notifications_never_embed_the_actor_identity(self):
        from community.notifications import notification_description

        description = notification_description(
            community_entity_id="community",
            actor_name="secret-handle",
            is_anonymous=True,
            verb="commented",
            post_id="post",
        )
        self.assertEqual(description["fromUserID"], "community")
        self.assertNotIn("secret", str(description))

    def test_direct_reads_reject_multiple_community_destinations(self):
        from community.posts import community_for_post
        from rest_framework.exceptions import NotFound

        tags = [
            SimpleNamespace(
                entity=SimpleNamespace(realms=Realm(id=value, type="community"))
            )
            for value in ("first", "second")
        ]
        post = SimpleNamespace(tagging=SimpleNamespace(all=lambda: tags))
        with self.assertRaises(NotFound):
            community_for_post(post)

    def test_community_entities_cannot_be_members_of_any_realm(self):
        from django.core.exceptions import ValidationError

        membership = Member(entity_id="community", realm_id="another-community")
        with patch("community.models.Realm.objects.filter") as lookup:
            lookup.return_value.exists.return_value = True
            with self.assertRaises(ValidationError):
                membership.clean()

    def test_pages_remain_eligible_for_membership(self):
        membership = Member(entity_id="page", realm_id="community")
        with patch("community.models.Realm.objects.filter") as lookup:
            lookup.return_value.exists.return_value = False
            membership.clean()

    def test_community_creation_requires_a_name_and_strict_privacy(self):
        from community.api_serializers import CommunityCreateSerializer

        self.assertFalse(
            CommunityCreateSerializer(
                data={"name": "", "slug": "readers", "is_private": False}
            ).is_valid()
        )
        self.assertFalse(
            CommunityCreateSerializer(
                data={"name": "Readers", "slug": "readers", "is_private": "false"}
            ).is_valid()
        )
        valid = CommunityCreateSerializer(
            data={"name": "Readers", "slug": "readers", "is_private": True}
        )
        self.assertTrue(valid.is_valid())
        self.assertTrue(valid.validated_data["is_private"])

    def test_destination_tag_cannot_be_removed(self):
        from community import posts

        self.assertTrue(callable(getattr(posts, "assert_removable_tag", None)))
        realm = Realm(id="realm", entity_id="community", type="community")
        with patch("community.posts.community_for_post", return_value=realm):
            with self.assertRaises(Exception) as error:
                posts.assert_removable_tag(Post(), "community")
            self.assertEqual(error.exception.status_code, 400)

    def test_private_contribution_without_membership_is_denied(self):
        from community.posts import assert_post_access

        realm = Realm(id="realm", type="community", is_private=True)
        with (
            patch("community.posts.community_for_post", return_value=realm),
            patch("community.posts.is_member", return_value=False),
        ):
            with self.assertRaises(Exception) as error:
                assert_post_access(
                    Post(), SimpleNamespace(entity=Entity(id="viewer")), contribute=True
                )
            self.assertEqual(error.exception.status_code, 404)

    def test_public_nonmember_can_comment_or_react(self):
        from community.posts import assert_post_access

        realm = Realm(id="realm", type="community", is_private=False)
        with (
            patch("community.posts.community_for_post", return_value=realm),
            patch("community.posts.is_member", return_value=False),
        ):
            self.assertIs(
                assert_post_access(
                    Post(), SimpleNamespace(entity=Entity(id="viewer")), contribute=True
                ),
                realm,
            )

    def test_audience_sql_uses_membership_and_community_tag(self):
        sql = str(Post.objects.filter(visible_posts_filter(Entity(id="viewer"))).query)
        self.assertIn("community_member", sql)
        self.assertIn("newsfeed_posttag", sql)
        self.assertIn("community", sql)

    def test_boolean_fields_preserve_existing_ownership(self):
        for model in (Post, Comment, Reaction, CommentReaction):
            with self.subTest(model=model.__name__):
                self.assertIn(
                    "is_anonymous", {field.name for field in model._meta.fields}
                )
                instance = model(entity_id="owner")
                self.assertFalse(instance.is_anonymous)
                self.assertEqual(instance.entity_id, "owner")

    def test_former_private_member_does_not_keep_author_access(self):
        post = Post(entity_id="author", privacy_status="public")
        realm = Realm(id="realm", type="community", is_private=True)
        with (
            patch("community.posts.community_for_post", return_value=realm),
            patch("community.posts.is_member", return_value=False),
        ):
            self.assertFalse(can_view_post(post, Entity(id="author")))

    def test_public_community_ignores_author_private_profile_audience(self):
        post = Post(entity_id="author", privacy_status="private")
        realm = Realm(id="realm", type="community", is_private=False)
        with (
            patch("community.posts.community_for_post", return_value=realm),
            patch("community.posts.is_member", return_value=False),
        ):
            self.assertTrue(can_view_post(post, None))

    def test_anonymous_comment_realtime_has_no_actor(self):
        from newsfeed.services.post_realtime import publish_comment_created

        comment = Comment(
            comment_id="comment", post_id="post", entity_id="author", is_anonymous=True
        )
        actor = SimpleNamespace(
            id="author",
            type="user",
            users=SimpleNamespace(
                username="private-handle", first_name="Real", last_name="Name"
            ),
        )
        with patch(
            "newsfeed.services.post_realtime.RedisPubSubClient.publish_json_on_commit"
        ) as publish:
            publish_comment_created(comment, actor)
        body = publish.call_args.args[1]["message"]["result"]
        self.assertIsNone(body.get("entity"))
        self.assertIsNone(body.get("actor"))
        self.assertTrue(body["is_anonymous"])
        self.assertNotIn("author", str(body))


class CommunityReactionNotificationTests(unittest.TestCase):
    def test_named_actor_avatar_and_community_identity_survive_anonymous_round_trip(
        self,
    ):
        self.queue_notice("post_reaction")
        self.callbacks.pop()()
        notice = self.store.rows[0]
        original_id = notice.notificationID
        self.assertEqual(notice.fromUserID, "actor")
        self.assertEqual(notice.community_entity_id, "community")
        self.assertFalse(notice.is_anonymous)
        notice.isRead = True
        self.reaction.is_anonymous = True
        self.queue_notice("post_reaction", is_anonymous=True)
        self.callbacks.pop()()
        self.assertEqual(len(self.store.rows), 1)
        self.assertEqual(self.store.rows[0].fromUserID, "community")
        self.assertTrue(self.store.rows[0].is_anonymous)
        self.reaction.is_anonymous = False
        self.queue_notice("post_reaction")
        self.callbacks.pop()()
        self.assertEqual(len(self.store.rows), 1)
        self.assertEqual(self.store.rows[0].notificationID, original_id)
        self.assertEqual(self.store.rows[0].fromUserID, "actor")
        self.assertTrue(self.store.rows[0].isRead)

    def setUp(self):
        from community.notifications import notify_community_activity
        from community.tests.notification_memory import NotificationMemory
        from user.ext_models.mongomodels import Notification

        self.notify = notify_community_activity
        self.store = NotificationMemory()
        self.callbacks = []
        self.post = Post(post_id="post", entity_id="owner", on_feed="feed")
        self.post._community_destination = Realm(
            id="realm", entity_id="community", type="community", is_private=True
        )
        self.actor = Entity(id="actor")
        self.reaction = SimpleNamespace(is_anonymous=False)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(Notification, "objects", new=self.store))
        stack.enter_context(
            patch.object(Notification, "save", new=self.store.save_document)
        )
        stack.enter_context(
            patch(
                "community.notifications.transaction.on_commit",
                side_effect=self.callbacks.append,
            )
        )
        stack.enter_context(
            patch(
                "community.notifications.get_entity_display_username",
                return_value="secret-handle",
            )
        )
        post_query = stack.enter_context(patch.object(Post.objects, "filter"))
        post_query.return_value.first.return_value = self.post
        members = stack.enter_context(patch.object(Member.objects, "filter"))
        members.return_value.values_list.return_value = ["owner"]
        for model in (Reaction, CommentReaction):
            query = stack.enter_context(patch.object(model.objects, "filter"))
            query.return_value.first.side_effect = lambda: self.reaction
        stack.enter_context(
            patch("user_service.services.redis.RedisPubSubClient.publish_json")
        )
        stack.enter_context(
            patch("user_service.services.rabbitmq.RabbitMQClient.publish")
        )

    def queue_notice(self, notification_type, *, is_anonymous=False):
        return self.notify(
            self.post,
            self.actor,
            "reaction",
            ["owner"],
            is_anonymous=is_anonymous,
            verb="reacted",
            notification_type=notification_type,
            anchor="comment" if notification_type == "comment_reaction" else None,
        )

    def test_anonymous_reaction_update_replaces_named_notice_without_losing_read_state(
        self,
    ):
        from user.services.mongohelpers import NotificationService

        for notification_type, already_read in product(
            ("post_reaction", "comment_reaction"), (False, True)
        ):
            with self.subTest(
                notification_type=notification_type, already_read=already_read
            ):
                self.store.rows.clear()
                self.reaction = SimpleNamespace(is_anonymous=False)
                self.queue_notice(notification_type)
                self.assertEqual(self.store.rows, [])  # Wait for SQL commit.
                self.callbacks.pop()()
                original_id = self.store.rows[0].notificationID
                self.store.rows[0].isRead = already_read
                # Scope recipient and type independently of the shared reference.
                for recipient, kind, sender in (
                    ("other", notification_type, "community"),
                    ("owner", "post_mention", "community"),
                    ("owner", notification_type, "different-community"),
                ):
                    NotificationService().add_notification(
                        referenceID="reaction",
                        referenceStatus=True,
                        toUserID=recipient,
                        fromUserID=sender,
                        content_headline="Other activity",
                        content_details="Keep me",
                        type=kind,
                    )
                self.reaction.is_anonymous = True
                self.queue_notice(notification_type, is_anonymous=True)
                self.callbacks.pop()()
                notices = [
                    row
                    for row in self.store.rows
                    if row.toUserID == "owner"
                    and row.type == notification_type
                    and row.fromUserID == "community"
                ]
                self.assertEqual(len(notices), 1)
                notice = notices[0]
                self.assertEqual(notice.notificationID, original_id)
                self.assertEqual(
                    notice.content.details,
                    "An anonymous member reacted on your post in the community.",
                )
                self.assertEqual(notice.fromUserID, "community")
                self.assertEqual(notice.isRead, already_read)
                self.assertTrue(notice.referenceStatus)
                self.assertNotIn("secret-handle", str(notice.to_mongo()))
                self.assertEqual(notice.target.supportingID, "post")
                self.assertEqual(len(self.store.rows), 4)
                self.assertEqual(
                    [row.content.details for row in self.store.rows[1:]],
                    ["Keep me", "Keep me", "Keep me"],
                )

    def test_legacy_duplicate_reaction_notices_are_sanitized_and_inactive_notice_reactivates_unread(
        self,
    ):
        from user.services.mongohelpers import NotificationService

        for notification_type in ("post_reaction", "comment_reaction"):
            with self.subTest(notification_type=notification_type):
                self.store.rows.clear()
                for _ in range(2):
                    notice = NotificationService().add_notification(
                        referenceID="reaction",
                        referenceStatus=False,
                        toUserID="owner",
                        fromUserID="community",
                        content_headline="Reaction",
                        content_details="secret-handle reacted",
                        type=notification_type,
                    )
                    notice.isRead = True
                self.reaction.is_anonymous = True
                self.queue_notice(notification_type, is_anonymous=True)
                self.callbacks.pop()()
                self.assertEqual(len(self.store.rows), 1)
                self.assertFalse(self.store.rows[0].isRead)
                self.assertTrue(self.store.rows[0].referenceStatus)
                self.assertNotIn("secret-handle", str(self.store.rows[0].to_mongo()))
                self.store.rows[0].referenceStatus = False
                self.store.rows[0].isRead = True
                self.queue_notice(notification_type, is_anonymous=True)
                self.callbacks.pop()()
                self.assertEqual(len(self.store.rows), 1)
                self.assertFalse(self.store.rows[0].isRead)
                self.assertTrue(self.store.rows[0].referenceStatus)

    def test_delayed_named_delivery_uses_current_reaction_identity_and_skips_deleted_reactions(
        self,
    ):
        for notification_type in ("post_reaction", "comment_reaction"):
            with self.subTest(notification_type=notification_type):
                self.store.rows.clear()
                self.reaction = SimpleNamespace(is_anonymous=False)
                self.queue_notice(notification_type)
                delayed_named = self.callbacks.pop()
                self.reaction.is_anonymous = True
                self.queue_notice(notification_type, is_anonymous=True)
                self.callbacks.pop()()
                delayed_named()
                self.assertEqual(len(self.store.rows), 1)
                self.assertNotIn("secret-handle", str(self.store.rows[0].to_mongo()))
                self.store.rows.clear()
                self.queue_notice(notification_type, is_anonymous=True)
                self.reaction = None
                self.callbacks.pop()()
                self.assertEqual(self.store.rows, [])


if __name__ == "__main__":
    unittest.main()
