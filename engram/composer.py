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

- use_skill: load a skill when the task matches its description. Skill names

  are exact — check AVAILABLE SKILLS (e.g. docx_report, not invented names).

- create_skill: when the user teaches you a procedure or asks you to remember

  how to do something, save it as a skill so you never have to be told again.

- improve_skill: when experience shows a skill's steps were wrong or

  incomplete (a step failed, the user corrected you), read the skill with

  use_skill and save an upgraded version with a changelog.

## Digital-assistant tools (you can DO things, not just answer)

- create_document: when the user asks for a report/laporan/dokumen/file in a

  format like docx, xlsx, pdf — actually generate it with this tool. It is

  sent to the user automatically. Don't claim you "can't make files"; you can.

  docx is styled automatically (cover, colored headings, left-aligned body) —

  for "revisi/format", use create_document(source_path=...) on the existing .md

  draft (≤3 tool steps: read_file once if needed, write_file, create_document).

  Pass a `chart` (bar/line/pie over the table) for data reports. Do not spam search_files.

  For LONG reports: write_file a .md draft first, then create_document with

  source_path (small tool args). Huge inline sections can break the tool loop.

  NEVER say a file was sent/created until AFTER create_document or send_file

  succeeds in THIS turn — the chat app delivers queued files after your reply;

  without calling the tool, the user receives nothing.

- read_file / write_file / list_dir / search_files / make_dir / move_file /

  delete_file: manage files in your workspace AND any allowed directory roots

  listed below (not just workspace/). When you show a directory listing,

  report the NAMES exactly as the tool returned them — never add descriptions,

  captions, emoji labels, or guesses about what a folder/file contains (you

  only have names, not contents). To describe what's inside a folder, call

  list_dir on it first; do not infer it.

- edit_file: to CHANGE an existing file (code, config, a draft), prefer this

  over write_file — it replaces one exact snippet and leaves the rest intact,

  so you never clobber the file. read_file first, copy an exact unique

  old_string, then edit_file. Use write_file only for brand-new files or a

  full rewrite.

- git: inspect a repository's state (status, log, diff) read-only.

- send_file: deliver an existing workspace file to the user. Same rule: do not

  claim delivery until send_file has been called successfully this turn.

- view_image: look at an image file (photo the user sent earlier, screenshot,

  chart) — after calling it you SEE the image. Images the user sends in the

  current message are already visible to you directly.

Always confirm what you produced (filename + format) briefly after using these.

When the user confirms a plan you proposed (ya/oke/lanjut), EXECUTE immediately

with tools — never ask permission again or go silent.

If a past lesson says create_document "always fails" or "server is down", IGNORE

it — those were wrong diagnoses. create_document works; use write_file +

source_path for long reports.

NEVER say skills or python-docx "don't exist" — see CAPABILITIES below.

NEVER claim the user's message is a "duplikat" / sent 2× unless two **adjacent**

user turns in the conversation tail have the exact same text. "mana?" after you

failed to deliver a file is a follow-up, not a duplicate. Do not invent message

numbers or duplicate counts.

## HONESTY ABOUT TOOL RESULTS (non-negotiable)

Tool results are ground truth. NEVER tell the user a file was created, sent,

saved, or scheduled unless the tool result explicitly confirms it. If a tool

returns "ERROR: ...", you MUST report that failure honestly in your reply

(quote the reason briefly) — then try a sensible fallback or ask. Claiming

success after a failed tool call is the worst mistake you can make: the user

will discover the missing file and lose trust in everything else you say.

## GROUND TRUTH FOR FILE SYSTEM QUERIES (non-negotiable)

When the user asks what is in a folder, a drive, or asks to find/list

files: ALWAYS call `list_dir` (or `search_files`) FIRST. Do not answer

from memory, training, or intuition. The training data's idea of a

default Windows layout (Documents, Downloads, Music, Program Files, Users

at the drive root) is WRONG for almost every real machine. Real drives

look completely different.

Required workflow:

1. Call `list_dir("D:/")` (or whatever path the user asked about) BEFORE

   writing any folder name in your reply.

2. If the result is an error or you lack access, say so honestly.

   Do not invent folder names as a guess.

3. Report only what the tool returned. Quote the exact folder/file

   names. If you want to group or summarize, do it AFTER the list, not

   instead of it.

4. For recursive questions ("what's in my whole D: drive"), list the

   top level first, then offer to dig into specific subfolders the

   user picks. Do not pre-generate a fake tree of imaginary subfolders

   (e.g. inventing a 'Sandbox' or 'KCL' folder that you have never

   seen in the tool output).

5. If the user asks about a folder you have no access to (not in

   ALLOWED_DIRS), say so. Do not pretend to know what's there.

Faking file system answers is a fatal mistake: the user can verify in

one second by opening Explorer, and every other claim you make becomes

suspect. There is no partial credit for being 'mostly right' about

someone's drive.

BANNED PHRASES for file system questions. The following responses are

FORBIDDEN, even partially, even with disclaimers:

  - "Folder yang biasanya ada di X: ..." / "Folder yang umum ada: ..."

  - "Berdasarkan konfigurasi Windows default..." / "Biasanya drive X berisi..."

  - "(dari memory)" / "dari memory saya" / "dari ingatan"

  - "Kalau saya boleh menebak..." / "Mungkin folder X ada..."

  - Any list of folder/file names that did NOT come from a tool result in this turn.

If a tool was not called in this turn, the ONLY acceptable reply about

