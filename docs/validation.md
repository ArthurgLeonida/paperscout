# Validation status

Last updated **2026-11-02**. "Live" means against the real Semantic Scholar API. "Slash" means invoked through a real Claude Code slash command (headless `claude -p` CLI). Nothing in this table was inferred from documentation alone.

| Capability | Implemented | Mocked tests | Live API | Real slash command |
|---|---|---|---|---|
| Paper lookup (ID/URL → record) | yes | yes | **pass**: an arXiv URL resolved to the full 3DGS record (DOI, venue, date, abstract). Later lookups got 429 | — |
| Topic search `/paper/search` | yes | yes | **not verified**: 429 on every attempt | `/recent`: throttled, reported honestly, no invented results |
| Title match, batch/TLDR enrichment | yes | yes | not verified | — |
| Seed recommendations | yes | yes | not verified | — |
| Reference lists (prerequisite detection) | yes | yes | not verified | — |
| Deduplication across sources | yes | yes | not verified on real data | — |
| Save to local folders, rank folder | yes | yes | — (local) | `/rank "3D Scene Editing" --unread` with an empty isolated folder: **pass** |
| Feedback, reading plan, assessment save/reopen | yes | yes (including reopen after restart) | not verified with real papers | — |
| Unsupported website folders/feeds | yes | yes | — | `/rank --all --source s2-feed`: **pass** (exit 3, honest alternative) |
| Rate-limit handling | yes | yes | **pass**: bounded attempts, sanitized diagnostics, clean stop | **pass** (via `/recent`) |
| `.env` loading and key detection | yes | yes (BOM, quotes, comments, precedence, project-root fallback) | key absent: detected; authenticated path not verified | — |
| `doctor` (never prints the key, never creates the DB) | yes | yes | — | — |
| Windows PowerShell CLI | — | — | setup, restart/reopen, unknown folder, invalid IDs, unknown option, unsupported source, empty plan: **pass** | — |
| Skill registration | — | — | — | all 11 listed by Claude Code 2.1.314 |
| VS Code extension `/` menu | — | — | — | **not verified** (see below) |
| Ranking quality vs. baselines | harness only | metric code only | **not evaluated**: needs your labels | — |
| Fresh clone (committable files only), Windows | — | 140 tests pass on Python 3.11.9 and 3.13.7; lint passes | — | — |
| Package build (wheel/sdist) | — | wheel contains the SQL migration, ranking config and LICENSE; sdist contains no data or secrets | — | — |
| CI on Linux (GitHub Actions) | workflow added | — | **not verified** until the first run on GitHub | — |

## Throttling diagnostics (sanitized)

- Response: `HTTP 429`, `x-amzn-ErrorType: TooManyRequestsException` (AWS API Gateway), body `"Too Many Requests. Please wait and try again or apply for a key for higher rate limits."`.
- No `Retry-After` header and no rate-limit headers.
- The same response came for plain one-off curl requests with a different User-Agent, on both `/paper/search` and `/paper/{id}`. A well-formed request isn't the issue (a malformed one returns 400).
- This machine sent about 40 unauthenticated requests over about 80 minutes. That's far below the documented shared limit, but the response doesn't say whether throttling is global or per IP. Early retry bursts (up to 8 attempts in two minutes) may have contributed slightly. The defaults are now 2 retries without a key and a hard per-command budget.
- 2026-11-02: one more single-request probe (`doctor --live`) also got 429. Throttling had persisted across both days.
- One way to tell whether the throttle is per IP: run `uv run -q paperscout doctor --live` once from a different network, such as a phone hotspot. A 200 there would point to per-IP throttling on the usual network.

## When your API key arrives (runbook)

All live runs use isolated databases. Your real library isn't touched unless you say so.

1. `uv run -q paperscout doctor`: should show `S2_API_KEY: configured (.env: …)`.
2. `uv run -q paperscout doctor --live`: one request; expect HTTP 200.
3. `uv run -q python scripts/live_smoke.py --budget 15`: the nine end-to-end steps against `data/smoke`.
4. With `PAPERSCOUT_HOME` pointing to `data/smoke`, run through real slash commands:
   - `/discover text-guided 3D Gaussian scene editing`
   - `/recent text-guided 3D Gaussian scene editing --days 90 --limit 10`
5. Produce the real shortlist for "text-guided 3D Gaussian scene editing, emphasizing preservation of unedited regions and multi-view consistency". Keep foundations and recent work separate, and mark Claude's role labels ("baseline", "peripheral") as interpretation.
6. Label it: `uv run -q paperscout eval template --run <run-id> --out eval/labels-editing.json`, fill in 0/1/2, then `uv run -q paperscout eval run --labels eval/labels-editing.json`.

## Bugs found and fixed during validation

- Windows PowerShell 5.1 split `slash … --args '"quoted" …'` into several arguments, and the rest was silently dropped. It's now rejected with guidance; piping through stdin works.
- Windows PowerShell pipes prepend a UTF-8 BOM. The tokenizer now strips it.
- `add` with an invalid or ambiguous paper exited with code 0. It now exits with 1.
- An unsupported `rank --source` created the default database before refusing. External-capability checks now run before the store is opened.
- With no profile or topic, priority showed `P3`. It now shows `unknown`.
- In Claude Code, the model sometimes chose the PowerShell tool or explored beyond the skill. The skills now require the Bash tool and limit exploration.

## Manual check for the VS Code extension

1. Open the repository folder as the VS Code workspace and start a new Claude Code chat.
2. Type `/` and check that `recent`, `discover`, `rank`, `folders`, `classify`, `add`, `assess`, `reading-plan`, `paper-feedback`, `note` and `research-help` are listed.
3. Run `/research-help rank`. Expected: a Bash call to `uv run -q paperscout slash research-help --args 'rank'` and the option list for `/rank`.
4. Run `/rank --all --source s2-feed`. Expected: "Not supported", with the alternative workflow, and nothing changed.
5. If step 2 fails but the CLI works, report the extension version. Typing the command name in full may still work.
