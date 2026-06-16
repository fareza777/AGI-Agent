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



    re.I)



_FILE_FMT_RE = re.compile(r"\.(docx|pptx|xlsx|pdf|csv|md|html)\b", re.I)



_CONFIRM_RE = re.compile(



    r"^(ya|yes|oke|ok|lanjut|silakan|gas|betul|setuju|mau|iyaa?)[\s!.?]*$", re.I)



_FILE_TASK_RE = re.compile(



    r"(buat|bikin|kirim|laporan|docx|word|ppt|pptx|justify|revisi|file|dokumen)",



    re.I)



_WHERE_FILE_RE = re.compile(



    r"^(mana|dimana|where)\??$|belum (masuk|terlihat|ada)|gak ada|kok belum",



    re.I)



_STALL_REPLY_RE = re.compile(



    r"(model hanya mengembalikan reasoning|mau lanjut dengan format|"



    r"sebelum aku coba lagi|server engram|silent-fail|7x beruntun)",



    re.I)







_EXECUTION_NUDGE = (



    "\n\n[INSTRUKSI SISTEM: Wajib eksekusi tool di giliran ini — write_file, "



    "create_document(source_path=...) untuk docx/pptx, send_file. "



    "Dilarang hanya menjelaskan, minta izin lagi, atau mengutip kegagalan lama.]"



)











# Pre-tool gate: when the user asks about a folder/drive/listing, we



# inject a real list_dir result into the context BEFORE the LLM reasons.



# This is hard code, not a prompt instruction — the model cannot talk



# its way around it. The alternative (telling the model in the system



# prompt to call list_dir) was ignored; the model kept answering from



# training data and inventing folder names.



_FS_QUERY_RE = re.compile(



    r"(isi\s+(folder|drive|path|directory|root)|"



    r"apa\s+(aja|yang|isi)\s+(isi|yang|ada)\s+(di|dalam)\s+|"



    r"folder\s+(apa|apa\s+aja|yang\s+ada)\s+(di|dalam)|"



    r"tunjukkan\s+(isi|folder|file)|"



    r"cek\s+(drive|folder|isi|d:|g:|c:)|"



    r"list\s+(drive|folder|isi|d:|g:)|"



    r"scan\s+(drive|folder|d:|g:)|"



    r"d:\s*\\?\??|g:\s*\\?\??|c:\s*\\?\??|"



    r"isi\s+drive|"



    r"what\'s\s+in\s+|"



    r"ada\s+(folder|file|apa)\s+(apa|aja)?\s*(di|dalam)?\s*[dg])",



    re.I)



# Strip the leading "di " that the user almost always uses ("di D:").



_FS_DRIVE_RE = re.compile(r"\b([dgc]):\\?", re.I)

# Also catch natural phrasing: "drive D", "di D", "partisi D".

_FS_NATURAL_DRIVE_RE = re.compile(

    r"\b(?:drive|partisi|di)\s+([dgc])\b", re.I)







def _fs_preflight(user_text: str) -> str | None:



    """If user_text looks like a file-system query, return a `list_dir`



    result for the implied path; otherwise None. The result is a plain



    string ready to inject as a system note for the LLM.



    The drive letter is optional: 'ada apa di drive D' -> D:/; 'cek



    folder X' -> ./X or workspace/X."""



    if not _FS_QUERY_RE.search(user_text):



        return None



    from . import desktop



    m = _FS_DRIVE_RE.search(user_text) or _FS_NATURAL_DRIVE_RE.search(user_text)



    if m:



        path = f"{m.group(1).upper()}:/"



    else:



        # No drive letter: use the workspace. User almost always means the



        # current drive they're on, but we don't know which. Refuse to



        # guess — ask the LLM to ask the user.



        return (



            "FS-PREFLIGHT: no drive letter in the query. Ask the user to "



            "specify the path (e.g. 'D:/', 'G:/My Drive/...').")



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



        f"If the user wants recursion, call search_files or list_dir on "



        f"specific subfolders, never guess:\n\n{result}")







def _inject_execution_nudge(user_text: str, store: Store, chat_id: str) -> str:



    """Push the model to call tools when the user wants files or confirmed a plan."""



    tail = store.recent_events(chat_id, config.CONVERSATION_TAIL)



    prev_assistant = next(



        (e["content"] for e in reversed(tail) if e["actor"] == "agent"), "")



    is_confirm = bool(_CONFIRM_RE.match(user_text.strip()))



    proposed = bool(re.search(



        r"(write_file|send_file|create_document|\.md|\.docx|\.pptx|alternatif)",



        prev_assistant, re.I))



    if is_confirm and proposed:



        return user_text + _EXECUTION_NUDGE



    if _FILE_TASK_RE.search(user_text) or _WHERE_FILE_RE.search(user_text.strip()):



        return user_text + _EXECUTION_NUDGE



    return user_text











def _should_retry_for_tools(reply: str, user_text: str, ctx: ToolContext) -> bool:



    if ctx.file_tools_called or ctx.produced_files:



        return False



    if not _FILE_TASK_RE.search(user_text) and not _CONFIRM_RE.match(user_text.strip()):



        return False



    return bool(_STALL_REPLY_RE.search(reply)) or reply.startswith("(")











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
        prev, re.I):
        return True
    return False











