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


def _icon(state: str) -> str:
    return {"CONFIRMED": "✓", "UNAVAILABLE": "UNAVAILABLE", "CONFLICTING": "CONFLICTING", "STALE": "STALE"}.get(state, state)


def format_alert(t: NormalizedToken, d: CandidateDecision) -> str:
    label = {"HIGH_CONVICTION": "🔥 HIGH CONVICTION", "STRONG_WATCH": "🟢 STRONG WATCH"}.get(d.classification, "WATCH")
    age = f"{t.age_minutes:.0f}m" if t.age_minutes is not None else "UNKNOWN"
    buyers = [w.unique_buyers for w in t.history if w.unique_buyers is not None]
    buy_vol = [usd(w.buy_volume) for w in t.history if w.buy_volume is not None]
    why = d.reasoning.get("positives") or []
    risks = d.reasoning.get("risks") or []
    invalidation = d.reasoning.get("invalidation") or []
    checks = d.safety.checks
    lines = [
        "🔥 <b>PULSE EARLY CANDIDATE</b>",
        f"<b>{html.escape(t.symbol)}</b>",
        f"Mint: <code>{html.escape(t.mint)}</code>",
        f"Age: {age} · MC: {usd(t.market_cap)} · Liquidity: {usd(t.liquidity_usd)}",
        f"Graduated: {'YES' if t.graduated is True else 'UNCONFIRMED'} · Total trading fees: {t.total_trading_fees_sol if t.total_trading_fees_sol is not None else 'UNKNOWN'} SOL",
        f"Profile: {d.profile} · Approx. {d.target_multiple}× MC: {usd(d.target_market_cap)}",
        "━━━━━━━━━━━━━━━━",
        f"<b>{d.target_multiple}× CANDIDATE SCORE {d.score}/100</b>", label,
        "━━━━━━━━━━━━━━━━", "<b>MOMENTUM</b>",
        "Unique Buyers: " + (" → ".join(map(str, buyers)) if buyers else "UNKNOWN"),
        "Buy Volume: " + (" → ".join(buy_vol) if buy_vol else "UNKNOWN"),
        f"Momentum: {d.momentum} · Demand: {d.demand_quality}",
        "━━━━━━━━━━━━━━━━", "<b>SAFETY</b>",
        f"Mint Authority: {checks['mint_authority'].value}",
        f"Freeze Authority: {checks['freeze_authority'].value}",
        f"Creator: {html.escape(t.creator_risk)}", f"Top Holders: {checks['top10_concentration'].value}",
        f"Bundles: {checks['bundled_supply'].value} · Snipers: {checks['sniper_supply'].value}",
        "━━━━━━━━━━━━━━━━", "<b>EXECUTABILITY</b>",
    ]
    lines.extend(f"${x.amount}: {x.grade} ({x.impact_pct:.1f}% — {x.source})" for x in d.executability)
    lines.append("Quotes/estimates are not executed trades; network fees excluded.")
    if d.profile == "5x":
        lines.append("Holder/insider concentration proxy only; funding clusters unverified.")
    lines.extend(["━━━━━━━━━━━━━━━━", "<b>SOURCES</b>"])
    lines.extend(f"{name}: {_icon(state)}" for name, state in d.source_states.items())
    lines.extend(["━━━━━━━━━━━━━━━━", "<b>WHY PULSE LIKES IT</b>"])
    lines.extend(html.escape(str(x)) for x in why)
    lines.append("<b>RISKS</b>")
    lines.extend(html.escape(str(x)) for x in risks or ["Young tokens remain highly speculative."])
    lines.append("<b>INVALIDATION</b>")
    lines.extend("• " + html.escape(str(x)) for x in invalidation)
    lines.append(f"{d.target_multiple}× Candidate Score is a ranking signal, not a guaranteed probability.")
    return "\n".join(lines)[:4096]

