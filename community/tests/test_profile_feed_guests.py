"""Guest profile feeds exercise the real view, ORM and serializer offline."""

# ruff: noqa: E402
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from bot.models import Bot
from community.models import Member, Realm
from entity.models import Block, Connection, Entity, Follow
from interests.models import Interest, PostInterestLink
from newsfeed.models import (
    Emoji, MapView, Post, PostPrivacy, PostReference, PostSave, PostScore,
    PostTag, PreviewCount, Reaction,
)
from newsfeed.views import NewsfeedProfileView
from user.models import Account


class GuestProfileFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.models = (
            Entity, Account, Realm, Bot, Member, Connection, Follow, Block,
            Post, PostTag, PostPrivacy, PostReference, MapView, Emoji,
            PreviewCount, PostScore, Interest, PostInterestLink, PostSave, Reaction,
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
        self.owner = Entity.objects.create(id="owner", type="user")
        Account.objects.bulk_create([Account(
            id="account", entity=self.owner, username="person",
            email="person@example.invalid", first_name="Person", last_name="One",
        )])
        for kind in ("page", "community"):
            entity = Entity.objects.create(id=kind, type="realm")
            Realm.objects.bulk_create([Realm(
                id=kind, realm_id=kind, entity=entity, created_by=self.owner,
                name=kind, type=kind, slug=f"{kind}-slug",
            )])
        Post.objects.bulk_create([
            Post(post_id="public", entity=self.owner, caption="Public post"),
            Post(post_id="restricted", entity=self.owner, privacy_status="connections"),
            Post(post_id="page-post", entity_id="page"),
            Post(post_id="community-post", entity=self.owner),
        ])
        PostTag.objects.create(post_id="community-post", entity_id="community")

    def tearDown(self):
        with connection.constraint_checks_disabled():
            for model in reversed(self.models):
                with connection.cursor() as cursor:
                    cursor.execute(f'DELETE FROM "{model._meta.db_table}"')

    def feed(self, username, *, user_class=AnonymousUser, viewcache=None, viewer=None):
        with override_settings(
            REST_FRAMEWORK={
                "DEFAULT_AUTHENTICATION_CLASSES": [],
                "UNAUTHENTICATED_USER": user_class,
            },
            ALLOWED_HOSTS=["testserver"],
        ):
            request = APIRequestFactory().post(
                f"/api/newsfeed/profile/{username}/",
                {"viewcache": viewcache or []}, format="json",
            )
            if viewer is not None:
                request.entity = viewer
                force_authenticate(request, user=Account.objects.get(pk="account"))
            return NewsfeedProfileView.as_view()(request, username=username)

    def test_guest_reads_public_cards_on_user_page_and_community_profiles(self):
        for username, expected in (
            ("person", {"public", "community-post"}),
            ("page-slug", {"page-post"}),
            ("community-slug", {"community-post"}),
            ("community", {"community-post"}),
        ):
            with self.subTest(username=username):
                response = self.feed(username)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual({p["post_id"] for p in response.data["results"]}, expected)

    def test_guest_without_user_object_can_read_a_public_profile(self):
        response = self.feed("page-slug", user_class=None)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["results"][0]["post_id"], "page-post")

    def test_guest_viewcache_does_not_crash_or_publish_engagements(self):
        for username in ("person", "page-slug", "community-slug", "community"):
            with self.subTest(username=username):
                with patch("newsfeed.views.RabbitMQClient.publish_on_commit") as publish:
                    response = self.feed(username, viewcache=[{"post_id": "public", "duration": 1}])
                self.assertEqual(response.status_code, 200, response.data)
                publish.assert_not_called()

    def test_private_user_and_community_profiles_remain_hidden_from_guests(self):
        Account.objects.filter(pk="account").update(is_private=True)
        Realm.objects.filter(pk="community").update(is_private=True)
        for username in ("person", "community-slug"):
            with self.subTest(username=username):
                response = self.feed(username)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data["results"], [])

    def test_authenticated_viewcache_still_uses_the_acting_entity(self):
        viewcache = [{"post_id": "public", "duration": 1}]
        with patch("newsfeed.views.RabbitMQClient.publish_on_commit") as publish:
            response = self.feed("person", viewer=Entity.objects.get(pk="page"), viewcache=viewcache)
        self.assertEqual(response.status_code, 200, response.data)
        # The existing endpoint skips engagements when visiting your own username.
        publish.assert_not_called()
        with patch("newsfeed.views.RabbitMQClient.publish_on_commit") as publish:
            response = self.feed("page-slug", viewer=Entity.objects.get(pk="page"), viewcache=viewcache)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(publish.call_args.args[1], {"entity_id": "page", "view_cache": viewcache})
