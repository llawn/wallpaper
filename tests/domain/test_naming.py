import unittest

from wallman.domain.naming import NAME, file_name, slug, unique_name


class NamingTest(unittest.TestCase):
    def test_file_name_joins_kebab_parts(self):
        self.assertEqual(file_name("Claude Monet", "The Magpie", 1869, ".JPG"), "claude-monet_the-magpie_1869.jpg")

    def test_file_name_refuses_empty_name(self):
        for artist, title in ((None, "!!!"), ("", "???"), ("¿?", "—")):
            with self.subTest(artist=artist, title=title), self.assertRaises(ValueError):
                file_name(artist, title, 1900, ".jpg")
        self.assertEqual(file_name("Monet", "!!!", None, ".jpg"), "monet.jpg")

    def test_file_name_normalises_unicode_and_spaces(self):
        self.assertEqual(file_name("Joaquín Sorolla", "  Chicos en la playa ", None, ".jpeg"), "joaquin-sorolla_chicos-en-la-playa.jpg")

    def test_file_name_without_artist(self):
        self.assertEqual(file_name(None, "My New Photo", None, ".png"), "my-new-photo.png")

    def test_unique_name_appends_counter(self):
        self.assertEqual(unique_name("a_b.jpg", {"a_b.jpg", "a_b-2.jpg"}), "a_b-3.jpg")

    def test_unique_name_unchanged_when_free(self):
        self.assertEqual(unique_name("a_b.jpg", {"x.jpg"}), "a_b.jpg")

    def test_slug_cuts_on_a_dash(self):
        self.assertEqual(slug("aaa bbb ccc", 9), "aaa-bbb")
        self.assertEqual(slug("aaa bbb ccc", 7), "aaa-bbb")

    def test_generated_names_match_pattern(self):
        self.assertTrue(NAME.match(file_name("Joaquín Sorolla", "Chicos!", 1910, ".png")))
        self.assertFalse(NAME.match("Fantasy-Autumn.png"))


class TagTest(unittest.TestCase):
    def test_tags_normalised_and_sorted(self):
        from wallman.domain.naming import normalise_tags, slug_tag
        self.assertEqual(slug_tag("  Café Society! "), "cafe-society")
        self.assertEqual(slug_tag("!!!"), "")
        self.assertEqual(normalise_tags(["Zebra", "alpha", "ALPHA", "!!", "Art Nouveau"]), ("alpha", "art-nouveau", "zebra"))
        self.assertEqual(normalise_tags([]), ())
