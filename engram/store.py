"""SQLite-backed cognitive substrate.

Implements the storage for three DESIGN.md subsystems:
  S1 Episodic Event Log  — append-only `events` table (never updated, never deleted;
                           `processed` only marks consolidation progress)
  S2 Semantic Graph      — `claims` table: bitemporal, provenance-linked beliefs
  S7 Goal Tree           — `goals` table (flat list in this minimal version)

Full-text retrieval uses SQLite FTS5 (BM25) over both events and claims.
"""

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

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

CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY,
    chat_id    TEXT NOT NULL,
    prompt     TEXT NOT NULL,                    -- what the agent should DO
    due_ts     TEXT NOT NULL,                    -- next run, ISO 8601 UTC
    recurrence TEXT,                             -- NULL=once | hourly | daily | weekly | every:<minutes>
    status     TEXT NOT NULL DEFAULT 'active',   -- active | done | cancelled
    created_at TEXT NOT NULL,
    last_run   TEXT
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
        # WAL lets the background "sleep cycle" thread read/write without
        # colliding with the chat thread, and busy_timeout waits out brief
        # contention instead of raising "database is locked". synchronous=NORMAL
        # is the safe, fast pairing for WAL. (In-memory DBs don't support WAL.)
        if self._db_path != ":memory:":
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self):
        """Additive, idempotent schema migrations for existing databases."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(claims)")}
        if "embedding" not in cols:
            self._conn.execute("ALTER TABLE claims ADD COLUMN embedding BLOB")

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

    def search_events(self, query: str, limit: int, chat_id: str = None) -> list:
        match = _fts_query(query)
        if not match:
            return []
        # Scope to this chat (plus global NULL-chat rows) so one user's episodes
        # never surface in another user's context. chat_id=None = no filter.
        scope = "WHERE events_fts MATCH ?"
        params = [match]
        if chat_id is not None:
            scope += " AND (e.chat_id = ? OR e.chat_id IS NULL)"
            params.append(chat_id)
        params.append(limit)
        with self._lock:
            try:
                return self._conn.execute(
                    "SELECT e.*, bm25(events_fts) AS rank FROM events_fts "
                    "JOIN events e ON e.id = events_fts.rowid "
                    f"{scope} ORDER BY rank LIMIT ?",
                    tuple(params),
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
        predicate = canonical_predicate(predicate)
        ts = now_iso()
        with self._lock:
            # The belief slot is per (subject, predicate, chat_id): one user's
            # update must NOT supersede another user's same-named belief.
            # `chat_id IS ?` matches both a value and NULL (global) correctly.
            existing = self._conn.execute(
                "SELECT * FROM claims WHERE subject=? AND predicate=? "
                "AND valid_to IS NULL AND chat_id IS ? "
                "ORDER BY id DESC LIMIT 1",
                (subject, predicate, chat_id),
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
        # Embed outside the lock (network call); store best-effort. Off unless
        # an embeddings endpoint is configured, so this is a no-op by default.
        self._maybe_store_embedding(new_id, f"{subject} {predicate} {value}")
        return {"id": new_id, "superseded": superseded, "duplicate": False}

    def _maybe_store_embedding(self, claim_id: int, text: str):
        from . import embeddings

        if not embeddings.available():
            return
        try:
            vec = embeddings.embed(text)
            if not vec:
                return
            with self._lock:
                self._conn.execute(
                    "UPDATE claims SET embedding=? WHERE id=?",
                    (embeddings.pack(vec), claim_id),
                )
                self._conn.commit()
        except Exception:
            pass  # retrieval still works via BM25; embedding is an enhancement

    def search_claims(self, query: str, limit: int, active_only: bool = True,
                      chat_id: str = None) -> list:
        match = _fts_query(query)
        if not match:
            return []
        active = "AND c.valid_to IS NULL" if active_only else ""
        # Scope beliefs to this chat (+ global NULL-chat rows) so one user's
        # memory never leaks into another's. chat_id=None = no filter.
        scope = ""
        params = [match]
        if chat_id is not None:
            scope = "AND (c.chat_id = ? OR c.chat_id IS NULL)"
            params.append(chat_id)
        # When embeddings are on, pull a wider BM25 pool and re-rank it
        # semantically. When off, pool == limit and rank order is unchanged.
        from . import embeddings

        hybrid = embeddings.available()
        pool = max(limit * 5, limit) if hybrid else limit
        params.append(pool)
        with self._lock:
            try:
                rows = self._conn.execute(
                    f"SELECT c.*, bm25(claims_fts) AS rank FROM claims_fts "
                    f"JOIN claims c ON c.id = claims_fts.rowid "
                    f"WHERE claims_fts MATCH ? {active} {scope} ORDER BY rank LIMIT ?",
                    tuple(params),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        if not hybrid or len(rows) <= limit:
            return rows[:limit]
        return self._rerank_by_embedding(query, rows, limit)

    def _rerank_by_embedding(self, query: str, rows: list, limit: int) -> list:
        """Blend BM25 rank with embedding cosine similarity. Falls back to the
        BM25 order if the query can't be embedded."""
        from . import embeddings

        qvec = embeddings.embed(query)
        if not qvec:
            return rows[:limit]
        # BM25 rank is negative (lower = better); turn into a 0..1 score.
        ranks = [r["rank"] for r in rows]
        lo, hi = min(ranks), max(ranks)
        spread = (hi - lo) or 1.0
        scored = []
        for r in rows:
            bm = 1.0 - (r["rank"] - lo) / spread
            sim = embeddings.cosine(qvec, embeddings.unpack(r["embedding"])) \
                if r["embedding"] else 0.0
            scored.append((0.4 * bm + 0.6 * sim, r))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [r for _, r in scored[:limit]]

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

    # ---------------- memory maintenance (sleep-cycle hygiene) ----------------

    def maintain_memory(self, decay_days: int = 30, decay_factor: float = 0.9,
                        floor: float = 0.15, backfill_limit: int = 32) -> dict:
        """Keep the belief store healthy:
          - decay 'insight' claims (hypotheses) not refreshed in `decay_days`,
          - retire any active claim whose confidence fell below `floor`,
          - backfill embeddings for claims that lack them (when enabled).
        Facts/preferences are NOT decayed by age — a fact stays true until
        contradicted. Returns counts for logging."""
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=decay_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        decayed = retired = 0
        with self._lock:
            stale = self._conn.execute(
                "SELECT id, confidence FROM claims WHERE valid_to IS NULL "
                "AND kind='insight' AND valid_from < ?", (cutoff,),
            ).fetchall()
            for r in stale:
                new_conf = round(r["confidence"] * decay_factor, 4)
                self._conn.execute("UPDATE claims SET confidence=? WHERE id=?",
                                   (new_conf, r["id"]))
                decayed += 1
            cur = self._conn.execute(
                "UPDATE claims SET valid_to=? WHERE valid_to IS NULL AND confidence < ?",
                (now_iso(), floor),
            )
            retired = cur.rowcount
            self._conn.commit()
        backfilled = self._backfill_embeddings(backfill_limit)
        return {"decayed": decayed, "retired": retired, "backfilled": backfilled}

    def _backfill_embeddings(self, limit: int) -> int:
        from . import embeddings

        if not embeddings.available():
            return 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, subject, predicate, value FROM claims "
                "WHERE valid_to IS NULL AND embedding IS NULL LIMIT ?", (limit,),
            ).fetchall()
        done = 0
        for r in rows:
            try:
                vec = embeddings.embed(f"{r['subject']} {r['predicate']} {r['value']}")
                if not vec:
                    continue
                with self._lock:
                    self._conn.execute("UPDATE claims SET embedding=? WHERE id=?",
                                       (embeddings.pack(vec), r["id"]))
                    self._conn.commit()
                done += 1
            except Exception:
                break  # endpoint hiccup — try again next maintenance pass
        return done

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

    # ---------------- scheduled agent tasks ----------------

    def add_task(self, chat_id: str, prompt: str, due_ts: str,
                 recurrence: str = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks (chat_id, prompt, due_ts, recurrence, created_at) "
                "VALUES (?,?,?,?,?)",
                (chat_id, prompt, due_ts, recurrence, now_iso()),
            )
            self._conn.commit()
            return cur.lastrowid

    def due_tasks(self) -> list:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM tasks WHERE status='active' AND due_ts<=? ORDER BY due_ts",
                (now_iso(),),
            ).fetchall()

    def active_tasks(self, chat_id: str) -> list:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM tasks WHERE chat_id=? AND status='active' ORDER BY due_ts",
                (chat_id,),
            ).fetchall()

    def complete_task_run(self, task_id: int, next_due: str = None) -> None:
        """Record a run; reschedule if recurring, else mark done."""
        with self._lock:
            if next_due:
                self._conn.execute(
                    "UPDATE tasks SET due_ts=?, last_run=? WHERE id=?",
                    (next_due, now_iso(), task_id),
                )
            else:
                self._conn.execute(
                    "UPDATE tasks SET status='done', last_run=? WHERE id=?",
                    (now_iso(), task_id),
                )
            self._conn.commit()

    def set_task_status(self, task_id: int, status: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status=? WHERE id=?", (status, task_id)
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ---------------- lessons (S10 learning loop) ----------------

    def recent_lessons(self, limit: int = 5) -> list:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM claims WHERE kind='lesson' AND valid_to IS NULL "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

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


# Predicate normalization (S2 slot consistency). Consolidation relies on the
# same attribute always landing in the same (subject, predicate) slot so updates
# supersede instead of piling up as duplicate beliefs. Models phrase predicates
# inconsistently (works_at / work_place / employer), so we canonicalize.
_PREDICATE_ALIASES = {
    "work_place": "works_at", "workplace": "works_at", "employer": "works_at",
    "company": "works_at", "job": "occupation", "profession": "occupation",
    "role": "occupation", "favorite_food": "food_preference",
    "likes_food": "food_preference", "food_likes": "food_preference",
    "dietary_preference": "food_preference", "lives_in": "location",
    "city": "location", "based_in": "location", "hometown": "location",
    "birthday": "date_of_birth", "dob": "date_of_birth",
    "phone_number": "phone", "contact_number": "phone",
    "email_address": "email", "preferred_language": "language",
    "lang": "language", "goal": "current_goal",
}


def canonical_predicate(predicate: str) -> str:
    """Map a predicate to its canonical slot name so updates supersede."""
    p = re.sub(r"[\s\-]+", "_", (predicate or "").strip().lower())
    p = re.sub(r"^(user_|the_|my_)", "", p)
    return _PREDICATE_ALIASES.get(p, p)


def _fts_query(text: str) -> str:
    """Convert free text to a safe FTS5 OR-query of quoted terms."""
    terms = [t for t in "".join(c if c.isalnum() else " " for c in text).split() if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in terms[:12])


_RECURRENCES = {"hourly": 60, "daily": 60 * 24, "weekly": 60 * 24 * 7}


def next_occurrence(due_iso: str, recurrence: str) -> str:
    """Next due timestamp for a recurring task, or None for one-shot tasks.

    Skips ahead past missed runs (e.g. the bot was offline for two days), so a
    daily task fires once on restart instead of replaying every missed day.
    """
    if not recurrence:
        return None
    rec = recurrence.strip().lower()
    if rec.startswith("every:"):
        try:
            minutes = max(1, int(rec.split(":", 1)[1]))
        except ValueError:
            return None
    else:
        minutes = _RECURRENCES.get(rec)
        if minutes is None:
            return None
    due = datetime.strptime(due_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    step = timedelta(minutes=minutes)
    while due <= now:
        due += step
    return due.strftime("%Y-%m-%dT%H:%M:%SZ")
