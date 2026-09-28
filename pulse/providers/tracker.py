from __future__ import annotations

import os
from typing import Any

from pulse.models import NormalizedToken, Observation

from .base import BaseProvider, as_float


class TrackerEligibilityProvider(BaseProvider):
    """Exact-mint lifecycle and cumulative trading fees, denominated in SOL.

    Contract: https://docs.solanatracker.io/data-api/search/token-search
    Do not substitute fees.total (includes tips), creator fees or volume * rate.
    """

    name = "tracker_eligibility"
    retries = 0
    min_interval = 1.1

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.api_key = os.getenv("SOLANA_TRACKER_API_KEY", "").strip()

    def enrich(self, token: NormalizedToken) -> NormalizedToken:
        token.graduated = None
        token.total_trading_fees_sol = None
        token.sources.pop(self.name, None)
        if not self.api_key:
            self.health.state, self.health.detail = "UNAVAILABLE", "SOLANA_TRACKER_API_KEY missing"
            return token
        data = self._request_json(
            "GET", "https://data.solanatracker.io/search",
            headers={"x-api-key": self.api_key},
            params={"query": token.mint, "limit": 100, "showAllPools": "false"},
            cache_key=token.mint,
        )
        if not isinstance(data, dict) or data.get("status") != "success" or not isinstance(data.get("data"), list):
            raise ValueError("Malformed Tracker search response")
        rows = [row for row in data["data"] if isinstance(row, dict) and row.get("mint") == token.mint]
        if len(rows) != 1:
            raise ValueError("Missing or ambiguous exact-mint Tracker evidence")
        row = rows[0]
        status = row.get("status")
        token.graduated = status == "graduated" if status in ("graduated", "graduating", "default") else None
        fees = row.get("fees")
        total = as_float(fees.get("totalTrading")) if isinstance(fees, dict) else None
        token.total_trading_fees_sol = total if total is not None and total >= 0 else None
        updated = as_float(row.get("lastUpdated"))
        # Documented Unix milliseconds; absent timestamp is stale, not fresh.
        observed_at = updated / 1000 if updated is not None else 0
        token.sources[self.name] = Observation(self.name, observed_at, {
            "status": status, "total_trading_fees_sol": token.total_trading_fees_sol,
            "fee_field": "fees.totalTrading", "fee_unit": "SOL",
        }, token.mint)
        return token
