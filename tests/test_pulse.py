from __future__ import annotations

import time
import unittest
from unittest.mock import Mock

import requests

from pulse.config import PulseConfig
from pulse.engine import PulseEngine
from pulse.evaluation import (
    demand_quality,
    evaluate,
    exit_analysis,
    hard_rug_gate,
    trend,
)
from pulse.formatting import format_alert
from pulse.models import MarketWindow, NormalizedToken, Observation, SafetyStatus
from pulse.providers.base import BaseProvider, ProviderError
from pulse.providers.dexscreener import DexScreenerProvider

MINT = "11111111111111111111111111111111"


def safe_token() -> NormalizedToken:
    now = time.time()
    return NormalizedToken(
        mint=MINT, name="Pulse Test", symbol="PULSE", created_at=now - 20 * 60,
        price_usd=.0001, market_cap=75_000, liquidity_usd=28_000,
        volume_m5=17_000, buys_m5=127, sells_m5=40, txns_m5=167,
        price_change_m5=15, price_change_h1=35, social_score=80,
        mint_authority_active=False, freeze_authority_active=False, rugged=False,
        top10_pct=30, top20_pct=42, largest_holder_pct=8, creator_pct=2,
        related_wallet_pct=5, sniper_pct=3, bundled_pct=2,
        wallet_cluster_score=.1, wash_trading_score=.1, creator_dumping=False,
        creator_risk="LOW", pair_url="https://dexscreener.com/solana/test",
        sources={
            "pumpfun": Observation("pumpfun", now, {"market_cap": 75_000}),
            "dexscreener": Observation("dexscreener", now, {"market_cap": 75_000}),
            "solana": Observation("solana", now, {"top10_pct": 30}),
        },
        history=[
            MarketWindow(now - 600, 29, 8, 2400, 500, 50, 14_000, 50, .00008, 60_000),
            MarketWindow(now - 300, 61, 15, 6700, 900, 90, 19_000, 95, .00009, 68_000),
            MarketWindow(now, 118, 20, 14900, 1500, 160, 28_000, 170, .0001, 75_000),
        ],
    )


class EvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = PulseConfig()

    def test_hard_rug_rejects_mint_authority(self):
        token = safe_token(); token.mint_authority_active = True
        self.assertEqual(hard_rug_gate(token, self.cfg).status, SafetyStatus.FAIL)

    def test_hard_rug_rejects_freeze_authority(self):
        token = safe_token(); token.freeze_authority_active = True
        self.assertEqual(evaluate(token, self.cfg).classification, "REJECTED")

    def test_creator_risk_rejects(self):
        token = safe_token(); token.creator_risk = "CRITICAL"
        self.assertEqual(evaluate(token, self.cfg).score, 0)

    def test_holder_concentration_rejects(self):
        token = safe_token(); token.top10_pct = 75
        self.assertIn("top10_concentration", evaluate(token, self.cfg).safety.failures)

    def test_bundle_risk_rejects(self):
        token = safe_token(); token.bundled_pct = 50
        self.assertEqual(evaluate(token, self.cfg).classification, "REJECTED")

    def test_sniper_risk_rejects(self):
        token = safe_token(); token.sniper_pct = 40
        self.assertEqual(evaluate(token, self.cfg).classification, "REJECTED")

    def test_wallet_clustering_rejects(self):
        token = safe_token(); token.wallet_cluster_score = .9
        self.assertEqual(demand_quality(token), "MANIPULATED")

    def test_wash_trading_rejects(self):
        token = safe_token(); token.wash_trading_score = .9
        decision = evaluate(token, self.cfg)
        self.assertEqual(decision.classification, "REJECTED")
        self.assertIn("wash_trading", decision.rejected_reason)

    def test_unknown_safety_caps_score(self):
        token = safe_token(); token.mint_authority_active = None
        self.assertLessEqual(evaluate(token, self.cfg).score, 74)

    def test_buyer_acceleration(self):
        self.assertEqual(trend([29, 61, 118]), "ACCELERATING")

    def test_volume_acceleration(self):
        self.assertEqual(trend([2400, 6700, 14900]), "ACCELERATING")

    def test_liquidity_growth(self):
        decision = evaluate(safe_token(), self.cfg)
        self.assertEqual(decision.liquidity_trend, "ACCELERATING")

    def test_late_pump_penalty(self):
        early = safe_token(); late = safe_token(); late.price_change_h1 = 650
        self.assertGreater(evaluate(early, self.cfg).components["early"], evaluate(late, self.cfg).components["early"])

    def test_candidate_score_transparent(self):
        decision = evaluate(safe_token(), self.cfg)
        self.assertEqual(decision.raw_score, sum(decision.components.values()))
        self.assertGreaterEqual(decision.score, 85)
        self.assertGreaterEqual(decision.available_evidence_max, 90)

    def test_optional_provider_gaps_do_not_force_clean_candidate_into_30s(self):
        token = safe_token()
        token.social_score = None
        token.creator_pct = None
        token.related_wallet_pct = None
        token.sniper_pct = None
        token.bundled_pct = None
        token.wallet_cluster_score = None
        token.wash_trading_score = None
        token.creator_dumping = None
        token.creator_risk = "UNKNOWN"
        token.history = [
            MarketWindow(time.time() - 600, transactions=55, liquidity=18_000, price=.00008, market_cap=62_000),
            MarketWindow(time.time() - 300, transactions=90, liquidity=23_000, price=.00009, market_cap=68_000),
            MarketWindow(time.time(), transactions=167, liquidity=28_000, price=.0001, market_cap=75_000),
        ]
        decision = evaluate(token, self.cfg)
        self.assertTrue(decision.alert)
        self.assertEqual(decision.classification, "WATCH")
        self.assertGreaterEqual(decision.score, self.cfg.alert_min_score)
        self.assertLessEqual(decision.score, 74)
        self.assertGreaterEqual(decision.available_evidence_max, 50)

    def test_thin_market_only_evidence_cannot_alert(self):
        now = time.time()
        token = NormalizedToken(
            mint=MINT,
            created_at=now - 20 * 60,
            price_usd=.0001,
            market_cap=75_000,
            liquidity_usd=28_000,
            buys_m5=25,
            sells_m5=10,
            txns_m5=35,
            price_change_m5=8,
            price_change_h1=25,
            sources={"dexscreener": Observation("dexscreener", now, {})},
            history=[
                MarketWindow(now - 300, transactions=20, liquidity=20_000),
                MarketWindow(now, transactions=35, liquidity=28_000),
            ],
        )
        decision = evaluate(token, self.cfg)
        self.assertFalse(decision.alert)
        self.assertLess(decision.available_evidence_max, 50)

    def test_safety_override(self):
        token = safe_token(); token.rugged = True
        decision = evaluate(token, self.cfg)
        self.assertEqual((decision.score, decision.alert), (0, False))

    def test_executable_exit_analysis(self):
        good = exit_analysis(100_000); poor = exit_analysis(3_000)
        self.assertEqual(good[-1].grade, "GOOD")
        self.assertEqual(poor[-1].grade, "POOR")

    def test_three_x_target(self):
        self.assertEqual(evaluate(safe_token(), self.cfg).target_market_cap, 225_000)

    def test_conflict_caps_confidence(self):
        token = safe_token(); token.conflicts.append("dexscreener:market_cap")
        self.assertLessEqual(evaluate(token, self.cfg).score, 74)

    def test_stale_source_marked(self):
        token = safe_token(); token.sources["pumpfun"].observed_at -= 1000
        self.assertEqual(evaluate(token, self.cfg).source_states["pumpfun"], "STALE")

    def test_telegram_format_has_no_probability_claim(self):
        token = safe_token(); decision = evaluate(token, self.cfg)
        from pulse.reasoning import structured_reasoning
        decision.reasoning = structured_reasoning(token, decision)
        alert = format_alert(token, decision)
        self.assertIn("ranking signal", alert)
        self.assertIn("Evidence coverage:", alert)
        self.assertNotIn("% chance", alert)
        self.assertNotIn("{\"", alert)


