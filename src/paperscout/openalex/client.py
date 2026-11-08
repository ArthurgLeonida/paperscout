"""OpenAlex (https://docs.openalex.org): citation counts when the paper provider has none, and papers by author.

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
An optional free API key (OPENALEX_API_KEY) gives a personal daily allowance instead of the one shared by
everyone on the same network; it is sent as the ``api_key`` parameter, added at send time only, so it never
reaches the response cache, logs or error messages. Each response's X-RateLimit-* headers are kept so commands
can say how much of today's allowance is left.

For ``/authors``, paperscout also sends researcher names and an affiliation (for example "KAIST") to find
institutions, authors and their works. Response text is data: nothing in it is followed as an instruction,
including notices in the response metadata.
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
from paperscout.models import Author, Paper

BATCH = 50
SELECT = "id,doi,title,cited_by_count,locations"
TITLE_SELECT = "id,doi,title,cited_by_count,publication_year"
_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$", re.IGNORECASE)
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)
WORK_SELECT = ("id,doi,display_name,publication_date,publication_year,cited_by_count,authorships,locations,"
               "primary_location,abstract_inverted_index,type")
AUTHOR_SELECT = "id,display_name,works_count,cited_by_count,summary_stats,last_known_institutions"
PAGE = 200
CITED_BY_WORK = "openalex-works"  # citation count that came with the work record itself (not re-fetched)


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


def openalex_id(value: str | None) -> str | None:
    """'https://openalex.org/A5085363061' -> 'A5085363061'."""
    return value.rsplit("/", 1)[-1] if value else None


def abstract_from_index(index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not isinstance(index, dict) or not index:
        return None
    slots: dict[int, str] = {}
    for word, positions in index.items():
        for position in positions or []:
            if isinstance(position, int):
                slots[position] = word
    return " ".join(slots[i] for i in sorted(slots)) or None


def arxiv_id_of(work: dict[str, Any]) -> str | None:
    for key in sorted(work_keys(work)):
        if key.startswith("arxiv:"):
            return key.split(":", 1)[1]
    return None


def paper_from_work(work: dict[str, Any]) -> Paper | None:
    """A paperscout Paper from an OpenAlex work. arXiv works get the same ID as the arXiv provider gives them."""
    title = " ".join((work.get("display_name") or work.get("title") or "").split())
    work_id = openalex_id(work.get("id"))
    if not title or not work_id:
        return None
    arxiv = arxiv_id_of(work)
    doi = bare_doi(work.get("doi"))
    if doi and _ARXIV_DOI.match(doi):
        doi = None  # arXiv's own DOI is implied by the arXiv ID
    external = {"OpenAlex": work_id}
    if doi:
        external["DOI"] = doi
    if arxiv:
        external["ArXiv"] = arxiv
    source = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
    venue = "arXiv preprint" if source and source.lower().startswith("arxiv") else source
    if arxiv:
        url = f"https://arxiv.org/abs/{arxiv}"
    elif doi:
        url = f"https://doi.org/{doi}"
    else:
        url = f"https://openalex.org/{work_id}"
    count = work.get("cited_by_count")
    year = work.get("publication_year")
    return Paper(
        paper_id=f"arxiv:{arxiv}" if arxiv else (f"doi:{doi}" if doi else f"openalex:{work_id}"),
        title=title,
        external_ids=external,
        url=url,
        abstract=abstract_from_index(work.get("abstract_inverted_index")),
        venue=venue,
        year=year if isinstance(year, int) else None,
        publication_date=work.get("publication_date"),
        citation_count=count if isinstance(count, int) else None,
        citation_source=CITED_BY_WORK if isinstance(count, int) else None,
        authors=[Author(name=(a.get("author") or {}).get("display_name") or a.get("raw_author_name") or "",
                        author_id=openalex_id((a.get("author") or {}).get("id")))
                 for a in work.get("authorships") or [] if isinstance(a, dict)],
    )


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
        self.allowance: dict[str, Any] | None = None  # from the latest response's X-RateLimit-* headers

    def _secret_params(self) -> dict[str, str]:
        return {"api_key": self.settings.openalex_api_key} if self.settings.openalex_api_key else {}

    def _observe(self, response: httpx.Response) -> None:
        headers = response.headers

        def number(name: str) -> float | None:
            try:
                return float(headers[name])
            except (KeyError, ValueError):
                return None

        remaining, limit = number("X-RateLimit-Remaining-USD"), number("X-RateLimit-Limit-USD")
        if remaining is not None and limit:
            reset = number("X-RateLimit-Reset")
            self.allowance = {"remaining_usd": remaining, "limit_usd": limit,
                              "reset_hours": round(reset / 3600, 1) if reset is not None else None,
                              "keyed": bool(self.settings.openalex_api_key)}

    def allowance_text(self) -> str | None:
        """'OpenAlex allowance today: $0.076 of $0.10 left (76%) ...', or None before any response."""
        a = self.allowance
        if not a:
            return None
        share = a["remaining_usd"] / a["limit_usd"]
        whose = "your API key" if a["keyed"] else "keyless, shared by everyone on your network"
        reset = f"; resets in about {a['reset_hours']:g} h" if a.get("reset_hours") is not None else ""
        low = " Running low: citation counts and /authors may fail until it resets." if share < 0.2 else ""
        return (f"OpenAlex allowance today: ${a['remaining_usd']:.3f} of ${a['limit_usd']:.2f} left ({share:.0%}; "
                f"{whose}{reset}).{low}")

    def _rate_limit_advice(self) -> str:
        reset = (self.allowance or {}).get("reset_hours")
        when = f"; it resets in about {reset:g} h" if reset is not None else ""
        if self.settings.openalex_api_key:
            return f"Your OpenAlex key's daily allowance is spent{when}."
        return ("Keyless OpenAlex access has a small daily allowance shared per IP address; popularity stays unknown "
                "until it resets. A free key (OPENALEX_API_KEY in .env) gives you your own allowance.")

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

    # ------------------------------------------------------------------ papers by author

    def institution(self, text: str) -> dict[str, Any] | None:
        """The best-matching institution for free text such as "KAIST" (OpenAlex also matches acronyms)."""
        data = self.request(
            "GET", f"{self.settings.openalex_base}/institutions",
            params={"search": text, "select": "id,display_name,display_name_acronyms,country_code", "per-page": 5},
            ttl=self.settings.cache_ttl.paper, source="openalex.institutions",
        )
        results = [r for r in (data.get("results") or []) if isinstance(r, dict) and r.get("id")]
        return results[0] if results else None

    def author_candidates(self, name: str, institution_id: str | None = None) -> list[dict[str, Any]]:
        """Author records for a name, best match first (OpenAlex may split one person into several records)."""
        params = {"search": name, "select": AUTHOR_SELECT, "per-page": 25}
        if institution_id:
            params["filter"] = f"affiliations.institution.id:{institution_id}"
        data = self.request("GET", f"{self.settings.openalex_base}/authors", params=params,
                            ttl=self.settings.cache_ttl.paper, source="openalex.authors")
        return [r for r in (data.get("results") or []) if isinstance(r, dict) and r.get("id")]

    def works(self, filters: str, max_pages: int = 3) -> tuple[int | None, list[dict[str, Any]]]:
        """Works matching an OpenAlex filter, newest first, up to ``max_pages`` pages of 200."""
        works: list[dict[str, Any]] = []
        cursor, total = "*", None
        for _ in range(max_pages):
            data = self.request(
                "GET", f"{self.settings.openalex_base}/works",
                params={"filter": filters, "select": WORK_SELECT, "per-page": PAGE, "sort": "publication_date:desc",
                        "cursor": cursor},
                ttl=self.settings.cache_ttl.search, source="openalex.works",
            )
            meta = data.get("meta") or {}
            total = meta.get("count") if isinstance(meta.get("count"), int) else total
            works += [w for w in (data.get("results") or []) if isinstance(w, dict)]
            cursor = meta.get("next_cursor")
            if not cursor or len(works) >= (total or 0):
                break
        return total, works
