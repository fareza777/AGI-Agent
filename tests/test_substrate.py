"""Offline tests for the memory substrate — no API key or network needed.

Run:  python -m pytest tests/  (or  python tests/test_substrate.py)
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram.store import Store, next_occurrence  # noqa: E402
from engram import config, composer, identity, skills, desktop, telegram_format  # noqa: E402
from engram.llm import parse_json, strip_reasoning  # noqa: E402
from engram.tools import ToolContext, run_tool  # noqa: E402
from engram.agent import (  # noqa: E402
    _guard_file_claims, _inject_execution_nudge, _should_retry_for_tools,
)
from engram.composer import _filter_stale_lessons  # noqa: E402


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
        # "favorite_food" is canonicalized to the "food_preference" slot.
        self.assertIn("food_preference", system)
        self.assertIn("rendang", system)
        self.assertIn("Learn Rust", system)
        self.assertIn(identity.load()[:30], system)
        self.assertEqual(messages[0]["role"], "user")          # API ordering rule
        self.assertEqual(messages[-1]["content"], "what food do I like?")

    def test_context_budget_trims_memory(self):
        # When the budget is tight, the dynamic MEMORY section is trimmed and
        # the highest-confidence belief survives ahead of weak fillers.
        import engram.config as cfg
        self.store.add_claim("user", "apple_premium", "apple premium belief kept",
                             "fact", 0.95, [1], "c1")
        for i in range(10):
            self.store.add_claim("user", f"apple_filler_{i}",
                                 f"apple weak filler belief number {i} " * 6,
                                 "fact", 0.30, [1], "c1")
        original = cfg.MAX_CONTEXT_TOKENS
        try:
            cfg.MAX_CONTEXT_TOKENS = 10**9
            full, _ = composer.build_context(self.store, "c1", "apple")
            full_claims = full.count("| apple_")
            # Tighten so several claims must be dropped.
            cfg.MAX_CONTEXT_TOKENS = composer._est_tokens(full) - 250
            tight, _ = composer.build_context(self.store, "c1", "apple")
        finally:
            cfg.MAX_CONTEXT_TOKENS = original
        self.assertLess(tight.count("| apple_"), full_claims)   # trimmed
        self.assertIn("apple premium belief kept", tight)       # strongest kept
        self.assertLess(composer._est_tokens(tight), composer._est_tokens(full))

    def test_memory_isolated_per_chat(self):
        # One user's belief must neither supersede nor surface in another's.
        self.store.add_claim("user", "secret", "Alpha", "fact", 0.9, [1], "A")
        self.store.add_claim("user", "secret", "Beta", "fact", 0.9, [2], "B")
        a = [c["value"] for c in self.store.search_claims("alpha beta", 10, chat_id="A")]
        b = [c["value"] for c in self.store.search_claims("alpha beta", 10, chat_id="B")]
        self.assertEqual(a, ["Alpha"])
        self.assertEqual(b, ["Beta"])
        # Same-chat update still supersedes within that chat only.
        out = self.store.add_claim("user", "secret", "Gamma", "fact", 0.9, [3], "A")
        self.assertEqual(out["superseded"]["value"], "Alpha")
        b2 = [c["value"] for c in self.store.search_claims("beta", 10, chat_id="B")]
        self.assertEqual(b2, ["Beta"])  # untouched

    def test_episodes_isolated_per_chat(self):
        self.store.log_event("user", "message", "zebra fact for A", "A")
        self.store.log_event("user", "message", "zebra fact for B", "B")
        a = self.store.search_events("zebra", 10, chat_id="A")
        self.assertTrue(all(e["chat_id"] == "A" for e in a))
        self.assertTrue(any("for A" in e["content"] for e in a))

    def test_predicate_canonicalization_supersedes(self):
        # Two phrasings of the same attribute must land in one slot so the
        # second supersedes the first instead of creating a duplicate belief.
        from engram import store as store_mod
        self.assertEqual(store_mod.canonical_predicate("works_at"), "works_at")
        self.assertEqual(store_mod.canonical_predicate("Work Place"), "works_at")
        self.assertEqual(store_mod.canonical_predicate("employer"), "works_at")
        self.store.add_claim("user", "works_at", "Acme", "fact", 0.9, [1], "c1")
        out = self.store.add_claim("user", "employer", "Globex", "fact", 0.9, [2], "c1")
        self.assertIsNotNone(out["superseded"])
        self.assertEqual(out["superseded"]["value"], "Acme")
        active = [c for c in self.store.active_claims() if c["predicate"] == "works_at"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["value"], "Globex")

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


class SkillLearningTests(unittest.TestCase):
    """The S8 lifecycle: create from conversation, upgrade from experience,
    draft → approve. Runs against a temp skills dir."""

    def setUp(self):
        from engram import config
        self._orig_dir = config.SKILLS_DIR
        self.tmpdir = tempfile.mkdtemp()
        config.SKILLS_DIR = __import__("pathlib").Path(self.tmpdir)
        fd, self.dbpath = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = Store(self.dbpath)
        self.ctx = ToolContext(self.store, "c1")

    def tearDown(self):
        import shutil
        from engram import config
        config.SKILLS_DIR = self._orig_dir
        shutil.rmtree(self.tmpdir)
        self.store.close()
        os.unlink(self.dbpath)

    def test_create_then_improve_skill(self):
        out = run_tool("create_skill",
                       {"name": "Deploy Checklist", "description": "Deploy the app safely",
                        "body": "1. Run tests\n2. Push"}, self.ctx)
        self.assertIn("v1, active", out)
        self.assertIn("Run tests", skills.load("deploy_checklist"))  # name sanitized

        out = run_tool("create_skill",
                       {"name": "deploy_checklist", "description": "x", "body": "y"}, self.ctx)
        self.assertIn("ERROR", out)  # duplicates rejected → improve_skill instead

        out = run_tool("improve_skill",
                       {"name": "deploy_checklist",
                        "body": "1. Run tests\n2. Check staging env vars\n3. Push",
                        "changelog": "staging env vars differ from prod"}, self.ctx)
        self.assertIn("v2", out)
        self.assertIn("staging env vars", skills.load("deploy_checklist"))
        # old version archived, auditable
        history = list((__import__("pathlib").Path(self.tmpdir) / "history").glob("*.md"))
        self.assertEqual(len(history), 1)
        self.assertIn("v1", history[0].name)
        # revision recorded in the episodic log
        self.assertTrue(self.store.search_events("skill_revision changelog", limit=5))

    def test_draft_approve_flow(self):
        skills.save("mined_skill", "A proposed skill", "1. step", status="draft")
        self.assertEqual(skills.load("mined_skill"), "")          # drafts invisible to model
        self.assertNotIn("mined_skill", dict(skills.index()))
        self.assertTrue(skills.approve("mined_skill"))
        self.assertEqual(skills.load("mined_skill"), "1. step")   # now active
        self.assertFalse(skills.approve("mined_skill"))           # already active

    def test_drop(self):
        skills.save("temp_skill", "d", "b")
        self.assertTrue(skills.drop("temp_skill"))
        self.assertIsNone(skills.get("temp_skill"))


class ScheduledTaskTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = Store(self.path)
        self.ctx = ToolContext(self.store, "c1")

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def test_schedule_list_cancel_via_tools(self):
        out = run_tool("schedule_task",
                       {"prompt": "kirim ringkasan berita AI",
                        "due_at": "2099-01-01T00:00:00Z",
                        "recurrence": "daily"}, self.ctx)
        self.assertIn("Task #1 scheduled (daily)", out)
        out = run_tool("list_tasks", {}, self.ctx)
        self.assertIn("ringkasan berita AI", out)
        self.assertIn("[daily]", out)
        self.assertIn("cancelled", run_tool("cancel_task", {"task_id": 1}, self.ctx))
        self.assertIn("No scheduled tasks", run_tool("list_tasks", {}, self.ctx))

    def test_schedule_task_validation(self):
        out = run_tool("schedule_task",
                       {"prompt": "x", "due_at": "2001-01-01T00:00:00Z"}, self.ctx)
        self.assertIn("ERROR", out)
        out = run_tool("schedule_task",
                       {"prompt": "x", "due_at": "2099-01-01T00:00:00Z",
                        "recurrence": "fortnightly"}, self.ctx)
        self.assertIn("ERROR", out)

    def test_due_and_complete_run(self):
        # due in the past → shows up in due_tasks
        self.store.add_task("c1", "do it", "2001-01-01T00:00:00Z", "daily")
        due = self.store.due_tasks()
        self.assertEqual(len(due), 1)
        # recurring: completing reschedules into the future
        nxt = next_occurrence(due[0]["due_ts"], due[0]["recurrence"])
        self.store.complete_task_run(due[0]["id"], nxt)
        self.assertEqual(self.store.due_tasks(), [])
        self.assertEqual(len(self.store.active_tasks("c1")), 1)
        # one-shot: completing marks done
        tid = self.store.add_task("c1", "once", "2001-01-01T00:00:00Z", None)
        self.store.complete_task_run(tid, next_occurrence("2001-01-01T00:00:00Z", None))
        self.assertEqual(len(self.store.active_tasks("c1")), 1)  # only the daily one

    def test_next_occurrence(self):
        # recurring from the distant past lands in the future (skips missed runs)
        nxt = next_occurrence("2001-01-01T00:00:00Z", "daily")
        self.assertGreater(nxt, "2026-01-01T00:00:00Z")
        self.assertTrue(nxt.endswith("T00:00:00Z"))   # keeps the time-of-day
        self.assertIsNone(next_occurrence("2001-01-01T00:00:00Z", None))
        self.assertIsNotNone(next_occurrence("2001-01-01T00:00:00Z", "every:90"))
        self.assertIsNone(next_occurrence("2001-01-01T00:00:00Z", "every:abc"))


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_ws = config.WORKSPACE_DIR
        self._orig_dirs = config.ALLOWED_DIRS
        config.WORKSPACE_DIR = __import__("pathlib").Path(self.tmp).resolve()
        config.ALLOWED_DIRS = [config.WORKSPACE_DIR]

    def tearDown(self):
        import shutil
        config.WORKSPACE_DIR = self._orig_ws
        config.ALLOWED_DIRS = self._orig_dirs
        shutil.rmtree(self.tmp)

    def test_safe_filename(self):
        self.assertEqual(desktop.safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(desktop.safe_filename("..\\..\\win\\evil.exe"), "evil.exe")
        self.assertEqual(desktop.safe_filename(""), "file.bin")

    def test_save_inbox_dedupes(self):
        p1 = desktop.save_inbox_bytes("report.txt", b"a")
        p2 = desktop.save_inbox_bytes("report.txt", b"b")
        self.assertNotEqual(p1, p2)
        self.assertEqual(p1.read_bytes(), b"a")
        self.assertEqual(p2.read_bytes(), b"b")
        self.assertTrue(str(p1).startswith(str(config.WORKSPACE_DIR)))


class VisionAndActivityTests(unittest.TestCase):
    """view_image queues images, run_tool emits live-activity lines, and
    encode_image enforces type/size limits."""

    # 1x1 transparent PNG
    PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
           b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc"
           b"\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_ws = config.WORKSPACE_DIR
        self._orig_dirs = config.ALLOWED_DIRS
        config.WORKSPACE_DIR = __import__("pathlib").Path(self.tmp).resolve()
        config.ALLOWED_DIRS = [config.WORKSPACE_DIR]
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = Store(self.db)

    def tearDown(self):
        import shutil
        config.WORKSPACE_DIR = self._orig_ws
        config.ALLOWED_DIRS = self._orig_dirs
        shutil.rmtree(self.tmp)
        self.store.close()
        os.unlink(self.db)

    def test_view_image_queues_pending(self):
        (config.WORKSPACE_DIR / "inbox").mkdir(parents=True)
        img = config.WORKSPACE_DIR / "inbox" / "photo.png"
        img.write_bytes(self.PNG)
        ctx = ToolContext(self.store, "c1")
        out = run_tool("view_image", {"path": "inbox/photo.png"}, ctx)
        self.assertIn("you can now see it", out)
        self.assertEqual(ctx.pending_images, [str(img)])
        # unsupported extension rejected
        (config.WORKSPACE_DIR / "doc.txt").write_text("x")
        self.assertIn("ERROR", run_tool("view_image", {"path": "doc.txt"}, ctx))

    def test_encode_image(self):
        from engram.llm import encode_image
        img = config.WORKSPACE_DIR / "a.png"
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(self.PNG)
        media_type, b64 = encode_image(str(img))
        self.assertEqual(media_type, "image/png")
        self.assertTrue(len(b64) > 0)
        self.assertIsNone(encode_image(str(config.WORKSPACE_DIR / "missing.png")))
        weird = config.WORKSPACE_DIR / "a.xyz"
        weird.write_bytes(b"data")
        self.assertIsNone(encode_image(str(weird)))

    def test_activity_feed_emitted(self):
        lines = []
        ctx = ToolContext(self.store, "c1", activity=lines.append)
        run_tool("calculate", {"expression": "2+2"}, ctx)
        self.assertEqual(lines, ["🧮 calculate: 2+2"])
        # a broken activity channel must not break the tool call
        def boom(_): raise RuntimeError("net down")
        ctx2 = ToolContext(self.store, "c1", activity=boom)
        self.assertEqual(run_tool("calculate", {"expression": "1+1"}, ctx2), "2")

    def test_format_activity_truncates(self):
        from engram.tools import format_activity
        line = format_activity("web_search", {"query": "x" * 200})
        self.assertLessEqual(len(line), 110)
        self.assertTrue(line.startswith("🔎 web_search: "))
        self.assertEqual(format_activity("list_tasks", {}), "🗓 list_tasks")


class LessonsInContextTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        os.unlink(self.path)

    def test_lessons_always_in_context(self):
        self.store.add_claim("agent", "lesson_doc_language",
                             "always write documents in Indonesian",
                             "lesson", 0.9, [], "c1")
        # query shares no keywords with the lesson — it must still appear
        system, _ = composer.build_context(self.store, "c1", "berapa 2+2?")
        self.assertIn("LESSONS FROM PAST MISTAKES", system)
        self.assertIn("Indonesian", system)


class ProviderRobustnessTests(unittest.TestCase):
    """The fixes for intermittent 'gagal menghubungi model' failures."""

    def test_clean_assistant_drops_provider_extras(self):
        from engram.llm import _clean_assistant
        msg = {"role": "assistant", "content": None, "reasoning": "secret chain",
               "refusal": None, "provider_meta": {"x": 1},
               "tool_calls": [{"id": "c1", "type": "function", "index": 0,
                               "function": {"name": "web_search",
                                            "arguments": '{"query": "x"}',
                                            "extra": True}}]}
        out = _clean_assistant(msg)
        self.assertEqual(set(out), {"role", "content", "tool_calls"})
        self.assertEqual(out["content"], "")
        call = out["tool_calls"][0]
        self.assertEqual(set(call), {"id", "type", "function"})
        self.assertEqual(set(call["function"]), {"name", "arguments"})

    def test_provider_error_extraction(self):
        from engram.llm import _provider_error

        class FakeResp:
            status_code = 429
            text = "ignored"
            def json(self):
                return {"error": {"message": "Rate limit exceeded: free-models-per-min"}}

        self.assertIn("Rate limit exceeded", _provider_error(FakeResp()))

        class FakeRespPlain:
            status_code = 502
            text = "Bad gateway"
            def json(self):
                raise ValueError("not json")

        self.assertEqual(_provider_error(FakeRespPlain()), "Bad gateway")

    def test_describe_error(self):
        from engram.llm import LLMError, describe_error
        err = LLMError("provider error 429: Rate limit exceeded (model free ...)",
                       status=429)
        self.assertIn("429", describe_error(err))

        class RateLimitError(Exception):
            pass

        self.assertIn("rate limit", describe_error(RateLimitError()))
        self.assertIn("provider", describe_error(ValueError("boom")))


class StreamingAndStrictProviderTests(unittest.TestCase):
    """SSE accumulation + message normalization for strict providers (MiniMax)."""

    def test_accumulate_sse_text(self):
        from engram.llm import _accumulate_sse
        lines = [
            'data: {"choices":[{"delta":{"role":"assistant","content":"Ha"}}]}',
            "",  # keep-alive
            'data: {"choices":[{"delta":{"content":"lo!"}}]}',
            'data: {"choices":[{"delta":{"reasoning_content":"mikir dulu"}}]}',
            "data: [DONE]",
        ]
        msg = _accumulate_sse(iter(lines))
        self.assertEqual(msg["content"], "Halo!")
        self.assertEqual(msg["reasoning_content"], "mikir dulu")

    def test_accumulate_sse_tool_calls(self):
        from engram.llm import _accumulate_sse
        lines = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c9",'
            '"function":{"name":"web_search","arguments":""}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"{\\"query\\":"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"\\"x\\"}"}}]}}]}',
            "data: [DONE]",
        ]
        msg = _accumulate_sse(iter(lines))
        call = msg["tool_calls"][0]
        self.assertEqual(call["id"], "c9")
        self.assertEqual(call["function"]["name"], "web_search")
        self.assertEqual(call["function"]["arguments"], '{"query":"x"}')

    def test_accumulate_sse_error_and_empty(self):
        from engram.llm import _accumulate_sse
        with self.assertRaises(ValueError):
            _accumulate_sse(iter(['data: {"error":{"message":"boom"}}']))
        with self.assertRaises(ValueError):
            _accumulate_sse(iter(["", ": ping"]))

    def test_merge_consecutive_users(self):
        from engram.llm import _merge_consecutive
        convo = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "halo"},
            {"role": "user", "content": "buat laporan"},   # after a failed turn
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "lanjut"},
        ]
        merged = _merge_consecutive(convo)
        self.assertEqual(len(merged), 4)
        self.assertEqual(merged[1]["content"], "halo\n\nbuat laporan")
        # tool sequences and list-content (images) are left untouched
        toolish = [{"role": "assistant", "content": "", "tool_calls": [{}]},
                   {"role": "assistant", "content": "x"}]
        self.assertEqual(len(_merge_consecutive(toolish)), 2)

    def test_clean_assistant_minimax_reasoning_and_fallback_ids(self):
        from engram import config as cfg
        from engram.llm import _clean_assistant
        msg = {"role": "assistant", "content": "ok", "reasoning_content": "r",
               "tool_calls": [{"function": {"name": "recall", "arguments": "{}"}}]}
        orig = cfg.PROVIDER
        try:
            cfg.PROVIDER = "minimax"
            out = _clean_assistant(msg)
            self.assertEqual(out["reasoning_content"], "r")
            self.assertEqual(out["tool_calls"][0]["id"], "call_0")  # synthesized
            cfg.PROVIDER = "openrouter"
            self.assertNotIn("reasoning_content", _clean_assistant(msg))
        finally:
            cfg.PROVIDER = orig


class ParseJsonTests(unittest.TestCase):
    """parse_json handles the messy outputs of OpenAI-compatible providers."""

    def test_clean_json(self):
        self.assertEqual(parse_json('{"claims": []}'), {"claims": []})

    def test_fenced_json(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_json_with_prose(self):
        text = 'Here is the result:\n{"claims": [{"x": 1}]}\nHope that helps!'
        self.assertEqual(parse_json(text), {"claims": [{"x": 1}]})


class StripReasoningTests(unittest.TestCase):
    def test_complete_block_removed(self):
        self.assertEqual(strip_reasoning("<think>plotting</think>Halo!"), "Halo!")

    def test_unmatched_close_keeps_tail(self):
        self.assertEqual(strip_reasoning("reasoning text</think>Jawaban."), "Jawaban.")

    def test_unclosed_open_is_empty(self):
        self.assertEqual(strip_reasoning("<think>ran out of tokens..."), "")

    def test_plain_text_untouched(self):
        self.assertEqual(strip_reasoning("just a normal reply"), "just a normal reply")


class TelegramFormatTests(unittest.TestCase):
    def test_table_flattened_to_bullets(self):
        md = ("| Tool | Fungsi |\n| --- | --- |\n"
              "| web_search | cari di web |\n| fetch_url | buka URL |")
        out = telegram_format.to_telegram_html(md)
        self.assertNotIn("|", out)
        self.assertIn("• Tool: web_search — Fungsi: cari di web", out)

    def test_heading_to_bold(self):
        self.assertEqual(telegram_format.to_telegram_html("## Hasil"), "<b>Hasil</b>")

    def test_bold_and_bullets(self):
        out = telegram_format.to_telegram_html("- **penting** ya\n- biasa")
        self.assertIn("• <b>penting</b> ya", out)
        self.assertIn("• biasa", out)

    def test_html_escaped(self):
        out = telegram_format.to_telegram_html("nilai a < b & c > d")
        self.assertIn("&lt;", out)
        self.assertIn("&amp;", out)

    def test_code_block_preserved(self):
        out = telegram_format.to_telegram_html("teks\n```\nx = 1 < 2\n```")
        self.assertIn("<pre>", out)
        self.assertIn("x = 1 &lt; 2", out)

    def test_think_stripped(self):
        out = telegram_format.to_telegram_html("<think>secret</think>Halo")
        self.assertEqual(out, "Halo")


class DesktopTests(unittest.TestCase):
    def setUp(self):
        import shutil
        self._shutil = shutil
        self.tmp = tempfile.mkdtemp()
        self._orig_ws = config.WORKSPACE_DIR
        self._orig_dirs = config.ALLOWED_DIRS
        config.WORKSPACE_DIR = __import__("pathlib").Path(self.tmp).resolve()
        config.ALLOWED_DIRS = [config.WORKSPACE_DIR]

    def tearDown(self):
        config.WORKSPACE_DIR = self._orig_ws
        config.ALLOWED_DIRS = self._orig_dirs
        self._shutil.rmtree(self.tmp)

    def test_write_read_list(self):
        desktop.write_file("notes/a.txt", "hello world")
        self.assertIn("hello world", desktop.read_file("notes/a.txt"))
        self.assertIn("a.txt", desktop.list_dir("notes"))

    def test_path_traversal_blocked(self):
        with self.assertRaises(desktop.WorkspaceError):
            desktop.write_file("../escape.txt", "nope")
        with self.assertRaises(desktop.WorkspaceError):
            desktop.read_file("/etc/passwd")

    def test_search_files(self):
        desktop.write_file("x.txt", "the quick brown fox")
        desktop.write_file("y.txt", "nothing here")
        out = desktop.search_files("brown")
        self.assertIn("x.txt", out)
        self.assertNotIn("y.txt", out)

    def test_create_document_md_and_csv(self):
        p = desktop.create_document("rep", "md", "Laporan",
                                    [{"heading": "Ringkasan", "body": "isi"}])
        self.assertTrue(p.exists())
        self.assertIn("# Laporan", p.read_text())
        c = desktop.create_document("data", "csv", "Data", [],
                                    {"headers": ["a", "b"], "rows": [[1, 2], [3, 4]]})
        self.assertIn("a,b", c.read_text())

    def test_create_document_docx_if_available(self):
        try:
            import docx  # noqa: F401
        except ImportError:
            self.skipTest("python-docx not installed")
        p = desktop.create_document("r", "docx", "Judul",
                                    [{"heading": "H", "body": "B"}])
        self.assertTrue(p.exists() and p.stat().st_size > 0)

    def test_create_document_from_source_path(self):
        md = config.WORKSPACE_DIR / "draft_src.md"
        md.write_text("# Title\n\n## Bagian A\n\nIsi paragraf.\n\n## Bagian B\n\nLainnya.",
                      encoding="utf-8")
        p = desktop.create_document("from_src", "docx", "Judul",
                                    source_path="draft_src.md")
        self.assertTrue(p.exists() and p.stat().st_size > 0)


class DocgenFallbackTests(unittest.TestCase):
    """Zero-dependency docx/xlsx/pdf generators — valid files, no libraries."""

    SECTIONS = [{"heading": "Ringkasan", "body": "Isi laporan & analisis <tes>"}]
    TABLE = {"headers": ["Produk", "Unit"], "rows": [["A", "12"], ["B", "8"]]}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = __import__("pathlib").Path(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_minimal_docx_is_valid_zip(self):
        import zipfile
        from engram import docgen
        target = self.dir / "r.docx"
        docgen.minimal_docx(target, "Laporan Tes", self.SECTIONS, self.TABLE)
        with zipfile.ZipFile(target) as z:
            names = set(z.namelist())
            self.assertIn("word/document.xml", names)
            self.assertIn("[Content_Types].xml", names)
            doc = z.read("word/document.xml").decode()
        self.assertIn("Laporan Tes", doc)
        self.assertIn("&lt;tes&gt;", doc)        # XML-escaped
        self.assertIn("<w:tbl>", doc)            # table rendered
        self.assertIn("Produk", doc)

    def test_minimal_xlsx_is_valid_zip(self):
        import zipfile
        from engram import docgen
        target = self.dir / "d.xlsx"
        docgen.minimal_xlsx(target, "Data", [], self.TABLE)
        with zipfile.ZipFile(target) as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("xl/workbook.xml", set(z.namelist()))
        self.assertIn("Produk", sheet)
        self.assertIn("<v>12</v>", sheet)        # numbers as numeric cells

    def test_minimal_pdf_structure(self):
        from engram import docgen
        target = self.dir / "r.pdf"
        long_sections = [{"heading": f"Bagian {i}", "body": "kalimat panjang " * 40}
                         for i in range(12)]                 # forces multi-page
        docgen.minimal_pdf(target, "Laporan (PDF)", long_sections, self.TABLE)
        data = target.read_bytes()
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertIn(rb"Laporan \(PDF\)", data)             # escaped parens
        self.assertGreater(data.count(b"/Type /Page "), 1)   # paginated

    def test_minimal_pptx_is_valid_package(self):
        import zipfile
        import xml.dom.minidom as minidom
        from engram import docgen
        target = self.dir / "deck.pptx"
        docgen.minimal_pptx(target, "Judul Deck", self.SECTIONS, self.TABLE)
        with zipfile.ZipFile(target) as z:
            self.assertIsNone(z.testzip())
            names = set(z.namelist())
            # Full reference chain must be present, or PowerPoint won't open it.
            for part in ("ppt/presentation.xml", "ppt/slideMasters/slideMaster1.xml",
                         "ppt/slideLayouts/slideLayout1.xml", "ppt/theme/theme1.xml",
                         "ppt/slides/slide1.xml"):
                self.assertIn(part, names)
            for n in names:
                if n.endswith(".xml") or n.endswith(".rels"):
                    minidom.parseString(z.read(n))   # well-formed XML
            self.assertIn("Judul Deck", z.read("ppt/slides/slide1.xml").decode())

    def test_col_letter(self):
        from engram.docgen import _col_letter
        self.assertEqual(_col_letter(0), "A")
        self.assertEqual(_col_letter(25), "Z")
        self.assertEqual(_col_letter(26), "AA")


class DocumentToolDeliveryTests(unittest.TestCase):
    """create_document must queue the file for delivery on the ToolContext."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_ws = config.WORKSPACE_DIR
        self._orig_dirs = config.ALLOWED_DIRS
        config.WORKSPACE_DIR = __import__("pathlib").Path(self.tmp).resolve()
        config.ALLOWED_DIRS = [config.WORKSPACE_DIR]
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = Store(self.db)

    def tearDown(self):
        import shutil
        config.WORKSPACE_DIR = self._orig_ws
        config.ALLOWED_DIRS = self._orig_dirs
        shutil.rmtree(self.tmp)
        self.store.close()
        os.unlink(self.db)

    def test_create_document_queues_file(self):
        ctx = ToolContext(self.store, "c1")
        out = run_tool("create_document",
                       {"filename": "laporan", "format": "md", "title": "T",
                        "sections": [{"heading": "H", "body": "B"}]}, ctx)
        self.assertIn("sent to the user", out)
        self.assertEqual(len(ctx.produced_files), 1)
        self.assertTrue(ctx.produced_files[0].endswith("laporan.md"))

    def test_create_document_accepts_stringified_args(self):
        # Weaker models pass structured args as JSON strings — must still work.
        ctx = ToolContext(self.store, "c1")
        out = run_tool("create_document",
                       {"filename": "rpt", "format": "docx", "title": "T",
                        "sections": '[{"heading": "H", "body": "B"}]',
                        "table": '{"headers": ["a"], "rows": [["1"]]}'}, ctx)
        self.assertIn("sent to the user", out)
        self.assertTrue(ctx.produced_files[0].endswith("rpt.docx"))

    def test_failed_tool_shows_error_in_activity_feed(self):
        lines = []
        ctx = ToolContext(self.store, "c1", activity=lines.append)
        run_tool("view_image", {"path": "tidak_ada.png"}, ctx)
        self.assertTrue(any(l.startswith("❌ view_image:") for l in lines))


class FileClaimGuardTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = Store(self.db)
        self.ctx = ToolContext(self.store, "c1")

    def tearDown(self):
        self.store.close()
        os.unlink(self.db)

    def test_no_warning_when_files_queued(self):
        self.ctx.produced_files.append("/tmp/x.docx")
        reply = "2 file sudah dikirim ke Telegram."
        self.assertEqual(
            _guard_file_claims(reply, self.ctx, "kirim laporan", self.store, "c1"), reply)

    def test_warning_on_hallucinated_send(self):
        reply = "Cek Telegram — 2 file sudah dikirim (docx + pptx)."
        out = _guard_file_claims(reply, self.ctx, "buat laporan docx", self.store, "c1")
        self.assertIn("Catatan sistem", out)
        self.assertIn("tidak dipanggil", out)

    def test_no_warning_on_status_recap(self):
        reply = ("Halo! Status: laporan_llm.docx — sudah dikirim, "
                 "ppt_llm.pptx — sudah dikirim. Mau revisi?")
        out = _guard_file_claims(reply, self.ctx, "halo", self.store, "c1")
        self.assertNotIn("Catatan sistem", out)

    def test_warning_when_tool_ran_but_no_file(self):
        self.ctx.file_tools_called = 1
        reply = "Sebentar ya, lagi buat file."
        out = _guard_file_claims(reply, self.ctx, "buat laporan", self.store, "c1")
        self.assertIn("tidak ada file yang ter-queue", out)

    def test_file_tools_called_counter(self):
        ctx = ToolContext(self.store, "c1")
        run_tool("recall", {"query": "test"}, ctx)
        self.assertEqual(ctx.file_tools_called, 0)
        # create_document needs workspace — just verify counter increments on send_file error path
        run_tool("send_file", {"path": "missing.docx"}, ctx)
        self.assertEqual(ctx.file_tools_called, 1)


