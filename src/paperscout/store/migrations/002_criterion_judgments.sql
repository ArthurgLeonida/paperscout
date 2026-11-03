-- 002: 1-100 criterion ratings written by Claude judge subagents (append-only).
-- A rating applies to one ranking context (topic or folder criteria + profile), identified by context_key,
-- and is reused by later runs with the same context.

CREATE TABLE criterion_judgments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    context_key    TEXT NOT NULL,
    paper_id       TEXT NOT NULL,
    criterion      TEXT NOT NULL,
    score          INTEGER,
    confidence     TEXT NOT NULL,
    basis          TEXT NOT NULL,
    evidence       TEXT NOT NULL,
    judge          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX idx_judgments_context ON criterion_judgments(context_key, paper_id);
