"""Community creation requires an explicit, usable profile slug."""

# Initialize the offline registry before application imports.
# ruff: noqa: E402

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate

from community.api_serializers import CommunityCreateSerializer
from community.community_views import CommunitiesView
from core.profile_paths import RESERVED_PROFILE_PATHS
from entity.models import Entity


class CommunityCreationSlugTests(unittest.TestCase):
    def create(self, data):
        request = APIRequestFactory().post("/api/communities/", data, format="json")
        request.entity = Entity(id="creator", type="user")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with (
            patch("community.community_views.has_permission", return_value=True),
            patch("community.community_views.Entity.objects.create") as create_entity,
            patch("community.community_views.Realm.objects.create") as create_realm,
        ):
            response = CommunitiesView.as_view()(request)
        create_entity.assert_not_called()
        create_realm.assert_not_called()
        return response

    def test_missing_blank_and_null_slug_rejected_before_creating_anything(self):
        for extra, code in (
            ({}, "required"),
            ({"slug": ""}, "blank"),
            ({"slug": "   "}, "blank"),
            ({"slug": None}, "null"),
        ):
            with self.subTest(extra=extra):
                data = {"name": "Readers", **extra}
                serializer = CommunityCreateSerializer(data=data)
                self.assertFalse(serializer.is_valid())
                response = self.create(data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["slug"][0].code, code)

    def test_valid_slug_is_retained_without_generation(self):
        serializer = CommunityCreateSerializer(
            data={"name": "Readers", "slug": " readers-club ", "is_private": True}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["slug"], "readers-club")
        self.assertTrue(serializer.validated_data["is_private"])

    def test_reserved_slug_preserves_existing_error_contract(self):
        with patch(
            "core.profile_paths.get_reserved_profile_paths",
            return_value=RESERVED_PROFILE_PATHS,
        ):
            response = self.create({"name": "Readers", "slug": "settings"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data["status"])
        self.assertEqual(response.data["field"], "slug")
        self.assertEqual(response.data["code"], "RESERVED_PATH")
