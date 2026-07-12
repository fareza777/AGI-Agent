"""Engram orchestrator: ties the substrate together for one chat turn,

and runs the background "sleep cycle" thread.

"""

import logging
import os
import re
import threading
import time
from . import composer, config, consolidator, llm, skill_compiler
from . import store as store_mod
from .store import Store
from .tools import ToolContext

log = logging.getLogger("engram.agent")
# Reply patterns that imply a file was delivered without checking produced_files.
_FILE_SENT_RE = re.compile(
    r"(sudah\s+(di)?kirim|file\s+sudah|cek\s+telegram|"
    r"telah\s+dikirim|already\s+sent|sent\s+(the|your|\d+|two|2)\s+file|"
    r"\d+\s+file\s+sudah)",
    re.I,
)
_FILE_FMT_RE = re.compile(r"\.(docx|pptx|xlsx|pdf|csv|md|html)\b", re.I)
_CONFIRM_RE = re.compile(
    r"^(ya|yes|oke|ok|lanjut|silakan|gas|betul|setuju|mau|iyaa?)[\s!.?]*$", re.I
)
_FILE_TASK_RE = re.compile(
    r"(buat|bikin|kirim|laporan|docx|word|ppt|pptx|xlsx|excel|pdf|justify|"
    r"revisi|file|dokumen|tambah|tambahin|sisip|diagram|tabel|grafik|chart|"
    r"slide|ubah|ganti|perbaiki|lengkapi|update|generate|versi)",
    re.I,
)
_WHERE_FILE_RE = re.compile(
    r"^(mana|dimana|where)\b|"
    r"(mana|dimana|where)\s+(hasil|file|dokumen|laporan|docx|pdf|ppt)|"
    r"belum (masuk|terlihat|ada|sampai|muncul|kelihatan)|"
    r"(gak|nggak|ga|tidak)\s+(ada|muncul|sampai|masuk)|kok belum",
    re.I,
)
# The model narrated future file work — "lalu create_document", "aku tulis
# ulang .md draft", "Plan: ... generate versi 2" — but emitted no tool call.
# This is the dominant stall: it describes the work instead of doing it.
_PROMISE_RE = re.compile(
    r"(create_document|write_file|edit_file|send_file|"
    r"(tulis|menulis)\s+ulang|rewrite|"
    r"generate\s+(versi|ulang|dokumen|file|baru)|"
    # "langsung render ke .docx", "tinggal render", "render ke pdf", "sudah ada draf"
    r"\brender\w*\b|(sudah|udah|tinggal)\s+(ada\s+)?(draf|draft|render)|"
    r"(langsung|tinggal)\s+(render|buat|bikin|generate|kirim|export|susun)|"
    r"(akan|aku|saya|nanti|lalu|kemudian|terus)\b[^.\n]{0,60}"
    r"(create_document|write_file|generate|render|tulis ulang|buat\s+(file|dokumen|docx|laporan))|"
    r"^\s*(plan|rencana)\s*:)",
    re.I | re.M,
)
_STALL_REPLY_RE = re.compile(
    r"(model hanya mengembalikan reasoning|mau lanjut dengan format|"
    r"sebelum aku coba lagi|server engram|silent-fail|7x beruntun)",
    re.I,
)
_EXECUTION_NUDGE = (
    "\n\n## EXECUTION (giliran ini)\n"
    "Kalau giliran ini meminta membuat atau mengirim file/laporan/dokumen, kamu "
    "WAJIB benar-benar memanggil tool-nya di giliran ini — jangan hanya "
    "menjelaskan rencana, minta izin lagi, atau mengaku draft sudah ada. Kalau "
    "merender dari source_path, write_file dulu draft .md-nya di giliran ini "
    "(jangan anggap sudah ada), baru create_document(source_path=...). Pakai "
    "send_file untuk mengirim file yang sudah ada."
)
# Pre-tool gate: when the user asks about a folder/drive/listing, we
# inject a real list_dir result into the context BEFORE the LLM reasons.
# This is hard code, not a prompt instruction — the model cannot talk
# its way around it. The alternative (telling the model in the system
# prompt to call list_dir) was ignored; the model kept answering from
# training data and inventing folder names.
_FS_QUERY_RE = re.compile(
    r"(isi\s+(folder|drive|path|directory|direktori|root|dari)|"
    r"apa\s+(aja|yang|isi)\s+(isi|yang|ada)\s+(di|dalam)\s+|"
    r"folder\s+(apa|apa\s+aja|yang\s+ada)|"
    r"(tunjukkan|tunjuk\w*|tampil\w*|lihat|buka|akses|explore|jelajahi|scan|list|cek|"
    r"baca)\s+(isi\s+)?(folder|drive|direktori|file|my\s*drive|[dgc]:)|"
    r"my\s*drive|"
    r"\b[dgc]:[\\/]|"
    r"(drive|partisi)\s+[dgc]\b|"
    r"what'?s\s+in\s+|"
    r"ada\s+(folder|file|apa)\s+(apa|aja)?\s*(di|dalam)?\s*[dgc])",
    re.I,
)
# Strip the leading "di " that the user almost always uses ("di D:").
_FS_DRIVE_RE = re.compile(r"\b([dgc]):\\?", re.I)
# Also catch natural phrasing: "drive D", "di D", "partisi D".
_FS_NATURAL_DRIVE_RE = re.compile(r"\b(?:drive|partisi|di)\s+([dgc])\b", re.I)


