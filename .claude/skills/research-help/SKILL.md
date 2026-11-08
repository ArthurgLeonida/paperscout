---
name: research-help
description: Show paperscout's research commands (/setup, /recent, /discover, /trends, /rank, /download, /folders, /classify, /add, /assess, /reading-plan, /paper-feedback, /note), their options, defaults, and integration limitations.
argument-hint: '[command]'
allowed-tools: Bash(uv run -q paperscout *)
---

# /research-help

Raw arguments: $ARGUMENTS

Execution rules: run commands with the **Bash** tool, not PowerShell, because Windows PowerShell 5.1 splits quoted arguments. Run only the commands this skill specifies. If the output is unexpected, run `uv run -q paperscout doctor` once and report what it shows. Don't explore further.

Run, passing the raw arguments unchanged and single-quoted. Use empty quotes when there are no arguments:
```
uv run -q paperscout slash research-help --args '<raw arguments>'
```
Show the output as it is, without inventing commands or options. Then add these limitations in two or three lines:
- Semantic Scholar website folders and Research Feeds have **no official API**. paperscout uses local research folders, file imports (BibTeX/RIS/ID lists exported from the website or Zotero), and topic- and seed-based discovery through the public API. That discovery is an alternative, not your website feed.
- `/plan` and `/feedback` are built-in Claude Code commands, so the reading plan is `/reading-plan` and feedback is `/paper-feedback`.
- Recommendations are personalised by the private background that `/setup` builds in `profile/`. Without it, results are ranked by topic only.
- Without a Semantic Scholar API key, paperscout searches arXiv. That needs no key and covers arXiv preprints only, with no citations or recommendations. `uv run -q paperscout doctor` shows the active provider.
- `/trends` counts signals in a sample of papers and has a subagent interpret them; it reports state-of-the-art claims as claims and never says which method is best.
- `/download` saves arXiv PDFs for personal use only: arXiv's terms don't allow redistributing them, and papers not on arXiv are listed with links instead.
- Read-only Zotero integration is the next planned feature; full-text assessment comes later (`uv run -q paperscout capabilities`).
