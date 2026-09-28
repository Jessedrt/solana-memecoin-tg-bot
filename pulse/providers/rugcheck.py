from __future__ import annotations

import time

from pulse.models import NormalizedToken, Observation

from .base import BaseProvider, as_float


class RugCheckProvider(BaseProvider):
    name = "rugcheck"

    def enrich(self, token: NormalizedToken) -> NormalizedToken:
        token.holder_proxy_clear = None
        token.rugcheck_danger = None
        data = self._request_json("GET", f"https://api.rugcheck.xyz/v1/tokens/{token.mint}/report",
                                  cache_key=token.mint)
        if not isinstance(data, dict) or not isinstance(data.get("risks"), list):
            raise ValueError("Incomplete RugCheck report")
        if data.get("mint") != token.mint:
            raise ValueError("RugCheck mint mismatch")
        risks = data["risks"]
        if any(not isinstance(r, dict) or not isinstance(r.get("level"), str) for r in risks):
            raise ValueError("Incomplete RugCheck risk levels")
        token.rugcheck_danger = any(r["level"].lower() == "danger" for r in risks)
        if isinstance(data.get("rugged"), bool):
            token.rugged = token.rugged is True or data["rugged"]
        # RPC is authoritative for revocation. RugCheck can add a failure,
        # but cannot overwrite a live authority or certify missing RPC data.
        mint_info = data.get("token") or {}
        for key, attr in (("mintAuthority", "mint_authority_active"), ("freezeAuthority", "freeze_authority_active")):
            if mint_info.get(key):
                setattr(token, attr, True)
        holders = data.get("topHolders")
        holder_count = 0
        if isinstance(holders, list) and holders:
            valid = [h for h in holders if isinstance(h, dict) and as_float(h.get("pct")) is not None]
            if len(valid) == len(holders):
                pcts = sorted((float(h["pct"]) for h in valid), reverse=True)
                if all(0 <= p <= 100 for p in pcts):
                    token.top10_pct = sum(pcts[:10])
                    token.top20_pct = sum(pcts[:20])
                    token.largest_holder_pct = pcts[0]
                    holder_count = len(valid)
                    top = sorted(valid, key=lambda h: float(h["pct"]), reverse=True)[:10]
                    owners = {h.get("owner") for h in top}
                    if len(owners) == 1 and None not in owners and "" not in owners and len(top) > 1:
                        token.holder_proxy_clear = False
                    if (len(top) == 10 and all(isinstance(h.get("owner"), str) and h["owner"]
                                               and isinstance(h.get("insider"), bool) for h in valid)):
                        by_owner: dict[str, float] = {}
                        for h in valid:
                            by_owner[h["owner"]] = by_owner.get(h["owner"], 0) + float(h["pct"])
                        insider_pct = sum(float(h["pct"]) for h in valid if h["insider"])
                        token.holder_proxy_clear = (len(owners) > 1 and max(by_owner.values()) <= 30
                                                    and insider_pct <= 35 and sum(pcts[:10]) <= 60)
                    # This is an observed holder/insider proxy, never a claim
                    # that distinct owners have independent funding.
        token.sources[self.name] = Observation(self.name, time.time(), {
            "danger": token.rugcheck_danger, "risk_names": [str(r.get("name", "")) for r in risks],
            "holder_count": holder_count, "holder_proxy_clear": token.holder_proxy_clear,
            "cluster_coverage": "holder/insider concentration proxy; funding relationships unverified",
        }, token.mint)
        return token
