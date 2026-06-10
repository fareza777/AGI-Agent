"""Engram orchestrator: ties the substrate together for one chat turn,
and runs the background "sleep cycle" thread.
"""

import logging
import threading
import time

from . import composer, config, consolidator, llm
from .store import Store

log = logging.getLogger("engram.agent")


class Agent:
    def __init__(self, store: Store = None):
        self.store = store or Store()
        self._stop = threading.Event()
        self._bg_thread = None

    # ---------------- one chat turn ----------------

    def handle_message(self, chat_id: str, user_text: str) -> str:
        self.store.log_event("user", "message", user_text, chat_id)
        system, messages = composer.build_context(self.store, chat_id, user_text)
        try:
            reply = llm.chat(system, messages)
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

    def stop(self):
        self._stop.set()

    def _loop(self):
        interval = config.CONSOLIDATE_INTERVAL_MIN * 60
        cycles = 0
        while not self._stop.wait(interval):
            self._safe_consolidate()
            cycles += 1
            if cycles % 4 == 0:  # reflect roughly every 4th consolidation pass
                self._safe_reflect()

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
