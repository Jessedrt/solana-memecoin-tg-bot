from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import bot
import serverless


def _authorized(req: BaseHTTPRequestHandler) -> bool:
    scanner_secret = os.getenv("SCANNER_SECRET", "").strip()
    cron_secret = os.getenv("CRON_SECRET", "").strip()
    auth = req.headers.get("Authorization", "")
    forwarded = req.headers.get("X-Scanner-Secret", "")
    key = (parse_qs(urlparse(req.path).query).get("key") or [""])[0]

    if scanner_secret and (forwarded == scanner_secret or key == scanner_secret):
        return True
    if cron_secret and auth == f"Bearer {cron_secret}":
        return True
    if scanner_secret and auth == f"Bearer {scanner_secret}":
        return True
    return False


class handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _run(self) -> None:
        if not _authorized(self):
            self._reply(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            stats = serverless.run_scan(require_durable_state=True)
            self._reply(200, {"ok": True, "stats": stats, "state": "upstash"})
        except Exception as exc:
            bot.log.exception("serverless scan failed: %s", exc)
            self._reply(503, {"ok": False, "error": str(exc)[:500]})

    def do_GET(self) -> None:
        self._run()

    def do_POST(self) -> None:
        self._run()
