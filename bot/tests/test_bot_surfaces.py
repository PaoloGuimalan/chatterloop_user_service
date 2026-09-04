"""
The surfaces a bot has to appear on to be a participant rather than a ghost.

test_bot_as_entity.py covers identity - that a bot resolves to a name wherever
an entity is named. This file covers the places a bot has to be REACHABLE: a
profile to open, a following list to appear in, a realm to join, and the one
relationship it must be refused.

Each test names the failure it prevents, because every one of these failed
SILENTLY rather than loudly. A bot dropped from a list, a profile that 404s, a
contact request that parks forever - none of them raise, and none of them show
up in a log.
"""

import uuid

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from bot.models import Bot
from user.views import UserAuthentication
from community.models import Realm
from entity.models import Entity, Follow
from entity.network_views import normalize_network_entity
from user.models import Account


def _account(username=None):
    entity = Entity.objects.create(type="user")
    return Account.objects.create(
        entity=entity,
        username=username or f"user{uuid.uuid4().hex[:8]}",
        first_name="Test",
        last_name="User",
        email=f"{uuid.uuid4()}@example.com",
        is_active=True,
        is_verified=True,
    )


def _bot(handle=None, **kwargs):
    entity = Entity.objects.create(type="bot")
    return Bot.objects.create(
        entity=entity,
        name=kwargs.pop("name", "Neon"),
        handle=handle or f"bot{uuid.uuid4().hex[:8]}",
        description=kwargs.pop("description", "Answers questions about the chat."),
        **kwargs,
    )


class NetworkEntityNormalizationTests(TestCase):
    """
    normalize_network_entity used to branch on `users` then `realms` and return
    None for anything else - so a followed bot was dropped from the following
    list with no error. A follow of a bot was always an ordinary Follow row; it
    was only the rendering that could not name one.
    """

    def test_a_bot_normalizes_instead_of_vanishing(self):
        bot = _bot(handle="neon", name="Neon")

        row = normalize_network_entity(bot.entity)

        self.assertIsNotNone(row, "a bot must not normalize to None")
        self.assertEqual(row["type"], "bot")
        self.assertEqual(row["handle"], "neon")
        self.assertEqual(row["display_name"], "Neon")
        self.assertEqual(row["entity_id"], str(bot.entity_id))

    def test_a_nameless_bot_falls_back_to_its_handle(self):
        bot = _bot(handle="neon", name="")

        self.assertEqual(normalize_network_entity(bot.entity)["display_name"], "neon")

    def test_an_unverified_bot_shows_no_badge_by_default(self):
        bot = _bot(handle="neon")

        self.assertFalse(normalize_network_entity(bot.entity)["is_verified"])

    def test_a_verified_bot_shows_the_badge(self):
        bot = _bot(handle="neon", is_verified=True)

        self.assertTrue(normalize_network_entity(bot.entity)["is_verified"])

    def test_the_row_says_a_bot_cannot_be_connected_to(self):
        bot = _bot(handle="neon")

        self.assertFalse(normalize_network_entity(bot.entity)["can_connect"])

    def test_users_and_realms_still_normalize_as_before(self):
        # The bot branch is additive; it must not shadow the two that worked.
        account = _account(username="paulo")
        self.assertEqual(normalize_network_entity(account.entity)["type"], "user")

        realm_entity = Entity.objects.create(type="realm")
        Realm.objects.create(
            entity=realm_entity, name="Design", slug="design", type="group",
            created_by=account.entity,
        )
        self.assertEqual(normalize_network_entity(realm_entity)["type"], "realm")

    def test_a_bare_entity_still_normalizes_to_none(self):
        # An entity backing none of the three is genuinely unrenderable, and
        # the caller drops it. That behaviour is intact.
        self.assertIsNone(normalize_network_entity(Entity.objects.create(type="user")))


