#!/usr/bin/env python3
"""Solana memecoin potential scanner to Telegram.

Sources (no paid keys required):
  - Pump.fun frontend API
  - RugCheck new-token feed
  - DexScreener token lookup

Discovery + scoring only. Not financial advice. Most Pump.fun tokens go to zero.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sol-scanner")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
PUMP_API = "https://frontend-api-v3.pump.fun"
DS_API = "https://api.dexscreener.com"
GECKO_API = "https://api.geckoterminal.com/api/v2"
RUGCHECK_NEW = "https://api.rugcheck.xyz/v1/stats/new_tokens"
RUGCHECK_REPORT = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
STATE_FILE = Path("alerted.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "30"))
MAX_AGE_MINUTES = int(os.getenv("MAX_AGE_MINUTES", "90"))
MIN_MCAP_USD = float(os.getenv("MIN_MCAP_USD", "4000"))
MAX_MCAP_USD = float(os.getenv("MAX_MCAP_USD", "350000"))
MIN_REPLIES = int(os.getenv("MIN_REPLIES", "2"))
MIN_SCORE = int(os.getenv("MIN_SCORE", "55"))
REQUIRE_STILL_ON_CURVE = os.getenv("REQUIRE_STILL_ON_CURVE", "true").lower() == "true"
REQUIRE_SOCIALS = os.getenv("REQUIRE_SOCIALS", "false").lower() == "true"
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "180"))
GECKO_NEW_POOL_PAGES = max(1, min(3, int(os.getenv("GECKO_NEW_POOL_PAGES", "2"))))
GRADUATION_SOL = 85.0


@dataclass
class Token:
    mint: str
    name: str
    symbol: str
    source: str
    usd_mcap: float = 0.0
    price_usd: float = 0.0
    replies: int = 0
    created_ms: int = 0
    complete: bool = False
    twitter: str = ""
    telegram: str = ""
    website: str = ""
    image: str = ""
    description: str = ""
    virtual_sol: float = 0.0
    live: bool = False
    king_hill: bool = False
    volume_h1: float = 0.0
    liquidity_usd: float = 0.0
    price_change_h1: float = 0.0
    buys_h1: int = 0
    sells_h1: int = 0
    pair_url: str = ""
    rug_score: int | None = None
    reasons: list[str] = field(default_factory=list)
    score: int = 0

    @property
    def age_min(self) -> float:
        if not self.created_ms:
            return 9999.0
        return max(0.0, (time.time() * 1000 - self.created_ms) / 60000)

    @property
    def curve_pct(self) -> float:
        if self.complete:
            return 100.0
        if self.virtual_sol <= 0:
            return 0.0
        return min(99.0, (self.virtual_sol / GRADUATION_SOL) * 100.0)


class Telegram:
    def __init__(self, token: str, chat_id: str = "") -> None:
        self.token = token
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{token}"
        self.offset = 0

    def send(self, text: str, chat_id: str | None = None, image: str | None = None) -> None:
        cid = chat_id or self.chat_id
        if not cid:
            log.warning("No chat id yet. Open the bot and send /start")
            return
        try:
            if image:
                r = requests.post(
                    f"{self.base}/sendPhoto",
                    json={"chat_id": cid, "photo": image, "caption": text[:1024], "parse_mode": "HTML"},
                    timeout=20,
                )
                if r.ok:
                    return
            r = requests.post(
                f"{self.base}/sendMessage",
                json={
                    "chat_id": cid,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=20,
            )
            if not r.ok:
                log.warning("Telegram send status %s %s", r.status_code, r.text[:200])
        except Exception as exc:
            log.error("Telegram send failed: %s", exc)

    def poll_commands(self) -> list[dict[str, Any]]:
        try:
            r = requests.get(
                f"{self.base}/getUpdates",
                params={"timeout": 0, "offset": self.offset},
                timeout=15,
            )
            data = r.json()
        except Exception as exc:
            log.debug("getUpdates failed: %s", exc)
            return []
        out: list[dict[str, Any]] = []
        for upd in data.get("result", []):
            self.offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("edited_message") or {}
            text = (msg.get("text") or "").strip()
            chat = msg.get("chat") or {}
            if text and chat.get("id") is not None:
                out.append({"chat_id": str(chat["id"]), "text": text, "from": chat})
        return out


def http_get(url: str, timeout: int = 15) -> Any:
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def pump_list(sort: str, limit: int = 30) -> list[dict[str, Any]]:
    url = f"{PUMP_API}/coins?offset=0&limit={limit}&sort={sort}&order=DESC&includeNsfw=false"
    data = http_get(url)
    return data if isinstance(data, list) else []


def rugcheck_new() -> list[dict[str, Any]]:
    try:
        data = http_get(RUGCHECK_NEW)
        return data if isinstance(data, list) else []
    except Exception as exc:
        log.debug("rugcheck new failed: %s", exc)
        return []


def gecko_new_pools() -> list[dict[str, Any]]:
    """Fetch newly created Solana pools from GeckoTerminal's public API."""
    rows: list[dict[str, Any]] = []
    for page in range(1, GECKO_NEW_POOL_PAGES + 1):
        try:
            data = http_get(
                f"{GECKO_API}/networks/solana/new_pools?page={page}",
                timeout=12,
            )
            page_rows = data.get("data") if isinstance(data, dict) else []
            if isinstance(page_rows, list):
                rows.extend(page_rows)
        except Exception as exc:
            log.warning("geckoterminal new pools page %s failed: %s", page, exc)
            break
    return rows


