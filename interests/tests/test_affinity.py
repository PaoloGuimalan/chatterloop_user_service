from django.test import TestCase

from entity.models import Entity
from interests.models import EntityInterestAffinity, Interest, InterestTrendingScore
from interests.services.affinity import bump_interest_affinity


def _make_entity():
    return Entity.objects.create(type="user")


class BumpInterestAffinityTests(TestCase):
    def setUp(self):
        self.entity = _make_entity()
        self.hiking = Interest.objects.create(name="Hiking")

    def test_bump_creates_both_affinity_and_trending_rows(self):
        bump_interest_affinity(self.entity.id, [self.hiking.id], "LIKE", False)

        affinity = EntityInterestAffinity.objects.get(entity=self.entity, interest=self.hiking)
        trending = InterestTrendingScore.objects.get(interest=self.hiking)

        self.assertEqual(affinity.score, 1.0)
        self.assertEqual(trending.score, 1.0)

    def test_bump_accumulates_across_actions(self):
        bump_interest_affinity(self.entity.id, [self.hiking.id], "LIKE", False)
        bump_interest_affinity(self.entity.id, [self.hiking.id], "COMMENT", False)

        affinity = EntityInterestAffinity.objects.get(entity=self.entity, interest=self.hiking)
        # LIKE (1.0) + COMMENT (4.0)
        self.assertEqual(affinity.score, 5.0)

    def test_bump_decrease_on_unlike(self):
        bump_interest_affinity(self.entity.id, [self.hiking.id], "LIKE", False)
        bump_interest_affinity(self.entity.id, [self.hiking.id], "LIKE", True)

        affinity = EntityInterestAffinity.objects.get(entity=self.entity, interest=self.hiking)
        trending = InterestTrendingScore.objects.get(interest=self.hiking)

        self.assertEqual(affinity.score, 0.0)
        self.assertEqual(trending.score, 0.0)

    def test_unknown_action_is_a_no_op(self):
        bump_interest_affinity(self.entity.id, [self.hiking.id], "UNKNOWN_ACTION", False)
        self.assertFalse(EntityInterestAffinity.objects.filter(entity=self.entity).exists())

    def test_empty_interest_ids_is_a_no_op(self):
        bump_interest_affinity(self.entity.id, [], "LIKE", False)
        self.assertFalse(EntityInterestAffinity.objects.filter(entity=self.entity).exists())
