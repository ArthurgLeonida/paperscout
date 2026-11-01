---
name: note
description: Generate a short reading-note scaffold for a paper (first, second, or deep pass) with prompts for purpose, problem, contribution, method, evidence, limitations, project connection, questions, and next action. Template only; it does not replace the user's own reading.
argument-hint: <paper> [--pass first|second|deep] [--save]
allowed-tools: Bash(uv run -q paperscout *)
---

# /note: reading-note scaffold (prompt version: note-v1)

Raw arguments: $ARGUMENTS

1. Run, passing the raw arguments **unchanged**, single-quoted (escape `'` as `'\''`):
   ```
   uv run -q paperscout slash note --args '<raw arguments>'
   ```
2. Show the scaffold as the CLI prints it. **Do not fill in the sections** with a summary of the paper. The scaffold is for the user's own notes. With `--save`, it is written only to the local notes folder; report the path.
3. Never write notes to Zotero or any other external application. That integration is not implemented, and any future external write will need explicit approval.
