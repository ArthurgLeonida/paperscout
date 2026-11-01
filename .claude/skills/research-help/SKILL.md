---
name: research-help
description: Show paperscout's research commands (/recent, /discover, /rank, /folders, /classify, /add, /assess, /reading-plan, /paper-feedback, /note), their options, defaults, and integration limitations.
argument-hint: '[command]'
allowed-tools: Bash(uv run -q paperscout *)
---

# /research-help

Raw arguments: $ARGUMENTS

Run, passing the raw arguments unchanged and single-quoted. Use empty quotes when there are no arguments:
```
uv run -q paperscout slash research-help --args '<raw arguments>'
```
Show the output as it is, without inventing commands or options. Then add these limitations in two or three lines:
- Semantic Scholar website folders and Research Feeds have **no official API**. paperscout uses local research folders, file imports (BibTeX/RIS/ID lists exported from the website or Zotero), and topic- and seed-based discovery through the public API. That discovery is an alternative, not your website feed.
- `/plan` and `/feedback` are built-in Claude Code commands, so the reading plan is `/reading-plan` and feedback is `/paper-feedback`.
- Zotero sync, the MCP server, and full-text assessment are planned for later phases (`uv run -q paperscout capabilities`).