def _path_from_text(text: str):
    """Resolve a drive/path mentioned in a message, or None."""
    if re.search(r"my\s*drive", text, re.I):
        return "G:/My Drive"
    m = _FS_DRIVE_RE.search(text) or _FS_NATURAL_DRIVE_RE.search(text)
    if m:
        letter = m.group(1).upper()
        return "G:/My Drive" if letter == "G" else f"{letter}:/"
    return None


def _recent_fs_path(store, chat_id) -> str | None:
    """The most recently mentioned drive/path in the conversation, so a vague
    follow-up ('mana isi drivenya', 'coba cek') still targets the right place."""
    if store is None or chat_id is None:
        return None
    for e in reversed(store.recent_events(chat_id, 8)):  # newest first
        path = _path_from_text(e["content"] or "")
        if path:
            return path
    return None


def _fs_preflight(user_text: str, store=None, chat_id=None) -> str | None:
    """If user_text looks like a file-system query, return a `list_dir`
    result for the implied path; otherwise None. The result is a plain string
    ready to inject as a system note for the LLM. The path is taken from the
    message, or — for a vague follow-up — from the recent conversation."""
    if not _FS_QUERY_RE.search(user_text):
        return None
    from . import desktop

    path = _path_from_text(user_text) or _recent_fs_path(store, chat_id)
    if not path:
        # No resolvable path (the FS regex also matches phrases like "baca
        # file X" that aren't listing queries). Injecting an "ask for the
        # path" note here derailed ordinary file requests — stay silent and
        # let the system-prompt rules (list_dir-first, ask if unclear) apply.
        return None
    try:
        result = desktop.list_dir(path)
    except Exception as exc:
        return f"FS-PREFLIGHT: list_dir({path}) failed: {exc}"
    # Truncate to a safe size so we don't blow the context.
    if len(result) > 4000:
        result = result[:4000] + "\n[...truncated; use list_dir tool for more]"
    return (
        f"FS-PREFLIGHT: list_dir({path}) returned this VERBATIM list. "
        f"Treat it as ground truth — do not invent folder/file names. "
        f"List the NAMES only. Do NOT add descriptions, captions, emoji "
        f"labels, or any guess about what a folder contains — you have only "
        f"the names, not the contents. To know what is inside a folder, call "
        f"list_dir on that folder; never infer or annotate. "
        f"If the user wants recursion, call list_dir / search_files on "
        f"specific subfolders, never guess:\n\n{result}"
    )


