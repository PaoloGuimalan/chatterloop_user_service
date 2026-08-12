import uuid
from unittest import mock

from django.test import TestCase

from community.models import Realm
from entity.models import Block, Entity, Report
from entity.services.blocking import is_blocked
from entity.services.reporting import ReportTargetError, create_report
from newsfeed.models import Comment, Post
from user.models import Account


def make_user(username):
    entity = Entity.objects.create(type="user")
    Account.objects.create(
        entity=entity,
        first_name="Test",
        last_name="User",
        email=f"{uuid.uuid4()}@example.com",
        username=username,
    )
    return entity


def make_realm(name, creator, realm_type="page"):
    realm_entity = Entity.objects.create(type="realm")
    realm = Realm.objects.create(
        entity=realm_entity,
        name=name,
        created_by=creator,
        type=realm_type,
    )
    return realm


class ReportTargetResolutionTests(TestCase):
    def setUp(self):
        self.reporter = make_user("reporter1")
        self.offender = make_user("offender1")

    def test_report_user_stores_entity_and_nulls_target_id(self):
        report, created = create_report(
            self.reporter, "user", self.offender.id, "spam"
        )
        self.assertTrue(created)
        self.assertEqual(str(report.reported_entity_id), str(self.offender.id))
        self.assertEqual(report.target_type, "user")
        # Entity-level targets carry no artefact id.
        self.assertIsNone(report.target_id)

    def test_report_realm_by_entity_id(self):
        realm = make_realm("Neon Systems", self.offender)
        report, created = create_report(
            self.reporter, "realm", realm.entity_id, "hate_speech"
        )
        self.assertTrue(created)
        self.assertEqual(str(report.reported_entity_id), str(realm.entity_id))
        self.assertIsNone(report.target_id)

    def test_report_realm_by_realm_id(self):
        """Server/member screens hold the realm pk, not the entity id."""
        realm = make_realm("Neon Guild", self.offender, realm_type="server")
        report, _ = create_report(self.reporter, "realm", realm.id, "spam")
        self.assertEqual(str(report.reported_entity_id), str(realm.entity_id))

    def test_report_post_lands_on_the_author_entity(self):
        post = Post.objects.create(
            entity=self.offender,
            file_type="image",
            content_type="post",
            on_feed="true",
        )
        report, _ = create_report(self.reporter, "post", post.post_id, "nudity")
        self.assertEqual(str(report.reported_entity_id), str(self.offender.id))
        # Content-level targets keep the artefact id.
        self.assertEqual(report.target_id, str(post.post_id))

    def test_report_post_authored_by_a_page_lands_on_the_page(self):
        realm = make_realm("Neon Systems", self.offender)
        post = Post.objects.create(
            entity=realm.entity,
            file_type="image",
            content_type="post",
            on_feed="true",
        )
        report, _ = create_report(self.reporter, "post", post.post_id, "spam")
        self.assertEqual(str(report.reported_entity_id), str(realm.entity_id))

    def test_report_comment_lands_on_the_commenter_entity(self):
        post = Post.objects.create(
            entity=self.reporter,
            file_type="image",
            content_type="post",
            on_feed="true",
        )
        comment = Comment.objects.create(
            post=post, entity=self.offender, text="rude thing"
        )
        report, _ = create_report(
            self.reporter, "comment", comment.comment_id, "harassment"
        )
        # The COMMENTER is reported, not the post's author - even though the
        # post here is the reporter's own.
        self.assertEqual(str(report.reported_entity_id), str(self.offender.id))
        self.assertEqual(report.target_id, str(comment.comment_id))

    def test_report_comment_authored_by_a_page_lands_on_the_page(self):
        realm = make_realm("Neon Systems", self.offender)
        post = Post.objects.create(
            entity=self.offender,
            file_type="image",
            content_type="post",
            on_feed="true",
        )
        comment = Comment.objects.create(
            post=post, entity=realm.entity, text="spam"
        )
        report, _ = create_report(
            self.reporter, "comment", comment.comment_id, "spam"
        )
        self.assertEqual(str(report.reported_entity_id), str(realm.entity_id))

    def test_deleted_comment_is_not_reportable(self):
        from django.utils.timezone import now

        post = Post.objects.create(
            entity=self.reporter,
            file_type="image",
            content_type="post",
            on_feed="true",
        )
        comment = Comment.objects.create(
            post=post, entity=self.offender, text="gone", deleted_at=now()
        )
        with self.assertRaises(ReportTargetError) as ctx:
            create_report(self.reporter, "comment", comment.comment_id, "spam")
        self.assertTrue(ctx.exception.not_found)

    def test_cannot_report_your_own_comment(self):
        post = Post.objects.create(
            entity=self.offender,
            file_type="image",
            content_type="post",
            on_feed="true",
        )
        comment = Comment.objects.create(
            post=post, entity=self.reporter, text="mine"
        )
        with self.assertRaises(ReportTargetError):
            create_report(self.reporter, "comment", comment.comment_id, "spam")

    def test_deleted_post_is_not_reportable(self):
        from django.utils.timezone import now

        post = Post.objects.create(
            entity=self.offender,
            file_type="image",
            content_type="post",
            on_feed="true",
            deleted_at=now(),
        )
        with self.assertRaises(ReportTargetError) as ctx:
            create_report(self.reporter, "post", post.post_id, "spam")
        self.assertTrue(ctx.exception.not_found)

    def test_cannot_report_yourself(self):
        with self.assertRaises(ReportTargetError):
            create_report(self.reporter, "user", self.reporter.id, "spam")

    def test_cannot_report_a_page_as_a_user_target(self):
        realm = make_realm("Neon Systems", self.offender)
        with self.assertRaises(ReportTargetError):
            create_report(self.reporter, "user", realm.entity_id, "spam")

    def test_invalid_reason_and_target_type_rejected(self):
        with self.assertRaises(ReportTargetError):
            create_report(self.reporter, "user", self.offender.id, "not_a_reason")
        with self.assertRaises(ReportTargetError):
            create_report(self.reporter, "banana", self.offender.id, "spam")

    def test_missing_target_id_rejected(self):
        with self.assertRaises(ReportTargetError):
            create_report(self.reporter, "user", None, "spam")

    def test_duplicate_pending_report_is_a_no_op(self):
        first, created_first = create_report(
            self.reporter, "user", self.offender.id, "spam"
        )
        second, created_second = create_report(
            self.reporter, "user", self.offender.id, "harassment"
        )
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(str(first.id), str(second.id))
        self.assertEqual(Report.objects.count(), 1)

    def test_resolved_report_does_not_block_a_new_one(self):
        first, _ = create_report(self.reporter, "user", self.offender.id, "spam")
        first.status = "dismissed"
        first.save(update_fields=["status"])

        second, created = create_report(
            self.reporter, "user", self.offender.id, "harassment"
        )
        self.assertTrue(created)
        self.assertNotEqual(str(first.id), str(second.id))

    def test_two_reporters_file_separate_reports(self):
        other_reporter = make_user("reporter2")
        create_report(self.reporter, "user", self.offender.id, "spam")
        create_report(other_reporter, "user", self.offender.id, "spam")
        self.assertEqual(
            Report.objects.filter(reported_entity=self.offender).count(), 2
        )


class ReportMessageTests(TestCase):
    """The message branch, with Mongo mocked.

    Deliberately never touches the real `messages` collection: it is not
    test-isolated the way Postgres is here, so inserting a fixture would write
    to live chat data.
    """

    def setUp(self):
        self.reporter = make_user("msgreporter")
        self.offender = make_user("msgoffender")

    def _report_with_sender(self, sender_id):
        with mock.patch(
            "user.ext_models.mongomodels.Message._get_collection"
        ) as get_collection:
            get_collection.return_value.find_one.return_value = {
                "messageID": "msg-123",
                "sender": sender_id,
            }
            return create_report(self.reporter, "message", "msg-123", "harassment")

    def test_sender_stored_as_entity_id_resolves(self):
        """What real docs actually hold - sender is an entity id."""
        report, created = self._report_with_sender(str(self.offender.id))
        self.assertTrue(created)
        self.assertEqual(str(report.reported_entity_id), str(self.offender.id))
        self.assertEqual(report.target_id, "msg-123")

    def test_sender_stored_as_account_id_still_resolves(self):
        """Older docs wrote the account id; both must land on the entity."""
        account = Account.objects.get(entity=self.offender)
        report, created = self._report_with_sender(str(account.id))
        self.assertTrue(created)
        self.assertEqual(str(report.reported_entity_id), str(self.offender.id))

    def test_unknown_sender_is_not_found(self):
        with self.assertRaises(ReportTargetError) as ctx:
            self._report_with_sender("nobody-at-all")
        self.assertTrue(ctx.exception.not_found)

    def test_missing_message_is_not_found(self):
        with mock.patch(
            "user.ext_models.mongomodels.Message._get_collection"
        ) as get_collection:
            get_collection.return_value.find_one.return_value = None
            with self.assertRaises(ReportTargetError) as ctx:
                create_report(self.reporter, "message", "gone", "spam")
        self.assertTrue(ctx.exception.not_found)

    def test_cannot_report_your_own_message(self):
        with self.assertRaises(ReportTargetError):
            self._report_with_sender(str(self.reporter.id))


class BlockEntityTests(TestCase):
    def setUp(self):
        self.blocker = make_user("blocker1")

    def test_a_page_can_be_blocked(self):
        owner = make_user("pageowner1")
        realm = make_realm("Neon Systems", owner)
        Block.objects.create(blocker=self.blocker, blocked=realm.entity)
        self.assertTrue(is_blocked(self.blocker, realm.entity))
        # The owner is a separate entity and must not be caught by the block.
        self.assertFalse(is_blocked(self.blocker, owner))
