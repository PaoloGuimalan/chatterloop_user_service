import uuid

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from diary.models import Entry
from diary.serializers import EntrySerializer
from diary.views import DiaryCRUDView, DiaryTotalView
from entity.permissions import PermissionEffect
from interests.models import EntityInterest, EntityInterestAffinity, Interest, InterestTrendingScore
from user.models import Account
from entity.models import Entity


def _make_account():
    entity = Entity.objects.create(type="user")
    return Account.objects.create(
        entity=entity,
        first_name="Test",
        last_name="User",
        email=f"{uuid.uuid4()}@example.com",
        is_active=True,
        is_verified=True,
    )


class TagsMigrationRoundTripTests(TestCase):
    """
    Live round-trip against the real post-migration schema (Django's test
    runner applies every migration before running tests, so this doubles as
    a correctness check on the SeparateDatabaseAndState moves themselves,
    not just the model definitions).
    """

    def test_tags_m2m_survives_interests_migration(self):
        account = _make_account()
        entry = Entry.objects.create(
            account=account,
            title="Trip",
            content="Went hiking today.",
            entry_date="2026-01-01",
        )
        hiking = Interest.objects.create(name="Hiking")
        camping = Interest.objects.create(name="Camping")

        entry.tags.set([hiking, camping])

        self.assertEqual(set(entry.tags.values_list("name", flat=True)), {"Hiking", "Camping"})
        self.assertIn(entry, hiking.entries.all())


class UnifiedTagCreationTests(TestCase):
    """
    Regression test for the pre-existing inconsistency this move fixed:
    EntrySerializer._handle_tags used Tag.objects.get_or_create(name=...)
    while DiaryCRUDView.post used a separate bulk_create(ignore_conflicts=
    True) path with weaker dedup. Both should now resolve identically via
    Interest.objects.get_or_create_by_name.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.account = _make_account()

    def test_serializer_and_view_produce_identical_interest_for_same_name(self):
        entry = Entry.objects.create(
            account=self.account,
            title="Via serializer",
            content="content",
            entry_date="2026-01-01",
        )
        serializer = EntrySerializer(
            entry, data={"tags": ["Mountain Biking"]}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        request = self.factory.post(
            "/api/diary/entry/",
            {
                "title": "Via view",
                "content": "content",
                "entry_date": "2026-01-01",
                "mood": None,
                "tags": [{"name": "mountain biking", "is_new": True}],
                "is_private": True,
                "attachments": [],
            },
            format="json",
        )
        force_authenticate(request, user=self.account)
        request.user = self.account
        response = DiaryCRUDView.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Interest.objects.filter(normalized_name="mountain biking").count(), 1)


class DiaryTagAffinityBumpTests(TestCase):
    """
    Tagging a diary entry is a personal interest signal - it should bump
    both this entity's own affinity ranking and the interest's global
    trending score, the same way reacting/commenting on a post does.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.account = _make_account()

    def test_serializer_tag_handling_bumps_affinity_and_trending(self):
        entry = Entry.objects.create(
            account=self.account,
            title="Trip",
            content="Went hiking today.",
            entry_date="2026-01-01",
        )
        serializer = EntrySerializer(entry, data={"tags": ["Hiking"]}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        hiking = Interest.objects.get(normalized_name="hiking")
        affinity = EntityInterestAffinity.objects.get(
            entity_id=self.account.entity_id, interest=hiking
        )
        trending = InterestTrendingScore.objects.get(interest=hiking)
        self.assertEqual(affinity.score, 5.0)
        self.assertEqual(trending.score, 5.0)

        override = EntityInterest.objects.get(
            entity_id=self.account.entity_id, interest=hiking
        )
        self.assertEqual(override.effect, PermissionEffect.GRANT)

    def test_view_tag_handling_bumps_affinity_and_trending(self):
        request = self.factory.post(
            "/api/diary/entry/",
            {
                "title": "Via view",
                "content": "content",
                "entry_date": "2026-01-01",
                "mood": None,
                "tags": [{"name": "Camping", "is_new": True}],
                "is_private": True,
                "attachments": [],
            },
            format="json",
        )
        force_authenticate(request, user=self.account)
        request.user = self.account
        response = DiaryCRUDView.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 200)
        camping = Interest.objects.get(normalized_name="camping")
        affinity = EntityInterestAffinity.objects.get(
            entity_id=self.account.entity_id, interest=camping
        )
        self.assertEqual(affinity.score, 5.0)

        override = EntityInterest.objects.get(
            entity_id=self.account.entity_id, interest=camping
        )
        self.assertEqual(override.effect, PermissionEffect.GRANT)


class DiaryTopTagsRankingTests(TestCase):
    def test_top_tags_ranked_by_personal_affinity_not_raw_count(self):
        account = _make_account()
        frequent_tag = Interest.objects.create(name="Frequent")
        favorite_tag = Interest.objects.create(name="Favorite")

        entry1 = Entry.objects.create(
            account=account, title="A", content="a", entry_date="2026-01-01"
        )
        entry1.tags.set([frequent_tag])
        entry2 = Entry.objects.create(
            account=account, title="B", content="b", entry_date="2026-01-02"
        )
        entry2.tags.set([frequent_tag])
        entry3 = Entry.objects.create(
            account=account, title="C", content="c", entry_date="2026-01-03"
        )
        entry3.tags.set([favorite_tag])

        # frequent_tag used on 2 entries, favorite_tag on just 1 - but
        # favorite_tag has a much higher personal affinity score, so it
        # should rank first despite the lower raw usage count.
        EntityInterestAffinity.objects.create(
            entity_id=account.entity_id, interest=favorite_tag, score=50.0
        )
        EntityInterestAffinity.objects.create(
            entity_id=account.entity_id, interest=frequent_tag, score=1.0
        )

        request = APIRequestFactory().get(f"/api/diary/total/{account.username}/")
        response = DiaryTotalView.as_view()(request, username=account.username)
        response.render()

        self.assertEqual(response.status_code, 200)
        names = [tag["name"] for tag in response.data["top_tags"]]
        self.assertEqual(names, ["Favorite", "Frequent"])
