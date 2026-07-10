import uuid

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from entity.models import Entity
from interests.models import EntityInterestAffinity, Interest, InterestTrendingScore
from interests.views import MyTopInterestsView, TrendingInterestsView
from user.models import Account


def _make_entity(entity_type="user"):
    return Entity.objects.create(type=entity_type)


def _make_account(entity):
    return Account.objects.create(
        entity=entity,
        first_name="Test",
        last_name="User",
        email=f"{uuid.uuid4()}@example.com",
        is_active=True,
        is_verified=True,
    )


class RankingViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.entity = _make_entity()
        self.account = _make_account(self.entity)
        self.hiking = Interest.objects.create(name="Hiking")
        self.cooking = Interest.objects.create(name="Cooking")

    def test_my_top_interests_scoped_and_ranked(self):
        other_entity = _make_entity()
        EntityInterestAffinity.objects.create(entity=self.entity, interest=self.hiking, score=10.0)
        EntityInterestAffinity.objects.create(entity=self.entity, interest=self.cooking, score=3.0)
        EntityInterestAffinity.objects.create(entity=other_entity, interest=self.hiking, score=99.0)

        request = self.factory.get("/api/interests/mine/top/")
        force_authenticate(request, user=self.account)
        request.entity = self.entity
        response = MyTopInterestsView.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["interest"]["name"], "Hiking")
        self.assertEqual(data[1]["interest"]["name"], "Cooking")

    def test_trending_interests_is_unscoped_and_ranked(self):
        InterestTrendingScore.objects.create(interest=self.hiking, score=50.0)
        InterestTrendingScore.objects.create(interest=self.cooking, score=75.0)

        request = self.factory.get("/api/interests/trending/")
        force_authenticate(request, user=self.account)
        request.entity = self.entity
        response = TrendingInterestsView.as_view()(request)
        response.render()

        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["interest"]["name"], "Cooking")
        self.assertEqual(data[1]["interest"]["name"], "Hiking")
