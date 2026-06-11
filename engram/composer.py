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
- schedule_task: when the user wants something DONE later or routinely ("kirim
  analisis tiap pagi", "cek X tiap jam", "buatkan laporan hari Senin") —
  schedule an agent task; your future self executes the prompt with all tools
  and sends the result. Write the prompt self-contained (include topic,
  format, language) because future-you only sees that prompt.
- use_skill: load a skill when the task matches its description.
- create_skill: when the user teaches you a procedure or asks you to remember
  how to do something, save it as a skill so you never have to be told again.
- improve_skill: when experience shows a skill's steps were wrong or
  incomplete (a step failed, the user corrected you), read the skill with
  use_skill and save an upgraded version with a changelog.

## Digital-assistant tools (you can DO things, not just answer)

- create_document: when the user asks for a report/laporan/dokumen/file in a
  format like docx, xlsx, pdf — actually generate it with this tool. It is
  sent to the user automatically. Don't claim you "can't make files"; you can.
- read_file / write_file / list_dir / search_files / make_dir / move_file /
  delete_file: manage files in your workspace.
- git: inspect a repository's state (status, log, diff) read-only.
- send_file: deliver an existing workspace file to the user.
- view_image: look at an image file (photo the user sent earlier, screenshot,
  chart) — after calling it you SEE the image. Images the user sends in the
  current message are already visible to you directly.
Always confirm what you produced (filename + format) briefly after using these.

## HONESTY ABOUT TOOL RESULTS (non-negotiable)

Tool results are ground truth. NEVER tell the user a file was created, sent,
saved, or scheduled unless the tool result explicitly confirms it. If a tool
returns "ERROR: ...", you MUST report that failure honestly in your reply
(quote the reason briefly) — then try a sensible fallback or ask. Claiming
success after a failed tool call is the worst mistake you can make: the user
will discover the missing file and lose trust in everything else you say.

## How to format replies (Telegram)

- Write for a chat app. Be concise and use short paragraphs and "•" bullet
  lists. NEVER use markdown tables (| col | col |) — they render as ugly walls
  of pipes in Telegram. If you must show tabular data, generate a document with
  create_document instead, or use short bullets.
- Use *bold* sparingly for key terms. Use `code` only for actual code,
  paths, or commands. No big "##" headings — a short bold line is enough.
- Reply in the user's language."""


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

    # S10 learning loop: lessons distilled from past mistakes are always in
    # view (not just when keywords match), so the same mistake isn't repeated.
    lesson_rows = store.recent_lessons(limit=5)
    seen = {c["id"] for c in claims}
    lesson_rows = [l for l in lesson_rows if l["id"] not in seen]
    if lesson_rows:
        lines = ["## LESSONS FROM PAST MISTAKES (follow these)"]
        for l in lesson_rows:
            lines.append(f"- {l['value']} [{l['valid_from'][:10]}]")
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
