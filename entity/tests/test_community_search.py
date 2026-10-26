"""Search directory contracts with real SQLite query evaluation, no migrations."""

# Django must initialize the offline app registry before model/view imports.
# ruff: noqa: E402

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from django.db import connection
from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Member, Realm
from entity.models import Entity, Follow
from entity.search_views import (
    SearchOverviewV2,
    SearchRealmsV2,
    build_realms_queryset,
    normalize_realm,
)
from entity.views import EntitySearch


class CommunitySearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.models = (Entity, Realm, Member, Follow)
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
        self.viewer = Entity.objects.create(id="viewer", type="user")
        self.factory = APIRequestFactory()
        self.add_realm("public", "community")
        self.add_realm("joined", "community", private=True, joined=True)
        self.add_realm("hidden", "community", private=True)
        self.add_realm("inactive", "community", active=False, joined=True)
        for kind in ("page", "server", "group", "channel", "conference", "voice"):
            self.add_realm(kind, kind)
        self.add_realm("private-page", "page", private=True, joined=True)

    def tearDown(self):
        # QuerySet.delete follows unrelated model relations, whose tables are
        # intentionally absent from this small isolated database.
        with connection.cursor() as cursor:
            for model in reversed(self.models):
                cursor.execute(f'DELETE FROM "{model._meta.db_table}"')

    def add_realm(self, key, kind, *, private=False, active=True, joined=False):
        entity = Entity.objects.create(id=key, type="realm")
        realm = Realm(
            id=key,
            realm_id=key,
            entity=entity,
            created_by=self.viewer,
            name=f"Garden {key}",
            slug=f"garden-{key}",
            type=kind,
            is_private=private,
            is_active=active,
            is_verified=True,
        )
        Realm.objects.bulk_create([realm])
        if joined:
            Member.objects.bulk_create(
                [Member(entity=self.viewer, realm=realm, added_by=self.viewer)]
            )
        return realm

    def request(self, view, path, **params):
        request = self.factory.get(path, params)
        request.entity = self.viewer
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with patch("entity.search_views.get_blocked_account_ids", return_value=[]):
            return view.as_view()(request, query="garden")

    def test_general_search_includes_public_and_joined_private_communities(self):
        rows = list(build_realms_queryset(self.viewer, "garden", [], ["all"]))
        self.assertEqual(
            {row["id"] for row in rows},
            {"public", "joined", "page", "server", "group"},
        )
        self.assertTrue(next(row for row in rows if row["id"] == "joined")["is_member"])
        Member.objects.filter(realm_id="joined").delete()
        self.assertNotIn(
            "joined",
            {
                row["id"]
                for row in build_realms_queryset(self.viewer, "garden", [], ["all"])
            },
        )

    def test_type_handle_and_block_filters_preserve_other_realm_kinds(self):
        for kinds, expected in (
            (["community"], {"public", "joined"}),
            (["page", "community"], {"public", "joined", "page"}),
            (["page", "server", "group"], {"page", "server", "group"}),
            (["channel", "voice", "conference"], set()),
        ):
            with self.subTest(kinds=kinds):
                rows = build_realms_queryset(self.viewer, "@garden", [], kinds)
                self.assertEqual({row["id"] for row in rows}, expected)
        rows = build_realms_queryset(self.viewer, "@garden-public", ["public"], ["all"])
        self.assertEqual(list(rows), [])
        self.assertEqual(
            list(build_realms_queryset(self.viewer, "no-such-name", [], ["all"])), []
        )

    def test_community_projection_contains_only_directory_metadata(self):
        rows = build_realms_queryset(self.viewer, "garden-public", [], ["community"])
        self.assertEqual(len(rows), 1)
        data = normalize_realm(rows[0])
        self.assertEqual(
            data,
            {
                "id": "public",
                "entity_id": "public",
                "type": "realm",
                "realm_type": "community",
                "display_name": "Garden public",
                "handle": "garden-public",
                "profile": None,
                "is_verified": True,
                "members_count": 0,
                "followers_count": 0,
                "is_member": False,
                "is_follower": False,
            },
        )

    def test_paginated_and_overview_search_include_community_results(self):
        detail = self.request(
            SearchRealmsV2,
            "/api/entity/search/v2/realms/garden/",
            realm_types="community",
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["count"], 2)
        self.assertEqual(
            {row["id"] for row in detail.data["results"]}, {"public", "joined"}
        )
        with (
            patch("entity.search_views.build_people_queryset", return_value=[]),
            patch("entity.search_views.build_bots_queryset", return_value=[]),
            patch(
                "newsfeed.services.post_search.build_post_search_queryset",
                return_value=[],
            ),
            patch("interests.services.topics.build_topic_queryset", return_value=[]),
            patch("interests.services.topics.serialize_topics", return_value=[]),
        ):
            overview = self.request(
                SearchOverviewV2, "/api/entity/search/v2/overview/garden/"
            )
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(
            {row["id"] for row in overview.data["result"]["realms"]["results"]},
            {"public", "joined", "page", "server", "group"},
        )

    def test_flat_entity_picker_still_excludes_communities_even_when_requested(self):
        for realm_types in ("all", "community", "page,community"):
            request = self.factory.get(
                "/api/entity/search/garden/",
                {"types": "realm", "realm_types": realm_types},
            )
            request.entity = self.viewer
            force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
            with patch("entity.views.get_blocked_account_ids", return_value=[]):
                response = EntitySearch.as_view()(request, query="garden")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(
                "community", {row["realm_type"] for row in response.data["results"]}
            )
