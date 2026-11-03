"""Shared HTTP client behaviour for every paper provider (Semantic Scholar, arXiv).

One implementation of: client-side pacing, a hard per-command request budget,
bounded retries with exponential backoff and ``Retry-After`` handling, SQLite
response caching with expiry, and sanitized diagnostics (never request headers,
never credentials).
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Callable

import httpx

log = logging.getLogger("paperscout.http")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
USER_AGENT = "paperscout/0.1 (personal research assistant; https://github.com/ArthurgLeonida/ai-paper-search)"


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ApiNotFound(ApiError):
    pass


class ApiBadRequest(ApiError):
    pass


class ApiRateLimited(ApiError):
    pass


class ApiBudgetExceeded(ApiError):
    """The per-command request budget is spent; nothing further is sent."""


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date)."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now if now is not None else time.time()
    return max(0.0, parsed.timestamp() - current)


@dataclass(frozen=True)
class RequestPolicy:
    provider: str  # display name used in messages
    budget_setting: str  # environment variable that controls max_requests
    min_interval: float
    max_retries: int
    max_backoff: float
    timeout: float
    max_requests: int


class ApiClient:
    """Base class; subclasses add endpoints and provider-specific hints."""

    def __init__(
        self,
        policy: RequestPolicy,
        headers: dict[str, str],
        cache: Any | None = None,  # object with cache_get/cache_put (the Store)
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.policy = policy
        self.cache = cache
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None
        self._http = httpx.Client(
            headers={"User-Agent": USER_AGENT, **headers},
            timeout=httpx.Timeout(policy.timeout, connect=10.0),
            transport=transport,
        )
        self.network_calls = 0
        self.cache_hits = 0
        self.sources_used: list[str] = []
        self.diagnostics: list[dict[str, Any]] = []

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ hooks
    def _auth_hint(self) -> str:
        return "this endpoint may require authentication"

    def _rate_limit_advice(self) -> str:
        return "Wait and try again later."

    def _error_message(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
            detail = payload.get("error") or payload.get("message") or str(payload)
        except (ValueError, AttributeError):
            detail = response.text[:200]
        return f"{self.policy.provider} HTTP {response.status_code}: {detail}"

    # ------------------------------------------------------------------ core
    def _pace(self) -> None:
        if self._last_request is None:
            return
        wait = self.policy.min_interval - (self._monotonic() - self._last_request)
        if wait > 0:
            self._sleep(wait)

    @staticmethod
    def _cache_key(method: str, url: str, params: dict | None, body: Any) -> str:
        blob = json.dumps([method, url, sorted((params or {}).items()), body], sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self.policy.max_backoff)
        base = min(self.policy.max_backoff, 2.0 * (2**attempt))
        return base + random.uniform(0, 0.5)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        ttl: int | None = None,
        refresh: bool = False,
        source: str = "api",
        parse: str = "json",  # "json" or "text"
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = self._cache_key(method, url, params, body)
        self.sources_used.append(source)
        if self.cache is not None and ttl and not refresh:
            cached = self.cache.cache_get(key)
            if cached is not None:
                self.cache_hits += 1
                log.debug("cache hit %s %s", method, url)
                return json.loads(cached) if parse == "json" else cached

        provider = self.policy.provider
        attempts = self.policy.max_retries + 1
        endpoint = httpx.URL(url).path
        last_error: ApiError | None = None
        waited = 0.0
        made = 0
        for attempt in range(attempts):
            if self.network_calls >= self.policy.max_requests:
                raise ApiBudgetExceeded(
                    f"Request budget of {self.policy.max_requests} HTTP attempts to {provider} for this command is "
                    f"spent ({self.policy.budget_setting}); stopped before {method} {endpoint}. "
                    "Nothing further was sent."
                )
            self._pace()
            self._last_request = self._monotonic()
            self.network_calls += 1
            made += 1
            try:
                response = self._http.request(method, url, params=params, json=body)
            except httpx.TransportError as exc:
                last_error = ApiError(f"network error contacting {provider}: {type(exc).__name__}")
                self.diagnostics.append({"method": method, "endpoint": endpoint, "status": None,
                                         "error_type": type(exc).__name__})
                delay = self._backoff(attempt, None)
                log.warning("transport error on %s (attempt %d/%d); retrying in %.1fs", endpoint, attempt + 1,
                            attempts, delay)
                if attempt + 1 < attempts:
                    self._sleep(delay)
                    waited += delay
                continue

            status = response.status_code
            self.diagnostics.append({
                "method": method,
                "endpoint": endpoint,
                "status": status,
                "error_type": response.headers.get("x-amzn-ErrorType"),
                "retry_after": response.headers.get("Retry-After"),
                "request_id": response.headers.get("x-amzn-RequestId"),
            })
            log.info("%s %s -> %d (attempt %d)", method, endpoint, status, attempt + 1)
            if status == 200:
                data = response.json() if parse == "json" else response.text
                if self.cache is not None and ttl:
                    self.cache.cache_put(key, url, json.dumps(data) if parse == "json" else data, ttl)
                return data
            if status == 404:
                raise ApiNotFound(self._error_message(response), status)
            if status == 400:
                raise ApiBadRequest(self._error_message(response), status)
            if status in (401, 403):
                raise ApiError(f"{self._error_message(response)} ({self._auth_hint()})", status)
            if status in RETRYABLE_STATUS:
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
                cls = ApiRateLimited if status == 429 else ApiError
                last_error = cls(self._error_message(response), status)
                if retry_after is not None and retry_after > self.policy.max_backoff:
                    raise cls(
                        f"{provider} asked to wait {retry_after:.0f}s (Retry-After); giving up for now. "
                        f"{self._rate_limit_advice()}",
                        status,
                    )
                delay = self._backoff(attempt, retry_after)
                if attempt + 1 < attempts:
                    log.warning("HTTP %d on %s; retrying in %.1fs", status, endpoint, delay)
                    self._sleep(delay)
                    waited += delay
                continue
            raise ApiError(self._error_message(response), status)

        assert last_error is not None
        if isinstance(last_error, ApiRateLimited):
            raise ApiRateLimited(self._rate_limit_message(method, endpoint, made, waited), 429)
        raise last_error

    def _rate_limit_message(self, method: str, endpoint: str, made: int, waited: float) -> str:
        last = self.diagnostics[-1] if self.diagnostics else {}
        return (
            f"Rate limited by {self.policy.provider}: HTTP 429 (error type: {last.get('error_type') or 'not given'}) "
            f"on {method} {endpoint}; {made} attempt(s) over {waited:.0f}s of backoff; "
            f"Retry-After: {last.get('retry_after') or 'absent'}; last request id: {last.get('request_id') or 'n/a'}. "
            f"{self._rate_limit_advice()} Nothing further was sent."
        )
