import unittest

from wallman.domain.models import Collection, CropBox, Resolution, Wallpaper
from wallman.domain.rules import centered_crop, check_collection, needs_crop

SHA = "a" * 64


def collection_with(*ws, medium="painting", external=False):
    return Collection("painting/x", medium, "impressionism", list(ws), external=external)


def codes(issues):
    return {i.code for i in issues}


def check(c, *names_sizes):
    return check_collection(c, {n for n, _ in names_sizes}, dict(names_sizes))


class RulesTest(unittest.TestCase):
    def test_centered_crop_of_square(self):
        self.assertEqual(centered_crop(Resolution(4096, 4096)), CropBox(0, 896, 4096, 2304))

    def test_centered_crop_of_ultrawide(self):
        self.assertEqual(centered_crop(Resolution(3440, 1080)), CropBox(760, 0, 1920, 1080))

    def test_needs_crop_bounds(self):
        self.assertFalse(needs_crop(Resolution(2560, 1600)))  # 1.6
        self.assertTrue(needs_crop(Resolution(3000, 2000)))  # 1.5
        self.assertFalse(needs_crop(Resolution(1950, 1000)))  # 1.95
        self.assertTrue(needs_crop(Resolution(1951, 1000)))

    def test_clean_collection_has_no_issues(self):
        r = Resolution(1920, 1080)
        c = collection_with(Wallpaper("a_b.jpg", "B", "A", "CC0-1.0", r, year=1900, source="https://x.org"))
        self.assertEqual(check(c, ("a_b.jpg", r)), [])

    def test_download_only_needs_download_and_sha(self):
        r = Resolution(1920, 1080)
        c = collection_with(Wallpaper("a_b.jpg", "B", "A", "all-rights-reserved", r))
        self.assertIn("download-required", codes(check_collection(c, {"a_b.jpg"}, {"a_b.jpg": r})))

    def test_all_rights_reserved_needs_holder(self):
        r = Resolution(1920, 1080)
        w = Wallpaper("a_b.jpg", "B", "A", "all-rights-reserved", r, download="https://x/y.jpg", sha256=SHA)
        self.assertIn("license", codes(check(collection_with(w), ("a_b.jpg", r))))

    def test_crop_box_required_for_cropped_download_only(self):
        r = Resolution(1920, 1080)
        w = Wallpaper("a_b.jpg", "B", "A", "official-wallpaper", r, download="https://x/y.jpg", sha256=SHA, processing=("crop",))
        self.assertIn("crop-box-required", codes(check(collection_with(w), ("a_b.jpg", r))))
        w2 = Wallpaper("a_b.jpg", "B", "A", "official-wallpaper", r, download="https://x/y.jpg", sha256=SHA,
                       processing=("crop",), crop=CropBox(0, 0, 1920, 1080))
        self.assertNotIn("crop-box-required", codes(check(collection_with(w2), ("a_b.jpg", r))))

    def test_external_collection_skips_name_and_size(self):
        r = Resolution(1200, 800)
        c = collection_with(Wallpaper("Fantasy-Autumn.png", "F", "A", "CC0-1.0", r), external=True)
        found = codes(check(c, ("Fantasy-Autumn.png", r)))
        self.assertFalse(found & {"name", "too-small", "ratio"})

    def test_personal_medium_skips_size_and_ratio(self):
        r = Resolution(1200, 800)
        c = collection_with(Wallpaper("a_b.jpg", "B", "A", "personal", r), medium="personal")
        self.assertEqual(check(c, ("a_b.jpg", r)), [])

    def test_too_small_and_ratio(self):
        r = Resolution(1500, 1000)
        found = codes(check(collection_with(Wallpaper("a_b.jpg", "B", "A", "CC0-1.0", r)), ("a_b.jpg", r)))
        self.assertTrue({"too-small", "ratio"} <= found)

    def test_resolution_mismatch(self):
        w = Wallpaper("a_b.jpg", "B", "A", "CC0-1.0", Resolution(1920, 1080))
        self.assertIn("resolution-mismatch", codes(check(collection_with(w), ("a_b.jpg", Resolution(2560, 1440)))))

    def test_unlisted_image_and_missing_file(self):
        r = Resolution(1920, 1080)
        c = collection_with(Wallpaper("a_b.jpg", "B", "A", "CC0-1.0", r))
        issues = check_collection(c, {"stray.png"}, {})
        self.assertEqual(codes(issues), {"unlisted", "missing"})
        self.assertEqual({i.level for i in issues if i.code == "missing"}, {"error"})
        d = collection_with(Wallpaper("a_b.jpg", "B", "A", "official-wallpaper", r, download="https://x/y", sha256=SHA))
        missing = [i for i in check_collection(d, set(), {}) if i.code == "missing"]
        self.assertEqual([i.level for i in missing], ["warning"])

    def test_field_checks(self):
        r = Resolution(1920, 1080)
        w = Wallpaper("a_b.jpg", "B", "A", "nope", r, year=1200, source="http://x", sha256="XYZ", processing=("zoom",), original="o.png")
        c = Collection("f", "sculpture", "Bad Movement", [w])
        found = codes(check(c, ("a_b.jpg", r)))
        self.assertTrue({"medium", "movement", "license", "year", "source", "sha256-format", "processing", "original-missing"} <= found)


class YearTagRulesTest(unittest.TestCase):
    R = Resolution(1920, 1080)

    def issues(self, **kw):
        return check(collection_with(Wallpaper("a_b.jpg", "B", "A", "CC0-1.0", self.R, **kw)), ("a_b.jpg", self.R))

    def test_year_invalid_forms_reported(self):
        from wallman.domain.years import YearSpan
        for bad in (YearSpan(1889, 1880), YearSpan(1200, 1200), YearSpan(1800, 2200), YearSpan(2101, 2101)):
            self.assertIn("year", codes(self.issues(year=bad)), bad)
        self.assertEqual(self.issues(year=YearSpan(1880, 1889)), [])
        self.assertEqual(self.issues(year=1900), [])  # a plain int is accepted as a single year
        self.assertEqual(self.issues(year=YearSpan(1400, 2100)), [])

    def test_invalid_tags_reported(self):
        self.assertIn("tag", codes(self.issues(tags=("Bad Tag",))))
        self.assertIn("tag", codes(self.issues(tags=("a", "a"))))
        self.assertEqual(self.issues(tags=("a", "b-c")), [])
