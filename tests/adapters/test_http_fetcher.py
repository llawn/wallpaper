import hashlib
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from wallman.adapters.http_fetcher import HttpFetcher
from wallman.errors import DownloadFailed

BODY = b"x" * 5000
UA = "WallpaperFetcher/1.0 (personal desktop wallpapers)"


class Handler(BaseHTTPRequestHandler):
    flaky = 0
    seen_ua = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        Handler.seen_ua = self.headers.get("User-Agent")
        if self.path == "/ok":
            self.send_response(200)
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)
        elif self.path == "/flaky":
            if Handler.flaky > 0:
                Handler.flaky -= 1
                self.send_response(503)
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"hi")
        elif self.path == "/short":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"abc")
            self.close_connection = True
        elif self.path == "/500":
            self.send_response(500)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


class HttpFetcherTest(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.sleeps = []
        self.f = HttpFetcher(UA, sleep=self.sleeps.append)

    def test_get_returns_bytes_and_sha256(self):
        data, digest = self.f.get(self.base + "/ok")
        self.assertEqual(data, BODY)
        self.assertEqual(digest, hashlib.sha256(BODY).hexdigest())
        self.assertEqual(Handler.seen_ua, UA)

    def test_only_web_urls(self):
        for url in ("file:///etc/passwd", "ftp://127.0.0.1/x", "data:,hello"):
            with self.subTest(url=url), self.assertRaises(DownloadFailed) as c:
                self.f.get(url)
            self.assertIn("http", str(c.exception))

    def test_get_rejects_oversize(self):
        f = HttpFetcher(UA, max_bytes=1000, sleep=self.sleeps.append)
        with self.assertRaises(DownloadFailed):
            f.get(self.base + "/ok")

    def test_get_404_raises_download_failed(self):
        with self.assertRaises(DownloadFailed) as c:
            self.f.get(self.base + "/nope")
        self.assertIn("404", str(c.exception))
        self.assertEqual(self.sleeps, [])

    def test_retries_5xx_then_succeeds(self):
        Handler.flaky = 2
        data, _ = self.f.get(self.base + "/flaky")
        self.assertEqual(data, b"hi")
        self.assertEqual(len(self.sleeps), 2)

    def test_retries_exhausted(self):
        with self.assertRaises(DownloadFailed) as c:
            self.f.get(self.base + "/500")
        self.assertIn("500", str(c.exception))
        self.assertEqual(len(self.sleeps), 3)

    def test_truncated_body_raises(self):
        with self.assertRaises(DownloadFailed):
            self.f.get(self.base + "/short")

    def test_garbage_status_line_raises_download_failed(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)

        def serve():
            c, _ = srv.accept()
            c.recv(4096)
            c.sendall(b"GARBAGE\r\n\r\n")
            c.close()
        threading.Thread(target=serve, daemon=True).start()
        with self.assertRaises(DownloadFailed):
            self.f.get(f"http://127.0.0.1:{srv.getsockname()[1]}/x")

    def test_unreachable_raises_download_failed(self):
        f = HttpFetcher(UA, retries=0, sleep=self.sleeps.append)
        with self.assertRaises(DownloadFailed):
            f.get("http://127.0.0.1:1/x")


if __name__ == "__main__":
    unittest.main()
