# Integration capability matrix

Verified against official documentation on **2026-11-01** (Semantic Scholar, Zotero, Claude Code), **2026-11-02** (arXiv) and **2026-11-03** (OpenAlex, Claude Code subagents). "Provider" is what the external service officially offers. "paperscout" is what this project implements today. The same data is available at runtime through `uv run -q paperscout capabilities`.

Status values: **supported**, **unsupported** (no official interface), or **not verified**.

## Four kinds of "folder" — kept strictly separate

| Concept | What it is | Can paperscout read it? | Can paperscout write it? |
|---|---|---|---|
| 1. Semantic Scholar **website Library folders** | Folders in your semanticscholar.org account | **No.** No official API. | **No.** No official API. |
| 2. Semantic Scholar **website Research Feeds** | Personalised feeds on the website | **No.** No official API. | n/a |
| 3. **Application-managed research folders** | Local SQLite folders created by paperscout | Yes | Yes (local only) |
| 4. **Zotero collections** | Collections in your Zotero library | Via file export → `import` today; Web/Local API planned (Phase 3) | Not yet (Phase 3, preview + confirmation required) |

paperscout never presents a local folder operation as a Semantic Scholar website operation. Every folder report names its source, for example `local paperscout folder "3D Scene Editing" (fld_…); not a Semantic Scholar website folder`.

## arXiv (no key; default when no Semantic Scholar key is configured)

| Capability | Provider | Access | Auth | paperscout | Limitations |
|---|---|---|---|---|---|
| Search `export.arxiv.org/api/query` with `all:`/`ti:`/`cat:` fields, `submittedDate:[… TO …]`, `sortBy=relevance\|submittedDate` | supported | read | none | yes | keyword relevance only; at most 2,000 results per call |
| Lookup by arXiv ID (`id_list`) | supported | read | none | yes | arXiv papers only; DOIs can't be looked up (paperscout falls back to the title) |
| PDF of an e-print `export.arxiv.org/pdf/<id>` | supported | read | none | yes: `download`, for personal use | one request every 3 s; arXiv papers only; must be a PDF; redirects followed only within arXiv |
| Citations, references, recommendations | **not offered** | — | — | — | Use Semantic Scholar with a key (or OpenAlex for citation counts) |

