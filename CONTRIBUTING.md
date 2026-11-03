# Contributing to paperscout

Thanks for your interest. Bug reports, documentation fixes and focused pull requests are all welcome.

## Development setup

```bash
git clone https://github.com/ArthurgLeonida/ai-paper-search.git
cd ai-paper-search
uv sync
uv run pytest                                   # mocked API responses; no Semantic Scholar key needed
uvx ruff check src tests scripts                # lint (configuration in pyproject.toml)
```

CI runs the same tests on Windows and Linux with Python 3.11 and 3.13.

## Project rules

These keep the tool trustworthy. Pull requests that break them will be asked to change.

1. **Never commit secrets or private data.** That means no API keys, `.env` files, databases, reports, notes or evaluation labels, and no research background: `profile/`, `CLAUDE.local.md`, or anything in `documents/` other than its README and folder skeleton. `.gitignore` covers these; check `git status` before committing.
2. **Official, documented interfaces only.** No undocumented Semantic Scholar endpoints, website scraping, browser automation, or password handling. A new capability goes into `src/paperscout/capabilities.py` and `docs/capability-matrix.md`, with its official source.
3. **One implementation of the logic.** Retrieval, ranking and storage live in the service layer (`service.py`, `planning.py`, `taxonomy.py`). The CLI parses arguments, and skills in `.claude/skills/` are thin workflow instructions that call the CLI. Don't duplicate logic in skills.
4. **External writes need preview and explicit confirmation**, plus an audit entry. Discovery never writes as a side effect.
5. **Honest output.** Unknown values stay unknown, never zero or false. Heuristic scores aren't probabilities. Abstract-only evidence must not be used to judge fairness, correctness, reproducibility, code availability or state-of-the-art claims. Don't describe the heuristic order as "AI-ranked".
6. **Ranking changes are versioned.** Copy `src/paperscout/ranking/heuristic-v2.toml`, bump `version`, and say how you evaluated the change (see `docs/evaluation.md`).
7. **Templates and code stay in sync.** `templates/profile/*` and `templates/CLAUDE.local.md` define what `/setup` writes. `tests/test_background.py` checks that `profile.json` still matches the model and that the headings `/setup` relies on still exist. Update both together.
8. **Tests use synthetic fixtures**, clearly labelled (titles start with `[SYNTHETIC]`), never real records presented as data. Mock HTTP with `httpx.MockTransport`; see `tests/conftest.py`.
9. **Live checks are optional and isolated.** `uv run -q python scripts/live_smoke.py` uses your own key, a separate database under `data/smoke`, and a small request budget. Never run live tests against someone else's key, and keep them out of CI.

## Pull requests

- Keep each pull request focused, and include tests for new behaviour or fixed bugs.
- Update the documentation when behaviour, commands or capabilities change.
- Describe what you verified (mocked tests, a live smoke test, manual Claude Code check) and what you didn't.