class StaleLessonFilterTests(unittest.TestCase):
    def test_filters_wrong_failure_lessons(self):
        lessons = [
            {"id": 1, "value": "create_document silent-fail on server engram"},
            {"id": 2, "value": "use write_file + source_path for long reports"},
        ]
        out = _filter_stale_lessons(lessons)
        self.assertEqual(len(out), 1)
        self.assertIn("source_path", out[0]["value"])


class ExecutionNudgeTests(unittest.TestCase):
    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.store = Store(self.db)

    def tearDown(self):
        self.store.close()
        os.unlink(self.db)

    def test_nudge_on_file_request(self):
        out = _inject_execution_nudge("buat laporan word justify", self.store, "c1")
        self.assertIn("INSTRUKSI SISTEM", out)

    def test_nudge_on_confirmation(self):
        self.store.log_event("agent", "message",
                             "Mau lanjut? Alternatif write_file + send_file", "c1")
        out = _inject_execution_nudge("ya", self.store, "c1")
        self.assertIn("INSTRUKSI SISTEM", out)

    def test_nudge_on_followup_instruction_after_proposal(self):
        # Reproduces the stuck screenshot: agent offered to add diagram/table,
        # user replies with an instruction (not a bare "ya"). Must still nudge.
        self.store.log_event("agent", "message",
                             "Mau aku tambahin diagram alur loop atau tabel "
                             "perbandingan lebih dalam?", "c1")
        out = _inject_execution_nudge("Tambah diagram dan tabel", self.store, "c1")
        self.assertIn("INSTRUKSI SISTEM", out)
        out2 = _inject_execution_nudge("Mana hasilnya", self.store, "c1")
        self.assertIn("INSTRUKSI SISTEM", out2)


