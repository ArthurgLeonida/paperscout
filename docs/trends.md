# Trends

`/trends` answers "where is this field heading?" with counts, and then has a subagent interpret them. It never says which method is best or "the new SOTA": from abstracts, paperscout can count what papers use, name and claim, not compare results across benchmarks.

## What is counted

`paperscout trends [topic] --months N` (default 12) splits the window into calendar quarters (the current one is partial and marked `*`). For each search and each quarter it takes up to 50 of the most relevant papers (one topic) or 15–40 per search (saved searches), from arXiv or Semantic Scholar. Then it counts, in Python:

| Signal | How | Reads as |
|---|---|---|
| Rising, emerging and fading terms | 1–3-word terms from titles and abstracts (stopwords, generic academic words, links and method names removed; terms never cross punctuation). Each term's share of papers in the earlier half of the window vs the recent half. A term isn't counted in papers found by a search that contains it, so search words can't trend by construction. Overlapping terms with nearly the same papers are merged, keeping the clearest. | What papers increasingly (or decreasingly) talk about |
| Methods other papers name | Tokens shaped like names (`NeRF`, `InstructPix2Pix`, `3DGS`, `U-Net`, `CLIP`), counted per paper, not counting a paper whose title has the name (it introduces it) | Adoption, and what papers compare against |
| State-of-the-art claims | Abstract sentences with "state of the art" or "SOTA" and a claim verb (achieve, outperform, surpass, …), and the names in those sentences | What authors **claim**, never verified |
| Citation velocity | Citations per year for papers at least six months old (OpenAlex on arXiv; Semantic Scholar otherwise) | What is being picked up |
| Cited by recent papers | Semantic Scholar only: papers cited by at least two of the most relevant recent papers | Shared foundations of recent work |
| Search volume | Matching papers per search per quarter, next to all papers in the profile's arXiv categories | Whether the topic grows faster than arXiv itself |

## Support labels

Each term change gets a two-proportion z-test (earlier vs recent share). Hundreds of terms are tested at once, so:

- **strong**: the change passes a Benjamini–Hochberg false-discovery check at 10% across every term that could have reached p < 0.05 (rare terms that can't reach it, however their mentions split, aren't counted as tests; Tarone's refinement for count data);
- **suggestive**: p < 0.05 for that term on its own. It may be chance.

Changes weaker than p < 0.05 aren't shown. With a few hundred abstracts, single terms are rarely strong; that is the honest result, and the analyst is told not to build a direction on one suggestive term.

## The trend analyst

`trends packet --run RUN` writes the signals, up to 80 example papers (untrusted text) and the paths of the private background files. The `trend-analyst` subagent ([.claude/agents/trend-analyst.md](../.claude/agents/trend-analyst.md)) returns 2–6 directions as JSON. `trends record` stores them only if every direction cites signal IDs and paper IDs from that run, `high` confidence rests on at least two signals, and any state-of-the-art wording is attributed as a claim ("… claim state-of-the-art results"). Otherwise nothing is stored and the message says why. The directions are stored as a new `trends` run that points to the signals run.

## Requests and limits

One request per search per quarter, plus one per quarter for the category totals, at arXiv's one request every three seconds: five saved searches over 12 months send 30 requests, about one and a half to four minutes depending on arXiv. A trends run raises the per-command request budget to exactly what it needs, up to 80. A page that arXiv returns short (fewer entries than it reports matching) is fetched once more without the cache. A search that still misses a quarter is left out of the counts, with a notice, so the two halves stay comparable. Trend samples are never counted as "seen before" in `/recent` or `/discover`.

Limits: the sample is relevance-ranked and follows the searches, so it describes them, not the whole field; small counts are noisy; claims use different benchmarks and setups; arXiv volume also grows overall.
