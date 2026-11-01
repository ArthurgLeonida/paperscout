---
name: paper-feedback
description: Record feedback on a paper (relevant, irrelevant, already_known, read_later, read_overview, understood, deep_study, implemented, not_useful) with an optional reason. It is stored append-only and used to filter and rank future results. (/feedback is a built-in Claude Code command, hence this name.)
argument-hint: <paper> <label> ["optional reason"]
allowed-tools: Bash(uv run -q paperscout *)
---

# /paper-feedback: record feedback (prompt version: feedback-v1)

Raw arguments: $ARGUMENTS

1. Run, passing the raw arguments **unchanged**, single-quoted (escape `'` as `'\''`):
   ```
   uv run -q paperscout slash paper-feedback --args '<raw arguments>'
   ```
   The first token is the paper (an ID or URL; a title must be in double quotes), the second is the label, and any remaining text is the reason.
2. Confirm what was recorded: the paper title, the label, and the reason. If earlier feedback exists, say that it is kept as history (append-only), not overwritten.
3. On `Usage error`, an unknown label, or an ambiguous paper, show the message and the valid labels or paper options, and ask the user. Never pick a label on the user's behalf.
