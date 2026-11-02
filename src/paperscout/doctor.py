"""Setup diagnostics. Never prints secrets and never creates the database."""

from __future__ import annotations

import platform
import sqlite3
from dataclasses import replace
from typing import Any

from paperscout import __version__
from paperscout.config import S2_KEY_FORM, Settings, env_candidates
from paperscout.s2.client import S2Error, SemanticScholarClient

LIVE_PROBE_ID = "ARXIV:2308.04079"  # a stable, well-known record used only as an access probe

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
            "SELECT p.name FROM settings s JOIN profiles p ON p.id = s.value WHERE s.key = 'active_profile_id'"
        ).fetchone()
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    finally:
        conn.close()
    return {"path": str(path), "exists": True, "schema_version": version, "counts": counts,
            "active_profile": row[0] if row else None}


def live_probe(settings: Settings, transport=None) -> dict[str, Any]:
    probe_settings = replace(settings, s2_max_retries=0, s2_max_requests=1)
    client = SemanticScholarClient(probe_settings, cache=None, transport=transport)
    result: dict[str, Any] = {"request": f"GET /graph/v1/paper/{LIVE_PROBE_ID}?fields=…", "retries": 0}
    try:
        paper = client.get_paper(LIVE_PROBE_ID)
        result.update(ok=True, title=paper.title)
    except S2Error as exc:
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
    loaded = s["env_files_loaded"]
    lines.append(f"- .env files loaded: {', '.join(loaded) if loaded else 'none'} "
                 f"(checked: {', '.join(report['env_files_checked'])})")
    if key["configured"]:
        lines.append(f"- S2_API_KEY: configured ({key['source']}); value not shown")
    else:
        lines.append(f"- S2_API_KEY: not configured. Requests use the shared unauthenticated pool. "
                     f"Request a key at {key['how_to_get']} and put it in .env (see README).")
    lines.append(f"- Request policy: ≥{s['s2_min_interval']}s between requests, {s['s2_max_retries']} retries, "
                 f"at most {s['s2_max_requests']} HTTP attempts per command")
    probe = report.get("live_probe")
    if probe:
        status = "OK" if probe["ok"] else "FAILED"
        lines.append(f"- Live probe ({probe['request']}, no retries): {status}")
        if probe.get("response"):
            r = probe["response"]
            lines.append(f"  - HTTP {r.get('status')}; error type: {r.get('error_type') or 'none'}; "
                         f"Retry-After: {r.get('retry_after') or 'absent'}; request id: {r.get('request_id') or 'n/a'}")
        if not probe["ok"]:
            lines.append(f"  - {probe['error']}")
    return "\n".join(lines)
