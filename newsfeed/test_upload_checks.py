"""
Comments and diary entries may only carry files their author uploaded through
Node's /media/uploads (newsfeed/services/media_release.resolve_own_upload).

Database-free on purpose (SimpleTestCase, Mongo stubbed): the runner then sets
up no test database, so these never reach the live services the full suite
connects to.

    manage.py test newsfeed.test_upload_checks
"""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from diary.views import DiaryCRUDView
from newsfeed.services import media_release
from newsfeed.services.media_release import (
    UploadRejected,
    resolve_comment_upload,
    resolve_own_upload,
)

MINE = "https://media.example.invalid/uploads/diaries/acc-1/x/Trip%20plan.pdf"


def _record(**overrides):
    fields = {
        "fileID": "FILE_1",
        "version": 2,
        "status": "ready",
        "purpose": "diary",
        "ownerAccount": "acc-1",
        "name": "Trip plan.pdf",
        "mime": "application/pdf",
        "fileDetails": {"data": MINE},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _files(record):
    """Stands in for UploadedFile.objects: every lookup finds `record`."""
    found = mock.Mock()
    found.first.return_value = record
    return mock.patch.object(
        media_release.UploadedFile, "objects", mock.Mock(return_value=found)
    )


class ResolveOwnUploadTests(SimpleTestCase):
    def test_the_authors_confirmed_upload_is_returned(self):
        record = _record()
        with _files(record):
            self.assertIs(resolve_own_upload(MINE, "acc-1", "diary"), record)

    def test_anything_else_is_refused(self):
        cases = {
            "an external link": None,
            "someone else's file": _record(ownerAccount="acc-2"),
            "an unconfirmed upload": _record(status="pending"),
            "a file uploaded for something else": _record(purpose="post_media"),
        }
        for label, record in cases.items():
            with self.subTest(label), _files(record):
                with self.assertRaises(UploadRejected):
                    resolve_own_upload(MINE, "acc-1", "diary")

    def test_a_missing_or_odd_link_is_refused(self):
        with _files(_record()):
            for value in (None, "", "   ", {"url": MINE}):
                with self.subTest(value=value), self.assertRaises(UploadRejected):
                    resolve_own_upload(value, "acc-1", "diary")


class CommentAttachmentTests(SimpleTestCase):
    def test_no_attachment_is_fine(self):
        # A text comment - links typed in it included - has no attachment.
        with _files(None):
            self.assertIsNone(resolve_comment_upload(None, "acc-1"))
            self.assertIsNone(resolve_comment_upload("", "acc-1"))

    def test_an_attachment_must_be_an_own_comment_upload(self):
        with _files(None), self.assertRaises(UploadRejected):
            resolve_comment_upload("https://example.invalid/cat.png", "acc-1")
        with _files(_record(purpose="comment")):
            self.assertIsNotNone(resolve_comment_upload(MINE, "acc-1"))


class DiaryAttachmentTests(SimpleTestCase):
    def _post(self, attachments):
        request = APIRequestFactory().post(
            "/api/diary/entry/",
            {
                "title": "Holiday",
                "content": "<p>Packing list attached.</p>",
                "entry_date": "2026-10-01",
                "is_private": True,
                "tags": [],
                "attachments": attachments,
            },
            format="json",
        )
        force_authenticate(request, user=SimpleNamespace(id="acc-1", is_authenticated=True))
        return DiaryCRUDView.as_view()(request)

    def test_an_attachment_that_isnt_an_own_upload_is_refused_before_saving(self):
        # Refused before the transaction: SimpleTestCase fails any database
        # query, so reaching one would error rather than answer 400.
        with _files(None):
            response = self._post(
                [{"url": "https://example.invalid/x.pdf", "file_name": "x.pdf"}]
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("uploaded to Chatterloop", response.data["message"])
