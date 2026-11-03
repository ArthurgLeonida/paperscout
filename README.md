# paperscout

A research-paper discovery and reading-priority assistant for [Claude Code](https://code.claude.com/), built on the **official Semantic Scholar APIs**.

It answers *"Which papers should I read for this research goal, why, in what order, and at what depth?"* It doesn't claim to find objectively "best" papers.

- Discovery through Semantic Scholar search, recommendations and reference lists, deduplicated with provenance
- Transparent, versioned heuristic ranking. Relevance, project usefulness, recency, evidence, confidence and effort are reported separately; citation counts never raise priority.
- A private research background built by `/setup` (from your CV, papers and project documents, or an interview), so Claude knows what you already know and what you're working on
- Local research folders (SQLite), append-only feedback, reading plans, note scaffolds
- Claude Code slash commands (`/recent`, `/discover`, `/rank`, …) that call one tested service layer
- Honest limits: Semantic Scholar **website** folders and Research Feeds have no official API, so they're never presented as supported (see [docs/capability-matrix.md](docs/capability-matrix.md))

> **Status: early (v0.1).** Everything is covered by tests against mocked API responses. Paper lookup and failure handling have been checked against the live API; live search and recommendations are still awaiting validation. See [docs/validation.md](docs/validation.md).

## Bring your own Semantic Scholar API key

paperscout runs entirely on your computer. There is no shared server, and the project ships no API key.

- Without a key, it uses Semantic Scholar's shared unauthenticated pool. That works in principle, but the pool is often throttled (HTTP 429); during validation it rejected nearly every request.
- For dependable use, request **your own** key through the [official form](https://www.semanticscholar.org/product/api#api-key-form). Approval and limits are Semantic Scholar's decision; the documented introductory limit is 1 request per second.
- A key is personal. Never commit it, paste it into issues or chats, or share it. The [API License Agreement](https://www.semanticscholar.org/product/api/license) doesn't allow redistributing or sublicensing API access.
- Contributors don't need a key: the test suite uses mocked responses.

## Setup

Prerequisites: Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), and git. Claude Code is optional; you only need it for the slash commands.

```powershell
git clone https://github.com/ArthurgLeonida/ai-paper-search.git
cd ai-paper-search
uv sync                                   # creates .venv and installs dependencies
uv run pytest                             # all tests use mocked API responses; no key needed
uv run -q paperscout doctor               # shows data location and whether a key is configured
```

1. **API key (recommended).** Once you have one:
   ```powershell
   Copy-Item .env.example .env            # macOS/Linux: cp .env.example .env
   notepad .env                           # set the line: S2_API_KEY=<your key>   (no quotes needed)
   git check-ignore -v .env               # must print a .gitignore rule; .env is never committed
   uv run -q paperscout doctor --live     # sends ONE request (no retries) and shows the HTTP status, never the key
   ```
   Real environment variables take precedence over `.env`. The key is sent only as the `x-api-key` header and is never logged.
2. **Open Claude Code in the repository folder** (CLI or VS Code). The project skills in `.claude/skills/` appear as slash commands.
3. **Describe your research background with `/setup`.** This is what lets Claude understand you and recommend papers that suit you. `/setup` offers three ways in:
   - **Documents.** Put your CV, your own papers, a project description and reading-history exports in `documents/` (see [documents/README.md](documents/README.md)). `/setup` reads them, cross-checks them, and proposes your profile.
   - **Single CV or paste.** Paste one document and answer follow-up questions.
   - **Interview.** Answer questions section by section.

   You confirm everything before it's written. `/setup` creates your **private** background in `profile/`:

   | File | Purpose |
   |---|---|
   | `01-researcher-background.md` | Career stage, experience, own papers, tools, and a **knowledge map** (expert / working / familiar / new per topic) |
   | `02-research-goals.md` | Current project, research question, interest rings, seed papers, criteria |
   | `03-reading-style.md` | Time budget, how you learn, devices and tools |
   | `04-recommendation-criteria.md` | What makes a paper a must-read *for you*; your meaning of foundation, baseline, candidate method, survey and peripheral |
   | `profile.json` | The machine-readable part the ranking uses, loaded with `paperscout profile sync` |

   It also creates `CLAUDE.local.md`, which loads those files into every Claude Code session. `profile/`, `CLAUDE.local.md` and the contents of `documents/` are **git-ignored**, so your background never leaves your computer. Update parts later with `/setup --section knowledge` (or goals, reading, criteria, calibrate). How Claude and the ranking use the background is explained in [docs/background.md](docs/background.md).

**Where data lives:** `%LOCALAPPDATA%\paperscout` on Windows, `~/.local/share/paperscout` elsewhere. That covers the database, cache, saved reports and note scaffolds. Override it with `PAPERSCOUT_HOME`.

**Experiments without touching your library.** Point `PAPERSCOUT_HOME` at a scratch folder, for example `$env:PAPERSCOUT_HOME = "$PWD\data\scratch"` (`data/` is git-ignored). The live smoke test always uses its own isolated database and stops at the first 429 or when its request budget runs out:
```powershell
uv run -q python scripts/live_smoke.py --budget 15    # lookup, search, recommendations, dedup, save, rank, feedback, plan, assessment
```

**Request limits.** Every command has a hard cap of 40 HTTP attempts (`PAPERSCOUT_S2_MAX_REQUESTS`). Retries are bounded: 2 without a key, 3 with one. Failures print sanitized diagnostics (status, gateway error type, Retry-After, request ID), never the key.

**Windows PowerShell notes.** Use the plain CLI form, for example `uv run -q paperscout rank "3D Scene Editing" --unread`. Windows PowerShell 5.1 splits quoted text that contains double quotes, so `slash … --args '"…"'` is rejected rather than misread; pipe it instead: `'"3D Scene Editing" --unread' | uv run -q paperscout slash rank`. Inside Claude Code, the skills use the Bash tool and aren't affected. If uv warns that `VIRTUAL_ENV … does not match` (for example with pyenv), the warning is harmless, and `uv run -q` hides it.

## Slash commands (Claude Code, opened in the repository folder)

| Command | Purpose |
|---|---|
| `/setup [--section NAME]` | Build or update your private research background (documents, CV, or interview) |
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

The order of results comes from deterministic heuristic scores. Your knowledge map adjusts recommended reading depth, but never priority. Claude explains the results in terms of your background and doesn't change the stored order; stored Claude assessments are shown for reference only (see [docs/ranking.md](docs/ranking.md)).

Feedback labels: `relevant`, `irrelevant`, `already_known`, `read_later`, `read_overview`, `understood`, `deep_study`, `implemented`, `not_useful`.

## The same commands from a terminal

```powershell
uv run -q paperscout recent text-guided 3D Gaussian scene editing --days 60 --limit 8
uv run -q paperscout folders create "3D Scene Editing" --description "Editing 3D scene representations" --include "3D scene editing"
uv run -q paperscout add <paper-id-or-url> --folder "3D Scene Editing"
uv run -q paperscout import my-s2-library-export.bib --folder "3D Scene Editing" --dry-run
uv run -q paperscout import documents/reading-history/zotero.bib --folder "Reading history" --mark already_known
uv run -q paperscout profile sync                     # reload profile/profile.json after editing it
uv run -q paperscout feedback-log --limit 20
uv run -q paperscout rank "3D Scene Editing" --unread
uv run -q paperscout plan --hours 5
uv run -q paperscout feedback <paper-id> read_later "check the editing losses"
uv run -q paperscout capabilities
```

Add `--json` to get JSON output. Ranking commands also save Markdown and JSON reports in the data folder (`reports/`).

## Bringing in your existing libraries

- **Semantic Scholar website folders.** On the website, select the papers in a Library folder and use **Export**, or copy the paper links into a text file (one per line). Then run `paperscout import <file> --folder "<local folder>"`. BibTeX, RIS and plain ID/URL lists are supported.
- **Zotero collections.** Right-click the collection, choose **Export Collection…** (BibTeX or RIS), then use `paperscout import`. Read-only Zotero integration is the next planned feature.

## Documentation

- [Capability matrix](docs/capability-matrix.md): what each service officially supports, with sources
- [Architecture](docs/architecture.md): service layer, Claude authentication strategy, provenance, write safety
- [Research background](docs/background.md): what `/setup` builds, how Claude and the ranking use it, privacy
- [Ranking](docs/ranking.md): what determines the order, dimensions, knowledge-map depth adjustments, configuration, evidence limits
- [Evaluation](docs/evaluation.md): building your labeled set and comparing against baselines
- [Validation status](docs/validation.md): what has been tested live, with mocks, or not at all

## Roadmap

| Step | Scope | Status |
|---|---|---|
| Phase 1 | Research background via `/setup` (knowledge map, goals, reading style), profiles and folders, Semantic Scholar search and recommendations, dedup, heuristic ranking, CLI, reports, feedback, slash commands, reading plans, folder proposals, classification | implemented; live search validation pending |
| Next | Read-only Zotero integration: list collections, rank a collection, skip papers already in the library | planned |
| Later | Scheduled discovery, permitted full-text assessment, bulk search. An MCP server only if it adds a capability the slash commands lack. Zotero writes only with preview + explicit confirmation | not started |

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests and project rules, and [SECURITY.md](SECURITY.md) for reporting vulnerabilities and handling API keys.

## License and attribution

The code is released under the [MIT License](LICENSE).

Paper data comes from [Semantic Scholar](https://www.semanticscholar.org/) and is subject to the [Semantic Scholar API License Agreement](https://www.semanticscholar.org/product/api/license), not to this project's license. If you publish research that uses this data, cite *The Semantic Scholar Open Data Platform*, as the API license asks. paperscout is an independent project, not affiliated with or endorsed by the Allen Institute for AI or Semantic Scholar.
