from __future__ import annotations

import time
from typing import Any

from .config import PulseConfig
from .models import (
    CandidateDecision,
    Executability,
    NormalizedToken,
    SafetyReport,
    SafetyStatus,
)


def _check(value: Any, fail: bool, warn: bool = False) -> SafetyStatus:
    if value is None:
        return SafetyStatus.UNKNOWN
    if fail:
        return SafetyStatus.FAIL
    return SafetyStatus.WARN if warn else SafetyStatus.PASS


def hard_rug_gate(t: NormalizedToken, cfg: PulseConfig) -> SafetyReport:
    checks = {
        "mint_authority": _check(t.mint_authority_active, t.mint_authority_active is True),
        "freeze_authority": _check(t.freeze_authority_active, t.freeze_authority_active is True),
        "rugged": _check(t.rugged, t.rugged is True),
        "creator_holding": _check(t.creator_pct, (t.creator_pct or 0) > cfg.max_creator_pct, (t.creator_pct or 0) > cfg.max_creator_pct * .7),
        "related_wallets": _check(t.related_wallet_pct, (t.related_wallet_pct or 0) > cfg.max_related_pct),
        "top10_concentration": _check(t.top10_pct, (t.top10_pct or 0) > cfg.max_top10_pct, (t.top10_pct or 0) > cfg.max_top10_pct * .8),
        "largest_holder": _check(t.largest_holder_pct, (t.largest_holder_pct or 0) > cfg.max_largest_holder_pct),
        "bundled_supply": _check(t.bundled_pct, (t.bundled_pct or 0) > cfg.max_bundle_pct),
        "sniper_supply": _check(t.sniper_pct, (t.sniper_pct or 0) > cfg.max_sniper_pct),
        "wallet_clustering": _check(t.wallet_cluster_score, (t.wallet_cluster_score or 0) >= .8, (t.wallet_cluster_score or 0) >= .55),
        "wash_trading": _check(t.wash_trading_score, (t.wash_trading_score or 0) >= .8, (t.wash_trading_score or 0) >= .55),
        "creator_dumping": _check(t.creator_dumping, t.creator_dumping is True),
        "creator_history": SafetyStatus.FAIL if t.creator_risk == "CRITICAL" else SafetyStatus.WARN if t.creator_risk == "HIGH" else SafetyStatus.PASS if t.creator_risk in ("LOW", "MEDIUM") else SafetyStatus.UNKNOWN,
        "liquidity": _check(t.liquidity_usd, (t.liquidity_usd or 0) < cfg.critical_liquidity, (t.liquidity_usd or 0) < cfg.preferred_liquidity),
    }
    failures = [name for name, result in checks.items() if result == SafetyStatus.FAIL]
    warnings = [name for name, result in checks.items() if result == SafetyStatus.WARN]
    unknowns = [name for name, result in checks.items() if result == SafetyStatus.UNKNOWN]
    status = SafetyStatus.FAIL if failures else SafetyStatus.UNKNOWN if len(unknowns) >= 4 else SafetyStatus.WARN if warnings or unknowns else SafetyStatus.PASS
    return SafetyReport(status, checks, failures, warnings, unknowns)


def _series(t: NormalizedToken, attr: str) -> list[float]:
    return [float(v) for w in t.history if (v := getattr(w, attr)) is not None]


def trend(values: list[float], min_growth: float = .10) -> str:
    if len(values) < 2:
        return "UNKNOWN"
    deltas = [(b - a) / max(abs(a), 1) for a, b in zip(values, values[1:])]
    if all(d >= min_growth for d in deltas):
        return "ACCELERATING"
    if all(d >= -.05 for d in deltas):
        return "STABLE"
    return "DECLINING"


def demand_quality(t: NormalizedToken) -> str:
    if (t.wash_trading_score or 0) >= .8 or (t.wallet_cluster_score or 0) >= .8:
        return "MANIPULATED"
    if (t.wash_trading_score or 0) >= .55 or (t.wallet_cluster_score or 0) >= .55:
        return "SUSPICIOUS"
    buyers = _series(t, "unique_buyers")
    txns = _series(t, "transactions")
    if len(buyers) < 2:
        return "UNKNOWN"
    latest_buyers = buyers[-1]
    latest_txns = txns[-1] if txns else latest_buyers
    independence = latest_buyers / max(latest_txns, 1)
    if trend(buyers) == "ACCELERATING" and independence >= .55:
        return "ORGANIC"
    if independence >= .35:
        return "MOSTLY ORGANIC"
    return "MIXED"


def exit_analysis(liquidity: float | None) -> list[Executability]:
    out: list[Executability] = []
    for amount in (100, 500, 1000):
        if not liquidity or liquidity <= 0:
            out.append(Executability(amount, 0, 100, "UNKNOWN"))
            continue
        quote = liquidity / 2
        proceeds = amount / (1 + amount / max(quote, 1e-9))
        impact = max(0.0, (1 - proceeds / amount) * 100)
        grade = "GOOD" if impact <= 3 else "ACCEPTABLE" if impact <= 8 else "POOR"
        out.append(Executability(amount, round(proceeds, 2), round(impact, 2), grade))
    return out


