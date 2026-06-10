"""S6 Working-Memory Composer — assemble a budgeted context window per turn.

Order of assembly (stable prefix first, for prompt caching):
  1. Identity Core (verbatim, always)
  2. Standing instructions
  3. Retrieved memory: active claims matching the user's message (BM25),
     each rendered with kind, confidence and date so the reasoner can weigh them
  4. Older episode excerpts matching the message
  5. Active goals for this chat
  6. Conversation tail (recent messages, as proper user/assistant turns)
"""

from datetime import datetime, timezone

from . import config, identity, skills
from .store import Store

_INSTRUCTIONS = """\
## How to use your memory

The MEMORY section below was retrieved from your permanent store for this turn.
Each item shows [kind, confidence, date]. Higher confidence and newer dates are
more trustworthy. "lesson" items are things you learned from past mistakes —
follow them. "insight" items are patterns you inferred — treat as hypotheses.
If memory contradicts the user, the user's current statement wins (your
consolidation engine will record the change). Never invent memories that are
not shown here.

## How to use your tools

- recall: if the user references something not in MEMORY, search before saying
  you don't remember.
- remember: when the user states something clearly important (job change, new
  commitment, correction of your belief), store it immediately.
- web_search / fetch_url: use for anything recent or outside your knowledge —
  don't guess.
- schedule_reminder: when the user asks to be reminded, schedule it (compute
  the UTC time from the current time below) and confirm the exact time back.
- use_skill: load a skill when the task matches its description."""


def build_context(store: Store, chat_id: str, user_text: str) -> tuple:
    """Returns (system_prompt, messages) ready for the chat model."""
    parts = [identity.load(), _INSTRUCTIONS]

    claims = store.search_claims(user_text, limit=config.MAX_RETRIEVED_CLAIMS)
    if claims:
        lines = ["## MEMORY — beliefs (claims)"]
        for c in claims:
            lines.append(
                f"- {c['subject']} | {c['predicate']} | {c['value']} "
                f"[{c['kind']}, conf {c['confidence']:.2f}, since {c['valid_from'][:10]}]"
            )
        parts.append("\n".join(lines))

    episodes = store.search_events(user_text, limit=config.MAX_RETRIEVED_EPISODES)
    tail_ids = {e["id"] for e in store.recent_events(chat_id, config.CONVERSATION_TAIL)}
    episodes = [e for e in episodes if e["id"] not in tail_ids]
    if episodes:
        lines = ["## MEMORY — past episode excerpts"]
        for e in episodes:
            snippet = e["content"][:400].replace("\n", " ")
            lines.append(f"- [{e['ts'][:10]}, {e['actor']}] {snippet}")
        parts.append("\n".join(lines))

    goals = store.goals(chat_id, "active")
    if goals:
        lines = ["## ACTIVE GOALS"]
        for g in goals:
            lines.append(f"- #{g['id']} {g['title']} (since {g['created_at'][:10]})")
        parts.append("\n".join(lines))

    skill_index = skills.index()
    if skill_index:
        lines = ["## AVAILABLE SKILLS (load with the use_skill tool)"]
        for name, description in skill_index:
            lines.append(f"- {name}: {description}")
        parts.append("\n".join(lines))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC (%A)")
    parts.append(f"Current time: {now}")

    system = "\n\n".join(parts)
    messages = _conversation_tail(store, chat_id)
    messages.append({"role": "user", "content": user_text})
    return system, messages


def _conversation_tail(store: Store, chat_id: str) -> list:
    messages = []
    for e in store.recent_events(chat_id, config.CONVERSATION_TAIL):
        role = "assistant" if e["actor"] == "agent" else "user"
        messages.append({"role": role, "content": e["content"]})
    # API requires the first message to be from the user.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages
