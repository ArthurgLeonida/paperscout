# Authors

`/authors` ranks the recent papers of researchers you name, such as a lab or the faculty of a department, against your research profile. It uses the same scoring, judge subagents, report layout, `/download` and `/reading-plan` as `/discover`.

```
/authors Seungryong Kim --affiliation KAIST
/authors Seungryong Kim; Jane Doe @ MIT --days 365 --topic "3D scene editing"
/authors                       # the followed_authors saved in your profile
```

## Why OpenAlex

arXiv has no author or affiliation records, only name strings, and Semantic Scholar's affiliations are sparse. [OpenAlex](https://openalex.org) (keyless, CC0) has institutions and authorships with affiliations, so `/authors` uses it whichever paper source is active.

## How a researcher is matched

| You give | paperscout does |
|---|---|
| A name and an affiliation (`--affiliation KAIST`, or `Name @ KAIST`) | Resolves the affiliation to an OpenAlex institution (acronyms work), searches works whose author list has the name and where some author is at that institution, and keeps a paper only when the named author is listed **with** that institution, or with no institution at all (common on preprints) |
| A name only | Uses the OpenAlex author record with the most works for exactly that name, and reports how many other records share it |
| An OpenAlex author ID (`A5085363061`) | Uses that record |

Matching on each paper's author list matters because OpenAlex often splits one person into several author records (a main one plus small fragments that hold the newest papers). Matching on a single author record would miss those papers.

Name spellings are compared loosely: "Ye, Jong-Chul", "Jong Chul Ye" and "Jongchul Ye" match, and so do family-name-first forms. Initials alone ("J. C. Ye") don't.

**Limits:**
- A namesake at the same institution would also match.
- OpenAlex affiliations can be wrong or missing.
- OpenAlex knows universities, not their schools or labs, so name the people. "KAIST" works as an affiliation; "Kim Jaechul Graduate School of AI" doesn't. The `/authors` skill can read the school's official faculty page and let you choose names; it never adds names from memory.

The report's *Researchers* table shows, for each name, how it was matched, the institution, how many papers fell in the window, and what was left out.

## Window, ranking and requests

- **Window:** the last 730 days by default (`--days`, `--since`, `--until`), by OpenAlex's publication date (usually the arXiv v1 date for preprints).
- **Duplicates:** a preprint and its published version are merged, keeping the higher citation count.
- **Ranking:** papers are ranked against your profile (saved searches, keywords, research question), or against `--topic`. The shortlist is 20 papers by default (`--limit`, up to 50), and the judges rate it like any other list.
- **Requests:** one per affiliation, plus one to three per researcher (pages of 200 works). The per-command OpenAlex budget is sized to that, up to 120.
- **Limit per run:** at most 30 researchers.
- **What's sent:** researcher names and the affiliation go to OpenAlex. Keyless requests share a small free daily allowance per network, and nothing else from your background is sent.

## Following researchers

Add `followed_authors` to `profile/profile.json` (each entry "First Last @ Affiliation" or an OpenAlex ID), then run `uv run -q paperscout profile sync`. `/authors` with no names then ranks their latest papers. `/setup` can fill this in, and the `/authors` skill offers to save a list you've just used, writing only after you confirm.
