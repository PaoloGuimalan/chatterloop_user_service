"""Viewer-only reaction identity contract, using inert in-memory SQL storage."""

# Django must be configured before importing model and serializer modules.
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

from community import posts
from community.models import Realm
from entity.models import Entity
from newsfeed.models import Comment, Post
from newsfeed.serializers import CommentSerializer, PostBasicSerializer, PostSerializer


FIELD = "entity_reaction_is_anonymous"


class ViewerReactionIdentityTests(unittest.TestCase):
    def test_destination_metadata_preserves_server_verification(self):
        realm = Realm(id="realm", entity_id="community", type="community")
        realm.joined = True
        for verified in (True, False):
            realm.is_verified = verified
            with self.subTest(verified=verified):
                self.assertIs(
                    posts.community_metadata(realm, None)["is_verified"], verified
                )

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.enterContext(patch.object(connection, "connection", self.db))
        self.db.executescript("""
            CREATE TABLE newsfeed_post (post_id TEXT PRIMARY KEY);
            CREATE TABLE newsfeed_comment (comment_id TEXT PRIMARY KEY, post_id TEXT);
            CREATE TABLE newsfeed_reaction (
                reaction_id TEXT PRIMARY KEY, post_id TEXT, entity_id TEXT,
                emoji_id TEXT, is_anonymous BOOLEAN
            );
            CREATE TABLE newsfeed_commentreaction (
                reaction_id TEXT PRIMARY KEY, comment_id TEXT, entity_id TEXT,
                emoji_id TEXT, is_anonymous BOOLEAN
            );
            INSERT INTO newsfeed_post VALUES ('post'), ('no-reaction');
            INSERT INTO newsfeed_comment VALUES ('comment', 'post'), ('no-reaction', 'post');
            INSERT INTO newsfeed_reaction VALUES
                ('other-post-reaction', 'post', 'other', 'emoji', 0),
                ('own-post-reaction', 'post', 'viewer', 'emoji', 1),
                ('page-post-reaction', 'post', 'page', 'emoji', 0),
                ('others-only', 'no-reaction', 'other', 'emoji', 1);
            INSERT INTO newsfeed_commentreaction VALUES
                ('other-comment-reaction', 'comment', 'other', 'emoji', 1),
                ('own-comment-reaction', 'comment', 'viewer', 'emoji', 0),
                ('page-comment-reaction', 'comment', 'page', 'emoji', 1),
                ('others-only', 'no-reaction', 'other', 'emoji', 1);
        """)

    def contribution(self, model, *, community=True, anonymous=True, pk=None):
        owner = Entity(id="owner", type="test")
        post = Post(post_id=pk if model is Post and pk else "post", entity=owner)
        post._community_destination = (
            Realm(id="realm", entity_id="community", type="community")
            if community
            else None
        )
        if community:
            post._community_destination.joined = True
        post._prefetched_objects_cache = {
            key: []
            for key in (
                "tagging",
                "privacy_users",
                "references",
                "preview",
                "interests",
            )
        }
        post._state.fields_cache.update(map_info=None, score=None)
        post.entity_reaction = "emoji"
        if model is Post:
            item = post
        else:
            item = Comment(comment_id=pk or "comment", post=post, entity=owner)
            item._prefetched_objects_cache = {"preview": []}
        item.is_anonymous = anonymous
        return item

    def serialize(self, serializer, item, viewer="viewer", authenticated=True):
        request = SimpleNamespace(
            entity=Entity(id=viewer) if viewer else None,
            user=SimpleNamespace(is_authenticated=authenticated),
        )
        return serializer(item, context={"request": request}).data

    def test_community_serializers_return_annotated_own_mode_and_keep_owner_masking(
        self,
    ):
        for serializer, model in (
            (PostSerializer, Post),
            (PostBasicSerializer, Post),
            (CommentSerializer, Comment),
        ):
            for own_mode in (True, False, None):
                with self.subTest(serializer=serializer.__name__, own_mode=own_mode):
                    item = self.contribution(model)
                    setattr(item, FIELD, own_mode)
                    result = self.serialize(serializer, item)
                    self.assertIn(FIELD, result)
                    self.assertIs(result[FIELD], own_mode)
                    self.assertIsNone(result["entity"])
                    self.assertFalse(result["is_owner"])
                    self.assertEqual(item.entity_id, "owner")

    def test_unannotated_community_responses_resolve_only_the_viewers_reaction(self):
        for serializer, model, expected in (
            (PostSerializer, Post, True),
            (PostBasicSerializer, Post, True),
            (CommentSerializer, Comment, False),
        ):
            with self.subTest(serializer=serializer.__name__):
                result = self.serialize(serializer, self.contribution(model))
                self.assertIn(FIELD, result)
                self.assertIs(result[FIELD], expected)
                absent = self.serialize(
                    serializer, self.contribution(model, pk="no-reaction")
                )
                self.assertIsNone(absent[FIELD])

    def test_no_authenticated_viewer_returns_null_even_with_an_annotation(self):
        for model, serializer in ((Post, PostSerializer), (Comment, CommentSerializer)):
            for viewer, authenticated in ((None, False), ("viewer", False)):
                with self.subTest(model=model.__name__, viewer=viewer):
                    item = self.contribution(model)
                    setattr(item, FIELD, True)
                    result = self.serialize(serializer, item, viewer, authenticated)
                    self.assertIn(FIELD, result)
                    self.assertIsNone(result[FIELD])

    def test_ordinary_payloads_do_not_gain_reaction_identity(self):
        for serializer, model in (
            (PostSerializer, Post),
            (PostBasicSerializer, Post),
            (CommentSerializer, Comment),
        ):
            with self.subTest(serializer=serializer.__name__):
                item = self.contribution(model, community=False, anonymous=False)
                setattr(item, FIELD, True)
                result = self.serialize(serializer, item)
                self.assertNotIn(FIELD, result)
                self.assertNotIn("is_anonymous", result)
                self.assertEqual(result["entity"]["id"], "owner")

    def test_named_community_author_and_owner_capabilities_are_preserved(self):
        for model, serializer in ((Post, PostSerializer), (Comment, CommentSerializer)):
            with self.subTest(model=model.__name__):
                item = self.contribution(model, anonymous=False)
                result = self.serialize(serializer, item, viewer="owner")
                self.assertIn(FIELD, result)
                self.assertIsNone(result[FIELD])
                self.assertTrue(result["is_owner"])
                self.assertEqual(result["entity"]["id"], "owner")

    def test_annotation_sql_scopes_posts_and_comments_to_user_or_page_viewer(self):
        helper = getattr(posts, "viewer_reaction_identity_annotations", None)
        self.assertTrue(
            callable(helper), "Shared viewer reaction annotation is missing"
        )
        for model, comment, viewer, expected in (
            (Post, False, "viewer", {"post": True, "no-reaction": None}),
            (Comment, True, "viewer", {"comment": False, "no-reaction": None}),
            (Post, False, "page", {"post": False, "no-reaction": None}),
            (Comment, True, "page", {"comment": True, "no-reaction": None}),
            (Post, False, None, {"post": None, "no-reaction": None}),
            (Comment, True, None, {"comment": None, "no-reaction": None}),
        ):
            with self.subTest(model=model.__name__, viewer=viewer):
                queryset = model.objects.annotate(
                    **helper(Entity(id=viewer) if viewer else None, comment=comment)
                ).values_list("pk", FIELD)
                self.assertEqual(dict(queryset), expected)

    def test_existing_list_helpers_include_viewer_identity_in_their_sql(self):
        from newsfeed.moment_views import _annotated_posts
        from newsfeed.views import CommentsView

        viewer = Entity(id="viewer")
        for queryset, expected in (
            (_annotated_posts(viewer), {"post": True, "no-reaction": None}),
            (
                CommentsView._with_reactions(Comment.objects.all(), viewer),
                {"comment": False, "no-reaction": None},
            ),
        ):
            with self.subTest(model=queryset.model.__name__):
                self.assertIn(FIELD, queryset.query.annotations)
                self.assertEqual(dict(queryset.values_list("pk", FIELD)), expected)


if __name__ == "__main__":
    unittest.main()
