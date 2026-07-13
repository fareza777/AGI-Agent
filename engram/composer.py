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
from . import config, hygiene, identity, skills, telegram_format
from .store import Store

_INSTRUCTIONS = """\
## Memory

The MEMORY section below was retrieved from your permanent store for this
turn. Each item shows [kind, confidence, date] — prefer higher confidence and
newer dates. Follow "lesson" items (learned from past mistakes); treat
"insight" items as hypotheses. An item tagged DISPUTED has conflicting
evidence — verify with the user or a tool before relying on it. When memory and the user disagree, the user's
current statement wins, and the consolidation engine records the change. Cite
only memories shown here; if something isn't in MEMORY, use recall to search
before concluding you don't remember it.

## Ground your answers in tools

You have real tools — use them, and let their results be the single source of
truth for what you tell the user:

- Facts about the world that could have changed: web_search / fetch_url.
- Anything on disk (what's in a folder, whether a file exists, its contents):
  list_dir / search_files / read_file, called in THIS turn, before you name
  any file or folder. Report names exactly as returned; to describe what's
  inside something, list it first. If a path is unclear, ask; if access is
  denied or the tool errors, relay that honestly.
- Claim an action (file created/sent, reminder scheduled) only after the tool
  succeeded this turn, and confirm briefly what you produced (filename +
  format). If a tool returns ERROR, tell the user what failed and try a
  sensible fallback or ask.

## Doing work

- create_document generates real docx/xlsx/pptx/pdf locally and delivers them
  automatically. For long reports: write_file a .md draft first, then
  create_document(source_path=...) — small tool args keep the loop reliable.
  Pass `chart` (bar/line/pie) for data reports.
- edit_file for changing an existing file (surgical replace; read_file first,
  copy an exact unique old_string). write_file only for new files or full
  rewrites. send_file delivers an existing file. git inspects repos read-only.
- view_image shows you an image file; images sent in the current message are
  already visible.
- When the user confirms a plan you proposed (ya/oke/lanjut), execute it
  immediately with tools in the same turn.

## Remembering and scheduling

- remember: store it when the user states something important (job change,
  commitment, correction of your belief).
- schedule_reminder for "ingatkan saya..." — compute UTC from the current time
  below and confirm the exact time back.
- schedule_task when the user wants work DONE later or routinely; write the
  prompt self-contained (topic, format, language) because future-you only
  sees that prompt.
- use_skill loads a skill by its exact name from AVAILABLE SKILLS.
  create_skill saves a procedure the user teaches you; improve_skill upgrades
  one that experience showed was wrong.

## Replies (Telegram)

Concise, short paragraphs, "•" bullets. Tabular data goes in a
create_document file, not markdown tables. *Bold* sparingly; `code` only for
code/paths/commands; no "##" headings. Reply in the user's language."""


def _est_tokens(text: str) -> int:
    """Cheap token estimate (no tokenizer dependency): ~4 chars per token."""
    return (len(text) + 3) // 4