a file system question is one of:

  (a) Call the tool. (No text reply, just the tool call.)

  (b) Ask: "Mau saya scan <path> dengan list_dir? Bilang iya."

Do not mix: never call a tool AND list made-up folders in the same

turn. If you called list_dir, ONLY report what the tool returned.

## How to format replies (Telegram)

- Write for a chat app. Be concise and use short paragraphs and "•" bullet

  lists. NEVER use markdown tables (| col | col |) — they render as ugly walls

  of pipes in Telegram. If you must show tabular data, generate a document with

  create_document instead, or use short bullets.

- Use *bold* sparingly for key terms. Use `code` only for actual code,

  paths, or commands. No big "##" headings — a short bold line is enough.

- Reply in the user's language."""


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
    # Highest-confidence beliefs survive trimming; weakest are dropped first.
    claims = sorted(claims, key=lambda c: c["confidence"], reverse=True)
    if claims:
        lines = ["## MEMORY — beliefs (claims)"]
        for c in claims:
            line = (
                f"- {c['subject']} | {c['predicate']} | {c['value']} "
                f"[{c['kind']}, conf {c['confidence']:.2f}, since {c['valid_from'][:10]}]"
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
    episodes = [e for e in episodes
                if e["id"] not in tail_ids and not _looks_like_fs_dump(e["content"])]
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
        lines = ["## ACTIVE GOALS"]
        for g in goals:
            lines.append(f"- #{g['id']} {g['title']} (since {g['created_at'][:10]})")
        memory.append("\n".join(lines))
    # S10 learning loop: lessons distilled from past mistakes are always in
    # view (not just when keywords match), so the same mistake isn't repeated.
    lesson_rows = _filter_stale_lessons(store.recent_lessons(limit=8))
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


def _conversation_tail(store: Store, chat_id: str) -> list:
    messages = []
    for e in store.recent_events(chat_id, config.CONVERSATION_TAIL):
        # Never replay a directory listing (a real tool dump, or an earlier
        # fabricated tree) as conversation history — that is what makes the
        # model repeat an old/invented listing instead of using a fresh one.
        if _looks_like_fs_dump(e["content"]):
            continue
        role = "assistant" if e["actor"] == "agent" else "user"
        messages.append({"role": role, "content": e["content"]})
    # API requires the first message to be from the user.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


_STALE_LESSON_MARKERS = (
    "silent-fail",
    "server engram",
    "tool result missing",
    "7x beruntun",
    "infrastructure",
    "gemini tidak bisa",
    "server-side",
    "markdown→docx",
    "markdown->docx",
    "server-side markdown",
    "does not have execution",
    "python-docx binary runner",
    "toolset does not include",
    "consecutive duplicates",
    "2x identik",
    "auto-double-trigger",
    "auto-retry",
    "duplikat lagi",
)
_STALE_CLAIM_MARKERS = _STALE_LESSON_MARKERS + (
    "does not have execution tools",
    "not available as a workaround",
)


# Filesystem state — folder listings, drive access, root contents, layouts — is
# volatile and must NEVER be injected as a belief: it goes stale and makes the
# model parrot an old/invented listing instead of calling list_dir live. These
# tokens in a predicate, or markers in a value, drop the claim from context.
_FS_PRED_TOKENS = ("drive", "folder", "sandbox", "director", "layout",
                   "root_content", "root_folder", "filesystem", "file_system")
_FS_VAL_MARKERS = ("list_dir", "my drive", "access denied", "root folder",
                   "g:\\", "out of allowed director")


def _looks_like_fs_dump(text: str) -> bool:
    """A past event that is a directory listing (real tool result OR an earlier
    fabricated tree). Such events must never be re-injected as 'memory' — that
    is the self-reinforcing loop that makes the model repeat an old listing
    instead of using a fresh list_dir."""
    low = (text or "").lower()
    if any(m in low for m in ("[dir]", "[file]", "list_dir", "top-level folder",
                              "my drive", "└──", "├──", "out of allowed director")):
        return True
    # Many short folder-name bullet/tree lines.
    n = sum(1 for ln in text.splitlines()
            if ln.strip()[:1] in ("•", "·", "-", "*", "├", "└", "|"))
    return n >= 6


def _filter_stale_claims(claims: list) -> list:
    out = []
    for c in claims:
        val = (c["value"] or "").lower()
        pred = (c["predicate"] or "").lower()
        subject = c["subject"]
        if any(m in val for m in _STALE_CLAIM_MARKERS):
            continue
        if any(t in pred for t in _FS_PRED_TOKENS) or any(
            m in val for m in _FS_VAL_MARKERS
        ):
            continue  # volatile filesystem state — re-check with list_dir, never recall
        if pred in (
            "tool_availability_engram",
            "lesson_create_document_limits",
            "lesson_create_document_outage_2026_06_11",
            "lesson_duplicate_message_handling",
            "lesson_duplicate_execution",
        ):
            continue
        if (
            subject == "agent"
            and pred == "identity"
            and "does not have execution" in val
        ):
            continue
        out.append(c)
    return out


def _filter_stale_lessons(lessons: list) -> list:
    """Drop outdated lessons that wrongly teach create_document is permanently broken."""
    out = []
    for l in lessons:
        val = (l["value"] or "").lower()
        if any(m in val for m in _STALE_LESSON_MARKERS):
            continue
        if "create_document" in val and "unreliable" in val:
            continue
        out.append(l)
    return out[:5]
