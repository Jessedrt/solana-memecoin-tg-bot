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
import html
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
SOLANA_TRACKER_API = "https://data.solanatracker.io"
RUGCHECK_NEW = "https://api.rugcheck.xyz/v1/stats/new_tokens"
RUGCHECK_REPORT = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
RUGCHECK_FULL_REPORT = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
STATE_FILE = Path("alerted.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "30"))
MAX_AGE_MINUTES = int(os.getenv("MAX_AGE_MINUTES", "90"))
MIN_MCAP_USD = float(os.getenv("MIN_MCAP_USD", "4000"))
MAX_MCAP_USD = float(os.getenv("MAX_MCAP_USD", "350000"))
MIN_REPLIES = int(os.getenv("MIN_REPLIES", "2"))
MIN_SCORE = int(os.getenv("MIN_SCORE", "55"))
PRE_PUMP_MIN_SCORE = int(os.getenv("PRE_PUMP_MIN_SCORE", "68"))
SOLANA_TRACKER_API_KEY = os.getenv("SOLANA_TRACKER_API_KEY", "").strip()
REQUIRE_DEEP_DD = os.getenv("REQUIRE_DEEP_DD", "true").lower() == "true"
MAX_BUNDLER_PCT = float(os.getenv("MAX_BUNDLER_PCT", "15"))
MAX_CHEAP_UNSOLD_TOP10_COUNT = int(os.getenv("MAX_CHEAP_UNSOLD_TOP10_COUNT", "3"))
MAX_CHEAP_UNSOLD_TOP10_PCT = float(os.getenv("MAX_CHEAP_UNSOLD_TOP10_PCT", "10"))
CHEAP_ENTRY_MCAP_USD = float(os.getenv("CHEAP_ENTRY_MCAP_USD", "10000"))
SERIAL_DEV_LAUNCHES_24H = int(os.getenv("SERIAL_DEV_LAUNCHES_24H", "4"))
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
    real_sol: float = 0.0
    virtual_token_raw: float = 0.0
    real_token_raw: float = 0.0
    pump_checked: bool = False
    live: bool = False
    king_hill: bool = False
    volume_h1: float = 0.0
    volume_m5: float = 0.0
    liquidity_usd: float = 0.0
    price_change_h1: float = 0.0
    price_change_m5: float = 0.0
    buys_h1: int = 0
    sells_h1: int = 0
    buys_m5: int = 0
    sells_m5: int = 0
    pair_url: str = ""
    dex_id: str = ""
    rug_score: int | None = None
    rug_full_checked: bool = False
    rugged: bool = False
    mint_authority_active: bool = False
    freeze_authority_active: bool = False
    top10_pct: float = 0.0
    largest_holder_pct: float = 0.0
    insider_pct: float = 0.0
    lp_locked_pct: float | None = None
    structural_risks: list[str] = field(default_factory=list)
    deep_dd_checked: bool = False
    deployer_wallet: str = ""
    dev_launch_count: int = 0
    dev_launches_24h: int = 0
    dev_survivor_count: int = 0
    dev_dead_count: int = 0
    bundler_pct: float = 0.0
    tracker_insider_pct: float = 0.0
    tracker_sniper_pct: float = 0.0
    tracker_top10_pct: float = 0.0
    tracker_dev_pct: float = 0.0
    tracker_rugged: bool = False
    tracker_risk_score: float = 0.0
    tracker_danger_risks: list[str] = field(default_factory=list)
    cheap_unsold_top10_count: int = 0
    cheap_unsold_top10_pct: float = 0.0
    top10_entry_mcap_min: float = 0.0
    top10_entry_mcap_max: float = 0.0
    entry_within_top10_range: bool = False
    deep_dd_reasons: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    score: int = 0
    early_score: int = 0
    early_reasons: list[str] = field(default_factory=list)

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

    def send(self, text: str, chat_id: str | None = None, image: str | None = None) -> int | None:
        """Send a Telegram message and return Telegram's message timestamp on success."""
        cid = chat_id or self.chat_id
        if not cid:
            log.warning("No chat id yet. Open the bot and send /start")
            return None
        try:
            if image:
                r = requests.post(
                    f"{self.base}/sendPhoto",
                    json={"chat_id": cid, "photo": image, "caption": text[:1024], "parse_mode": "HTML"},
                    timeout=20,
                )
                if r.ok:
                    payload = r.json()
                    return int(((payload.get("result") or {}).get("date")) or time.time())
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
            if r.ok:
                payload = r.json()
                return int(((payload.get("result") or {}).get("date")) or time.time())
            log.warning("Telegram send status %s %s", r.status_code, r.text[:200])
        except Exception as exc:
            log.error("Telegram send failed: %s", exc)
        return None

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


def is_pump_token(token: Token) -> bool:
    return (
        token.source.startswith("pump")
        or "pump" in (token.dex_id or "").lower()
        or token.mint.lower().endswith("pump")
    )


def pump_enrich(token: Token) -> bool:
    """Load Pump's own bonding-curve state for a known mint."""
    if not is_pump_token(token):
        return False
    try:
        raw = http_get(f"{PUMP_API}/coins-v2/{token.mint}", timeout=10)
        if isinstance(raw, dict) and isinstance(raw.get("coin"), dict):
            raw = raw["coin"]
        if not isinstance(raw, dict):
            return False
        fresh = from_pump(raw, token.source or "pump")
        token.pump_checked = True
        token.complete = fresh.complete
        token.virtual_sol = fresh.virtual_sol
        token.real_sol = fresh.real_sol
        token.virtual_token_raw = fresh.virtual_token_raw
        token.real_token_raw = fresh.real_token_raw
        if fresh.usd_mcap > 0:
            token.usd_mcap = fresh.usd_mcap
        if fresh.created_ms:
            token.created_ms = fresh.created_ms
        token.twitter = token.twitter or fresh.twitter
        token.telegram = token.telegram or fresh.telegram
        token.website = token.website or fresh.website
        token.image = token.image or fresh.image
        token.description = token.description or fresh.description
        if token.name in ("", "?"):
            token.name = fresh.name
        if token.symbol in ("", "?"):
            token.symbol = fresh.symbol
        return True
    except Exception as exc:
        log.debug("pump coin lookup failed for %s: %s", token.mint, exc)
        return False


def dex_exit_estimate(liquidity_usd: float, notional_usd: float) -> tuple[float, float]:
    """Approximate constant-product sell proceeds using half of total LP as quote reserve."""
    if liquidity_usd <= 0 or notional_usd <= 0:
        return 0.0, 100.0
    quote_reserve = liquidity_usd / 2.0
    proceeds = notional_usd / (1.0 + (notional_usd / max(quote_reserve, 1e-9)))
    impact = max(0.0, (1.0 - proceeds / notional_usd) * 100.0)
    return proceeds, impact


def pump_exit_estimate(token: Token, notional_usd: float) -> tuple[float, float]:
    """Estimate a Pump bonding-curve sell from its real + virtual reserves."""
    if (
        not token.pump_checked
        or token.complete
        or token.price_usd <= 0
        or token.virtual_sol <= 0
        or token.virtual_token_raw <= 0
        or token.real_sol <= 0
    ):
        return 0.0, 100.0

    # Pump tokens use 6 decimals. Infer SOL/USD from the curve spot price
    # and the independently observed USD token price.
    token_price_sol = (token.virtual_sol / token.virtual_token_raw) * 1_000_000
    if token_price_sol <= 0:
        return 0.0, 100.0
    sol_usd = token.price_usd / token_price_sol
    if sol_usd <= 0:
        return 0.0, 100.0

    token_amount_raw = (notional_usd / token.price_usd) * 1_000_000
    v_sol = token.virtual_sol
    v_tok = token.virtual_token_raw
    k = v_sol * v_tok
    new_v_sol = k / (v_tok + token_amount_raw)
    sol_out = max(0.0, v_sol - new_v_sol)

    # A sell cannot receive more SOL than is actually present on the curve.
    sol_out = min(sol_out, token.real_sol)
    proceeds = sol_out * sol_usd * 0.9875  # conservative fee allowance
    impact = max(0.0, (1.0 - proceeds / notional_usd) * 100.0)
    return proceeds, impact


def exit_estimates(token: Token) -> list[tuple[int, float, float]]:
    rows: list[tuple[int, float, float]] = []
    for size in (1000, 10000, 100000):
        if is_pump_token(token) and not token.complete:
            proceeds, impact = pump_exit_estimate(token, float(size))
        else:
            proceeds, impact = dex_exit_estimate(token.liquidity_usd, float(size))
        rows.append((size, proceeds, impact))
    return rows


def has_exit_capacity(token: Token) -> bool:
    """Require a meaningful $1K exit before emitting an Early Pump alert."""
    rows = exit_estimates(token)
    if not rows:
        return False
    _, proceeds, _ = rows[0]
    return proceeds >= 800.0


def tracker_get(path: str, params: dict[str, Any] | None = None) -> Any:
    if not SOLANA_TRACKER_API_KEY:
        raise RuntimeError("SOLANA_TRACKER_API_KEY is missing")
    r = requests.get(
        f"{SOLANA_TRACKER_API}{path}",
        params=params or {},
        headers={"x-api-key": SOLANA_TRACKER_API_KEY, "Accept": "application/json", "User-Agent": UA},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def solana_tracker_due_diligence(token: Token) -> dict[str, Any]:
    """Dev history, bundles, insiders and top-holder cost basis."""
    if not SOLANA_TRACKER_API_KEY:
        return {"checked": False, "reason": "SOLANA_TRACKER_API_KEY missing"}
    try:
        info = tracker_get(f"/tokens/{token.mint}")
        pools = info.get("pools") or [] if isinstance(info, dict) else []
        risk = info.get("risk") or {} if isinstance(info, dict) else {}

        deployer = ""
        for pool in pools if isinstance(pools, list) else []:
            if not isinstance(pool, dict):
                continue
            deployer = str(pool.get("deployer") or "")
            if not deployer:
                creation = pool.get("creation") or {}
                if isinstance(creation, dict):
                    deployer = str(creation.get("creator") or "")
            if deployer:
                break

        bundlers = tracker_get(f"/tokens/{token.mint}/bundlers")
        bundler_pct = float(bundlers.get("percentage") or 0) if isinstance(bundlers, dict) else 0.0

        insider_pct = 0.0
        sniper_pct = 0.0
        tracker_top10 = 0.0
        tracker_dev = 0.0
        tracker_rugged = False
        tracker_risk_score = 0.0
        tracker_danger_risks: list[str] = []
        if isinstance(risk, dict):
            insiders = risk.get("insiders") or {}
            snipers = risk.get("snipers") or {}
            if isinstance(insiders, dict):
                insider_pct = float(insiders.get("totalPercentage") or insiders.get("percentage") or 0)
            if isinstance(snipers, dict):
                sniper_pct = float(snipers.get("totalPercentage") or snipers.get("percentage") or 0)
            tracker_top10 = float(risk.get("top10") or 0)
            dev_risk = risk.get("dev") or {}
            if isinstance(dev_risk, dict):
                tracker_dev = float(dev_risk.get("percentage") or 0)
            tracker_rugged = bool(risk.get("rugged"))
            tracker_risk_score = float(risk.get("score") or 0)
            for item in risk.get("risks") or []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("level") or "").lower() == "danger":
                    name = str(item.get("name") or item.get("description") or "").strip()
                    if name:
                        tracker_danger_risks.append(name)

        holders = tracker_get(f"/tokens/{token.mint}/holders", {"enrich": "all"})
        accounts = holders.get("accounts") if isinstance(holders, dict) else []
        accounts = accounts if isinstance(accounts, list) else []

        implied_supply = token.usd_mcap / token.price_usd if token.usd_mcap > 0 and token.price_usd > 0 else 0.0
        entry_mcaps: list[float] = []
        cheap_count = 0
        cheap_pct = 0.0
        investors = 0

        for row in accounts:
            if not isinstance(row, dict):
                continue
            identity = row.get("identity") or {}
            identity_text = " ".join(
                [str(identity.get("type") or "")] +
                [str(x) for x in (identity.get("tags") or [])]
            ).lower() if isinstance(identity, dict) else ""
            if "pool" in identity_text or "exchange" in identity_text:
                continue
            pnl = row.get("pnl") or {}
            tp = pnl.get("token") if isinstance(pnl, dict) else {}
            tp = tp if isinstance(tp, dict) else {}
            avg_cost = float(tp.get("avgCost") or 0)
            if avg_cost <= 0 or implied_supply <= 0:
                continue
            investors += 1
            entry_mc = avg_cost * implied_supply
            entry_mcaps.append(entry_mc)
            sells = int(tp.get("sells") or 0)
            total_sold = float(tp.get("totalSold") or 0)
            pct = float(row.get("percentage") or 0)
            if entry_mc < CHEAP_ENTRY_MCAP_USD and sells == 0 and total_sold <= 0:
                cheap_count += 1
                cheap_pct += pct
            if investors >= 10:
                break

        entry_min = min(entry_mcaps) if entry_mcaps else 0.0
        entry_max = max(entry_mcaps) if entry_mcaps else 0.0
        entry_in_range = bool(entry_min and entry_max and entry_min <= token.usd_mcap <= entry_max)

        dev_count = dev_24h = dev_live = dev_dead = 0
        if deployer:
            dev = tracker_get(f"/deployer/{deployer}", {"limit": 50})
            rows = dev.get("tokens") if isinstance(dev, dict) else []
            rows = rows if isinstance(rows, list) else []
            prior = [x for x in rows if isinstance(x, dict) and str(x.get("mint") or "") != token.mint]
            dev_count = len(prior)
            cutoff = time.time() * 1000 - 86400000
            for item in prior:
                created = float(item.get("createdAt") or 0)
                if created and created < 10000000000:
                    created *= 1000
                if created >= cutoff:
                    dev_24h += 1
                mc = float(item.get("marketCapUsd") or 0)
                liq = float(item.get("liquidityUsd") or 0)
                if mc >= 20000 and liq >= 5000:
                    dev_live += 1
                if mc < 5000 or liq < 1000:
                    dev_dead += 1

        reasons: list[str] = []
        if bundler_pct > MAX_BUNDLER_PCT:
            reasons.append(f"bundlers {bundler_pct:.1f}%")
        if cheap_count > MAX_CHEAP_UNSOLD_TOP10_COUNT or cheap_pct > MAX_CHEAP_UNSOLD_TOP10_PCT:
            reasons.append(f"cheap unsold top holders: {cheap_count} / {cheap_pct:.1f}%")
        if entry_mcaps and not entry_in_range:
            reasons.append(f"entry outside top-holder range {fmt_usd(entry_min)}-{fmt_usd(entry_max)}")
        if dev_24h >= SERIAL_DEV_LAUNCHES_24H:
            reasons.append(f"serial dev {dev_24h} launches/24h")
        if dev_count >= 3 and dev_dead / max(dev_count, 1) >= 0.70:
            reasons.append(f"poor dev history {dev_dead}/{dev_count} dead")

        return {
            "checked": bool(deployer and investors >= 3),
            "deployer_wallet": deployer,
            "dev_launch_count": dev_count,
            "dev_launches_24h": dev_24h,
            "dev_survivor_count": dev_live,
            "dev_dead_count": dev_dead,
            "bundler_pct": bundler_pct,
            "tracker_insider_pct": insider_pct,
            "tracker_sniper_pct": sniper_pct,
            "tracker_top10_pct": tracker_top10,
            "tracker_dev_pct": tracker_dev,
            "tracker_rugged": tracker_rugged,
            "tracker_risk_score": tracker_risk_score,
            "tracker_danger_risks": tracker_danger_risks,
            "cheap_unsold_top10_count": cheap_count,
            "cheap_unsold_top10_pct": cheap_pct,
            "top10_entry_mcap_min": entry_min,
            "top10_entry_mcap_max": entry_max,
            "entry_within_top10_range": entry_in_range,
            "holder_entry_count": investors,
            "reasons": reasons,
        }
    except Exception as exc:
        log.warning("Solana Tracker DD failed for %s: %s", token.mint, exc)
        return {"checked": False, "reason": str(exc)[:160]}


def apply_deep_dd(token: Token, data: dict[str, Any]) -> None:
    token.deep_dd_checked = bool(data.get("checked"))
    token.deployer_wallet = str(data.get("deployer_wallet") or "")
    token.dev_launch_count = int(data.get("dev_launch_count") or 0)
    token.dev_launches_24h = int(data.get("dev_launches_24h") or 0)
    token.dev_survivor_count = int(data.get("dev_survivor_count") or 0)
    token.dev_dead_count = int(data.get("dev_dead_count") or 0)
    token.bundler_pct = float(data.get("bundler_pct") or 0)
    token.tracker_insider_pct = float(data.get("tracker_insider_pct") or 0)
    token.tracker_sniper_pct = float(data.get("tracker_sniper_pct") or 0)
    token.tracker_top10_pct = float(data.get("tracker_top10_pct") or 0)
    token.tracker_dev_pct = float(data.get("tracker_dev_pct") or 0)
    token.tracker_rugged = bool(data.get("tracker_rugged"))
    token.tracker_risk_score = float(data.get("tracker_risk_score") or 0)
    token.tracker_danger_risks = [str(x) for x in (data.get("tracker_danger_risks") or [])]
    token.cheap_unsold_top10_count = int(data.get("cheap_unsold_top10_count") or 0)
    token.cheap_unsold_top10_pct = float(data.get("cheap_unsold_top10_pct") or 0)
    token.top10_entry_mcap_min = float(data.get("top10_entry_mcap_min") or 0)
    token.top10_entry_mcap_max = float(data.get("top10_entry_mcap_max") or 0)
    token.entry_within_top10_range = bool(data.get("entry_within_top10_range"))
    token.deep_dd_reasons = [str(x) for x in (data.get("reasons") or [])]


def deep_due_diligence_ok(token: Token) -> bool:
    if not token.deep_dd_checked:
        return not REQUIRE_DEEP_DD
    if token.tracker_rugged:
        return False
    if token.tracker_top10_pct > 45:
        return False
    if token.tracker_dev_pct > 5:
        return False
    if token.tracker_insider_pct > 10:
        return False
    if token.tracker_sniper_pct > 15:
        return False
    if token.bundler_pct > MAX_BUNDLER_PCT:
        return False
    if token.tracker_danger_risks:
        return False
    if token.cheap_unsold_top10_count > MAX_CHEAP_UNSOLD_TOP10_COUNT:
        return False
    if token.cheap_unsold_top10_pct > MAX_CHEAP_UNSOLD_TOP10_PCT:
        return False
    if token.top10_entry_mcap_min > 0 and not token.entry_within_top10_range:
        return False
    if token.dev_launches_24h >= SERIAL_DEV_LAUNCHES_24H:
        return False
    if token.dev_launch_count >= 3 and token.dev_dead_count / max(token.dev_launch_count, 1) >= 0.70:
        return False
    return True


def tracker_structural_fallback_ok(token: Token) -> bool:
    """Fallback when RugCheck has not indexed a fresh mint yet."""
    if not token.deep_dd_checked:
        return False
    return deep_due_diligence_ok(token)


def rugcheck_new() -> list[dict[str, Any]]:
    try:
        data = http_get(RUGCHECK_NEW)
        return data if isinstance(data, list) else []
    except Exception as exc:
        log.debug("rugcheck new failed: %s", exc)
        return []


def dexscreener_latest(endpoint: str) -> list[dict[str, Any]]:
    """Fetch broad Solana discovery rows from a public DexScreener feed."""
    try:
        data = http_get(f"{DS_API}/{endpoint}", timeout=12)
    except Exception as exc:
        log.warning("dexscreener %s discovery failed: %s", endpoint, exc)
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [
        row for row in data
        if isinstance(row, dict) and str(row.get("chainId") or "").lower() == "solana"
    ]


def from_dexscreener_discovery(raw: dict[str, Any], source: str) -> Token:
    links = raw.get("links") or []
    twitter = ""
    telegram = ""
    website = ""
    for link in links:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "")
        kind = str(link.get("type") or link.get("label") or "").lower()
        if not url:
            continue
        if "twitter" in kind or "x.com" in url or "twitter.com" in url:
            twitter = twitter or url
        elif "telegram" in kind or "t.me/" in url:
            telegram = telegram or url
        elif not website:
            website = url

    return Token(
        mint=str(raw.get("tokenAddress") or ""),
        name=str(raw.get("description") or "?")[:80] or "?",
        symbol="?",
        source=source,
        twitter=twitter,
        telegram=telegram,
        website=website,
        image=str(raw.get("icon") or ""),
        description=str(raw.get("description") or "")[:240],
        pair_url=str(raw.get("url") or ""),
    )


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


