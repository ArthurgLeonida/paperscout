"""SQLite persistence: schema migrations plus small repository methods.

All timestamps are stored as ISO-8601 UTC strings. Feedback, assessments,
metadata snapshots and the audit log are append-only: earlier records are
never rewritten.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

from paperscout.identifiers import normalize_title
from paperscout.models import (
    FeedbackEvent,
    FeedbackLabel,
    FolderMembership,
    Paper,
    ResearchFolder,
    ResearchProfile,
    utcnow,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _migrations() -> list[tuple[int, str]]:
    folder = resources.files("paperscout.store") / "migrations"
    found = []
    for entry in folder.iterdir():
        if entry.name.endswith(".sql"):
            found.append((int(entry.name.split("_", 1)[0]), entry.read_text(encoding="utf-8")))
    return sorted(found)


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> list[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    newly = []
    for version, sql in _migrations():
        if version in applied:
            continue
        with conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, _iso(utcnow())),
            )
        newly.append(version)
    return newly


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------ profiles
    def save_profile(self, profile: ResearchProfile) -> ResearchProfile:
        now = utcnow()
        existing = None
        if profile.id:
            existing = self._profile_row("id = ?", profile.id)
        if existing is None:
            existing = self._profile_row("name = ?", profile.name)
        if existing is not None:
            profile = profile.model_copy(
                update={"id": existing["id"], "created_at": _dt(existing["created_at"]), "updated_at": now}
            )
            with self.conn:
                self.conn.execute(
                    "UPDATE profiles SET name = ?, data_json = ?, updated_at = ? WHERE id = ?",
                    (profile.name, profile.model_dump_json(), _iso(now), profile.id),
                )
        else:
            profile = profile.model_copy(update={"id": new_id("prof"), "created_at": now, "updated_at": now})
            with self.conn:
                self.conn.execute(
                    "INSERT INTO profiles (id, name, data_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (profile.id, profile.name, profile.model_dump_json(), _iso(now), _iso(now)),
                )
        return profile

    def _profile_row(self, where: str, value: str) -> sqlite3.Row | None:
        return self.conn.execute(f"SELECT * FROM profiles WHERE {where}", (value,)).fetchone()

    def get_profile(self, name_or_id: str) -> ResearchProfile:
        row = self._profile_row("id = ?", name_or_id) or self._profile_row("name = ?", name_or_id)
        if row is None:
            names = ", ".join(p.name for p in self.list_profiles()) or "none defined"
            raise NotFound(f"No research profile named {name_or_id!r}. Existing profiles: {names}")
        return ResearchProfile.model_validate_json(row["data_json"])

    def list_profiles(self) -> list[ResearchProfile]:
        rows = self.conn.execute("SELECT data_json FROM profiles ORDER BY name").fetchall()
        return [ResearchProfile.model_validate_json(r["data_json"]) for r in rows]

    def active_profile(self) -> ResearchProfile | None:
        profile_id = self.get_setting("active_profile_id")
        if not profile_id:
            return None
        try:
            return self.get_profile(profile_id)
        except NotFound:
            return None

    def set_active_profile(self, name_or_id: str) -> ResearchProfile:
        profile = self.get_profile(name_or_id)
        self.set_setting("active_profile_id", profile.id)
        return profile

    # ------------------------------------------------------------------ folders
    def create_folder(self, folder: ResearchFolder) -> ResearchFolder:
        if self._folder_row("name = ?", folder.name) is not None:
            raise Conflict(f"A local folder named {folder.name!r} already exists.")
        now = utcnow()
        folder = folder.model_copy(update={"id": new_id("fld"), "created_at": now, "updated_at": now})
        with self.conn:
            self.conn.execute(
                "INSERT INTO folders (id, name, profile_id, data_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (folder.id, folder.name, folder.profile_id, folder.model_dump_json(), _iso(now), _iso(now)),
            )
        return folder

    def update_folder(self, folder: ResearchFolder) -> ResearchFolder:
        now = utcnow()
        folder = folder.model_copy(update={"updated_at": now})
        with self.conn:
            self.conn.execute(
                "UPDATE folders SET name = ?, profile_id = ?, data_json = ?, updated_at = ?, "
                "last_discovery_at = ?, last_ranked_at = ? WHERE id = ?",
                (
                    folder.name,
                    folder.profile_id,
                    folder.model_dump_json(),
                    _iso(now),
                    _iso(folder.last_discovery_at),
                    _iso(folder.last_ranked_at),
                    folder.id,
                ),
            )
        return folder

    def _folder_row(self, where: str, value: str) -> sqlite3.Row | None:
        return self.conn.execute(f"SELECT * FROM folders WHERE {where}", (value,)).fetchone()

    def find_folder(self, name_or_id: str) -> ResearchFolder | None:
        row = self._folder_row("id = ?", name_or_id) or self._folder_row("name = ?", " ".join(name_or_id.split()))
        return ResearchFolder.model_validate_json(row["data_json"]) if row else None

    def get_folder(self, name_or_id: str) -> ResearchFolder:
        folder = self.find_folder(name_or_id)
        if folder is None:
            names = ", ".join(f'"{f.name}"' for f in self.list_folders()) or "none yet"
            raise NotFound(f"No local research folder named {name_or_id!r}. Existing local folders: {names}")
        return folder

    def list_folders(self) -> list[ResearchFolder]:
        rows = self.conn.execute("SELECT data_json FROM folders ORDER BY name").fetchall()
        return [ResearchFolder.model_validate_json(r["data_json"]) for r in rows]

    def add_to_folder(
        self, folder_id: str, paper_id: str, added_by: str, source: str, note: str | None = None
    ) -> bool:
        """Idempotent: returns False when the paper is already in the folder."""
        with self.conn:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO folder_papers (folder_id, paper_id, added_at, added_by, source, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (folder_id, paper_id, _iso(utcnow()), added_by, source, note),
            )
        return cursor.rowcount == 1

    def remove_from_folder(self, folder_id: str, paper_id: str) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM folder_papers WHERE folder_id = ? AND paper_id = ?", (folder_id, paper_id)
            )
        return cursor.rowcount == 1

    def memberships(self, folder_id: str) -> list[FolderMembership]:
        rows = self.conn.execute(
            "SELECT * FROM folder_papers WHERE folder_id = ? ORDER BY added_at", (folder_id,)
        ).fetchall()
        return [
            FolderMembership(
                folder_id=r["folder_id"],
                paper_id=r["paper_id"],
                added_at=_dt(r["added_at"]),
                added_by=r["added_by"],
                source=r["source"],
                note=r["note"],
            )
            for r in rows
        ]

    def folders_for_papers(self, paper_ids: Iterable[str]) -> dict[str, list[str]]:
        ids = list(paper_ids)
        result: dict[str, list[str]] = {pid: [] for pid in ids}
        if not ids:
            return result
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT fp.paper_id, f.name FROM folder_papers fp JOIN folders f ON f.id = fp.folder_id "
            f"WHERE fp.paper_id IN ({placeholders}) ORDER BY f.name",
            ids,
        ).fetchall()
        for row in rows:
            result[row["paper_id"]].append(row["name"])
        return result

    # ------------------------------------------------------------------ papers
    def upsert_paper(self, paper: Paper, source: str) -> int:
        """Store the latest snapshot; returns the snapshot id (deduplicated by content)."""
        body = paper.model_dump_json()
        digest = hashlib.sha256(body.encode()).hexdigest()
        now = _iso(utcnow())
        with self.conn:
            self.conn.execute(
                "INSERT INTO papers (paper_id, corpus_id, doi, arxiv_id, norm_title, year, metadata_json, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(paper_id) DO UPDATE SET "
                "corpus_id = excluded.corpus_id, doi = excluded.doi, arxiv_id = excluded.arxiv_id, "
                "norm_title = excluded.norm_title, year = excluded.year, metadata_json = excluded.metadata_json, "
                "source = excluded.source, fetched_at = excluded.fetched_at",
                (
                    paper.paper_id,
                    paper.corpus_id,
                    paper.doi,
                    paper.arxiv_id,
                    normalize_title(paper.title),
                    paper.year,
                    body,
                    source,
                    now,
                ),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO paper_snapshots (paper_id, source, fetched_at, content_hash, metadata_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (paper.paper_id, source, now, digest, body),
            )
            row = self.conn.execute(
                "SELECT id FROM paper_snapshots WHERE paper_id = ? AND content_hash = ?", (paper.paper_id, digest)
            ).fetchone()
        return row["id"]

    def get_paper(self, paper_id: str) -> Paper | None:
        row = self.conn.execute("SELECT metadata_json FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
        return Paper.model_validate_json(row["metadata_json"]) if row else None

    def get_papers(self, paper_ids: Iterable[str]) -> dict[str, Paper]:
        ids = list(paper_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT paper_id, metadata_json FROM papers WHERE paper_id IN ({placeholders})", ids
        ).fetchall()
        return {r["paper_id"]: Paper.model_validate_json(r["metadata_json"]) for r in rows}

    def find_local_paper(
        self, *, doi: str | None = None, arxiv_id: str | None = None, corpus_id: int | None = None
    ) -> Paper | None:
        for column, value in (("doi", doi), ("arxiv_id", arxiv_id), ("corpus_id", corpus_id)):
            if value is None:
                continue
            row = self.conn.execute(
                f"SELECT metadata_json FROM papers WHERE {column} = ?", (value,)
            ).fetchone()
            if row:
                return Paper.model_validate_json(row["metadata_json"])
        return None

    # ------------------------------------------------------------------ feedback
    def add_feedback(self, event: FeedbackEvent) -> FeedbackEvent:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO feedback (paper_id, label, reason, profile_id, folder_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.paper_id,
                    event.label.value,
                    event.reason,
                    event.profile_id,
                    event.folder_id,
                    _iso(event.created_at),
                ),
            )
        return event.model_copy(update={"id": cursor.lastrowid})

    def feedback_history(self, paper_id: str) -> list[FeedbackEvent]:
        rows = self.conn.execute("SELECT * FROM feedback WHERE paper_id = ? ORDER BY id", (paper_id,)).fetchall()
        return [self._feedback(r) for r in rows]

    def feedback_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """Most recent feedback events with paper titles (for calibration and review)."""
        rows = self.conn.execute(
            "SELECT f.created_at, f.label, f.reason, f.paper_id, p.metadata_json FROM feedback f "
            "LEFT JOIN papers p ON p.paper_id = f.paper_id ORDER BY f.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "at": r["created_at"],
                "label": r["label"],
                "reason": r["reason"],
                "paper_id": r["paper_id"],
                "title": json.loads(r["metadata_json"])["title"] if r["metadata_json"] else None,
            }
            for r in rows
        ]

    @staticmethod
    def _feedback(row: sqlite3.Row) -> FeedbackEvent:
        return FeedbackEvent(
            id=row["id"],
            paper_id=row["paper_id"],
            label=FeedbackLabel(row["label"]),
            reason=row["reason"],
            profile_id=row["profile_id"],
            folder_id=row["folder_id"],
            created_at=_dt(row["created_at"]),
        )

    def latest_feedback(self, paper_ids: Iterable[str], profile_id: str | None) -> dict[str, FeedbackEvent]:
        """Latest applicable event per paper.

        Read-state labels (already known, understood, ...) describe the reader and
        apply under every profile; relevance labels apply to the profile they were
        recorded under, or globally when recorded without one.
        """
        ids = list(paper_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM feedback WHERE paper_id IN ({placeholders}) ORDER BY id", ids
        ).fetchall()
        latest: dict[str, FeedbackEvent] = {}
        for row in rows:
            event = self._feedback(row)
            applies = event.label.counts_as_read or event.profile_id in (None, profile_id)
            if applies:
                latest[event.paper_id] = event
        return latest

    def feedback_paper_ids(self, profile_id: str | None, labels: set[FeedbackLabel]) -> list[str]:
        latest = self.latest_feedback(
            [r["paper_id"] for r in self.conn.execute("SELECT DISTINCT paper_id FROM feedback")], profile_id
        )
        return [pid for pid, ev in latest.items() if ev.label in labels]

    # ------------------------------------------------------------------ runs & provenance
    def create_run(
        self,
        kind: str,
        query: str | None,
        params: dict[str, Any],
        profile_id: str | None,
        ranking_version: str,
    ) -> str:
        run_id = f"run_{utcnow().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
        with self.conn:
            self.conn.execute(
                "INSERT INTO runs (id, kind, query, params_json, profile_id, ranking_version, api_sources, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, '[]', ?)",
                (run_id, kind, query, json.dumps(params, default=str), profile_id, ranking_version, _iso(utcnow())),
            )
        return run_id

    def finish_run(self, run_id: str, api_sources: list[str], notes: list[str]) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at = ?, api_sources = ?, notes_json = ? WHERE id = ?",
                (_iso(utcnow()), json.dumps(sorted(set(api_sources))), json.dumps(notes), run_id),
            )

    def add_run_candidate(
        self, run_id: str, paper_id: str, position: int, provenance: str, assessment: str, snapshot_id: int | None
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO run_candidates (run_id, paper_id, position, provenance, assessment, snapshot_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, paper_id, position, provenance, assessment, snapshot_id),
            )

    def previously_surfaced(self, paper_ids: Iterable[str], exclude_run: str | None = None) -> set[str]:
        ids = list(paper_ids)
        if not ids:
            return set()
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT DISTINCT paper_id FROM run_candidates WHERE paper_id IN ({placeholders}) AND run_id != ?",
            [*ids, exclude_run or ""],
        ).fetchall()
        return {r["paper_id"] for r in rows}

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFound(f"No run {run_id!r}")
        return dict(row)

    def latest_run(self, kinds: tuple[str, ...]) -> dict[str, Any] | None:
        placeholders = ",".join("?" * len(kinds))
        row = self.conn.execute(
            f"SELECT * FROM runs WHERE kind IN ({placeholders}) AND finished_at IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 1",
            kinds,
        ).fetchone()
        return dict(row) if row else None

    def run_candidates(self, run_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM run_candidates WHERE run_id = ? ORDER BY position", (run_id,)
        ).fetchall()

    # ------------------------------------------------------------------ assessments
    def add_assessment(
        self,
        paper_id: str,
        profile_id: str | None,
        assessor: str,
        prompt_version: str | None,
        evidence_level: str,
        data: dict[str, Any],
    ) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO assessments (paper_id, profile_id, assessor, prompt_version, evidence_level, data_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (paper_id, profile_id, assessor, prompt_version, evidence_level, json.dumps(data), _iso(utcnow())),
            )
        return cursor.lastrowid

    def latest_claude_assessments(self, paper_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(paper_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM assessments WHERE paper_id IN ({placeholders}) AND assessor LIKE 'claude:%' ORDER BY id",
            ids,
        ).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            latest[row["paper_id"]] = {**json.loads(row["data_json"]), "stored_at": row["created_at"],
                                       "assessor": row["assessor"]}
        return latest

    def assessments_for(self, paper_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM assessments WHERE paper_id = ? ORDER BY id", (paper_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ audit
    def audit(
        self, action: str, destination: str, target: str, dry_run: bool, outcome: str, details: dict[str, Any]
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO audit_log (at, action, destination, target, dry_run, outcome, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_iso(utcnow()), action, destination, target, int(dry_run), outcome, json.dumps(details, default=str)),
            )

    def audit_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ http cache
    def cache_get(self, key: str, now: datetime | None = None) -> str | None:
        row = self.conn.execute("SELECT body, expires_at FROM http_cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        if _dt(row["expires_at"]) <= (now or utcnow()):
            return None
        return row["body"]

    def cache_put(self, key: str, url: str, body: str, ttl_seconds: int, now: datetime | None = None) -> None:
        now = now or utcnow()
        with self.conn:
            self.conn.execute(
                "INSERT INTO http_cache (key, url, body, fetched_at, expires_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET body = excluded.body, fetched_at = excluded.fetched_at, "
                "expires_at = excluded.expires_at",
                (key, url, body, _iso(now), _iso(now + timedelta(seconds=ttl_seconds))),
            )

    def cache_purge_expired(self) -> int:
        with self.conn:
            cursor = self.conn.execute("DELETE FROM http_cache WHERE expires_at <= ?", (_iso(utcnow()),))
        return cursor.rowcount