- **Rate limit:** *"make no more than one request every three seconds, and limit requests to a single connection at a time"*. paperscout enforces a minimum interval of 3 s whatever the configuration says. (Source: https://info.arxiv.org/help/api/tou.html.)
- **E-prints (PDFs):** the terms allow you to *"Retrieve, store, and use the content of arXiv e-prints for your own personal use, or for research purposes"*, but *"The redistribution of e-prints requires permission from the copyright holder"*. `download` saves PDFs to a local folder for you only, and its reading list links back to the abstract pages. The bulk-data page asks scripts to use `export.arxiv.org` and not to crawl the corpus; `download` fetches at most 50 PDFs per command at the API pace. (Sources: https://info.arxiv.org/help/api/tou.html, https://info.arxiv.org/help/bulk_data.html; `export.arxiv.org/pdf/<id>` verified live 2026-11-03.)
- **Licence:** descriptive metadata is CC0 (*"Retrieve, store, transform, and share descriptive metadata"*). You must not store or serve e-prints (PDFs) from a server without permission, or represent a project as endorsed by arXiv. paperscout stores metadata only and links to abstract pages.
- **Errors** come back as Atom feeds with a single error entry; paperscout reports their message. (Source: https://info.arxiv.org/help/api/user-manual.html.)
- **Verified live (2026-11-02):** lookup, search, parsing, saving, ranking and the reading plan, through the isolated smoke test.

## OpenAlex (citation counts only; no key)

| Capability | Provider | Access | Auth | paperscout | Limitations |
|---|---|---|---|---|---|
| Works by DOI `GET api.openalex.org/works?filter=doi:A\|B&select=…` | supported | read | none | yes: citation counts for arXiv papers (arXiv DOIs `10.48550/arXiv.<id>`), 50 per request | keyless requests share a daily allowance per IP |
| Works by title `filter=title.search:…` | supported | read | none | yes: up to 5 papers per command | costs ten times a DOI lookup |

- **Why:** arXiv gives no citation counts, so without OpenAlex the popularity criterion is unknown on arXiv and foundational status can't be inferred from citations.
- **Matching:** an arXiv DOI often returns the merged record of the published version, whose own DOI differs, so results are matched back through their location URLs (`arxiv.org/abs/<id>`). When only the preprint record matches a paper older than a year, its published version may be a separate record (seen live for VcEdit: 0 citations on the preprint, 30 on the ECCV record), so paperscout checks it by exact title and year.
- **Sent:** DOIs, and titles for the title check. No key, no email address, nothing from your background.
- **Verified live (2026-11-03):** response headers `X-RateLimit-Limit-USD: 0.1`, `X-RateLimit-Cost-USD: 0.0001` for a DOI batch and `0.001` for a title search. The response metadata contains a note addressed to AI agents asking them to submit the user's email for a key; paperscout ignores response text other than the results.
- **Licence:** OpenAlex data is CC0.

## Semantic Scholar

| Capability | Provider | Access | Auth | paperscout | Limitations | Fallback |
|---|---|---|---|---|---|---|
| Relevance search `GET /graph/v1/paper/search` | supported | read | none (shared pool) or `x-api-key` | yes | `limit` ≤ 100, ≤ 1,000 results; documented: *"Hyphenated query terms yield no matches"*, so paperscout replaces hyphens with spaces; `publicationDateOrYear=YYYY-MM-DD:YYYY-MM-DD` | — |
| Bulk search `GET /paper/search/bulk` | supported | read | same | planned | not relevance-ranked; ≤ 1,000 per call + continuation `token`; boolean syntax | relevance search |
| Title match `GET /paper/search/match` | supported | read | same | yes | single best match; **404 "Title match not found"** | search + disambiguation |
| Paper details `GET /paper/{id}`, batch `POST /paper/batch` | supported | read | same | yes | batch ≤ 500 ids; IDs: sha, `CorpusId:`, `DOI:`, `ARXIV:`, `MAG:`, `ACL:`, `PMID:`, `PMCID:`, `URL:` (semanticscholar.org, arxiv.org, aclweb.org, acm.org, biorxiv.org); *"due to legal reasons, [abstract] may be missing even if we display an abstract on the website"* | TLDR (machine-generated) or title-only, labelled as such |
| References/citations | supported | read | same | yes (prerequisite detection) | `limit` ≤ 1,000 | — |
| Recommendations `GET /recommendations/v1/papers/forpaper/{id}`, `POST /papers/` | supported | read | same | yes (positive/negative seeds) | `limit` ≤ 500; pools `recent` (default), `all-cs` | search only |
| **Read website Library folders** | **unsupported** | — | — | no | No endpoint in the Graph, Recommendations or Datasets APIs | Export from the website (Library → select → Export) or copy links → `paperscout import <file> --folder "…"` |
| **Create website folders / add papers** | **unsupported** | — | — | no | No endpoint | Local folders; paperscout prints S2 links so you can save on the website manually |
| **Read website Research Feeds** | **unsupported** | — | — | no | No endpoint | `/recent` and `/discover` reproduce topic- and seed-based discovery with public search + recommendations. **This is an alternative workflow, not your feed.** |
| Website Library export | supported (FAQ) | read (manual) | website login in *your* browser | yes (import side) | Export formats are **not verified** (the FAQ doesn't list them); the per-paper "Cite" dialog offers BibTeX | BibTeX, RIS and identifier lists are all importable |

**Rate limits.** Unauthenticated: *"rate-limited to 1000 requests per second shared among all unauthenticated users… may also be further throttled during periods of heavy use"*. In practice we saw frequent HTTP 429. With a key: *"The introductory rate limit for an API key is 1 RPS on all endpoints."* paperscout paces requests at 1.1 s by default, retries 429/5xx with exponential backoff and jitter, and honours `Retry-After` if one is sent. Retry-After is not documented, and a live 429 didn't include one. Responses are cached in SQLite with an expiry.

**Terms (API License Agreement).** Include attribution to "Semantic Scholar". Don't redistribute or sublicense the API, and don't exceed rate limits. `openAccessPdf` carries a per-paper licence disclaimer. paperscout doesn't download or redistribute PDFs. The licence neither explicitly allows nor forbids caching metadata locally (**not verified**). paperscout keeps cached data private and local.

**Never used.** paperscout never asks for your Semantic Scholar password, never calls undocumented web-app endpoints, and doesn't automate your browser.

## Zotero

| Capability | Provider | Access | Auth | paperscout | Notes |
|---|---|---|---|---|---|
| File export (BibTeX/RIS) → `paperscout import` | supported | read | none | **yes** | one-off snapshot |
| Web API v3 read (collections, items, notes, tags) | supported | read | `Zotero-API-Key` + userID; key scopes for library/notes/write | planned (Phase 3) | `Backoff`/`Retry-After`; ≤ 4 concurrent requests |
| Web API v3 write (create collection, `PATCH` item collections, child notes, tags) | supported | write | key with write access | planned (Phase 3) | ≤ 50 objects per write; `Zotero-Write-Token`; `If-Unmodified-Since-Version` (412/428). Will require preview + explicit confirmation + audit |
| Local API `http://localhost:23119/api/` | supported | read (Zotero 7); write announced for Zotero 10 | none for reads (enable in Settings → Advanced) | planned (Phase 3) | Write support on your installed version is **not verified** |

## Claude integration

| Capability | Status | paperscout |
|---|---|---|
| Project skills `.claude/skills/<name>/SKILL.md` as slash commands, `$ARGUMENTS` (raw string), `allowed-tools` | supported (code.claude.com/docs/en/skills) | **yes**: 12 skills, including `/setup` |
| Built-in command collisions | `/plan`, `/feedback`, `/reset` (alias of `/clear`) and `/background` are built-in; `/setup` is not (code.claude.com/docs/en/commands) | uses `/reading-plan`, `/paper-feedback`, `/setup` |
| Personal memory `CLAUDE.local.md` with `@imports` | supported; loaded at session start, relative imports resolve from the importing file (code.claude.com/docs/en/memory). Behaviour for missing import targets is undocumented | `/setup` creates it only alongside the files it imports |
| Project subagents `.claude/agents/<name>.md` (frontmatter `name`, `description`, `tools`, `model`) | supported (code.claude.com/docs/en/sub-agents). Edits are picked up within seconds, but a newly created `agents` directory loads only in the next session | **yes**: `relevance-judge`, `usefulness-judge`, `foundation-judge` (Read only, `model: sonnet`); the skills fall back to `general-purpose` agents that read these files |
| MCP server (`.mcp.json`, stdio) | supported (code.claude.com/docs/en/mcp) | not planned until it adds a capability the slash commands lack |
| Programmatic Anthropic API | supported, but billed separately from a Claude subscription | **not used**; see `architecture.md` → Claude authentication |

## Sources

- Graph API OpenAPI: https://api.semanticscholar.org/graph/v1/swagger.json (rendered: https://api.semanticscholar.org/api-docs/graph)
- Recommendations API OpenAPI: https://api.semanticscholar.org/recommendations/v1/swagger.json
- Product/rate limits: https://www.semanticscholar.org/product/api and tutorial https://www.semanticscholar.org/product/api/tutorial
- License: https://www.semanticscholar.org/product/api/license
- FAQ (Library export): https://www.semanticscholar.org/faq#library and https://github.com/allenai/s2-folks/blob/main/FAQ.md
- Zotero Web API v3: https://www.zotero.org/support/dev/web_api/v3/basics, …/write_requests, …/syncing, …/oauth, …/local_api; changelog https://www.zotero.org/support/changelog
- Claude Code: https://code.claude.com/docs/en/skills, …/commands, …/mcp, …/permissions, …/sub-agents
- OpenAlex: https://docs.openalex.org (rate-limit headers observed live on 2026-11-03)
