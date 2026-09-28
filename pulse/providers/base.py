from __future__ import annotations

import logging
import threading
import time
from abc import ABC
from typing import Any

import requests

from pulse.models import NormalizedToken, ProviderHealth

log = logging.getLogger("pulse.providers")


class ProviderError(RuntimeError):
    pass


class BaseProvider(ABC):
    name = "base"
    timeout = 8
    retries = 2
    min_interval = 0.2
    cache_ttl = 30

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.health = ProviderHealth(self.name)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._last_call = 0.0

    def _request_json(self, method: str, url: str, *, cache_key: str = "", **kwargs: Any) -> Any:
        now = time.time()
        if cache_key and cache_key in self._cache:
            ts, value = self._cache[cache_key]
            if now - ts <= self.cache_ttl:
                return value
        error: Exception | None = None
        started = time.monotonic()
        for attempt in range(self.retries + 1):
            try:
                with self._lock:
                    delay = self.min_interval - (time.monotonic() - self._last_call)
                    if delay > 0:
                        time.sleep(delay)
                    self._last_call = time.monotonic()
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
                response.raise_for_status()
                value = response.json()
                if cache_key:
                    self._cache[cache_key] = (time.time(), value)
                self.health.mark_success(int((time.monotonic() - started) * 1000))
                return value
            except (requests.RequestException, ValueError) as exc:
                error = exc
                if attempt < self.retries:
                    time.sleep(0.2 * (2 ** attempt))
        detail = f"{type(error).__name__}: {error}"
        self.health.mark_failure(detail)
        raise ProviderError(f"{self.name}: {detail}") from error

    def discover(self) -> list[NormalizedToken]:
        return []

    def enrich(self, token: NormalizedToken) -> NormalizedToken:
        return token


def as_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def required_mint(value: Any) -> str:
    mint = str(value or "").strip()
    if not (32 <= len(mint) <= 50) or any(c.isspace() for c in mint):
        raise ValueError("invalid Solana mint")
    return mint

