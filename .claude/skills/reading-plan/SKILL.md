---
name: reading-plan
description: Build a realistic reading plan for a time budget from unread papers in local paperscout folders, read-later papers, or the latest discovery run. It covers overview vs. substantive vs. deep reading, prerequisites first, and time for notes. (/plan is a built-in Claude Code command, hence this name.)
argument-hint: '[--hours H | --week] [--folder NAME] [--profile NAME]'
allowed-tools: Bash(uv run -q paperscout *)
---

# /reading-plan: time-boxed reading plan (prompt version: plan-v1)

Raw arguments: $ARGUMENTS

Execution rules: run commands with the **Bash** tool, not PowerShell, because Windows PowerShell 5.1 splits quoted arguments. Run only the commands this skill specifies. If the output is unexpected, run `uv run -q paperscout doctor` once and report what it shows. Don't explore further.

1. Run, passing the raw arguments **unchanged**, single-quoted (escape `'` as `'\''`):
   ```
   uv run -q paperscout slash reading-plan --args '<raw arguments>'
   ```
   With no budget, the CLI uses `weekly_reading_hours` from the profile. If that is missing, it errors. Then ask the user for `--hours`.
2. Present the plan by session:
   - For each paper: depth (overview, substantive, or deep), time range, role, and why it is at that position. Prerequisites and foundational papers come first.
   - Keep the notes buffer and the open-questions buffer visible. Do not fill them with more reading.
   - Say clearly that **all durations are planning heuristics, not predictions**.
   - List papers deferred for lack of time.
3. Keep the plan's order; it is deterministic. If the abstracts show a dependency the heuristic missed, add a separate **"Claude's suggested adjustments (interpretation, not stored)"** note with the reason. Suggest `/note <paper> --pass first` for scaffolds and `/paper-feedback` afterwards. Do not record feedback yourself.
