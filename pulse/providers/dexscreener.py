from __future__ import annotations

import time

from pulse.models import NormalizedToken, Observation

from .base import BaseProvider, as_float, as_int, required_mint


class DexScreenerProvider(BaseProvider):
    name = "dexscreener"
    base_url = "https://api.dexscreener.com"
    min_interval = 0.1

    def discover(self) -> list[NormalizedToken]:
        # Secondary attention feed only; Pump.fun remains the early launch feed.
        data = self._request_json("GET", f"{self.base_url}/token-profiles/latest/v1", cache_key="profiles")
        if not isinstance(data, list):
            raise ValueError("DexScreener profiles response is not a list")
        out: list[NormalizedToken] = []
        for row in data:
            if not isinstance(row, dict) or str(row.get("chainId")) != "solana":
                continue
            try:
                mint = required_mint(row.get("tokenAddress"))
            except ValueError:
                continue
            out.append(NormalizedToken(
                mint=mint,
                sources={self.name: Observation(self.name, time.time(), {"attention": True}, mint)},
            ))
        return out

    def enrich(self, token: NormalizedToken) -> NormalizedToken:
        data = self._request_json("GET", f"{self.base_url}/tokens/v1/solana/{token.mint}", cache_key=f"token:{token.mint}")
        if not isinstance(data, list):
            raise ValueError("DexScreener token response is not a list")
        pairs = [p for p in data if isinstance(p, dict) and p.get("chainId") == "solana"]
        if not pairs:
            return token
        pair = max(pairs, key=lambda p: as_float((p.get("liquidity") or {}).get("usd")) or 0)
        liq = pair.get("liquidity") or {}
        volume, txns, change = pair.get("volume") or {}, pair.get("txns") or {}, pair.get("priceChange") or {}
        m5 = txns.get("m5") or {}
        base = pair.get("baseToken") or {}
        created = as_float(pair.get("pairCreatedAt"))
        if created and created > 10_000_000_000:
            created /= 1000
        values = {
            "name": str(base.get("name") or token.name), "symbol": str(base.get("symbol") or token.symbol),
            "created_at": created, "price_usd": as_float(pair.get("priceUsd")),
            "market_cap": as_float(pair.get("marketCap")), "fdv": as_float(pair.get("fdv")),
            "liquidity_usd": as_float(liq.get("usd")), "volume_m5": as_float(volume.get("m5")),
            "volume_h1": as_float(volume.get("h1")), "volume_h6": as_float(volume.get("h6")),
            "volume_h24": as_float(volume.get("h24")), "buys_m5": as_int(m5.get("buys")),
            "sells_m5": as_int(m5.get("sells")), "price_change_m5": as_float(change.get("m5")),
            "price_change_h1": as_float(change.get("h1")), "price_change_h6": as_float(change.get("h6")),
            "price_change_h24": as_float(change.get("h24")), "pair_url": str(pair.get("url") or ""),
        }
        for key, value in values.items():
            if value not in (None, "", "?"):
                setattr(token, key, value)
        token.txns_m5 = (token.buys_m5 or 0) + (token.sells_m5 or 0)
        token.sources[self.name] = Observation(self.name, time.time(), values, str(pair.get("pairAddress") or ""))
        return token

