# Ranking and evidence standards

The goal is to answer *which papers should I read for this goal, why, in what order, and at what depth?* It doesn't claim to find objectively "best" papers.

## The 1–100 score (`score-v3`)

Every paper gets six criteria, each rated **1–100 or unknown**, and a **score** from 1 to 100: the weighted mean of the criteria that are known. Unknown criteria are **dropped and the weights renormalised**, never counted as zero.

| Criterion | Weight | Computed by paperscout (always available) | Rated by a judge subagent (unless `--quick`) | Unknown when |
|---|---|---|---|---|
| **Relevance** | 30 | Share of the topic's terms in title and abstract (title and phrase matches weigh more); TF-IDF similarity for long criteria texts | `relevance-judge`: how directly the paper is *about* the topic, folder or saved searches being ranked | nothing to rank against |
| **Project usefulness** | 25 | Profile keyword and criteria hits, plus TF-IDF similarity to the research question, description and desired outcomes | `usefulness-judge`: how much it helps the current project (must-read criteria, baselines, candidate methods, related work) | no profile, or the topic differs from the profile |
| **Foundational value** | 15 | In the reference lists of top results (85 if shared by two or more, 70 for one; Semantic Scholar only); old and widely cited (75) | `foundation-judge`: how much the field and your area build on it | no evidence, or a judge finds it too recent to tell |
| **Recency** | 10 × preference | 100 at publication, halving every 180 / 540 / 1095 days for a strong / moderate / no recency preference | — | no date |
| **Popularity** | 10 | Citations per year on a log scale: about 13 at 1/year, 45 at 10, 74 at 50, 100 at 200 or more | — | no citation count, or under 12 months old |
| **Seed similarity** | 10 | Text similarity to your seed papers and papers you marked relevant; Semantic Scholar recommendation rank | — | no seeds |

Then:
- matching an **exclusion criterion** halves the score; being closer to a negative seed than to the positive ones multiplies it by 0.6;
- **priority** follows from the score: **P1** ≥ 60, **P2** ≥ 40, **P3** ≥ 25, otherwise skip; `irrelevant` or `not_useful` feedback forces skip;
- the foundation weight is 0 when the profile sets `include_foundational: false`, and the recency weight is 0 with no recency preference.

Reports show the score, each criterion's value, its source (`computed`, or `judge <model>, <confidence> confidence`) and its basis. `--verbose` adds each criterion's share of the score. **The score orders a reading list. It isn't a probability or a quality measure, and differences under about 10 points mean little.**

## Shortlists

`/recent` and `/discover` keep the top **20** papers by default (`--limit N`, up to 50) as the run's shortlist: it's what the report shows and saves, what the judges rate (`judge packet` defaults to it), and what `download` takes its top papers from. The run stores the shortlist's paper IDs in order, so a later `download` follows exactly the list you saw, including the judged re-ranking.

## Judge subagents

`/rank`, `/recent` and `/discover` (unless you add `--quick`) ask three Claude Code subagents to rate the run's top papers:

1. The CLI ranks with computed criteria and stores the run.
2. `paperscout judge packet --run RUN --top N` writes a packet (`proposals/judge-<run>/packet.json`, git-ignored): the relevance target, the profile's goal fields, the paths of your background files, and each paper's title, date, citations and abstract, all marked untrusted.
3. `relevance-judge`, `usefulness-judge` and `foundation-judge` (`.claude/agents/`) run in parallel. They can only read files, they read the packet and your `profile/*.md`, and they return JSON: one rating per paper with a score (or `null`), a confidence, the basis (`abstract`, `tldr`, `metadata` or `model_knowledge`) and a one-sentence reason.
4. `paperscout judge record --run RUN --file ratings.json` validates everything before storing anything. It checks that the batch belongs to the run, rates only the run's papers, once each, with scores of 1–100, one batch per criterion, and doesn't claim abstract evidence for a paper without one. It then re-rates the run and stores the result as a new run.

