"""Community policy tests run with plain unittest; no databases or services."""

import unittest

from community import post_policy as policy


class CommunityPolicyTests(unittest.TestCase):
    def test_private_audience_requires_current_membership_even_for_author(self):
        self.assertTrue(callable(getattr(policy, "can_read", None)))
        self.assertFalse(policy.can_read(is_private=True, is_member=False))
        self.assertTrue(policy.can_read(is_private=True, is_member=True))

    def test_public_community_does_not_require_client_capability(self):
        self.assertTrue(callable(getattr(policy, "can_read", None)))
        self.assertTrue(policy.can_read(is_private=False, is_member=False))

    def test_public_audience_allows_aware_nonmembers_but_inactive_realms_are_hidden(
        self,
    ):
        self.assertTrue(callable(getattr(policy, "can_read", None)))
        self.assertTrue(policy.can_read(is_private=False, is_member=False))
        self.assertFalse(
            policy.can_read(is_private=False, is_member=True, is_active=False)
        )

    def test_contributors_require_membership_even_in_public_community(self):
        self.assertTrue(callable(getattr(policy, "can_contribute", None)))
        self.assertFalse(policy.can_contribute(is_member=False))
        self.assertTrue(policy.can_contribute(is_member=True))
        self.assertFalse(policy.can_contribute(is_member=True, is_active=False))

    def test_public_nonmembers_can_interact_but_not_publish(self):
        self.assertTrue(policy.can_interact(is_private=False, is_member=False))
        self.assertFalse(policy.can_contribute(is_member=False))
        self.assertFalse(policy.can_interact(is_private=True, is_member=False))
        self.assertTrue(policy.can_interact(is_private=True, is_member=True))
        self.assertFalse(
            policy.can_interact(is_private=False, is_member=True, is_active=False)
        )

    def test_anonymity_rejects_strings_numbers_and_null(self):
        self.assertTrue(callable(getattr(policy, "validate_anonymity", None)))
        for value in ("true", "false", 0, 1, None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.validate_anonymity(value, is_community=True)

    def test_anonymity_only_allowed_in_community(self):
        self.assertTrue(callable(getattr(policy, "validate_anonymity", None)))
        self.assertFalse(policy.validate_anonymity(False, is_community=False))
        self.assertTrue(policy.validate_anonymity(True, is_community=True))
        with self.assertRaises(ValueError):
            policy.validate_anonymity(True, is_community=False)

    def test_anonymous_projection_removes_identity_and_keeps_owner_capability(self):
        self.assertTrue(callable(getattr(policy, "project_contribution", None)))
        original = {
            "entity": {"id": "real"},
            "deleted_by": "real",
            "feed_reason": {"entity": "real"},
            "text": "hello",
        }
        result = policy.project_contribution(
            original, is_anonymous=True, owner_id="real", viewer_id="real"
        )
        self.assertIsNone(result["entity"])
        self.assertIsNone(result["deleted_by"])
        self.assertIsNone(result["feed_reason"])
        self.assertTrue(result["is_owner"])
        self.assertTrue(result["is_anonymous"])
        self.assertEqual(result["text"], "hello")
        self.assertEqual(original["entity"], {"id": "real"})

    def test_other_viewers_receive_no_owner_capability(self):
        self.assertTrue(callable(getattr(policy, "project_contribution", None)))
        result = policy.project_contribution(
            {"entity": "real"}, is_anonymous=True, owner_id="real", viewer_id="other"
        )
        self.assertFalse(result["is_owner"])
        self.assertIsNone(result["entity"])


if __name__ == "__main__":
    unittest.main()
