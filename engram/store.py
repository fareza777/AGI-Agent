"""SQLite-backed cognitive substrate.

Implements the storage for three DESIGN.md subsystems:
  S1 Episodic Event Log  — append-only `events` table (never updated, never deleted;
                           `processed` only marks consolidation progress)
  S2 Semantic Graph      — `claims` table: bitemporal, provenance-linked beliefs
  S7 Goal Tree           — `goals` table (flat list in this minimal version)

Full-text retrieval uses SQLite FTS5 (BM25) over both events and claims.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,
    chat_id   TEXT,
    actor     TEXT NOT NULL,            -- user | agent | system
    kind      TEXT NOT NULL,            -- message | command | consolidation | reflection
    content   TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    content, content='events', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS claims (
    id               INTEGER PRIMARY KEY,
    subject          TEXT NOT NULL,
    predicate        TEXT NOT NULL,
    value            TEXT NOT NULL,
    kind             TEXT NOT NULL,     -- fact | preference | lesson | insight
    confidence       REAL NOT NULL,
    source_event_ids TEXT NOT NULL,     -- JSON array of event ids (provenance)
    chat_id          TEXT,
    created_at       TEXT NOT NULL,
    valid_from       TEXT NOT NULL,
    valid_to         TEXT,              -- NULL = currently believed true
    superseded_by    INTEGER            -- claim id that replaced this one
);

CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(
    subject, predicate, value, content='claims', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS claims_ai AFTER INSERT ON claims BEGIN
    INSERT INTO claims_fts(rowid, subject, predicate, value)
    VALUES (new.id, new.subject, new.predicate, new.value);
END;

CREATE TABLE IF NOT EXISTS goals (
    id         INTEGER PRIMARY KEY,
    chat_id    TEXT,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',   -- active | done | dropped
    notes      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY,
    chat_id    TEXT NOT NULL,
    due_ts     TEXT NOT NULL,                    -- ISO 8601 UTC
    message    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | cancelled
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, db_path: str = None):
        self._db_path = db_path or config.DB_PATH
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------------- S1: episodic event log ----------------

    def log_event(self, actor: str, kind: str, content: str, chat_id: str = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts, chat_id, actor, kind, content) VALUES (?,?,?,?,?)",
                (now_iso(), chat_id, actor, kind, content),
            )
            self._conn.commit()
            return cur.lastrowid

    def recent_events(self, chat_id: str, limit: int) -> list:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE chat_id=? AND kind IN ('message','command') "
                "ORDER BY id DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def unprocessed_events(self, limit: int = 60) -> list:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM events WHERE processed=0 AND kind='message' "
                "ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()

    def mark_processed(self, event_ids: list):
        if not event_ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE events SET processed=1 WHERE id=?", [(i,) for i in event_ids]
            )
            self._conn.commit()

    def unprocessed_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE processed=0 AND kind='message'"
            ).fetchone()
        return row["n"]

    def search_events(self, query: str, limit: int) -> list:
        match = _fts_query(query)
        if not match:
            return []
        with self._lock:
            try:
                return self._conn.execute(
                    "SELECT e.*, bm25(events_fts) AS rank FROM events_fts "
                    "JOIN events e ON e.id = events_fts.rowid "
                    "WHERE events_fts MATCH ? ORDER BY rank LIMIT ?",
                    (match, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []

    # ---------------- S2: semantic claims ----------------

    def add_claim(self, subject: str, predicate: str, value: str, kind: str,
                  confidence: float, source_event_ids: list, chat_id: str = None) -> dict:
        """Insert a claim into its (subject, predicate) slot.

        Returns {"id": new_id, "superseded": old_row_or_None, "duplicate": bool}.
        Slot semantics (S4 contradiction handling): an active claim with the same
        subject+predicate but a different value is closed (valid_to set) and linked
        via superseded_by. An identical value just boosts confidence.
        """
        ts = now_iso()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM claims WHERE subject=? AND predicate=? AND valid_to IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (subject, predicate),
            ).fetchone()

            if existing and existing["value"].strip().lower() == value.strip().lower():
                new_conf = min(1.0, max(existing["confidence"], confidence) + 0.05)
                self._conn.execute(
                    "UPDATE claims SET confidence=? WHERE id=?", (new_conf, existing["id"])
                )
                self._conn.commit()
                return {"id": existing["id"], "superseded": None, "duplicate": True}

            cur = self._conn.execute(
                "INSERT INTO claims (subject, predicate, value, kind, confidence, "
                "source_event_ids, chat_id, created_at, valid_from) VALUES (?,?,?,?,?,?,?,?,?)",
                (subject, predicate, value, kind, confidence,
                 json.dumps(source_event_ids), chat_id, ts, ts),
            )
            new_id = cur.lastrowid

            superseded = None
            if existing:
                self._conn.execute(
                    "UPDATE claims SET valid_to=?, superseded_by=? WHERE id=?",
                    (ts, new_id, existing["id"]),
                )
                superseded = existing

            self._conn.commit()
            return {"id": new_id, "superseded": superseded, "duplicate": False}

    def search_claims(self, query: str, limit: int, active_only: bool = True) -> list:
        match = _fts_query(query)
        if not match:
            return []
        active = "AND c.valid_to IS NULL" if active_only else ""
        with self._lock:
            try:
                return self._conn.execute(
                    f"SELECT c.*, bm25(claims_fts) AS rank FROM claims_fts "
                    f"JOIN claims c ON c.id = claims_fts.rowid "
                    f"WHERE claims_fts MATCH ? {active} ORDER BY rank LIMIT ?",
                    (match, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []

    def active_claims(self, limit: int = 200) -> list:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM claims WHERE valid_to IS NULL ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def claim_history(self, subject: str, predicate: str) -> list:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM claims WHERE subject=? AND predicate=? ORDER BY id ASC",
                (subject, predicate),
            ).fetchall()

    # ---------------- S7: goals ----------------

    def add_goal(self, chat_id: str, title: str) -> int:
        ts = now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO goals (chat_id, title, created_at, updated_at) VALUES (?,?,?,?)",
                (chat_id, title, ts, ts),
            )
            self._conn.commit()
            return cur.lastrowid

    def set_goal_status(self, goal_id: int, status: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE goals SET status=?, updated_at=? WHERE id=?",
                (status, now_iso(), goal_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def goals(self, chat_id: str, status: str = "active") -> list:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM goals WHERE chat_id=? AND status=? ORDER BY id ASC",
                (chat_id, status),
            ).fetchall()

    # ---------------- reminders (scheduled messages) ----------------

    def add_reminder(self, chat_id: str, due_ts: str, message: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO reminders (chat_id, due_ts, message, created_at) VALUES (?,?,?,?)",
                (chat_id, due_ts, message, now_iso()),
            )
            self._conn.commit()
            return cur.lastrowid

    def due_reminders(self) -> list:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM reminders WHERE status='pending' AND due_ts<=? ORDER BY due_ts",
                (now_iso(),),
            ).fetchall()

    def pending_reminders(self, chat_id: str) -> list:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM reminders WHERE chat_id=? AND status='pending' ORDER BY due_ts",
                (chat_id,),
            ).fetchall()

    def set_reminder_status(self, reminder_id: int, status: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE reminders SET status=? WHERE id=?", (status, reminder_id)
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ---------------- meta (e.g. Telegram update offset) ----------------

    def get_meta(self, key: str, default: str = None) -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str):
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def close(self):
        self._conn.close()


def _fts_query(text: str) -> str:
    """Convert free text to a safe FTS5 OR-query of quoted terms."""
    terms = [t for t in "".join(c if c.isalnum() else " " for c in text).split() if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in terms[:12])
