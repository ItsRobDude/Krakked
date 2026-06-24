"""Public-only Kraken Futures market-data client."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import requests
from requests import HTTPError, RequestException, Timeout

from krakked.connection.exceptions import (
    KrakenAPIError,
    RateLimitError,
    ServiceUnavailableError,
)
from krakked.connection.rate_limiter import RateLimiter

KRAKEN_FUTURES_API_URL = "https://futures.kraken.com"


class KrakenFuturesPublicClient:
    """Tiny public-only Kraken Futures client for research probes.

    This class intentionally has no API-key, nonce, signing, or private endpoint
    support. It is only for public market-data feasibility checks.
    """

    def __init__(
        self,
        *,
        api_url: str = KRAKEN_FUTURES_API_URL,
        calls_per_second: float = 0.5,
        request_timeout: float = 10.0,
        rate_limiter: RateLimiter | None = None,
        session: requests.Session | None = None,
        raw_cache_dir: str | Path | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.request_timeout = request_timeout
        self.rate_limiter = rate_limiter or RateLimiter(calls_per_second)
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "KrakkedFundingBasisProbe/0.1.0"})
        self.raw_cache_dir = (
            Path(raw_cache_dir).expanduser().resolve() if raw_cache_dir else None
        )

    def get_instruments(self) -> dict[str, Any]:
        return self._get_json("/derivatives/api/v3/instruments")

    def get_tickers(self) -> dict[str, Any]:
        return self._get_json("/derivatives/api/v3/tickers")

    def get_historical_funding_rates(self, symbol: str) -> dict[str, Any]:
        return self._get_json(
            "/derivatives/api/v3/historical-funding-rates",
            params={"symbol": symbol},
        )

    def get_candles(
        self,
        *,
        tick_type: str,
        symbol: str,
        interval: str,
        start: int,
        end: int,
        count: int = 5000,
    ) -> dict[str, Any]:
        return self._get_json(
            f"/api/charts/v1/{tick_type}/{symbol}/{interval}",
            params={"from": start, "to": end, "count": count},
        )

    def _get_json(
        self, path: str, *, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        cache_path = self._cache_path(path, params or {})
        if cache_path and cache_path.exists():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
            raise KrakenAPIError(
                f"Cached Futures response is not an object: {cache_path}"
            )

        self.rate_limiter.wait()
        url = f"{self.api_url}{path}"
        try:
            response = self.session.get(
                url,
                params=dict(params or {}),
                timeout=self.request_timeout,
            )
            if response.status_code == 429:
                raise RateLimitError("Kraken Futures rate limit exceeded")
            if 500 <= response.status_code < 600:
                body_preview = response.text[:200] if response.text else "No body"
                raise ServiceUnavailableError(
                    f"Kraken Futures API Service Error: HTTP {response.status_code} - {body_preview}"
                )
            response.raise_for_status()
            payload = response.json()
        except HTTPError as exc:
            raise KrakenAPIError(f"Kraken Futures HTTP Error: {exc}") from exc
        except Timeout as exc:
            raise ServiceUnavailableError(
                f"Kraken Futures request timed out: {exc}"
            ) from exc
        except RequestException as exc:
            raise ServiceUnavailableError(
                f"Kraken Futures network error: {exc}"
            ) from exc
        except ValueError as exc:
            raise KrakenAPIError(f"Kraken Futures response is not JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise KrakenAPIError("Kraken Futures response root is not an object")
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
        return payload

    def _cache_path(self, path: str, params: Mapping[str, Any]) -> Path | None:
        if self.raw_cache_dir is None:
            return None
        key_payload = json.dumps(
            {"path": path, "params": dict(sorted(params.items()))},
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()[:24]
        safe_name = path.strip("/").replace("/", "_") or "root"
        return self.raw_cache_dir / f"{safe_name}-{digest}.json"
