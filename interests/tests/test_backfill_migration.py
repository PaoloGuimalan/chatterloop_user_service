import importlib
import uuid

from django.apps import apps
from django.test import TestCase

from diary.models import Entry
from entity.models import Entity
from entity.permissions import PermissionEffect
from interests.models import EntityInterest, EntityInterestAffinity, Interest, InterestTrendingScore
from user.models import Account

# Migration filenames aren't valid Python identifiers (leading digit), so
# this can't be a normal "from ... import ..." statement - importlib.
# import_module accepts arbitrary dotted strings even though the plain
# import statement wouldn't.
backfill_module = importlib.import_module(
    "interests.migrations.0004_backfill_diary_tag_affinity"
)
backfill_grants_module = importlib.import_module(
    "interests.migrations.0005_backfill_diary_tag_grant_overrides"
)


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


class BackfillDiaryTagAffinityTests(TestCase):
    def test_backfill_seeds_personal_and_trending_scores_from_existing_usage(self):
        account = _make_account()
        other_account = _make_account()
        hiking = Interest.objects.create(name="Hiking")

        # Entry.tags.set() bypasses EntrySerializer/_handle_tags entirely,
        # so no EntityInterestAffinity/InterestTrendingScore rows get
        # created here - this reproduces the exact real-world situation the
        # backfill migration targets: pre-existing diary_entry_tags rows
        # with zero affinity/trending data behind them.
        entry1 = Entry.objects.create(
            account=account, title="A", content="a", entry_date="2026-01-01"
        )
        entry1.tags.set([hiking])
        entry2 = Entry.objects.create(
            account=account, title="B", content="b", entry_date="2026-01-02"
        )
        entry2.tags.set([hiking])
        entry3 = Entry.objects.create(
            account=other_account, title="C", content="c", entry_date="2026-01-03"
        )
        entry3.tags.set([hiking])

        self.assertFalse(EntityInterestAffinity.objects.exists())
        self.assertFalse(InterestTrendingScore.objects.exists())

        backfill_module.backfill_diary_tag_affinity(apps, None)

        personal = EntityInterestAffinity.objects.get(
            entity_id=account.entity_id, interest=hiking
        )
        self.assertEqual(personal.score, 10.0)  # 2 historical uses * 5.0

        other_personal = EntityInterestAffinity.objects.get(
            entity_id=other_account.entity_id, interest=hiking
        )
        self.assertEqual(other_personal.score, 5.0)  # 1 historical use * 5.0

        trending = InterestTrendingScore.objects.get(interest=hiking)
        self.assertEqual(trending.score, 15.0)  # 3 historical uses total * 5.0

    def test_backfill_is_idempotent_when_rerun(self):
        account = _make_account()
        hiking = Interest.objects.create(name="Hiking")
        entry = Entry.objects.create(
            account=account, title="A", content="a", entry_date="2026-01-01"
        )
        entry.tags.set([hiking])

        backfill_module.backfill_diary_tag_affinity(apps, None)
        backfill_module.backfill_diary_tag_affinity(apps, None)

        personal = EntityInterestAffinity.objects.get(
            entity_id=account.entity_id, interest=hiking
        )
        self.assertEqual(personal.score, 5.0)


class BackfillDiaryTagGrantOverridesTests(TestCase):
    def test_backfill_creates_explicit_grant_for_existing_diary_tags(self):
        account = _make_account()
        hiking = Interest.objects.create(name="Hiking")
        entry = Entry.objects.create(
            account=account, title="A", content="a", entry_date="2026-01-01"
        )
        entry.tags.set([hiking])

        self.assertFalse(EntityInterest.objects.exists())

        backfill_grants_module.backfill_diary_tag_grant_overrides(apps, None)

        override = EntityInterest.objects.get(entity_id=account.entity_id, interest=hiking)
        self.assertEqual(override.effect, PermissionEffect.GRANT)

    def test_backfill_does_not_overwrite_existing_explicit_deny(self):
        account = _make_account()
        hiking = Interest.objects.create(name="Hiking")
        entry = Entry.objects.create(
            account=account, title="A", content="a", entry_date="2026-01-01"
        )
        entry.tags.set([hiking])
        EntityInterest.objects.create(
            entity_id=account.entity_id, interest=hiking, effect=PermissionEffect.DENY
        )

        backfill_grants_module.backfill_diary_tag_grant_overrides(apps, None)

        override = EntityInterest.objects.get(entity_id=account.entity_id, interest=hiking)
        self.assertEqual(override.effect, PermissionEffect.DENY)

    def test_backfill_is_idempotent_when_rerun(self):
        account = _make_account()
        hiking = Interest.objects.create(name="Hiking")
        entry = Entry.objects.create(
            account=account, title="A", content="a", entry_date="2026-01-01"
        )
        entry.tags.set([hiking])

        backfill_grants_module.backfill_diary_tag_grant_overrides(apps, None)
        backfill_grants_module.backfill_diary_tag_grant_overrides(apps, None)

        self.assertEqual(EntityInterest.objects.filter(entity_id=account.entity_id).count(), 1)
