"""Client for the official Semantic Scholar Academic Graph and Recommendations APIs.

Features: optional API key (``x-api-key`` header, never logged), client-side
pacing, exponential backoff with jitter, ``Retry-After`` handling, SQLite
response caching with expiry, batching, and pagination helpers.
Only documented public endpoints are used; there is no access to private
website libraries or Research Feeds.
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

import httpx

from paperscout.config import Settings
from paperscout.models import Paper

log = logging.getLogger("paperscout.s2")

# Fields requested from search/recommendation endpoints. ``fieldsOfStudy`` is
# documented as slated for deprecation, so ``s2FieldsOfStudy`` is used instead.
SEARCH_FIELDS = (
    "paperId,corpusId,externalIds,url,title,abstract,venue,year,publicationDate,publicationTypes,"
    "s2FieldsOfStudy,citationCount,influentialCitationCount,referenceCount,isOpenAccess,openAccessPdf,authors"
)
# Paper details / batch additionally support the machine-generated TLDR.
DETAIL_FIELDS = SEARCH_FIELDS + ",tldr"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
BATCH_LIMIT = 500


class S2Error(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class S2NotFound(S2Error):
    pass


class S2BadRequest(S2Error):
    pass


class S2RateLimited(S2Error):
    pass


def search_query_text(text: str) -> str:
    """Prepare free text for /paper/search, which documents that
    "Hyphenated query terms yield no matches": hyphens become spaces."""
    cleaned = text.replace("‐", "-").replace("‑", "-").replace("–", "-")
    return " ".join(cleaned.replace("-", " ").split())


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


class SemanticScholarClient:
    def __init__(
        self,
        settings: Settings,
        cache: Any | None = None,  # object with cache_get/cache_put (the Store)
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.cache = cache
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None
        headers = {"User-Agent": "paperscout/0.1 (personal research assistant)"}
        if settings.s2_api_key:
            headers["x-api-key"] = settings.s2_api_key
        self._http = httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(settings.s2_timeout, connect=10.0),
            transport=transport,
        )
        self.network_calls = 0
        self.cache_hits = 0
        self.sources_used: list[str] = []

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ core request
    def _pace(self) -> None:
        if self._last_request is None:
            return
        wait = self.settings.s2_min_interval - (self._monotonic() - self._last_request)
        if wait > 0:
            self._sleep(wait)

    @staticmethod
    def _cache_key(method: str, url: str, params: dict | None, body: Any) -> str:
        blob = json.dumps([method, url, sorted((params or {}).items()), body], sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        ttl: int | None = None,
        refresh: bool = False,
        source: str = "s2",
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = self._cache_key(method, url, params, body)
        self.sources_used.append(source)
        if self.cache is not None and ttl and not refresh:
            cached = self.cache.cache_get(key)
            if cached is not None:
                self.cache_hits += 1
                log.debug("cache hit %s %s", method, url)
                return json.loads(cached)

        attempts = self.settings.s2_max_retries + 1
        last_error: S2Error | None = None
        for attempt in range(attempts):
            self._pace()
            self._last_request = self._monotonic()
            self.network_calls += 1
            try:
                response = self._http.request(method, url, params=params, json=body)
            except httpx.TransportError as exc:
                last_error = S2Error(f"network error contacting Semantic Scholar: {type(exc).__name__}")
                delay = self._backoff(attempt, None)
                log.warning("transport error on %s (attempt %d/%d); retrying in %.1fs", url, attempt + 1, attempts, delay)
                if attempt + 1 < attempts:
                    self._sleep(delay)
                continue

            status = response.status_code
            log.info("%s %s -> %d (attempt %d)", method, url, status, attempt + 1)
            if status == 200:
                data = response.json()
                if self.cache is not None and ttl:
                    self.cache.cache_put(key, url, json.dumps(data), ttl)
                return data
            if status == 404:
                raise S2NotFound(self._error_message(response), status)
            if status == 400:
                raise S2BadRequest(self._error_message(response), status)
            if status in (401, 403):
                hint = "check S2_API_KEY" if self.settings.s2_api_key else "this endpoint may require an API key"
                raise S2Error(f"{self._error_message(response)} ({hint})", status)
            if status in RETRYABLE_STATUS:
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
                cls = S2RateLimited if status == 429 else S2Error
                last_error = cls(self._error_message(response), status)
                if retry_after is not None and retry_after > self.settings.s2_max_backoff:
                    raise cls(
                        f"Semantic Scholar asked to wait {retry_after:.0f}s (Retry-After); giving up for now. "
                        "Try again later or configure S2_API_KEY.",
                        status,
                    )
                delay = self._backoff(attempt, retry_after)
                if attempt + 1 < attempts:
                    log.warning("HTTP %d on %s; retrying in %.1fs", status, url, delay)
                    self._sleep(delay)
                continue
            raise S2Error(self._error_message(response), status)

        assert last_error is not None
        if isinstance(last_error, S2RateLimited):
            raise S2RateLimited(
                f"Rate limited by Semantic Scholar after {attempts} attempts. "
                "Unauthenticated requests share a public pool; wait a minute or set S2_API_KEY.",
                429,
            )
        raise last_error

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self.settings.s2_max_backoff)
        base = min(self.settings.s2_max_backoff, 2.0 * (2**attempt))
        return base + random.uniform(0, 0.5)

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
            detail = payload.get("error") or payload.get("message") or str(payload)
        except (ValueError, AttributeError):
            detail = response.text[:200]
        return f"Semantic Scholar HTTP {response.status_code}: {detail}"

    # ------------------------------------------------------------------ graph endpoints
    def _graph(self, path: str) -> str:
        return self.settings.s2_graph_base.rstrip("/") + path

    def _recs(self, path: str) -> str:
        return self.settings.s2_recs_base.rstrip("/") + path

    def search(
        self,
        query: str,
        *,
        limit: int = 50,
        offset: int = 0,
        publication_date_or_year: str | None = None,
        year: str | None = None,
        fields_of_study: str | None = None,
        publication_types: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Relevance-ranked search (GET /paper/search). Returns the raw page."""
        params = {
            "query": search_query_text(query),
            "fields": SEARCH_FIELDS,
            "limit": min(limit, 100),
            "offset": offset,
            "publicationDateOrYear": publication_date_or_year,
            "year": year,
            "fieldsOfStudy": fields_of_study,
            "publicationTypes": publication_types,
        }
        return self.request(
            "GET", self._graph("/paper/search"), params=params, ttl=self.settings.cache_ttl.search,
            refresh=refresh, source="s2.search",
        )

    def search_papers(self, query: str, *, max_results: int = 50, **kwargs: Any) -> list[tuple[int, Paper]]:
        """Paginate relevance search; returns (1-based search rank, paper)."""
        results: list[tuple[int, Paper]] = []
        offset = 0
        while len(results) < max_results:
            page = self.search(query, limit=min(100, max_results - len(results)), offset=offset, **kwargs)
            data = page.get("data") or []
            for position, item in enumerate(data, start=offset + 1):
                if item.get("paperId"):
                    results.append((position, Paper.from_s2(item)))
            next_offset = page.get("next")
            if not data or next_offset is None or next_offset <= offset:
                break
            offset = next_offset
        return results[:max_results]

    def match_title(self, title: str) -> dict[str, Any] | None:
        """Closest title match (GET /paper/search/match). None when nothing matches."""
        try:
            data = self.request(
                "GET", self._graph("/paper/search/match"),
                params={"query": title, "fields": SEARCH_FIELDS},
                ttl=self.settings.cache_ttl.search, source="s2.match",
            )
        except S2NotFound:
            return None
        items = data.get("data") or []
        return items[0] if items else None

    def get_paper(self, paper_id: str, *, refresh: bool = False) -> Paper:
        data = self.request(
            "GET", self._graph(f"/paper/{paper_id}"), params={"fields": DETAIL_FIELDS},
            ttl=self.settings.cache_ttl.paper, refresh=refresh, source="s2.paper",
        )
        return Paper.from_s2(data)

    def get_papers(self, paper_ids: Iterable[str], *, refresh: bool = False) -> list[Paper | None]:
        """POST /paper/batch in chunks; result is aligned with input (None = not found)."""
        ids = list(paper_ids)
        out: list[Paper | None] = []
        for start in range(0, len(ids), BATCH_LIMIT):
            chunk = ids[start : start + BATCH_LIMIT]
            data = self.request(
                "POST", self._graph("/paper/batch"), params={"fields": DETAIL_FIELDS}, body={"ids": chunk},
                ttl=self.settings.cache_ttl.paper, refresh=refresh, source="s2.batch",
            )
            out.extend(Paper.from_s2(item) if item and item.get("paperId") else None for item in data)
        return out

    def references(self, paper_id: str, *, limit: int = 100) -> list[Paper]:
        data = self.request(
            "GET", self._graph(f"/paper/{paper_id}/references"),
            params={"fields": SEARCH_FIELDS, "limit": min(limit, 1000)},
            ttl=self.settings.cache_ttl.references, source="s2.references",
        )
        return [
            Paper.from_s2(item["citedPaper"])
            for item in data.get("data") or []
            if item.get("citedPaper") and item["citedPaper"].get("paperId")
        ]

    # ------------------------------------------------------------------ recommendations
    def recommend_for_paper(self, paper_id: str, *, limit: int = 20, pool: str = "recent") -> list[Paper]:
        data = self.request(
            "GET", self._recs(f"/papers/forpaper/{paper_id}"),
            params={"fields": SEARCH_FIELDS, "limit": min(limit, 500), "from": pool},
            ttl=self.settings.cache_ttl.recommendations, source="s2.recommendations",
        )
        return [Paper.from_s2(p) for p in data.get("recommendedPapers") or [] if p.get("paperId")]

    def recommend(self, positive: list[str], negative: list[str] | None = None, *, limit: int = 20) -> list[Paper]:
        data = self.request(
            "POST", self._recs("/papers/"),
            params={"fields": SEARCH_FIELDS, "limit": min(limit, 500)},
            body={"positivePaperIds": positive, "negativePaperIds": negative or []},
            ttl=self.settings.cache_ttl.recommendations, source="s2.recommendations",
        )
        return [Paper.from_s2(p) for p in data.get("recommendedPapers") or [] if p.get("paperId")]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
