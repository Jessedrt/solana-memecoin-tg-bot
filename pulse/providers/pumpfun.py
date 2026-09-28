from __future__ import annotations

import time
from typing import Any

from pulse.models import NormalizedToken, Observation

from .base import BaseProvider, as_float, as_int, required_mint


class PumpFunProvider(BaseProvider):
    """Best-effort Pump.fun frontend adapter.

    Pump.fun does not publish a stable public data API contract for this feed, so
    health/status explicitly identifies it as LIMITED instead of promising SLA.
    """

    name = "pumpfun"
    base_url = "https://frontend-api-v3.pump.fun"

    def discover(self) -> list[NormalizedToken]:
        found: dict[str, NormalizedToken] = {}
        for sort in ("created_timestamp", "last_trade_timestamp"):
            data = self._request_json(
                "GET", f"{self.base_url}/coins",
                cache_key=f"discover:{sort}",
                params={"offset": 0, "limit": 50, "sort": sort, "order": "DESC", "includeNsfw": "false"},
            )
            if not isinstance(data, list):
                raise ValueError("Pump.fun response is not a list")
            for raw in data:
                if not isinstance(raw, dict):
                    continue
                try:
                    token = self._normalize(raw)
                except ValueError:
                    continue
                found.setdefault(token.mint, token)
        self.health.state = "LIMITED"
        self.health.detail = "best-effort frontend feed; no public stability guarantee"
        return list(found.values())

    def _normalize(self, raw: dict[str, Any]) -> NormalizedToken:
        mint = required_mint(raw.get("mint"))
        created = as_float(raw.get("created_timestamp"))
        if created and created > 10_000_000_000:
            created /= 1000
        market_cap = as_float(raw.get("usd_market_cap") or raw.get("market_cap"))
        virtual_sol = as_float(raw.get("virtual_sol_reserves"))
        if virtual_sol and virtual_sol > 1_000_000:
            virtual_sol /= 1_000_000_000
        progress = min(100.0, virtual_sol / 85 * 100) if virtual_sol is not None else None
        holder_count = as_int(raw.get("holder_count") or raw.get("holders_count") or raw.get("total_holders"))
        fields = {
            "market_cap": market_cap, "created_at": created, "bonding_progress": progress,
            "migrated": bool(raw.get("complete")), "creator": raw.get("creator"),
            "replies": as_int(raw.get("reply_count")), "holder_count": holder_count,
        }
        return NormalizedToken(
            mint=mint, name=str(raw.get("name") or "?"), symbol=str(raw.get("symbol") or "?"),
            created_at=created, market_cap=market_cap, bonding_progress=progress,
            migrated=bool(raw.get("complete")), creator=str(raw.get("creator") or "") or None,
            holder_count=holder_count,
            image_url=str(raw.get("image_uri") or ""),
            sources={self.name: Observation(self.name, time.time(), fields, mint)},
        )

