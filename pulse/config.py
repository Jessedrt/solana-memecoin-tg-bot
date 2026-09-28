from __future__ import annotations

import os
from dataclasses import dataclass, field


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class PulseConfig:
    min_age_minutes: float = field(default_factory=lambda: _float("PULSE_MIN_AGE_MINUTES", 5))
    max_age_minutes: float = field(default_factory=lambda: _float("PULSE_MAX_AGE_MINUTES", 360))
    min_market_cap: float = field(default_factory=lambda: _float("PULSE_MIN_MCAP_USD", 20_000))
    max_market_cap: float = field(default_factory=lambda: _float("PULSE_MAX_MCAP_USD", 500_000))
    preferred_liquidity: float = field(default_factory=lambda: _float("PULSE_PREFERRED_LIQUIDITY_USD", 8_000))
    critical_liquidity: float = field(default_factory=lambda: _float("PULSE_CRITICAL_LIQUIDITY_USD", 2_000))
    max_creator_pct: float = field(default_factory=lambda: _float("PULSE_MAX_CREATOR_PCT", 20))
    max_related_pct: float = field(default_factory=lambda: _float("PULSE_MAX_RELATED_WALLETS_PCT", 35))
    max_top10_pct: float = field(default_factory=lambda: _float("PULSE_MAX_TOP10_PCT", 60))
    max_largest_holder_pct: float = field(default_factory=lambda: _float("PULSE_MAX_LARGEST_HOLDER_PCT", 30))
    max_bundle_pct: float = field(default_factory=lambda: _float("PULSE_MAX_BUNDLE_PCT", 30))
    max_sniper_pct: float = field(default_factory=lambda: _float("PULSE_MAX_SNIPER_PCT", 25))
    stale_seconds: int = field(default_factory=lambda: _int("PULSE_STALE_SECONDS", 300))
    conflict_ratio: float = field(default_factory=lambda: _float("PULSE_CONFLICT_RATIO", 0.25))
    alert_min_score: int = field(default_factory=lambda: _int("PULSE_ALERT_MIN_SCORE", 65))
    strong_watch_score: int = field(default_factory=lambda: _int("PULSE_STRONG_WATCH_SCORE", 75))
    high_conviction_score: int = field(default_factory=lambda: _int("PULSE_HIGH_CONVICTION_SCORE", 85))
    re_alert_score_delta: int = field(default_factory=lambda: _int("PULSE_REALERT_SCORE_DELTA", 10))
    max_candidates: int = field(default_factory=lambda: _int("PULSE_MAX_CANDIDATES", 12))
    weights: dict[str, int] = field(default_factory=lambda: {
        "safety": _int("PULSE_WEIGHT_SAFETY", 30),
        "wallet": _int("PULSE_WEIGHT_WALLET", 20),
        "momentum": _int("PULSE_WEIGHT_MOMENTUM", 15),
        "liquidity": _int("PULSE_WEIGHT_LIQUIDITY", 15),
        "early": _int("PULSE_WEIGHT_EARLY", 10),
        "social": _int("PULSE_WEIGHT_SOCIAL", 10),
    })

