from __future__ import annotations

import os
from dataclasses import dataclass, field, replace


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
    profile: str = "3x"
    target_multiple: int = 3
    max_liquidity: float | None = None
    min_age_minutes: float = field(default_factory=lambda: _float("PULSE_MIN_AGE_MINUTES", 5))
    max_age_minutes: float = field(default_factory=lambda: _float("PULSE_MAX_AGE_MINUTES", 360))
    min_market_cap: float = field(default_factory=lambda: max(30_000, _float("PULSE_MIN_MCAP_USD", 30_000)))
    min_trading_fees_sol: float = 2.0
    min_holders: int = field(default_factory=lambda: _int("PULSE_MIN_HOLDERS", 200))
    max_market_cap: float = field(default_factory=lambda: _float("PULSE_MAX_MCAP_USD", 100_000))
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
    min_alert_evidence_pct: float = field(default_factory=lambda: _float("PULSE_MIN_ALERT_EVIDENCE_PCT", 65))
    max_alert_price_change_m5: float = field(default_factory=lambda: _float("PULSE_MAX_ALERT_PRICE_CHANGE_M5", 60))
    max_alert_price_change_h1: float = field(default_factory=lambda: _float("PULSE_MAX_ALERT_PRICE_CHANGE_H1", 150))
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

    @classmethod
    def for_profile(cls, profile: str | None = None) -> PulseConfig:
        name = (profile or os.getenv("PULSE_PROFILE") or "3x").strip().lower()
        if name not in ("3x", "5x"):
            raise ValueError("Profile must be 3x or 5x")
        config = cls()
        if name == "3x":
            return config
        return replace(config, profile="5x", target_multiple=5,
                       min_age_minutes=15, max_age_minutes=720,
                       min_market_cap=max(30_000, config.min_market_cap), max_market_cap=min(config.max_market_cap, 100_000),
                       critical_liquidity=8_000, preferred_liquidity=8_000,
                       max_liquidity=80_000, max_candidates=min(config.max_candidates, 4))

