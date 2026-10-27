"""Real ORM query budgets on isolated SQLite; never connects to live services."""

# ruff: noqa: E402
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from django.db import connection
from django.db.models import CharField, Value
from django.test.utils import CaptureQueriesContext

from bot.models import Bot
from community.models import Member, Realm
from entity.models import Entity
from interests.models import Interest, PostInterestLink
from newsfeed.models import (
    Emoji,
    MapView,
    Post,
    PostPrivacy,
    PostReference,
    PostScore,
    PostTag,
    PreviewCount,
)
from newsfeed.serializers import PostSerializer
from newsfeed.services.post_cards import load_post_cards
from user.models import Account


class FeedPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.models = (
            Entity,
            Account,
            Realm,
            Bot,
            Member,
            Post,
            PostTag,
            PostPrivacy,
            PostReference,
            MapView,
            Emoji,
            PreviewCount,
            PostScore,
            Interest,
            PostInterestLink,
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
        self.entities = {
            kind: Entity.objects.create(id=kind, type=entity_type)
            for kind, entity_type in (
                ("user", "user"),
                ("page", "realm"),
                ("bot", "bot"),
                ("club", "realm"),
            )
        }
        Account.objects.bulk_create(
            [
                Account(
                    entity=self.entities["user"],
                    username="reader",
                    email="reader@example.invalid",
                    first_name="Reader",
                    last_name="One",
                )
            ]
        )
        Realm.objects.bulk_create(
            [
                Realm(
                    id="page",
                    realm_id="page",
                    entity=self.entities["page"],
                    created_by=self.entities["user"],
                    name="Page",
                    type="page",
                    slug="page",
                ),
                Realm(
                    id="club",
                    realm_id="club",
                    entity=self.entities["club"],
                    created_by=self.entities["user"],
                    name="Club",
                    type="community",
                    slug="club",
                ),
            ]
        )
        Bot.objects.bulk_create(
            [Bot(entity=self.entities["bot"], name="Bot", handle="helper")]
        )
        Member.objects.create(
            realm_id="club",
            entity=self.entities["user"],
            added_by=self.entities["user"],
        )
        Post.objects.bulk_create(
            [
                Post(
                    post_id=f"post-{index}",
                    entity=self.entities[("user", "page", "bot")[index % 3]],
                    caption="https://example.invalid/article",
                    file_type="text",
                    content_type="text",
                )
                for index in range(10)
            ]
        )
        PostTag.objects.bulk_create(
            [
                PostTag(post_id=f"post-{index}", entity=self.entities[kind])
                for index in range(10)
                for kind in ("club", "user", "bot")
            ]
        )

    def tearDown(self):
        with connection.constraint_checks_disabled():
            for model in reversed(self.models):
                with connection.cursor() as cursor:
                    cursor.execute(f'DELETE FROM "{model._meta.db_table}"')

    def cards(self, count):
        return (
            load_post_cards(Post.objects.all())
            .annotate(
                entity_reaction=Value(None, output_field=CharField()),
                entity_reaction_is_anonymous=Value(None, output_field=CharField()),
            )
            .order_by("pk")[:count]
        )

    def serialize(self, count):
        request = SimpleNamespace(
            entity=self.entities["user"], user=SimpleNamespace(is_authenticated=True)
        )
        with patch(
            "newsfeed.serializers.get_preview", return_value={"status": "ok"}
        ) as preview:
            with CaptureQueriesContext(connection) as queries:
                result = PostSerializer(
                    self.cards(count), many=True, context={"request": request}
                ).data
        return result, len(queries), preview.call_count

    def test_card_query_count_does_not_grow_with_author_or_tag_count(self):
        one, one_queries, _ = self.serialize(1)
        ten, ten_queries, _ = self.serialize(10)
        print(f"Feed card queries: one={one_queries}, ten={ten_queries}")
        self.assertEqual(len(one), 1)
        self.assertEqual(len(ten), 10)
        self.assertEqual(ten_queries, one_queries)
        self.assertLessEqual(ten_queries, 8)
        self.assertEqual(
            {item["entity"]["type"] for item in ten}, {"user", "realm", "bot"}
        )
        self.assertTrue(all(item["community"]["is_member"] for item in ten))

    def test_duplicate_link_previews_are_reused_only_inside_one_response(self):
        _, _, preview_calls = self.serialize(10)
        self.assertEqual(preview_calls, 1)
        _, _, next_response_calls = self.serialize(1)
        self.assertEqual(next_response_calls, 1)

    def test_eager_loading_preserves_the_existing_response_and_anonymity(self):
        Post.objects.filter(pk="post-0").update(is_anonymous=True)
        request = SimpleNamespace(
            entity=self.entities["user"], user=SimpleNamespace(is_authenticated=True)
        )
        baseline = (
            Post.objects.select_related("entity", "score")
            .prefetch_related(
                "tagging__entity__realms",
                "privacy_users",
                "references",
                "map_info",
                "preview",
            )
            .annotate(
                entity_reaction=Value(None, output_field=CharField()),
                entity_reaction_is_anonymous=Value(None, output_field=CharField()),
            )
            .order_by("pk")[:10]
        )
        with patch("newsfeed.serializers.get_preview", return_value={"status": "ok"}):
            expected = PostSerializer(
                baseline, many=True, context={"request": request}
            ).data
        actual, _, _ = self.serialize(10)
        self.assertEqual(actual, expected)
        self.assertIsNone(actual[0]["entity"])
        self.assertTrue(actual[0]["is_owner"])

    def test_guests_get_the_same_cards_without_membership_queries(self):
        with patch("newsfeed.serializers.get_preview", return_value=None):
            with CaptureQueriesContext(connection) as queries:
                result = PostSerializer(self.cards(10), many=True, context={}).data
        self.assertEqual(len(queries), 6)
        self.assertTrue(all(not item["community"]["is_member"] for item in result))
