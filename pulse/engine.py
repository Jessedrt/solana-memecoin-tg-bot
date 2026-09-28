from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable

from .config import PulseConfig
from .evaluation import eligibility_issue, evaluate, hard_rug_gate, profile_range_issue
from .models import CandidateDecision, MarketWindow, NormalizedToken
from .providers import (
    DexScreenerProvider,
    FomoProvider,
    GmgnProvider,
    PumpFunProvider,
    SolanaProvider,
)
from .providers.base import BaseProvider
from .providers.geckoterminal import GeckoTerminalProvider
from .providers.jupiter import JupiterProvider
from .providers.rugcheck import RugCheckProvider
from .providers.tracker import TrackerEligibilityProvider
from .reasoning import structured_reasoning

log = logging.getLogger("pulse.engine")


class PulseEngine:
    def __init__(
        self,
        config: PulseConfig | None = None,
        providers: Iterable[BaseProvider] | None = None,
    ) -> None:
        self.config = config or PulseConfig.for_profile()
        self.deadline = time.monotonic() + 180
        defaults = ([GeckoTerminalProvider(), DexScreenerProvider(), SolanaProvider(), RugCheckProvider(), JupiterProvider()]
                    if self.config.profile == "5x" else
                    [PumpFunProvider(), DexScreenerProvider(), GmgnProvider(), FomoProvider(), SolanaProvider(), RugCheckProvider()])
        defaults.append(TrackerEligibilityProvider())
        self.providers = list(providers if providers is not None else defaults)
        if self.config.profile == "5x":
            for provider in self.providers:
                provider.retries = 0  # Bound a serverless scan under provider outages.

    def discover(self) -> list[NormalizedToken]:
        discovery = [p for p in self.providers if p.name not in ("solana", "rugcheck", "jupiter", "tracker_eligibility")
                     and (self.config.profile != "5x" or p.name == "geckoterminal")]
        tokens: list[NormalizedToken] = []
        with ThreadPoolExecutor(max_workers=min(4, len(discovery) or 1)) as pool:
            futures = {pool.submit(p.discover): p for p in discovery}
            for future in as_completed(futures):
                provider = futures[future]
                try:
                    tokens.extend(future.result())
                except Exception as exc:
                    provider.health.mark_failure(type(exc).__name__)
                    log.warning("provider_failure provider=%s error=%s", provider.name, type(exc).__name__)
        return self._deduplicate(tokens)

    def _deduplicate(self, tokens: list[NormalizedToken]) -> list[NormalizedToken]:
        merged: dict[str, NormalizedToken] = {}
        for token in tokens:
            if token.mint not in merged:
                merged[token.mint] = token
                continue
            target = merged[token.mint]
            for name, obs in token.sources.items():
                target.sources[name] = obs
            for field in ("name", "symbol", "created_at", "market_cap", "price_usd", "liquidity_usd", "bonding_progress", "migrated", "creator", "image_url"):
                old, new = getattr(target, field), getattr(token, field)
                if old in (None, "", "?") and new not in (None, "", "?"):
                    setattr(target, field, new)
                elif field in ("market_cap", "price_usd", "liquidity_usd") and old and new:
                    if abs(float(old) - float(new)) / max(float(old), float(new)) > self.config.conflict_ratio:
                        target.conflicts.append(f"{name}:{field}")
        return list(merged.values())

    def _guideline_priority(self, t: NormalizedToken) -> float:
        age = t.age_minutes
        score = 0.0
        if age is not None and self.config.min_age_minutes <= age <= self.config.max_age_minutes:
            score += 30
        if t.market_cap and self.config.min_market_cap <= t.market_cap <= self.config.max_market_cap:
            score += 25
        if t.liquidity_usd and t.liquidity_usd >= self.config.preferred_liquidity:
            score += 20
        if "pumpfun" in t.sources:
            score += 20
        score += min(5, len(t.sources))
        return score

    def _mark_conflicts(self, token: NormalizedToken) -> None:
        for field in ("market_cap", "price_usd", "liquidity_usd"):
            values: list[tuple[str, float]] = []
            for source, obs in token.sources.items():
                value = obs.fields.get(field)
                if isinstance(value, (int, float)) and value > 0:
                    values.append((source, float(value)))
            if len(values) >= 2:
                low, high = min(v for _, v in values), max(v for _, v in values)
                if (high - low) / high > self.config.conflict_ratio:
                    for source, _ in values:
                        marker = f"{source}:{field}"
                        if marker not in token.conflicts:
                            token.conflicts.append(marker)

    def enrich(
        self,
        token: NormalizedToken,
        provider_names: tuple[str, ...] = ("dexscreener", "solana"),
    ) -> NormalizedToken:
        allowed = set(provider_names)
        providers = [p for p in self.providers if getattr(p, "name", "") in allowed]
        with ThreadPoolExecutor(max_workers=len(providers) or 1) as pool:
            futures = {pool.submit(provider.enrich, token): provider for provider in providers}
            for future in as_completed(futures):
                provider = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    provider.health.mark_failure(type(exc).__name__)
                    log.warning("provider_enrichment_failure provider=%s mint=%s error=%s", provider.name, token.mint, type(exc).__name__)
        self._mark_conflicts(token)
        return token

    def enrich_many(self, tokens: Iterable[NormalizedToken]) -> list[NormalizedToken]:
        """Batch DexScreener market enrichment, then verify each mint on Solana."""
        rows = list(tokens)
        if not rows:
            return rows

        dex = next((p for p in self.providers if getattr(p, "name", "") == "dexscreener"), None)
        dex_batched = False
        if dex is not None and hasattr(dex, "enrich_many"):
            try:
                dex.enrich_many(rows)
                dex_batched = True
            except Exception as exc:
                dex.health.mark_failure(type(exc).__name__)
                log.warning("provider_batch_enrichment_failure provider=dexscreener error=%s", type(exc).__name__)

        for token in rows:
            if self.config.profile == "5x" and time.monotonic() >= self.deadline:
                break
            if not dex_batched:
                self.enrich(token, provider_names=("dexscreener",))
            if profile_range_issue(token, self.config):
                continue
            if token.market_cap is None or token.market_cap < self.config.min_market_cap:
                continue
            self.enrich(token, provider_names=("tracker_eligibility",))
            if eligibility_issue(token, self.config):
                continue
            self.enrich(token, provider_names=("solana",))
            self.enrich(token, provider_names=("rugcheck",))
        return rows

    def evaluate(self, token: NormalizedToken, history: list[MarketWindow] | None = None) -> CandidateDecision:
        token.history = list(history or token.history)
        # Quote only range/safety survivors and evaluate immediately so a
        # later candidate's network calls cannot age this token's quotes.
        if self.config.profile == "5x" and not profile_range_issue(token, self.config) and not eligibility_issue(token, self.config):
            gate = hard_rug_gate(token, self.config)
            mandatory = ("mint_authority", "freeze_authority", "rugcheck_danger", "holder_proxy", "top10_concentration")
            if (not gate.failures and all(gate.checks.get(k) == "PASS" for k in mandatory)
                    and time.monotonic() + 55 <= self.deadline):
                self.enrich(token, provider_names=("jupiter",))
        decision = evaluate(token, self.config)
        decision.reasoning = structured_reasoning(token, decision)
        event = "REJECTED" if decision.rejected_reason else "EVALUATED"
        log.info("%s mint=%s score=%s classification=%s reason=%s", event, token.mint, decision.score, decision.classification, decision.rejected_reason or "none")
        return decision

    def scan(self, history_loader: Callable[[str], list[MarketWindow]] | None = None) -> list[tuple[NormalizedToken, CandidateDecision]]:
        discovered = self.discover()
        discovered.sort(key=self._guideline_priority, reverse=True)
        selected = discovered[: self.config.max_candidates]
        self.enrich_many(selected)
        results: list[tuple[NormalizedToken, CandidateDecision]] = []
        for token in selected:
            history = history_loader(token.mint) if history_loader else []
            results.append((token, self.evaluate(token, history)))
        return results

    def health_snapshot(self) -> dict[str, dict[str, object]]:
        return {p.name: vars(p.health).copy() for p in self.providers}
