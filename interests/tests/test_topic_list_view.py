"""
The topic directory - /api/interests/topics/ with and without ?q=.

Two things are worth pinning here and nothing else is: the RANKING (relevance
beats trending when a query is present, trending is the whole order when it is
not) and the rule that a topic with nothing the viewer may read never appears -
that one is a privacy boundary, not a preference.
"""

import uuid

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from entity.models import Entity
from interests.models import Interest, InterestTrendingScore, PostInterestLink
from interests.views import TopicListView
from newsfeed.models import Post
from user.models import Account


def _entity():
    return Entity.objects.create(type="user")


def _account(entity):
    return Account.objects.create(
        entity=entity,
        first_name="Test",
        last_name="User",
        email=f"{uuid.uuid4()}@example.com",
        is_active=True,
        is_verified=True,
    )


class TopicListViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.entity = _entity()
        self.account = _account(self.entity)

    def _post(self, interest, entity=None, privacy_status="public"):
        """A visible post filed under `interest`, so the topic has a row."""
        post = Post.objects.create(
            entity=entity or self.entity,
            file_type="image",
            content_type="post",
            on_feed="true",
            privacy_status=privacy_status,
        )
        PostInterestLink.objects.create(post=post, interest=interest)
        return post

    def _get(self, query=None):
        url = "/api/interests/topics/"
        request = self.factory.get(url, {"q": query} if query else {})
        force_authenticate(request, user=self.account)
        request.entity = self.entity
        response = TopicListView.as_view()(request)
        response.render()
        return response

    def _tagged(self, name, score=0.0, **post_kwargs):
        interest = Interest.objects.create(name=name)
        if score:
            InterestTrendingScore.objects.create(interest=interest, score=score)
        self._post(interest, **post_kwargs)
        return interest

    def test_no_query_is_the_trending_order(self):
        self._tagged("Cooking", score=10.0)
        self._tagged("Hiking", score=90.0)

        response = self._get()

        self.assertEqual(response.status_code, 200)
        slugs = [row["slug"] for row in response.data["results"]]
        self.assertEqual(slugs, ["hiking", "cooking"])

    def test_a_query_ranks_relevance_over_trending(self):
        # The busiest topic of the three, and the least exact match.
        self._tagged("Golden Sunset", score=99.0)
        self._tagged("Sunset Series", score=50.0)
        self._tagged("Sunset", score=1.0)

        response = self._get("sunset")

        slugs = [row["slug"] for row in response.data["results"]]
        # Exact key, then prefix, then substring - trending only breaks ties
        # inside a tier, which is why the 99-scored topic is last.
        self.assertEqual(slugs, ["sunset", "sunsetseries", "goldensunset"])

    def test_a_query_matches_the_readable_name_too(self):
        # "north edsa" normalises to "northedsa", so somebody typing the words
        # and somebody typing the hashtag must land on the same row.
        self._tagged("North Edsa", score=5.0)

        self.assertEqual(
            [row["slug"] for row in self._get("north edsa").data["results"]],
            ["northedsa"],
        )
        self.assertEqual(
            [row["slug"] for row in self._get("#northedsa").data["results"]],
            ["northedsa"],
        )

    def test_a_topic_with_no_visible_post_is_not_listed(self):
        # Somebody else's private post is the only thing under this interest.
        stranger = _entity()
        _account(stranger)
        self._tagged("Secret", score=99.0, entity=stranger, privacy_status="private")
        self._tagged("Public Thing", score=1.0)

        response = self._get()

        slugs = [row["slug"] for row in response.data["results"]]
        self.assertEqual(slugs, ["publicthing"])
        # And the count agrees with the page - the exclusion happens in SQL, so
        # an infinite scroll paging this is not reading a total that includes
        # rows it will never be shown.
        self.assertEqual(response.data["count"], 1)

    def test_an_interest_with_no_posts_at_all_is_not_listed(self):
        Interest.objects.create(name="Unused")
        self._tagged("Used", score=1.0)

        self.assertEqual(
            [row["slug"] for row in self._get().data["results"]], ["used"]
        )

    def test_a_row_carries_the_shape_the_clients_render(self):
        self._tagged("Hiking", score=7.0)

        row = self._get().data["results"][0]

        self.assertEqual(row["name"], "Hiking")
        self.assertEqual(row["slug"], "hiking")
        # No taxonomy parent yet - discovery creates orphans, and they are
        # still listable.
        self.assertEqual(row["category"], "General")
        self.assertEqual(row["posts"], 1)
        self.assertEqual(row["score"], 7.0)
        self.assertEqual(len(row["faces"]), 1)
        self.assertEqual(row["faces"][0]["entity_id"], str(self.entity.id))
