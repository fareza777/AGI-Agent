"""Offline tests for the memory substrate — no API key or network needed.

Run:  python -m pytest tests/  (or  python tests/test_substrate.py)
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram.store import Store  # noqa: E402
from engram import composer, identity, skills  # noqa: E402
from engram.llm import parse_json  # noqa: E402
from engram.tools import ToolContext, run_tool  # noqa: E402


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


class ToolTests(unittest.TestCase):
    """Offline tools, exercised through the real dispatcher."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = Store(self.path)
        self.ctx = ToolContext(self.store, "c1")

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def test_remember_recall_and_supersession(self):
        out = run_tool("remember", {"subject": "user", "predicate": "works_at",
                                    "value": "Acme", "kind": "fact",
                                    "confidence": 0.9}, self.ctx)
        self.assertIn("new belief", out)
        out = run_tool("remember", {"subject": "user", "predicate": "works_at",
                                    "value": "Globex", "kind": "fact",
                                    "confidence": 0.95}, self.ctx)
        self.assertIn("Superseded", out)
        self.assertIn("Acme", out)
        out = run_tool("recall", {"query": "works_at Globex"}, self.ctx)
        self.assertIn("Globex", out)
        out = run_tool("belief_history", {"subject": "user", "predicate": "works_at"}, self.ctx)
        self.assertIn("ACTIVE", out)
        self.assertIn("superseded", out)

    def test_goal_tool(self):
        self.assertIn("#", run_tool("manage_goal", {"action": "add", "title": "Ship it"}, self.ctx))
        self.assertIn("Ship it", run_tool("manage_goal", {"action": "list"}, self.ctx))
        self.assertIn("done", run_tool("manage_goal", {"action": "done", "goal_id": 1}, self.ctx))

    def test_reminder_tool(self):
        out = run_tool("schedule_reminder",
                       {"due_at": "2099-01-01T09:00:00Z", "message": "hi"}, self.ctx)
        self.assertIn("scheduled", out)
        self.assertIn("hi", run_tool("list_reminders", {}, self.ctx))
        out = run_tool("schedule_reminder",
                       {"due_at": "2001-01-01T09:00:00Z", "message": "past"}, self.ctx)
        self.assertIn("ERROR", out)

    def test_calculate(self):
        self.assertEqual(run_tool("calculate", {"expression": "2 + 3 * 4"}, self.ctx), "14")
        self.assertEqual(run_tool("calculate", {"expression": "2 ** 10"}, self.ctx), "1024")
        out = run_tool("calculate", {"expression": "__import__('os')"}, self.ctx)
        self.assertIn("ERROR", out)

    def test_unknown_tool_and_event_logging(self):
        self.assertIn("ERROR", run_tool("nonexistent", {}, self.ctx))
        run_tool("calculate", {"expression": "1+1"}, self.ctx)
        hits = self.store.search_events("calculate", limit=5)
        self.assertTrue(hits)  # tool calls land in the episodic log

    def test_use_skill(self):
        out = run_tool("use_skill", {"name": "weekly_review"}, self.ctx)
        self.assertIn("manage_goal", out)
        self.assertIn("ERROR", run_tool("use_skill", {"name": "nope"}, self.ctx))


class SkillsTests(unittest.TestCase):
    def test_index_and_load(self):
        entries = dict(skills.index())
        self.assertIn("weekly_review", entries)
        self.assertIn("research_brief", entries)
        self.assertTrue(entries["weekly_review"])          # has a description
        self.assertIn("web_search", skills.load("research_brief"))
        self.assertEqual(skills.load("missing"), "")


class ParseJsonTests(unittest.TestCase):
    """parse_json handles the messy outputs of OpenAI-compatible providers."""

    def test_clean_json(self):
        self.assertEqual(parse_json('{"claims": []}'), {"claims": []})

    def test_fenced_json(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_json_with_prose(self):
        text = 'Here is the result:\n{"claims": [{"x": 1}]}\nHope that helps!'
        self.assertEqual(parse_json(text), {"claims": [{"x": 1}]})


if __name__ == "__main__":
    unittest.main()
