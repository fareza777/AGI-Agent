"""S3 Consolidation Engine + S4 Contradiction Handling + S9 Insight Generator.

consolidate(): the "sleep cycle". Reads unprocessed episodes from the event
log, asks the model to distill them into structured claims (facts, preferences,
lessons), and writes them into the claim store. Slot-based supersession in
Store.add_claim handles contradictions: a new value for an existing
(subject, predicate) slot closes the old claim and links it via superseded_by.

reflect(): samples current beliefs + recent episodes and generates "insight"
claims — cross-memory patterns that exist in no single event.

Both run from a background thread (see agent.py) and on demand via /reflect.
"""

import json
import logging

from . import hygiene, llm
from .store import Store

log = logging.getLogger("engram.consolidator")

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "value": {"type": "string"},
                    "kind": {"type": "string", "enum": ["fact", "preference", "lesson"]},
                    "confidence": {"type": "number"},
                },
                "required": ["subject", "predicate", "value", "kind", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

_EXTRACT_SYSTEM = """\
You are the memory-consolidation module of a personal AI agent. You receive a
transcript of recent events and distill them into durable, atomic claims.

Rules:
- Extract only information worth remembering long-term: stable facts about the
  user and their world, preferences, decisions, commitments, and lessons from
  mistakes. Skip small talk and one-off trivia.
- One claim = one atomic statement. subject is the entity (usually "user", or a
  named person/project), predicate is a short snake_case attribute
  (e.g. dietary_preference, works_at, project_status), value is the content.
- Use a consistent predicate for the same attribute so updates land in the same
  slot — that is how the agent detects when something changed.
- confidence: 0.95 for the user's explicit statements, 0.7 for clear
  implications, 0.5 for guesses.
- GROUNDING: a "fact" or "preference" claim must be grounded in something the
  USER said (events whose actor is user). NEVER distill a fact/preference from
  text that appears only in agent turns — the agent's own replies may contain
  errors or fabrications, and storing them as beliefs makes those errors
  permanent. Agent turns are only evidence for "lesson" claims (what the agent
  did wrong / should do differently).
- kind "lesson" is for things the agent should do differently next time. Watch
  specifically for: the user correcting the agent, a tool call that failed and
  how it was fixed, output the user disliked (format, length, language). Write
  the lesson as an actionable rule, e.g. "when making documents for the user,
  use Indonesian unless asked otherwise" (subject "agent", predicate like
  lesson_document_language).
- NEVER store volatile filesystem state: directory listings, folder names or
  contents, what files exist, drive access status, or any snapshot of a drive/
  folder. These go stale fast and the agent must always re-check live with
  list_dir — recalling them causes hallucinated listings. (A stable, durable
  fact like "the user has a G: drive synced from Google Drive" is fine; the
  *contents* of any folder are not.)
- Return an empty list if nothing is worth remembering."""

# Volatile filesystem state and self-referential meta-lessons must never
# become long-term beliefs. The LLM is told this above; hygiene.is_poisoned is
# the hard backstop (shared with the composer's retrieval filter and the
# store's quarantine pass).
_is_volatile_fs_claim = hygiene.is_poisoned

_INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["subject", "predicate", "value", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["insights"],
    "additionalProperties": False,
}

_INSIGHT_SYSTEM = """\
You are the reflection module of a personal AI agent. You receive the agent's
current beliefs and recent episodes. Generate at most 3 INSIGHTS: higher-order
patterns, trends, or hypotheses that are not stated in any single memory
(e.g. "user tends to deprioritize project X", "user's interest shifted from A
to B over the last weeks"). Each insight needs a falsifiable phrasing in value.
confidence ≤ 0.6 — insights are hypotheses, not facts. Return an empty list if
the evidence is too thin; do not manufacture insights."""


def consolidate(store: Store) -> dict:
    """One consolidation pass. Returns counts: {'events':n,'new':n,'updated':n}.

    Events are grouped BY chat so one user's transcript never distills into
    another user's beliefs, and a failure in one group doesn't block the others
    (a malformed batch is dead-lettered instead of looping forever)."""
    events = store.unprocessed_events()
    if not events:
        return {"events": 0, "new": 0, "updated": 0}

    by_chat = {}
    for e in events:
        by_chat.setdefault(e["chat_id"], []).append(e)

    total_new = total_updated = total_events = 0
    for chat_id, group in by_chat.items():
        total_events += len(group)
        event_ids = [e["id"] for e in group]
        transcript = "\n".join(
            f"[event {e['id']} | {e['ts']} | {e['actor']}] {e['content']}"
            for e in group
        )
        try:
            result = llm.extract(_EXTRACT_SYSTEM, transcript, _EXTRACT_SCHEMA)
        except Exception:
            # Dead-letter: mark processed so a permanently-bad batch can't wedge
            # the whole consolidation pipeline. The raw events stay in the log.
            log.exception("consolidation extract failed for chat %s — "
                          "dead-lettering %d events", chat_id, len(group))
            store.mark_processed(event_ids)
            continue
        for c in result.get("claims", []):
            if _is_volatile_fs_claim(c.get("predicate", ""), c.get("value", "")):
                continue  # never persist filesystem state as a belief
            try:
                outcome = store.add_claim(
                    subject=c["subject"].strip().lower(),
                    predicate=c["predicate"].strip().lower(),
                    value=c["value"].strip(),
                    kind=c["kind"],
                    confidence=max(0.0, min(1.0, float(c["confidence"]))),
                    source_event_ids=event_ids,
                    chat_id=chat_id,
                )
            except (KeyError, ValueError, TypeError):
                continue  # skip a malformed claim, keep the rest
            if outcome["duplicate"]:
                continue
            if outcome["superseded"] is not None:
                total_updated += 1
                log.info(
                    "belief updated: %s.%s: %r -> %r",
                    c["subject"], c["predicate"],
                    outcome["superseded"]["value"], c["value"],
                )
            else:
                total_new += 1
        store.mark_processed(event_ids)

    store.log_event(
        "system", "consolidation",
        json.dumps({"events": total_events, "new": total_new,
                    "updated": total_updated}),
    )
    return {"events": total_events, "new": total_new, "updated": total_updated}


_SWEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id_a": {"type": "integer"},
                    "id_b": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["id_a", "id_b", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["conflicts"],
    "additionalProperties": False,
}

_SWEEP_SYSTEM = """\
You are the belief-hygiene module of a personal AI agent. You receive the
agent's active beliefs about ONE subject, each with a numeric id. Identify
pairs that CANNOT both be true at the same time right now (e.g. "works in
Berlin" vs "commutes to the Paris office daily", "is vegetarian" vs "favorite
food is steak"). Slot updates are already handled elsewhere — only report
IMPLICIT contradictions across different predicates. Be conservative: two
beliefs that merely look unrelated or redundant are NOT a conflict. Return an
empty list when in doubt."""


def sweep_contradictions(store: Store, max_subjects: int = 6) -> list:
    """S4 stage 2: sample subjects that hold several active claims and ask the
    model to spot implicit contradictions structure alone misses. Confirmed
    pairs are marked disputed (flag + confidence haircut) — surfaced as
    DISPUTED whenever retrieved, never silently believed. Returns the list of
    conflicts found."""
    claims = [c for c in store.active_claims(limit=200)
              if not hygiene.is_poisoned(c["predicate"], c["value"])
              and c["kind"] in ("fact", "preference")
              and not c["disputed"]]
    by_subject = {}
    for c in claims:
        by_subject.setdefault(c["subject"], []).append(c)
    crowded = sorted(
        (group for group in by_subject.values() if len(group) >= 2),
        key=len, reverse=True,
    )[:max_subjects]

    found = []
    for group in crowded:
        valid_ids = {c["id"] for c in group}
        beliefs = "\n".join(
            f"- [id {c['id']}] {c['subject']} | {c['predicate']} | {c['value']} "
            f"(since {c['valid_from'][:10]})"
            for c in group
        )
        try:
            result = llm.extract(_SWEEP_SYSTEM, f"BELIEFS:\n{beliefs}",
                                 _SWEEP_SCHEMA, max_tokens=1500)
        except Exception:
            log.exception("contradiction sweep failed for subject %r — skipping",
                          group[0]["subject"])
            continue
        for con in result.get("conflicts", []):
            a, b = con.get("id_a"), con.get("id_b")
            if a not in valid_ids or b not in valid_ids or a == b:
                continue  # hallucinated ids — ignore
            store.mark_disputed([a, b])
            found.append({"id_a": a, "id_b": b,
                          "reason": str(con.get("reason", ""))[:300]})
    if found:
        store.log_event("system", "contradiction_sweep",
                        json.dumps({"conflicts": found}))
    return found


def reflect(store: Store, chat_id: str = None) -> list:
    """One reflection pass. Returns the list of new insight claims (as dicts)."""
    # Poisoned rows are excluded so reflection can't launder a bad belief into
    # a fresh "insight" (they're also quarantined by memory maintenance).
    claims = [c for c in store.active_claims(limit=120)
              if not hygiene.is_poisoned(c["predicate"], c["value"])]
    if len(claims) < 5:
        return []

    beliefs = "\n".join(
        f"- {c['subject']} | {c['predicate']} | {c['value']} "
        f"[{c['kind']}, conf {c['confidence']:.2f}, {c['valid_from'][:10]}]"
        for c in claims
    )
    result = llm.extract(_INSIGHT_SYSTEM, f"CURRENT BELIEFS:\n{beliefs}", _INSIGHT_SCHEMA)

    created = []
    for ins in result.get("insights", []):
        if _is_volatile_fs_claim(ins.get("predicate", ""), ins.get("value", "")):
            continue  # don't derive insights from / about filesystem state
        outcome = store.add_claim(
            subject=ins["subject"].strip().lower(),
            predicate=ins["predicate"].strip().lower(),
            value=ins["value"].strip(),
            kind="insight",
            confidence=min(0.6, max(0.0, float(ins["confidence"]))),
            source_event_ids=[],
            chat_id=chat_id,
        )
        if not outcome["duplicate"]:
            created.append(ins)

    if created:
        store.log_event("system", "reflection", json.dumps({"insights": len(created)}))
    return created
