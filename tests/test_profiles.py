from __future__ import annotations

import io
import json
import os
import time
import unittest
from unittest.mock import Mock, patch

import serverless
from api.telegram import handler
from pulse.config import PulseConfig
from pulse.engine import PulseEngine
from pulse.evaluation import eligibility_issue, evaluate, profile_range_issue
from pulse.formatting import format_alert
from pulse.models import Executability, NormalizedToken, Observation
from pulse.providers.dexscreener import DexScreenerProvider
from pulse.providers.jupiter import USDC, JupiterProvider
from pulse.providers.rugcheck import RugCheckProvider
from pulse.providers.solana import SolanaProvider
from pulse.providers.tracker import TrackerEligibilityProvider
from tests.test_pulse import MINT, safe_token


def five_token():
    token = safe_token()
    token.rugcheck_danger = False
    token.holder_proxy_clear = True
    token.sources["dexscreener"].fields["liquidity_usd"] = token.liquidity_usd
    for name in ("geckoterminal", "rugcheck"):
        token.sources[name] = Observation(name, time.time(), {})
    token.quotes = [Executability(size, size * .98, 2, "GOOD", "Jupiter round-trip quote", time.time()) for size in (100, 500, 1000)]
    return token


class EligibilityTests(unittest.TestCase):
    def test_both_profiles_require_graduation_and_two_sol(self):
        for profile in ("3x", "5x"):
            cfg = PulseConfig.for_profile(profile)
            for field, values in (("graduated", (None, False)), ("total_trading_fees_sol", (None, 0, 1.999, float("nan"), float("inf")))):
                for value in values:
                    with self.subTest(profile=profile, field=field, value=value):
                        token = five_token()
                        setattr(token, field, value)
                        self.assertFalse(evaluate(token, cfg).alert)

    def test_exact_thresholds_and_lower_boundary(self):
        for profile in ("3x", "5x"):
            token = five_token()
            cfg = PulseConfig.for_profile(profile)
            token.market_cap = 30_000
            token.sources["dexscreener"].fields["market_cap"] = 30_000
            self.assertIsNone(eligibility_issue(token, cfg))
            self.assertTrue(evaluate(token, cfg).alert)
            token.market_cap = 29_999.99
            self.assertFalse(evaluate(token, cfg).alert)

    def test_old_environment_cannot_weaken_floor(self):
        with patch.dict(os.environ, {"PULSE_MIN_MCAP_USD": "20000"}):
            self.assertEqual(PulseConfig.for_profile("3x").min_market_cap, 30_000)
            self.assertEqual(PulseConfig.for_profile("5x").min_market_cap, 30_000)

    def test_no_pump_complete_or_dex_listing_bypass(self):
        token = five_token()
        token.migrated = True
        token.graduated = None
        self.assertFalse(evaluate(token, PulseConfig()).alert)

    def test_missing_stale_future_or_inconsistent_evidence(self):
        for provider in ("dexscreener", "tracker_eligibility"):
            for age in (301, -100):
                token = five_token()
                token.sources[provider].observed_at = time.time() - age
                self.assertIsNotNone(eligibility_issue(token, PulseConfig()))
        token = five_token()
        token.sources["tracker_eligibility"].fields["total_trading_fees_sol"] = 1
        self.assertIsNotNone(eligibility_issue(token, PulseConfig()))


