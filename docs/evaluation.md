# Evaluating the ranking

**No claim is made that paperscout ranks better than its baselines.** That has to be measured on your own labels.

## Build a small human-labeled set

1. Run a few real queries that represent your goals:
   `uv run -q paperscout recent text-guided 3D Gaussian scene editing --limit 20`
   (Each report shows a run ID such as `run_20261101T120000_ab12cd`.)
2. Export a labeling template. Items are shuffled so the system order doesn't anchor you:
   `uv run -q paperscout eval template --run <RUN_ID> --out eval/labels-editing.json`
3. Open the JSON and set each `label`: **2** = must read for this goal, **1** = useful, **0** = not relevant. Use `null` to skip. Put your name in `labeled_by`. Judge against your research goal, not citation counts.
4. Aim for 3–5 queries × 20 papers. Label without looking at the system's ranking.

`eval/` is the intended place for these files. Labels describe your private research interests, so they aren't committed by default; see `eval/README.md`.

## Compare against baselines

```
uv run -q paperscout eval run --labels eval/labels-editing.json --k 5
```

This compares, on the same labeled papers:

| Ordering | Meaning |
|---|---|
| heuristic (paperscout) | the stored run order (the 1–100 score, with judge ratings if the run was judged) |
| search order | Semantic Scholar relevance-search rank |
| recency only | newest first |
| BM25 lexical | plain non-LLM relevance (no profile, seeds, or recency) |

Reported metrics: **precision@k** (share of top-k labeled ≥ 1), **nDCG@k** (graded), and **near-duplicate pairs@k** (redundancy).

## What else to assess (manual rubric)

| Aspect | How |
|---|---|
| Relevance in top results | precision@k / nDCG@k above |
| Explanation grounding | For 10 random recommendations, check that every "why" claim is supported by the shown metadata or abstract. Count unsupported claims. |
| Coverage | Did the list include at least one prerequisite or foundational paper and one survey when they exist? Did it miss papers you know are essential? |
| Redundancy | near-duplicate pairs@k; also check versions of the same paper (arXiv vs. venue) |
| Cost and latency | `api_calls` and `cache_hits` in the report stats; wall-clock time of the command; Claude Code usage for the interpretation step |

Small single-labeler samples are noisy, so treat differences as hypotheses. Change one weight at a time, bump the config version, and re-evaluate on the same labels.
