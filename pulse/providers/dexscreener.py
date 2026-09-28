from __future__ import annotations

import time
from collections.abc import Iterable

from pulse.models import NormalizedToken, Observation

from .base import BaseProvider, ProviderError, as_float, as_int, required_mint


class DexScreenerProvider(BaseProvider):
    name = "dexscreener"
    base_url = "https://api.dexscreener.com"
    # Pair/token endpoints are documented at 300 rpm. Keep a little headroom.
    min_interval = 0.21

    _discovery_feeds = (
        ("profile", "/token-profiles/latest/v1"),
        ("boost_latest", "/token-boosts/latest/v1"),
        ("boost_top", "/token-boosts/top/v1"),
    )

    def discover(self) -> list[NormalizedToken]:
        """Use DexScreener as a secondary attention/discovery source.

        Profiles and boosts are promotional/attention signals only. They are
        never interpreted as evidence that a token is safe.
        """
        found: dict[str, NormalizedToken] = {}
        for label, path in self._discovery_feeds:
            try:
                data = self._request_json(
                    "GET",
                    f"{self.base_url}{path}",
                    cache_key=f"discover:{label}",
                )
            except ProviderError:
                # One rotating feed being unavailable should not take the
                # entire DexScreener provider offline.
                continue
            if not isinstance(data, list):
                continue
            observed_at = time.time()
            for row in data:
                if not isinstance(row, dict) or str(row.get("chainId")) != "solana":
                    continue
                try:
                    mint = required_mint(row.get("tokenAddress"))
                except ValueError:
                    continue

                token = found.get(mint)
                fields = {
                    "attention": True,
                    "attention_source": label,
                    "boost_amount": as_float(row.get("amount")),
                    "boost_total_amount": as_float(row.get("totalAmount")),
                }
                if token is None:
                    token = NormalizedToken(mint=mint)
                    found[mint] = token
                    attention_sources: list[str] = []
                else:
                    existing = token.sources.get(self.name)
                    attention_sources = list((existing.fields if existing else {}).get("attention_sources") or [])
                if label not in attention_sources:
                    attention_sources.append(label)
                fields["attention_sources"] = attention_sources

                existing = token.sources.get(self.name)
                merged = dict(existing.fields) if existing else {}
                merged.update({k: v for k, v in fields.items() if v is not None})
                token.sources[self.name] = Observation(
                    self.name,
                    observed_at,
                    merged,
                    mint,
                )
        return list(found.values())

    @staticmethod
    def _token_side(pair: dict, mint: str) -> dict:
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        if str(base.get("address") or "") == mint:
            return base
        if str(quote.get("address") or "") == mint:
            return quote
        return {}

    @staticmethod
    def _pair_created_at(pair: dict) -> float | None:
        created = as_float(pair.get("pairCreatedAt"))
        if created and created > 10_000_000_000:
            created /= 1000
        return created

    @staticmethod
    def _liquidity_usd(pair: dict) -> float:
        return as_float((pair.get("liquidity") or {}).get("usd")) or 0.0

    def _apply_pairs(
        self,
        token: NormalizedToken,
        rows: Iterable[dict],
        *,
        raw_id: str,
    ) -> NormalizedToken:
        # Deduplicate by pairAddress because batch responses can occasionally
        # contain the same pool more than once.
        unique: dict[str, dict] = {}
        anonymous = 0
        for row in rows:
            if not isinstance(row, dict) or str(row.get("chainId")) != "solana":
                continue
            if not self._token_side(row, token.mint):
                continue
            pair_address = str(row.get("pairAddress") or "")
            if not pair_address:
                anonymous += 1
                pair_address = f"anonymous:{anonymous}"
            unique[pair_address] = row

        pairs = list(unique.values())
        if not pairs:
            return token

        primary = max(
            pairs,
            key=lambda p: (
                self._liquidity_usd(p),
                as_float((p.get("volume") or {}).get("h24")) or 0.0,
            ),
        )
        side = self._token_side(primary, token.mint)
        primary_liquidity = self._liquidity_usd(primary)
        total_liquidity = sum(self._liquidity_usd(p) for p in pairs)
        active_pool_count = sum(1 for p in pairs if self._liquidity_usd(p) > 0)

        created_values = [self._pair_created_at(p) for p in pairs]
        created_values = [v for v in created_values if v]
        earliest_pair_created = min(created_values) if created_values else None
        primary_pair_created = self._pair_created_at(primary)

        volume_keys = ("m5", "h1", "h6", "h24")
        aggregate_volume = {
            key: sum(as_float((p.get("volume") or {}).get(key)) or 0.0 for p in pairs)
            for key in volume_keys
        }
        aggregate_buys_m5 = sum(
            as_int(((p.get("txns") or {}).get("m5") or {}).get("buys")) or 0
            for p in pairs
        )
        aggregate_sells_m5 = sum(
            as_int(((p.get("txns") or {}).get("m5") or {}).get("sells")) or 0
            for p in pairs
        )

        change = primary.get("priceChange") or {}
        info = primary.get("info") or {}
        has_socials = bool((info.get("socials") or []) or (info.get("websites") or [])) if isinstance(info, dict) else False
        values = {
            "name": str(side.get("name") or token.name),
            "symbol": str(side.get("symbol") or token.symbol),
            "created_at": earliest_pair_created,
            "price_usd": as_float(primary.get("priceUsd")),
            "market_cap": as_float(primary.get("marketCap")),
            "fdv": as_float(primary.get("fdv")),
            # Keep the scoring/executability field conservative: use the most
            # liquid single pool. Total liquidity is preserved as evidence.
            "liquidity_usd": primary_liquidity or None,
            "total_liquidity_usd": total_liquidity or None,
            "pool_count": len(pairs),
            "active_pool_count": active_pool_count,
            "volume_m5": aggregate_volume["m5"] or None,
            "volume_h1": aggregate_volume["h1"] or None,
            "volume_h6": aggregate_volume["h6"] or None,
            "volume_h24": aggregate_volume["h24"] or None,
            "buys_m5": aggregate_buys_m5 or None,
            "sells_m5": aggregate_sells_m5 or None,
            "price_change_m5": as_float(change.get("m5")),
            "price_change_h1": as_float(change.get("h1")),
            "price_change_h6": as_float(change.get("h6")),
            "price_change_h24": as_float(change.get("h24")),
            "pair_url": str(primary.get("url") or ""),
            "pair_address": str(primary.get("pairAddress") or ""),
            "dex_id": str(primary.get("dexId") or ""),
            "has_socials": has_socials,
            "primary_pair_created_at": primary_pair_created,
            "active_boosts": as_int((primary.get("boosts") or {}).get("active")),
        }

        # Do not replace a real token-creation timestamp from Pump.fun with a
        # later DEX-pair timestamp. If DexScreener is our only age evidence,
        # use the earliest known pair as the approximation.
        if earliest_pair_created:
            if token.created_at is None:
                token.created_at = earliest_pair_created
            else:
                token.created_at = min(token.created_at, earliest_pair_created)

        if token.name in ("", "?") and values["name"] not in ("", "?"):
            token.name = str(values["name"])
        if token.symbol in ("", "?") and values["symbol"] not in ("", "?"):
            token.symbol = str(values["symbol"])

        for key in (
            "price_usd",
            "market_cap",
            "fdv",
            "liquidity_usd",
            "volume_m5",
            "volume_h1",
            "volume_h6",
            "volume_h24",
            "buys_m5",
            "sells_m5",
            "price_change_m5",
            "price_change_h1",
            "price_change_h6",
            "price_change_h24",
            "pair_url",
        ):
            value = values.get(key)
            if value not in (None, "", "?"):
                setattr(token, key, value)

        token.txns_m5 = aggregate_buys_m5 + aggregate_sells_m5
        token.dex_id = str(primary.get("dexId") or "") or token.dex_id
        if has_socials:
            token.has_socials = True

        existing = token.sources.get(self.name)
        merged = dict(existing.fields) if existing else {}
        merged.update({k: v for k, v in values.items() if v not in (None, "", "?")})
        token.sources[self.name] = Observation(
            self.name,
            time.time(),
            merged,
            raw_id or str(primary.get("pairAddress") or ""),
        )
        return token

    def enrich_many(self, tokens: Iterable[NormalizedToken]) -> list[NormalizedToken]:
        """Batch-enrich up to 30 mint addresses per DexScreener request."""
        ordered: list[NormalizedToken] = []
        seen: set[str] = set()
        for token in tokens:
            if token.mint in seen:
                continue
            seen.add(token.mint)
            ordered.append(token)

        for offset in range(0, len(ordered), 30):
            chunk = ordered[offset : offset + 30]
            addresses = ",".join(t.mint for t in chunk)
            try:
                data = self._request_json(
                    "GET",
                    f"{self.base_url}/tokens/v1/solana/{addresses}",
                    cache_key=f"tokens:{addresses}",
                )
                if not isinstance(data, list):
                    raise ValueError("DexScreener batch token response is not a list")
                for token in chunk:
                    self._apply_pairs(
                        token,
                        data,
                        raw_id=f"batch:{token.mint}",
                    )
            except (ProviderError, ValueError):
                # Fallback to the documented per-token pool endpoint so one
                # problematic batch does not eliminate market evidence.
                for token in chunk:
                    try:
                        data = self._request_json(
                            "GET",
                            f"{self.base_url}/token-pairs/v1/solana/{token.mint}",
                            cache_key=f"pairs:{token.mint}",
                        )
                        if isinstance(data, list):
                            self._apply_pairs(token, data, raw_id=token.mint)
                    except ProviderError:
                        continue
        return ordered

    def enrich(self, token: NormalizedToken) -> NormalizedToken:
        data = self._request_json(
            "GET",
            f"{self.base_url}/token-pairs/v1/solana/{token.mint}",
            cache_key=f"pairs:{token.mint}",
        )
        if not isinstance(data, list):
            raise ValueError("DexScreener token-pairs response is not a list")
        return self._apply_pairs(token, data, raw_id=token.mint)
