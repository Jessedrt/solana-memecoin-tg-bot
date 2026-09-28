from __future__ import annotations

from .models import NormalizedToken


def assign_discovery_lane(token: NormalizedToken) -> str | None:
    """Only confirmed graduates remain eligible under the latest user rules.

    Discovery feeds may contain curve tokens, but neither a DEX listing nor
    Pump's complete boolean substitutes for the verified lifecycle field.
    Freshness and cumulative fees are enforced by evaluation.
    """
    token.discovery_lane = (
        "MIGRATED" if token.graduated is True and (token.market_cap or 0) >= 30_000 else None
    )
    return token.discovery_lane