def rugcheck_score(mint: str) -> int | None:
    try:
        data = http_get(RUGCHECK_REPORT.format(mint=mint), timeout=10)
        if isinstance(data, dict) and "score_normalised" in data:
            return int(data["score_normalised"])
        if isinstance(data, dict) and "score" in data:
            return int(data["score"])
    except Exception:
        return None
    return None


def dexscreener_enrich(token: Token) -> None:
    try:
        data = http_get(f"{DS_API}/latest/dex/tokens/{token.mint}", timeout=10)
    except Exception:
        return
    pairs = data.get("pairs") or []
    sol = [p for p in pairs if p.get("chainId") == "solana"]
    if not sol:
        return
    pair = max(sol, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
    token.liquidity_usd = float((pair.get("liquidity") or {}).get("usd") or 0)
    token.price_usd = float(pair.get("priceUsd") or 0)
    vol = pair.get("volume") or {}
    token.volume_h1 = float(vol.get("h1") or 0)
    ch = pair.get("priceChange") or {}
    token.price_change_h1 = float(ch.get("h1") or 0)
    tx = (pair.get("txns") or {}).get("h1") or {}
    token.buys_h1 = int(tx.get("buys") or 0)
    token.sells_h1 = int(tx.get("sells") or 0)
    token.pair_url = pair.get("url") or ""
    mc = pair.get("marketCap") or pair.get("fdv")
    if mc and token.usd_mcap <= 0:
        token.usd_mcap = float(mc)


def from_pump(raw: dict[str, Any], source: str) -> Token:
    virt = raw.get("virtual_sol_reserves") or raw.get("virtual_quote_reserves") or 0
    try:
        virt_sol = float(virt) / 1e9 if float(virt) > 1000 else float(virt or 0)
    except (TypeError, ValueError):
        virt_sol = 0.0
    created = raw.get("created_timestamp") or 0
    try:
        created = int(created)
    except (TypeError, ValueError):
        created = 0
    return Token(
        mint=str(raw.get("mint") or ""),
        name=str(raw.get("name") or "?"),
        symbol=str(raw.get("symbol") or "?"),
        source=source,
        usd_mcap=float(raw.get("usd_market_cap") or raw.get("market_cap_usd") or 0),
        replies=int(raw.get("reply_count") or 0),
        created_ms=created,
        complete=bool(raw.get("complete")),
        twitter=str(raw.get("twitter") or ""),
        telegram=str(raw.get("telegram") or ""),
        website=str(raw.get("website") or ""),
        image=str(raw.get("image_uri") or ""),
        description=str(raw.get("description") or "")[:240],
        virtual_sol=virt_sol,
        live=bool(raw.get("is_currently_live")),
        king_hill=bool(raw.get("king_of_the_hill_timestamp")),
    )


def from_rugcheck(raw: dict[str, Any]) -> Token:
    created = raw.get("createAt") or ""
    created_ms = 0
    if created:
        try:
            dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            created_ms = int(dt.timestamp() * 1000)
        except ValueError:
            created_ms = int(time.time() * 1000)
    return Token(
        mint=str(raw.get("mint") or ""),
        name=str(raw.get("symbol") or "?"),
        symbol=str(raw.get("symbol") or "?"),
        source="rugcheck-new",
        created_ms=created_ms,
    )


def from_gecko_pool(raw: dict[str, Any]) -> Token:
    attrs = raw.get("attributes") or {}
    rel = raw.get("relationships") or {}
    base_ref = ((rel.get("base_token") or {}).get("data") or {}).get("id") or ""
    mint = str(base_ref)
    if mint.startswith("solana_"):
        mint = mint[len("solana_"):]

    display_name = str(attrs.get("name") or "?")
    base_name = display_name.split("/", 1)[0].strip() or "?"

    created_ms = 0
    created = attrs.get("pool_created_at") or ""
    if created:
        try:
            dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            created_ms = int(dt.timestamp() * 1000)
        except (TypeError, ValueError):
            pass

    def num(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    tx = attrs.get("transactions") or {}
    h1_tx = tx.get("h1") or {}
    volume = attrs.get("volume_usd") or {}
    change = attrs.get("price_change_percentage") or {}

    return Token(
        mint=mint,
        name=base_name,
        symbol=base_name,
        source="gecko-new",
        usd_mcap=num(attrs.get("market_cap_usd") or attrs.get("fdv_usd")),
        price_usd=num(attrs.get("base_token_price_usd")),
        created_ms=created_ms,
        volume_h1=num(volume.get("h1")),
        liquidity_usd=num(attrs.get("reserve_in_usd")),
        price_change_h1=num(change.get("h1")),
        buys_h1=int(num(h1_tx.get("buys"))),
        sells_h1=int(num(h1_tx.get("sells"))),
        pair_url=(
            f"https://www.geckoterminal.com/solana/pools/{attrs.get('address')}"
            if attrs.get("address")
            else ""
        ),
    )


def score_token(t: Token) -> int:
    reasons: list[str] = []
    pts = 0
    if 3 <= t.age_min <= 20:
        pts += 18
        reasons.append("fresh 3-20m")
    elif 20 < t.age_min <= 60:
        pts += 14
        reasons.append("under 1h")
    elif t.age_min < 3:
        pts += 8
        reasons.append("just launched")
    elif t.age_min <= MAX_AGE_MINUTES:
        pts += 6
    if 8000 <= t.usd_mcap <= 80000:
        pts += 20
        reasons.append("mcap sweet spot")
    elif 4000 <= t.usd_mcap < 8000:
        pts += 12
        reasons.append("early mcap")
    elif 80000 < t.usd_mcap <= 200000:
        pts += 10
        reasons.append("already moving")
    elif t.usd_mcap > 200000:
        pts += 4
    if t.replies >= 40:
        pts += 16
        reasons.append(f"{t.replies} replies")
    elif t.replies >= 10:
        pts += 12
        reasons.append(f"{t.replies} replies")
    elif t.replies >= MIN_REPLIES:
        pts += 6
    if t.twitter:
        pts += 8
        reasons.append("has X")
    if t.telegram:
        pts += 5
        reasons.append("has TG")
    if t.live:
        pts += 10
        reasons.append("live stream")
    if t.king_hill and not t.complete:
        pts += 10
        reasons.append("king of the hill")
    if 55 <= t.curve_pct < 100:
        pts += 14
        reasons.append(f"curve {t.curve_pct:.0f}%")
    elif 25 <= t.curve_pct < 55:
        pts += 7
        reasons.append(f"curve {t.curve_pct:.0f}%")
    if t.volume_h1 >= 20000:
        pts += 12
        reasons.append(f"${t.volume_h1:,.0f} 1h vol")
    elif t.volume_h1 >= 5000:
        pts += 7
        reasons.append(f"${t.volume_h1:,.0f} 1h vol")
    if t.liquidity_usd >= 15000:
        pts += 8
        reasons.append(f"${t.liquidity_usd:,.0f} liq")
    if t.price_change_h1 >= 40:
        pts += 8
        reasons.append(f"+{t.price_change_h1:.0f}% 1h")
    if t.buys_h1 and t.sells_h1:
        ratio = t.buys_h1 / max(t.sells_h1, 1)
        if ratio >= 1.4:
            pts += 6
            reasons.append(f"buys>sells {ratio:.1f}x")
    if t.rug_score is not None:
        if t.rug_score <= 20:
            pts += 6
            reasons.append(f"rugcheck {t.rug_score}")
        elif t.rug_score >= 60:
            pts -= 20
            reasons.append(f"HIGH RISK rugcheck {t.rug_score}")
    t.reasons = reasons
    t.score = max(0, min(100, pts))
    return t.score


def passes_hard_filters(t: Token) -> bool:
    if not t.mint:
        return False
    if t.age_min > MAX_AGE_MINUTES:
        return False
    if REQUIRE_STILL_ON_CURVE and t.complete:
        return False
    if t.usd_mcap and not (MIN_MCAP_USD <= t.usd_mcap <= MAX_MCAP_USD):
        if t.source != "rugcheck-new":
            return False
    if t.source.startswith("pump") and t.replies < MIN_REPLIES and t.age_min > 8:
        return False
    if REQUIRE_SOCIALS and not (t.twitter or t.telegram):
        return False
    return True


def load_state() -> dict[str, float]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict[str, float]) -> None:
    cutoff = time.time() - ALERT_COOLDOWN_MINUTES * 60 * 4
    slim = {k: v for k, v in state.items() if v >= cutoff}
    STATE_FILE.write_text(json.dumps(slim))


def fmt_usd(n: float) -> str:
    if n >= 1_000_000:
        return f"${n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"${n/1_000:.1f}k"
    return f"${n:.0f}"


def format_alert(t: Token) -> str:
    age = f"{t.age_min:.0f}m" if t.age_min < 120 else f"{t.age_min/60:.1f}h"
    ratio = (t.buys_h1 / max(t.sells_h1, 1)) if t.buys_h1 else 0
    rug = str(t.rug_score) if t.rug_score is not None else "n/a"

    reasons = ", ".join(t.reasons[:8]) or "passed scanner filters"

    socials = []
    if t.twitter:
        socials.append(f'<a href="{t.twitter}">X</a>')
    if t.telegram:
        socials.append(f'<a href="{t.telegram}">Telegram</a>')
    if t.website:
        socials.append(f'<a href="{t.website}">Website</a>')
    social_text = " · ".join(socials) if socials else "None detected"

    market_links = (
        f'<a href="https://dexscreener.com/solana/{t.mint}">DexScreener</a> · '
        f'<a href="https://gmgn.ai/sol/token/{t.mint}">GMGN</a> · '
        f'<a href="https://birdeye.so/token/{t.mint}?chain=solana">Birdeye</a> · '
        f'<a href="https://solscan.io/token/{t.mint}">Solscan</a>'
    )

    quick_buy = (
        f'<a href="https://t.me/GMGN_sol_bot?start=i_xGrok_{t.mint}">GMGN bot</a> · '
        f'<a href="https://t.me/BloomSolana_bot?start=ca_{t.mint}">Bloom</a> · '
        f'<a href="https://t.me/bonkbot_bot?start=ref_ca_{t.mint}">BonkBot</a>'
    )

    desc = ""
    if t.description:
        safe = (
            t.description.replace("<", "").replace(">", "")
            .replace("&", "and").replace("\n", " ")
        )
        desc = f"\n📝 <b>About</b>\n<i>{safe[:180]}</i>\n"

    return (
        f"🔥 <b>{t.symbol}</b> — {t.name}\n"
        f"Score <b>{t.score}/100</b> · {t.source} · age {age}\n\n"

        f"📊 <b>Market</b>\n"
        f"MCAP: <b>{fmt_usd(t.usd_mcap)}</b>\n"
        f"Liquidity: <b>{fmt_usd(t.liquidity_usd)}</b>\n"
        f"1h Volume: <b>{fmt_usd(t.volume_h1)}</b>\n"
        f"1h Change: <b>{t.price_change_h1:+.0f}%</b>\n\n"

        f"⚡ <b>Momentum</b>\n"
        f"Buys: {t.buys_h1} · Sells: {t.sells_h1}"
        + (f" · B/S <b>{ratio:.1f}x</b>\n" if ratio else "\n")
        + f"Replies: {t.replies}\n"
        f"Curve: {t.curve_pct:.0f}%\n\n"

        f"🛡 <b>Safety</b>\n"
        f"RugCheck Risk: <b>{rug}/100</b> " + ("🟢" if t.rug_score is not None and t.rug_score <= 20 else "🟡" if t.rug_score is not None and t.rug_score < 60 else "🔴" if t.rug_score is not None else "⚪️") + "\n"
        f"Still on curve: {'Yes' if not t.complete else 'No'}\n"
        f"Socials: {social_text}\n\n"

        f"🧠 <b>Why PULSE alerted</b>\n"
        f"{reasons}\n"
        f"{desc}\n"

        f"📋 <b>Contract</b>\n"
        f"<code>{t.mint}</code>\n\n"

        f"🔎 <b>Research</b>\n"
        f"{market_links}\n\n"

        f"⚙️ <b>Quick access</b>\n"
        f"{quick_buy}"
    )

HELP = (
    "Solana memecoin scanner is running.\n\n"
    "Commands:\n"
    "/start — register this chat for alerts\n"
    "/on — enable/refresh automatic scanning\n"
    "/scan — force a scan now and show results\n"
    "/status — filters + last stats\n"
    "/performance — today's alert performance\n"
    "/help — this message\n\n"
    "I watch Pump.fun + RugCheck + DexScreener and ping you when a coin "
    "looks like it has early traction (mcap, replies, curve, volume).\n\n"
    "Not financial advice. Most memecoins die."
)


def collect_candidates() -> list[Token]:
    found: dict[str, Token] = {}

    # Primary source: public, keyless Solana new-pool discovery.
    gecko_rows = gecko_new_pools()
    log.info("fetched %s GeckoTerminal new pools", len(gecko_rows))
    for raw in gecko_rows:
        tok = from_gecko_pool(raw)
        if tok.mint and tok.mint not in found:
            found[tok.mint] = tok

    # Secondary source: RugCheck's recently detected Solana tokens.
    rug_rows = rugcheck_new()
    log.info("fetched %s RugCheck new tokens", len(rug_rows))
    for raw in rug_rows:
        tok = from_rugcheck(raw)
        if tok.mint and tok.mint not in found:
            found[tok.mint] = tok

    return list(found.values())

def scan_once(tg: Telegram, state: dict[str, float]) -> dict[str, int]:
    stats = {"seen": 0, "filtered": 0, "scored": 0, "alerted": 0}
    tokens = collect_candidates()
    stats["seen"] = len(tokens)
    now = time.time()
    cooldown = ALERT_COOLDOWN_MINUTES * 60
    survivors: list[Token] = []
    for t in tokens:
        if not passes_hard_filters(t):
            continue
        if now - state.get(t.mint, 0) < cooldown:
            continue
        survivors.append(t)
    stats["filtered"] = len(survivors)
    survivors.sort(key=lambda x: (x.replies, x.usd_mcap), reverse=True)
    for t in survivors[:18]:
        dexscreener_enrich(t)
        if t.source.startswith("pump") or t.usd_mcap >= MIN_MCAP_USD:
            t.rug_score = rugcheck_score(t.mint)
        score_token(t)
        stats["scored"] += 1
        if t.score < MIN_SCORE:
            continue
        if t.source == "rugcheck-new" and t.usd_mcap < MIN_MCAP_USD and t.score < 70:
            continue
        tg.send(format_alert(t), image=t.image or None)
        state[t.mint] = now
        stats["alerted"] += 1
        log.info("ALERT %s %s score=%s mcap=%s", t.symbol, t.mint, t.score, t.usd_mcap)
        time.sleep(0.4)
    save_state(state)
    return stats


def handle_command(tg: Telegram, chat_id: str, text: str, state: dict[str, float], last_stats: dict[str, int]) -> None:
    cmd = text.split()[0].split("@")[0].lower()
    if cmd in ("/start", "/help"):
        if not TELEGRAM_CHAT_ID:
            tg.chat_id = chat_id
        tg.send(HELP, chat_id=chat_id)
        tg.send(
            f"This chat id is <code>{chat_id}</code>\n"
            f"Put it in TELEGRAM_CHAT_ID if you want alerts locked to you only.",
            chat_id=chat_id,
        )
    elif cmd == "/status":
        tg.send(
            "Filters\n"
            f"• age ≤ {MAX_AGE_MINUTES}m\n"
            f"• mcap {fmt_usd(MIN_MCAP_USD)}–{fmt_usd(MAX_MCAP_USD)}\n"
            f"• min replies {MIN_REPLIES}\n"
            f"• min score {MIN_SCORE}\n"
            f"• still on curve: {REQUIRE_STILL_ON_CURVE}\n"
            f"• scan every {SCAN_INTERVAL}s\n"
            f"Last scan: seen {last_stats.get('seen', 0)}, "
            f"passed {last_stats.get('filtered', 0)}, "
            f"alerted {last_stats.get('alerted', 0)}\n"
            f"Known alerts stored: {len(state)}",
            chat_id=chat_id,
        )
    elif cmd == "/scan":
        tg.send("Scanning now…", chat_id=chat_id)
        stats = scan_once(tg, state)
        last_stats.update(stats)
        if stats["alerted"] == 0:
            tg.send(
                f"No high-potential hits this pass.\n"
                f"Seen {stats['seen']} · passed filters {stats['filtered']} · scored {stats['scored']}",
                chat_id=chat_id,
            )


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "Missing TELEGRAM_BOT_TOKEN.\n"
            "1) Open Telegram → @BotFather → /newbot\n"
            "2) Copy the token into a .env file (see .env.example)"
        )
    tg = Telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    state = load_state()
    last_stats: dict[str, int] = {}
    log.info("scanner started interval=%ss min_score=%s", SCAN_INTERVAL, MIN_SCORE)
    if TELEGRAM_CHAT_ID:
        tg.send(
            f"Scanner online at {datetime.now(timezone.utc).strftime('%H:%M UTC')}.\n"
            f"Watching Pump.fun / RugCheck / DexScreener.\n"
            f"Send /status or /scan."
        )
    last_scan = 0.0
    while True:
        try:
            for msg in tg.poll_commands():
                cid = msg["chat_id"]
                if TELEGRAM_CHAT_ID and cid != TELEGRAM_CHAT_ID:
                    continue
                if not TELEGRAM_CHAT_ID:
                    tg.chat_id = cid
                handle_command(tg, cid, msg["text"], state, last_stats)
            now = time.time()
            if now - last_scan >= SCAN_INTERVAL:
                last_stats.update(scan_once(tg, state))
                last_scan = now
                log.info("scan stats %s", last_stats)
        except KeyboardInterrupt:
            log.info("stopped")
            return
        except Exception as exc:
            log.exception("loop error: %s", exc)
            time.sleep(5)
        time.sleep(1)


if __name__ == "__main__":
    main()
