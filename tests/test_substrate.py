"""Offline tests for the memory substrate — no API key or network needed.

Run:  python -m pytest tests/  (or  python tests/test_substrate.py)
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram.store import Store  # noqa: E402
from engram import composer, identity  # noqa: E402


class StoreTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def test_event_log_and_search(self):
        eid = self.store.log_event("user", "message", "I love hiking in the mountains", "c1")
        self.assertGreater(eid, 0)
        hits = self.store.search_events("hiking mountains", limit=5)
        self.assertEqual(hits[0]["id"], eid)

    def test_claim_slot_supersession(self):
        first = self.store.add_claim("user", "works_at", "Acme Corp", "fact", 0.9, [1])
        self.assertFalse(first["duplicate"])
        # same value → duplicate, confidence boost, no new claim
        dup = self.store.add_claim("user", "works_at", "acme corp", "fact", 0.9, [2])
        self.assertTrue(dup["duplicate"])
        # different value → supersession
        second = self.store.add_claim("user", "works_at", "Globex", "fact", 0.95, [3])
        self.assertFalse(second["duplicate"])
        self.assertIsNotNone(second["superseded"])
        self.assertEqual(second["superseded"]["value"], "Acme Corp")

        history = self.store.claim_history("user", "works_at")
        self.assertEqual(len(history), 2)
        self.assertIsNotNone(history[0]["valid_to"])          # old claim closed
        self.assertEqual(history[0]["superseded_by"], second["id"])
        self.assertIsNone(history[1]["valid_to"])             # new claim active

        active = self.store.search_claims("works_at Globex", limit=5)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["value"], "Globex")

    def test_goals(self):
        gid = self.store.add_goal("c1", "Ship the MVP")
        self.assertEqual(len(self.store.goals("c1")), 1)
        self.assertTrue(self.store.set_goal_status(gid, "done"))
        self.assertEqual(len(self.store.goals("c1")), 0)

    def test_composer_builds_budgeted_context(self):
        self.store.add_claim("user", "favorite_food", "rendang", "preference", 0.95, [1], "c1")
        self.store.log_event("user", "message", "hello there", "c1")
        self.store.log_event("agent", "message", "hi! how can I help?", "c1")
        self.store.add_goal("c1", "Learn Rust")

        system, messages = composer.build_context(self.store, "c1", "what food do I like?")
        self.assertIn("favorite_food", system)
        self.assertIn("rendang", system)
        self.assertIn("Learn Rust", system)
        self.assertIn(identity.load()[:30], system)
        self.assertEqual(messages[0]["role"], "user")          # API ordering rule
        self.assertEqual(messages[-1]["content"], "what food do I like?")

    def test_meta_roundtrip(self):
        self.store.set_meta("tg_offset", "42")
        self.assertEqual(self.store.get_meta("tg_offset"), "42")


if __name__ == "__main__":
    unittest.main()