def rugcheck_full_enrich(token: Token) -> bool:
    """Fetch RugCheck's full report and extract structural rug signals."""
    try:
        data = http_get(RUGCHECK_FULL_REPORT.format(mint=token.mint), timeout=12)
    except Exception as exc:
        log.debug("RugCheck full report failed for %s: %s", token.mint, exc)
        return False
    if not isinstance(data, dict):
        return False

    token.rug_full_checked = True
    token.rugged = bool(data.get("rugged"))

    if data.get("score_normalised") is not None:
        try:
            token.rug_score = int(data["score_normalised"])
        except (TypeError, ValueError):
            pass

    tok = data.get("token") or {}
    if isinstance(tok, dict):
        token.mint_authority_active = bool(tok.get("mintAuthority"))
        token.freeze_authority_active = bool(tok.get("freezeAuthority"))

    holders = data.get("topHolders") or []
    pcts: list[float] = []
    insider_pct = 0.0
    if isinstance(holders, list):
        for holder in holders[:10]:
            if not isinstance(holder, dict):
                continue
            try:
                pct = float(holder.get("pct") or 0)
            except (TypeError, ValueError):
                pct = 0.0
            if pct > 0:
                pcts.append(pct)
            if holder.get("insider"):
                insider_pct += pct
    token.top10_pct = sum(pcts)
    token.largest_holder_pct = max(pcts, default=0.0)
    token.insider_pct = insider_pct

    lock_values: list[float] = []
    summary_lock = data.get("lpLockedPct")
    if summary_lock is not None:
        try:
            lock_values.append(float(summary_lock))
        except (TypeError, ValueError):
            pass
    markets = data.get("markets") or []
    if isinstance(markets, list):
        for market in markets:
            if not isinstance(market, dict):
                continue
            lp = market.get("lp") or {}
            if isinstance(lp, dict) and lp.get("lpLockedPct") is not None:
                try:
                    lock_values.append(float(lp.get("lpLockedPct")))
                except (TypeError, ValueError):
                    pass
    token.lp_locked_pct = max(lock_values) if lock_values else None

    risk_names: list[str] = []
    for risk in data.get("risks") or []:
        if not isinstance(risk, dict):
            continue
        name = str(risk.get("name") or "").strip()
        level = str(risk.get("level") or "").lower()
        if name and level in ("danger", "high", "critical", "warn", "warning"):
            risk_names.append(name)
    token.structural_risks = risk_names[:8]
    return True