class ProfileTests(unittest.TestCase):
    def test_profiles_and_labels(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(PulseConfig.for_profile().profile, "3x")
        token = five_token()
        for profile, multiple in (("3x", 3), ("5x", 5)):
            decision = evaluate(token, PulseConfig.for_profile(profile))
            self.assertEqual(decision.target_market_cap, 75_000 * multiple)
            self.assertIn(f"{multiple}× CANDIDATE", format_alert(token, decision))
        with self.assertRaises(ValueError):
            PulseConfig.for_profile("6x")

    def test_five_x_hard_ranges(self):
        cfg = PulseConfig.for_profile("5x")
        for field, value in (("market_cap", 250001), ("liquidity_usd", 7999), ("liquidity_usd", 80001), ("created_at", time.time()-721*60), ("created_at", time.time()-14*60)):
            token = five_token()
            setattr(token, field, value)
            self.assertIsNotNone(profile_range_issue(token, cfg))
        for value in (8000, 80000):
            token = five_token()
            token.liquidity_usd = value
            self.assertIsNone(profile_range_issue(token, cfg))

    def test_five_x_quotes_and_unknown_holder_proxy_block(self):
        for mutate in (lambda t: t.quotes.clear(), lambda t: setattr(t, "holder_proxy_clear", None), lambda t: setattr(t.quotes[0], "observed_at", time.time()-61), lambda t: setattr(t.quotes[1], "grade", "POOR")):
            token = five_token()
            mutate(token)
            self.assertFalse(evaluate(token, PulseConfig.for_profile("5x")).alert)

    def test_no_paid_discovery_or_quotes_for_ineligible(self):
        providers = [Mock(name="mock") for _ in range(3)]
        for provider, name in zip(providers, ("geckoterminal", "dexscreener", "jupiter")):
            provider.name = name
            provider.discover.return_value = []
        engine = PulseEngine(PulseConfig.for_profile("5x"), providers)
        engine.discover()
        providers[0].discover.assert_called_once()
        providers[1].discover.assert_not_called()
        token = five_token()
        token.graduated = False
        engine.evaluate(token)
        providers[2].enrich.assert_not_called()


class TrackerTests(unittest.TestCase):
    def provider(self, row):
        provider = TrackerEligibilityProvider()
        provider.api_key = "test-only"
        provider._request_json = Mock(return_value={"status": "success", "data": [row]})
        return provider

    def test_exact_field_no_tips_or_creator_fees(self):
        row = {"mint": MINT, "status": "graduated", "lastUpdated": time.time()*1000,
               "fees": {"total": 10, "totalTrading": 2, "totalTips": 8}, "creatorFees": 999}
        token = safe_token()
        self.provider(row).enrich(token)
        self.assertEqual(token.total_trading_fees_sol, 2)
        self.assertTrue(token.graduated)
        del row["fees"]["totalTrading"]
        self.provider(row).enrich(token)
        self.assertIsNone(token.total_trading_fees_sol)

    def test_graduating_is_not_graduated(self):
        for status in ("graduating", "default", None, "true"):
            token = safe_token()
            self.provider({"mint": MINT, "status": status}).enrich(token)
            self.assertIsNot(token.graduated, True)

    def test_wrong_mint_and_duplicate_rows_fail(self):
        provider = self.provider({"mint": "wrong"})
        with self.assertRaises(ValueError):
            provider.enrich(safe_token())
        provider._request_json.return_value["data"] = [{"mint": MINT}, {"mint": MINT}]
        with self.assertRaises(ValueError):
            provider.enrich(safe_token())

    def test_missing_key_and_invalid_fees_fail_closed(self):
        provider = self.provider({})
        provider.api_key = ""
        token = safe_token()
        provider.enrich(token)
        self.assertIsNone(token.graduated)
        provider._request_json.assert_not_called()
        for value in (-1, "nan", "inf", True):
            self.provider({"mint": MINT, "fees": {"totalTrading": value}}).enrich(token)
            self.assertIsNone(token.total_trading_fees_sol)


class SafetyProviderTests(unittest.TestCase):
    def test_rpc_missing_authorities_are_unknown(self):
        provider = SolanaProvider()
        for value in (None, {}, {"data": {"parsed": {"type": "mint", "info": {}}}}):
            provider._rpc = Mock(return_value={"value": value})
            token = NormalizedToken(MINT)
            with self.assertRaises(ValueError):
                provider.enrich(token)
            self.assertIsNone(token.mint_authority_active)

    def test_rugcheck_danger_and_holder_proxy(self):
        provider = RugCheckProvider()
        row = {"mint": MINT, "risks": [], "topHolders": [{"owner": str(i), "pct": 3, "insider": False} for i in range(10)]}
        provider._request_json = Mock(return_value=row)
        token = five_token()
        provider.enrich(token)
        self.assertTrue(token.holder_proxy_clear)
        row["risks"] = [{"level": "danger", "name": "LP unlocked"}]
        provider.enrich(token)
        self.assertFalse(evaluate(token, PulseConfig()).alert)
        for holder in row["topHolders"]:
            holder["owner"] = "same"
        provider.enrich(token)
        self.assertFalse(token.holder_proxy_clear)

    def test_quote_side_pair_does_not_supply_base_token_cap(self):
        provider = DexScreenerProvider()
        token = NormalizedToken(MINT)
        provider._apply_pairs(token, [{"chainId": "solana", "baseToken": {"address": "other"}, "quoteToken": {"address": MINT}, "marketCap": 80000}], raw_id="test")
        self.assertIsNone(token.market_cap)


class QuoteTests(unittest.TestCase):
    def test_round_trip_raw_units_no_wallet_and_impact_conversion(self):
        provider = JupiterProvider()
        provider.api_key = "test"
        calls = []
        def quote(method, url, **kwargs):
            params = kwargs["params"]
            calls.append(params)
            output = int(params["amount"]) * 10 if params["inputMint"] == USDC else int(params["amount"]) // 10
            return {"inputMint": params["inputMint"], "outputMint": params["outputMint"], "inAmount": params["amount"],
                    "outAmount": str(output), "otherAmountThreshold": str(output), "priceImpactPct": ".01", "routePlan": [{}]}
        provider._request_json = quote
        token = five_token()
        provider.enrich(token)
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[0]["amount"], "100000000")
        self.assertEqual(calls[1]["inputMint"], MINT)
        self.assertEqual(calls[1]["outputMint"], USDC)
        self.assertEqual(calls[1]["amount"], "1000000000")
        self.assertNotIn("taker", calls[0])
        self.assertEqual(token.quotes[0].impact_pct, 1)

    def test_missing_key_or_no_route_never_pass(self):
        provider = JupiterProvider()
        token = five_token()
        provider.api_key = ""
        provider.enrich(token)
        self.assertEqual(token.quotes, [])
        provider.api_key = "test"
        provider._request_json = Mock(return_value={"error": "No route"})
        provider.enrich(token)
        self.assertEqual(token.quotes[0].grade, "UNKNOWN")