A judge rating **replaces** the computed value for that criterion. A `null` rating keeps the computed value with a note. Ratings are append-only and keyed to the ranking context (the relevance target, the profile's content and the exclusions), so later rankings of the same folder or saved searches reuse them, and editing the profile or the folder criteria retires them. `--quick` ignores them.

Judge ratings are LLM judgments of an abstract, not verified facts. Confidence can be *high* only when at least two criteria are judged from an abstract and every judged criterion has high confidence; lexical heuristics alone never claim it. Reports say how many papers have judged criteria, and the skills must say which criteria were judged rather than calling the list "AI-ranked". Claude's own commentary in a skill still never changes the stored order; disagreement goes in a separate "Claude's suggested adjustments (interpretation, not stored)" section. Stored `ClaudeAssessment`s (from `/assess`) remain reference-only.

## Citation counts and OpenAlex

Semantic Scholar returns citation counts. arXiv doesn't, so paperscout asks **OpenAlex** (keyless, CC0) for them, sending only paper identifiers, and titles for a few older papers. An arXiv DOI often returns the published version's merged record, which is matched back through its `arxiv.org/abs/<id>` location. When only a preprint record matches a paper older than a year, the published version may be a separate record, so up to 5 such papers per command are also checked by exact title and year. Turn this off with `PAPERSCOUT_OPENALEX=off`; popularity is then unknown on arXiv. See `capability-matrix.md`.

## Reading role, depth, evidence and confidence

| Field | How it is set |
|---|---|
| Reading role | Metadata rules: reference-list provenance → prerequisite; `Review` type or survey title → survey; benchmark/dataset title; ≥ 3 years old and ≥ 200 citations → foundational; ≤ 18 months → recent method; else method |
| Depth | skip for skipped papers; overview for surveys; for P1, **deep** when two of relevance, usefulness and seed similarity are ≥ 85, else substantive; P2 foundational or prerequisite papers get substantive; otherwise overview |
| Reading effort | overview 20–40 min, substantive 1.5–2.5 h, deep 4–7 h, surveys 40–75 min. **Planning heuristics, not predictions.** |
| Evidence level | `metadata` (title only), `tldr` (machine-generated), `abstract`; `full_text` reserved for Phase 3 |
| Confidence | low for metadata/TLDR evidence or three unknown signals; medium otherwise; high only from confident judges (above) |

## Deliberate design choices

- **Citations never dominate.** They feed only popularity (weight 10) and the computed foundation value (weight 15, and only for papers at least 3 years old with 200+ citations). Relevance and usefulness carry 55 of the 100 weight points.
- **New papers aren't penalised for having few citations.** Popularity is unknown, not low, for papers under 12 months old, and the foundation judge returns `null` for recent papers that don't present a reusable building block.
- Venue prestige and author fame aren't used.
- No criterion judges scientific quality.

## Abstract-level limits

From metadata and abstracts, paperscout, the judges and the skills make no judgments about experimental fairness, correctness of derivations, reproducibility, code availability (unless independently verified), or state-of-the-art superiority. `ClaudeAssessment` enforces this when Claude's assessments are stored: those fields must be `unknown` unless the evidence level is `full_text` with a recorded document version. The judge prompts forbid such claims too.

## Your background: knowledge map

`profile.json` carries the knowledge map from `profile/01-researcher-background.md`. A topic counts as mentioned when all of its words, or the words of a configured synonym, appear in the title or abstract.

| Level | Paper role | Effect |
|---|---|---|
| `expert` | foundational, prerequisite, survey | substantive/deep → **overview** ("likely review material") |
| `new` | foundational, prerequisite, survey (P1/P2 only) | overview → **substantive** ("worth a proper read") |
| `new` | method, recent method | note: read its prerequisites first |
| `working`, `familiar` | any | no change |

Only **depth** (and therefore the planning-time estimate) changes, never the score or the order. Each adjustment appears as a "Your background" line. The usefulness judge is told not to lower a paper's rating because you may already know it. With no profile, or in topic-only mode, the knowledge map isn't applied.

## Feedback in ranking

- `irrelevant` / `not_useful` force priority `skip`, and the paper becomes a negative seed. These labels are profile-scoped.
- `relevant`, `deep_study` and `implemented` make the paper a positive seed.
- Read-state labels (`already_known`, `read_overview`, `understood`, `deep_study`, `implemented`) are global, because they describe you rather than the project. They exclude the paper from discovery unless you pass `--include-read`, and from `--unread` folder rankings.
- `read_later` doesn't exclude anything. It adds the paper to the reading-plan pool.
- Feedback is append-only. The latest applicable event is used, and the history is shown.

## Profile mismatch and thin folders

For an ad-hoc topic, paperscout measures the share of topic terms that appear in the active profile. Below `min_topic_overlap` (0.25), it ranks by topic only and says so. Passing `--profile` explicitly forces profile use, with a low-overlap warning.

A folder with only a name (no description or inclusion criteria) is ranked against its name **plus** the profile's keywords and research question, with mode `folder name + profile`. A name like "Baselines" says too little to judge relevance on its own.

## Changing the configuration

Copy `src/paperscout/ranking/score-v3.toml`, change the weights or thresholds, **bump `version`**, and load it with `load_ranking_config(path=…)`. Stored runs record which version produced them. To change what the judges look for, edit `.claude/agents/*-judge.md` and bump its `prompt_version`; stored ratings keep the version that produced them. Don't claim a configuration is better until it's been evaluated (see `evaluation.md`).