def structural_safety_ok(token: Token) -> bool:
    """Fail closed on structural token risks that can enable a fast rug."""
    if not token.rug_full_checked:
        return False
    if token.rugged:
        return False
    if token.mint_authority_active or token.freeze_authority_active:
        return False
    if token.largest_holder_pct > 15:
        return False
    if token.top10_pct > 35:
        return False
    if token.insider_pct > 10:
        return False

    # Live Pump bonding curves are governed by curve reserves rather than DEX LP locks.
    if not is_pump_token(token) or token.complete:
        if token.lp_locked_pct is None:
            return False
        if token.lp_locked_pct < 80:
            return False

    danger_words = (
        "low liquidity",
        "single holder",
        "high ownership",
        "top 10 holders high ownership",
        "mint authority",
        "freeze authority",
        "lp unlocked",
        "liquidity not locked",
        "rugged",
    )
    for risk in token.structural_risks:
        low = risk.lower()
        if any(word in low for word in danger_words):
            return False
    return True


def momentum_not_extended(token: Token) -> bool:
    """Reject late/hype-stage moves and visible sell pressure."""
    if token.price_change_h1 >= 250:
        return False
    if token.price_change_m5 >= 80:
        return False
    if token.buys_h1 and token.sells_h1:
        if token.buys_h1 / max(token.sells_h1, 1) < 1.05:
            return False
    return True


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
    token.volume_m5 = float(vol.get("m5") or 0)
    ch = pair.get("priceChange") or {}
    token.price_change_h1 = float(ch.get("h1") or 0)
    token.price_change_m5 = float(ch.get("m5") or 0)
    tx_h1 = (pair.get("txns") or {}).get("h1") or {}
    tx_m5 = (pair.get("txns") or {}).get("m5") or {}
    token.buys_h1 = int(tx_h1.get("buys") or 0)
    token.sells_h1 = int(tx_h1.get("sells") or 0)
    token.buys_m5 = int(tx_m5.get("buys") or 0)
    token.sells_m5 = int(tx_m5.get("sells") or 0)
    token.pair_url = pair.get("url") or token.pair_url or ""
    token.dex_id = str(pair.get("dexId") or token.dex_id or "")

    base = pair.get("baseToken") or {}
    if token.symbol in ("", "?"):
        token.symbol = str(base.get("symbol") or token.symbol or "?")
    if token.name in ("", "?") or token.source.startswith("dexscreener-"):
        token.name = str(base.get("name") or token.name or token.symbol or "?")

    created = pair.get("pairCreatedAt")
    if created and not token.created_ms:
        try:
            token.created_ms = int(created)
        except (TypeError, ValueError):
            pass

    info = pair.get("info") or {}
    if not token.image:
        token.image = str(info.get("imageUrl") or "")
    websites = info.get("websites") or []
    if not token.website and websites:
        first = websites[0] if isinstance(websites[0], dict) else {}
        token.website = str(first.get("url") or "")
    for social in info.get("socials") or []:
        if not isinstance(social, dict):
            continue
        platform = str(social.get("platform") or "").lower()
        handle = str(social.get("handle") or social.get("url") or "")
        if not handle:
            continue
        if platform in ("twitter", "x") and not token.twitter:
            token.twitter = handle if handle.startswith("http") else f"https://x.com/{handle.lstrip('@')}"
        elif platform == "telegram" and not token.telegram:
            token.telegram = handle if handle.startswith("http") else f"https://t.me/{handle.lstrip('@')}"

    mc = pair.get("marketCap") or pair.get("fdv")
    if mc and token.usd_mcap <= 0:
        token.usd_mcap = float(mc)


