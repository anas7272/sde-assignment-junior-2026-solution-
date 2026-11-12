"""SQLite schema and connection helper.

Design in one paragraph: ``events`` is the append-only interaction ledger
(triggers forbid UPDATE/DELETE). An event's lead is NOT stored on the row;
it is *derived* by the ``v_event_links`` view (workflow + phone hash ->
lead, overrides via call_id). That is what makes a late-arriving lead
automatically "un-orphan" earlier events without ever editing the ledger.
"""
from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS ingest_files (
    file_id                    INTEGER PRIMARY KEY,
    drop_name                  TEXT    NOT NULL,
    relpath                    TEXT    NOT NULL,
    kind                       TEXT    NOT NULL CHECK (kind IN ('leads', 'events')),
    workflow_id                TEXT,
    batch_id                   TEXT,
    sha256                     TEXT    NOT NULL,
    ingested_at                TEXT    NOT NULL,
    lead_rows_total            INTEGER NOT NULL DEFAULT 0,
    lead_rows_rejected         INTEGER NOT NULL DEFAULT 0,
    duplicate_lead_rows_merged INTEGER NOT NULL DEFAULT 0,
    field_warnings             INTEGER NOT NULL DEFAULT 0,
    events_total               INTEGER NOT NULL DEFAULT 0,
    malformed_events           INTEGER NOT NULL DEFAULT 0,
    duplicate_events_dropped   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (drop_name, relpath)
);

CREATE TABLE IF NOT EXISTS workflows (
    workflow_id     TEXT PRIMARY KEY,
    reference_field TEXT,
    phone_field     TEXT NOT NULL,
    schema_json     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispositions (
    workflow_id TEXT    NOT NULL REFERENCES workflows(workflow_id),
    code        TEXT    NOT NULL,
    is_terminal INTEGER NOT NULL CHECK (is_terminal IN (0, 1)),
    is_success  INTEGER NOT NULL CHECK (is_success  IN (0, 1)),
    PRIMARY KEY (workflow_id, code)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS leads (
    lead_id        INTEGER PRIMARY KEY,
    workflow_id    TEXT    NOT NULL REFERENCES workflows(workflow_id),
    phone_hash     TEXT    NOT NULL,              -- HMAC-SHA256(key, +91XXXXXXXXXX)
    phone_last4    TEXT    NOT NULL CHECK (length(phone_last4) = 4),
    batch_id       TEXT    NOT NULL,              -- file stem of first valid row; permanent
    source_file_id INTEGER NOT NULL REFERENCES ingest_files(file_id),
    UNIQUE (workflow_id, phone_hash)              -- lead identity
);

-- One row per (lead, field). PII fields hold ciphertext (is_encrypted = 1).
CREATE TABLE IF NOT EXISTS lead_fields (
    lead_id      INTEGER NOT NULL REFERENCES leads(lead_id),
    field_name   TEXT    NOT NULL,
    value        TEXT    NOT NULL,
    is_encrypted INTEGER NOT NULL CHECK (is_encrypted IN (0, 1)),
    PRIMARY KEY (lead_id, field_name)
) WITHOUT ROWID;

-- Rejected lead rows. Deliberately carries no row content, so no PII.
CREATE TABLE IF NOT EXISTS rejections (
    file_id     INTEGER NOT NULL REFERENCES ingest_files(file_id),
    workflow_id TEXT    NOT NULL,
    batch_id    TEXT    NOT NULL,
    row_number  INTEGER NOT NULL,
    reason      TEXT    NOT NULL,
    PRIMARY KEY (file_id, row_number)
) WITHOUT ROWID;

-- The append-only interaction ledger.
CREATE TABLE IF NOT EXISTS events (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,  -- arrival order: deterministic tie-break
    event_id         TEXT    NOT NULL UNIQUE,            -- idempotency / dedupe key
    event_type       TEXT,
    workflow_id      TEXT,
    call_id          TEXT,
    phone_hash       TEXT,                               -- NULL if absent or invalid; never plaintext
    occurred_at_utc  TEXT    NOT NULL,
    occurred_us      INTEGER NOT NULL,                   -- exact sortable instant
    disposition_code TEXT,
    payload          TEXT    NOT NULL,                   -- JSON; contains no phone
    file_id          INTEGER NOT NULL REFERENCES ingest_files(file_id)
);
CREATE INDEX IF NOT EXISTS ix_events_link ON events (workflow_id, phone_hash);
CREATE INDEX IF NOT EXISTS ix_events_call ON events (call_id);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is an append-only ledger'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is an append-only ledger'); END;

-- Derived link: which lead does each event belong to? NULL = orphan.
-- Overrides carry no phone; they follow the lead of the call's other events
-- (preferring the call.initiated event).
CREATE VIEW IF NOT EXISTS v_event_links AS
SELECT e.seq, e.event_type, e.call_id, e.occurred_us, e.disposition_code,
  CASE WHEN e.event_type = 'disposition.override' THEN
    (SELECT l.lead_id
       FROM events o
       JOIN leads l ON l.workflow_id = o.workflow_id AND l.phone_hash = o.phone_hash
      WHERE o.call_id = e.call_id
        AND o.event_type <> 'disposition.override'
        AND (e.workflow_id IS NULL OR o.workflow_id = e.workflow_id)
      ORDER BY (o.event_type = 'call.initiated') DESC, o.seq
      LIMIT 1)
  ELSE
    (SELECT l.lead_id FROM leads l
      WHERE l.workflow_id = e.workflow_id AND l.phone_hash = e.phone_hash)
  END AS lead_id
FROM events e;

-- Quarantine = ledger rows that currently link to no lead (final state, always fresh).
CREATE VIEW IF NOT EXISTS v_quarantine AS
SELECT e.* FROM events e JOIN v_event_links v ON v.seq = e.seq WHERE v.lead_id IS NULL;
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)  # explicit BEGIN/COMMIT in the code
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(DDL)
    return conn
