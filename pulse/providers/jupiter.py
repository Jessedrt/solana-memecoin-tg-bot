from __future__ import annotations

import os
import time
from typing import Any

from pulse.models import Executability, NormalizedToken, Observation

from .base import BaseProvider, ProviderError, as_float

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class JupiterProvider(BaseProvider):
    """Quote-only: no taker, transaction signing, wallet key or execution."""

    name = "jupiter"
    retries = 0
    min_interval = 1.1
    cache_ttl = 10

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.api_key = os.getenv("JUPITER_API_KEY", "").strip()

    def _quote(self, input_mint: str, output_mint: str, amount: int) -> tuple[int, float]:
        data = self._request_json(
            "GET", "https://api.jup.ag/swap/v2/order",
            headers={"x-api-key": self.api_key},
            params={"inputMint": input_mint, "outputMint": output_mint,
                    "amount": str(amount), "swapMode": "ExactIn", "slippageBps": 100},
        )
        if not isinstance(data, dict) or data.get("error") or data.get("errorCode"):
            raise ValueError("Jupiter route unavailable")
        if (data.get("inputMint") != input_mint or data.get("outputMint") != output_mint
                or str(data.get("inAmount")) != str(amount) or not data.get("routePlan")):
            raise ValueError("Jupiter quote mismatch")
        output = int(data["outAmount"])
        minimum = int(data["otherAmountThreshold"])
        impact = as_float(data.get("priceImpact"))
        if impact is None:
            ratio = as_float(data.get("priceImpactPct"))
            impact = ratio * 100 if ratio is not None else None
        if not 0 < minimum <= output or impact is None:
            raise ValueError("Incomplete Jupiter quote")
        return minimum, abs(impact)

    def enrich(self, token: NormalizedToken) -> NormalizedToken:
        token.quotes = []
        if not self.api_key:
            self.health.state, self.health.detail = "UNAVAILABLE", "JUPITER_API_KEY missing"
            return token
        for dollars in (100, 500, 1000):
            started = time.time()
            try:
                tokens, buy_impact = self._quote(USDC, token.mint, dollars * 1_000_000)
                proceeds, sell_impact = self._quote(token.mint, USDC, tokens)
                received = proceeds / 1_000_000
                loss = max(buy_impact, sell_impact, max(0, 1 - received / dollars) * 100)
                grade = "GOOD" if loss <= 3 else "ACCEPTABLE" if loss <= 8 else "POOR"
                token.quotes.append(Executability(dollars, round(received, 2), round(loss, 2), grade, "Jupiter round-trip quote", started))
            except (ProviderError, ValueError, KeyError, TypeError, OverflowError) as exc:
                self.health.mark_failure(type(exc).__name__)
                token.quotes.append(Executability(dollars, 0, 100, "UNKNOWN", "Jupiter round-trip quote", started))
                break  # Bound rate-limit/timeout cost; never turn failure into a pass.
        token.sources[self.name] = Observation(self.name, time.time(), {
            "quoted_sizes": [q.amount for q in token.quotes],
            "quote_only": True, "base_asset": "USDC", "includes_network_fees": False,
        }, token.mint)
        return token
