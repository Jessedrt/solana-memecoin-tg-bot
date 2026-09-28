from __future__ import annotations

import os
import time
from typing import Any

from pulse.models import NormalizedToken, Observation

from .base import BaseProvider, ProviderError, as_float


class SolanaProvider(BaseProvider):
    name = "solana"
    timeout = 10

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com").strip()

    def _rpc(self, method: str, params: list[Any], cache_key: str) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        data = self._request_json("POST", self.rpc_url, cache_key=cache_key, json=payload)
        if not isinstance(data, dict) or data.get("error"):
            raise ValueError(f"Solana RPC {method} failed")
        return data.get("result")

    def enrich(self, token: NormalizedToken) -> NormalizedToken:
        account = self._rpc("getAccountInfo", [token.mint, {"encoding": "jsonParsed", "commitment": "confirmed"}], f"acct:{token.mint}")
        info = (((account or {}).get("value") or {}).get("data") or {}).get("parsed", {}).get("info", {})
        if not isinstance(info, dict):
            raise ValueError("Solana mint account was not parsed")
        degraded: list[str] = []
        try:
            supply_result = self._rpc("getTokenSupply", [token.mint, {"commitment": "confirmed"}], f"supply:{token.mint}")
        except ProviderError:
            supply_result = None
            degraded.append("supply")
        try:
            largest_result = self._rpc("getTokenLargestAccounts", [token.mint, {"commitment": "confirmed"}], f"largest:{token.mint}")
        except ProviderError:
            largest_result = None
            degraded.append("largest accounts")
        supply_value = ((supply_result or {}).get("value") or {})
        supply = as_float(supply_value.get("uiAmountString") or supply_value.get("uiAmount"))
        holders = (largest_result or {}).get("value") or []
        percentages: list[float] = []
        if supply and supply > 0:
            for row in holders:
                amount = as_float((row or {}).get("uiAmountString") or (row or {}).get("uiAmount"))
                if amount is not None:
                    percentages.append(amount / supply * 100)
        token.supply = supply
        token.mint_authority_active = info.get("mintAuthority") is not None
        token.freeze_authority_active = info.get("freezeAuthority") is not None
        token.top10_pct = sum(percentages[:10]) if percentages else None
        token.top20_pct = sum(percentages[:20]) if percentages else None
        token.largest_holder_pct = percentages[0] if percentages else None
        fields = {
            "supply": supply, "mint_authority_active": token.mint_authority_active,
            "freeze_authority_active": token.freeze_authority_active, "top10_pct": token.top10_pct,
            "top20_pct": token.top20_pct, "largest_holder_pct": token.largest_holder_pct,
        }
        token.sources[self.name] = Observation(self.name, time.time(), fields, token.mint)
        if degraded:
            self.health.state = "DEGRADED"
            self.health.detail = "unavailable: " + ", ".join(degraded)
        return token

