import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tests.cli.test_cli import git, make_image
from tests.fakes import FakeFetcher
from tests.services import fixture
from wallman import bootstrap
from wallman.web.server import make_server

FILE = "claude-monet_water-lilies_1906.jpg"
FOLDER = "painting/impressionism"
REF = f"{FOLDER}/{FILE}"


class ApiBase(unittest.TestCase):
    """Server fixture shared by the API test modules (no tests of its own)."""
    def setUp(self):
        if not shutil.which("magick"):
            self.skipTest("ImageMagick not installed")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.root, self.cache, self.src = base / "repo", base / "cache", base / "src"
        self.images = self.root / "images"
        self.images.mkdir(parents=True)
        fixture.write_collection(self.images, FOLDER, "painting", "impressionism", [
            fixture.entry(FILE, "Water Lilies", "Claude Monet", "public-domain", 1906, "https://example.org/monet")])
        make_image(self.images / REF)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")
        self.fetcher = FakeFetcher()
        self.app = bootstrap.build(self.root, self.cache, self.fetcher, config_home=base / "config")
        self.static = base / "static"
        self.static.mkdir()
        self.server, self.token = make_server(self.app, 0, self.static)
        self.port = self.server.server_address[1]
        t = threading.Thread(target=self.server.serve_forever)
        t.start()
        self.addCleanup(t.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None, headers=None, token=True, raw=None, ctype=None):
        h = dict(headers or {})
        if token:
            h["X-Wallman-Token"] = self.token
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            ctype = "application/json"
        if ctype:
            h["Content-Type"] = ctype
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers, e.read()

    def api(self, method, path, body=None, **kw):
        status, _, data = self.call(method, path, body, **kw)
        return status, json.loads(data)

    def upload(self, size="3840x2160", color="blue"):
        img = self.src / "u.jpg"
        make_image(img, size=size, color=color)
        return self.api("POST", "/api/uploads", raw=img.read_bytes(), ctype="application/octet-stream",
                        headers={"X-Filename": "u.jpg"})

    META = {"folder": FOLDER, "title": "Blue Dancers", "artist": "Edgar Degas", "year": 1893,
            "license": "public-domain", "license_detail": None, "source": "https://example.org/degas", "crop": None}


