"""scripts/bench.py with a small N: every row runs and the gate works (timings are not checked here)."""
import contextlib
import importlib.util
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BENCH = Path(__file__).resolve().parents[2] / "scripts" / "bench.py"


def load_bench():
    spec = importlib.util.spec_from_file_location("wallman_bench", BENCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(shutil.which("magick") and shutil.which("git"), "ImageMagick or git not installed")
class BenchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp.name, "TMPDIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        tempfile.tempdir = None  # re-read TMPDIR
        self.addCleanup(setattr, tempfile, "tempdir", None)
        self.tmp = Path(tmp.name)
        self.bench = load_bench()

    def run_bench(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = self.bench.main(["--n", "30", *args])
        return code, out.getvalue()

    def test_small_run_prints_every_row_and_cleans_up(self):
        code, out = self.run_bench("--no-gate")
        self.assertEqual(code, 0)
        for row in self.bench.TARGETS:
            self.assertIn(row, out)
        self.assertEqual(list(self.tmp.glob("wallman-bench-*")), [])  # the temp repository is gone
        self.assertTrue(list((self.tmp / "wallman-bench").glob("base-*.jpg")))

    def test_a_missed_target_exits_1(self):
        with mock.patch.dict(self.bench.TARGETS, {"PATCH title": 0.0}):
            code, out = self.run_bench()
        self.assertEqual(code, 1)
        self.assertIn("missed: PATCH title", out)