def _guard_file_claims(reply: str, ctx: ToolContext,



                      user_text: str = "", store: Store = None, chat_id: str = "") -> str:



    """Append a correction when the model claims files were sent but none were



    queued — only on turns where the user expected a deliverable."""



    if ctx.produced_files:



        return reply



    if store is None or not _user_expects_files(user_text, store, chat_id):



        return reply



    if ctx.file_tools_called:



        return (f"{reply}\n\n"



                "⚠️ Catatan sistem: tool dokumen dipanggil tapi tidak ada file "



                "yang ter-queue. Pembuatan/pengiriman mungkin gagal — coba lagi "



                "atau minta format lain.")



    claims_sent = bool(_FILE_SENT_RE.search(reply))



    # Strong "I just sent it now" phrasing — not a past-tense status recap.



    claims_now = bool(re.search(



        r"(cek\s+telegram|baru\s+(saja\s+)?(di)?kirim|"



        r"\d+\s+file\s+sudah\s+dikirim|sudah\s+jalan.*kirim|"



        r"log internal|status success di log)",



        reply, re.I))



    if not claims_sent and not claims_now:



        return reply



    return (f"{reply}\n\n"



            "⚠️ Catatan sistem: tidak ada file yang dibuat/dikirim pada giliran "



            "ini (create_document/send_file tidak dipanggil). File belum sampai "



            "ke chat — minta saya buat/kirim ulang; kali ini saya wajib pakai tool.")











class Agent:



    def __init__(self, store: Store = None):



        self.store = store or Store()



        self._stop = threading.Event()



        self._bg_thread = None



        self._scheduler_thread = None



        # Set by the interface (e.g. TelegramBot):



        #   notifier(chat_id, text)        — reminders & scheduled-task results



        #   file_notifier(chat_id, path)   — files produced by scheduled tasks



        #   activity_notifier(chat_id, text) — live "🔎 web_search: ..." feed



        self.notifier = None



        self.file_notifier = None



        self.activity_notifier = None







    # ---------------- one chat turn ----------------







    def handle_message(self, chat_id: str, user_text: str, images: list = None):



        """Run one turn. Returns (reply_text, [produced_file_paths]).



        images: paths of images attached to this turn (vision)."""



        self.store.log_event("user", "message", user_text, chat_id)



        nudged = _inject_execution_nudge(user_text, self.store, chat_id)



        system, messages = composer.build_context(self.store, chat_id, nudged)



        # FS preflight: inject real list_dir output for drive/folder queries



        # so the model has ground truth and can't invent folder names.



        fs_note = _fs_preflight(nudged)



        if fs_note:



            messages.append({"role": "user", "content": fs_note})



            log.info("fs-preflight fired for: %s", user_text[:80])



        if images:



            messages[-1] = {"role": "user", "content": nudged, "images": images}



        activity = None



        if self.activity_notifier is not None and config.SHOW_ACTIVITY:



            activity = lambda text: self.activity_notifier(chat_id, text)  # noqa: E731



        file_cb = None



        if self.file_notifier is not None:



            file_cb = lambda p: self.file_notifier(chat_id, p)  # noqa: E731



        ctx = ToolContext(self.store, chat_id, activity=activity, file_notifier=file_cb)



        try:



            reply = llm.chat(system, messages, ctx=ctx)



            if _should_retry_for_tools(reply, user_text, ctx):



                log.info("stall detected on file task — retrying with execution nudge")



                messages.append({"role": "assistant", "content": reply})



                messages.append({"role": "user", "content": _EXECUTION_NUDGE.strip()})



                reply = llm.chat(system, messages, ctx=ctx)



        except Exception as exc:



            log.exception("chat model call failed")



            reply = f"⚠️ Gagal: {llm.describe_error(exc)}."



            if ctx.produced_files:



                reply += ("\nFile yang sempat dibuat tetap saya kirim di bawah.")



        reply = _guard_file_claims(reply, ctx, user_text, self.store, chat_id)



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



        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)



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



                self.store.log_event("agent", "message",



                                     f"(reminder delivered) {r['message']}", r["chat_id"])



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



                f"hasilnya] {task['prompt']}")



            self.notifier(task["chat_id"], f"🤖 Tugas terjadwal #{task['id']}:\n\n{reply}")



            if self.file_notifier:



                failed = []



                for path in files:



                    if not self.file_notifier(task["chat_id"], path):



                        failed.append(os.path.basename(path))



                if failed:



                    self.notifier(



                        task["chat_id"],



                        "⚠️ Gagal kirim file: " + ", ".join(failed))



        except Exception:



            log.exception("scheduled task #%s failed; will retry next tick", task["id"])



            return



        self.store.complete_task_run(



            task["id"], store_mod.next_occurrence(task["due_ts"], task["recurrence"]))







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







    def _safe_consolidate(self):



        try:



            stats = consolidator.consolidate(self.store)



            if stats["events"]:



                log.info("consolidated %s", stats)



        except Exception:



            log.exception("consolidation failed")







    def _safe_reflect(self):



        try:



            insights = consolidator.reflect(self.store)



            if insights:



                log.info("generated %d insight(s)", len(insights))



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



                        f"Lihat: /skill {draft['name']} · Aktifkan: /approve {draft['name']}")



        except Exception:



            log.exception("skill mining failed")