@unittest.skipUnless(shutil.which("magick"), "ImageMagick not installed")
class ApiTest(ApiBase):
    def test_api_requires_token(self):
        status, body = self.api("GET", "/api/collections", token=False)
        self.assertEqual((status, body["error"]), (401, "Unauthorized"))
        status, _ = self.api("GET", "/api/collections", headers={"X-Wallman-Token": "nope"}, token=False)
        self.assertEqual(status, 401)

    def test_cookie_auth_on_root(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        op = urllib.request.build_opener(NoRedirect)
        with self.assertRaises(urllib.error.HTTPError) as c:
            op.open(f"http://127.0.0.1:{self.port}/?token={self.token}")
        with c.exception as e:
            self.assertEqual(e.code, 302)
            self.assertEqual(e.headers["Location"], "/")
            cookie = e.headers["Set-Cookie"]
        self.assertIn(f"wallman_token={self.token}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        status, _, _ = self.call("GET", "/api/collections", headers={"Cookie": f"wallman_token={self.token}"}, token=False)
        self.assertEqual(status, 200)
        status, _, page = self.call("GET", "/", headers={"Cookie": f"wallman_token={self.token}"}, token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"<html", page.lower())
        status, _, page = self.call("GET", "/", token=False)
        self.assertEqual(status, 401)
        self.assertIn(b"just serve", page)
        status, _, _ = self.call("GET", "/?token=wrong", token=False)
        self.assertEqual(status, 401)

    def test_static_mime(self):
        (self.static / "zz_test.css").write_text("a{}")
        status, headers, _ = self.call("GET", "/static/zz_test.css", token=False)
        self.assertEqual((status, headers.get_content_type()), (200, "text/css"))
        status, _, _ = self.call("GET", "/static/..%2Fstatic_secret", token=False)
        self.assertEqual(status, 404)

    def test_origin_rejected_on_mutation(self):
        status, body = self.api("DELETE", "/api/wallpapers", {"refs": []}, headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, body = self.api("DELETE", "/api/wallpapers", {"refs": []},
                                headers={"Origin": f"http://localhost:{self.port}"})
        self.assertEqual((status, body), (200, {"deleted": 0}))

    def test_api_rejects_traversal(self):
        status, body = self.api("GET", "/api/wallpapers/..%2F..%2Fetc/passwd")
        self.assertEqual((status, body["error"]), (400, "PathOutsideRepo"))

    def test_list_and_get(self):
        status, cols = self.api("GET", "/api/collections")
        self.assertEqual(cols, [{"folder": FOLDER, "medium": "painting", "movement": "impressionism",
                                 "count": 1, "external": False}])
        status, page = self.api("GET", "/api/wallpapers?folder=painting&q=monet&license=public-domain")
        self.assertEqual((page["total"], page["offset"], len(page["items"])), (1, 0, 1))
        status, one = self.api("GET", f"/api/wallpapers/{REF}")
        row = one
        self.assertEqual((row["folder"], row["file"], row["resolution"], row["crop"], row["processing"]),
                         (FOLDER, FILE, [3840, 2160], None, []))
        self.assertTrue(row["shareable"])
        self.assertEqual(row["issues"], [])
        status, body = self.api("GET", f"/api/wallpapers/{FOLDER}/missing.jpg")
        self.assertEqual((status, body["error"]), (404, "NotFound"))
        status, page = self.api("GET", "/api/wallpapers?q=zzz")
        self.assertEqual(page["items"], [])
        self.assertEqual(self.api("GET", "/api/nope")[0], 404)

    def test_licenses_and_media(self):
        _, lic = self.api("GET", "/api/licenses")
        self.assertIn({"id": "MIT", "label": "MIT: free to share and modify, keep the notice", "shareable": True,
                       "doc": "/docs/licenses/MIT"}, lic)
        self.assertEqual([l["id"] for l in lic], sorted(l["id"] for l in lic))
        _, media = self.api("GET", "/api/media")
        self.assertEqual(media, sorted(media))
        self.assertIn("painting", media)

    def test_upload_then_add_flow(self):
        status, up = self.upload()
        self.assertEqual(status, 200)
        self.assertEqual((up["original_name"], up["width"], up["height"], up["needs_crop"]), ("u.jpg", 3840, 2160, False))
        self.assertAlmostEqual(up["ratio"], 16 / 9)
        status, made = self.api("POST", "/api/wallpapers", {**self.META, "upload_id": up["upload_id"]})
        self.assertEqual(status, 201, made)
        self.assertEqual(made["file"], "edgar-degas_blue-dancers_1893.jpg")
        self.assertTrue((self.images / FOLDER / made["file"]).is_file())
        _, page = self.api("GET", "/api/wallpapers")
        self.assertEqual(page["total"], 2)

    def test_upload_url(self):
        img = self.src / "w.jpg"
        make_image(img)
        self.fetcher.responses["https://example.org/w.jpg"] = img.read_bytes()
        status, up = self.api("POST", "/api/uploads/url", {"url": "https://example.org/w.jpg"})
        self.assertEqual(status, 200)
        self.assertEqual(up["download"], "https://example.org/w.jpg")

    def test_add_needs_crop_returns_422(self):
        _, up = self.upload(size="3000x2000")
        self.assertTrue(up["needs_crop"])
        status, body = self.api("POST", "/api/wallpapers", {**self.META, "upload_id": up["upload_id"]})
        self.assertEqual((status, body["error"]), (422, "RatioNeedsCrop"))
        crop = {"x": 0, "y": 156, "w": 3000, "h": 1688}
        status, made = self.api("POST", "/api/wallpapers", {**self.META, "upload_id": up["upload_id"], "crop": crop})
        self.assertEqual(status, 201, made)
        self.assertIn("crop", made["processing"])
        self.assertEqual(made["resolution"], [3000, 1688])

    def test_add_bad_body(self):
        status, body = self.api("POST", "/api/wallpapers", {"upload_id": "x"})
        self.assertEqual((status, body["error"]), (400, "BadRequest"))
        status, body = self.api("POST", "/api/wallpapers", raw=b"{not json", ctype="application/json")
        self.assertEqual((status, body["error"]), (400, "BadRequest"))
        status, body = self.api("PATCH", f"/api/wallpapers/{REF}", {"bogus": 1})
        self.assertEqual((status, body["error"]), (400, "BadRequest"))

    def test_patch_renames(self):
        status, body = self.api("PATCH", f"/api/wallpapers/{REF}", {"title": "Nympheas"})
        self.assertEqual(status, 200, body)
        self.assertEqual((body["folder"], body["file"], body["title"]), (FOLDER, "claude-monet_nympheas_1906.jpg", "Nympheas"))
        self.assertTrue((self.images / FOLDER / "claude-monet_nympheas_1906.jpg").is_file())
        self.assertFalse((self.images / REF).exists())

    def test_crop_route(self):
        status, body = self.api("POST", f"/api/wallpapers/{REF}/crop", {"x": 0, "y": 0, "w": 3200, "h": 1800})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["resolution"], [3200, 1800])

    def test_delete_many(self):
        status, body = self.api("DELETE", "/api/wallpapers", {"refs": [{"folder": FOLDER, "file": FILE}]})
        self.assertEqual((status, body), (200, {"deleted": 1}))
        self.assertFalse((self.images / REF).exists())
        _, page = self.api("GET", "/api/wallpapers")
        self.assertEqual((page["items"], page["total"]), ([], 0))

    def test_status_shape(self):
        (self.images / FOLDER / FILE).unlink()
        status, body = self.api("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"changes", "errors", "warnings", "theme_mtime", "theme_warnings", "git_error"})
        self.assertEqual(body["errors"], 1)
        self.assertIsInstance(body["changes"], list)
        make_image(self.root / "new.png")
        git(self.root, "add", "new.png")
        _, body = self.api("GET", "/api/status")
        self.assertTrue(any(c["path"].endswith("new.png") for c in body["changes"]), body["changes"])

    def test_thumbnail_cached(self):
        status, headers, data = self.call("GET", f"/api/thumbs/{REF}")
        self.assertEqual((status, headers.get_content_type()), (200, "image/jpeg"))
        size = subprocess.run(["magick", "identify", "-format", "%w", "jpeg:-"], input=data, capture_output=True).stdout
        self.assertEqual(size, b"360")
        files = list(self.app.thumbs_dir.glob("*.jpg"))
        self.assertEqual(len(files), 1)
        before = files[0].stat().st_mtime_ns
        self.call("GET", f"/api/thumbs/{REF}")
        self.assertEqual(files[0].stat().st_mtime_ns, before)
        self.assertEqual(len(list(self.app.thumbs_dir.glob("*.jpg"))), 1)

    def test_image_route(self):
        status, headers, data = self.call("GET", f"/api/images/{REF}")
        self.assertEqual((status, headers.get_content_type()), (200, "image/jpeg"))
        self.assertEqual(data, (self.images / REF).read_bytes())
        status, _, _ = self.call("GET", f"/api/images/{FOLDER}/none.jpg")
        self.assertEqual(status, 404)

    def toml(self):
        return (self.images / FOLDER / "wallpapers.toml").read_bytes()

    def test_bad_value_types_rejected(self):
        _, up = self.upload()
        before = self.toml()
        for bad in ({"year": ""}, {"year": '1893\nsha256 = "x"'}, {"year": True}, {"title": 5}, {"artist": ["a"]},
                    {"title": ""}, {"crop": {}}, {"crop": {"x": 0, "y": 0, "w": 10}},
                    {"crop": {"x": -1, "y": 0, "w": 10, "h": 10}}, {"crop": {"x": 0, "y": 0, "w": True, "h": 10}},
                    {"crop": {"x": 0, "y": 0, "w": 0, "h": 10}}):
            status, body = self.api("POST", "/api/wallpapers", {**self.META, **bad, "upload_id": up["upload_id"]})
            self.assertEqual((status, body["error"]), (400, "BadRequest"), bad)
        for bad in ({"year": ""}, {"year": "1893\nx = 1"}, {"title": 5}, {"license_detail": 3}, {"source": {}}):
            status, body = self.api("PATCH", f"/api/wallpapers/{REF}", bad)
            self.assertEqual((status, body["error"]), (400, "BadRequest"), bad)
        self.assertEqual(self.toml(), before)

    def test_year_span_and_tags_serialised_and_patchable(self):
        self.upload()
        status, body = self.api("PATCH", f"/api/wallpapers/{REF}", {"year": [1880, 1889], "tags": ["Sea", "blue"]})
        self.assertEqual(status, 200, body)
        self.assertEqual((body["year"], body["tags"]), ([1880, 1889], ["blue", "sea"]))
        status, body = self.api("PATCH", f"/api/wallpapers/{body['folder']}/{body['file']}", {"year": 1893})
        self.assertEqual((status, body["year"]), (200, 1893))
        status, body = self.api("PATCH", f"/api/wallpapers/{body['folder']}/{body['file']}", {"year": [1890, 1880]})
        self.assertEqual(status, 400)

    def test_bad_destination_folder_400(self):
        _, up = self.upload()
        before = self.toml()
        for folder in ("paintng/x", "painting/../poster", ""):
            status, body = self.api("POST", "/api/wallpapers", {**self.META, "folder": folder, "upload_id": up["upload_id"]})
            self.assertEqual(status, 400, (folder, body))
            status, body = self.api("PATCH", f"/api/wallpapers/{REF}", {"folder": folder})
            self.assertEqual(status, 400, (folder, body))
        self.assertEqual(self.toml(), before)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_thumb_and_image_routes_only_serve_images(self):
        (self.images / FOLDER / "notes.txt").write_text("hello")
        for route in ("thumbs", "images"):
            status, _, _ = self.call("GET", f"/api/{route}/{FOLDER}/notes.txt")
            self.assertEqual(status, 400, route)
            status, _, _ = self.call("GET", f"/api/{route}/{FOLDER}/wallpapers.toml")
            self.assertEqual(status, 400, route)

    def test_unnameable_title_is_400(self):
        _, up = self.upload()
        status, body = self.api("POST", "/api/wallpapers", {**self.META, "title": "!!!", "artist": None,
                                                            "year": None, "upload_id": up["upload_id"]})
        self.assertEqual((status, body["error"]), (400, "BadRequest"), body)

    def test_empty_optional_fields_stored_as_null(self):
        _, up = self.upload()
        status, made = self.api("POST", "/api/wallpapers", {**self.META, "source": "", "license_detail": "",
                                                            "upload_id": up["upload_id"]})
        self.assertEqual(status, 201, made)
        self.assertEqual((made["source"], made["license_detail"]), (None, None))
        self.assertNotIn(b'source = ""', (self.images / FOLDER / "wallpapers.toml").read_bytes())

    def test_upload_requires_filename(self):
        status, body = self.api("POST", "/api/uploads", raw=b"abc", ctype="application/octet-stream")
        self.assertEqual((status, body["error"]), (400, "BadRequest"))

    def test_upload_not_an_image(self):
        status, body = self.api("POST", "/api/uploads", raw=b"abc", ctype="application/octet-stream",
                                headers={"X-Filename": "a%20b.jpg"})
        self.assertEqual((status, body["error"]), (400, "InvalidImage"))
        self.assertEqual(list((self.cache / "uploads").iterdir()), [])

    def test_negative_content_length_400(self):
        for value in ("-5", "abc"):
            c = http.client.HTTPConnection("127.0.0.1", self.port)
            try:
                c.putrequest("POST", "/api/wallpapers")
                c.putheader("X-Wallman-Token", self.token)
                c.putheader("Content-Length", value)
                c.endheaders()
                r = c.getresponse()
                self.assertEqual(r.status, 400)
                self.assertEqual(json.loads(r.read())["error"], "BadRequest")
            finally:
                c.close()

    def test_head_has_no_body_and_files_are_no_cache(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        try:
            for path in (f"/api/images/{REF}", f"/api/thumbs/{REF}"):
                c.request("HEAD", path, headers={"X-Wallman-Token": self.token})
                r = c.getresponse()
                self.assertEqual(r.status, 200)
                self.assertEqual(r.read(), b"")
                self.assertEqual(r.getheader("Cache-Control"), "no-cache")
                self.assertGreater(int(r.getheader("Content-Length")), 0)
        finally:
            c.close()

    def test_upload_too_large_413(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        try:
            c.putrequest("POST", "/api/uploads")
            c.putheader("X-Wallman-Token", self.token)
            c.putheader("Content-Type", "application/octet-stream")
            c.putheader("Content-Length", str(201 * 2**20 + 5))
            c.endheaders()
            r = c.getresponse()
            self.assertEqual(r.status, 413)
            self.assertEqual(json.loads(r.read())["error"], "UploadTooLarge")
        finally:
            c.close()


if __name__ == "__main__":
    unittest.main()