def from_pump(raw: dict[str, Any], source: str) -> Token:
    virt = raw.get("virtual_sol_reserves") or raw.get("virtual_quote_reserves") or 0
    try:
        virt_sol = float(virt) / 1e9 if float(virt) > 1000 else float(virt or 0)
    except (TypeError, ValueError):
        virt_sol = 0.0
    try:
        real_raw = float(raw.get("real_sol_reserves") or 0)
        real_sol = real_raw / 1e9 if real_raw > 1000 else real_raw
    except (TypeError, ValueError):
        real_sol = 0.0
    try:
        virtual_token_raw = float(raw.get("virtual_token_reserves") or 0)
        real_token_raw = float(raw.get("real_token_reserves") or 0)
    except (TypeError, ValueError):
        virtual_token_raw = 0.0
        real_token_raw = 0.0
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
        real_sol=real_sol,
        virtual_token_raw=virtual_token_raw,
        real_token_raw=real_token_raw,
        pump_checked=bool(raw.get("mint")),
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
        dex_id=str((((rel.get("dex") or {}).get("data") or {}).get("id")) or ""),
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
    if t.price_change_h1 >= 250:
        pts -= 20
        reasons.append(f"already extended +{t.price_change_h1:.0f}% 1h")
    elif t.price_change_h1 >= 40:
        pts += 8
        reasons.append(f"+{t.price_change_h1:.0f}% 1h")
    if t.buys_h1 and t.sells_h1:
        ratio = t.buys_h1 / max(t.sells_h1, 1)
        if ratio >= 1.4:
            pts += 6
            reasons.append(f"buys>sells {ratio:.1f}x")
        elif ratio < 1.0:
            pts -= 8
            reasons.append(f"sell pressure {ratio:.1f}x")
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


