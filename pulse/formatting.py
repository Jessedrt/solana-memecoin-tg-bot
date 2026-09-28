from __future__ import annotations

import html

from .models import CandidateDecision, NormalizedToken


def usd(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def _check_icon(value: str) -> str:
    return {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "UNKNOWN": "?"}.get(value, value)


def _momentum_icon(value: str) -> str:
    return {
        "ACCELERATING": "↗",
        "STABLE": "→",
        "DECLINING": "↘",
        "UNKNOWN": "?",
    }.get(value, "?")


def format_alert(t: NormalizedToken, d: CandidateDecision) -> str:
    """Compact Telegram alert with the useful facts and research links only."""
    label = {
        "HIGH_CONVICTION": "🔥 HIGH",
        "STRONG_WATCH": "🟢 STRONG",
        "WATCH": "🟡 WATCH",
    }.get(d.classification, "🟡 WATCH")

    age = f"{t.age_minutes:.0f}m" if t.age_minutes is not None else "?"
    symbol = html.escape(t.symbol or "?")
    name = html.escape(t.name or t.symbol or "?")
    mint = html.escape(t.mint)
    checks = d.safety.checks

    buys = str(t.buys_m5) if t.buys_m5 is not None else "?"
    sells = str(t.sells_m5) if t.sells_m5 is not None else "?"
    txns = str(t.txns_m5) if t.txns_m5 is not None else "?"
    p5 = f"{t.price_change_m5:+.0f}%" if t.price_change_m5 is not None else "?"
    p1h = f"{t.price_change_h1:+.0f}%" if t.price_change_h1 is not None else "?"

    unknowns = []
    if t.creator_risk == "UNKNOWN":
        unknowns.append("creator")
    if checks["bundled_supply"].value == "UNKNOWN":
        unknowns.append("bundles")
    if checks["sniper_supply"].value == "UNKNOWN":
        unknowns.append("snipers")
    if d.demand_quality == "UNKNOWN":
        unknowns.append("wallet quality")

    dex = t.pair_url or f"https://dexscreener.com/solana/{t.mint}"
    pump = f"https://pump.fun/coin/{t.mint}"
    gmgn = f"https://gmgn.ai/sol/token/{t.mint}"
    solscan = f"https://solscan.io/token/{t.mint}"
    bubbles = f"https://v2.bubblemaps.io/map?address={t.mint}&chain=solana&partnerId=regular"

    links = [
        f'<a href="{html.escape(dex, quote=True)}">Chart</a>',
        f'<a href="{html.escape(pump, quote=True)}">Pump</a>',
        f'<a href="{html.escape(gmgn, quote=True)}">GMGN</a>',
        f'<a href="{html.escape(solscan, quote=True)}">Solscan</a>',
        f'<a href="{html.escape(bubbles, quote=True)}">Bubbles</a>',
    ]

    lines = [
        f"{label} <b>PULSE {d.target_multiple}× CANDIDATE</b>",
        f"<b>{name}</b> ({symbol})",
        f"<b>{d.score}/100</b> · evidence {d.evidence_confidence * 100:.0f}% · {html.escape(t.discovery_lane or 'NO LANE')}",
        f"MC {usd(t.market_cap)} · LP {usd(t.liquidity_usd)} · {age} · Holders {t.holder_count if t.holder_count is not None else '?'}",
        f"Graduated {'YES' if t.graduated is True else '?'} · Trading fees {t.total_trading_fees_sol if t.total_trading_fees_sol is not None else '?'} SOL",
        f"5m {txns} tx · B/S {buys}/{sells} · {p5}",
        f"1h {p1h} · {_momentum_icon(d.momentum)} {d.momentum}",
        (
            "Safety "
            f"M {_check_icon(checks['mint_authority'].value)} · "
            f"F {_check_icon(checks['freeze_authority'].value)} · "
            f"Top10 {_check_icon(checks['top10_concentration'].value)}"
        ),
    ]

    if unknowns:
        lines.append("Unknown: " + ", ".join(unknowns))
    if d.profile == "5x":
        lines.append("Holder/insider proxy; funding clusters unverified. Jupiter quotes ≠ fills.")

    lines.extend([
        f"<code>{mint}</code>",
        " · ".join(links),
        "Score is a ranking signal, not a guarantee.",
    ])
    return "\n".join(lines)[:4096]
