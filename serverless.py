"""Serverless runtime helpers for the Solana memecoin scanner."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import quote

import requests
import redis as redis_lib

import bot

REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").strip() or os.getenv("KV_REST_API_URL", "").strip()
REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip() or os.getenv("KV_REST_API_TOKEN", "").strip()
STANDARD_REDIS_URL = os.getenv("REDIS_URL", "").strip()
_redis_client = None
QSTASH_TOKEN = os.getenv("QSTASH_TOKEN", "").strip()
QSTASH_BASE_URL = os.getenv("QSTASH_URL", "").strip().rstrip("/") or "https://qstash.upstash.io"
SCANNER_SECRET = os.getenv("SCANNER_SECRET", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
SCAN_CRON = os.getenv("SCAN_CRON", "*/2 * * * *").strip()
ENRICH_LIMIT = max(1, min(20, int(os.getenv("MAX_TOKENS_TO_ENRICH", "10"))))

STATE_KEY = "sol-meme:alert-state"
CHAT_KEY = "sol-meme:chat-id"
STATS_KEY = "sol-meme:last-stats"
LOCK_KEY = "sol-meme:scan-lock"
PERFORMANCE_KEY = "sol-meme:performance"
PERFORMANCE_REFRESH_KEY = "sol-meme:performance:last-refresh"
PERFORMANCE_REFRESH_SECONDS = max(120, int(os.getenv("PERFORMANCE_REFRESH_SECONDS", "300")))
REPORT_TZ_OFFSET_HOURS = int(os.getenv("REPORT_TZ_OFFSET_HOURS", "1"))


def has_redis() -> bool:
    return bool(STANDARD_REDIS_URL or (REDIS_REST_URL and REDIS_REST_TOKEN))


def _standard_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.Redis.from_url(
            STANDARD_REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=8,
            health_check_interval=30,
        )
    return _redis_client


def redis_command(*parts: Any) -> Any:
    if STANDARD_REDIS_URL:
        return _standard_redis().execute_command(*parts)

    if not (REDIS_REST_URL and REDIS_REST_TOKEN):
        raise RuntimeError("Redis is not configured")

    r = requests.post(
        REDIS_REST_URL,
        headers={"Authorization": f"Bearer {REDIS_REST_TOKEN}", "Content-Type": "application/json"},
        json=list(parts),
        timeout=10,
    )
    r.raise_for_status()
    payload = r.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload.get("result") if isinstance(payload, dict) else payload


def redis_ok(value: Any) -> bool:
    """Normalize Redis SET success across redis-py (True) and REST ("OK")."""
    return value is True or (isinstance(value, str) and value.upper() == "OK")


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
        return redis_ok(redis_command("SET", LOCK_KEY, str(int(time.time())), "EX", 240, "NX"))
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
    backend = "Redis ✅" if has_redis() else "stateless ⚠️"
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

        # Use sendMessage so Telegram can render the Pump/Dex web preview.
        sent_at = tg.send(bot.format_alert(token))
        if sent_at is None:
            bot.log.warning("Alert send failed for %s; not adding to performance tracking", token.mint)
            continue

        # Performance starts at the Telegram send time, not discovery/scan time.
        entry = bot.Token(
            mint=token.mint,
            name=token.name,
            symbol=token.symbol,
            source="telegram-entry",
        )
        bot.dexscreener_enrich(entry)
        if entry.price_usd <= 0 and entry.usd_mcap <= 0:
            entry.price_usd = token.price_usd
            entry.usd_mcap = token.usd_mcap

        track_alert(entry, float(sent_at))
        state[token.mint] = float(sent_at)
        stats["alerted"] += 1
        time.sleep(0.2)

    save_alert_state(state)
    save_last_stats(stats)
    return stats


def run_scan(*, require_durable_state: bool = True) -> dict[str, int]:
    if not bot.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    if require_durable_state and not has_redis():
        raise RuntimeError("Automatic scanning requires Redis")

    chat_id = get_chat_id()
    if not chat_id:
        raise RuntimeError("No Telegram alert chat registered. Set TELEGRAM_CHAT_ID or send /start after Redis is connected.")

    if not acquire_scan_lock():
        return {"seen": 0, "filtered": 0, "scored": 0, "alerted": 0, "skipped": 1}

    try:
        tg = bot.Telegram(bot.TELEGRAM_BOT_TOKEN, chat_id)
        refresh_performance()
        # Strict alert-only mode: daily performance is tracked internally but never pushed to Telegram.
        state = load_alert_state()
        return scan_once(tg, state)
    finally:
        release_scan_lock()


def trigger_qstash_scan() -> bool:
    base = production_base_url()
    if not QSTASH_TOKEN or not base or not SCANNER_SECRET:
        return False

    destination = f"{base}/api/scan"
    url = f"{QSTASH_BASE_URL}/v2/publish/{destination}"
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


def ensure_qstash_schedule() -> dict[str, Any]:
    """Create or update the recurring QStash scan schedule."""
    base = production_base_url()
    if not QSTASH_TOKEN:
        return {"configured": False, "reason": "QSTASH_TOKEN missing"}
    if not has_redis():
        return {"configured": False, "reason": "Redis missing"}
    if not SCANNER_SECRET:
        return {"configured": False, "reason": "SCANNER_SECRET missing"}
    if not base:
        return {"configured": False, "reason": "Production URL unavailable"}

    destination = f"{base}/api/scan"
    schedule_id = os.getenv("QSTASH_SCHEDULE_ID", "solana-memecoin-scanner").strip()
    url = f"{QSTASH_BASE_URL}/v2/schedules/{destination}"
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {QSTASH_TOKEN}",
            "Content-Type": "application/json",
            "Upstash-Cron": SCAN_CRON,
            "Upstash-Schedule-Id": schedule_id,
            "Upstash-Method": "POST",
            "Upstash-Retries": "1",
            "Upstash-Timeout": "120s",
            "Upstash-Forward-X-Scanner-Secret": SCANNER_SECRET,
        },
        json={"source": "qstash-schedule"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return {
        "configured": True,
        "schedule_id": data.get("scheduleId") or schedule_id,
        "cron": SCAN_CRON,
        "destination": destination,
    }


def claim_telegram_update(update_id: Any, ttl_seconds: int = 86400) -> bool:
    """Return True only for the first delivery of a Telegram update id."""
    if update_id in (None, "") or not has_redis():
        return True
    key = f"sol-meme:tg-update:{update_id}"
    try:
        return redis_ok(redis_command("SET", key, "1", "EX", int(ttl_seconds), "NX"))
    except Exception as exc:
        bot.log.warning("Telegram update dedupe failed: %s", exc)
        return True


def _local_dt(ts: float | None = None):
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=REPORT_TZ_OFFSET_HOURS))
    return datetime.fromtimestamp(ts or time.time(), tz=tz)


def local_day_key(ts: float | None = None) -> str:
    return _local_dt(ts).strftime("%Y-%m-%d")


def load_performance() -> dict[str, Any]:
    data = load_json(PERFORMANCE_KEY, {})
    return data if isinstance(data, dict) else {}


def save_performance(data: dict[str, Any]) -> bool:
    return save_json(PERFORMANCE_KEY, data, ttl_seconds=86400 * 45)


def track_alert(token: bot.Token, alerted_at: float | None = None) -> None:
    if not has_redis():
        return
    alerted_at = float(alerted_at or time.time())
    day = local_day_key(alerted_at)
    data = load_performance()
    days = data.setdefault("days", {})
    bucket = days.setdefault(day, {"reported": False, "alerts": {}})
    alerts = bucket.setdefault("alerts", {})
    if token.mint in alerts:
        return

    entry_mcap = float(token.usd_mcap or 0)
    entry_price = float(token.price_usd or 0)
    alerts[token.mint] = {
        "mint": token.mint,
        "symbol": token.symbol or "?",
        "name": token.name or token.symbol or "?",
        "alerted_at": int(alerted_at),
        "entry_mcap": entry_mcap,
        "entry_price": entry_price,
        "peak_mcap": entry_mcap,
        "peak_price": entry_price,
        "peak_multiple": 1.0,
        "last_mcap": entry_mcap,
        "last_price": entry_price,
        "last_checked_at": int(alerted_at),
    }
    save_performance(data)


def _record_multiple(rec: dict[str, Any], current_mcap: float, current_price: float) -> float:
    entry_price = float(rec.get("entry_price") or 0)
    entry_mcap = float(rec.get("entry_mcap") or 0)
    multiple = 0.0
    if entry_price > 0 and current_price > 0:
        multiple = current_price / entry_price
    elif entry_mcap > 0 and current_mcap > 0:
        multiple = current_mcap / entry_mcap
    return multiple


def refresh_performance(force: bool = False) -> dict[str, int]:
    stats = {"tracked": 0, "checked": 0, "two_x": 0}
    if not has_redis():
        return stats

    now = time.time()
    if not force:
        try:
            last = float(redis_command("GET", PERFORMANCE_REFRESH_KEY) or 0)
            if now - last < PERFORMANCE_REFRESH_SECONDS:
                data = load_performance()
                today = ((data.get("days") or {}).get(local_day_key()) or {}).get("alerts") or {}
                stats["tracked"] = len(today)
                stats["two_x"] = sum(1 for r in today.values() if float(r.get("peak_multiple") or 0) >= 2)
                return stats
        except Exception:
            pass

    data = load_performance()
    today_bucket = ((data.get("days") or {}).get(local_day_key()) or {})
    alerts = today_bucket.get("alerts") or {}
    stats["tracked"] = len(alerts)

    for mint, rec in list(alerts.items()):
        try:
            probe = bot.Token(
                mint=mint,
                name=str(rec.get("name") or "?"),
                symbol=str(rec.get("symbol") or "?"),
                source="performance",
            )
            bot.dexscreener_enrich(probe)
            current_mcap = float(probe.usd_mcap or 0)
            current_price = float(probe.price_usd or 0)
            if current_mcap <= 0 and current_price <= 0:
                continue
            rec["last_mcap"] = current_mcap
            rec["last_price"] = current_price
            rec["last_checked_at"] = int(now)
            rec["peak_mcap"] = max(float(rec.get("peak_mcap") or 0), current_mcap)
            rec["peak_price"] = max(float(rec.get("peak_price") or 0), current_price)
            multiple = _record_multiple(rec, current_mcap, current_price)
            rec["peak_multiple"] = max(float(rec.get("peak_multiple") or 1), multiple or 0)
            stats["checked"] += 1
        except Exception as exc:
            bot.log.debug("performance refresh failed for %s: %s", mint, exc)

    stats["two_x"] = sum(1 for r in alerts.values() if float(r.get("peak_multiple") or 0) >= 2)
    save_performance(data)
    try:
        redis_command("SET", PERFORMANCE_REFRESH_KEY, str(int(now)), "EX", 86400)
    except Exception:
        pass
    return stats


def format_performance_report(day: str, *, interim: bool = False) -> str:
    data = load_performance()
    bucket = ((data.get("days") or {}).get(day) or {})
    alerts = list((bucket.get("alerts") or {}).values())
    alerts.sort(key=lambda r: float(r.get("peak_multiple") or 0), reverse=True)
    winners = [r for r in alerts if float(r.get("peak_multiple") or 0) >= 2]

    title = "📈 PULSE Performance — today" if interim else f"🏁 PULSE Daily Results — {day}"
    lines = [
        title,
        f"Alerts tracked: <b>{len(alerts)}</b>",
        f"Reached 2x+: <b>{len(winners)}</b>",
    ]
    if not winners:
        lines.append("No tracked alert reached 2x yet." if interim else "No tracked alert reached 2x during the day.")
        return "\n".join(lines)

    lines.append("")
    for i, rec in enumerate(winners[:20], start=1):
        mult = float(rec.get("peak_multiple") or 0)
        entry = float(rec.get("entry_mcap") or 0)
        peak = float(rec.get("peak_mcap") or 0)
        symbol = str(rec.get("symbol") or "?")
        mint = str(rec.get("mint") or "")
        if entry > 0 and peak > 0:
            move = f"{bot.fmt_usd(entry)} → {bot.fmt_usd(peak)}"
        else:
            move = "price-tracked"
        lines.append(
            f"{i}. <b>{symbol}</b> — <b>{mult:.2f}x</b> peak · {move}\n"
            f'<a href="https://dexscreener.com/solana/{mint}">DexScreener</a>'
        )
    return "\n".join(lines)


def send_due_daily_reports(tg: bot.Telegram) -> int:
    if not has_redis():
        return 0
    data = load_performance()
    days = data.get("days") or {}
    today = local_day_key()
    sent = 0
    for day in sorted(days):
        bucket = days.get(day) or {}
        if day >= today or bucket.get("reported"):
            continue
        tg.send(format_performance_report(day))
        bucket["reported"] = True
        bucket["reported_at"] = int(time.time())
        sent += 1
    if sent:
        save_performance(data)
    return sent


def today_performance_text() -> str:
    refresh_performance(force=True)
    return format_performance_report(local_day_key(), interim=True)
