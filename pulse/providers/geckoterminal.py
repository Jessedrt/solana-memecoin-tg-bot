from __future__ import annotations

import time
from datetime import datetime

from pulse.models import NormalizedToken, Observation

from .base import BaseProvider, as_float, required_mint


class GeckoTerminalProvider(BaseProvider):
    """Public new-pool discovery, not paid/boosted token discovery."""

    name = "geckoterminal"
    min_interval = 2.1

    def discover(self) -> list[NormalizedToken]:
        data = self._request_json(
            "GET", "https://api.geckoterminal.com/api/v2/networks/solana/new_pools",
            params={"page": 1, "include": "base_token"}, cache_key="new-pools",
        )
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ValueError("Malformed GeckoTerminal new pools")
        included = {r.get("id"): r.get("attributes", {}) for r in data.get("included", []) if isinstance(r, dict)}
        tokens = []
        for row in data["data"]:
            try:
                attrs = row["attributes"]
                token_id = row["relationships"]["base_token"]["data"]["id"]
                meta = included.get(token_id, {})
                mint = required_mint(meta.get("address") or token_id.removeprefix("solana_"))
                created = datetime.fromisoformat(attrs["pool_created_at"].replace("Z", "+00:00")).timestamp()
                token = NormalizedToken(mint, name=meta.get("name") or "?", symbol=meta.get("symbol") or "?",
                                        created_at=created)
                # Discovery-only hints. DexScreener must independently supply
                # the market values used by the hard filter (no FDV fallback).
                token.sources[self.name] = Observation(self.name, time.time(), {
                    "pool_created_at": created,
                    "discovery_market_cap": as_float(attrs.get("market_cap_usd")),
                    "discovery_liquidity": as_float(attrs.get("reserve_in_usd")),
                }, str(attrs.get("address") or ""))
                tokens.append(token)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        return tokens
