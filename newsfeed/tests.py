from django.test import TestCase

from entity.models import Entity
from entity.permissions import PermissionEffect
from interests.models import EntityInterest, EntityInterestAffinity, Interest
from newsfeed.helpers.query_functions import (
    MAX_TRENDING_CATEGORIES,
    resolved_interest_categories,
)


def _make_entity():
    return Entity.objects.create(type="user")


class ResolvedInterestCategoriesTests(TestCase):
    """
    TrendingPool.category is queried with category__in=user_interests - a
    Cassandra/Scylla query that hard-rejects more than 25 IN values
    ("cartesian product of all values in IN conditions is greater than 25").
    An entity can accumulate far more than that many granted/high-affinity
    interests over time (e.g. via diary tagging), so this must stay capped.
    """

    def test_result_never_exceeds_max_categories_plus_global(self):
        entity = _make_entity()
        for i in range(MAX_TRENDING_CATEGORIES + 10):
            interest = Interest.objects.create(name=f"Interest {i}")
            EntityInterest.objects.create(
                entity=entity, interest=interest, effect=PermissionEffect.GRANT
            )

        categories = resolved_interest_categories(entity)
        self.assertLessEqual(len(categories), MAX_TRENDING_CATEGORIES + 1)
        self.assertIn("global", categories)

    def test_grants_fill_cap_before_implicit_affinity(self):
        entity = _make_entity()
        for i in range(MAX_TRENDING_CATEGORIES):
            interest = Interest.objects.create(name=f"Granted {i}")
            EntityInterest.objects.create(
                entity=entity, interest=interest, effect=PermissionEffect.GRANT
            )
        # High-affinity interests beyond the grant-filled cap should be
        # excluded, not overflow the query past the Cassandra limit.
        overflow_interest = Interest.objects.create(name="Overflow")
        EntityInterestAffinity.objects.create(
            entity=entity, interest=overflow_interest, score=100.0
        )

        categories = resolved_interest_categories(entity)
        self.assertNotIn("overflow", categories)
        self.assertLessEqual(len(categories), MAX_TRENDING_CATEGORIES + 1)

    def test_small_entity_returns_all_signals_plus_global(self):
        entity = _make_entity()
        hiking = Interest.objects.create(name="Hiking")
        cooking = Interest.objects.create(name="Cooking")
        EntityInterest.objects.create(
            entity=entity, interest=hiking, effect=PermissionEffect.GRANT
        )
        EntityInterestAffinity.objects.create(entity=entity, interest=cooking, score=10.0)

        categories = resolved_interest_categories(entity)
        self.assertEqual(set(categories), {"hiking", "cooking", "global"})
