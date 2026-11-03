"""Setup diagnostics. Never prints secrets and never creates the database."""

from __future__ import annotations

import platform
import sqlite3
from dataclasses import replace
from typing import Any

from paperscout import __version__
from paperscout.background import inspect_background
from paperscout.config import S2_KEY_FORM, Settings, env_candidates
from paperscout.arxiv.client import ArxivClient
from paperscout.http import ApiError
from paperscout.s2.client import SemanticScholarClient

LIVE_PROBE_ID = "2308.04079"  # a stable, well-known arXiv paper used only as an access probe

TABLES = {
    "profiles": "profiles",
    "folders": "folders",
    "papers": "papers",
    "folder memberships": "folder_papers",
    "feedback events": "feedback",
    "runs": "runs",
    "assessments": "assessments",
    "audit entries": "audit_log",
}


def inspect_database(settings: Settings) -> dict[str, Any]:
    path = settings.db_path
    if not path.is_file():
        return {"path": str(path), "exists": False}
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        counts = {label: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for label, table in TABLES.items()}
        row = conn.execute(
            "SELECT p.id, p.name FROM settings s JOIN profiles p ON p.id = s.value WHERE s.key = 'active_profile_id'"
        ).fetchone()
        synced = None
        if row:
            hit = conn.execute("SELECT value FROM settings WHERE key = ?", (f"profile_sync:{row[0]}",)).fetchone()
            synced = hit[0] if hit else None
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    finally:
        conn.close()
    return {"path": str(path), "exists": True, "schema_version": version, "counts": counts,
            "active_profile": row[1] if row else None, "active_profile_sync_hash": synced}


def live_probe(settings: Settings, transport=None) -> dict[str, Any]:
    """Exactly one request, no retries, to the active provider."""
    if settings.effective_provider == "arxiv":
        probe_settings = replace(settings, arxiv_max_retries=0, arxiv_max_requests=1)
        client = ArxivClient(probe_settings, cache=None, transport=transport)
        request = f"GET {settings.arxiv_base}?id_list={LIVE_PROBE_ID}"
    else:
        probe_settings = replace(settings, s2_max_retries=0, s2_max_requests=1)
        client = SemanticScholarClient(probe_settings, cache=None, transport=transport)
        request = f"GET /graph/v1/paper/ARXIV:{LIVE_PROBE_ID}?fields=…"
    result: dict[str, Any] = {"request": request, "retries": 0, "provider": client.policy.provider}
    try:
        if isinstance(client, ArxivClient):
            paper = client.get_papers([LIVE_PROBE_ID])[0]
            if paper is None:
                raise ApiError("arXiv returned no entry for the probe paper")
        else:
            paper = client.get_paper(f"ARXIV:{LIVE_PROBE_ID}")
        result.update(ok=True, title=paper.title)
    except ApiError as exc:
        result.update(ok=False, error=str(exc).split(" Request a key")[0])
    finally:
        client.close()
    if client.diagnostics:
        result["response"] = client.diagnostics[-1]
    return result


def run_doctor(settings: Settings, live: bool = False, transport=None) -> dict[str, Any]:
    from pathlib import Path

    report: dict[str, Any] = {
        "paperscout": __version__,
        "python": platform.python_version(),
        "settings": settings.redacted(),
        "env_files_checked": [str(p) for p in env_candidates(Path.cwd())],
        "database": inspect_database(settings),
        "background": inspect_background(),
        "api_key": {
            "configured": bool(settings.s2_api_key),
            "source": settings.key_source,
            "how_to_get": None if settings.s2_api_key else S2_KEY_FORM,
        },
    }
    if live:
        report["live_probe"] = live_probe(settings, transport)
    return report


def render(report: dict[str, Any]) -> str:
    s, db, key = report["settings"], report["database"], report["api_key"]
    lines = [
        "# paperscout doctor",
        f"- paperscout {report['paperscout']} on Python {report['python']}",
        f"- Data home: {s['home']}",
    ]
    if db["exists"]:
        counts = ", ".join(f"{k} {v}" for k, v in db["counts"].items())
        lines.append(f"- Database: {db['path']} (schema v{db['schema_version']}): {counts}")
        lines.append(f"- Active profile: {db['active_profile'] or 'none'}")
    else:
        lines.append(f"- Database: not created yet ({db['path']}); doctor does not create it")
    lines += render_background(report["background"], db)
    loaded = s["env_files_loaded"]
    lines.append(f"- .env files loaded: {', '.join(loaded) if loaded else 'none'} "
                 f"(checked: {', '.join(report['env_files_checked'])})")
    if key["configured"]:
        lines.append(f"- S2_API_KEY: configured ({key['source']}); value not shown")
    else:
        if s["provider"].startswith("arxiv"):
            lines.append(f"- S2_API_KEY: not configured. Not needed for the arXiv provider; for Semantic Scholar's "
                         f"citations and recommendations, request a key at {key['how_to_get']} (see README).")
        else:
            lines.append(f"- S2_API_KEY: not configured. Requests use the shared unauthenticated pool. "
                         f"Request a key at {key['how_to_get']} and put it in .env (see README).")
    lines.append(f"- Paper provider: {s['provider']}")
    if s["openalex"] == "on":
        lines.append("- Citation counts: OpenAlex for papers without them (keyless; paper identifiers and titles "
                     "only); turn off with PAPERSCOUT_OPENALEX=off")
    else:
        lines.append("- Citation counts: OpenAlex is off (PAPERSCOUT_OPENALEX=off), so popularity is unknown on arXiv")
    lines.append(f"- Request policy: Semantic Scholar ≥{s['s2_min_interval']}s between requests, "
                 f"{s['s2_max_retries']} retries, at most {s['s2_max_requests']} attempts per command; "
                 f"arXiv ≥{s['arxiv_min_interval']}s between requests, at most {s['arxiv_max_requests']} per command")
    probe = report.get("live_probe")
    if probe:
        status = "OK" if probe["ok"] else "FAILED"
        lines.append(f"- Live probe to {probe['provider']} ({probe['request']}, no retries): {status}")
        if probe.get("response"):
            r = probe["response"]
            lines.append(f"  - HTTP {r.get('status')}; error type: {r.get('error_type') or 'none'}; "
                         f"Retry-After: {r.get('retry_after') or 'absent'}; request id: {r.get('request_id') or 'n/a'}")
        if not probe["ok"]:
            lines.append(f"  - {probe['error']}")
    return "\n".join(lines)


def render_background(bg: dict[str, Any], db: dict[str, Any]) -> list[str]:
    files = bg["files"]
    prose = {k: v for k, v in files.items() if k.endswith(".md")}
    if all(v == "missing" for v in files.values()):
        return [f"- Research background: not set up ({bg['profile_dir']} is empty). Run /setup in Claude Code."]
    lines = [f"- Research background ({bg['profile_dir']}): " + ", ".join(f"{k} {v}" for k, v in prose.items())]
    lines.append(f"  - CLAUDE.local.md: {bg['claude_local_md']}"
                 + ("" if bg["claude_local_md"] == "imports profile/" else " (Claude won't load your background; run /setup)"))
    if files["profile.json"] == "missing":
        lines.append("  - profile.json: missing (run `/setup --section sync`)")
    elif bg["profile_json_hash"] and bg["profile_json_hash"] == db.get("active_profile_sync_hash"):
        lines.append("  - profile.json: synced with the active profile")
    else:
        lines.append(f"  - profile.json: {files['profile.json']}; not synced with the active profile "
                     "(run `uv run -q paperscout profile sync`)")
    return lines
