"""Client for the official Semantic Scholar Academic Graph and Recommendations APIs.

The optional API key is sent as the ``x-api-key`` header and never logged.
Pacing, budget, retries, caching and diagnostics come from
``paperscout.http.ApiClient``. Only documented public endpoints are used;
there is no access to private website libraries or Research Feeds.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable

import httpx

from paperscout.config import S2_KEY_FORM, Settings
from paperscout.http import ApiClient, ApiNotFound, RequestPolicy
from paperscout.models import Paper

# Fields requested from search/recommendation endpoints. ``fieldsOfStudy`` is
# documented as slated for deprecation, so ``s2FieldsOfStudy`` is used instead.
SEARCH_FIELDS = (
    "paperId,corpusId,externalIds,url,title,abstract,venue,year,publicationDate,publicationTypes,"
    "s2FieldsOfStudy,citationCount,influentialCitationCount,referenceCount,isOpenAccess,openAccessPdf,authors"
)
# Paper details / batch additionally support the machine-generated TLDR.
DETAIL_FIELDS = SEARCH_FIELDS + ",tldr"
BATCH_LIMIT = 500


def search_query_text(text: str) -> str:
    """Prepare free text for /paper/search, which documents that
    "Hyphenated query terms yield no matches": hyphens become spaces."""
    cleaned = text.replace("‐", "-").replace("‑", "-").replace("–", "-")
    return " ".join(cleaned.replace("-", " ").split())


class SemanticScholarClient(ApiClient):
    def __init__(
        self,
        settings: Settings,
        cache: Any | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        policy = RequestPolicy(
            provider="Semantic Scholar",
            budget_setting="PAPERSCOUT_S2_MAX_REQUESTS",
            min_interval=settings.s2_min_interval,
            max_retries=settings.s2_max_retries,
            max_backoff=settings.s2_max_backoff,
            timeout=settings.s2_timeout,
            max_requests=settings.s2_max_requests,
        )
        headers = {"x-api-key": settings.s2_api_key} if settings.s2_api_key else {}
        super().__init__(policy, headers, cache, transport, sleep, monotonic)

    def _auth_hint(self) -> str:
        return "check S2_API_KEY" if self.settings.s2_api_key else "this endpoint may require an API key"

    def _rate_limit_advice(self) -> str:
        if self.settings.s2_api_key:
            return ("API key: configured (sent as x-api-key; documented introductory limit 1 request/second). "
                    "Wait and retry; if this persists, check the key's status with Semantic Scholar.")
        return ("API key: not configured. Unauthenticated requests share one public pool that is often throttled. "
                f"Request a key at {S2_KEY_FORM} and set S2_API_KEY in .env, or use the arXiv provider "
                "(PAPERSCOUT_PROVIDER=arxiv).")

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
        except ApiNotFound:
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
    def recommend(self, positive: list[str], negative: list[str] | None = None, *, limit: int = 20) -> list[Paper]:
        data = self.request(
            "POST", self._recs("/papers/"),
            params={"fields": SEARCH_FIELDS, "limit": min(limit, 500)},
            body={"positivePaperIds": positive, "negativePaperIds": negative or []},
            ttl=self.settings.cache_ttl.recommendations, source="s2.recommendations",
        )
        return [Paper.from_s2(p) for p in data.get("recommendedPapers") or [] if p.get("paperId")]