class DexScreenerProviderTests(unittest.TestCase):
    def test_discovery_merges_profiles_and_boost_attention(self):
        provider = DexScreenerProvider()
        provider._request_json = Mock(side_effect=[
            [{"chainId": "solana", "tokenAddress": MINT}],
            [{"chainId": "solana", "tokenAddress": MINT, "amount": 10}],
            [{"chainId": "solana", "tokenAddress": MINT, "totalAmount": 25}],
        ])

        rows = provider.discover()

        self.assertEqual(len(rows), 1)
        evidence = rows[0].sources["dexscreener"].fields
        self.assertEqual(
            evidence["attention_sources"],
            ["profile", "boost_latest", "boost_top"],
        )
        self.assertTrue(evidence["attention"])

    def test_batch_enrichment_uses_primary_pool_and_aggregates_activity(self):
        now = time.time()
        token = NormalizedToken(MINT, created_at=now - 20 * 60)
        provider = DexScreenerProvider()
        provider._request_json = Mock(return_value=[
            {
                "chainId": "solana",
                "pairAddress": "LOW",
                "dexId": "pumpswap",
                "url": "https://dexscreener.com/solana/low",
                "baseToken": {"address": MINT, "name": "Pulse", "symbol": "PLS"},
                "quoteToken": {"address": "So11111111111111111111111111111111111111112"},
                "pairCreatedAt": int((now - 60) * 1000),
                "priceUsd": "0.00010",
                "marketCap": 100000,
                "fdv": 100000,
                "liquidity": {"usd": 5000},
                "volume": {"m5": 500, "h1": 2000, "h6": 6000, "h24": 12000},
                "txns": {"m5": {"buys": 5, "sells": 2}},
                "priceChange": {"m5": 2, "h1": 8},
                "boosts": {"active": 1},
            },
            {
                "chainId": "solana",
                "pairAddress": "HIGH",
                "dexId": "raydium",
                "url": "https://dexscreener.com/solana/high",
                "baseToken": {"address": MINT, "name": "Pulse", "symbol": "PLS"},
                "quoteToken": {"address": "So11111111111111111111111111111111111111112"},
                "pairCreatedAt": int((now - 120) * 1000),
                "priceUsd": "0.00011",
                "marketCap": 110000,
                "fdv": 110000,
                "liquidity": {"usd": 20000},
                "volume": {"m5": 1500, "h1": 5000, "h6": 14000, "h24": 30000},
                "txns": {"m5": {"buys": 15, "sells": 5}},
                "priceChange": {"m5": 4, "h1": 12},
                "boosts": {"active": 0},
            },
        ])

        provider.enrich_many([token])

        self.assertEqual(token.liquidity_usd, 20000)
        self.assertEqual(token.volume_m5, 2000)
        self.assertEqual(token.volume_h1, 7000)
        self.assertEqual(token.buys_m5, 20)
        self.assertEqual(token.sells_m5, 7)
        self.assertEqual(token.txns_m5, 27)
        self.assertEqual(token.pair_url, "https://dexscreener.com/solana/high")
        self.assertLessEqual(token.created_at, now - 20 * 60 + 1)
        evidence = token.sources["dexscreener"].fields
        self.assertEqual(evidence["dex_id"], "raydium")
        self.assertEqual(evidence["pool_count"], 2)
        self.assertEqual(evidence["active_pool_count"], 2)
        self.assertEqual(evidence["total_liquidity_usd"], 25000)



class FakeProvider:
    def __init__(self, name: str, tokens=None, error=None):
        self.name, self.tokens, self.error = name, tokens or [], error
        self.health = Mock(state="UNKNOWN")
    def discover(self):
        if self.error: raise self.error
        return self.tokens
    def enrich(self, token): return token


class DiscoveryTests(unittest.TestCase):
    def test_multi_source_deduplication_by_mint(self):
        now = time.time()
        a = NormalizedToken(MINT, name="A", sources={"pumpfun": Observation("pumpfun", now, {})})
        b = NormalizedToken(MINT, symbol="B", sources={"dexscreener": Observation("dexscreener", now, {})})
        engine = PulseEngine(providers=[FakeProvider("pumpfun", [a]), FakeProvider("dexscreener", [b])])
        rows = engine.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0].sources), {"pumpfun", "dexscreener"})

    def test_provider_failure_degrades_gracefully(self):
        token = NormalizedToken(MINT)
        engine = PulseEngine(providers=[FakeProvider("bad", error=RuntimeError("down")), FakeProvider("good", [token])])
        self.assertEqual(engine.discover()[0].mint, MINT)

    def test_provider_timeout_retries(self):
        session = Mock()
        provider = BaseProvider(session=session)
        provider.name = "test"; provider.retries = 1; provider.min_interval = 0
        session.request.side_effect = requests.Timeout("timeout")
        with self.assertRaises(ProviderError):
            provider._request_json("GET", "https://example.invalid")
        self.assertEqual(session.request.call_count, 2)

