"""
The database-free rules of a realm invite (community/invite_rules.py).

Plain unittest on purpose - no Django settings, no test database:
    python -m unittest community.tests.test_invite_rules
(manage.py test would also connect the Cassandra alias in DATABASES, which is
the live cluster.)
"""

import unittest

from community.invite_rules import (
    InviteError,
    invite_notification_ux,
    invite_route,
    invite_sentence,
    parse_target,
    resolve_purpose,
)


class ResolvePurposeTests(unittest.TestCase):
    def test_a_one_purpose_realm_takes_it_by_default(self):
        for realm_type in ("group", "server", "conference"):
            self.assertEqual(resolve_purpose(realm_type), ("join", None))

    def test_a_page_must_say_which(self):
        with self.assertRaises(InviteError):
            resolve_purpose("page")
        self.assertEqual(resolve_purpose("page", "follow"), ("follow", None))

    def test_a_page_team_invite_needs_a_real_role(self):
        self.assertEqual(
            resolve_purpose("page", "manage", "Admin"), ("manage", "admin")
        )
        self.assertEqual(
            resolve_purpose("page", "manage", "moderator"), ("manage", "moderator")
        )
        for bad in (None, "", "owner", "member"):
            with self.assertRaises(InviteError):
                resolve_purpose("page", "manage", bad)

    def test_a_role_is_dropped_where_it_means_nothing(self):
        self.assertEqual(resolve_purpose("group", "join", "admin"), ("join", None))
        self.assertEqual(resolve_purpose("page", "follow", "admin"), ("follow", None))

    def test_a_purpose_the_realm_cannot_have_is_refused(self):
        with self.assertRaises(InviteError):
            resolve_purpose("group", "follow")
        with self.assertRaises(InviteError):
            resolve_purpose("server", "manage", "admin")

    def test_realms_that_are_not_invited_into(self):
        for realm_type in ("channel", "voice", None, "nonsense"):
            with self.assertRaises(InviteError):
                resolve_purpose(realm_type)


class ParseTargetTests(unittest.TestCase):
    def test_an_email(self):
        self.assertEqual(parse_target(" Ana@Example.COM "), ("email", "ana@example.com"))

    def test_a_username_with_or_without_the_at(self):
        self.assertEqual(parse_target("@maya"), ("username", "maya"))
        self.assertEqual(parse_target("maya"), ("username", "maya"))

    def test_nonsense_is_refused(self):
        for bad in ("", "   ", None, "@", "ana@", "ana@nodot", "two words", "@two words"):
            with self.assertRaises(InviteError, msg=repr(bad)):
                parse_target(bad)


class SentenceTests(unittest.TestCase):
    def test_each_purpose_reads_right(self):
        self.assertEqual(
            invite_sentence("Maya", "Climbers", "group", "join"),
            "Maya invited you to join the group Climbers.",
        )
        self.assertEqual(
            invite_sentence("Maya", "Acme", "page", "manage", "admin"),
            "Maya invited you to help run Acme as an admin.",
        )
        self.assertEqual(
            invite_sentence("Maya", "Acme", "page", "manage", "moderator"),
            "Maya invited you to help run Acme as a moderator.",
        )
        self.assertEqual(
            invite_sentence("Maya", "Acme", "page", "follow"),
            "Maya invited you to follow Acme.",
        )


class NotificationUxTests(unittest.TestCase):
    def test_every_platform_gets_the_invite_page_and_both_answers(self):
        redirects, actions = invite_notification_ux("tok123")
        self.assertEqual(
            sorted(r["platform"] for r in redirects), ["android", "ios", "web"]
        )
        self.assertTrue(all(r["route"] == invite_route("tok123") for r in redirects))

        for platform in ("web", "android", "ios"):
            mine = [a for a in actions if a["platform"] == platform]
            self.assertEqual([a["id"] for a in mine], ["accept", "decline"])
            for action in mine:
                # A PATH on our own user service - the clients refuse anything
                # else for an authenticated api-request.
                self.assertEqual(action["type"], "api-request")
                self.assertEqual(action["service"], "user")
                self.assertTrue(action["url"].startswith("/"))
                self.assertFalse(action["url"].startswith("//"))
                self.assertEqual(action["method"], "PATCH")
                self.assertEqual(action["payload"]["invite_token"], "tok123")
            self.assertEqual(
                [a["payload"]["status"] for a in mine], ["accepted", "declined"]
            )

    def test_two_invites_never_share_buttons(self):
        # Each pair answers its own invite.
        self.assertNotEqual(
            invite_notification_ux("a")[1], invite_notification_ux("b")[1]
        )

    def test_a_conference_opens_its_lobby_on_the_web_only(self):
        # The lobby reads ?invite_token= and lets a guest in; the apps have no
        # conference screens, so they keep the invite page.
        redirects, _ = invite_notification_ux("tok9", "conference", "team-sync")
        routes = {r["platform"]: r["route"] for r in redirects}
        self.assertEqual(routes["web"], "/conference/team-sync?invite_token=tok9")
        self.assertEqual(routes["android"], "/invite/tok9")
        self.assertEqual(routes["ios"], "/invite/tok9")

    def test_other_realms_and_slugless_conferences_open_the_invite_page(self):
        self.assertEqual(invite_route("t", "group", "g-slug"), "/invite/t")
        self.assertEqual(invite_route("t", "page", "acme"), "/invite/t")
        self.assertEqual(invite_route("t", "conference", None), "/invite/t")


if __name__ == "__main__":
    unittest.main()
