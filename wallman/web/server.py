"""Local HTTP server: token auth, JSON dispatch, static files. Bound to 127.0.0.1 only."""
from __future__ import annotations

import gc
import http.cookies
import json
import mimetypes
import secrets
import shutil
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..bootstrap import App
from ..errors import UploadTooLarge, WallmanError
from . import docs
from .routes import ROUTES, Binary, Request
from .thumbs import ThumbPool

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 200 * 2**20 + 2**20
COOKIE = "wallman_token"
PLACEHOLDER = b"<!doctype html><html><head><meta charset=utf-8><title>wallman</title></head>" \
              b"<body><h1>wallman</h1><p>The UI is not installed yet.</p></body></html>"
DENIED = b"<!doctype html><html><head><meta charset=utf-8><title>wallman</title></head>" \
         b"<body><p>Unauthorized: open the URL printed by <code>just serve</code>.</p></body></html>"


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app: App, port: int, token: str, static_dir: Path = STATIC) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.app, self.token, self.static_dir = app, token, Path(static_dir)
        self.thumbs = ThumbPool(app.thumbs_dir, app.tool)

    def server_close(self) -> None:
        self.thumbs.close()
        super().server_close()

    @property
    def port(self) -> int:
        return self.server_address[1]


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"
    # headers and body go out in separate writes: with Nagle on, the body of a keep-alive response waits for the
    # client's delayed ACK (~40 ms per thumbnail or small JSON reply)
    disable_nagle_algorithm = True

    def log_message(self, fmt, *args):  # quiet: the console shows the URL, not every request
        pass

    # --- plumbing -------------------------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _error(self, status: int, name: str, message: str) -> None:
        self._json(status, {"error": name, "message": message})

    def _binary(self, b: Binary) -> None:
        if b.not_modified:
            self.send_response(304)
            self.send_header("ETag", b.etag)
            self.send_header("Cache-Control", b.cache)
            self.send_header("Content-Length", "0")
            return self.end_headers()
        if b.body is not None:
            return self._send(200, b.body if self.command != "HEAD" else b"", b.content_type, {"Cache-Control": b.cache})
        self._file(b.path, b.content_type, b.cache, b.etag)

    def _file(self, path: Path, ctype: str, cache: str = "no-cache", etag: str | None = None) -> None:
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", cache)
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def _token_ok(self, supplied: str | None) -> bool:
        return supplied is not None and secrets.compare_digest(supplied.encode(), self.server.token.encode())

    def _authed(self) -> bool:
        if self._token_ok(self.headers.get("X-Wallman-Token")):
            return True
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return False
        return COOKIE in jar and self._token_ok(jar[COOKIE].value)

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        port = self.server.port
        return origin is None or origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    # --- dispatch ---------------------------------------------------------------------------------
    def do_GET(self): self._handle()
    def do_HEAD(self): self._handle()
    def do_POST(self): self._handle()
    def do_PATCH(self): self._handle()
    def do_DELETE(self): self._handle()

    def _handle(self) -> None:
        self._body_read = False
        try:
            parts = urllib.parse.urlsplit(self.path)
            path = parts.path
            if path.startswith("/api/"):
                self._api(path, parts.query)
            else:
                self._page(path, parts.query)
            if not self._body_read and self.headers.get("Content-Length", "0") != "0":
                self.close_connection = True  # an unread body would corrupt the next keep-alive request
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:  # last resort: never leak a traceback to the client
            print(f"wallman: internal error: {e!r}", file=sys.stderr)
            self.close_connection = True
            try:
                self._error(500, "InternalError", "internal error")
            except OSError:
                pass

    def _page(self, path: str, query: str) -> None:
        if self.command not in ("GET", "HEAD"):
            return self._error(405, "MethodNotAllowed", "method not allowed")
        if path == "/":
            given = urllib.parse.parse_qs(query).get("token", [None])[0]
            if self._token_ok(given):
                return self._send(302, b"", "text/plain", {
                    "Location": "/", "Set-Cookie": f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/"})
            if not self._authed():
                return self._send(401, DENIED, "text/html; charset=utf-8")
            index = self.server.static_dir / "index.html"
            return self._send(200, index.read_bytes() if index.is_file() else PLACEHOLDER, "text/html; charset=utf-8")
        if path == docs.BASE or path.startswith(docs.BASE + "/"):
            if not self._authed():
                return self._send(401, DENIED, "text/html; charset=utf-8")
            rest = path[len(docs.BASE):].rstrip("/") if path.endswith("/") and path.count("/") == 3 else path[len(docs.BASE):]
            if not rest:
                status, page = docs.index_page()
            elif rest.count("/") == 1:
                status, page = docs.license_page(urllib.parse.unquote(rest[1:]))
            else:
                status, page = docs.license_page("")
            return self._send(status, page.encode(), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            base = self.server.static_dir.resolve()
            target = (base / urllib.parse.unquote(path[len("/static/"):])).resolve()
            if target.is_relative_to(base) and target.is_file():
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                return self._file(target, ctype)
        self._error(404, "NotFound", "not found")

    def _api(self, path: str, query: str) -> None:
        method = self.command
        if method == "HEAD":
            method = "GET"
        if not self._authed():
            return self._error(401, "Unauthorized", "missing or wrong token")
        if method in ("POST", "PATCH", "DELETE") and not self._origin_ok():
            return self._error(403, "Forbidden", "cross-origin request refused")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            return self._error(400, "BadRequest", "invalid Content-Length")
        if length > MAX_BODY:
            return self._error(413, UploadTooLarge.__name__, "request body is too large")
        streaming = method == "POST" and path == "/api/uploads"  # the body goes to disk, not to memory
        body = b"" if streaming or not length else self.rfile.read(length)
        self._body_read = not streaming
        handler, match = None, None
        for m, rx, fn in ROUTES:
            hit = rx.fullmatch(path)
            if hit and m == method:
                handler, match = fn, hit
                break
        if handler is None:
            return self._error(404, "NotFound", f"no route for {method} {path}")
        parsed = urllib.parse.parse_qs(query, keep_blank_values=True)
        request = Request({k: v[-1] for k, v in parsed.items()}, body, self.headers, self.server.thumbs,
                          self.rfile if streaming else None, length, parsed)
        try:
            status, payload = handler(self.server.app, match, request)
        except WallmanError as e:
            return self._error(e.http_status, type(e).__name__, str(e))
        except ValueError as e:
            return self._error(400, "BadRequest", str(e))
        if streaming:
            self._body_read = True
        if isinstance(payload, Binary):
            return self._binary(payload)
        self._json(status, payload)


def make_server(app: App, port: int = 0, static_dir: Path | None = None) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_urlsafe(24)
    return Server(app, port, token, static_dir or STATIC), token


def warm_up(app: App) -> None:
    """Build the library index now (not in the first request), then move what survives (~1M objects at 10,000
    entries) out of the cyclic collector's sight: a full collection would otherwise walk all of it (~100 ms,
    measured inside a PATCH) in the middle of a later request. Frozen objects are still freed by reference
    counting when the index drops them. Process-wide, so only the server does it."""
    app.index.warm()
    gc.collect()
    gc.freeze()


def serve(app: App, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    app.uploads.purge()
    warm_up(app)
    server, token = make_server(app, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/?token={token}"
    print(f"wallman UI: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
