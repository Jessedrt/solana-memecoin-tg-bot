from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

import bot
import serverless


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        payload = {
            "ok": True,
            "service": "solana-memecoin-tg-bot",
            "telegram_token": bool(bot.TELEGRAM_BOT_TOKEN),
            "chat_registered": bool(serverless.get_chat_id()),
            "redis": serverless.has_redis(),
            "redis_backend": ("url" if serverless.STANDARD_REDIS_URL else ("rest" if serverless.REDIS_REST_URL else "none")),
            "qstash": bool(serverless.QSTASH_TOKEN),
            "solana_tracker": bool(bot.SOLANA_TRACKER_API_KEY),
            "deep_dd_required": bool(bot.REQUIRE_DEEP_DD),
            "scheduler": serverless.SCAN_CRON,
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