def _execution_directive(user_text: str, store: Store, chat_id: str) -> str:
    """Return a SYSTEM-prompt addendum (or "") that pushes the model to call
    tools when the user wants files or confirmed a plan. It goes in the system
    prompt, NOT appended to the user message — embedding instructions in user
    content makes injection-aware models refuse it (and the user's real request)
    as a prompt-injection attack."""
    tail = store.recent_events(chat_id, config.CONVERSATION_TAIL)
    prev_assistant = next(
        (e["content"] for e in reversed(tail) if e["actor"] == "agent"), ""
    )
    is_confirm = bool(_CONFIRM_RE.match(user_text.strip()))
    proposed = bool(
        re.search(
            r"(write_file|edit_file|send_file|create_document|\.md|\.docx|\.pptx|"
            r"diagram|tabel|grafik|versi 2|alternatif|render)",
            prev_assistant,
            re.I,
        )
    )
    # After the agent proposed a document/edit, ANY follow-up (a confirmation,
    # an instruction like "tambah diagram dan tabel", or "mana hasilnya") means
    # execute now — not just bare "ya".
    if (proposed and (is_confirm or _FILE_TASK_RE.search(user_text)
                      or _WHERE_FILE_RE.search(user_text.strip()))) or \
       _FILE_TASK_RE.search(user_text) or _WHERE_FILE_RE.search(user_text.strip()):
        return _EXECUTION_NUDGE
    return ""


def _should_retry_for_tools(reply: str, user_text: str, ctx: ToolContext) -> bool:
    """Retry the turn (with a hard execution nudge) when the model produced a
    file-shaped answer but called no file tool. The strongest signal is the
    reply itself promising tool work ("lalu create_document", "tulis ulang .md",
    "Plan: ... generate versi 2") while produced_files is still empty — that is
    exactly the 'announce but never act' stall, regardless of how the user
    phrased the request."""
    if ctx.file_tools_called or ctx.produced_files:
        return False
    # A genuine clarifying question ("docx atau pdf?") is not a stall — let the
    # user answer rather than forcing a tool call.
    if reply.strip().endswith("?"):
        return False
    if _STALL_REPLY_RE.search(reply) or reply.startswith("("):
        return True
    # Narrated future file work but emitted no tool call this turn.
    return bool(_PROMISE_RE.search(reply))


def _user_expects_files(user_text: str, store: Store, chat_id: str) -> bool:
    """True when this turn should produce/deliver files — not casual chat."""
    text = (user_text or "").strip()
    if _FILE_TASK_RE.search(text):
        return True
    if _WHERE_FILE_RE.search(text):
        return True
    if not _CONFIRM_RE.match(text):
        return False
    tail = store.recent_events(chat_id, 4)
    prev = next((e["content"] for e in reversed(tail) if e["actor"] == "agent"), "")
    # Only count it as a follow-up file intent if the previous assistant
    # message was an actual PROPOSAL to send a file, not a list of files
    # that happen to be on disk ("ada 6 folder + 7 file di D:...").
    # Proposal language: "saya akan kirim", "ini file X.docx", ".docx"
    # as a standalone token, "kirim/buat ... file/dokumen".
    if re.search(
        r"(saya akan (kirim|generate|buat)|ini (file |lampiran )?\w+\.(docx|pptx|pdf|xlsx)|"
        r"(kirim|generate|buat).{0,30}(file|dokumen|laporan))",
        prev,
        re.I,
    ):
        return True
    return False