class ProfileShellResolvesBotsTests(TestCase):
    """
    A bot lands on the SAME profile shell as a person or a page.

    The shell branches on `type` - "user" gets the person layout, anything else
    gets the realm one - so a bot only has to arrive shaped like the realm
    payload. That is cheaper than a second screen and leaves exactly one way
    for a client to reach a profile.
    """

    def setUp(self):
        self.factory = APIRequestFactory()
        self.account = _account()

    def _get(self, handle, account=None):
        account = account or self.account
        request = self.factory.get(f"/api/user/auth/{handle}/")
        force_authenticate(request, user=account)
        request.entity = account.entity
        return UserAuthentication.as_view()(request, username=handle)

    def test_a_bot_handle_resolves_instead_of_404ing(self):
        # The realm branch is a get_object_or_404, so before this a bot handle
        # reached it and 404'd rather than falling through.
        _bot(handle="neon")

        response = self._get("neon")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["type"], "bot")

    def test_it_carries_every_key_the_realm_layout_reads(self):
        # The whole point of mapping onto this shape: the existing layout
        # renders it with no client change. A missing key is a blank section.
        _bot(handle="neon")

        data = self._get("neon").data["data"]

        for key in (
            "id", "entity", "realm_id", "slug", "name", "description",
            "profile", "cover_photo", "email", "created_by", "parent",
            "is_active", "is_private", "is_verified", "followers_count",
            "is_follower", "members", "is_member", "is_admin", "my_role",
            "type", "connection",
        ):
            self.assertIn(key, data, f"profile payload is missing {key!r}")

    def test_the_handle_lookup_is_case_insensitive(self):
        _bot(handle="neon")

        self.assertEqual(self._get("NEON").status_code, 200)

    def test_follower_state_is_answered_for_the_viewer(self):
        bot = _bot(handle="neon")
        follower = _account()
        Follow.objects.create(follower=follower.entity, followee=bot.entity, status=True)

        data = self._get("neon", account=follower).data["data"]

        self.assertEqual(data["followers_count"], 1)
        self.assertTrue(data["is_follower"])

    def test_a_bot_is_unbadged_by_default_and_never_private(self):
        _bot(handle="neon")

        data = self._get("neon").data["data"]

        self.assertFalse(data["is_verified"])
        self.assertFalse(data["is_private"])

    def test_a_verified_bot_carries_the_badge_on_its_profile(self):
        _bot(handle="neon", is_verified=True)

        data = self._get("neon").data["data"]

        self.assertTrue(data["is_verified"])

    def test_a_bot_offers_no_connection(self):
        # A bot cannot accept a contact request, so the layout must not render
        # one as available.
        _bot(handle="neon")

        connection = self._get("neon").data["data"]["connection"]

        self.assertFalse(connection["is_connection_present"])
        self.assertIsNone(connection["connection_id"])

    def test_an_account_still_wins_over_a_bot_on_the_same_handle(self):
        # Handles are unique per table but NOT across them, so branch ORDER is
        # the precedence rule. Accounts resolve first, matching how the rest of
        # the platform resolves an entity's identity.
        _account(username="twin")
        _bot(handle="twin")

        self.assertEqual(self._get("twin").data["data"]["type"], "user")

    def test_every_no_photo_sentinel_becomes_null(self):
        # A bot stores "none" (the account sentinel) while the realm layout
        # reading this payload checks for "N/A". Passing either through raw
        # gets it treated as a photo URL.
        for sentinel in ("none", "N/A", ""):
            bot = _bot()
            bot.profile = sentinel
            bot.save()
            self.assertIsNone(
                self._get(bot.handle).data["data"]["profile"],
                f"{sentinel!r} was not normalised",
            )

    def test_a_real_photo_url_survives(self):
        bot = _bot(handle="neon")
        bot.profile = "https://cdn.example/neon.png"
        bot.save()

        self.assertEqual(
            self._get("neon").data["data"]["profile"],
            "https://cdn.example/neon.png",
        )

    def test_a_bot_reports_that_it_cannot_be_connected_to(self):
        # The realm layout renders "Add Contact" whenever no connection exists,
        # which for a bot is always - so without this flag the button would show
        # on every bot profile and could only ever be refused.
        _bot(handle="neon")

        data = self._get("neon").data["data"]

        self.assertFalse(data["can_connect"])
        self.assertTrue(data["can_follow"])

    def test_a_deactivated_bot_does_not_resolve(self):
        _bot(handle="retired", is_active=False)

        # Falls through to the realm branch, which 404s on an unknown slug.
        self.assertEqual(self._get("retired").status_code, 404)
