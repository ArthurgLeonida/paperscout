"""Client for the arXiv API (https://info.arxiv.org/help/api/user-manual.html).

No key is needed. The terms of use (https://info.arxiv.org/help/api/tou.html)
allow at most one request every three seconds over a single connection; this
client never paces faster than that, whatever the configuration says.
Metadata (title, abstract, authors, identifiers, categories) is CC0. paperscout
stores metadata and links to arXiv abstract pages. It saves an e-print PDF only
when the user runs ``download``, for their personal use, which the terms allow
("Retrieve, store, and use the content of arXiv e-prints for your own personal
use, or for research purposes"); it never serves or redistributes PDFs.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx

from paperscout.config import ARXIV_MIN_INTERVAL, Settings
from paperscout.http import ApiBadRequest, ApiClient, ApiError, ApiNotFound, ApiRateLimited, RequestPolicy
from paperscout.identifiers import IdentifierError, normalize_arxiv
from paperscout.models import Author, Paper
from paperscout.ranking.text import STOPWORDS

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}
ID_BATCH = 100
_WORD = re.compile(r"[a-z0-9]+")
_CATEGORY = re.compile(r"^[a-z\-]+(\.[A-Za-z\-]+)?$")


def query_terms(text: str, limit: int = 8) -> list[str]:
    """Significant lower-case words for an arXiv query (hyphens split, stopwords dropped)."""
    words = _WORD.findall(text.lower().replace("-", " "))
    out: list[str] = []
    for word in words:
        if len(word) > 1 and word not in STOPWORDS and word not in out:
            out.append(word)
    return out[:limit]


def build_search_query(
    text: str,
    *,
    mode: str = "all",  # "all" = every term must match, "any" = at least one term
    window: tuple[date, date] | None = None,
    categories: Iterable[str] = (),
) -> str:
    terms = query_terms(text)
    if not terms:
        raise ValueError("the topic has no searchable words")
    joiner = " AND " if mode == "all" else " OR "
    parts = ["(" + joiner.join(f"all:{t}" for t in terms) + ")"]
    cats = [c for c in categories if _CATEGORY.match(c)]
    if cats:
        parts.append("(" + " OR ".join(f"cat:{c}" for c in cats) + ")")
    if window:
        parts.append(f"submittedDate:[{window[0]:%Y%m%d}0000 TO {window[1]:%Y%m%d}2359]")
    return " AND ".join(parts)


def _text(entry: ET.Element, path: str) -> str:
    return " ".join((entry.findtext(path, default="", namespaces=NS) or "").split())


def parse_feed(xml_text: str) -> tuple[int | None, list[Paper]]:
    """Parse an arXiv Atom feed into papers. Raises ApiBadRequest for arXiv error feeds."""
    head = xml_text[:4096]
    if "<!DOCTYPE" in head or "<!ENTITY" in xml_text:
        raise ApiError("Unexpected DTD in the arXiv response; refusing to parse it.")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ApiError(f"arXiv returned a response that is not valid Atom XML ({exc}).") from None
    total_text = root.findtext("opensearch:totalResults", default="", namespaces=NS)
    total = int(total_text) if total_text.strip().isdigit() else None
    papers: list[Paper] = []
    for entry in root.findall("atom:entry", NS):
        entry_id = _text(entry, "atom:id")
        if "/api/errors" in entry_id:
            raise ApiBadRequest(f"arXiv API error: {_text(entry, 'atom:summary') or 'unspecified'}", 400)
        raw_id = re.sub(r"^https?://arxiv\.org/abs/", "", entry_id)
        try:
            arxiv_id = normalize_arxiv(raw_id)
        except IdentifierError:
            continue
        published = _text(entry, "atom:published")[:10] or None
        doi = _text(entry, "arxiv:doi") or None
        journal_ref = _text(entry, "arxiv:journal_ref") or None
        primary = entry.find("arxiv:primary_category", NS)
        primary_term = primary.get("term") if primary is not None else None
        categories = [c.get("term") for c in entry.findall("atom:category", NS) if c.get("term")]
        pdf = next((link.get("href") for link in entry.findall("atom:link", NS) if link.get("title") == "pdf"), None)
        external = {"ArXiv": arxiv_id}
        if doi:
            external["DOI"] = doi
        papers.append(
            Paper(
                paper_id=f"arxiv:{arxiv_id}",
                title=_text(entry, "atom:title") or "(untitled)",
                external_ids=external,
                url=f"https://arxiv.org/abs/{arxiv_id}",
                abstract=_text(entry, "atom:summary") or None,
                venue=journal_ref or (f"arXiv preprint ({primary_term})" if primary_term else "arXiv preprint"),
                year=int(published[:4]) if published else None,
                publication_date=published,
                fields_of_study=categories or None,
                is_open_access=True,
                open_access_pdf_url=pdf,
                authors=[Author(name=_text(a, "atom:name")) for a in entry.findall("atom:author", NS)
                         if _text(a, "atom:name")],
            )
        )
    return total, papers


class ArxivClient(ApiClient):
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
            provider="arXiv",
            budget_setting="PAPERSCOUT_ARXIV_MAX_REQUESTS",
            min_interval=max(ARXIV_MIN_INTERVAL, settings.arxiv_min_interval),
            max_retries=settings.arxiv_max_retries,
            max_backoff=settings.s2_max_backoff,
            timeout=settings.s2_timeout,
            max_requests=settings.arxiv_max_requests,
        )
        super().__init__(policy, {}, cache, transport, sleep, monotonic)

    def _rate_limit_advice(self) -> str:
        return "arXiv allows one request every three seconds; wait a little and try again."

    def download_pdf(self, arxiv_id: str, dest: Path, max_bytes: int) -> int:
        """Save one e-print PDF to ``dest`` (written atomically). Returns its size in bytes.

        Paced like every arXiv request, never retried, never cached, and checked to be a PDF. Redirects are
        followed only to arXiv hosts.
        """
        url = httpx.URL(f"{self.settings.arxiv_pdf_base}/{arxiv_id}")
        part = dest.with_name(dest.name + ".part")
        try:
            for _ in range(3):
                size = self._stream_pdf(url, arxiv_id, part, max_bytes)
                if isinstance(size, httpx.URL):
                    url = size
                    continue
                with part.open("rb") as fh:
                    if fh.read(5) != b"%PDF-":
                        raise ApiError(f"The file arXiv sent for {arxiv_id} is not a PDF; skipped.")
                part.replace(dest)
                return size
            raise ApiError(f"Too many redirects for the PDF of {arxiv_id}.")
        except httpx.TransportError as exc:
            raise ApiError(f"network error downloading the PDF of {arxiv_id}: {type(exc).__name__}") from None
        finally:
            part.unlink(missing_ok=True)

    def _stream_pdf(self, url: httpx.URL, arxiv_id: str, part: Path, max_bytes: int) -> int | httpx.URL:
        """One paced GET: writes the body to ``part`` and returns its size, or returns an arXiv redirect target."""
        self._pace()
        self._last_request = self._monotonic()
        self.network_calls += 1
        self.sources_used.append("arxiv.pdf")
        with self._http.stream("GET", url) as response:
            if response.is_redirect:
                target = response.url.join(response.headers.get("location", ""))
                host = target.host or ""
                if host != "arxiv.org" and not host.endswith(".arxiv.org"):
                    raise ApiError(f"arXiv redirected the PDF of {arxiv_id} to another site ({host}); not followed.")
                return target
            status = response.status_code
            if status == 404:
                raise ApiNotFound(f"arXiv has no PDF for {arxiv_id} (HTTP 404).", status)
            if status == 429 or status >= 500:
                raise ApiRateLimited(f"arXiv answered HTTP {status} for the PDF of {arxiv_id}. "
                                     f"{self._rate_limit_advice()}", status)
            if status != 200:
                raise ApiError(f"arXiv answered HTTP {status} for the PDF of {arxiv_id}.", status)
            kind = response.headers.get("content-type", "")
            if "pdf" not in kind.lower():
                raise ApiError(f"arXiv sent {kind or 'no content type'} instead of a PDF for {arxiv_id} "
                               "(the PDF may still be generating; try again later).")
            size = 0
            with part.open("wb") as fh:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ApiError(f"The PDF of {arxiv_id} is larger than {max_bytes // 2**20} MB; skipped "
                                       "(PAPERSCOUT_DOWNLOAD_MAX_MB).")
                    fh.write(chunk)
            return size

    def _error_message(self, response: httpx.Response) -> str:
        detail = response.text[:300]
        if "<feed" in detail or "<?xml" in detail:
            try:
                parse_feed(response.text)
            except ApiBadRequest as exc:
                return str(exc)
            except ApiError:
                pass
        return f"arXiv HTTP {response.status_code}: {' '.join(detail.split())[:200]}"

    def query(
        self,
        *,
        search_query: str | None = None,
        id_list: list[str] | None = None,
        max_results: int = 50,
        sort_by: str = "relevance",
        ttl: int | None = None,
        source: str = "arxiv.search",
    ) -> tuple[int | None, list[Paper]]:
        params = {
            "search_query": search_query,
            "id_list": ",".join(id_list) if id_list else None,
            "start": 0,
            "max_results": min(max_results, 2000),
            "sortBy": sort_by,
            "sortOrder": "descending",
        }
        text = self.request("GET", self.settings.arxiv_base, params=params, parse="text",
                            ttl=ttl or self.settings.cache_ttl.search, source=source)
        return parse_feed(text)

    def search_papers(
        self,
        text: str,
        *,
        max_results: int = 50,
        window: tuple[date, date] | None = None,
        categories: Iterable[str] = (),
        widen: bool = True,
    ) -> list[tuple[int, Paper]]:
        """Relevance search; if requiring every term finds too little, widen once to any term (when allowed)."""
        cats = list(categories)
        _, papers = self.query(search_query=build_search_query(text, window=window, categories=cats),
                               max_results=max_results)
        if widen and len(papers) < min(max_results, 10) and len(query_terms(text)) > 1:
            _, wider = self.query(search_query=build_search_query(text, mode="any", window=window, categories=cats),
                                  max_results=max_results)
            seen = {p.paper_id for p in papers}
            papers += [p for p in wider if p.paper_id not in seen]
        return list(enumerate(papers[:max_results], start=1))

    def title_search(self, title: str, max_results: int = 5) -> list[Paper]:
        terms = query_terms(title, limit=12)
        if not terms:
            return []
        _, papers = self.query(search_query=" AND ".join(f"ti:{t}" for t in terms), max_results=max_results,
                               source="arxiv.title")
        return papers

    def get_papers(self, arxiv_ids: Iterable[str]) -> list[Paper | None]:
        """Look up papers by arXiv ID; the result is aligned with the input (None = not found)."""
        ids = [normalize_arxiv(i) for i in arxiv_ids]
        found: dict[str, Paper] = {}
        for start in range(0, len(ids), ID_BATCH):
            chunk = ids[start : start + ID_BATCH]
            _, papers = self.query(id_list=chunk, max_results=len(chunk), ttl=self.settings.cache_ttl.paper,
                                   source="arxiv.id")
            found.update({p.arxiv_id: p for p in papers})
        return [found.get(i) for i in ids]
