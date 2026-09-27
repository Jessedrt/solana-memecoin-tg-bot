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

            elif cmd == "/scan":
                serverless.run_scan(require_durable_state=True)

            elif cmd == "/performance":
                tg.send(serverless.today_performance_text(), chat_id=chat_id)

            elif cmd == "/status":
                tg.send(serverless.scanner_status_text(len(serverless.load_alert_state()), serverless.load_last_stats()), chat_id=chat_id)

            elif cmd == "/off":
                serverless.set_scanner_enabled(False)
                tg.send("🔴 Pulse auto scanner marked OFF.", chat_id=chat_id)

            elif cmd in ("/schedule", "/on"):
                result = serverless.ensure_qstash_schedule()
                if not result.get("configured"):
                    bot.log.warning("Could not enable schedule: %s", result.get("reason", "unknown"))
                else:
                    serverless.set_scanner_enabled(True)
                    tg.send(f"🟢 Pulse auto scanner ON · {result.get('cron', 'scheduled')}", chat_id=chat_id)

            else:
                # Strict alert-only mode: all other Telegram commands are ignored silently.
                bot.log.info("Ignored Telegram command in strict alert-only mode: %s", cmd)

            self._reply(200, {"ok": True})
        except Exception as exc:
            bot.log.exception("telegram webhook error: %s", exc)
            # Alert-only mode: operational errors stay in Vercel logs.
            self._reply(500, {"ok": False, "error": str(exc)[:300]})
