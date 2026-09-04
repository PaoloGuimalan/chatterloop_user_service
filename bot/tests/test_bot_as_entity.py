"""
A bot is an entity like any other.

Every one of these covers a place that resolved an entity's identity by trying
`users` then `realms` and stopping - which meant a bot was silently absent from
a list it belonged in, or rendered nameless in one it appeared in. They are
grouped by the surface they protect rather than by module, because the bug is
the same bug each time and the point is that it stays fixed everywhere.
"""

import uuid

from django.test import TestCase

from bot.models import Bot
from community.models import Realm
from entity.models import Entity, Follow
from entity.serializers import EntitySerializer
from entity.utils import (
    entity_side_is_visible,
    get_entity_display_username,
    get_entity_name,
)
from newsfeed.services.comment_mentions import resolve_mentioned_entities
from user.models import Account


def _entity(kind):
    return Entity.objects.create(type=kind)


def ids(values):
    """
    Entity.id is a CharField defaulting to uuid.uuid4, so a freshly created
    instance holds a UUID object while the same row read back holds a string.
    Every comparison here normalises, otherwise the assertion fails on the type
    rather than on the value.
    """
    return {str(v) for v in values}


def _account(username=None):
    entity = _entity("user")
    return Account.objects.create(
        entity=entity,
        username=username or f"user{uuid.uuid4().hex[:8]}",
        first_name="Test",
        last_name="User",
        email=f"{uuid.uuid4()}@example.com",
        is_active=True,
        is_verified=True,
    )


def _bot(handle="helper", name="Helper Bot", **kwargs):
    return Bot.objects.create(entity=_entity("bot"), handle=handle, name=name, **kwargs)


class BotsAreMentionableTests(TestCase):
    """
    The comment-mention path is how a bot is addressed in a post at all: it
    parses handles on write purely to send a notification, so an entity that
    does not resolve here is never notified no matter what the text says.
    """

    def setUp(self):
        self.author = _account()

    def test_a_bot_is_resolved_by_its_handle(self):
        bot = _bot(handle="helper")
        resolved = resolve_mentioned_entities("hey @helper look at this", self.author.entity)
        self.assertEqual(ids(e.id for e in resolved), ids([bot.entity_id]))

    def test_a_bot_resolves_alongside_a_user_and_a_realm(self):
        bot = _bot(handle="helper")
        other = _account(username="ana")
        realm = Realm.objects.create(
            entity=_entity("realm"), name="Neon", slug="neon",
            created_by=self.author.entity, type="page",
        )
        resolved = resolve_mentioned_entities(
            "@helper @ana @neon all of you", self.author.entity
        )
        self.assertEqual(
            ids(e.id for e in resolved),
            ids([bot.entity_id, other.entity_id, realm.entity_id]),
        )

    def test_handle_match_is_case_insensitive(self):
        bot = _bot(handle="helper")
        resolved = resolve_mentioned_entities("@Helper", self.author.entity)
        self.assertEqual(ids(e.id for e in resolved), ids([bot.entity_id]))

    def test_a_deactivated_bot_is_not_mentionable(self):
        _bot(handle="retired", is_active=False)
        self.assertEqual(resolve_mentioned_entities("@retired", self.author.entity), [])

    def test_an_unknown_handle_is_just_text(self):
        _bot(handle="helper")
        self.assertEqual(resolve_mentioned_entities("@nobody", self.author.entity), [])


class BotIdentityRendersTests(TestCase):
    """
    EntitySerializer returned `details: null` for a bot, so a bot member or
    contact rendered as a nameless, avatarless row wherever an entity is
    embedded.
    """

    def test_entity_serializer_emits_bot_details(self):
        bot = _bot(handle="helper", name="Helper Bot")
        data = EntitySerializer(bot.entity).data
        self.assertEqual(data["type"], "bot")
        self.assertIsNotNone(data["details"])
        self.assertEqual(data["details"]["handle"], "helper")
        self.assertEqual(data["details"]["name"], "Helper Bot")

    def test_details_carry_the_display_aliases_clients_already_read(self):
        # The whole point of the aliases: a bot appears in contacts, members
        # and followers with NO client change.
        bot = _bot(handle="helper", name="Helper Bot")
        details = EntitySerializer(bot.entity).data["details"]
        self.assertEqual(details["username"], "helper")
        self.assertEqual(details["first_name"], "Helper Bot")
        self.assertEqual(details["middle_name"], "N/A")
        self.assertEqual(details["last_name"], "")

    def test_a_bot_is_unbadged_by_default(self):
        self.assertFalse(EntitySerializer(_bot().entity).data["details"]["is_badged"])

    def test_a_verified_bot_carries_the_badge(self):
        bot = _bot(is_verified=True)
        self.assertTrue(EntitySerializer(bot.entity).data["details"]["is_badged"])

    def test_no_picture_normalises_to_the_sentinel_clients_test_for(self):
        bot = _bot(profile="")
        self.assertEqual(EntitySerializer(bot.entity).data["details"]["profile"], "none")

    def test_entity_utils_already_name_a_bot(self):
        # These had a bot branch before this work; asserted here so the three
        # identity paths are covered in one place.
        bot = _bot(handle="helper", name="Helper Bot")
        self.assertEqual(get_entity_display_username(bot.entity), "@helper")
        self.assertEqual(get_entity_name(bot.entity), "Helper Bot")