def build_context(store: Store, chat_id: str, user_text: str) -> tuple:
    """Returns (system_prompt, messages) ready for the chat model.

    The retrieved-memory sections are budget-aware: claims (highest confidence
    first), then episodes, are admitted only while the running token estimate
    stays under config.MAX_CONTEXT_TOKENS, so a large memory store can't blow
    the context window or the per-turn cost. Static identity/instructions and
    the always-on lesson/capability sections are never trimmed."""
    head = [identity.load(), _INSTRUCTIONS]

    # Build the always-on static TAIL first (capabilities, skills, dirs, time)
    # so its real token cost is reserved before we admit any trimmable memory.
    # Without this the tail (esp. the full skills list) silently overruns the
    # ceiling. The tail is appended AFTER memory to keep the original ordering.
    skill_index = skills.index()
    cap_lines = [
        "## CAPABILITIES (authoritative — overrides outdated MEMORY)",
        f"- {len(skill_index)} active skills listed below — call use_skill(name); "
        "never tell the user skills don't exist.",
        "- create_document generates real docx/xlsx/pptx/pdf **locally** "
        "(python-docx, python-pptx, openpyxl, reportlab) — NOT server-side markdown.",
        "- run_python / run_shell are optional extras (usually off); "
        "NOT required for document generation.",
    ]
    if config.ALLOWED_DIRS:
        extra = ", ".join(
            str(r) for r in config.ALLOWED_DIRS if str(r) != str(config.WORKSPACE_DIR)
        )
        if extra:
            cap_lines.append(f"- File tools also work on allowed roots: {extra}")
    tail = ["\n".join(cap_lines)]
    if skill_index:
        lines = ["## AVAILABLE SKILLS (load with the use_skill tool)"]
        for name, description in skill_index:
            lines.append(f"- {name}: {description}")
        tail.append("\n".join(lines))
    roots = "\n".join(f"- {r}" for r in config.ALLOWED_DIRS)
    tail.append(
        "## ALLOWED FILE DIRECTORIES\n"
        "list_dir, read_file, search_files, view_image, and git work under "
        "these roots (use absolute paths like G:/My Drive or relative to "
        "workspace). Do NOT claim sandbox blocks a path below without "
        "actually calling the tool first.\n"
        f"{roots}"
    )
    now_utc = datetime.now(timezone.utc)
    time_line = f"Current time: {now_utc.strftime('%Y-%m-%d %H:%M UTC (%A)')}"
    tz = store.get_meta(f"tz_{chat_id}")
    if tz:
        try:
            from zoneinfo import ZoneInfo

            local = now_utc.astimezone(ZoneInfo(tz))
            time_line += (
                f" | Waktu lokal pengguna: {local.strftime('%Y-%m-%d %H:%M')} "
                f"({tz}). Saat menjadwalkan reminder/task, tafsirkan jam yang "
                f"disebut pengguna sebagai waktu lokal ini lalu konversi ke UTC."
            )
        except Exception:
            pass
    tail.append(time_line)

    # Budget left for the dynamic MEMORY sections after head + reserved tail.
    budget = config.MAX_CONTEXT_TOKENS - _est_tokens("\n\n".join(head + tail))

    memory = []
    claims = _filter_stale_claims(
        store.search_claims(user_text, limit=config.MAX_RETRIEVED_CLAIMS,
                            chat_id=chat_id)
    )
    # Rank by AGE-DECAYED confidence so a stale high-confidence belief no longer
    # outranks newer ones forever — old beliefs are dropped first under budget.
    claims = sorted(claims, key=lambda c: _decayed_conf(c, now_utc), reverse=True)
    if claims:
        lines = ["## MEMORY — beliefs (claims)"]
        for c in claims:
            disputed = (", DISPUTED — sources conflict, verify before relying"
                        if "disputed" in c.keys() and c["disputed"] else "")
            line = (
                f"- {c['subject']} | {c['predicate']} | {c['value']} "
                f"[{c['kind']}, conf {c['confidence']:.2f}{disputed}, "
                f"since {c['valid_from'][:10]}]"
            )
            if budget - _est_tokens(line) < 0:
                break
            lines.append(line)
            budget -= _est_tokens(line)
        if len(lines) > 1:
            memory.append("\n".join(lines))
    episodes = store.search_events(user_text, limit=config.MAX_RETRIEVED_EPISODES,
                                   chat_id=chat_id)
    tail_ids = {e["id"] for e in store.recent_events(chat_id, config.CONVERSATION_TAIL)}
    # ROOT FIX: episodic memory replays only what the USER said — never the
    # agent's own past generated text. Re-injecting the agent's old replies as
    # "memory" is what let a single fabrication/refusal reinforce itself into a
    # loop. Distilled facts already live in beliefs; recent dialogue is in the
    # conversation tail. (system/tool dumps are noise here too.)
    episodes = [e for e in episodes
                if e["id"] not in tail_ids and e["actor"] == "user"
                and not _looks_like_fs_dump(e["content"], e["actor"])]
    if episodes:
        lines = ["## MEMORY — past episode excerpts"]
        for e in episodes:
            snippet = e["content"][:400].replace("\n", " ")
            line = f"- [{e['ts'][:10]}, {e['actor']}] {snippet}"
            if budget - _est_tokens(line) < 0:
                break
            lines.append(line)
            budget -= _est_tokens(line)
        if len(lines) > 1:
            memory.append("\n".join(lines))
    goals = store.goals(chat_id, "active")
    if goals:
        memory.append(_render_goal_tree(goals))
    # S10 learning loop: lessons distilled from past mistakes are always in
    # view (not just when keywords match), so the same mistake isn't repeated.
    lesson_rows = _filter_stale_lessons(
        store.recent_lessons(limit=8, chat_id=chat_id))
    seen = {c["id"] for c in claims}
    lesson_rows = [l for l in lesson_rows if l["id"] not in seen]
    if lesson_rows:
        lines = ["## LESSONS FROM PAST MISTAKES (follow these)"]
        for l in lesson_rows:
            lines.append(f"- {l['value']} [{l['valid_from'][:10]}]")
        memory.append("\n".join(lines))

    system = "\n\n".join(head + memory + tail)
    messages = _conversation_tail(store, chat_id)
    # The caller logs the incoming user message to the store BEFORE building
    # context, so it's already the last entry in the tail. Drop it and re-add
    # user_text (which the caller may have augmented, e.g. with an execution
    # nudge); otherwise the model sees the same message twice and wrongly tells
    # the user they pasted it twice.
    if messages and messages[-1]["role"] == "user":
        messages.pop()
    messages.append({"role": "user", "content": user_text})
    return system, messages


