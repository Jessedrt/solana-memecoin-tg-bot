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


def _component_scores(
    t: NormalizedToken,
    safety: SafetyReport,
    cfg: PulseConfig,
) -> tuple[dict[str, int], dict[str, float]]:
    """Score only evidence that actually exists.

    Optional/unavailable providers no longer act like confirmed negative
    evidence. Missing evidence reduces coverage/confidence instead. Critical
    safety failures still reject the token before any alert can be sent.
    """
    weights = cfg.weights

    known_safety = [state for state in safety.checks.values() if state != SafetyStatus.UNKNOWN]
    safety_coverage = len(known_safety) / max(len(safety.checks), 1)
    if known_safety:
        safety_ratio = sum(
            1.0 if state == SafetyStatus.PASS else .55 if state == SafetyStatus.WARN else 0.0
            for state in known_safety
        ) / len(known_safety)
    else:
        safety_ratio = 0.0

    quality = demand_quality(t)
    wallet_coverage = 0.0
    wallet_ratio = .0
    if quality != "UNKNOWN":
        wallet_coverage += .75
        wallet_ratio = {
            "ORGANIC": 1.0,
            "MOSTLY ORGANIC": .8,
            "MIXED": .5,
            "SUSPICIOUS": .15,
            "MANIPULATED": 0.0,
        }[quality]
    if t.creator_risk != "UNKNOWN":
        wallet_coverage += .25
        if quality == "UNKNOWN":
            wallet_ratio = .65 if t.creator_risk == "LOW" else .45
        if t.creator_risk == "LOW":
            wallet_ratio = min(1.0, wallet_ratio + .15)
        elif t.creator_risk in ("HIGH", "CRITICAL"):
            wallet_ratio = max(0.0, wallet_ratio - .3)

    tx_trend = trend(_series(t, "transactions"))
    buyer_trend = trend(_series(t, "unique_buyers"))
    buy_volume_trend = trend(_series(t, "buy_volume"))
    momentum_coverage = 0.0
    if len(_series(t, "transactions")) >= 2:
        momentum_coverage += .35
    if t.buys_m5 is not None and t.sells_m5 is not None:
        momentum_coverage += .25
    if t.price_change_m5 is not None:
        momentum_coverage += .20
    if t.price_change_h1 is not None:
        momentum_coverage += .20

    momentum_ratio = .35 if momentum_coverage else 0.0
    if "ACCELERATING" in (buyer_trend, buy_volume_trend):
        momentum_ratio += .25
    elif tx_trend == "ACCELERATING":
        momentum_ratio += .20
    elif tx_trend == "STABLE":
        momentum_ratio += .08
    elif tx_trend == "DECLINING":
        momentum_ratio -= .12

    if t.buys_m5 is not None and t.sells_m5 is not None:
        buy_sell = t.buys_m5 / max(t.sells_m5, 1)
        if buy_sell >= 1.5:
            momentum_ratio += .25
        elif buy_sell >= 1.2:
            momentum_ratio += .18
        elif buy_sell >= 1.0:
            momentum_ratio += .08
        elif buy_sell < .8:
            momentum_ratio -= .15

    if t.price_change_m5 is not None:
        if 0 <= t.price_change_m5 <= 30:
            momentum_ratio += .12
        elif t.price_change_m5 < -10:
            momentum_ratio -= .12
        elif t.price_change_m5 > 60:
            momentum_ratio -= .15
    if t.price_change_h1 is not None:
        if 0 <= t.price_change_h1 <= 90:
            momentum_ratio += .08
        elif t.price_change_h1 > 150:
            momentum_ratio -= .20
    momentum_ratio = min(1.0, max(0.0, momentum_ratio))

    liq = t.liquidity_usd or 0
    mc = t.market_cap or 0
    liquidity_coverage = 0.0
    if t.liquidity_usd is not None:
        liquidity_coverage += .60
    if t.market_cap is not None:
        liquidity_coverage += .20
    if len(_series(t, "liquidity")) >= 2:
        liquidity_coverage += .20
    ratio = liq / mc if mc > 0 else 0
    liquidity_ratio = min(1.0, (.35 if liq >= cfg.preferred_liquidity else .1) + min(.45, ratio * 1.5))
    exits = exit_analysis(t.liquidity_usd)
    if exits[-1].grade == "GOOD":
        liquidity_ratio = min(1.0, liquidity_ratio + .2)
    elif exits[-1].grade == "POOR":
        liquidity_ratio *= .6

    age = t.age_minutes
    early_coverage = 0.0
    if age is not None:
        early_coverage += .60
    if t.price_change_h1 is not None:
        early_coverage += .20
    if t.price_change_m5 is not None:
        early_coverage += .20
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

    social_coverage = 0.0
    social_ratio = 0.0
    if t.social_score is not None:
        social_coverage += .60
        social_ratio = min(1.0, max(0.0, t.social_score / 100))
    if t.attention_acceleration is not None:
        social_coverage += .40
        social_ratio = max(social_ratio, min(1.0, max(0.0, t.attention_acceleration)))

    coverage = {
        "safety": safety_coverage,
        "wallet": min(1.0, wallet_coverage),
        "momentum": min(1.0, momentum_coverage),
        "liquidity": min(1.0, liquidity_coverage),
        "early": min(1.0, early_coverage),
        "social": min(1.0, social_coverage),
    }
    ratios = {
        "safety": max(0.0, safety_ratio),
        "wallet": max(0.0, wallet_ratio),
        "momentum": momentum_ratio,
        "liquidity": max(0.0, liquidity_ratio),
        "early": max(0.0, early_ratio),
        "social": max(0.0, social_ratio),
    }
    components = {
        name: round(weights[name] * coverage[name] * ratios[name])
        for name in weights
    }
    return components, coverage


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
    components, coverage = _component_scores(t, safety, cfg)
    raw_score = sum(components.values())
    available_max = round(sum(cfg.weights[name] * coverage[name] for name in cfg.weights))
    evidence_confidence = min(1.0, max(0.0, available_max / 100))

    # Normalize against evidence we genuinely have, then apply a confidence
    # discount so thin evidence cannot look equivalent to a fully verified
    # candidate. This fixes the prior bug where unavailable optional providers
    # automatically made otherwise clean candidates score in the 20s-40s.
    normalized = (raw_score / available_max * 100) if available_max > 0 else 0.0
    confidence_factor = .78 + .22 * evidence_confidence
    score = min(100, round(normalized * confidence_factor))

    quality = demand_quality(t)
    buyer_trend = trend(_series(t, "unique_buyers"))
    buy_volume_trend = trend(_series(t, "buy_volume"))
    tx_trend = trend(_series(t, "transactions"))
    if "ACCELERATING" in (buyer_trend, buy_volume_trend, tx_trend):
        momentum = "ACCELERATING"
    elif "DECLINING" in (buyer_trend, buy_volume_trend, tx_trend):
        momentum = "DECLINING"
    elif "STABLE" in (buyer_trend, buy_volume_trend, tx_trend):
        momentum = "STABLE"
    else:
        momentum = "UNKNOWN"
    liquidity_trend = trend(_series(t, "liquidity"), .05)

    rejected = None
    if safety.status == SafetyStatus.FAIL:
        score, rejected = 0, "critical safety failure: " + ", ".join(safety.failures)
    elif quality == "MANIPULATED":
        score, rejected = 0, "manipulated demand"

    # Evidence that is too thin can be observed, but must not trigger Telegram.
    if available_max < 50:
        score = min(score, cfg.alert_min_score - 1)

    # Critical unknowns can never become high-conviction. They can still become
    # WATCH when the verified evidence is strong enough; UNKNOWN is not FAIL.
    critical_unknown = any(
        x in safety.unknowns
        for x in ("mint_authority", "freeze_authority", "top10_concentration", "creator_history")
    )
    if critical_unknown:
        score = min(score, 74)
    if t.conflicts:
        score = min(score, 74)

    evidence_pct = evidence_confidence * 100
    market_cap_ok = t.market_cap is not None and cfg.min_market_cap <= t.market_cap <= cfg.max_market_cap
    age_ok = t.age_minutes is not None and cfg.min_age_minutes <= t.age_minutes <= cfg.max_age_minutes
    move_ok = (
        (t.price_change_m5 is None or t.price_change_m5 <= cfg.max_alert_price_change_m5)
        and (t.price_change_h1 is None or t.price_change_h1 <= cfg.max_alert_price_change_h1)
    )
    alert_eligible = (
        not rejected
        and score >= cfg.alert_min_score
        and evidence_pct >= cfg.min_alert_evidence_pct
        and market_cap_ok
        and age_ok
        and move_ok
        and momentum != "DECLINING"
    )

    classification = (
        "REJECTED" if rejected
        else "HIGH_CONVICTION" if alert_eligible and score >= cfg.high_conviction_score
        else "STRONG_WATCH" if alert_eligible and score >= cfg.strong_watch_score
        else "WATCH" if alert_eligible
        else "NO_ALERT"
    )
    decision = CandidateDecision(
        mint=t.mint,
        score=score,
        classification=classification,
        alert=alert_eligible,
        safety=safety,
        components=components,
        demand_quality=quality,
        momentum=momentum,
        liquidity_trend=liquidity_trend,
        executability=exit_analysis(t.liquidity_usd),
        target_market_cap=(t.market_cap * 3 if t.market_cap else None),
        reasoning={},
        source_states=source_states(t, cfg),
        rejected_reason=rejected,
        raw_score=raw_score,
        available_evidence_max=available_max,
        evidence_confidence=round(evidence_confidence, 3),
    )
    return decision
