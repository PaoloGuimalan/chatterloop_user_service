"""Execute the shared read predicate without application databases or caches."""
import os
import sqlite3
import unittest
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django
django.setup()

from community.models import Realm
from entity.models import Entity
from newsfeed.models import Post
from newsfeed.services.post_visibility import can_view_post, visible_posts_filter


class ApprovalVisibilityTests(unittest.TestCase):
    def test_loaded_community_posts_are_hidden_even_from_author_until_approved(self):
        author = Entity(id="author")
        post = Post(post_id="post", entity=author, privacy_status="public", on_feed="feed")
        for community in (None, Realm(id="realm", type="community", is_active=True)):
            post._community_destination = community
            for state in (0, 1, 2):
                post.approval = state
                for viewer in (None, author, Entity(id="member")):
                    with self.subTest(community=bool(community), state=state, viewer=viewer):
                        with patch("community.posts.is_member", return_value=True):
                            self.assertEqual(can_view_post(post, viewer), community is None or state == 1)

    def test_sql_filters_hidden_community_rows_before_limit_but_keeps_ordinary_states(self):
        with sqlite3.connect(":memory:") as db:
            db.executescript("""
                CREATE TABLE entity_entity (id TEXT PRIMARY KEY);
                CREATE TABLE community_realm (id TEXT, entity_id TEXT, type TEXT, is_active BOOLEAN, is_private BOOLEAN);
                CREATE TABLE community_member (realm_id TEXT, entity_id TEXT);
                CREATE TABLE newsfeed_post (post_id TEXT, entity_id TEXT, privacy_status TEXT, on_feed TEXT, approval INTEGER);
                CREATE TABLE newsfeed_posttag (post_id TEXT, entity_id TEXT);
                CREATE TABLE newsfeed_postprivacy (post_id TEXT, allowed_entity_id TEXT);
                CREATE TABLE entity_connection (action_by_id TEXT, involved_entity_id TEXT, status BOOLEAN);
                CREATE TABLE user_account (entity_id TEXT, is_active BOOLEAN, is_verified BOOLEAN);
                CREATE TABLE bot_bot (entity_id TEXT, is_active BOOLEAN);
                INSERT INTO entity_entity VALUES ('author'), ('member'), ('community');
                INSERT INTO community_realm VALUES ('realm', 'community', 'community', 1, 0);
                INSERT INTO community_member VALUES ('realm', 'member');
            """)
            for state in (0, 1, 2):
                for prefix in ("community", "ordinary"):
                    post_id = f"{prefix}-{state}"
                    db.execute("INSERT INTO newsfeed_post VALUES (?, 'author', 'public', 'feed', ?)", (post_id, state))
                    if prefix == "community":
                        db.execute("INSERT INTO newsfeed_posttag VALUES (?, 'community')", (post_id,))
            for viewer in (None, Entity(id="author"), Entity(id="member"), Entity(id="outsider")):
                for private in (False, True):
                    db.execute("UPDATE community_realm SET is_private = ?", (private,))
                    query = Post.objects.filter(visible_posts_filter(viewer)).order_by("post_id").values_list("post_id", flat=True)
                    sql, params = query.query.sql_with_params()
                    actual = {row[0] for row in db.execute(sql.replace("%s", "?"), params)}
                    expected = {f"ordinary-{state}" for state in (0, 1, 2)}
                    if not private or getattr(viewer, "pk", None) == "member":
                        expected.add("community-1")
                    self.assertEqual(actual, expected)
                    sql, params = query[:1].query.sql_with_params()
                    self.assertEqual([row[0] for row in db.execute(sql.replace("%s", "?"), params)], sorted(expected)[:1])
