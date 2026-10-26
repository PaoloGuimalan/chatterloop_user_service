"""Community photos must use the validated upload and feed-post transaction."""

# ruff: noqa: E402
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community.offline_test_settings")
import django

django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate

from community.models import Realm
from community.views import MyRealms
from entity.models import Entity


class CommunityMediaUpdateTests(unittest.TestCase):
    def update(self, realm_type, fields):
        realm = Realm(
            id="realm", realm_id="realm", entity_id="community", type=realm_type
        )
        request = APIRequestFactory().put(
            "/api/community/", {"realm_id": "realm", "fields": fields}, format="json"
        )
        request.entity = Entity(id="admin", type="user")
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
        with (
            patch("community.views.get_object_or_404", return_value=realm),
            patch("community.views.has_permission", return_value=True),
            patch("community.views.Realm.objects.filter") as records,
        ):
            response = MyRealms.as_view()(request)
        return response, records.return_value.update.call_args_list

    def test_community_photo_urls_cannot_bypass_upload_post_creation(self):
        for media_type in ("profile", "cover_photo"):
            with self.subTest(media_type=media_type):
                response, mutations = self.update(
                    "community", {media_type: "https://example.invalid/photo.jpg"}
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("media upload", response.data["message"])
                self.assertEqual(mutations, [])

    def test_community_details_still_update_normally(self):
        response, mutations = self.update("community", {"description": "New details"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mutations[0].kwargs, {"description": "New details"})

    def test_page_media_settings_preserve_existing_behavior(self):
        response, mutations = self.update(
            "page", {"profile": "https://example.invalid/photo.jpg"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mutations[0].kwargs, {"profile": "https://example.invalid/photo.jpg"}
        )
