import unittest


class ProfilePathPolicyTests(unittest.TestCase):
    def test_reserved_names_are_rejected_in_all_canonical_spellings(self):
        from core.profile_paths import ProfilePathError, validate_profile_path

        for value in ("studio", "STUDIO", " studios ", "ｓｔｕｄｉｏ", "scenes", "login", "moments", "api"):
            with self.subTest(value=value), self.assertRaises(ProfilePathError) as error:
                validate_profile_path(value, "username")
            self.assertEqual(error.exception.payload["code"], "RESERVED_PATH")
            self.assertEqual(error.exception.payload["field"], "username")

    def test_similar_names_are_allowed_but_path_syntax_is_not(self):
        from core.profile_paths import ProfilePathError, validate_profile_path

        for value in ("mystudio", "studio-team", "scenes_123", "moderator", "system", "Maya"):
            validate_profile_path(value, "slug")
        for value in ("/studio", "studio/anything", "studio\\x", "%73tudio", "name?x", "name#x", "studi\to", "studi\no", "studi\ro", ".", "..", 42):
            with self.subTest(value=value), self.assertRaises(ProfilePathError):
                validate_profile_path(value, "slug")

    def test_manifest_reserves_roots_not_nested_editor_names(self):
        from core.profile_paths import RESERVED_PROFILE_PATHS

        self.assertTrue({"studio", "studios", "scenes", "edits", "design", "delete-account"} <= RESERVED_PROFILE_PATHS)
        self.assertFalse({"archive", "create", "drafts"} & RESERVED_PROFILE_PATHS)


if __name__ == "__main__":
    unittest.main()