class BotIsAUsableCounterpartTests(TestCase):
    """
    entity_side_is_visible gates connection and follow queries. A bot filtered
    out here is a bot whose contact row exists but never appears in any list -
    which is how a direct conversation with one would go missing.
    """

    def test_a_follow_of_a_bot_survives_the_visibility_filter(self):
        follower = _account()
        bot = _bot(handle="helper")
        Follow.objects.create(
            follower=follower.entity, followee=bot.entity, status=True
        )
        visible = Follow.objects.filter(entity_side_is_visible("followee"))
        self.assertEqual(ids(f.followee_id for f in visible), ids([bot.entity_id]))

    def test_a_follow_of_a_deactivated_bot_is_filtered_out(self):
        follower = _account()
        bot = _bot(handle="retired", is_active=False)
        Follow.objects.create(
            follower=follower.entity, followee=bot.entity, status=True
        )
        self.assertFalse(
            Follow.objects.filter(entity_side_is_visible("followee")).exists()
        )


class BotsAreDiscoverableTests(TestCase):
    """
    Search v2's Bots section. A bot nobody can find is a bot nobody can add to
    a group or start a conversation with, so discovery is the first half of
    "acts like any other entity".
    """

    def setUp(self):
        from user.utils.blocking import get_blocked_account_ids

        self.viewer = _account()
        self.blocked = get_blocked_account_ids(self.viewer.entity)

    def _search(self, query):
        from entity.search_views import build_bots_queryset, normalize_bot

        rows = build_bots_queryset(self.viewer.entity, query, self.blocked)
        return [normalize_bot(row) for row in rows]

    def test_found_by_handle(self):
        _bot(handle="helper", name="Helper Bot")
        self.assertEqual([r["handle"] for r in self._search("helper")], ["helper"])

    def test_found_by_name(self):
        _bot(handle="hb", name="Helper Bot")
        self.assertEqual([r["handle"] for r in self._search("Helper")], ["hb"])

    def test_at_prefix_searches_handles_only(self):
        _bot(handle="hb", name="Helper Bot")
        # "@Helper" is a handle query, and this bot's handle is "hb" - so the
        # name must NOT match it.
        self.assertEqual(self._search("@Helper"), [])
        self.assertEqual([r["handle"] for r in self._search("@hb")], ["hb"])

    def test_system_bots_are_excluded(self):
        # The moderation bot speaks as the platform; offering it as something
        # to tag or add invites confusion about who is talking.
        _bot(handle="moderator", name="Moderation", is_system=True)
        self.assertEqual(self._search("moderator"), [])

    def test_deactivated_bots_are_excluded(self):
        _bot(handle="retired", is_active=False)
        self.assertEqual(self._search("retired"), [])

    def test_the_normalized_row_carries_what_a_card_needs(self):
        _bot(handle="helper", name="Helper Bot", description="Answers questions.")
        [row] = self._search("helper")
        self.assertEqual(row["type"], "bot")
        self.assertEqual(row["display_name"], "Helper Bot")
        self.assertEqual(row["handle"], "helper")
        self.assertEqual(row["description"], "Answers questions.")
        # Unverified by default.
        self.assertFalse(row["is_verified"])
        # Never pending - a bot has no privacy gate.
        self.assertFalse(row["is_follow_pending"])

    def test_a_verified_bot_carries_the_badge_in_search(self):
        _bot(handle="helper", name="Helper Bot", is_verified=True)
        [row] = self._search("helper")
        self.assertTrue(row["is_verified"])

    def test_follower_reach_is_counted_through_the_entity(self):
        # The annotation walks Bot -> entity -> followers. A wrong join path
        # here returns a silently wrong number rather than an error, which is
        # exactly the kind of bug that survives a code review.
        bot = _bot(handle="helper")
        for _ in range(3):
            Follow.objects.create(
                follower=_account().entity, followee=bot.entity, status=True
            )
        # A pending follow is not reach.
        Follow.objects.create(
            follower=_account().entity, followee=bot.entity, status=False
        )
        [row] = self._search("helper")
        self.assertEqual(row["followers_count"], 3)

    def test_is_followed_reflects_the_viewer(self):
        bot = _bot(handle="helper")
        self.assertFalse(self._search("helper")[0]["is_followed"])

        Follow.objects.create(
            follower=self.viewer.entity, followee=bot.entity, status=True
        )
        self.assertTrue(self._search("helper")[0]["is_followed"])

    def test_prefix_matches_rank_above_substring_matches(self):
        _bot(handle="zzz", name="Contains helper inside")
        _bot(handle="helper", name="Helper Bot")
        self.assertEqual(
            [r["handle"] for r in self._search("helper")], ["helper", "zzz"]
        )