class StallRetryTests(unittest.TestCase):
    """The 'announce a plan but never call a tool' stall must force a retry."""

    class _Ctx:
        def __init__(self, called=0, produced=()):
            self.file_tools_called = called
            self.produced_files = list(produced)

    def test_retry_on_narrated_plan(self):
        r = ("Add diagram + tabel. Plan: rewrite bab 4, 5, 8 — tambah ASCII "
             "diagram + comparison tables. Generate versi 2.")
        self.assertTrue(_should_retry_for_tools(r, "Tambah diagram dan tabel",
                                                self._Ctx()))

    def test_retry_on_promised_create_document(self):
        r = "Aku tulis ulang .md draft lalu create_document."
        self.assertTrue(_should_retry_for_tools(r, "Mana hasilnya", self._Ctx()))

    def test_no_retry_when_file_produced(self):
        r = "Aku tulis ulang .md lalu create_document."
        self.assertFalse(_should_retry_for_tools(r, "x", self._Ctx(produced=["a.docx"])))

    def test_no_retry_on_clarifying_question(self):
        r = "Mau saya buat laporan dalam docx atau pdf?"
        self.assertFalse(_should_retry_for_tools(r, "buat laporan", self._Ctx()))

    def test_no_retry_on_casual_reply(self):
        self.assertFalse(_should_retry_for_tools("Tentu, senang membantu!",
                                                 "halo", self._Ctx()))


