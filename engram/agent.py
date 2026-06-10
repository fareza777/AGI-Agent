"""Engram orchestrator: ties the substrate together for one chat turn,
and runs the background "sleep cycle" thread.
"""

import logging
import threading
import time

from . import composer, config, consolidator, llm, skill_compiler
from .store import Store
from .tools import ToolContext

log = logging.getLogger("engram.agent")


class Agent:
    def __init__(self, store: Store = None):
        self.store = store or Store()
        self._stop = threading.Event()
        self._bg_thread = None
        self._scheduler_thread = None
        # Set by the interface (e.g. TelegramBot): callable(chat_id, text).
        # Used by the scheduler to deliver due reminders.
        self.notifier = None

    # ---------------- one chat turn ----------------

    def handle_message(self, chat_id: str, user_text: str) -> str:
        self.store.log_event("user", "message", user_text, chat_id)
        system, messages = composer.build_context(self.store, chat_id, user_text)
        try:
            reply = llm.chat(system, messages, ctx=ToolContext(self.store, chat_id))
        except Exception:
            log.exception("chat model call failed")
            reply = "Maaf, saya gagal menghubungi model. Coba lagi sebentar lagi."
        self.store.log_event("agent", "message", reply, chat_id)

        # Inline trigger: consolidate when enough raw experience has piled up,
        # so memory stays fresh even between background passes.
        if self.store.unprocessed_count() >= config.CONSOLIDATE_EVERY_N_EVENTS:
            threading.Thread(target=self._safe_consolidate, daemon=True).start()

        return reply

    # ---------------- background sleep cycle ----------------

    def start_background(self):
        self._bg_thread = threading.Thread(target=self._loop, daemon=True)
        self._bg_thread.start()
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler_thread.start()

    def stop(self):
        self._stop.set()

    def _scheduler_loop(self):
        """Deliver due reminders. Failures leave the reminder pending for retry."""
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
