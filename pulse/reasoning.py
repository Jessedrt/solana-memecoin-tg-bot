from __future__ import annotations

from .models import CandidateDecision, NormalizedToken


def structured_reasoning(t: NormalizedToken, d: CandidateDecision) -> dict[str, object]:
    """Deterministic, auditable reasoning over normalized evidence only."""
    positives: list[str] = []
    risks: list[str] = []
    unavailable = [name for name, state in d.source_states.items() if state != "CONFIRMED"]
    if d.momentum == "ACCELERATING":
        positives.append("Buyer or buy-volume growth is accelerating across stored windows.")
    if d.liquidity_trend == "ACCELERATING":
        positives.append("Liquidity is increasing.")
    if d.demand_quality in ("ORGANIC", "MOSTLY ORGANIC"):
        positives.append("Observed demand is distributed across independent wallets.")
    if t.age_minutes is not None and t.age_minutes <= 120 and (t.price_change_h1 or 0) < 100:
        positives.append("The token is young and has not completed a large one-hour move.")
    if d.safety.unknowns:
        risks.append("Critical safety evidence is incomplete: " + ", ".join(d.safety.unknowns) + ".")
    if t.conflicts:
        risks.append("Provider data conflicts: " + ", ".join(t.conflicts) + ".")
    if d.executability[-1].grade in ("POOR", "UNKNOWN"):
        risks.append("A $1,000 exit is not currently well supported by liquidity.")
    if not positives:
        positives.append("No strong positive signal was confirmed.")
    invalidation = ["creator begins selling", "liquidity deteriorates", "buyer acceleration reverses", "wallet clustering appears"]
    return {
        "discovery": "Found by " + ", ".join(sorted(t.sources)) + ".",
        "early": bool(t.age_minutes is not None and t.age_minutes <= 120 and (t.price_change_h1 or 0) < 100),
        "unique_buyers_accelerating": d.momentum == "ACCELERATING" and any(w.unique_buyers is not None for w in t.history),
        "volume_accelerating": d.momentum == "ACCELERATING" and any(w.buy_volume is not None for w in t.history),
        "liquidity": d.liquidity_trend,
        "demand_quality": d.demand_quality,
        "creator_history": t.creator_risk,
        "holder_concentration": {"top10_pct": t.top10_pct, "top20_pct": t.top20_pct},
        "bundle_sniper_risk": {"bundled_pct": t.bundled_pct, "sniper_pct": t.sniper_pct},
        "executability": {str(x.amount): x.grade for x in d.executability},
        "three_x_feasibility": "PLAUSIBLE" if d.score >= 75 and d.executability[-1].grade != "POOR" else "UNPROVEN",
        "positives": positives,
        "risks": risks,
        "invalidation": invalidation,
        "unavailable": unavailable,
    }