class RuntimeProfileTests(unittest.TestCase):
    def test_profile_persistence_and_separate_keys(self):
        with patch.object(serverless, "has_redis", return_value=True), patch.object(serverless, "redis_command", return_value="5x") as redis:
            self.assertEqual(serverless.scanner_profile(), "5x")
            self.assertEqual(serverless.set_scanner_profile("3x"), "3x")
            redis.assert_called_with("SET", serverless.SCANNER_PROFILE_KEY, "3x")
        self.assertEqual(serverless.profile_key("key", "3x"), "key")
        self.assertEqual(serverless.profile_key("key", "5x"), "key:5x")

    def test_new_pool_retained_until_old_enough(self):
        token = five_token()
        now = time.time()
        token.created_at = now - 60
        with patch.object(serverless, "load_json", return_value={}), patch.object(serverless, "save_json") as save:
            self.assertEqual(serverless._free_stack_candidates([token], now, 4), [])
            cache = save.call_args.args[1]
        with patch.object(serverless, "load_json", return_value=cache), patch.object(serverless, "save_json"):
            self.assertEqual(serverless._free_stack_candidates([], now+900, 4)[0].mint, MINT)

    def test_webhook_profile_and_authorization(self):
        for secret, chat, allowed in (("secret", "owner", True), ("bad", "owner", False), ("secret", "other", False)):
            request = object.__new__(handler)
            body = json.dumps({"update_id": 123, "message": {"text": "/profile 5x", "chat": {"id": chat}}}).encode()
            request.headers = {"Content-Length": str(len(body)), "X-Telegram-Bot-Api-Secret-Token": secret}
            request.rfile = io.BytesIO(body)
            request._reply = Mock()
            with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "secret"}), patch.object(serverless.bot, "TELEGRAM_BOT_TOKEN", "test"), patch.object(serverless.bot, "TELEGRAM_CHAT_ID", "owner"), patch.object(serverless, "claim_telegram_update", return_value=True), patch.object(serverless.bot, "Telegram"), patch.object(serverless, "set_scanner_profile", return_value="5x") as setter:
                request.do_POST()
                self.assertEqual(setter.called, allowed)
