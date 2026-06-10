"""S8 Skill Compiler — learn procedures from experience.

Two learning paths:

1. In-conversation (tools.py): when the user teaches a procedure, the model
   calls create_skill (active immediately — explicit teaching); when
   experience shows a skill is wrong or incomplete, the model calls
   improve_skill (version bump + archived history).

2. Background mining (this module): mine() scans recent episodes — messages
   AND tool calls, which the event log records — for procedures the agent has
   repeated or fumbled, and drafts a skill. Drafts are NOT loaded into context
   until the user approves them (/approve), per the staged rollout in
   DESIGN.md: propose first, auto-activate never.
"""

import json
import logging

from . import llm, skills
from .store import Store

log = logging.getLogger("engram.skill_compiler")

_MINE_SCHEMA = {
    "type": "object",
    "properties": {
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "body": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["name", "description", "body", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["skills"],
    "additionalProperties": False,
}

_MINE_SYSTEM = """\
You are the skill-mining module of a personal AI agent. You receive recent
episodes (user messages, agent replies, and tool calls with results) plus the
list of skills the agent already has.

Propose a new skill ONLY if the episodes show a clear, repeatable procedure:
- the same kind of multi-step task was performed two or more times, or
- the agent fumbled a task in a way a written procedure would fix, or
- the user expressed a recurring routine worth automating.

A skill is a markdown procedure the agent will load when a matching task
appears. name must be snake_case. description is ONE line stating when to use
it. body is numbered steps referencing the agent's tools by name (recall,
remember, manage_goal, schedule_reminder, web_search, fetch_url, calculate).
Do NOT duplicate or trivially overlap an existing skill. Quality bar is high:
return an empty list unless the evidence is clear. Never more than one skill."""


def mine(store: Store, max_events: int = 80) -> list:
    """One mining pass. Returns [{'name','description','rationale','chat_id'}]
    for drafts created (empty most of the time — the bar is deliberately high).
    """
    with store._lock:
        rows = store._conn.execute(
            "SELECT * FROM events WHERE kind IN ('message','tool') "
            "ORDER BY id DESC LIMIT ?", (max_events,)).fetchall()
    rows = list(reversed(rows))
    if len(rows) < 10:
        return []

    transcript = "\n".join(
        f"[{r['ts'][:16]} | {r['actor']} | {r['kind']}] {r['content'][:400]}"
        for r in rows)
    existing = "\n".join(f"- {n}: {d}" for n, d in skills.index()) or "(none)"
    chat_id = next((r["chat_id"] for r in reversed(rows) if r["chat_id"]), None)

    result = llm.extract(
        _MINE_SYSTEM,
        f"EXISTING SKILLS:\n{existing}\n\nRECENT EPISODES:\n{transcript}",
        _MINE_SCHEMA)

    created = []
    for s in result.get("skills", [])[:1]:
        name = skills.sanitize(s["name"])
        if not name or skills.get(name) is not None:
            continue
        skills.save(name, s["description"], s["body"], status="draft")
        store.log_event("system", "skill_draft",
                        json.dumps({"name": name, "rationale": s["rationale"]},
                                   ensure_ascii=False), chat_id)
        log.info("drafted skill '%s': %s", name, s["rationale"])
        created.append({"name": name, "description": s["description"],
                        "rationale": s["rationale"], "chat_id": chat_id})
    return created
