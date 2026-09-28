from __future__ import annotations

from .models import NormalizedToken


NEW_TOKEN_DEXES = {"pumpfun"}
ABOUT_TO_GRADUATE_DEXES = {"bags", "bonkfun", "pumpfun"}
MIGRATED_DEXES = {"pumpfun", "moonshot", "launchlab", "bonkfun", "bonkers", "launchcoin"}


def _source_name(token: NormalizedToken) -> str:
    launchpad = (token.launchpad or "").strip().lower()
    if launchpad:
        return launchpad
    dex_id = (token.dex_id or "").strip().lower()
    if dex_id:
        return dex_id
    if "pumpfun" in token.sources:
        return "pumpfun"
    return ""


def _volume(token: NormalizedToken) -> float:
    """Best available market-volume observation for preset matching."""
    values = [
        value
        for value in (token.volume_m5, token.volume_h1, token.volume_h6, token.volume_h24)
        if value is not None
    ]
    return max(values, default=0.0)


def assign_discovery_lane(token: NormalizedToken) -> str | None:
    """Map a token into one of the three scanner presets.

    Gas-fee filtering is intentionally not implemented because the current
    providers do not expose a trustworthy equivalent of the supplied gas-fee
    filter. Missing data is not fabricated.
    """
    source = _source_name(token)
    mc = token.market_cap or 0.0
    volume = _volume(token)
    age = token.age_minutes

    migrated_match = (
        token.migrated is True
        and source in MIGRATED_DEXES
        and mc >= 40_000
    )
    if migrated_match:
        token.discovery_lane = "MIGRATED"
        return token.discovery_lane

    near_match = (
        token.migrated is not True
        and source in ABOUT_TO_GRADUATE_DEXES
        and token.holder_count is not None
        and token.holder_count >= 30
        and token.sniper_pct is not None
        and token.sniper_pct <= 15
        and volume >= 15_000
        and 9_000 <= mc <= 34_000
        and age is not None
        and age <= 333
    )
    if near_match:
        token.discovery_lane = "ABOUT_TO_GRADUATE"
        return token.discovery_lane

    new_match = (
        token.migrated is not True
        and source in NEW_TOKEN_DEXES
        and token.has_socials is True
        and token.creator_pct is not None
        and token.creator_pct <= 3
        and token.sniper_pct is not None
        and token.sniper_pct <= 5
        and volume >= 1_690
        and mc >= 7_000
    )
    if new_match:
        token.discovery_lane = "NEW_TOKEN"
        return token.discovery_lane

    token.discovery_lane = None
    return None
