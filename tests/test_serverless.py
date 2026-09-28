from __future__ import annotations

import time
import unittest
from unittest.mock import Mock, patch

import serverless
from pulse.config import PulseConfig
from pulse.models import CandidateDecision, NormalizedToken, SafetyReport, SafetyStatus


class StorageTests(unittest.TestCase):
    def test_duplicate_alert_prevention(self):
        token = NormalizedToken("11111111111111111111111111111111")
        safety = SafetyReport(SafetyStatus.PASS, {})
        decision = CandidateDecision(token.mint, 80, "STRONG_WATCH", True, safety, {}, "ORGANIC", "ACCELERATING", "STABLE", [], 100, {}, {})
        alerts = {token.mint: {"score": 80, "classification": "STRONG_WATCH", "confirmed_sources": []}}
        self.assertFalse(serverless._should_alert(token, decision, alerts, PulseConfig()))
        decision.score = 91; decision.classification = "HIGH_CONVICTION"
        self.assertTrue(serverless._should_alert(token, decision, alerts, PulseConfig()))

    @patch.object(serverless, "save_performance")
    @patch.object(serverless, "load_performance", return_value={})
    @patch.object(serverless, "has_redis", return_value=True)
    def test_performance_record_schema(self, _redis, load, save):
        token = NormalizedToken("11111111111111111111111111111111", symbol="T", market_cap=100, price_usd=1, liquidity_usd=50)
        safety = SafetyReport(SafetyStatus.PASS, {})
        decision = CandidateDecision(token.mint, 90, "HIGH_CONVICTION", True, safety, {}, "ORGANIC", "ACCELERATING", "STABLE", [], 300, {}, {})
        serverless.track_pulse_alert(token, decision, time.time())
        saved = save.call_args.args[0]
        rec = next(iter(next(iter(saved["days"].values()))["alerts"].values()))
        self.assertIn("checkpoints", rec)
        self.assertIn("executable_3x", rec)
        self.assertEqual(rec["score"], 90)


class SchedulerTests(unittest.TestCase):
    @patch.object(serverless, "redis_command", return_value="1")
    @patch.object(serverless, "has_redis", return_value=True)
    def test_on_state(self, _has, _redis):
        self.assertTrue(serverless.scanner_enabled())

    @patch.object(serverless, "save_json", return_value=True)
    @patch.object(serverless, "load_json", return_value={"schedule_id": "sched_1"})
    @patch.object(serverless, "set_scanner_enabled", return_value=True)
    @patch.object(serverless, "has_redis", return_value=True)
    @patch.object(serverless.requests, "get")
    @patch.object(serverless.requests, "delete")
    def test_off_deletes_remote_schedule(self, delete, get, _has, enabled, load, save):
        get.return_value = Mock(); get.return_value.raise_for_status.return_value = None; get.return_value.json.return_value = []
        delete.return_value = Mock(status_code=204); delete.return_value.raise_for_status.return_value = None
        with patch.object(serverless, "QSTASH_TOKEN", "token"):
            result = serverless.disable_qstash_schedule()
        self.assertTrue(result["disabled"])
        delete.assert_called_once()
        enabled.assert_called_once_with(False)

    @patch.object(serverless, "scanner_enabled", return_value=True)
    @patch.object(serverless, "load_last_stats", return_value={"discovered": 10, "evaluated": 5})
    @patch.object(serverless, "provider_health", return_value={"dexscreener": {"state": "ONLINE"}})
    def test_status_reports_real_provider_state(self, *_):
        text = serverless.pulse_status_text()
        self.assertIn("DexScreener: ONLINE", text)
        self.assertIn("GMGN: UNKNOWN", text)


