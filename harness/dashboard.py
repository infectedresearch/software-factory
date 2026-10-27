#!/usr/bin/env python3
"""Software factory board. Serves on 127.0.0.1:8766; Caddy exposes it at
https://fracktron.com/factory/ to admin sessions only.
"""

from __future__ import annotations

import json
import sqlite3
import ssl
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import config as C
import status

PAGE = (C.HOME / "board.html").read_bytes
RESEARCH = C.HOME / "research" / "page"
LOGIN_URL = "https://fracktron.com/auth/oauth/login?" + urlencode({"return_to": "/factory/"})
AUTH_URL = "https://127.0.0.1:8070/api/session"
ALLOWED_ORIGINS = {"https://fracktron.com"}
# auth/db.py uses the cwd-relative "db.sqlite" of the live checkout.
AUTH_DBS = (Path("/srv/factory/target/db.sqlite"),)


def api_key() -> str:
    for path in AUTH_DBS:
        if not path.is_file():
            continue
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
                row = db.execute("SELECT value FROM config WHERE key = 'auth_api_key'").fetchone()
        except sqlite3.Error:
            continue
        if row and row[0]:
            return str(row[0])
    return ""


def session_user(token: str) -> dict | None:
    key = api_key()
    if not key or not token or len(token) > 256:
        return None
    request = urllib.request.Request(
        AUTH_URL, data=b"", method="POST",
        headers={"X-Auth-Key": key, "X-Session-Token": token},
    )
    try:
        with urllib.request.urlopen(request, context=ssl._create_unverified_context(), timeout=5) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def cookie(header: str | None, name: str) -> str:
    for part in (header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def access(user: dict | None) -> str:
    if not user:
        return "login"
    return "allow" if user.get("is_admin") else "forbidden"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _gate(self) -> bool:
        decision = access(session_user(cookie(self.headers.get("Cookie"), "session")))
        if decision == "allow":
            return True
        if decision == "login":
            self.send_response(302)
            self.send_header("Location", LOGIN_URL)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return False
        self._send(403, b"Administrator access required\n", "text/plain; charset=utf-8")
        return False

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        if not self._gate():
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            self._send(200, PAGE(), "text/html; charset=utf-8")
            return
        if url.path == "/api/status":
            self._json(status.overview())
            return
        if url.path.startswith("/research/"):
            name = url.path.removeprefix("/research/") or "index.html"
            kinds = {".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".md": "text/markdown; charset=utf-8", ".pdf": "application/pdf"}
            path = RESEARCH / name
            if "/" in name or path.suffix not in kinds or not path.is_file():
                self._send(404, b"not found\n", "text/plain")
                return
            self._send(200, path.read_bytes(), kinds[path.suffix])
            return
        lane = C.lane(query.get("lane", [""])[0])
        if url.path == "/api/review" and lane:
            snap = status.snapshot(lane)
            self._json({"lane": lane.id, **status.review_state(lane.worktree, snap["passes"], lane)})
        elif url.path == "/api/diff" and lane:
            self._json(status.patch_for(lane.worktree, query.get("sha", [""])[0]))
        else:
            self._send(404, b"not found\n", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if not self._gate():
            return
        if self.headers.get("Origin") not in ALLOWED_ORIGINS:
            self._json({"ok": False, "error": "Cross-site request refused."}, 403)
            return
        length = min(int(self.headers.get("Content-Length") or "0"), 4096)
        try:
            payload = json.loads(self.rfile.read(length).decode() or "{}")
        except json.JSONDecodeError:
            self._json({"ok": False, "error": "Bad request."}, 400)
            return
        sha = str(payload.get("sha") or "")
        lane_id = str(payload.get("lane") or "")
        lane = C.lane(lane_id)
        missing = lambda: {"ok": False, "error": "Unknown lane."}  # noqa: E731
        routes = {
            "/api/stop": lambda: status.request_stop(lane_id or None),
            "/api/start": lambda: status.start_lanes(lane_id or None),
            "/api/model": lambda: status.set_model(str(payload.get("model") or "")),
            "/api/audit": lambda: status.start_audit(lane) if lane else missing(),
            "/api/smart-review": lambda: status.start_smart_review(lane) if lane else missing(),
            "/api/review/include-kept": lambda: status.include_kept(lane.worktree) if lane else missing(),
            "/api/inline-review": lambda: status.set_inline_review(bool(payload.get("on"))),
            "/api/reviewer": lambda: status.set_reviewer(str(payload.get("model") or "")),
            "/api/review/include": lambda: status.include(lane.worktree, sha) if lane else missing(),
            "/api/review/skip": lambda: status.skip(sha),
        }
        handler = routes.get(urlsplit(self.path).path)
        if handler is None:
            self._json({"ok": False, "error": "Unknown action."}, 404)
            return
        self._json(handler())


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
    server.daemon_threads = True
    print("factory board http://127.0.0.1:8766/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
