"""Engram orchestrator: ties the substrate together for one chat turn,
and runs the background "sleep cycle" thread.
"""

import logging
import threading
import time

from . import composer, config, consolidator, llm, skill_compiler
from . import store as store_mod
from .store import Store
from .tools import ToolContext

log = logging.getLogger("engram.agent")


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
        system, messages = composer.build_context(self.store, chat_id, user_text)
        if images:
            messages[-1] = {"role": "user", "content": user_text, "images": images}
        activity = None
        if self.activity_notifier is not None and config.SHOW_ACTIVITY:
            activity = lambda text: self.activity_notifier(chat_id, text)  # noqa: E731
        ctx = ToolContext(self.store, chat_id, activity=activity)
        try:
            reply = llm.chat(system, messages, ctx=ctx)
        except Exception as exc:
            log.exception("chat model call failed")
            reply = f"⚠️ Gagal: {llm.describe_error(exc)}."
            if ctx.produced_files:
                reply += ("\nFile yang sempat dibuat tetap saya kirim di bawah.")
        self.store.log_event("agent", "message", reply, chat_id)

        # Inline trigger: consolidate when enough raw experience has piled up,
        # so memory stays fresh even between background passes.
        if self.store.unprocessed_count() >= config.CONSOLIDATE_EVERY_N_EVENTS:
            threading.Thread(target=self._safe_consolidate, daemon=True).start()

        return reply, ctx.produced_files

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
                for path in files:
                    self.file_notifier(task["chat_id"], path)
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