def _guard_file_claims(
    reply: str,
    ctx: ToolContext,
    user_text: str = "",
    store: Store = None,
    chat_id: str = "",
) -> str:
    """Append a correction when the model claims files were sent but none were

    queued — only on turns where the user expected a deliverable."""
    if ctx.produced_files:
        return reply
    if store is None:
        return reply
    if not _user_expects_files(user_text, store, chat_id):
        return reply
    # Hard guard: never append the 'file belum sampai' catatan on turns where
    # the user's text itself has no file/deliverable intent. The 'expected'
    # signal came from a previous follow-up; the agent replying to a
    # conversation/clarification turn is not a delivery failure. If the
    # user only said 'cek drive D' (no file/deliverable word) AND the
    # current text isn't a bare 'iya' after a file proposal, skip.
    user_has_file_intent = bool(_FILE_TASK_RE.search(user_text or ""))
    is_confirm_only = bool(_CONFIRM_RE.match((user_text or "").strip()))
    if not user_has_file_intent and not is_confirm_only:
        return reply
    if ctx.file_tools_called:
        return (
            f"{reply}\n\n"
            "⚠️ Catatan sistem: tool dokumen dipanggil tapi tidak ada file "
            "yang ter-queue. Pembuatan/pengiriman mungkin gagal — coba lagi "
            "atau minta format lain."
        )
    claims_sent = bool(_FILE_SENT_RE.search(reply))
    # Strong "I just sent it now" phrasing — not a past-tense status recap.
    claims_now = bool(
        re.search(
            r"(cek\s+telegram|baru\s+(saja\s+)?(di)?kirim|"
            r"\d+\s+file\s+sudah\s+dikirim|sudah\s+jalan.*kirim|"
            r"log internal|status success di log)",
            reply,
            re.I,
        )
    )
    if not claims_sent and not claims_now:
        return reply
    return (
        f"{reply}\n\n"
        "⚠️ Catatan sistem: tidak ada file yang dibuat/dikirim pada giliran "
        "ini (create_document/send_file tidak dipanggil). File belum sampai "
        "ke chat — minta saya buat/kirim ulang; kali ini saya wajib pakai tool."
    )


_URL_RE = re.compile(r"https?://([a-z0-9.\-]+)[^\s)>\]]*", re.I)


# A reply that presents a directory listing / claims a live FS fetch.
_FS_LISTING_RE = re.compile(
    r"(top-level folders?|isi (dari )?(folder|drive|direktori)|"
    r"daftar (isi |)folder|berhasil di-?fetch|di-?fetch sekarang|"
    r"folder utama|data mentah|list_dir|📂|📁|├──|└──|"
    r"\d+\s*folder\b)",
    re.I,
)
# A bullet / tree / path line that names a directory entry.
_FS_ENTRY_RE = re.compile(r"^\s*(?:[•·\-\*]|[├└]──|\|──|📁|📂|📄)\s*(.+?)\s*$")
_FS_FORCE_NUDGE = (
    "Daftar folder yang kamu tampilkan tidak cocok dengan hasil tool list_dir "
    "(atau kamu belum memanggilnya). Tolong panggil tool list_dir pada path "
    "yang dimaksud (mis. 'G:/My Drive') sekarang, lalu salin PERSIS nama yang "
    "dikembalikan tool — jangan menambah, mengubah, atau mengarang nama. Kalau "
    "path-nya belum jelas, tanyakan dulu ke aku."
)


def _fs_named_entries(reply: str) -> list:
    """Folder/file names the reply presents as directory entries."""
    out = []
    for ln in reply.splitlines():
        m = _FS_ENTRY_RE.match(ln)
        if not m:
            continue
        name = m.group(1).strip().strip("/\\").strip()
        # Drop any trailing annotation after a dash / em-dash / parenthesis.
        name = re.split(r"\s+[—–\-]\s|\s+\(", name, 1)[0].strip()
        low = name.lower()
        if 2 <= len(name) <= 50 and low not in ("file", "folder", "dir", "free"):
            out.append(name)
    return out


