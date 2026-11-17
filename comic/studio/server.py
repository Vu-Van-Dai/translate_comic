"""Studio's local web server: stdlib only, bound to 127.0.0.1.

A browser page rather than an .exe (the operator, 2026-11-08): nothing to
build or sign, and the same code can be frozen into an .exe later. Requests
whose Host header is not this machine are refused, so a web page elsewhere
cannot drive it through DNS rebinding.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from comic.studio import store

STATIC = Path(__file__).parent / "static"
MAX_BODY = 4_000_000


class Handler(BaseHTTPRequestHandler):
    server_version = "ComicStudio/1"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # -- helpers -----------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost", "::1")

    def _send(self, code: int, body: bytes, ctype: str, cache: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _query(self) -> dict[str, str]:
        q = parse_qs(urlparse(self.path).query)
        return {k: v[0] for k, v in q.items()}

    def _page_args(self, q: dict) -> tuple[str, str, int]:
        try:
            return q["series"], q["chapter"], int(q["page"])
        except (KeyError, ValueError):
            raise store.StudioError("series, chapter and page are required") from None

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        route = urlparse(self.path).path
        q = self._query()
        try:
            if route in ("/", "/index.html"):
                return self._static("index.html")
            if route.startswith("/static/"):
                return self._static(route[len("/static/"):])
            if route == "/api/series":
                return self._json(store.list_series())
            if route == "/api/chapter":
                return self._json(store.chapter_info(q.get("series", ""), q.get("chapter", "")))
            if route == "/api/page":
                return self._json(store.load_page(*self._page_args(q)))
            if route == "/image":
                f = store.page_image(*self._page_args(q))
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                return self._send(200, f.read_bytes(), ctype, cache=True)
            return self._send(404, b"not found", "text/plain")
        except store.StudioError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:  # report it in the page instead of dropping the connection
            return self._json({"error": f"{type(exc).__name__}: {str(exc)[:300]}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        route = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json({"error": "request too large"}, 413)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json({"error": "body is not JSON"}, 400)
        try:
            if route == "/api/chapter-reviewed":
                return self._json(store.set_chapter_reviewed(
                    body.get("series", ""), body.get("chapter", ""), bool(body.get("reviewed", True))))
            if route == "/api/page":
                series, chapter, page = body.get("series"), body.get("chapter"), body.get("page")
                if not isinstance(page, int):
                    raise store.StudioError("page must be a number")
                return self._json(store.save_page(series, chapter, page, body))
            return self._send(404, b"not found", "text/plain")
        except store.StudioError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:  # surface, never silently drop an edit
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _static(self, name: str) -> None:
        f = (STATIC / name).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)


def serve(series: str | None, chapter: str | None, port: int, open_browser: bool) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    if series:
        url += f"?series={quote(series)}" + (f"&chapter={quote(chapter)}" if chapter else "")
    print(f"Studio: {url}   (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
