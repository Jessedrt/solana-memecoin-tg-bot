from pulse.models import NormalizedToken

from .base import BaseProvider


class FomoProvider(BaseProvider):
    name = "fomo"

    def discover(self) -> list[NormalizedToken]:
        self.health.state = "UNAVAILABLE"
        self.health.detail = "no supported public API configured; no data fabricated"
        return []

