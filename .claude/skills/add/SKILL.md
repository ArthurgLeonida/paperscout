---
name: add
description: Explicitly add a paper (DOI, arXiv, Semantic Scholar ID/URL, or title) to a named local paperscout research folder. Use only when the user explicitly asks to add or save a paper to a folder.
argument-hint: <paper> --folder "NAME" [--dry-run]
allowed-tools: Bash(uv run -q paperscout *)
---

# /add: add a paper to a local folder (prompt version: add-v1)

Raw arguments: $ARGUMENTS

1. Run, passing the raw arguments **unchanged**, single-quoted (escape `'` as `'\''`):
   ```
   uv run -q paperscout slash add --args '<raw arguments>'
   ```
2. Report exactly what the CLI says:
   - `Added: "<title>" → local paperscout folder "<name>" (<id>)`: confirm it with the resolved title, and add that this is a local folder, not the Semantic Scholar website.
   - `Already present (no change)`: say so.
   - `Not added: … ambiguous` with options: list them and ask which paper is meant. Then re-run with the chosen ID.
   - `Error: No local folder named …`: folders are never created implicitly. Show the existing folders and offer `/folders create "<name>"`. Create one only if the user confirms.
   - `Not supported:` (for `--to zotero` or `--to s2-library`): explain that the integration is unsupported or not yet implemented, and give the alternative the message suggests.
3. Never claim success unless the CLI printed `Added` or `Already present`. A `--dry-run` changes nothing; say so.
