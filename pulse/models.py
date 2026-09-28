from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SafetyStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class SourceState(str, Enum):
    CONFIRMED = "CONFIRMED"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICTING = "CONFLICTING"
    STALE = "STALE"


@dataclass
class ProviderHealth:
    name: str
    state: str = "UNKNOWN"
    last_success_at: float | None = None
    last_error_at: float | None = None
    latency_ms: int | None = None
    detail: str = "not called"

    def mark_success(self, latency_ms: int) -> None:
        self.state, self.last_success_at, self.latency_ms, self.detail = "ONLINE", time.time(), latency_ms, "ok"

    def mark_failure(self, detail: str) -> None:
        self.state, self.last_error_at, self.detail = "OFFLINE", time.time(), detail[:160]


@dataclass
class Observation:
    provider: str
    observed_at: float
    fields: dict[str, Any]
    raw_id: str = ""


@dataclass
class MarketWindow:
    observed_at: float
    unique_buyers: int | None = None
    unique_sellers: int | None = None
    buy_volume: float | None = None
    sell_volume: float | None = None
    transactions: int | None = None
    liquidity: float | None = None
    holder_count: int | None = None
    price: float | None = None
    market_cap: float | None = None


@dataclass
class NormalizedToken:
    mint: str
    name: str = "?"
    symbol: str = "?"
    created_at: float | None = None
    price_usd: float | None = None
    market_cap: float | None = None
    fdv: float | None = None
    liquidity_usd: float | None = None
    volume_m5: float | None = None
    volume_h1: float | None = None
    volume_h6: float | None = None
    volume_h24: float | None = None
    buys_m5: int | None = None
    sells_m5: int | None = None
    txns_m5: int | None = None
    price_change_m5: float | None = None
    price_change_h1: float | None = None
    price_change_h6: float | None = None
    price_change_h24: float | None = None
    bonding_progress: float | None = None
    migrated: bool | None = None
    launchpad: str | None = None
    dex_id: str | None = None
    has_socials: bool | None = None
    discovery_lane: str | None = None
    creator: str | None = None
    social_score: float | None = None
    attention_acceleration: float | None = None
    mint_authority_active: bool | None = None
    freeze_authority_active: bool | None = None
    supply: float | None = None
    top10_pct: float | None = None
    top20_pct: float | None = None
    largest_holder_pct: float | None = None
    creator_pct: float | None = None
    related_wallet_pct: float | None = None
    sniper_pct: float | None = None
    bundled_pct: float | None = None
    holder_count: int | None = None
    independent_holders: int | None = None
    fresh_wallet_pct: float | None = None
    wallet_cluster_score: float | None = None
    wash_trading_score: float | None = None
    creator_risk: str = "UNKNOWN"
    creator_prior_launches: int | None = None
    creator_failed_launches: int | None = None
    creator_dumping: bool | None = None
    rugged: bool | None = None
    pair_url: str = ""
    image_url: str = ""
    sources: dict[str, Observation] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    history: list[MarketWindow] = field(default_factory=list)

    @property
    def age_minutes(self) -> float | None:
        return None if not self.created_at else max(0.0, (time.time() - self.created_at) / 60)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SafetyReport:
    status: SafetyStatus
    checks: dict[str, SafetyStatus]
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)


@dataclass
class Executability:
    amount: int
    proceeds: float
    impact_pct: float
    grade: str


@dataclass
class CandidateDecision:
    mint: str
    score: int
    classification: str
    alert: bool
    safety: SafetyReport
    components: dict[str, int]
    demand_quality: str
    momentum: str
    liquidity_trend: str
    executability: list[Executability]
    target_market_cap: float | None
    reasoning: dict[str, Any]
    source_states: dict[str, str]
    rejected_reason: str | None = None
    raw_score: int = 0
    available_evidence_max: int = 0
    evidence_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

