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

from . import llm
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
- kind "lesson" is for things the agent should do differently next time. Watch
  specifically for: the user correcting the agent, a tool call that failed and
  how it was fixed, output the user disliked (format, length, language). Write
  the lesson as an actionable rule, e.g. "when making documents for the user,
  use Indonesian unless asked otherwise" (subject "agent", predicate like
  lesson_document_language).
- Return an empty list if nothing is worth remembering."""

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
    """One consolidation pass. Returns counts: {'events':n,'new':n,'updated':n}."""
    events = store.unprocessed_events()
    if not events:
        return {"events": 0, "new": 0, "updated": 0}

    transcript = "\n".join(
        f"[event {e['id']} | {e['ts']} | {e['actor']}] {e['content']}" for e in events
    )
    result = llm.extract(_EXTRACT_SYSTEM, transcript, _EXTRACT_SCHEMA)

    event_ids = [e["id"] for e in events]
    chat_id = events[-1]["chat_id"]
    new = updated = 0
    for c in result.get("claims", []):
        outcome = store.add_claim(
            subject=c["subject"].strip().lower(),
            predicate=c["predicate"].strip().lower(),
            value=c["value"].strip(),
            kind=c["kind"],
            confidence=max(0.0, min(1.0, float(c["confidence"]))),
            source_event_ids=event_ids,
            chat_id=chat_id,
        )
        if outcome["duplicate"]:
            continue
        if outcome["superseded"] is not None:
            updated += 1
            log.info(
                "belief updated: %s.%s: %r -> %r",
                c["subject"], c["predicate"], outcome["superseded"]["value"], c["value"],
            )
        else:
            new += 1

    store.mark_processed(event_ids)
    store.log_event(
        "system", "consolidation",
        json.dumps({"events": len(events), "new": new, "updated": updated}),
    )
    return {"events": len(events), "new": new, "updated": updated}


def reflect(store: Store, chat_id: str = None) -> list:
    """One reflection pass. Returns the list of new insight claims (as dicts)."""
    claims = store.active_claims(limit=120)
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
