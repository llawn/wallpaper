"""Crop-box maths of wallman/web/static/cropper.js, run under node (skipped when node is absent)."""
import shutil
import subprocess
import unittest

from wallman.web.server import STATIC

SCRIPT = r"""
import * as c from %(url)s;
const W = (b) => JSON.stringify(b);
const ok = (cond, msg) => { if (!cond) { console.error("FAIL: " + msg); process.exitCode = 1; } };
const is169 = (b) => Math.abs(b.h - b.w * 9 / 16) <= 0.5 + 1e-9;
const ints = (b) => ["x", "y", "w", "h"].every((k) => Number.isInteger(b[k]));
const inside = (b, w, h) => b.x >= 0 && b.y >= 0 && b.x + b.w <= w && b.y + b.h <= h;
const check = (b, w, h, msg) => ok(ints(b) && is169(b) && inside(b, w, h), msg + " " + W(b));

// default: largest centred 16:9 box
let b = c.defaultBox(4000, 3000);          // 4:3 -> full width
ok(W(b) === W({x: 0, y: 375, w: 4000, h: 2250}), "4:3 default " + W(b));
b = c.defaultBox(3000, 1000);              // wide -> full height
ok(W(b) === W({x: 611, y: 0, w: 1777, h: 1000}), "wide default " + W(b));
b = c.defaultBox(1080, 1920);              // portrait
check(b, 1080, 1920, "portrait default");
ok(b.w === 1080 && b.h === 608 && b.y === 656, "portrait default size " + W(b));
for (const [w, h] of [[1, 1], [17, 9], [1001, 997], [7681, 4321], [333, 4000]]) check(c.defaultBox(w, h), w, h, `default ${w}x${h}`);

// move: clamped to the image
b = c.moveBox({x: 0, y: 375, w: 4000, h: 2250}, 50, 5000, 4000, 3000);
ok(W(b) === W({x: 0, y: 750, w: 4000, h: 2250}), "move clamps " + W(b));
b = c.moveBox({x: 100, y: 100, w: 1600, h: 900}, -500, -30.6, 4000, 3000);
ok(W(b) === W({x: 0, y: 69, w: 1600, h: 900}), "move rounds and clamps " + W(b));

// clamp: oversize box shrinks to fit, keeps 16:9
b = c.clampBox({x: -10, y: -10, w: 5000, h: 2813}, 4000, 3000);
check(b, 4000, 3000, "clamp oversize");
ok(b.w === 4000, "clamp keeps max width " + W(b));
b = c.clampBox({x: 3000, y: 2900, w: 1600, h: 900}, 4000, 3000);
ok(W(b) === W({x: 2400, y: 2100, w: 1600, h: 900}), "clamp position " + W(b));

// resize from a corner: opposite corner fixed, ratio kept, clamped
const start = {x: 1000, y: 1000, w: 1600, h: 900};
b = c.resizeBox(start, "se", 3000, 1500, 4000, 3000);        // anchor (1000,1000)
check(b, 4000, 3000, "resize se");
ok(b.x === 1000 && b.y === 1000 && b.w === 2000 && b.h === 1125, "resize se uses larger axis " + W(b));
b = c.resizeBox(start, "se", 99999, 99999, 4000, 3000);      // bounded by space below: 2000 -> w 3555
check(b, 4000, 3000, "resize se clamp");
ok(b.x === 1000 && b.y === 1000 && b.w === 3000 && b.h === 1688, "resize se clamp size " + W(b));
b = c.resizeBox(start, "nw", -5000, -5000, 4000, 3000);      // anchor (2600,1900)
check(b, 4000, 3000, "resize nw clamp");
ok(b.x + b.w === 2600 && b.y + b.h === 1900, "resize nw keeps anchor " + W(b));
b = c.resizeBox(start, "ne", 2700, 500, 4000, 3000);         // anchor (1000,1900)
check(b, 4000, 3000, "resize ne");
ok(b.x === 1000 && b.y + b.h === 1900, "resize ne anchor " + W(b));
b = c.resizeBox(start, "sw", 2590, 1005, 4000, 3000);        // collapse onto the anchor -> min size
check(b, 4000, 3000, "resize sw min");
ok(b.w >= c.MIN_W && b.x + b.w === 2600 && b.y === 1000, "resize sw min size " + W(b));

// output size check
ok(c.outputOk({x: 0, y: 0, w: 1800, h: 1013}) === true, "1800x1013 ok");
ok(c.outputOk({x: 0, y: 0, w: 1799, h: 1012}) === false, "1799 too small");
ok(c.outputOk({x: 0, y: 0, w: 1600, h: 900}) === false, "1600x900 too small");

// view <-> source scale
const v = c.viewScale(4000, 3000, 800, 800);
ok(v.scale === 0.2 && v.width === 800 && v.height === 600, "viewScale " + W(v));
// I4: which pixels a re-crop is expressed in
const url = (it) => "/api/images/" + it.file;
let o = c.cropOptions({ file: "a.jpg", download: "https://d/a.jpg", processing: ["crop"], resolution: [3200, 1800],
                        crop: [1, 2, 3200, 1800] }, url);
ok(o.fromOriginal && o.src === "https://d/a.jpg" && !o.width && W(o.initial) === W({x: 1, y: 2, w: 3200, h: 1800}),
   "download entry crops the original " + W(o));
o = c.cropOptions({ file: "s.png", download: "https://d/s.svg", processing: ["render"], resolution: [3840, 2160], crop: null }, url);
ok(!o.fromOriginal && o.src === "/api/images/s.png" && o.width === 3840 && o.height === 2160,
   "rendered entry crops the current file " + W(o));
ok(/rendered/i.test(o.heading), "rendered entry explains why " + o.heading);
o = c.cropOptions({ file: "b.jpg", download: null, processing: [], resolution: [4000, 3000], crop: null }, url);
ok(!o.fromOriginal && o.src === "/api/images/b.jpg" && o.width === 4000, "plain entry crops the current file " + W(o));
if (!process.exitCode) console.log("cropper ok");
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class CropperJsTest(unittest.TestCase):
    def test_crop_box_maths(self):
        url = (STATIC / "cropper.js").as_uri()
        r = subprocess.run(["node", "--input-type=module", "-e", SCRIPT % {"url": repr(url)}],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("cropper ok", r.stdout)


if __name__ == "__main__":
    unittest.main()
