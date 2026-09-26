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
            if cmd in ("/start", "/help"):
                persisted = serverless.register_chat_id(chat_id)
                tg.send(bot.HELP, chat_id=chat_id)
                if bot.TELEGRAM_CHAT_ID:
                    note = "Alerts are locked to the TELEGRAM_CHAT_ID configured in Vercel."
                elif persisted:
                    note = "This chat is now registered for scanner alerts."
                else:
                    note = "Automatic alerts need TELEGRAM_CHAT_ID or Upstash Redis so this chat can be remembered."
                tg.send(note, chat_id=chat_id)

            elif cmd == "/status":
                tg.send(
                    serverless.scanner_status_text(
                        len(serverless.load_alert_state()),
                        serverless.load_last_stats(),
                    ),
                    chat_id=chat_id,
                )

            elif cmd == "/schedule":
                result = serverless.ensure_qstash_schedule()
                if result.get("configured"):
                    tg.send(
                        "Automatic scanning enabled ✅\n"
                        f"Schedule: <code>{result.get('cron')}</code>\n"
                        f"ID: <code>{result.get('schedule_id')}</code>",
                        chat_id=chat_id,
                    )
                else:
                    tg.send(
                        f"Could not enable schedule: <code>{result.get('reason', 'unknown')}</code>",
                        chat_id=chat_id,
                    )

            elif cmd == "/scan":
                tg.send("Scan queued…", chat_id=chat_id)
                if not serverless.trigger_qstash_scan():
                    # Manual scans can run inline even before Redis/QStash is connected.
                    stats = serverless.scan_once(tg, serverless.load_alert_state())
                    if stats.get("alerted", 0) == 0:
                        tg.send(
                            "No high-potential hits this pass.\n"
                            f"Seen {stats.get('seen', 0)} · passed {stats.get('filtered', 0)} · scored {stats.get('scored', 0)}",
                            chat_id=chat_id,
                        )
            else:
                tg.send("Use /start, /scan, /status, /schedule or /help.", chat_id=chat_id)

            self._reply(200, {"ok": True})
        except Exception as exc:
            bot.log.exception("telegram webhook error: %s", exc)
            try:
                tg.send(f"Scanner error: <code>{str(exc)[:250]}</code>", chat_id=chat_id)
            except Exception:
                pass
            self._reply(500, {"ok": False, "error": str(exc)[:300]})