def _component_scores(t: NormalizedToken, safety: SafetyReport, cfg: PulseConfig) -> dict[str, int]:
    weights = cfg.weights
    safety_ratio = 1.0
    safety_ratio -= .12 * len(safety.warnings)
    safety_ratio -= .10 * len(safety.unknowns)
    wallet_ratio = .2
    quality = demand_quality(t)
    wallet_ratio = {"ORGANIC": 1, "MOSTLY ORGANIC": .8, "MIXED": .5, "SUSPICIOUS": .15, "MANIPULATED": 0, "UNKNOWN": .25}[quality]
    if t.creator_risk == "LOW":
        wallet_ratio = min(1, wallet_ratio + .15)
    elif t.creator_risk in ("HIGH", "CRITICAL"):
        wallet_ratio = max(0, wallet_ratio - .3)

    buyer_trend, volume_trend = trend(_series(t, "unique_buyers")), trend(_series(t, "buy_volume"))
    momentum_ratio = .2 + (.4 if buyer_trend == "ACCELERATING" else 0) + (.3 if volume_trend == "ACCELERATING" else 0)
    if (t.buys_m5 or 0) > (t.sells_m5 or 0) * 1.3:
        momentum_ratio += .1

    liq = t.liquidity_usd or 0
    mc = t.market_cap or 0
    ratio = liq / mc if mc > 0 else 0
    liquidity_ratio = min(1, (.35 if liq >= cfg.preferred_liquidity else .1) + min(.45, ratio * 1.5))
    exits = exit_analysis(t.liquidity_usd)
    if exits[-1].grade == "GOOD":
        liquidity_ratio = min(1, liquidity_ratio + .2)
    elif exits[-1].grade == "POOR":
        liquidity_ratio *= .6

    age = t.age_minutes
    early_ratio = .25
    if age is not None and cfg.min_age_minutes <= age <= 120:
        early_ratio = .9
    elif age is not None and age <= cfg.max_age_minutes:
        early_ratio = .65
    late_move = max(t.price_change_h1 or 0, t.price_change_m5 or 0)
    if late_move > 300:
        early_ratio = 0
    elif late_move > 100:
        early_ratio *= .35

    social_ratio = min(1, max(0, (t.social_score or 0) / 100))
    if t.attention_acceleration is not None:
        social_ratio = max(social_ratio, min(1, t.attention_acceleration))
    return {
        "safety": round(weights["safety"] * max(0, safety_ratio)),
        "wallet": round(weights["wallet"] * wallet_ratio),
        "momentum": round(weights["momentum"] * min(1, momentum_ratio)),
        "liquidity": round(weights["liquidity"] * liquidity_ratio),
        "early": round(weights["early"] * early_ratio),
        "social": round(weights["social"] * social_ratio),
    }


def source_states(t: NormalizedToken, cfg: PulseConfig) -> dict[str, str]:
    now = time.time()
    states = {name: "UNAVAILABLE" for name in ("pumpfun", "gmgn", "dexscreener", "fomo", "solana")}
    for name, obs in t.sources.items():
        states[name] = "STALE" if now - obs.observed_at > cfg.stale_seconds else "CONFIRMED"
    for conflict in t.conflicts:
        source = conflict.split(":", 1)[0]
        if source in states:
            states[source] = "CONFLICTING"
    return states


def evaluate(t: NormalizedToken, cfg: PulseConfig) -> CandidateDecision:
    safety = hard_rug_gate(t, cfg)
    components = _component_scores(t, safety, cfg)
    score = min(100, sum(components.values()))
    quality = demand_quality(t)
    buyer_trend = trend(_series(t, "unique_buyers"))
    volume_trend = trend(_series(t, "buy_volume"))
    momentum = "ACCELERATING" if "ACCELERATING" in (buyer_trend, volume_trend) else "DECLINING" if "DECLINING" in (buyer_trend, volume_trend) else "UNKNOWN"
    liquidity_trend = trend(_series(t, "liquidity"), .05)
    rejected = None
    if safety.status == SafetyStatus.FAIL:
        score, rejected = 0, "critical safety failure: " + ", ".join(safety.failures)
    elif quality == "MANIPULATED":
        score, rejected = 0, "manipulated demand"
    # Critical unknowns can never become high-conviction.
    critical_unknown = any(x in safety.unknowns for x in ("mint_authority", "freeze_authority", "top10_concentration", "creator_history"))
    if critical_unknown:
        score = min(score, 74)
    if t.conflicts:
        score = min(score, 74)
    classification = "REJECTED" if rejected else "HIGH_CONVICTION" if score >= cfg.high_conviction_score else "STRONG_WATCH" if score >= cfg.strong_watch_score else "WATCH" if score >= 65 else "NO_ALERT"
    decision = CandidateDecision(
        mint=t.mint, score=score, classification=classification,
        alert=not rejected and score >= cfg.alert_min_score,
        safety=safety, components=components, demand_quality=quality, momentum=momentum,
        liquidity_trend=liquidity_trend, executability=exit_analysis(t.liquidity_usd),
        target_market_cap=(t.market_cap * 3 if t.market_cap else None), reasoning={},
        source_states=source_states(t, cfg), rejected_reason=rejected,
    )
    return decision

