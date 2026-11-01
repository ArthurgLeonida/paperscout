# paperscout

A research-paper discovery and reading-priority assistant for Claude Code, built on the **official Semantic Scholar APIs**.

It answers *"Which papers should I read for this research goal, why, in what order, and at what depth?"* It doesn't claim to find objectively "best" papers.

- Discovery through Semantic Scholar search, recommendations and reference lists, deduplicated with provenance
- Transparent, versioned heuristic ranking. Relevance, project usefulness, recency, evidence, confidence and effort are reported separately; citation counts never raise priority.
- Local research profiles and folders (SQLite), append-only feedback, reading plans, note scaffolds
- Claude Code slash commands (`/recent`, `/discover`, `/rank`, …) that call one tested service layer
- Honest limits: Semantic Scholar **website** folders and Research Feeds have no official API, so they're never presented as supported (see [docs/capability-matrix.md](docs/capability-matrix.md))

## Windows setup

Prerequisites: Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/) (`pip install uv` or `winget install astral-sh.uv`). Claude Code is optional, needed only for the slash commands.

```powershell
cd E:\Documentos_HD\ai-semantic-scholar
uv sync                                   # creates .venv and installs dependencies
uv run -q paperscout init                 # creates the database, shows where data lives
uv run pytest                             # all tests use mocked API responses
```

1. **Recommended: get a Semantic Scholar API key.** Unauthenticated requests share a public pool that is often fully throttled (HTTP 429). Request a key at https://www.semanticscholar.org/product/api, then:
   ```powershell
   Copy-Item .env.example .env
   notepad .env                            # set S2_API_KEY=...
   ```
   `.env` is git-ignored, and the key is never logged.
2. **Create your research profile.** Start from an example and edit it:
   ```powershell
   Copy-Item examples\profiles\3d-scene-editing.json my-profile.json
   notepad my-profile.json
   uv run -q paperscout profile create --file my-profile.json --activate
   ```
   `uv run -q paperscout profile template` prints an empty profile. Profiles work for any field; see `examples/profiles/medical-segmentation.json`.
3. **Use it from Claude Code.** Open Claude Code (CLI or VS Code) **in this folder**. The project skills in `.claude/skills/` then appear as slash commands.

Data location: `%LOCALAPPDATA%\paperscout` (database, cache, saved reports, note scaffolds). Override it with `PAPERSCOUT_HOME` in `.env`.

If uv warns that `VIRTUAL_ENV … does not match`, pyenv has set `VIRTUAL_ENV`. The warning is harmless, and `uv run -q` hides it.

## Slash commands (Claude Code, opened in this folder)

| Command | Purpose |
|---|---|
| `/recent <topic> [--days N \| --since D] [--until D] [--folder F] [--profile P] [--limit N] [--include-read]` | Recent papers (default window 90 days, configurable) |
| `/discover <topic> [--folder F] [--profile P] [--limit N] [--seed ID]` | Balanced reading list: prerequisites, surveys, methods |
| `/rank "<folder>" [--unread] [--new] [--imported]` · `/rank --all [--profile P]` | Rank local folders |
| `/folders list \| show "F" \| create "F" \| propose <field>` | Manage local folders; proposals are previewed and need confirmation |
| `/classify <DOI \| arXiv \| S2 id/URL \| title>` | Suggest folders; never changes anything without approval |
| `/add <paper> --folder "F"` | Add to a local folder (never creates folders implicitly) |
| `/assess <paper> [--profile P]` | One-paper relevance and priority assessment |
| `/reading-plan [--hours H \| --week] [--folder F]` | Time-boxed plan (`/plan` is a built-in command) |
| `/paper-feedback <paper> <label> ["reason"]` | Append-only feedback (`/feedback` is a built-in command) |
| `/note <paper> [--pass first\|second\|deep] [--save]` | Note scaffold (template only) |
| `/research-help [command]` | Commands, defaults, limitations |

Multiword topics need no quotes: `/recent text-guided 3D Gaussian scene editing`. Quote folder names that contain spaces: `/rank "3D Scene Editing" --unread`. Unknown options are rejected with a suggestion rather than ignored.

Feedback labels: `relevant`, `irrelevant`, `already_known`, `read_later`, `read_overview`, `understood`, `deep_study`, `implemented`, `not_useful`.

## The same commands from a terminal

```powershell
uv run -q paperscout recent text-guided 3D Gaussian scene editing --days 60 --limit 8
uv run -q paperscout folders create "3D Scene Editing" --description "Editing 3D scene representations" --include "3D scene editing"
uv run -q paperscout add https://arxiv.org/abs/2308.04079 --folder "3D Scene Editing"
uv run -q paperscout import my-s2-library-export.bib --folder "3D Scene Editing" --dry-run
uv run -q paperscout rank "3D Scene Editing" --unread
uv run -q paperscout plan --hours 5
uv run -q paperscout feedback 2308.04079 understood
uv run -q paperscout capabilities
```

Add `--json` to get JSON output. Ranking commands also save Markdown and JSON reports under `%LOCALAPPDATA%\paperscout\reports`.

## Bringing in your existing libraries

- **Semantic Scholar website folders.** On the website, select the papers in a Library folder and use **Export**, or copy the paper links into a text file (one per line). Then run `paperscout import <file> --folder "<local folder>"`. BibTeX, RIS and plain ID/URL lists are supported.
- **Zotero collections.** Right-click the collection, choose **Export Collection…** (BibTeX or RIS), then use `paperscout import`. Direct Zotero API integration is planned for Phase 3.

## Documentation

- [Capability matrix](docs/capability-matrix.md): what each service officially supports, with sources
- [Architecture](docs/architecture.md): service layer, Claude authentication strategy, provenance, write safety
- [Ranking](docs/ranking.md): dimensions, configuration, evidence limits
- [Evaluation](docs/evaluation.md): building your labeled set and comparing against baselines

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Capability matrix, profiles and folders, S2 search and recommendations, dedup, evidence-grounded heuristic ranking, CLI, Markdown/JSON reports, feedback, tests, Windows setup | done |
| 1+ | Slash-command skills; reading plans; folder proposals; classification (pulled forward from Phase 2 because the slash commands need them) | done |
| 2 | Thin MCP server exposing the same service layer | next |
| 3 | Optional Zotero Web/Local API (preview + confirmation + audit), scheduled discovery, permitted full-text assessment, bulk search | after Phase 2 |

## Attribution

Paper data comes from [Semantic Scholar](https://www.semanticscholar.org/). If you publish research that uses this data, cite *The Semantic Scholar Open Data Platform*, as the API licence asks.
