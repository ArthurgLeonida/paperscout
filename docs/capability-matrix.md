# Integration capability matrix

Verified against official documentation on **2026-11-01**. "Provider" is what the external service officially offers. "paperscout" is what this project implements today. The same data is available at runtime through `uv run -q paperscout capabilities`.

Status values: **supported**, **unsupported** (no official interface), or **not verified**.

## Four kinds of "folder" — kept strictly separate

| Concept | What it is | Can paperscout read it? | Can paperscout write it? |
|---|---|---|---|
| 1. Semantic Scholar **website Library folders** | Folders in your semanticscholar.org account | **No.** No official API. | **No.** No official API. |
| 2. Semantic Scholar **website Research Feeds** | Personalised feeds on the website | **No.** No official API. | n/a |
| 3. **Application-managed research folders** | Local SQLite folders created by paperscout | Yes | Yes (local only) |
| 4. **Zotero collections** | Collections in your Zotero library | Via file export → `import` today; Web/Local API planned (Phase 3) | Not yet (Phase 3, preview + confirmation required) |

paperscout never presents a local folder operation as a Semantic Scholar website operation. Every folder report names its source, for example `local paperscout folder "3D Scene Editing" (fld_…); not a Semantic Scholar website folder`.

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
| Project skills `.claude/skills/<name>/SKILL.md` as slash commands, `$ARGUMENTS` (raw string), `allowed-tools` | supported (code.claude.com/docs/en/skills) | **yes**: 11 skills |
| Built-in command collisions | `/plan` and `/feedback` are built-in (code.claude.com/docs/en/commands) | uses `/reading-plan` and `/paper-feedback` |
| MCP server (`.mcp.json`, stdio) | supported (code.claude.com/docs/en/mcp) | planned (Phase 2) |
| Programmatic Anthropic API | supported, but billed separately from a Claude subscription | **not used**; see `architecture.md` → Claude authentication |

## Sources

- Graph API OpenAPI: https://api.semanticscholar.org/graph/v1/swagger.json (rendered: https://api.semanticscholar.org/api-docs/graph)
- Recommendations API OpenAPI: https://api.semanticscholar.org/recommendations/v1/swagger.json
- Product/rate limits: https://www.semanticscholar.org/product/api and tutorial https://www.semanticscholar.org/product/api/tutorial
- License: https://www.semanticscholar.org/product/api/license
- FAQ (Library export): https://www.semanticscholar.org/faq#library and https://github.com/allenai/s2-folks/blob/main/FAQ.md
- Zotero Web API v3: https://www.zotero.org/support/dev/web_api/v3/basics, …/write_requests, …/syncing, …/oauth, …/local_api; changelog https://www.zotero.org/support/changelog
- Claude Code: https://code.claude.com/docs/en/skills, …/commands, …/mcp, …/permissions
