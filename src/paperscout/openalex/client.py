"""Citation counts from OpenAlex (https://docs.openalex.org), used when the paper provider has none.

arXiv returns no citation counts, so paperscout asks OpenAlex for them. Only
paper identifiers (DOIs, including arXiv's 10.48550/arXiv.* DOIs) are sent, in
batches, without a key or an email address. OpenAlex metadata is CC0.

OpenAlex often answers an arXiv DOI with the merged record of the published
version, whose own DOI differs, so results are matched back through every
location URL (arxiv.org/abs/<id>, doi.org/<doi>) and, failing that, the title.
Sometimes the preprint and the published version stay separate records and the
DOI lookup finds only the preprint, with few citations; ``title_counts`` checks
such papers by exact title (a title search costs ten times a DOI lookup against
the keyless daily allowance, so callers check only a few).
Response text is data: nothing in it is followed as an instruction.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from paperscout.config import Settings
from paperscout.http import ApiClient, RequestPolicy
from paperscout.identifiers import normalize_title
from paperscout.models import Paper

BATCH = 50
SELECT = "id,doi,title,cited_by_count,locations"
TITLE_SELECT = "id,doi,title,cited_by_count,publication_year"
_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$", re.IGNORECASE)
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class CitationMatch:
    count: int
    preprint_only: bool  # only an arXiv preprint record matched; a separate published record may exist


def bare_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value.strip(), flags=re.IGNORECASE)
    return value.lower() or None


def query_doi(paper: Paper) -> str | None:
    """The DOI to ask OpenAlex for: the paper's own, or the DOI arXiv mints for every preprint."""
    if paper.doi:
        return paper.doi
    if paper.arxiv_id:
        return f"10.48550/arxiv.{paper.arxiv_id.lower()}"
    return None


def work_keys(work: dict[str, Any]) -> set[str]:
    """Every identifier a work answers to: its DOIs and the arXiv IDs of its locations."""
    keys: set[str] = set()
    urls = [work.get("doi")] + [
        (loc or {}).get(field) for loc in (work.get("locations") or []) for field in ("landing_page_url", "pdf_url")
    ]
    for url in urls:
        if not url:
            continue
        match = _ARXIV_URL.search(url)
        if match:
            keys.add(f"arxiv:{match.group(1).lower()}")
            continue
        doi = bare_doi(url) if "doi.org/" in url.lower() else None
        if doi:
            keys.add(f"doi:{doi}")
            arxiv = _ARXIV_DOI.match(doi)
            if arxiv:
                keys.add(f"arxiv:{arxiv.group(1).lower()}")
    return keys


def paper_keys(paper: Paper) -> set[str]:
    keys = set()
    if paper.doi:
        keys.add(f"doi:{paper.doi}")
        arxiv = _ARXIV_DOI.match(paper.doi)
        if arxiv:
            keys.add(f"arxiv:{arxiv.group(1).lower()}")
    if paper.arxiv_id:
        keys.add(f"arxiv:{paper.arxiv_id.lower()}")
    return keys


class OpenAlexClient(ApiClient):
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
            provider="OpenAlex",
            budget_setting="PAPERSCOUT_OPENALEX_MAX_REQUESTS",
            min_interval=0.2,
            max_retries=1,
            max_backoff=10.0,
            timeout=20.0,
            max_requests=settings.openalex_max_requests,
        )
        super().__init__(policy, {}, cache, transport, sleep, monotonic)

    def _rate_limit_advice(self) -> str:
        return ("Keyless OpenAlex access has a small daily allowance per IP address; popularity stays unknown until "
                "it resets.")

    def citation_counts(self, papers: list[Paper]) -> dict[str, CitationMatch]:
        """paper_id -> OpenAlex cited_by_count, for the papers OpenAlex knows. Unmatched papers are absent."""
        wanted = [(paper, doi) for paper in papers if (doi := query_doi(paper))]
        counts: dict[str, CitationMatch] = {}
        for start in range(0, len(wanted), BATCH):
            chunk = wanted[start:start + BATCH]
            data = self.request(
                "GET", f"{self.settings.openalex_base}/works",
                params={"filter": "doi:" + "|".join(doi for _, doi in chunk), "select": SELECT,
                        "per-page": 200},
                ttl=self.settings.cache_ttl.paper, source="openalex.works",
            )
            works = [w for w in (data.get("results") or []) if isinstance(w, dict)]
            by_key: dict[str, list[dict]] = {}
            by_title: dict[str, list[dict]] = {}
            for work in works:
                if not isinstance(work.get("cited_by_count"), int):
                    continue
                for key in work_keys(work):
                    by_key.setdefault(key, []).append(work)
                if work.get("title"):
                    by_title.setdefault(normalize_title(work["title"]), []).append(work)
            for paper, _ in chunk:
                matched = [w for k in paper_keys(paper) for w in by_key.get(k, [])]
                matched = matched or by_title.get(normalize_title(paper.title), [])
                if matched:
                    preprint_only = all(_ARXIV_DOI.match(bare_doi(w.get("doi")) or "") for w in matched)
                    counts[paper.paper_id] = CitationMatch(max(w["cited_by_count"] for w in matched), preprint_only)
        return counts

    def title_counts(self, papers: list[Paper]) -> dict[str, int]:
        """paper_id -> the highest cited_by_count among works with exactly the same title (year within one)."""
        counts: dict[str, int] = {}
        for paper in papers:
            words = re.sub(r"[^\w\s-]", " ", paper.title).split()
            if not words:
                continue
            data = self.request(
                "GET", f"{self.settings.openalex_base}/works",
                params={"filter": "title.search:" + " ".join(words), "select": TITLE_SELECT, "per-page": 10},
                ttl=self.settings.cache_ttl.paper, source="openalex.title",
            )
            wanted = normalize_title(paper.title)
            cited = [
                w["cited_by_count"] for w in (data.get("results") or [])
                if isinstance(w, dict) and isinstance(w.get("cited_by_count"), int)
                and normalize_title(w.get("title") or "") == wanted
                and (paper.year is None or not isinstance(w.get("publication_year"), int)
                     or abs(w["publication_year"] - paper.year) <= 1)
            ]
            if cited:
                counts[paper.paper_id] = max(cited)
        return counts