def early_pump_score(t: Token, previous: dict[str, Any] | None = None) -> int:
    """Score early acceleration signals before a large price move is already obvious."""
    reasons: list[str] = []
    pts = 0

    # Avoid calling an already-exploded candle "early".
    if t.price_change_m5 >= 80 or t.price_change_h1 >= 250:
        t.early_score = 0
        t.early_reasons = ["already extended"]
        return 0

    if t.age_min <= 10:
        pts += 12
        reasons.append("very fresh")
    elif t.age_min <= 30:
        pts += 10
        reasons.append("fresh")
    elif t.age_min <= 60:
        pts += 5

    if 7000 <= t.usd_mcap <= 60000:
        pts += 14
        reasons.append("early MC")
    elif 60000 < t.usd_mcap <= 150000:
        pts += 7

    if t.liquidity_usd >= 15000:
        pts += 10
        reasons.append("healthy LP")
    elif t.liquidity_usd >= 7000:
        pts += 6

    if t.volume_m5 >= 5000:
        pts += 15
        reasons.append(f"{fmt_usd(t.volume_m5)} 5m vol")
    elif t.volume_m5 >= 1690:
        pts += 10
        reasons.append(f"{fmt_usd(t.volume_m5)} 5m vol")
    elif t.volume_m5 >= 800:
        pts += 5

    if t.buys_m5 >= 20:
        pts += 14
        reasons.append(f"{t.buys_m5} buys/5m")
    elif t.buys_m5 >= 8:
        pts += 9
        reasons.append(f"{t.buys_m5} buys/5m")

    if t.buys_m5:
        ratio = t.buys_m5 / max(t.sells_m5, 1)
        if ratio >= 2.2:
            pts += 14
            reasons.append(f"5m B/S {ratio:.1f}x")
        elif ratio >= 1.5:
            pts += 9
            reasons.append(f"5m B/S {ratio:.1f}x")
        elif ratio < 0.9:
            pts -= 8

    if 2 <= t.price_change_m5 <= 25:
        pts += 10
        reasons.append(f"+{t.price_change_m5:.0f}% 5m early move")
    elif 25 < t.price_change_m5 <= 60:
        pts += 5
        reasons.append(f"+{t.price_change_m5:.0f}% 5m")
    elif t.price_change_m5 < -8:
        pts -= 10

    if t.usd_mcap > 0 and t.volume_m5 / t.usd_mcap >= 0.08:
        pts += 8
        reasons.append("high vol/MC")

    if t.twitter or t.telegram or t.website:
        pts += 4
        reasons.append("socials")

    # The strongest signal is acceleration between scanner observations.
    previous = previous or {}
    prev_vol = float(previous.get("volume_m5") or 0)
    prev_buys = int(previous.get("buys_m5") or 0)
    prev_liq = float(previous.get("liquidity_usd") or 0)
    if prev_vol > 0 and t.volume_m5 >= prev_vol * 1.35:
        pts += 10
        reasons.append("5m volume accelerating")
    if prev_buys > 0 and t.buys_m5 >= max(prev_buys + 3, prev_buys * 1.25):
        pts += 10
        reasons.append("buy velocity accelerating")
    if prev_liq > 0 and t.liquidity_usd >= prev_liq * 1.05:
        pts += 5
        reasons.append("liquidity rising")

    # RugCheck is a gate, not proof of safety.
    if t.rug_score is not None:
        if t.rug_score <= 20:
            pts += 8
            reasons.append("low RugCheck risk")
        elif t.rug_score < 40:
            pts += 3
        elif t.rug_score >= 60:
            pts -= 35
            reasons.append("high RugCheck risk")

    t.early_reasons = reasons
    t.early_score = max(0, min(100, int(pts)))
    return t.early_score


