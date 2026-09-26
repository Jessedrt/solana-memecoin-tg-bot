from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, urlparse

import requests

import bot
import serverless


def _authorized(req: BaseHTTPRequestHandler) -> bool:
    expected = os.getenv("SETUP_SECRET", "").strip()
    if not expected:
        return False
    key = (parse_qs(urlparse(req.path).query).get("key") or [""])[0]
    auth = req.headers.get("Authorization", "")
    return key == expected or auth == f"Bearer {expected}"


def _set_telegram_webhook(base: str) -> dict:
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise RuntimeError("TELEGRAM_WEBHOOK_SECRET is missing")
    r = requests.post(
        f"https://api.telegram.org/bot{bot.TELEGRAM_BOT_TOKEN}/setWebhook",
        json={
            "url": f"{base}/api/telegram",
            "secret_token": secret,
            "allowed_updates": ["message", "edited_message"],
            "drop_pending_updates": True,
        },
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description") or "Telegram setWebhook failed")
    return payload


def _create_qstash_schedule(base: str) -> dict:
    if not serverless.QSTASH_TOKEN:
        return {"configured": False, "reason": "QSTASH_TOKEN missing"}
    if not serverless.has_redis():
        return {"configured": False, "reason": "Upstash Redis missing"}
    if not serverless.SCANNER_SECRET:
        return {"configured": False, "reason": "SCANNER_SECRET missing"}

    destination = f"{base}/api/scan"
    url = f"https://qstash.upstash.io/v2/schedules/{quote(destination, safe='')}"
    schedule_id = os.getenv("QSTASH_SCHEDULE_ID", "solana-memecoin-scanner").strip()
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {serverless.QSTASH_TOKEN}",
            "Content-Type": "application/json",
            "Upstash-Cron": serverless.SCAN_CRON,
            "Upstash-Schedule-Id": schedule_id,
            "Upstash-Method": "POST",
            "Upstash-Retries": "1",
            "Upstash-Timeout": "120s",
            "Upstash-Forward-X-Scanner-Secret": serverless.SCANNER_SECRET,
        },
        json={"source": "qstash-schedule"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return {
        "configured": True,
        "schedule_id": data.get("scheduleId") or schedule_id,
        "cron": serverless.SCAN_CRON,
        "destination": destination,
    }


class handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not _authorized(self):
            self._reply(401, {"ok": False, "error": "unauthorized"})
            return
        if not bot.TELEGRAM_BOT_TOKEN:
            self._reply(500, {"ok": False, "error": "TELEGRAM_BOT_TOKEN missing"})
            return

        base = serverless.production_base_url()
        if not base:
            self._reply(500, {"ok": False, "error": "Could not determine production URL. Set PUBLIC_BASE_URL."})
            return

        try:
            webhook = _set_telegram_webhook(base)
            schedule = _create_qstash_schedule(base)
            self._reply(
                200,
                {
                    "ok": True,
                    "base_url": base,
                    "telegram_webhook": webhook.get("description", "configured"),
                    "redis": serverless.has_redis(),
                    "qstash_schedule": schedule,
                    "next": "Open Telegram and send /start, then /status.",
                },
            )
        except Exception as exc:
            self._reply(500, {"ok": False, "error": str(exc)[:500]})
