"""Social reasons must reflect current named activity, not stale cache rows."""

# Django must initialize before importing model-backed modules.
# ruff: noqa: E402

import os
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from django.db import connection
from community.models import Realm
from entity.models import Entity
from newsfeed.models import Post
from newsfeed.serializers import CommunityContributionMixin
from newsfeed.views import NewsfeedView


class CommunityFeedReasonTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.enterContext(patch.object(connection, "connection", self.db))
        self.db.executescript("""
            CREATE TABLE newsfeed_comment (
                comment_id TEXT PRIMARY KEY, post_id TEXT, entity_id TEXT,
                is_anonymous BOOLEAN, deleted_at TEXT
            );
            INSERT INTO newsfeed_comment VALUES ('comment', 'post', 'friend', 0, NULL);
            CREATE TABLE community_member (id TEXT, realm_id TEXT, entity_id TEXT);
            INSERT INTO community_member VALUES ('member', 'realm', 'viewer');
        """)
        self.post = Post(post_id="post", entity_id="owner")
        self.post._community_destination = Realm(id="realm", entity_id="community", type="community")
        self.post._community_destination.joined = True
        actor = Entity(id="friend", type="test")
        self.enterContext(patch.object(Entity.objects, "filter", return_value=SimpleNamespace(select_related=lambda *args: [actor])))

    def resolve(self, **kwargs):
        return NewsfeedView._resolve_feed_reasons(
            {"post": ("comment", "friend")}, Entity(id="viewer"),
            posts=[self.post], connection_ids=kwargs.get("connections", ["friend"]),
        )

    def test_named_connection_comment_reason_is_preserved(self):
        reason = self.resolve()["post"]
        self.assertEqual(reason["type"], "comment")
        self.assertEqual(reason["entity"]["id"], "friend")

    def test_anonymized_deleted_and_former_connection_comments_are_silent(self):
        self.assertEqual(self.resolve(connections=[]), {})
        for statement in (
            "UPDATE newsfeed_comment SET is_anonymous = 1",
            "UPDATE newsfeed_comment SET is_anonymous = 0, deleted_at = '2026-10-26'",
            "DELETE FROM newsfeed_comment",
        ):
            with self.subTest(statement=statement):
                self.db.execute(statement)
                self.assertEqual(self.resolve(), {})

    def test_private_community_reason_requires_current_membership(self):
        self.post._community_destination.is_private = True
        self.db.execute("DELETE FROM community_member")
        self.assertEqual(self.resolve(), {})

    def test_community_source_and_unknown_reasons_never_name_a_member(self):
        for row_type in ("community", "fanout", "unknown"):
            with self.subTest(row_type=row_type):
                result = NewsfeedView._resolve_feed_reasons(
                    {"post": (row_type, "friend")}, Entity(id="viewer"),
                    posts=[self.post], connection_ids=["friend"],
                )
                self.assertEqual(result, {})

    def test_serializer_keeps_validated_reason_with_destination_metadata(self):
        class Base:
            def to_representation(self, instance):
                return {"entity": {"id": "owner"}, "feed_reason": {"type": "comment", "entity": {"id": "friend"}}}

        class Serializer(CommunityContributionMixin, Base):
            context = {"request": SimpleNamespace(entity=Entity(id="viewer"))}

        result = Serializer().to_representation(self.post)
        self.assertEqual(result["feed_reason"]["entity"]["id"], "friend")
        self.assertEqual(result["community"]["entity_id"], "community")
        self.post.is_anonymous = True
        result = Serializer().to_representation(self.post)
        self.assertIsNone(result["entity"])
        self.assertIsNone(result["feed_reason"])

    def test_validated_named_comment_reason_survives_anonymous_post_owner_projection(self):
        self.post.is_anonymous = True
        reasons = self.resolve()

        class Base:
            def to_representation(self, instance):
                return {"entity": {"id": "owner"}, "feed_reason": reasons["post"]}

        class Serializer(CommunityContributionMixin, Base):
            context = {
                "request": SimpleNamespace(entity=Entity(id="viewer")),
                "validated_community_comment_reasons": reasons,
            }

        result = Serializer().to_representation(self.post)
        self.assertIsNone(result["entity"])
        self.assertFalse(result["is_owner"])
        self.assertIsNotNone(result["feed_reason"])
        self.assertEqual(result["feed_reason"]["entity"]["id"], "friend")
        self.db.execute("UPDATE newsfeed_comment SET is_anonymous = 1")
        Serializer.context["validated_community_comment_reasons"] = self.resolve()
        result = Serializer().to_representation(self.post)
        self.assertIsNone(result["feed_reason"])


if __name__ == "__main__":
    unittest.main()
