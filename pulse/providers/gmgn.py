from pulse.models import NormalizedToken

from .base import BaseProvider


class GmgnProvider(BaseProvider):
    name = "gmgn"

    def discover(self) -> list[NormalizedToken]:
        self.health.state = "UNAVAILABLE"
        self.health.detail = "no supported public API configured; no data fabricated"
        return []