def _fs_fabrication(reply: str, ctx: ToolContext) -> bool:
    """True when the reply presents a directory listing whose entries are
    invented: either no list_dir/search_files ran this turn, or the named
    entries don't actually appear in the tool output (model ignored the real
    result — common with weaker tool-calling models)."""
    # Only judge when we actually have a real listing to compare against
    # (a list_dir tool call this turn, or the FS-preflight injection). With no
    # ground truth we cannot tell a real recall from a fabrication — and a
    # false "I didn't really read it" apology is worse than letting it through.
    if getattr(ctx, "fs_calls", 0) == 0:
        return False
    names = _fs_named_entries(reply)
    looks_like_listing = bool(_FS_LISTING_RE.search(reply)) or len(names) >= 6
    if not looks_like_listing or len(names) < 4:
        return False
    tool_text = "\n".join(getattr(ctx, "tool_output", [])).lower()
    grounded = sum(1 for nm in names if nm.lower() in tool_text)
    # Most named entries must come from the real tool output.
    return grounded < max(2, len(names) // 2)


def _guard_fs_claims(reply: str, ctx: ToolContext) -> str:
    """Replace a fabricated directory listing (no list_dir ran) with an honest
    correction — never let an invented file tree reach the user as 'live'."""
    if not _fs_fabrication(reply, ctx):
        return reply
    return (
        "⚠️ Maaf — saya tidak benar-benar membaca folder itu (tidak ada "
        "pemanggilan list_dir pada giliran ini), jadi daftar apa pun yang "
        "sempat saya susun TIDAK valid dan saya batalkan. Beri tahu path "
        "persisnya (mis. `G:/My Drive`) dan saya akan list_dir sungguhan lalu "
        "tampilkan isinya apa adanya."
    )


def _guard_unsourced_links(reply: str, ctx: ToolContext) -> str:
    """Grounding guard: flag URLs in the reply whose domain never appeared in
    any tool result this turn — the classic 'invented a plausible-looking
    source' hallucination. We don't delete them (could be legitimately recalled
    from memory), but we warn the user the links weren't verified this turn."""
    # NB: str.lstrip("www.") strips *characters*, not the prefix — it mangles
    # domains like weather.com into "eather.com" and produces false
    # "unverified link" warnings. Use removeprefix.
    domains = {m.group(1).lower().removeprefix("www.")
               for m in _URL_RE.finditer(reply)}
    if not domains:
        return reply
    tool_text = "\n".join(ctx.tool_output).lower()
    unsourced = sorted(d for d in domains if d not in tool_text)
    if not unsourced:
        return reply
    listed = ", ".join(unsourced[:5])
    return (
        f"{reply}\n\n"
        f"⚠️ Catatan sistem: link berikut tidak berasal dari hasil tool pada "
        f"giliran ini dan belum terverifikasi — {listed}. Saya bisa buka dengan "
        f"fetch_url untuk memastikan sebelum Anda mempercayainya."
    )


class Agent:
    def __init__(self, store: Store = None):
        self.store = store or Store()
        self._stop = threading.Event()
        self._bg_thread = None
        self._scheduler_thread = None
        # Per-chat turn lock: a scheduled task and a live user message for the
        # same chat must not run concurrently, or they interleave tool state and
        # double-spend the model. Different chats still run in parallel.
        self._chat_locks = {}
        self._chat_locks_guard = threading.Lock()
        # Set by the interface (e.g. TelegramBot):
        #   notifier(chat_id, text)        — reminders & scheduled-task results
        #   file_notifier(chat_id, path)   — files produced by scheduled tasks
        #   activity_notifier(chat_id, text) — live "🔎 web_search: ..." feed
        self.notifier = None
        self.file_notifier = None
        self.activity_notifier = None

    def _lock_for(self, chat_id: str) -> threading.Lock:
        with self._chat_locks_guard:
            lock = self._chat_locks.get(chat_id)
            if lock is None:
                lock = threading.Lock()
                self._chat_locks[chat_id] = lock
            return lock

    # ---------------- one chat turn ----------------
    def handle_message(self, chat_id: str, user_text: str, images: list = None,
                       on_delta=None):
        """Run one turn, serialized per chat. Returns (reply, [file_paths]).

        on_delta(text): optional live-streaming callback for the reply."""
        with self._lock_for(chat_id):
            return self._handle_message(chat_id, user_text, images, on_delta)

    def _handle_message(self, chat_id: str, user_text: str, images: list = None,
                        on_delta=None):
        """Run one turn. Returns (reply_text, [produced_file_paths]).

        images: paths of images attached to this turn (vision)."""
        self.store.log_event("user", "message", user_text, chat_id)
        system, messages = composer.build_context(self.store, chat_id, user_text)
        # Execution directive goes in the SYSTEM prompt (a trusted instruction),
        # never appended to the user message — embedding "instructions" in user
        # content makes injection-aware models reject it AND the real request.
        system += _execution_directive(user_text, self.store, chat_id)
        # FS preflight: inject real list_dir output for drive/folder queries
        # so the model has ground truth and can't invent folder names.
        fs_note = _fs_preflight(user_text, self.store, chat_id)
        if fs_note:
            messages.append({"role": "user", "content": fs_note})
            log.info("fs-preflight fired for: %s", user_text[:80])
        if images:
            messages[-1] = {"role": "user", "content": user_text, "images": images}
        activity = None
        if self.activity_notifier is not None and config.SHOW_ACTIVITY:
            activity = lambda text: self.activity_notifier(chat_id, text)  # noqa: E731
        file_cb = None
        if self.file_notifier is not None:
            file_cb = lambda p: self.file_notifier(chat_id, p)  # noqa: E731
        ctx = ToolContext(self.store, chat_id, activity=activity, file_notifier=file_cb)
        if fs_note and "FS-PREFLIGHT: list_dir" in fs_note:
            # The preflight ran a REAL list_dir and injected it — count it as a
            # filesystem fetch and grounding source so the output guard treats
            # the model echoing it as grounded, not a fabrication.
            ctx.fs_calls += 1
            ctx.tool_output.append(fs_note)
        try:
            reply = llm.chat(system, messages, ctx=ctx, on_delta=on_delta)
            # The model sometimes narrates file work without calling a tool.
            # Re-run with a hard execution nudge — up to twice, escalating —
            # so a "Plan: ... generate versi 2" answer becomes an actual file
            # instead of leaving the user stuck asking "mana hasilnya".
            for attempt in range(2):
                if not _should_retry_for_tools(reply, user_text, ctx):
                    break
                log.info("stall detected on file task — forced retry %d", attempt + 1)
                messages.append({"role": "assistant", "content": reply})
                escalation = _EXECUTION_NUDGE.strip()
                if attempt == 1:
                    escalation = (
                        "Kamu sudah dua kali cuma menjelaskan tanpa hasil. "
                        "Sekarang langsung kerjakan: write_file untuk membuat "
                        "draft .md-nya dulu (jangan anggap sudah ada), lalu "
                        "create_document(source_path=...). Tolong panggil "
                        "tool-nya, jangan balas teks saja. Kalau kontennya besar, "
                        "potong jadi beberapa write_file append lalu satu "
                        "create_document."
                    )
                messages.append({"role": "user", "content": escalation})
                # tool_choice-level forcing: the retry MUST start with a tool
                # call — a repeat text-only answer is exactly the stall.
                reply = llm.chat(system, messages, ctx=ctx, force_tools=True)
            # The model presented a directory listing without ever calling
            # list_dir — force it to actually fetch instead of inventing.
            for attempt in range(2):
                if not _fs_fabrication(reply, ctx):
                    break
                log.info("fs fabrication detected — forcing list_dir, attempt %d",
                         attempt + 1)
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": _FS_FORCE_NUDGE})
                reply = llm.chat(system, messages, ctx=ctx, force_tools=True)
        except Exception as exc:
            log.exception("chat model call failed")
            reply = f"⚠️ Gagal: {llm.describe_error(exc)}."
            if ctx.produced_files:
                reply += "\nFile yang sempat dibuat tetap saya kirim di bawah."
        reply = _guard_fs_claims(reply, ctx)
        reply = _guard_file_claims(reply, ctx, user_text, self.store, chat_id)
        reply = _guard_unsourced_links(reply, ctx)
        self.store.log_event("agent", "message", reply, chat_id)
        # Inline trigger: consolidate when enough raw experience has piled up,
        # so memory stays fresh even between background passes.
        if self.store.unprocessed_count() >= config.CONSOLIDATE_EVERY_N_EVENTS:
            threading.Thread(target=self._safe_consolidate, daemon=True).start()
        return reply, [p for p in ctx.produced_files if p not in ctx.delivered_files]

    # ---------------- background sleep cycle ----------------
    def start_background(self):
        self._bg_thread = threading.Thread(target=self._loop, daemon=True)
        self._bg_thread.start()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop, daemon=True
        )
        self._scheduler_thread.start()

    def stop(self):
        self._stop.set()

    def _scheduler_loop(self):
        """Deliver due reminders and execute due agent tasks.

        Failures leave the item pending/active so it retries next tick."""
        while not self._stop.wait(config.REMINDER_POLL_SEC):
            if self.notifier is None:
                continue
            for r in self.store.due_reminders():
                try:
                    self.notifier(r["chat_id"], f"⏰ Pengingat: {r['message']}")
                except Exception:
                    log.exception("failed to deliver reminder #%s", r["id"])
                    continue
                self.store.set_reminder_status(r["id"], "sent")
                self.store.log_event(
                    "agent",
                    "message",
                    f"(reminder delivered) {r['message']}",
                    r["chat_id"],
                )
            for t in self.store.due_tasks():
                self._run_task(t)

    def _run_task(self, task):
        """Execute one scheduled task as a full agent turn (tools included) and

        deliver the result. Proactive autonomy: the agent works unprompted."""
        log.info("running scheduled task #%s: %s", task["id"], task["prompt"][:80])
        try:
            reply, files = self.handle_message(
                task["chat_id"],
                f"[TUGAS TERJADWAL #{task['id']} — jalankan sekarang dan laporkan "
                f"hasilnya] {task['prompt']}",
            )
            self.notifier(
                task["chat_id"], f"🤖 Tugas terjadwal #{task['id']}:\n\n{reply}"
            )
            if self.file_notifier:
                failed = []
                for path in files:
                    if not self.file_notifier(task["chat_id"], path):
                        failed.append(os.path.basename(path))
                if failed:
                    self.notifier(
                        task["chat_id"], "⚠️ Gagal kirim file: " + ", ".join(failed)
                    )
        except Exception:
            log.exception("scheduled task #%s failed; will retry next tick", task["id"])
            return
        self.store.complete_task_run(
            task["id"], store_mod.next_occurrence(task["due_ts"], task["recurrence"])
        )

    def _loop(self):
        interval = config.CONSOLIDATE_INTERVAL_MIN * 60
        cycles = 0
        while not self._stop.wait(interval):
            self._safe_consolidate()
            cycles += 1
            if cycles % 4 == 0:  # reflect roughly every 4th consolidation pass
                self._safe_reflect()
            if cycles % 6 == 0:  # mine for new skills less often — high bar
                self._safe_mine()
            if cycles % 8 == 0:  # memory hygiene: decay/prune/backfill embeddings
                self._safe_maintain()
            if cycles % 12 == 0:  # implicit-contradiction sweep (S4 stage 2)
                self._safe_sweep()

    def _safe_consolidate(self):
        try:
            stats = consolidator.consolidate(self.store)
            if stats["events"]:
                log.info("consolidated %s", stats)
        except Exception:
            log.exception("consolidation failed")

    def _safe_maintain(self):
        try:
            stats = self.store.maintain_memory()
            if any(stats.values()):
                log.info("memory maintenance %s", stats)
        except Exception:
            log.exception("memory maintenance failed")

    def _safe_sweep(self):
        try:
            conflicts = consolidator.sweep_contradictions(self.store)
            if conflicts:
                log.info("contradiction sweep flagged %d pair(s)", len(conflicts))
        except Exception:
            log.exception("contradiction sweep failed")

    def _safe_reflect(self):
        try:
            # Reflect PER chat so insights stay scoped to one user's beliefs and
            # are stored against their chat_id — never mixed across tenants.
            total = 0
            for chat_id in self.store.active_chat_ids():
                total += len(consolidator.reflect(self.store, chat_id=chat_id))
            if total:
                log.info("generated %d insight(s)", total)
        except Exception:
            log.exception("reflection failed")

    def _safe_mine(self):
        try:
            for draft in skill_compiler.mine(self.store):
                if self.notifier and draft["chat_id"]:
                    self.notifier(
                        draft["chat_id"],
                        f"💡 Saya menyusun draft skill baru dari pengalaman kita: "
                        f"*{draft['name']}* — {draft['description']}\n"
                        f"Alasan: {draft['rationale']}\n"
                        f"Lihat: /skill {draft['name']} · Aktifkan: /approve {draft['name']}",
                    )
        except Exception:
            log.exception("skill mining failed")
