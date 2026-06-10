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


if __name__ == "__main__":
    unittest.main()
