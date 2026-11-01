"""Normalize user-supplied paper references into Semantic Scholar identifier forms.

Supported inputs: DOI (bare, ``doi:``, doi.org URLs), arXiv IDs (new and old
style, with or without version, abs/pdf URLs, arXiv DOIs), Semantic Scholar
paper IDs and URLs, ``CorpusId:``, PubMed/PMC, ACL Anthology, and titles.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

# Domains the Graph API documents for the ``URL:`` identifier form.
S2_URL_DOMAINS = ("semanticscholar.org", "arxiv.org", "aclweb.org", "acm.org", "biorxiv.org")

_S2_SHA = re.compile(r"^[0-9a-f]{40}$")
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_ARXIV_OLD = re.compile(r"^([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$", re.IGNORECASE)
_PREFIXED = re.compile(r"^(corpusid|doi|arxiv|pmid|pmcid|acl|mag|url)\s*:\s*(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class PaperRef:
    kind: str  # s2 | corpus | doi | arxiv | pmid | pmcid | acl | mag | url | title
    value: str
    original: str

    @property
    def is_title(self) -> bool:
        return self.kind == "title"

    @property
    def s2_id(self) -> str:
        """Identifier string accepted by GET /paper/{paper_id}."""
        prefix = {
            "s2": "",
            "corpus": "CorpusId:",
            "doi": "DOI:",
            "arxiv": "ARXIV:",
            "pmid": "PMID:",
            "pmcid": "PMCID:",
            "acl": "ACL:",
            "mag": "MAG:",
            "url": "URL:",
        }
        if self.kind not in prefix:
            raise ValueError("titles must be resolved through title search, not an identifier lookup")
        return prefix[self.kind] + self.value


class IdentifierError(ValueError):
    pass


def normalize_doi(text: str) -> str:
    doi = unquote(text.strip())
    doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    doi = doi.rstrip(".,;)]}>\"'")
    if not _DOI.match(doi):
        raise IdentifierError(f"not a valid DOI: {text!r}")
    return doi.lower()


def normalize_arxiv(text: str) -> str:
    value = text.strip()
    value = re.sub(r"^arxiv:\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\.pdf$", "", value, flags=re.IGNORECASE)
    for pattern in (_ARXIV_NEW, _ARXIV_OLD):
        match = pattern.match(value)
        if match:
            return match.group(1)
    raise IdentifierError(f"not a valid arXiv identifier: {text!r}")


def _from_doi(doi: str, original: str) -> PaperRef:
    # arXiv-minted DOIs resolve more reliably through the arXiv identifier.
    match = re.match(r"^10\.48550/arxiv\.(.+)$", doi)
    if match:
        try:
            return PaperRef("arxiv", normalize_arxiv(match.group(1)), original)
        except IdentifierError:
            pass
    return PaperRef("doi", doi, original)


def _from_url(text: str) -> PaperRef:
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path)

    if host.endswith("doi.org"):
        return _from_doi(normalize_doi(path.lstrip("/")), text)

    if host.endswith("arxiv.org"):
        match = re.match(r"^/(?:abs|pdf|html)/(.+?)/?$", path)
        if match:
            return PaperRef("arxiv", normalize_arxiv(match.group(1)), text)

    if host.endswith("semanticscholar.org"):
        segments = [s for s in path.split("/") if s]
        for seg in reversed(segments):
            if _S2_SHA.match(seg.lower()):
                return PaperRef("s2", seg.lower(), text)
        match = re.search(r"corpusid:(\d+)", path, re.IGNORECASE)
        if match:
            return PaperRef("corpus", match.group(1), text)

    if host.endswith("pubmed.ncbi.nlm.nih.gov"):
        match = re.match(r"^/(\d+)/?$", path)
        if match:
            return PaperRef("pmid", match.group(1), text)

    if host.endswith("ncbi.nlm.nih.gov"):
        match = re.search(r"/pmc/articles/(PMC\d+)", path, re.IGNORECASE)
        if match:
            return PaperRef("pmcid", match.group(1).upper(), text)

    if host.endswith("aclanthology.org") or host.endswith("aclweb.org"):
        match = re.search(r"([A-Z]\d{2}-\d{4}|\d{4}\.[a-z\-]+\.\d+)", path, re.IGNORECASE)
        if match:
            return PaperRef("acl", match.group(1), text)

    match = re.search(r"(10\.\d{4,9}/[^\s?#]+)", path)
    if match and (host.endswith("acm.org") or host.endswith("ieee.org") or "doi" in path.lower()):
        try:
            return _from_doi(normalize_doi(match.group(1)), text)
        except IdentifierError:
            pass

    if any(host == d or host.endswith("." + d) for d in S2_URL_DOMAINS):
        return PaperRef("url", text, text)

    raise IdentifierError(
        f"Unsupported paper URL host {host!r}. Use a DOI, arXiv ID, Semantic Scholar URL/ID, "
        "or the paper title instead."
    )


def parse_paper_ref(text: str) -> PaperRef:
    """Parse one user-supplied reference. Anything unrecognized is treated as a title."""
    raw = text.strip()
    if not raw:
        raise IdentifierError("empty paper reference")
    stripped = raw.strip("<>\"' ")

    if re.match(r"^https?://", stripped, re.IGNORECASE):
        return _from_url(stripped)

    prefixed = _PREFIXED.match(stripped)
    if prefixed:
        kind, value = prefixed.group(1).lower(), prefixed.group(2).strip()
        if kind == "corpusid":
            if not value.isdigit():
                raise IdentifierError(f"CorpusId must be numeric: {raw!r}")
            return PaperRef("corpus", value, raw)
        if kind == "doi":
            return _from_doi(normalize_doi(value), raw)
        if kind == "arxiv":
            return PaperRef("arxiv", normalize_arxiv(value), raw)
        if kind == "url":
            return _from_url(value)
        if kind == "pmcid":
            return PaperRef("pmcid", value.upper(), raw)
        return PaperRef(kind, value, raw)

    if _S2_SHA.match(stripped.lower()):
        return PaperRef("s2", stripped.lower(), raw)
    if re.match(r"^10\.\d{4,9}/", stripped):
        return _from_doi(normalize_doi(stripped), raw)
    if _ARXIV_NEW.match(stripped) or _ARXIV_OLD.match(stripped):
        return PaperRef("arxiv", normalize_arxiv(stripped), raw)

    return PaperRef("title", " ".join(stripped.split()), raw)


def normalize_title(title: str) -> str:
    """Casefolded, accent-stripped, punctuation-free title used for duplicate detection."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^0-9a-z]+", " ", text.casefold())
    return " ".join(text.split())
