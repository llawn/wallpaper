import unittest

from wallman.domain.buckets import ratio_bucket
from wallman.domain.models import Resolution


class BucketsTest(unittest.TestCase):
    def test_ratio_bucket_bounds(self):
        # (width, height, bucket): lower bound inclusive, upper bound exclusive
        cases = [
            (340, 100, "32:9"), (339, 100, "21:9"), (5120, 1440, "32:9"),
            (220, 100, "21:9"), (219, 100, "16:9"),
            (170, 100, "16:9"), (169, 100, "16:10"), (1920, 1080, "16:9"),
            (155, 100, "16:10"), (154, 100, "3:2"), (1920, 1200, "16:10"),
            (145, 100, "3:2"), (144, 100, "4:3"),
            (130, 100, "4:3"), (129, 100, "5:4"),
            (120, 100, "5:4"), (119, 100, "square"),
            (95, 100, "square"), (94, 100, "portrait"), (1080, 1920, "portrait"), (1, 1, "square"),
        ]
        for w, h, want in cases:
            self.assertEqual(ratio_bucket(Resolution(w, h)), want, f"{w}x{h}")