def passes_hard_filters(t: Token) -> bool:
    if not t.mint:
        return False
    if t.created_ms and t.age_min > MAX_AGE_MINUTES:
        return False
    if not t.created_ms and not t.source.startswith("dexscreener-"):
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
    rug = t.rug_score
    rug_text = f"{rug}/100" if rug is not None else "n/a"
    rug_badge = (
        "🟢" if rug is not None and rug <= 20
        else "🟡" if rug is not None and rug <= 40
        else "🔴" if rug is not None
        else "⚪️"
    )

    symbol = html.escape(t.symbol or "?")
    name = html.escape(t.name or t.symbol or "?")
    reasons = html.escape(", ".join(t.reasons[:8]) or "passed scanner filters")

    if t.price_usd > 0:
        if t.price_usd >= 1:
            price_text = f"\${t.price_usd:,.4f}"
        elif t.price_usd >= 0.01:
            price_text = f"\${t.price_usd:.6f}"
        else:
            price_text = f"\${t.price_usd:.10f}".rstrip("0").rstrip(".")
    else:
        price_text = "n/a"

    socials = []
    if t.twitter:
        socials.append(f'<a href="{html.escape(t.twitter, quote=True)}">X</a>')
    if t.telegram:
        socials.append(f'<a href="{html.escape(t.telegram, quote=True)}">TG</a>')
    if t.website:
        socials.append(f'<a href="{html.escape(t.website, quote=True)}">Web</a>')
    social_text = " · ".join(socials) if socials else "None detected"

    pump_like = is_pump_token(t)
    dex = f"https://dexscreener.com/solana/{t.mint}"
    pump = f"https://pump.fun/coin/{t.mint}"
    primary_url = t.pair_url or (pump if pump_like else dex)
    venue = html.escape(t.dex_id or ("Pump" if pump_like else t.source))
    state_text = ("Migrated" if t.complete else "Bonding") if pump_like else "DEX pool"
    gmgn = f"https://gmgn.ai/sol/token/{t.mint}"
    birdeye = f"https://birdeye.so/token/{t.mint}?chain=solana"
    solscan = f"https://solscan.io/token/{t.mint}"

    lines: list[str] = [
        f"🔥 <b>{name} (\${symbol})</b>",
        f'└ <a href="{primary_url}">{venue}</a> │ ⏱ {age} │ Score <b>{t.score}/100</b>',
        "",
        "📊 <b>Stats</b>",
        f"├ USD   <b>{price_text}</b> ({t.price_change_h1:+.0f}% 1H)",
        f"├ MC    <b>{fmt_usd(t.usd_mcap)}</b>",
        f"├ Vol   <b>{fmt_usd(t.volume_h1)}</b>",
        f"├ LP    <b>{fmt_usd(t.liquidity_usd)}</b>",
        f"├ 1H    <b>{t.price_change_h1:+.0f}%</b>  B {t.buys_h1} │ S {t.sells_h1}",
        f"└ B/S   <b>{ratio:.1f}x</b>" if ratio else "└ B/S   n/a",
        "",
        "🔗 <b>Socials</b>",
        f"├ {social_text}",
    ]

    if t.description:
        safe = html.escape(t.description.replace("\n", " ").strip())
        lines.append(f"└ Lore  <i>{safe[:180]}</i>")

    lines.extend([
        "",
        "🔐 <b>Security</b>",
        f"├ Rug   <b>{rug_text}</b> {rug_badge}",
        f"├ Top10 <b>{t.top10_pct:.1f}%</b> · Max <b>{t.largest_holder_pct:.1f}%</b>",
        f"├ LP lock <b>{'n/a' if t.lp_locked_pct is None else f'{t.lp_locked_pct:.0f}%'}</b>",
        f"├ Auth  mint {'ON' if t.mint_authority_active else 'off'} · freeze {'ON' if t.freeze_authority_active else 'off'}",
        f"├ Curve <b>{t.curve_pct:.0f}%</b>",
        f"├ State <b>{state_text}</b>",
        f"└ Src   {html.escape(t.source)}",
        "",
    ])

    if t.early_score >= PRE_PUMP_MIN_SCORE:
        early = html.escape(", ".join(t.early_reasons[:6]) or "early acceleration")
        lines.extend([
            f"🚨 <b>Early Pump</b>  <b>{t.early_score}/100</b>",
            f"└ {early}",
            "",
        ])

    lines.extend([
        "🧬 <b>Deep DD</b>",
        f"├ Bundlers <b>{t.bundler_pct:.1f}%</b> · Insiders <b>{t.tracker_insider_pct:.1f}%</b> · Snipers <b>{t.tracker_sniper_pct:.1f}%</b>",
        f"├ Top10 <b>{t.tracker_top10_pct:.1f}%</b> · Dev hold <b>{t.tracker_dev_pct:.1f}%</b>",
        f"├ Dev <b>{t.dev_launch_count}</b> prior · <b>{t.dev_launches_24h}</b>/24h · survivors <b>{t.dev_survivor_count}</b>",
        f"├ Cheap unsold top10 <b>{t.cheap_unsold_top10_count}</b> · {t.cheap_unsold_top10_pct:.1f}%",
        (
            f"└ Top10 entry MC <b>{fmt_usd(t.top10_entry_mcap_min)}–{fmt_usd(t.top10_entry_mcap_max)}</b> "
            f"{'✅' if t.entry_within_top10_range else '❌'}"
            if t.top10_entry_mcap_min > 0
            else "└ Top10 entry MC <b>unavailable</b>"
        ),
        "",
    ])

    lines.append("💧 <b>Exit check</b>")
    exits = exit_estimates(t)
    for idx, (size, proceeds, impact) in enumerate(exits):
        branch = "└" if idx == len(exits) - 1 else "├"
        lines.append(
            f"{branch} \${size//1000}K → <b>\${proceeds:,.0f}</b> · impact {impact:.0f}%"
        )
    if pump_like and not t.complete:
        lines.append(f"└ Curve real SOL <b>{t.real_sol:.2f}</b>")

    lines.extend([
        "",
        "🧠 <b>Why alerted</b>",
        f"└ {reasons}",
        "",
        "📋 <b>CA</b>",
        f"<code>{html.escape(t.mint)}</code>",
        "",
    ])

    research = []
    if pump_like:
        research.append(f'<a href="{pump}">PF</a>')
    bubbles = f"https://v2.bubblemaps.io/map?address={t.mint}&chain=solana&partnerId=regular"
    research.extend([
        f'<a href="{dex}">DS</a>',
        f'<a href="{bubbles}">Bubbles</a>',
        f'<a href="{gmgn}">GMGN</a>',
        f'<a href="{birdeye}">BE</a>',
        f'<a href="{solscan}">SOL</a>',
    ])
    lines.append(" · ".join(research))
    lines.append(
        "Buy: "
        f'<a href="https://t.me/GMGN_sol_bot?start=i_xGrok_{t.mint}">GMGN</a> · '
        f'<a href="https://t.me/BloomSolana_bot?start=ca_{t.mint}">Bloom</a> · '
        f'<a href="https://t.me/bonkbot_bot?start=ref_ca_{t.mint}">BonkBot</a>'
    )

    return "\n".join(lines)

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

    # Independent DexScreener discovery. These feeds span launchpads/DEXes
    # and reduce reliance on any single new-pool provider.
    ds_profiles = dexscreener_latest("token-profiles/latest/v1")
    log.info("fetched %s DexScreener Solana token profiles", len(ds_profiles))
    for raw in ds_profiles:
        tok = from_dexscreener_discovery(raw, "dexscreener-profile")
        if tok.mint and tok.mint not in found:
            found[tok.mint] = tok

    ds_boosts = dexscreener_latest("token-boosts/latest/v1")
    log.info("fetched %s DexScreener Solana boosted tokens", len(ds_boosts))
    for raw in ds_boosts:
        tok = from_dexscreener_discovery(raw, "dexscreener-boost")
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
