"""Serverless runtime helpers for the Solana memecoin scanner."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import quote

import requests

import bot

REDIS_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").strip() or os.getenv("KV_REST_API_URL", "").strip()
REDIS_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip() or os.getenv("KV_REST_API_TOKEN", "").strip()
QSTASH_TOKEN = os.getenv("QSTASH_TOKEN", "").strip()
SCANNER_SECRET = os.getenv("SCANNER_SECRET", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
SCAN_CRON = os.getenv("SCAN_CRON", "*/2 * * * *").strip()
ENRICH_LIMIT = max(1, min(20, int(os.getenv("MAX_TOKENS_TO_ENRICH", "10"))))

STATE_KEY = "sol-meme:alert-state"
CHAT_KEY = "sol-meme:chat-id"
STATS_KEY = "sol-meme:last-stats"
LOCK_KEY = "sol-meme:scan-lock"


def has_redis() -> bool:
    return bool(REDIS_URL and REDIS_TOKEN)


def redis_command(*parts: Any) -> Any:
    if not has_redis():
        raise RuntimeError("Upstash Redis is not configured")
    r = requests.post(
        REDIS_URL,
        headers={"Authorization": f"Bearer {REDIS_TOKEN}", "Content-Type": "application/json"},
        json=list(parts),
        timeout=10,
    )
    r.raise_for_status()
    payload = r.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload.get("result") if isinstance(payload, dict) else payload


def load_json(key: str, default: Any) -> Any:
    if not has_redis():
        return default
    try:
        raw = redis_command("GET", key)
        return json.loads(raw) if raw else default
    except Exception as exc:
        bot.log.warning("Redis read failed for %s: %s", key, exc)
        return default


def save_json(key: str, value: Any, ttl_seconds: int | None = None) -> bool:
    if not has_redis():
        return False
    try:
        raw = json.dumps(value, separators=(",", ":"))
        if ttl_seconds:
            redis_command("SET", key, raw, "EX", int(ttl_seconds))
        else:
            redis_command("SET", key, raw)
        return True
    except Exception as exc:
        bot.log.warning("Redis write failed for %s: %s", key, exc)
        return False


def load_alert_state() -> dict[str, float]:
    data = load_json(STATE_KEY, {})
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in data.items():
        try:
            out[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def save_alert_state(state: dict[str, float]) -> bool:
    cutoff = time.time() - bot.ALERT_COOLDOWN_MINUTES * 60 * 4
    slim = {k: v for k, v in state.items() if float(v) >= cutoff}
    ttl = max(86400, bot.ALERT_COOLDOWN_MINUTES * 60 * 5)
    return save_json(STATE_KEY, slim, ttl_seconds=ttl)


def register_chat_id(chat_id: str) -> bool:
    if bot.TELEGRAM_CHAT_ID:
        return True
    if not has_redis():
        return False
    try:
        redis_command("SET", CHAT_KEY, str(chat_id))
        return True
    except Exception as exc:
        bot.log.warning("Could not persist Telegram chat id: %s", exc)
        return False


def get_chat_id() -> str:
    if bot.TELEGRAM_CHAT_ID:
        return bot.TELEGRAM_CHAT_ID
    if not has_redis():
        return ""
    try:
        value = redis_command("GET", CHAT_KEY)
        return str(value or "")
    except Exception as exc:
        bot.log.warning("Could not read Telegram chat id: %s", exc)
        return ""


def load_last_stats() -> dict[str, int]:
    data = load_json(STATS_KEY, {})
    return data if isinstance(data, dict) else {}


def save_last_stats(stats: dict[str, int]) -> bool:
    payload = dict(stats)
    payload["finished_at"] = int(time.time())
    return save_json(STATS_KEY, payload, ttl_seconds=86400 * 7)


def acquire_scan_lock() -> bool:
    if not has_redis():
        return True
    try:
        return redis_command("SET", LOCK_KEY, str(int(time.time())), "EX", 240, "NX") == "OK"
    except Exception as exc:
        bot.log.warning("Could not acquire scan lock: %s", exc)
        return False


def release_scan_lock() -> None:
    if not has_redis():
        return
    try:
        redis_command("DEL", LOCK_KEY)
    except Exception:
        pass


def production_base_url() -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    host = os.getenv("VERCEL_PROJECT_PRODUCTION_URL", "").strip() or os.getenv("VERCEL_URL", "").strip()
    if not host:
        return ""
    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/")
    return f"https://{host.rstrip('/')}"


def scanner_status_text(state_count: int, stats: dict[str, int]) -> str:
    backend = "Upstash Redis ✅" if has_redis() else "stateless ⚠️"
    return (
        "Scanner status\n"
        f"• age ≤ {bot.MAX_AGE_MINUTES}m\n"
        f"• mcap {bot.fmt_usd(bot.MIN_MCAP_USD)}–{bot.fmt_usd(bot.MAX_MCAP_USD)}\n"
        f"• min replies {bot.MIN_REPLIES}\n"
        f"• min score {bot.MIN_SCORE}\n"
        f"• still on curve: {bot.REQUIRE_STILL_ON_CURVE}\n"
        f"• scheduler: {SCAN_CRON}\n"
        f"• state: {backend}\n"
        f"Last scan: seen {stats.get('seen', 0)}, "
        f"passed {stats.get('filtered', 0)}, "
        f"scored {stats.get('scored', 0)}, "
        f"alerted {stats.get('alerted', 0)}\n"
        f"Known cooldown entries: {state_count}"
    )


def scan_once(tg: bot.Telegram, state: dict[str, float]) -> dict[str, int]:
    stats = {"seen": 0, "filtered": 0, "scored": 0, "alerted": 0}
    tokens = bot.collect_candidates()
    stats["seen"] = len(tokens)
    now = time.time()
    cooldown = bot.ALERT_COOLDOWN_MINUTES * 60

    survivors: list[bot.Token] = []
    for token in tokens:
        if not bot.passes_hard_filters(token):
            continue
        if now - state.get(token.mint, 0) < cooldown:
            continue
        survivors.append(token)

    stats["filtered"] = len(survivors)
    survivors.sort(key=lambda x: (x.replies, x.usd_mcap), reverse=True)

    for token in survivors[:ENRICH_LIMIT]:
        bot.dexscreener_enrich(token)
        if token.source.startswith("pump") or token.usd_mcap >= bot.MIN_MCAP_USD:
            token.rug_score = bot.rugcheck_score(token.mint)
        bot.score_token(token)
        stats["scored"] += 1

        if token.score < bot.MIN_SCORE:
            continue
        if token.source == "rugcheck-new" and token.usd_mcap < bot.MIN_MCAP_USD and token.score < 70:
            continue

        tg.send(bot.format_alert(token), image=token.image or None)
        state[token.mint] = now
        stats["alerted"] += 1
        time.sleep(0.2)

    save_alert_state(state)
    save_last_stats(stats)
    return stats


def run_scan(*, require_durable_state: bool = True) -> dict[str, int]:
    if not bot.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    if require_durable_state and not has_redis():
        raise RuntimeError("Automatic scanning requires Upstash Redis")

    chat_id = get_chat_id()
    if not chat_id:
        raise RuntimeError("No Telegram alert chat registered. Set TELEGRAM_CHAT_ID or send /start after Redis is connected.")

    if not acquire_scan_lock():
        return {"seen": 0, "filtered": 0, "scored": 0, "alerted": 0, "skipped": 1}

    try:
        tg = bot.Telegram(bot.TELEGRAM_BOT_TOKEN, chat_id)
        state = load_alert_state()
        return scan_once(tg, state)
    finally:
        release_scan_lock()


def trigger_qstash_scan() -> bool:
    base = production_base_url()
    if not QSTASH_TOKEN or not base or not SCANNER_SECRET:
        return False

    destination = f"{base}/api/scan"
    url = f"https://qstash.upstash.io/v2/publish/{quote(destination, safe='')}"
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {QSTASH_TOKEN}",
            "Content-Type": "application/json",
            "Upstash-Method": "POST",
            "Upstash-Retries": "1",
            "Upstash-Forward-X-Scanner-Secret": SCANNER_SECRET,
        },
        json={"source": "telegram"},
        timeout=12,
    )
    r.raise_for_status()
    return True
