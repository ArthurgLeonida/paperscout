# paperscout

A research-paper discovery and reading-priority assistant for [Claude Code](https://code.claude.com/), built on the **official arXiv and Semantic Scholar APIs**. It works without any API key, through arXiv.

It answers *"Which papers should I read for this research goal, why, in what order, and at what depth?"* It doesn't claim to find objectively "best" papers.

- Discovery through arXiv search (no key needed) or Semantic Scholar search, recommendations and reference lists (with a key), deduplicated with provenance
- Transparent, versioned heuristic ranking. Relevance, project usefulness, recency, evidence, confidence and effort are reported separately; citation counts never raise priority.
- A private research background built by `/setup` (from your CV, papers and project documents, or an interview), so Claude knows what you already know and what you're working on
- Local research folders (SQLite), append-only feedback, reading plans, note scaffolds
- Claude Code slash commands (`/recent`, `/discover`, `/rank`, …) that call one tested service layer
- Honest limits: Semantic Scholar **website** folders and Research Feeds have no official API, so they're never presented as supported (see [docs/capability-matrix.md](docs/capability-matrix.md))

> **Status: early (v0.1).** Everything is covered by tests against mocked API responses. The full workflow (lookup, search, saving, ranking, feedback, reading plan, assessments) has passed a live end-to-end run on **arXiv**. Semantic Scholar search and recommendations are still awaiting live validation, because they need an API key. See [docs/validation.md](docs/validation.md).

## Paper sources: arXiv (no key) or Semantic Scholar (with a key)

paperscout runs entirely on your computer. There is no shared server, and the project ships no API key.

| | arXiv (default without a key) | Semantic Scholar (default with a key) |
|---|---|---|
| Key | None | Your own key from the [official form](https://www.semanticscholar.org/product/api#api-key-form). Approval and limits are Semantic Scholar's decision |
| Coverage | arXiv preprints (most CV/ML papers appear there) | Papers across publishers and venues |
| Data | Title, abstract, authors, categories, exact v1 submission date, DOI/journal ref when the authors added them | The same, plus venue, citation counts, machine-generated TLDRs |
| Discovery | Keyword search, optionally limited to your arXiv categories | Search, seed-based recommendations, reference lists (for prerequisites) |
| Limits | 1 request every 3 s (arXiv terms of use; enforced) | 1 request/s with a key; the unauthenticated pool is often throttled (HTTP 429) |

- `PAPERSCOUT_PROVIDER=auto` (the default) uses Semantic Scholar when `S2_API_KEY` is set, and arXiv otherwise. Force one with `PAPERSCOUT_PROVIDER=arxiv` or `semantic-scholar`, or per command with `--provider`. `uv run -q paperscout doctor` shows the active provider.
- On arXiv, reports say plainly what's missing: no citation counts, recommendations or reference lists, so foundational papers and shared prerequisites aren't inferred.
- A Semantic Scholar key is personal. Never commit it, paste it into issues or chats, or share it. The [API License Agreement](https://www.semanticscholar.org/product/api/license) doesn't allow redistributing or sublicensing API access.
- Contributors don't need a key: the test suite uses mocked responses for both providers.

## Setup

Prerequisites: Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), and git. Claude Code is optional; you only need it for the slash commands.

```powershell
git clone https://github.com/ArthurgLeonida/ai-paper-search.git
cd ai-paper-search
uv sync                                   # creates .venv and installs dependencies
uv run pytest                             # all tests use mocked API responses; no key needed
uv run -q paperscout doctor               # shows data location and whether a key is configured
```

1. **Optional: a Semantic Scholar API key.** Without one, everything runs on arXiv. Once you have a key:
   ```powershell
   Copy-Item .env.example .env            # macOS/Linux: cp .env.example .env
   notepad .env                           # set the line: S2_API_KEY=<your key>   (no quotes needed)
   git check-ignore -v .env               # must print a .gitignore rule; .env is never committed
   uv run -q paperscout doctor --live     # sends ONE request (no retries) to the active provider and shows the HTTP status
   ```
   Real environment variables take precedence over `.env`. The key is sent only as the `x-api-key` header and is never logged.
2. **Open Claude Code in the repository folder** (CLI or VS Code). The project skills in `.claude/skills/` appear as slash commands.
3. **Describe your research background with `/setup`.** This is what lets Claude understand you and recommend papers that suit you. `/setup` offers three ways in:
   - **Documents.** Put your CV, an optional LinkedIn export, your own papers and thesis, a project description or manuscript in preparation, and reading-history exports in `documents/` (see [documents/README.md](documents/README.md)). `/setup` reads them, cross-checks them, and proposes your profile.
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

   It also creates `CLAUDE.local.md`, which loads those files into every Claude Code session. `profile/`, `CLAUDE.local.md` and the contents of `documents/` are **git-ignored**, so your background never leaves your computer. `/setup` also saves 4–6 short **searches** that cover your core interests, which `/recent` and `/discover` run when you give no topic. Update parts later with `/setup --section knowledge` (or goals, searches, reading, criteria, calibrate). How Claude and the ranking use the background is explained in [docs/background.md](docs/background.md).

**Where data lives:** `%LOCALAPPDATA%\paperscout` on Windows, `~/.local/share/paperscout` elsewhere. That covers the database, cache, saved reports and note scaffolds. Override it with `PAPERSCOUT_HOME`.

**Experiments without touching your library.** Point `PAPERSCOUT_HOME` at a scratch folder, for example `$env:PAPERSCOUT_HOME = "$PWD\data\scratch"` (`data/` is git-ignored). The live smoke test always uses its own isolated database and stops at the first 429 or when its request budget runs out:
```powershell
uv run -q python scripts/live_smoke.py --budget 15    # lookup, search, recommendations, dedup, save, rank, feedback, plan, assessment
```

**Request limits.** Each command has a hard cap on HTTP attempts: 40 to Semantic Scholar (`PAPERSCOUT_S2_MAX_REQUESTS`) and 10 to arXiv (`PAPERSCOUT_ARXIV_MAX_REQUESTS`). arXiv requests are always at least 3 seconds apart. Retries are bounded, and failures print sanitized diagnostics (status, error type, Retry-After, request ID), never the key. Responses are cached, so repeating a query costs nothing.

**Windows PowerShell notes.** Use the plain CLI form, for example `uv run -q paperscout rank "3D Scene Editing" --unread`. Windows PowerShell 5.1 splits quoted text that contains double quotes, so `slash … --args '"…"'` is rejected rather than misread; pipe it instead: `'"3D Scene Editing" --unread' | uv run -q paperscout slash rank`. Inside Claude Code, the skills use the Bash tool and aren't affected. If uv warns that `VIRTUAL_ENV … does not match` (for example with pyenv), the warning is harmless, and `uv run -q` hides it.

## Slash commands (Claude Code, opened in the repository folder)

| Command | Purpose |
|---|---|
| `/setup [--section NAME]` | Build or update your private research background (documents, CV, or interview) |
| `/recent [topic] [--days N \| --since D] [--until D] [--folder F] [--profile P] [--limit N] [--include-read]` | Recent papers (default window 90 days, configurable); no topic = your saved searches |
| `/discover [topic] [--folder F] [--profile P] [--limit N] [--seed ID]` | Balanced reading list: prerequisites, surveys, methods; no topic = your saved searches |
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

## How to use it day to day

You can ask in plain language ("find recent papers that could be baselines for my project", "I want to learn flow matching, where do I start?") and Claude picks the right command, or type the commands yourself.

- **Papers for you:** `/recent` or `/discover` with no topic runs your saved searches, merges the results, and ranks them against your profile.
- **A specific topic:** use 3–6 words, phrased like a paper title. `/discover multi-view consistent gaussian splatting editing` works far better than a whole research question.
- **A field you want to learn:** give the field as the topic (`/discover flow matching`). If it differs from your project, the report says it's ranked by topic only. Mark the field `new` with `/setup --section knowledge` so background papers on it get a proper read, and use `/reading-plan --hours 3` for a study plan.
- **The loop:** `/assess` a paper, `/add` it to a folder, `/rank` the folder, `/reading-plan --week`, `/note <paper> --pass first` for your tablet, and `/paper-feedback` so later results fit you better (`/setup --section calibrate` turns the feedback into criteria).

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

Paper data comes from [arXiv](https://arxiv.org/) and [Semantic Scholar](https://www.semanticscholar.org/). Thank you to arXiv for use of its open access interoperability. arXiv metadata is CC0 under the [arXiv API terms of use](https://info.arxiv.org/help/api/tou.html). paperscout stores metadata only, links to arXiv abstract pages, and never stores or serves PDFs. Semantic Scholar data is subject to the [Semantic Scholar API License Agreement](https://www.semanticscholar.org/product/api/license), not to this project's license; if you publish research that uses it, cite *The Semantic Scholar Open Data Platform*, as that license asks. paperscout is an independent project, not affiliated with or endorsed by arXiv, the Allen Institute for AI, or Semantic Scholar.
