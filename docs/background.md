# Research background

paperscout recommends papers *for a particular researcher*. To do that well, it needs to know what you already know, what you're working on, and how you read. `/setup` collects this once and keeps it on your computer. The design follows the profile-onboarding pattern of [ai-job-search](https://github.com/ai-job-search-dev/ai-job-search), adapted for research reading and for a public repository.

## What `/setup` builds

| File (in `profile/`, git-ignored) | Contents | Read by |
|---|---|---|
| `01-researcher-background.md` | Career stage, education, experience, own papers, tools, compute, **knowledge map** | Claude (every session) |
| `02-research-goals.md` | Project, research question, outcomes, interest rings, seed papers, criteria, constraints | Claude (every session) |
| `03-reading-style.md` | Time budget, session length, learning style, devices, tools, access | Claude (every session) |
| `04-recommendation-criteria.md` | Must-read criteria, your definitions of foundation / baseline / candidate method / survey / peripheral, fit and friction signals, calibration from feedback | Claude (every session) |
| `profile.json` | Machine-readable subset: field, research question, keywords, seeds, criteria, recency, knowledge map, optional arXiv categories, weekly hours | paperscout's deterministic ranking, after `paperscout profile sync` |
| `../CLAUDE.local.md` | `@imports` of the four prose files | Claude Code, which loads it at the start of every session |

The tracked templates live in `templates/`. Your filled-in copies are never committed.

## Three ways in

1. **Documents folder.** Put a CV, your own papers, a project description and reading-history exports in `documents/` (layout in `documents/README.md`). `/setup` reads them, cross-checks dates and titles, and proposes additions and conflicts separately before writing anything.
2. **Single CV or paste.** It extracts what the document supports and asks follow-up questions for the rest.
3. **Interview.** A conversation, section by section: background, knowledge, goals, reading, criteria.

All three paths end the same way: prose files, then `profile.json`, then `paperscout profile sync`, then `CLAUDE.local.md`, and optionally a reading-history import. Re-running is safe: it reads before writing, changes nothing you didn't confirm, and labels anything inferred (for example *[inferred from cv/: confirm]*).

Update a single part with `/setup --section background|knowledge|goals|reading|criteria|reading-history|calibrate|sync`. `calibrate` reads your feedback (`paperscout feedback-log`) and proposes patterns for *Calibration From Feedback*, and writes only the ones you confirm.

## How the background is used

**By Claude (interpretation).** The skills explain every recommendation against your goals and knowledge map:
- which prerequisites you're missing, and which background papers cover ground you already know
- how a paper relates to your project, using your own role definitions
- how to approach it given your reading style

If Claude would prioritise something differently, it says so in a separate "Claude's suggested adjustments (interpretation, not stored)" section. The output is never described as AI-ranked.

**By the deterministic ranking (`heuristic-v2`).** `profile.json` drives:
- topic relevance and project usefulness (keywords, research question, outcomes)
- seed affinity (seed papers, plus papers you marked relevant)
- recency (your preference)
- exclusions (out-of-scope topics)
- reading plans (weekly hours)

The **knowledge map** adjusts *depth only*:

| Your level for a topic the paper mentions | Effect |
|---|---|
| `expert` | A foundational, prerequisite or survey paper is lightened to an **overview**: likely review material |
| `new` | A relevant (P1/P2) foundational, prerequisite or survey paper is deepened to a **substantive** read. A method paper gets a note to read its prerequisites first |
| `working`, `familiar` | No change |

Priority and ordering never change, and each adjustment appears in reports as a "Your background" line with its reason.

**By discovery filters.** Papers you mark `already_known` (directly, or through `import --mark already_known` on a reading-history export) are excluded from `/recent` and `/discover` unless you pass `--include-read`.

## Privacy

- `profile/`, `CLAUDE.local.md` and everything in `documents/` except its README and folder skeleton are git-ignored. `/setup` checks this before writing anything and stops if the rules are missing.
- Nothing about your background is sent to Semantic Scholar beyond what a search needs: query terms, seed paper identifiers, and paper IDs to resolve.
- Claude Code reads the files locally as part of your session.

## Without Claude Code

Copy `templates/profile/*` to `profile/`, fill in the placeholders by hand (`profile.json` matters most), and run `uv run -q paperscout profile sync`. It rejects files that still contain template placeholders. `uv run -q paperscout doctor` shows what's filled in, whether `CLAUDE.local.md` loads it, and whether `profile.json` matches the synced profile.
