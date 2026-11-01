-- 001: initial schema. Feedback, assessments, snapshots and the audit log are append-only.

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE profiles (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    data_json  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE folders (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE COLLATE NOCASE,
    profile_id        TEXT REFERENCES profiles(id) ON DELETE SET NULL,
    data_json         TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    last_discovery_at TEXT,
    last_ranked_at    TEXT
);

-- Latest known metadata per paper (S2 paperId is the primary key).
CREATE TABLE papers (
    paper_id      TEXT PRIMARY KEY,
    corpus_id     INTEGER,
    doi           TEXT,
    arxiv_id      TEXT,
    norm_title    TEXT NOT NULL,
    year          INTEGER,
    metadata_json TEXT NOT NULL,
    source        TEXT NOT NULL,
    fetched_at    TEXT NOT NULL
);
CREATE INDEX idx_papers_doi ON papers(doi);
CREATE INDEX idx_papers_arxiv ON papers(arxiv_id);
CREATE INDEX idx_papers_title ON papers(norm_title);

-- Every metadata snapshot we used, for provenance.
CREATE TABLE paper_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id      TEXT NOT NULL,
    source        TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    UNIQUE (paper_id, content_hash)
);

CREATE TABLE folder_papers (
    folder_id  TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    paper_id   TEXT NOT NULL REFERENCES papers(paper_id),
    added_at   TEXT NOT NULL,
    added_by   TEXT NOT NULL,
    source     TEXT NOT NULL,
    note       TEXT,
    PRIMARY KEY (folder_id, paper_id)
);

CREATE TABLE feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id   TEXT NOT NULL,
    label      TEXT NOT NULL,
    reason     TEXT,
    profile_id TEXT,
    folder_id  TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_feedback_paper ON feedback(paper_id);

CREATE TABLE runs (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    query           TEXT,
    params_json     TEXT NOT NULL,
    profile_id      TEXT,
    ranking_version TEXT NOT NULL,
    api_sources     TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    notes_json      TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE run_candidates (
    run_id         TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    paper_id       TEXT NOT NULL,
    position       INTEGER NOT NULL,
    provenance     TEXT NOT NULL,
    assessment     TEXT NOT NULL,
    snapshot_id    INTEGER,
    PRIMARY KEY (run_id, paper_id)
);
CREATE INDEX idx_run_candidates_paper ON run_candidates(paper_id);

CREATE TABLE assessments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id       TEXT NOT NULL,
    profile_id     TEXT,
    assessor       TEXT NOT NULL,
    prompt_version TEXT,
    evidence_level TEXT NOT NULL,
    data_json      TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    action       TEXT NOT NULL,
    destination  TEXT NOT NULL,
    target       TEXT NOT NULL,
    dry_run      INTEGER NOT NULL,
    outcome      TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE TABLE http_cache (
    key        TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    body       TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
