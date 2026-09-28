from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler

import bot
import serverless


class handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._reply(200, {"ok": True, "route": "telegram-webhook"})

    def do_POST(self) -> None:
        if not bot.TELEGRAM_BOT_TOKEN:
            self._reply(500, {"ok": False, "error": "TELEGRAM_BOT_TOKEN missing"})
            return

        expected = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
        if expected and self.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != expected:
            self._reply(403, {"ok": False, "error": "invalid webhook secret"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            update = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._reply(400, {"ok": False, "error": "invalid json"})
            return

        if not serverless.claim_telegram_update(update.get("update_id")):
            self._reply(200, {"ok": True, "duplicate": True})
            return

        msg = update.get("message") or update.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        if not text or not chat_id:
            self._reply(200, {"ok": True, "ignored": True})
            return

        if bot.TELEGRAM_CHAT_ID and chat_id != bot.TELEGRAM_CHAT_ID:
            self._reply(200, {"ok": True, "ignored": True})
            return

        tg = bot.Telegram(bot.TELEGRAM_BOT_TOKEN, bot.TELEGRAM_CHAT_ID or chat_id)
        cmd = text.split()[0].split("@")[0].lower()

        try:
            if cmd == "/start":
                serverless.register_chat_id(chat_id)
                tg.send("Pulse registered. Use /on, /off, /status, or /scan.", chat_id=chat_id)

            elif cmd == "/scan":
                result = serverless.run_scan(require_durable_state=True)
                tg.send(f"Scan complete: {result.get('evaluated', 0)} evaluated, {result.get('alerted', 0)} alerted.", chat_id=chat_id)

            elif cmd in ("/schedule", "/on"):
                result = serverless.ensure_qstash_schedule()
                if not result.get("configured"):
                    bot.log.warning("Could not enable schedule: %s", result.get("reason", "unknown"))
                    tg.send(f"Pulse could not start: {result.get('reason', 'unknown error')}", chat_id=chat_id)
                else:
                    tg.send(f"Pulse scanner ON. Schedule {result.get('schedule_id')} verified.", chat_id=chat_id)

            elif cmd == "/off":
                result = serverless.disable_qstash_schedule()
                if result.get("disabled"):
                    tg.send("Pulse scanner OFF. Future scheduled scans are disabled.", chat_id=chat_id)
                else:
                    tg.send(f"Pulse could not stop: {result.get('reason', 'unknown error')}", chat_id=chat_id)

            elif cmd == "/status":
                tg.send(serverless.pulse_status_text(), chat_id=chat_id)

            elif cmd == "/performance":
                tg.send(serverless.today_performance_text(), chat_id=chat_id)

            else:
                tg.send("Commands: /on /off /status /scan /performance", chat_id=chat_id)

            self._reply(200, {"ok": True})
        except Exception as exc:
            bot.log.exception("telegram webhook error: %s", exc)
            # Alert-only mode: operational errors stay in Vercel logs.
            self._reply(500, {"ok": False, "error": str(exc)[:300]})

