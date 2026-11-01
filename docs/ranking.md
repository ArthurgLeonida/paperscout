# Ranking and evidence standards

The goal is to answer *which papers should I read for this goal, why, in what order, and at what depth?* It doesn't claim to find objectively "best" papers.

## Separate dimensions (each reported on its own)

| Dimension | How it is computed (heuristic-v1) | Unknown when |
|---|---|---|
| Topic relevance | Share of distinct topic terms found in title and abstract (title and bigram phrases weighted); TF-IDF similarity for long criteria texts | no topic or criteria |
| Project usefulness | Profile keyword and criteria hits + TF-IDF similarity to research question, project description and desired outcomes | no profile, or topic differs materially from the active profile |
| Seed affinity | Text similarity to positive seeds (profile seeds, folder seeds, papers you marked relevant); recommendation provenance; flagged if closer to a negative seed | no seeds |
| Recency | Exponential decay of publication-date age; the half-life depends on the profile's recency preference | no date (year-only dates are approximated and flagged) |
| Reading role | Metadata rules: reference-list provenance → prerequisite; `Review` type or survey title → survey; benchmark/dataset title; ≥ 3 years old and widely cited → foundational; ≤ 18 months → recent method | insufficient metadata |
| Evidence level | `metadata` (title only), `tldr` (machine-generated), `abstract`; `full_text` reserved for Phase 3 | — |
| Confidence | Low for metadata/TLDR evidence; never "high" from lexical heuristics alone | — |
| Reading effort | Range per depth (overview 20–40 min, substantive 1.5–2.5 h, deep 4–7 h; surveys 40–75 min). **Planning heuristics, not predictions.** | — |

The **ordering score** combines the known dimensions with configurable weights (`src/paperscout/ranking/heuristic-v1.toml`). Unknown dimensions are **dropped and the weights renormalised** rather than counted as zero, and the uncertainty is listed. The score is shown only with `--verbose`, labelled *"heuristic for ordering only, not a probability or quality score"*. Users see bands (high, medium, low, unknown) and priorities (P1, P2, P3, skip).

## Deliberate non-signals

- **Citation counts never raise priority.** They're used only to label a paper's role as foundational. Recent papers with few citations get an explicit note that this wasn't penalised.
- Popularity, venue prestige and author fame aren't used.
- The heuristic doesn't judge scientific quality.

## Abstract-level limits

From metadata and abstracts, paperscout and the skills make no judgments about experimental fairness, correctness of derivations, reproducibility, code availability (unless independently verified), or state-of-the-art superiority. `ClaudeAssessment` enforces this when Claude's assessments are stored: those fields must be `unknown` unless the evidence level is `full_text` with a recorded document version.

## Feedback in ranking

- `irrelevant` / `not_useful` force priority `skip`, and the paper becomes a negative seed for recommendations. These labels are profile-scoped.
- `relevant`, `deep_study` and `implemented` make the paper a positive seed.
- Read-state labels (`already_known`, `read_overview`, `understood`, `deep_study`, `implemented`) are global, because they describe you rather than the project. They exclude the paper from discovery unless you pass `--include-read`, and from `--unread` folder rankings.
- `read_later` doesn't exclude anything. It adds the paper to the reading-plan pool.
- Feedback is append-only. The latest applicable event is used, and the history is shown.

## Profile mismatch

For an ad-hoc topic, paperscout measures the share of topic terms that appear in the active profile. Below `min_topic_overlap` (0.25), it ranks by topic only and says so. Passing `--profile` explicitly forces profile use, with a low-overlap warning.

## Changing the configuration

Copy `heuristic-v1.toml`, change the weights or thresholds, **bump `version`**, and load it with `load_ranking_config(path=…)`. Stored runs record which version produced them. Don't claim a configuration is better until it's been evaluated (see `evaluation.md`).