class IntegrationsAndChartsTests(unittest.TestCase):
    """Sprint 2/3 additions: connectors gating, charts, embeddings helpers."""

    def test_send_email_reports_unconfigured(self):
        # With no SMTP configured the connector must say so, never pretend.
        import importlib
        from engram import connectors, config
        host, frm = config.SMTP_HOST, config.SMTP_FROM
        config.SMTP_HOST, config.SMTP_FROM = "", ""
        try:
            out = connectors.send_email("a@b.com", "hi", "body")
        finally:
            config.SMTP_HOST, config.SMTP_FROM = host, frm
        self.assertTrue(out.startswith("ERROR"))
        self.assertIn("dikonfigurasi", out)

    def test_send_email_tool_gated_through_dispatcher(self):
        from engram.tools import run_tool, ToolContext
        from engram.store import Store
        store = Store(":memory:")
        ctx = ToolContext(store, "c1")
        out = run_tool("send_email", {"to": "x@y.com", "subject": "s", "body": "b"}, ctx)
        self.assertTrue(out.startswith("ERROR"))  # not configured in tests
        store.close()

    def test_embeddings_pack_roundtrip_and_cosine(self):
        from engram import embeddings
        vec = [0.1, -0.2, 0.3, 0.4]
        back = embeddings.unpack(embeddings.pack(vec))
        for a, b in zip(vec, back):
            self.assertAlmostEqual(a, b, places=5)
        self.assertAlmostEqual(embeddings.cosine([1, 0], [1, 0]), 1.0, places=6)
        self.assertAlmostEqual(embeddings.cosine([1, 0], [0, 1]), 0.0, places=6)
        self.assertEqual(embeddings.cosine([], [1, 2]), 0.0)

    def test_chart_render_degrades_without_data(self):
        from engram import charts
        # No table / no numeric column -> None, never an exception.
        self.assertIsNone(charts.render({}, {"type": "bar"}, "/tmp/none.png"))
        self.assertIsNone(
            charts.render({"headers": ["a"], "rows": [["x"]]},
                          {"type": "bar", "value_col": 0}, "/tmp/none.png"))


if __name__ == "__main__":
    unittest.main()
