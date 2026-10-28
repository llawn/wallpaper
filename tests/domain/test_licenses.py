import unittest

from wallman.domain.licenses import LABELS, LICENSES, SHAREABLE, is_shareable


class LicensesTest(unittest.TestCase):
    def test_shareable_classes(self):
        for lic in ("public-domain", "CC0-1.0", "CC-BY-SA-4.0", "GPL-3.0-only", "LGPL-2.1-only", "MIT", "BSD-3-Clause", "Apache-2.0"):
            self.assertTrue(is_shareable(lic))
        for lic in ("all-rights-reserved", "ghibli-free-use", "official-wallpaper", "unsplash-license", "personal"):
            self.assertFalse(is_shareable(lic))

    def test_every_license_has_a_label_and_shareable_is_subset(self):
        self.assertEqual(set(LABELS), set(LICENSES))
        self.assertLessEqual(SHAREABLE, LICENSES)
