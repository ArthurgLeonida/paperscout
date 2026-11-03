# Ranking and evidence standards

The goal is to answer *which papers should I read for this goal, why, in what order, and at what depth?* It doesn't claim to find objectively "best" papers.

## What determines the order

| Component | Affects the stored order? | Role |
|---|---|---|
| Deterministic Python heuristic (`heuristic-v2`) | **Yes, it is the only thing that does** | Computes dimensions, priority, and the ordering score |
| Claude's interpretation in a slash command | No | Explains results from the evidence. Any disagreement goes in a separately labelled "Claude's suggested adjustments (interpretation, not stored)" section |
| Stored Claude assessments (`paperscout assessment record`) | No | Validated and kept append-only with model and prompt version; shown next to the paper ("reference only, not used for ordering") |

They aren't combined. Output must not be described as "AI-ranked". Whether stored Claude judgments should ever influence ordering is an open design question to settle with evaluation data (see `evaluation.md`).

## Separate dimensions (each reported on its own)

| Dimension | How it is computed (heuristic-v2) | Unknown when |
|---|---|---|
| Topic relevance | Share of distinct topic terms found in title and abstract (title and bigram phrases weighted); TF-IDF similarity for long criteria texts | no topic or criteria |
| Project usefulness | Profile keyword and criteria hits + TF-IDF similarity to research question, project description and desired outcomes | no profile, or topic differs materially from the active profile |
| Seed affinity | Text similarity to positive seeds (profile seeds, folder seeds, papers you marked relevant); recommendation provenance; flagged if closer to a negative seed | no seeds |
| Recency | Exponential decay of publication-date age; the half-life depends on the profile's recency preference | no date (year-only dates are approximated and flagged) |
| Reading role | Metadata rules: reference-list provenance → prerequisite; `Review` type or survey title → survey; benchmark/dataset title; ≥ 3 years old and widely cited → foundational; ≤ 18 months → recent method | insufficient metadata |
| Evidence level | `metadata` (title only), `tldr` (machine-generated), `abstract`; `full_text` reserved for Phase 3 | — |
| Confidence | Low for metadata/TLDR evidence; never "high" from lexical heuristics alone | — |
| Reading effort | Range per depth (overview 20–40 min, substantive 1.5–2.5 h, deep 4–7 h; surveys 40–75 min). **Planning heuristics, not predictions.** | — |

The **ordering score** combines the known dimensions with configurable weights (`src/paperscout/ranking/heuristic-v2.toml`). Unknown dimensions are **dropped and the weights renormalised** rather than counted as zero, and the uncertainty is listed. The score is shown only with `--verbose`, labelled *"heuristic for ordering only, not a probability or quality score"*. Users see bands (high, medium, low, unknown) and priorities (P1, P2, P3, skip).

## When the source has no citation data (arXiv)

arXiv provides no citation counts, so a paper can't be labelled *foundational* from citations. It gets a time-based role (recent method or method) and the uncertainty *"Citation count unknown (the source provides none), so foundational status was not inferred."* Shared prerequisites (reference lists) and seed recommendations are also unavailable, so `/discover` balances only surveys. Unknown stays unknown: a missing citation count is never treated as zero.

## Deliberate non-signals

- **Citation counts never raise priority.** They're used only to label a paper's role as foundational. Recent papers with few citations get an explicit note that this wasn't penalised.
- Popularity, venue prestige and author fame aren't used.
- The heuristic doesn't judge scientific quality.

## Abstract-level limits

From metadata and abstracts, paperscout and the skills make no judgments about experimental fairness, correctness of derivations, reproducibility, code availability (unless independently verified), or state-of-the-art superiority. `ClaudeAssessment` enforces this when Claude's assessments are stored: those fields must be `unknown` unless the evidence level is `full_text` with a recorded document version.

## Your background: knowledge map (heuristic-v2)

`profile.json` carries the knowledge map from `profile/01-researcher-background.md`. A topic counts as mentioned when all of its words, or the words of a configured synonym, appear in the title or abstract.

| Level | Paper role | Effect |
|---|---|---|
| `expert` | foundational, prerequisite, survey | substantive/deep → **overview** ("likely review material") |
| `new` | foundational, prerequisite, survey (P1/P2 only) | overview → **substantive** ("worth a proper read") |
| `new` | method, recent method | note: read its prerequisites first |
| `working`, `familiar` | any | no change |

Only **depth** (and therefore the planning-time estimate) changes. Priority, the ordering score and the order are identical to v1. Each adjustment appears as a "Your background" line. With no profile, or when a topic differs from the profile (topic-only mode), the knowledge map isn't applied.

## Feedback in ranking

- `irrelevant` / `not_useful` force priority `skip`, and the paper becomes a negative seed for recommendations. These labels are profile-scoped.
- `relevant`, `deep_study` and `implemented` make the paper a positive seed.
- Read-state labels (`already_known`, `read_overview`, `understood`, `deep_study`, `implemented`) are global, because they describe you rather than the project. They exclude the paper from discovery unless you pass `--include-read`, and from `--unread` folder rankings.
- `read_later` doesn't exclude anything. It adds the paper to the reading-plan pool.
- Feedback is append-only. The latest applicable event is used, and the history is shown.

## Profile mismatch

For an ad-hoc topic, paperscout measures the share of topic terms that appear in the active profile. Below `min_topic_overlap` (0.25), it ranks by topic only and says so. Passing `--profile` explicitly forces profile use, with a low-overlap warning.

## Changing the configuration

Copy `heuristic-v2.toml`, change the weights or thresholds, **bump `version`**, and load it with `load_ranking_config(path=…)`. Stored runs record which version produced them. Don't claim a configuration is better until it's been evaluated (see `evaluation.md`).
