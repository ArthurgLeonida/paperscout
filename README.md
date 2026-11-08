# paperscout

**A research reading assistant for [Claude Code](https://code.claude.com/).** Tell it what you work on once. It finds papers, explains why each one matters to *you*, and suggests what to read first and how deeply.

It uses the official arXiv and Semantic Scholar APIs, and **needs no API key**: it works out of the box with arXiv.

> **Status: early (v0.1).** Every feature is covered by tests with mocked API responses, and the main workflow (lookup, search, saving, ranking, feedback, reading plan, assessments) has passed a live end-to-end run on arXiv. Semantic Scholar search and recommendations still await live validation, because they need a key. See [docs/validation.md](docs/validation.md).

## Contents

1. [What it does](#what-it-does)
2. [Quick start](#quick-start)
3. [Commands](#commands)
4. [Everyday recipes](#everyday-recipes)
5. [Reading the results](#reading-the-results)
6. [Your research background](#your-research-background)
7. [Paper sources and API keys](#paper-sources-and-api-keys)
8. [Settings](#settings)
9. [Using the terminal](#using-the-terminal)
10. [Importing your existing library](#importing-your-existing-library)
11. [Troubleshooting](#troubleshooting)
12. [Documentation](#documentation), [Roadmap](#roadmap), [Contributing](#contributing), [License and attribution](#license-and-attribution)

## What it does

paperscout answers one question: **"Which papers should I read for my research goal, why, in what order, and how deeply?"**

| | |
|---|---|
| **Finds papers** | Recent papers, balanced reading lists (foundations, surveys, methods), and where a field is heading |
| **Explains each choice** | A 1–100 score from six visible criteria, each with its source and reason, and why the paper matters for your project |
| **Knows you** | A private background (CV, papers, project, what you already know) that `/setup` builds once |
| **Organises your reading** | Local folders, reading plans, note templates, and feedback that improves later results |
| **Gets you the PDFs** | The top papers' arXiv PDFs, numbered in reading order, ready for a tablet |

It doesn't claim to find objectively "best" papers, and it never declares a method "the state of the art". It shows what it counted and where every judgment came from.

## Quick start

You need [Python 3.11+](https://www.python.org/), [uv](https://docs.astral.sh/uv/) and git. [Claude Code](https://code.claude.com/) is needed for the slash commands; the terminal commands work without it.

**1. Install**

```bash
git clone https://github.com/ArthurgLeonida/ai-paper-search.git
cd ai-paper-search
uv sync                        # installs everything into .venv
uv run -q paperscout doctor    # checks the setup and shows where your data will live
```

**2. Open Claude Code in this folder** (in the terminal or VS Code). The commands below appear as slash commands.

**3. Describe your research, once**

```
/setup
```

Give it your CV and papers, or just answer its questions. It writes a private profile that every recommendation uses ([details](#your-research-background)).

**4. Find papers**

```
/discover                                # a reading list from your saved searches
/recent text-guided 3D scene editing     # what's new on a topic (last 90 days)
/trends                                  # where your field is heading
```

**5. Read them**

```
/download                                # the top 10 PDFs, numbered in reading order
/reading-plan --week                     # a plan that fits your weekly reading time
```

You can also ask in plain language, for example *"find recent papers that could be baselines for my project"*, and Claude picks the right command.

## Commands

Run these in Claude Code, opened in this folder. Topics need no quotes (`/recent flow matching`), but folder names with spaces do (`/rank "3D Scene Editing"`). Unknown options are rejected with a suggestion, and `/research-help <command>` lists every option.

### Find papers

| Command | What it does | Example |
|---|---|---|
| `/recent [topic]` | Recently published papers, ranked for you. Default window: 90 days (`--days N`, `--since`, `--until`) | `/recent diffusion guidance --days 30` |
| `/discover [topic]` | A balanced reading list (prerequisites, surveys, methods) with no date limit | `/discover flow matching` |
| `/authors <names>` | Papers by specific researchers (a lab, a department's faculty), ranked for you. Default window: 2 years | `/authors Seungryong Kim --affiliation KAIST` |
| `/trends [topic]` | Where a field is heading: terms rising or fading per quarter, methods papers name, claims, citation velocity | `/trends --months 18` |

With no topic, `/recent`, `/discover` and `/trends` use the saved searches in your profile, and `/authors` with no names uses the researchers you follow. Add `--quick` to skip the Claude judges and get computed scores in seconds. `--limit N` sets the list length (default 20, up to 50).

### Organise

| Command | What it does | Example |
|---|---|---|
| `/folders` | List, show, create or propose local research folders | `/folders propose medical computer vision` |
| `/classify <paper>` | Suggest which folders a paper belongs in (changes nothing) | `/classify 2308.04079` |
| `/add <paper> --folder "F"` | Add a paper to a folder | `/add 2308.04079 --folder "Baselines"` |

### Prioritise and read

| Command | What it does | Example |
|---|---|---|
| `/rank "<folder>"` | Rank the papers in a folder (`--unread`, `--new`, or `--all` folders) | `/rank "Baselines" --unread` |
| `/assess <paper>` | Should I read this one, and how deeply? | `/assess https://arxiv.org/abs/2308.04079` |
| `/download` | Save the top papers' arXiv PDFs (default 10) with a reading list | `/download --top 5` |
| `/reading-plan` | A time-boxed plan from unread, read-later or newly found papers | `/reading-plan --hours 3` |

### Learn and give feedback

| Command | What it does | Example |
|---|---|---|
| `/note <paper>` | A reading-note template for a first, second or deep pass | `/note 2308.04079 --pass first` |
| `/paper-feedback <paper> <label>` | Tell paperscout what you thought, so later results adapt | `/paper-feedback 2308.04079 already_known` |

Feedback labels: `relevant`, `irrelevant`, `already_known`, `read_later`, `read_overview`, `understood`, `deep_study`, `implemented`, `not_useful`.

### Set up and get help

| Command | What it does |
|---|---|
| `/setup [--section NAME]` | Build or update your research background. Sections: `knowledge`, `goals`, `searches`, `reading`, `criteria`, `calibrate` |
| `/research-help [command]` | All commands, options, defaults and limitations |

`/reading-plan` and `/paper-feedback` have these names because `/plan` and `/feedback` are built-in Claude Code commands.

## Everyday recipes

**Papers for my project this week.** Run `/recent` (or `/discover`) with no topic. It runs your saved searches, merges the results and ranks them against your profile. Then use `/download` and `/reading-plan --week`.

**Checking related work before a submission.** Run `/discover <your topic>` for established work, `/recent <your topic> --days 180` for the latest, and `/trends` to see what reviewers may expect. `/assess` any paper you're unsure about.

**Following researchers or a lab.** Run `/authors Seungryong Kim; <another name> --affiliation KAIST`. Each paper counts only if the name and the institution appear together on its author list, so namesakes elsewhere are left out. Name the people, not the school: if you give a school or lab, Claude can read its official faculty page and let you pick. To follow them, save them as `followed_authors` in your profile, and `/authors` with no names will use them. Details: [docs/authors.md](docs/authors.md).

**Seeing where the field is heading.** Run `/trends` for your saved searches, or `/trends gaussian splatting editing --months 18` for one topic. Broader searches give bigger samples and firmer counts.

**Learning a new field.** Run `/discover flow matching`. Mark the field as `new` with `/setup --section knowledge` so its background papers get a proper read, then run `/reading-plan --hours 3`.

**Reading on a tablet.** `/download` saves files like `01 - 65 - <title> (<arXiv id>).pdf` in `Downloads/paperscout/<date> <list>/`, with a `reading-list.md`. Copy the folder over, or point `PAPERSCOUT_DOWNLOAD_DIR` at a synced folder. Only arXiv papers are downloaded, and only for your personal use.

**Keeping results relevant.** Work through a paper in this order: `/assess` it, `/add` it to a folder, `/rank` the folder, take notes with `/note`, then record your view with `/paper-feedback`. `/setup --section calibrate` turns your feedback into criteria.

> **Tip:** use 3–6 words for a topic, phrased like a paper title. `/discover multi-view consistent gaussian splatting editing` works far better than a whole research question.

## Reading the results

### The answer layout

`/recent`, `/discover` and `/rank` always answer in the same order:

1. **Summary:** papers per priority (P1, P2, P3), how many are new, how many were left out, and how long the P1 papers take to read.
2. **Table:** every paper with its score, priority, date, role, suggested reading depth, notes (such as "new" or "in Baselines") and link.
3. **Top picks:** three lines per P1 paper: *why read it*, *check* (uncertainties, friction, prerequisites you lack) and *next*.
4. **Claude's suggested adjustments:** only when Claude would order things differently. They never change the stored ranking.

`/trends` follows the same idea: a summary, a table of directions, three lines per direction, then the counts behind them. Every run also saves the full report as Markdown and JSON in your data folder.

### How papers are scored

Each paper gets a **score from 1 to 100**, the weighted mean of six criteria:

| Criterion | Weight | Where it comes from |
|---|---|---|
| Relevance to the topic or searches | 30 | a Claude judge subagent (computed with `--quick`) |
| Usefulness for your current project | 25 | a Claude judge that reads your background (computed with `--quick`) |
| Foundational value | 15 | a Claude judge (or reference lists and citations) |
| Recency | 10 | computed, scaled by your recency preference |
| Popularity | 10 | computed from citations per year; unknown for papers under a year old |
| Similarity to your seed papers | 10 | computed |

- **P1** is 60 or more, **P2** 40 or more, **P3** 25 or more.
- Unknown criteria are left out, never counted as zero.
- The score orders a reading list. It isn't a quality measure, and gaps under about 10 points mean little.
- Your knowledge map changes how deeply you should read a paper, never its score.

Details: [docs/ranking.md](docs/ranking.md). Trend statistics: [docs/trends.md](docs/trends.md).

### What paperscout won't claim

From titles and abstracts, nobody can verify that experiments are fair, results are correct or reproducible, code is available, or a method is the state of the art. paperscout leaves those as "unknown", and `/trends` reports state-of-the-art *claims* as claims.

## Your research background

`/setup` offers three ways to start:

- **Documents:** put your CV, LinkedIn export, papers, thesis, project description or manuscript, and reading-history exports in `documents/` ([how](documents/README.md)). `/setup` reads and cross-checks them.
- **One document:** paste your CV and answer a few follow-up questions.
- **Interview:** answer questions section by section.

You confirm everything before it's written. `/setup` then creates these files in `profile/`:

| File | What it holds |
|---|---|
| `01-researcher-background.md` | Career stage, experience, own papers, tools, and a **knowledge map** (expert / working / familiar / new per topic) |
| `02-research-goals.md` | Current project, research question, interests, seed papers, criteria |
| `03-reading-style.md` | Weekly reading time, how you learn, devices |
| `04-recommendation-criteria.md` | What makes a paper a must-read for you, and what you mean by foundation, baseline, candidate method, survey and peripheral |
| `profile.json` | The machine-readable part the ranking uses (`uv run -q paperscout profile sync` reloads it) |

It also saves 4–6 short **searches**, used whenever you give no topic, and creates `CLAUDE.local.md`, which loads your background into every Claude Code session. To update one part later, run `/setup --section <name>`.

> **Privacy:** `profile/`, `CLAUDE.local.md` and the contents of `documents/` are git-ignored, so your background never leaves your computer. More in [docs/background.md](docs/background.md).

## Paper sources and API keys

paperscout runs entirely on your computer. There's no shared server, and the project ships no API key.

| | arXiv (used without a key) | Semantic Scholar (used with a key) |
|---|---|---|
| **Key** | None | Your own, from the [official form](https://www.semanticscholar.org/product/api#api-key-form). Approval and limits are Semantic Scholar's decision |
| **Coverage** | arXiv preprints (most computer vision and ML papers) | Papers across publishers and venues |
| **Discovery** | Keyword search, optionally limited to your arXiv categories | Search, recommendations from seed papers, reference lists |
| **Citations** | From [OpenAlex](https://openalex.org) (only identifiers and a few titles are sent) | Included |
| **Speed** | One request every 3 s (arXiv's terms; enforced) | One request per second with a key |

On arXiv, reports say plainly what's missing: there are no recommendations or reference lists, so shared prerequisites aren't inferred.

**OpenAlex** (keyless) fills two gaps whichever source you use: citation counts on arXiv (only paper identifiers and a few titles are sent) and `/authors`, which sends the researcher names and affiliation you give it. Without a key, OpenAlex requests share a small free daily allowance with everyone on your network. A free key from [openalex.org/settings/api](https://openalex.org/settings/api), set as `OPENALEX_API_KEY` in `.env`, gives you your own. Reports show how much of today's allowance is left, and `PAPERSCOUT_OPENALEX=off` turns OpenAlex off.

**Adding a Semantic Scholar key (optional)**

```bash
cp .env.example .env                  # Windows PowerShell: Copy-Item .env.example .env
# edit .env and set:  S2_API_KEY=<your key>   (no quotes needed)
git check-ignore -v .env              # must print a .gitignore rule, so .env is never committed
uv run -q paperscout doctor --live    # one request to the paper source and one to OpenAlex; shows the results
```

The key is sent only in the `x-api-key` header and is never logged. It's personal: never commit or share it, because the [API License Agreement](https://www.semanticscholar.org/product/api/license) doesn't allow redistributing access.

## Settings

All settings are optional. Put them in `.env` or set them as environment variables; real environment variables take precedence.

| Setting | Default | What it does |
|---|---|---|
| `S2_API_KEY` | none | Your Semantic Scholar key |
| `OPENALEX_API_KEY` | none | Your free [OpenAlex key](https://openalex.org/settings/api): your own daily allowance instead of one shared by your network |
| `PAPERSCOUT_PROVIDER` | `auto` | `auto` uses Semantic Scholar with a key and arXiv without. Force `arxiv` or `semantic-scholar` here, or per command with `--provider` |
| `PAPERSCOUT_HOME` | `%LOCALAPPDATA%\paperscout` on Windows, `~/.local/share/paperscout` elsewhere | Where the database, cache, reports and notes live |
| `PAPERSCOUT_DOWNLOAD_DIR` | `Downloads/paperscout` | Where `/download` saves PDFs |
| `PAPERSCOUT_RECENT_DAYS` | `90` | Default `/recent` window |
| `PAPERSCOUT_OPENALEX` | `on` | OpenAlex: citation counts when using arXiv, and `/authors` |
| `PAPERSCOUT_ARXIV_MAX_REQUESTS` | `10` | Maximum HTTP attempts to arXiv per command (`/trends` sizes its own budget, up to 80) |
| `PAPERSCOUT_S2_MAX_REQUESTS` | `40` | Maximum HTTP attempts to Semantic Scholar per command |

Responses are cached, so repeating a query costs nothing. Pacing, retries and the download size limit are described in [.env.example](.env.example).

**Trying things without touching your library:** point `PAPERSCOUT_HOME` at a scratch folder, such as `data/scratch` (`data/` is git-ignored).

## Using the terminal

Every slash command is also a terminal command: `uv run -q paperscout <command> …`.

```bash
uv run -q paperscout recent text-guided 3D Gaussian scene editing --days 60 --limit 8
uv run -q paperscout folders create "3D Scene Editing" --include "3D scene editing"
uv run -q paperscout add <paper-id-or-url> --folder "3D Scene Editing"
uv run -q paperscout rank "3D Scene Editing" --unread
uv run -q paperscout plan --hours 5
uv run -q paperscout feedback <paper-id> read_later "check the editing losses"
uv run -q paperscout trends diffusion guidance --months 12    # counts only; /trends adds the directions
```

A few commands exist only in the terminal:

| Command | What it does |
|---|---|
| `import <file> --folder "F"` | Import BibTeX, RIS or ID lists (`--mark already_known` for your reading history) |
| `profile list \| show \| use \| sync` | Manage profiles; `sync` reloads `profile/profile.json` |
| `judge packet \| record \| show --run R` | The judge workflow that `/rank`, `/recent` and `/discover` run for you |
| `trends packet \| record \| show --run R` | The trend-analyst workflow that `/trends` runs for you |
| `feedback-log`, `audit` | Your recent feedback, and the log of every write |
| `eval template \| run` | Evaluate the ranking against your own labels ([docs/evaluation.md](docs/evaluation.md)) |
| `capabilities`, `doctor [--live]`, `init` | What each integration supports, a setup check, and database creation |

Add `--json` to any command for JSON output.

## Importing your existing library

- **Semantic Scholar website folders:** select the papers in a Library folder and use **Export**, or paste their links into a text file (one per line). Then run `uv run -q paperscout import <file> --folder "<folder>"`.
- **Zotero collections:** right-click the collection, choose **Export Collection…** (BibTeX or RIS), then use `paperscout import`. Read-only Zotero integration is planned next.

Semantic Scholar website folders and Research Feeds have **no official API**, so paperscout never pretends to read or change them; it works with local folders instead (see the [capability matrix](docs/capability-matrix.md)).

## Troubleshooting

| Problem | What to do |
|---|---|
| Something looks wrong | Run `uv run -q paperscout doctor`. It shows the data folder, the active provider, whether a key is set and whether `.env` loaded |
| `HTTP 429` (rate limited) | Semantic Scholar without a key is often throttled. Wait, use arXiv, or request a key. Errors show sanitized diagnostics, never the key |
| "Request budget … is spent" | The per-command cap was reached and nothing more was sent. Run again later (cached results are reused) or raise the cap in `.env` |
| Windows PowerShell splits quoted arguments | Use the plain form, `uv run -q paperscout rank "3D Scene Editing"`, or pipe the raw arguments: `'"3D Scene Editing" --unread' \| uv run -q paperscout slash rank`. Slash commands in Claude Code aren't affected |
| OpenAlex allowance running low, or OpenAlex `HTTP 429` | Add a free `OPENALEX_API_KEY` to `.env`. `uv run -q paperscout doctor --live` shows how much is left today and when it resets |
| uv warns that `VIRTUAL_ENV … does not match` | Harmless (for example with pyenv); `uv run -q` hides it |
| A new command or agent doesn't appear | Restart Claude Code in this folder. Project skills and agents load when a session starts |

## Documentation

- [Capability matrix](docs/capability-matrix.md): what each service officially supports, with sources
- [Architecture](docs/architecture.md): service layer, Claude authentication strategy, provenance, write safety
- [Research background](docs/background.md): what `/setup` builds, how Claude and the ranking use it, privacy
- [Ranking](docs/ranking.md): the 1–100 score, criteria and weights, judge subagents, OpenAlex citations, depth adjustments, evidence limits
- [Authors](docs/authors.md): how `/authors` matches researchers to papers, and its limits
- [Trends](docs/trends.md): what `/trends` counts, the support labels, the trend analyst and its checks, request limits
- [Evaluation](docs/evaluation.md): building your labelled set and comparing against baselines
- [Validation status](docs/validation.md): what has been tested live, with mocks, or not at all

## Roadmap

| Step | Scope | Status |
|---|---|---|
| Phase 1 | Research background (`/setup`), folders, arXiv and Semantic Scholar discovery, 1–100 scoring with judge subagents and OpenAlex citations, reports, feedback, reading plans, `/download`, `/authors`, `/trends` | Implemented; live Semantic Scholar validation pending |
| Next | Read-only Zotero integration: list collections, rank a collection, skip papers already in your library | Planned |
| Later | Scheduled discovery, permitted full-text assessment, bulk search. An MCP server only if it adds something the slash commands can't. Zotero writes only with a preview and explicit confirmation | Not started |

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and project rules, and [SECURITY.md](SECURITY.md) for reporting vulnerabilities and handling API keys.

No key is needed to develop: `uv run pytest` uses mocked responses for both providers, and `uvx ruff check src tests scripts` runs the linter. The live smoke test, `uv run -q python scripts/live_smoke.py --budget 15`, uses its own isolated database and stops at the first 429 or when its request budget runs out.

## License and attribution

The code is released under the [MIT License](LICENSE).

Paper data comes from [arXiv](https://arxiv.org/) and [Semantic Scholar](https://www.semanticscholar.org/). Thank you to arXiv for use of its open access interoperability.

- **arXiv:** metadata is CC0 under the [arXiv API terms of use](https://info.arxiv.org/help/api/tou.html). paperscout stores metadata and links to arXiv abstract pages. It saves e-print PDFs only when you run `download`, to a local folder for your personal use, which the terms allow. It never serves or redistributes them, and you shouldn't either.
- **Semantic Scholar:** data is subject to the [Semantic Scholar API License Agreement](https://www.semanticscholar.org/product/api/license), not to this project's license. If you publish research that uses it, cite *The Semantic Scholar Open Data Platform*, as that license asks.

paperscout is an independent project, not affiliated with or endorsed by arXiv, the Allen Institute for AI, or Semantic Scholar.
