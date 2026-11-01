"""Capability matrix as data, plus explicit flags for unsupported connectors.

``provider_status`` describes what the external service officially offers
(verified against official documentation on 2026-11-01; see
docs/capability-matrix.md for sources). ``implemented`` describes what
paperscout itself does today. Operations on unsupported capabilities raise
``UnsupportedCapability`` with an honest alternative.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    key: str
    description: str
    provider_status: str  # supported | unsupported | not_verified
    access: str  # read | write | read/write | n/a
    auth: str
    implemented: str  # yes | planned:phase-N | no
    source: str
    limitations: str
    fallback: str


CAPABILITIES: dict[str, Capability] = {
    c.key: c
    for c in [
        Capability(
            "s2.search", "Relevance-ranked paper search (GET /graph/v1/paper/search)", "supported", "read",
            "none (shared public pool) or x-api-key", "yes",
            "https://api.semanticscholar.org/api-docs/graph",
            "limit <= 100 per page; up to 1,000 relevance-ranked results; hyphenated terms yield no matches "
            "(paperscout replaces hyphens with spaces); unauthenticated requests are frequently throttled",
            "-",
        ),
        Capability(
            "s2.bulk_search", "Bulk keyword search with boolean syntax and sorting (GET /paper/search/bulk)",
            "supported", "read", "none or x-api-key", "planned:phase-3",
            "https://api.semanticscholar.org/api-docs/graph",
            "not relevance-ranked; up to 1,000 per call with continuation token", "relevance search",
        ),
        Capability(
            "s2.title_match", "Closest title match (GET /paper/search/match)", "supported", "read",
            "none or x-api-key", "yes", "https://api.semanticscholar.org/api-docs/graph",
            "single best match; 404 'Title match not found'", "relevance search with disambiguation",
        ),
        Capability(
            "s2.paper_lookup", "Paper details and batch lookup by DOI/arXiv/S2 id/CorpusId/PMID/ACL/URL",
            "supported", "read", "none or x-api-key", "yes", "https://api.semanticscholar.org/api-docs/graph",
            "batch <= 500 ids; abstracts may be missing for legal reasons even if shown on the website",
            "-",
        ),
        Capability(
            "s2.references", "Reference/citation lists (GET /paper/{id}/references|citations)", "supported",
            "read", "none or x-api-key", "yes", "https://api.semanticscholar.org/api-docs/graph",
            "limit <= 1000 per page", "-",
        ),
        Capability(
            "s2.recommendations", "Paper recommendations from one paper or positive/negative seed lists",
            "supported", "read", "none or x-api-key", "yes",
            "https://api.semanticscholar.org/api-docs/recommendations",
            "limit <= 500; pools 'recent' (default) and 'all-cs'", "-",
        ),
        Capability(
            "s2.website_library.read", "Read your Semantic Scholar website Library folders", "unsupported",
            "read", "n/a", "no", "https://api.semanticscholar.org/api-docs/",
            "no official endpoint in the Graph, Recommendations or Datasets APIs",
            "Export the folder on the website (Library > select papers > Export) or copy paper links, then "
            "`paperscout import <file> --folder <name>` into an application-managed local folder.",
        ),
        Capability(
            "s2.website_library.write", "Create website folders or add papers to them", "unsupported", "write",
            "n/a", "no", "https://api.semanticscholar.org/api-docs/",
            "no official endpoint", "Use local research folders; paperscout prints Semantic Scholar links so you "
            "can save papers on the website manually.",
        ),
        Capability(
            "s2.research_feed.read", "Read your Semantic Scholar website Research Feeds", "unsupported", "read",
            "n/a", "no", "https://api.semanticscholar.org/api-docs/",
            "no official endpoint", "`paperscout recent` / `discover` reproduce topic- and seed-based discovery "
            "with the public search and recommendations APIs. This is an alternative workflow, NOT your website feed.",
        ),
        Capability(
            "s2.website_library.export", "Export Library papers from the website", "supported", "read",
            "website login (manual, in your browser)", "yes",
            "https://www.semanticscholar.org/faq#library",
            "export formats are not listed in the FAQ (not verified); per-paper 'Cite' offers BibTeX",
            "paperscout imports BibTeX, RIS, and plain identifier/URL lists.",
        ),
        Capability(
            "local.folders", "Application-managed research folders (SQLite on this computer)", "supported",
            "read/write", "none", "yes", "this project", "local only; not synced to any website", "-",
        ),
        Capability(
            "zotero.file_import", "Import a Zotero collection exported as BibTeX or RIS", "supported", "read",
            "none", "yes", "https://www.zotero.org/support/kb/exporting",
            "one-off snapshot; no sync", "-",
        ),
        Capability(
            "zotero.web_api.read", "Read Zotero collections/items/notes/tags via the Web API v3", "supported",
            "read", "Zotero API key + user ID", "planned:phase-3",
            "https://www.zotero.org/support/dev/web_api/v3/basics", "rate limits with Backoff/Retry-After",
            "Export the collection to BibTeX/RIS and use `paperscout import`.",
        ),
        Capability(
            "zotero.web_api.write", "Create collections, add items, write notes/tags via the Web API v3",
            "supported", "write", "Zotero API key with write access", "planned:phase-3",
            "https://www.zotero.org/support/dev/web_api/v3/write_requests",
            "<= 50 objects per write; version preconditions; will require preview + explicit confirmation",
            "Add items manually in Zotero; paperscout can print DOIs/links to paste.",
        ),
        Capability(
            "zotero.local_api", "Zotero desktop local API (http://localhost:23119/api/)", "supported", "read",
            "none for reads (enable in Zotero settings)", "planned:phase-3",
            "https://www.zotero.org/support/dev/web_api/v3/local_api",
            "read-only in Zotero 7; write support announced for Zotero 10 (not verified on your install)",
            "File export + import.",
        ),
        Capability(
            "claude.skills", "Project slash commands via .claude/skills/<name>/SKILL.md", "supported", "n/a",
            "your Claude Code login", "yes", "https://code.claude.com/docs/en/skills",
            "/plan and /feedback are built-in commands, so paperscout uses /reading-plan and /paper-feedback",
            "-",
        ),
        Capability(
            "claude.mcp", "MCP server exposing paperscout tools", "supported", "n/a", "your Claude Code login",
            "planned:phase-2", "https://code.claude.com/docs/en/mcp", "project servers need one-time approval",
            "Skills call the CLI directly.",
        ),
        Capability(
            "anthropic.api", "Programmatic Anthropic API ranking mode", "supported", "n/a",
            "separate ANTHROPIC_API_KEY with its own billing", "no",
            "https://docs.claude.com/en/api/overview", "costs API credits; not included in a Claude subscription",
            "Claude Code reviews paperscout's structured evidence in-session.",
        ),
        Capability(
            "fulltext.assessment", "Full-text assessment of permitted open-access PDFs", "not_verified", "read",
            "none", "planned:phase-3", "openAccessPdf.disclaimer in the Graph API",
            "licences vary per paper; PDFs are never redistributed",
            "Abstract-level assessment with explicit limits.",
        ),
    ]
}


class UnsupportedCapability(RuntimeError):
    def __init__(self, key: str):
        cap = CAPABILITIES[key]
        status = "not supported by the provider" if cap.provider_status == "unsupported" else (
            f"not implemented in paperscout yet ({cap.implemented})"
        )
        super().__init__(f"{cap.description}: {status}. {cap.limitations}. Alternative: {cap.fallback}")
        self.capability = cap


def require(key: str) -> Capability:
    cap = CAPABILITIES[key]
    if cap.provider_status != "supported" or cap.implemented != "yes":
        raise UnsupportedCapability(key)
    return cap


# Sources accepted by ranking commands. Only "local" is available in Phase 1.
RANK_SOURCES = {
    "local": "local.folders",
    "zotero": "zotero.web_api.read",
    "s2-library": "s2.website_library.read",
    "s2-feed": "s2.research_feed.read",
}


def check_rank_source(source: str) -> None:
    if source not in RANK_SOURCES:
        raise ValueError(f"Unknown source {source!r}. Valid sources: {', '.join(RANK_SOURCES)}")
    require(RANK_SOURCES[source])
