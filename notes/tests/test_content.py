import unittest


class ContentTests(unittest.TestCase):
    def test_empty_or_markup_only_is_rejected(self):
        from notes.services.content import normalize_note_content
        for body in ("", "<p><br></p>", "<p>&nbsp;\u200b</p>", "<script>alert(1)</script>"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                normalize_note_content(body)

    def test_diary_html_formatting_is_preserved(self):
        from notes.services.content import normalize_note_content
        body = '<h2 class="ql-align-center">Plan</h2><p><strong>Bold</strong> <em>italic</em></p><ul><li>One</li></ul>'
        html, plain = normalize_note_content(body)
        self.assertIn('<h2 class="ql-align-center">Plan</h2>', html)
        self.assertIn("<strong>Bold</strong>", html)
        self.assertEqual(plain, "Plan Bold italic One")

    def test_unsafe_html_and_urls_removed(self):
        from notes.services.content import normalize_note_content
        html, text = normalize_note_content('<p onclick="x()">Hello<script>evil()</script><a href="javascript:alert(1)">link</a><iframe src="https://evil"></iframe></p>')
        self.assertNotIn("onclick", html)
        self.assertNotIn("javascript", html)
        self.assertNotIn("evil", html)
        self.assertEqual(text, "Hello link")

    def test_unicode_and_color_style_survive(self):
        from notes.services.content import normalize_note_content
        html, plain = normalize_note_content('<p><span style="color: red; background-image:url(https://evil)">你好 café</span></p>')
        self.assertIn("color: red", html)
        self.assertNotIn("background-image", html)
        self.assertEqual(plain, "你好 café")
