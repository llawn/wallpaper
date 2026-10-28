import unittest

from wallman.domain.years import YearSpan, parse_year, suggest_years, year_from_toml, year_to_toml


class YearsTest(unittest.TestCase):
    def test_parse_year_forms(self):
        cases = {
            "1889": (1889, 1889), " 1889 ": (1889, 1889), "188x": (1880, 1889), "188X": (1880, 1889),
            "18xx": (1800, 1899), "1880s": (1880, 1889), "1800s": (1800, 1899), "1900s": (1900, 1999),
            "19th century": (1800, 1899), "20th Century": (1900, 1999), "1st century": None,
            "1880-1895": (1880, 1895), "1880–1895": (1880, 1895), "1880 - 1895": (1880, 1895),
            "c. 1880": (1875, 1885), "ca 1880": (1875, 1885), "circa 1880": (1875, 1885), "c.1880": (1875, 1885),
            "1895-1880": None, "1200": None, "2101": None, "garbage": None, "": None, "188": None, "12xx": None,
        }
        for text, want in cases.items():
            got = parse_year(text)
            self.assertEqual(got, YearSpan(*want) if want else None, text)

    def test_label_and_toml(self):
        self.assertEqual(YearSpan(1889, 1889).label, "1889")
        self.assertEqual(YearSpan(1880, 1889).label, "1880–1889")
        self.assertEqual(year_to_toml(YearSpan(1889, 1889)), 1889)
        self.assertEqual(year_to_toml(YearSpan(1880, 1889)), [1880, 1889])
        self.assertEqual(year_from_toml(1889), YearSpan(1889, 1889))
        self.assertEqual(year_from_toml([1880, 1889]), YearSpan(1880, 1889))
        self.assertIsNone(year_from_toml(None))
        for bad in ("1889", True, [1, 2, 3], ["a", "b"], 1.5):
            self.assertIsNone(year_from_toml(bad))

    def test_overlaps(self):
        self.assertTrue(YearSpan(1880, 1889).overlaps(YearSpan(1889, 1900)))
        self.assertFalse(YearSpan(1880, 1889).overlaps(YearSpan(1890, 1900)))

    def test_suggest_years_prefix_20(self):
        self.assertEqual(suggest_years("20"), ["2000s", "2010s", "2020s", "20th century", "21st century"])

    def test_suggest_years_other_prefixes(self):
        self.assertEqual(suggest_years("188", 4), ["1880s", "1880", "1881", "1882"])
        self.assertEqual(suggest_years("1889"), ["1889", "1880s", "19th century"])
        self.assertEqual(suggest_years(""), [])
        self.assertEqual(suggest_years("abc"), [])
        self.assertEqual(suggest_years("99"), [])
        for prefix in ("1", "19", "20", "188", "210", "2100", "18", "1805", "14", "140", "21"):
            for s in suggest_years(prefix, 50):
                self.assertIsNotNone(parse_year(s), (prefix, s))
        self.assertEqual(suggest_years("2100"), ["2100"])  # decade/century labels past 2100 are filtered out
        self.assertEqual(suggest_years("210"), ["2100"])