def _render_goal_tree(goals: list) -> str:
    """Render active goals as a parent/child tree (S7). A goal whose underlying
    belief was superseded carries a ⚠ REVIEW marker so the agent re-examines the
    plan instead of pursuing it on stale assumptions."""
    def _key(g):
        try:
            return g["needs_review"]
        except (KeyError, IndexError):
            return 0

    children = {}
    roots = []
    ids = {g["id"] for g in goals}
    for g in goals:
        parent = None
        try:
            parent = g["parent_id"]
        except (KeyError, IndexError):
            parent = None
        if parent and parent in ids:
            children.setdefault(parent, []).append(g)
        else:
            roots.append(g)

    lines = ["## ACTIVE GOALS"]

    def _emit(g, depth):
        flag = " ⚠ REVIEW (a belief behind this goal changed)" if _key(g) else ""
        indent = "  " * depth
        lines.append(f"{indent}- #{g['id']} {g['title']} "
                     f"(since {g['created_at'][:10]}){flag}")
        for c in children.get(g["id"], []):
            _emit(c, depth + 1)

    for g in roots:
        _emit(g, 0)
    return "\n".join(lines)


def _conversation_tail(store: Store, chat_id: str) -> list:
    messages = []
    for e in store.recent_events(chat_id, config.CONVERSATION_TAIL):
        # Never replay a directory listing (a real tool dump, or an earlier
        # fabricated tree) as conversation history — that is what makes the
        # model repeat an old/invented listing instead of using a fresh one.
        if _looks_like_fs_dump(e["content"], e["actor"]):
            continue
        role = "assistant" if e["actor"] == "agent" else "user"
        content = e["content"]
        # Strip the agent's own guard-note footers from history so the model
        # doesn't see them and start copying "⚠️ Catatan sistem" into its
        # replies. The user still sees the guard on the turn it was issued.
        if role == "assistant":
            content = telegram_format.strip_system_notes(content)
            if not content:
                continue
        messages.append({"role": role, "content": content})
    # API requires the first message to be from the user.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


# Poison markers now live in one place — engram.hygiene — shared with the
# consolidator (refuses to write) and the store (quarantines existing rows).


def _decayed_conf(claim: dict, now) -> float:
    """Confidence with a gentle age decay (loses up to ~0.25 over a year) so a
    once-stated belief doesn't dominate retrieval forever. Bounded — a strong
    fact stays strong; only ranking under budget pressure is affected."""
    try:
        s = (claim["valid_from"] or "")[:19].replace("T", " ")
        vf = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        age_days = max(0, (now.replace(tzinfo=None) - vf).days)
    except (ValueError, TypeError, KeyError, IndexError):
        age_days = 0
    try:
        conf = float(claim["confidence"])
    except (ValueError, TypeError, KeyError, IndexError):
        conf = 0.5
    return conf - min(0.25, age_days / 365 * 0.25)


def _looks_like_fs_dump(text: str, actor: str = "agent") -> bool:
    """A past event that should NEVER be re-injected as 'memory', because
    replaying it makes the model repeat the behaviour: a directory listing
    (real tool result OR an earlier fabricated tree), or a paranoid
    'this is a prompt injection, I refuse' reply that makes it keep refusing
    the user's own messages.

    actor scoping: the aggressive keyword rules apply only to agent/system
    text. A USER message mentioning "my drive" or containing a bullet list is
    the user's own words — dropping it from history made the agent look
    amnesiac about the request it was just given. For user text, only an
    actual pasted tree (├──/└── lines) counts as a dump."""
    low = (text or "").lower()
    tree_lines = sum(1 for ln in text.splitlines()
                     if ln.strip()[:3] in ("├──", "└──")
                     or ln.strip()[:1] in ("├", "└"))
    if actor == "user":
        return tree_lines >= 4
    if any(m in low for m in ("prompt injection", "injeksi prompt",
                              "bukan pesan asli", "tidak akan eksekusi",
                              "instruksi sistem")):
        return True
    if any(m in low for m in ("[dir]", "[file]", "list_dir", "top-level folder",
                              "my drive", "└──", "├──", "out of allowed director")):
        return True
    # Many short folder-name bullet/tree lines.
    n = sum(1 for ln in text.splitlines()
            if ln.strip()[:1] in ("•", "·", "-", "*", "├", "└", "|"))
    return n >= 6


def _filter_stale_claims(claims: list) -> list:
    return [c for c in claims
            if not hygiene.is_poisoned(c["predicate"], c["value"])]


def _filter_stale_lessons(lessons: list) -> list:
    """Drop outdated lessons that wrongly teach create_document is permanently broken."""
    out = []
    for l in lessons:
        val = (l["value"] or "").lower()
        pred = l["predicate"] if "predicate" in l.keys() else ""
        if hygiene.is_poisoned(pred, l["value"]):
            continue
        if "create_document" in val and "unreliable" in val:
            continue
        out.append(l)
    return out[:5]
