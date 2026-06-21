"""Tool layer — what the agent can *do*, not just say.

Two classes of tools:

Memory-native (the part Hermes-style agents can't have, because their memory
is flat text):
  recall          — re-enter the retrieval pipeline mid-reasoning (iterative S6)
  remember        — write an explicit claim into a belief slot (with supersession)
  belief_history  — read the full timeline of one belief slot
  manage_goal     — add/complete/list goals in the persistent goal tree
  schedule_reminder / list_reminders — future-dated messages the scheduler delivers

Digital-assistant (do things, not just answer — sandboxed to config.ALLOWED_DIRS):
  list_dir / read_file / write_file / make_dir / move_file / delete_file /
  search_files — workspace file management
  create_document — generate docx/xlsx/pptx/pdf/md/html/csv and deliver it to the user
  send_file       — deliver an existing workspace file
  git             — read-only repository inspection
  run_shell       — shell commands; OFF by default (ENGRAM_ENABLE_SHELL_TOOL=1)

World-facing (parity with Hermes-style agents):
  web_search      — DuckDuckGo, no API key needed
  fetch_url       — fetch a page and return readable text
  calculate       — safe arithmetic evaluator
  use_skill       — load the full body of a markdown skill on demand
  create_skill / improve_skill — learn and upgrade skills from experience
  run_python      — subprocess; OFF by default (ENGRAM_ENABLE_CODE_TOOL=1)

Files a tool produces are queued on ToolContext.produced_files; the interface
(TelegramBot) delivers them after the turn. Every tool execution is appended to
the episodic event log, so the consolidation engine can later distill lessons.
"""

import ast
import html
import json
import logging
import operator
import os
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone

import requests

from . import config, desktop, skills
from .store import Store

log = logging.getLogger("engram.tools")


class ToolContext:
    def __init__(self, store: Store, chat_id: str, activity=None, file_notifier=None):
        self.store = store
        self.chat_id = chat_id
        # Files the agent produced this turn; the interface delivers them.
        self.produced_files = []
        # Subset already pushed to Telegram during the turn (don't wait for reply).
        self.delivered_files = []
        # How many times create_document / send_file ran this turn.
        self.file_tools_called = 0
        # Images queued by view_image; the LLM loop attaches them to the next turn.
        self.pending_images = []
        # Concatenated text of every tool result this turn — lets the grounding
        # guard check whether a URL/source the model cited actually came back
        # from a tool, or was invented.
        self.tool_output = []
        # Grounding tools (web_search / fetch_url / recall) that ran this turn.
        self.grounding_calls = 0
        # Optional callable(text): live activity feed shown in the chat.
        self.activity = activity
        # Optional callable(path) -> bool: deliver file immediately when ready.
        self.file_notifier = file_notifier

    def deliver_file(self, path):
        p = str(path)
        if p not in self.produced_files:
            self.produced_files.append(p)
        if (self.file_notifier is not None and p not in self.delivered_files
                and os.path.isfile(p)):
            try:
                if self.file_notifier(p):
                    self.delivered_files.append(p)
            except Exception:
                log.debug("immediate file delivery failed", exc_info=True)

    def emit_activity(self, text: str):
        if self.activity is None:
            return
        try:
            self.activity(text)
        except Exception:
            log.debug("activity feed delivery failed", exc_info=True)


# ---------------- tool specs (Anthropic schema; converted for OpenAI-compat) ----------------

def _spec(name, description, properties=None, required=None):
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    }


def tool_specs() -> list:
    specs = [
        _spec("recall",
              "Search your own permanent memory (beliefs and past episodes) for "
              "anything relevant to a query. Use this whenever the answer may "
              "depend on something the user told you before that is not already "
              "in the MEMORY section of your context.",
              {"query": {"type": "string", "description": "what to look for"}},
              ["query"]),
        _spec("remember",
              "Write one important fact, preference, or lesson into permanent "
              "memory immediately (don't wait for background consolidation). "
              "If the slot already holds a different value, the old belief is "
              "superseded and kept in history.",
              {"subject": {"type": "string", "description": "entity, usually 'user'"},
               "predicate": {"type": "string", "description": "snake_case attribute, e.g. works_at"},
               "value": {"type": "string"},
               "kind": {"type": "string", "enum": ["fact", "preference", "lesson"]},
               "confidence": {"type": "number", "description": "0..1"}},
              ["subject", "predicate", "value", "kind", "confidence"]),
        _spec("belief_history",
              "Read the full timeline of one belief slot: every value it has "
              "held, when each was believed, and what superseded it.",
              {"subject": {"type": "string"}, "predicate": {"type": "string"}},
              ["subject", "predicate"]),
        _spec("manage_goal",
              "Manage the user's persistent goals. action=add needs title; "
              "action=done needs goal_id; action=list needs nothing.",
              {"action": {"type": "string", "enum": ["add", "done", "list"]},
               "title": {"type": "string"},
               "goal_id": {"type": "integer"}},
              ["action"]),
        _spec("schedule_reminder",
              "Schedule a message to be sent to the user at a future time. "
              "due_at must be an ISO 8601 UTC timestamp like 2026-06-11T09:00:00Z. "
              "The current UTC time is in your context.",
              {"due_at": {"type": "string"}, "message": {"type": "string"}},
              ["due_at", "message"]),
        _spec("list_reminders", "List the user's pending reminders."),
        _spec("schedule_task",
              "Schedule an AGENT TASK: at the given time you will actually "
              "execute the prompt (with all your tools) and send the result to "
              "the user — use for 'kirim analisis tiap pagi', 'cek harga X "
              "tiap jam', 'buatkan laporan hari Senin'. Different from "
              "schedule_reminder, which only sends a fixed text. due_at: ISO "
              "8601 UTC. recurrence: once | hourly | daily | weekly | "
              "every:<minutes>.",
              {"prompt": {"type": "string",
                          "description": "instruction your future self will execute"},
               "due_at": {"type": "string", "description": "first run, ISO 8601 UTC"},
               "recurrence": {"type": "string",
                              "enum": ["once", "hourly", "daily", "weekly"],
                              "description": "or 'every:<minutes>'"}},
              ["prompt", "due_at"]),
        _spec("list_tasks", "List the user's scheduled agent tasks."),
        _spec("cancel_task", "Cancel a scheduled agent task by id.",
              {"task_id": {"type": "integer"}}, ["task_id"]),
        _spec("web_search",
              "Search the web (DuckDuckGo). Returns titles, URLs and snippets. "
              "Use for anything recent or outside your knowledge.",
              {"query": {"type": "string"}}, ["query"]),
        _spec("fetch_url",
              "Fetch a web page and return its readable text (truncated).",
              {"url": {"type": "string"}}, ["url"]),
        _spec("calculate",
              "Evaluate an arithmetic expression exactly (+ - * / // % ** and "
              "parentheses). Use for any non-trivial math.",
              {"expression": {"type": "string"}}, ["expression"]),
        _spec("use_skill",
              "Load the full instructions of one of your skills by name. The "
              "available skills are listed in your context.",
              {"name": {"type": "string"}}, ["name"]),
        _spec("create_skill",
              "Save a NEW reusable skill. Use when the user teaches you a "
              "procedure ('kalau aku minta X, lakukan Y') or asks you to "
              "remember how to do something. body = numbered markdown steps "
              "referencing your tools by name. Active immediately.",
              {"name": {"type": "string", "description": "snake_case"},
               "description": {"type": "string", "description": "one line: when to use it"},
               "body": {"type": "string"}},
              ["name", "description", "body"]),
        _spec("improve_skill",
              "Upgrade an existing skill from experience: when a skill's steps "
              "proved wrong/incomplete, or the user corrects how a task should "
              "be done. Read the current body with use_skill first, then pass "
              "the COMPLETE improved body. The old version is archived and the "
              "version number bumped.",
              {"name": {"type": "string"},
               "body": {"type": "string", "description": "complete new body"},
               "description": {"type": "string", "description": "updated one-liner (optional)"},
               "changelog": {"type": "string", "description": "what changed and why"}},
              ["name", "body", "changelog"]),

        # ---- digital-assistant: files ----
        _spec("list_dir",
              "List files and folders in your workspace (or an allowed directory).",
              {"path": {"type": "string", "description": "default '.'"}}),
        _spec("read_file",
              "Read a file from the workspace / allowed directories. Handles "
              "plain text AND extracts text from PDF, Word (.docx), Excel "
              "(.xlsx), and PowerPoint (.pptx) — use it to summarize/analyze a "
              "document the user sent.",
              {"path": {"type": "string"}}, ["path"]),
        _spec("write_file",
              "Write (create or overwrite) a text file in the workspace.",
              {"path": {"type": "string"}, "content": {"type": "string"}},
              ["path", "content"]),
        _spec("make_dir", "Create a folder in the workspace.",
              {"path": {"type": "string"}}, ["path"]),
        _spec("move_file", "Move or rename a file within the workspace.",
              {"src": {"type": "string"}, "dst": {"type": "string"}}, ["src", "dst"]),
        _spec("delete_file", "Delete a file (or an empty folder) in the workspace.",
              {"path": {"type": "string"}}, ["path"]),
        _spec("search_files",
              "Search filenames and text content under a workspace path.",
              {"query": {"type": "string"}, "path": {"type": "string", "description": "default '.'"}},
              ["query"]),

        # ---- digital-assistant: document generation + delivery ----
        _spec("create_document",
              "Generate a real document and SEND it to the user. Use this for "
              "'buatkan laporan', 'bikin docx/excel/ppt/pdf', etc. Formats: "
              "docx, xlsx, pptx, pdf, md, html, txt, csv. "
              "For LONG reports: write_file a .md draft first, then call "
              "create_document with source_path (NOT huge inline sections — "
              "large payloads can break the tool loop). Short docs: inline "
              "sections ([{heading, body}]); optionally a table "
              "{headers:[...], rows:[[...]]}. csv/xlsx are best with a table. "
              "In docx/pptx bodies: '- ' lines become bullets (2 leading "
              "spaces = sub-bullet), '1. ' numbered items, **text** bold. "
              "For pptx each section becomes one slide.",
              {"filename": {"type": "string", "description": "without extension is fine"},
               "format": {"type": "string",
                          "enum": ["docx", "xlsx", "pptx", "pdf", "md", "html", "txt", "csv"]},
               "title": {"type": "string"},
               "source_path": {"type": "string",
                               "description": "workspace .md/.txt/.html to render "
                               "(preferred for long content)"},
               "sections": {"type": "array", "items": {
                   "type": "object",
                   "properties": {"heading": {"type": "string"},
                                  "body": {"type": "string"}}}},
               "table": {"type": "object", "properties": {
                   "headers": {"type": "array", "items": {"type": "string"}},
                   "rows": {"type": "array", "items": {
                       "type": "array", "items": {"type": "string"}}}}},
               "chart": {"type": "object",
                         "description": "optional chart drawn from the table and "
                         "embedded (docx/pptx as an image, xlsx as a native chart)",
                         "properties": {
                             "type": {"type": "string",
                                      "enum": ["bar", "barh", "line", "pie"]},
                             "label_col": {"type": "integer",
                                           "description": "0-based column for labels"},
                             "value_col": {"type": "integer",
                                           "description": "0-based numeric column"},
                             "title": {"type": "string"}}}},
              ["filename", "format", "title"]),
        _spec("send_file",
              "Send an existing workspace file to the user (e.g. one you wrote "
              "with write_file, or generated earlier).",
              {"path": {"type": "string"}}, ["path"]),

        _spec("view_image",
              "Look at an image file (photo the user sent to inbox/, a "
              "screenshot, a chart). After calling this you will SEE the image "
              "and can describe/analyze it. Supported: jpg, png, webp, gif.",
              {"path": {"type": "string"}}, ["path"]),

        # ---- digital-assistant: repo inspection ----
        _spec("git",
              "Run a READ-ONLY git command on a repository in an allowed "
              "directory (status, log, diff, branch, show, remote, ls-files, "
              "shortlog). Use to check repo state.",
              {"repo_path": {"type": "string"},
               "command": {"type": "string", "description": "e.g. 'status' or 'log --oneline'"}},
              ["repo_path", "command"]),
        _spec("send_email",
              "Send a plain-text email to one or more recipients via the "
              "configured SMTP account. If email isn't configured the tool "
              "returns an ERROR saying so — never claim an email was sent "
              "unless the result confirms it.",
              {"to": {"type": "string", "description": "comma-separated addresses"},
               "subject": {"type": "string"},
               "body": {"type": "string"}},
              ["to", "subject", "body"]),
    ]
    if config.ENABLE_CODE_TOOL:
        specs.append(_spec(
            "run_python",
            "Run a short Python script in a subprocess (10s timeout) and return "
            "its stdout/stderr. Use print() for output.",
            {"code": {"type": "string"}}, ["code"]))
    if config.ENABLE_SHELL_TOOL:
        specs.append(_spec(
            "run_shell",
            "Run a shell command in the workspace directory and return its "
            "output. Use for git clone, builds, system tasks.",
            {"command": {"type": "string"}}, ["command"]))
    return specs


def openai_tool_specs() -> list:
    return [{"type": "function",
             "function": {"name": s["name"], "description": s["description"],
                          "parameters": s["input_schema"]}}
            for s in tool_specs()]


# ---------------- dispatcher ----------------

_GATED = {"run_python": "ENABLE_CODE_TOOL", "run_shell": "ENABLE_SHELL_TOOL"}

_ICONS = {
    "web_search": "🔎", "fetch_url": "🌐", "calculate": "🧮",
    "recall": "🧠", "remember": "🧠", "belief_history": "🧠",
    "manage_goal": "🎯",
    "schedule_reminder": "⏰", "list_reminders": "⏰",
    "schedule_task": "🗓", "list_tasks": "🗓", "cancel_task": "🗓",
    "use_skill": "📚", "create_skill": "📚", "improve_skill": "📚",
    "list_dir": "📁", "make_dir": "📁", "move_file": "📁",
    "read_file": "📄", "write_file": "✍️", "delete_file": "🗑",
    "search_files": "🔍", "create_document": "📝", "send_file": "📤",
    "git": "🔧", "run_python": "🐍", "run_shell": "💻", "view_image": "👁",
    "send_email": "✉️",
}
# Most-informative arg to show, in priority order.
_ARG_KEYS = ("query", "url", "path", "filename", "expression", "command",
             "prompt", "name", "title", "message", "repo_path", "src", "subject")


def format_activity(name: str, args: dict) -> str:
    """One compact line for the live activity feed, e.g. '🔎 web_search: berita AI'."""
    icon = _ICONS.get(name, "⚙️")
    for key in _ARG_KEYS:
        val = (args or {}).get(key)
        if val:
            val = str(val).replace("\n", " ").strip()
            if len(val) > 80:
                val = val[:77] + "…"
            return f"{icon} {name}: {val}"
    return f"{icon} {name}"


_FILE_DELIVERY_TOOLS = frozenset({"create_document", "send_file"})
# Noisy tools — skip live activity lines in Telegram (user sees spam).
_QUIET_ACTIVITY = frozenset({"list_dir", "read_file", "search_files", "recall"})


def run_tool(name: str, args: dict, ctx: ToolContext) -> str:
    handler = _HANDLERS.get(name)
    if handler is None or (name in _GATED and not getattr(config, _GATED[name])):
        return f"ERROR: unknown tool '{name}'"
    if name in _FILE_DELIVERY_TOOLS:
        ctx.file_tools_called += 1
    if name not in _QUIET_ACTIVITY:
        ctx.emit_activity(format_activity(name, args or {}))
    try:
        result = handler(args or {}, ctx)
    except Exception as exc:  # tool errors go back to the model, not up the stack
        log.exception("tool %s failed", name)
        result = f"ERROR: {type(exc).__name__}: {exc}"
    if isinstance(result, str):
        # Record output for the grounding guard (cap to keep memory bounded).
        ctx.tool_output.append(result[:6000])
        if name in ("web_search", "fetch_url", "recall"):
            ctx.grounding_calls += 1
    if isinstance(result, str) and result.startswith("ERROR"):
        # Surface failures in the live feed too — the user must never be told
        # "sudah dikirim" while a ❌ was silently swallowed.
        ctx.emit_activity(f"❌ {name}: {result[:120]}")
    ctx.store.log_event(
        "system", "tool",
        json.dumps({"tool": name, "args": args, "result": str(result)[:500]},
                   ensure_ascii=False),
        ctx.chat_id)
    return str(result)


# ---------------- memory-native tools ----------------

def _recall(args, ctx):
    query = args["query"]
    claims = ctx.store.search_claims(query, limit=12)
    events = ctx.store.search_events(query, limit=6)
    lines = []
    for c in claims:
        lines.append(f"BELIEF: {c['subject']} | {c['predicate']} | {c['value']} "
                     f"[{c['kind']}, conf {c['confidence']:.2f}, since {c['valid_from'][:10]}]")
    for e in events:
        snippet = e["content"][:300].replace("\n", " ")
        lines.append(f"EPISODE [{e['ts'][:10]}, {e['actor']}]: {snippet}")
    return "\n".join(lines) if lines else "No memories match that query."


def _remember(args, ctx):
    outcome = ctx.store.add_claim(
        subject=args["subject"].strip().lower(),
        predicate=args["predicate"].strip().lower(),
        value=args["value"].strip(),
        kind=args["kind"],
        confidence=max(0.0, min(1.0, float(args["confidence"]))),
        source_event_ids=[],
        chat_id=ctx.chat_id)
    if outcome["duplicate"]:
        return "Already believed (confidence boosted)."
    if outcome["superseded"] is not None:
        return (f"Stored. Superseded previous belief: "
                f"'{outcome['superseded']['value']}' (held since "
                f"{outcome['superseded']['valid_from'][:10]}).")
    return "Stored as a new belief."


def _belief_history(args, ctx):
    rows = ctx.store.claim_history(args["subject"].strip().lower(),
                                   args["predicate"].strip().lower())
    if not rows:
        return "No history for that slot."
    lines = []
    for r in rows:
        status = "ACTIVE" if r["valid_to"] is None else f"superseded {r['valid_to'][:10]}"
        lines.append(f"{r['valid_from'][:10]}: {r['value']} ({status})")
    return "\n".join(lines)


def _manage_goal(args, ctx):
    action = args["action"]
    if action == "add":
        gid = ctx.store.add_goal(ctx.chat_id, args["title"])
        return f"Goal #{gid} added."
    if action == "done":
        ok = ctx.store.set_goal_status(int(args["goal_id"]), "done")
        return "Goal marked done." if ok else "ERROR: no such goal."
    goals = ctx.store.goals(ctx.chat_id, "active")
    if not goals:
        return "No active goals."
    return "\n".join(f"#{g['id']} {g['title']} (since {g['created_at'][:10]})" for g in goals)


def _schedule_reminder(args, ctx):
    due = args["due_at"].strip().replace("z", "Z")
    parsed = datetime.fromisoformat(due.replace("Z", "+00:00"))
    if parsed <= datetime.now(timezone.utc):
        return "ERROR: due_at is in the past."
    rid = ctx.store.add_reminder(ctx.chat_id, parsed.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 args["message"])
    return f"Reminder #{rid} scheduled for {due} UTC."


def _list_reminders(args, ctx):
    rows = ctx.store.pending_reminders(ctx.chat_id)
    if not rows:
        return "No pending reminders."
    return "\n".join(f"#{r['id']} {r['due_ts']} — {r['message']}" for r in rows)


def _schedule_task(args, ctx):
    due = args["due_at"].strip().replace("z", "Z")
    parsed = datetime.fromisoformat(due.replace("Z", "+00:00"))
    if parsed <= datetime.now(timezone.utc):
        return "ERROR: due_at is in the past."
    recurrence = (args.get("recurrence") or "once").strip().lower()
    if recurrence == "once":
        recurrence = None
    elif not (recurrence in ("hourly", "daily", "weekly")
              or recurrence.startswith("every:")):
        return f"ERROR: invalid recurrence '{recurrence}'"
    tid = ctx.store.add_task(ctx.chat_id, args["prompt"],
                             parsed.strftime("%Y-%m-%dT%H:%M:%SZ"), recurrence)
    rec_label = recurrence or "once"
    return f"Task #{tid} scheduled ({rec_label}), first run {due} UTC."


def _list_tasks(args, ctx):
    rows = ctx.store.active_tasks(ctx.chat_id)
    if not rows:
        return "No scheduled tasks."
    return "\n".join(
        f"#{r['id']} [{r['recurrence'] or 'once'}] next {r['due_ts']} — {r['prompt'][:120]}"
        for r in rows)


def _cancel_task(args, ctx):
    ok = ctx.store.set_task_status(int(args["task_id"]), "cancelled")
    return "Task cancelled." if ok else "ERROR: no such task."


# ---------------- world-facing tools ----------------

_UA = {"User-Agent": "Mozilla/5.0 (compatible; EngramAgent/0.1)"}
# DuckDuckGo blocks obvious bot user-agents (returns an empty result page),
# which used to silently yield "No results." and tempt the model to fabricate.
# A realistic browser UA is sent for the search endpoints specifically.
_BROWSER_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


def _ddg_uddg(href: str) -> str:
    """DuckDuckGo wraps result hrefs in a /l/?uddg= redirect — unwrap it."""
    m = re.search(r"[?&]uddg=([^&]+)", href)
    return urllib.parse.unquote(m.group(1)) if m else href


def _search_ddg_html(query: str) -> list:
    """Primary backend: html.duckduckgo.com. Returns [(title, url, snippet)]."""
    resp = requests.post("https://html.duckduckgo.com/html/",
                         data={"q": query}, headers=_BROWSER_UA, timeout=20)
    resp.raise_for_status()
    page = resp.text
    # Class names drift over time — match result__a OR any result anchor.
    links = re.findall(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        page, re.DOTALL)
    snippets = re.findall(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
                          page, re.DOTALL)
    hits = []
    for i, (href, title) in enumerate(links):
        snippet = _strip_html(snippets[i]) if i < len(snippets) else ""
        hits.append((_strip_html(title), _ddg_uddg(href), snippet))
    return hits


def _search_ddg_lite(query: str) -> list:
    """Fallback backend: lite.duckduckgo.com (simpler HTML, different blocking)."""
    resp = requests.post("https://lite.duckduckgo.com/lite/",
                         data={"q": query}, headers=_BROWSER_UA, timeout=20)
    resp.raise_for_status()
    links = re.findall(
        r'<a[^>]*class="[^"]*result-link[^"]*"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        resp.text, re.DOTALL)
    return [(_strip_html(title), _ddg_uddg(href), "") for href, title in links]


def _search_wikipedia(query: str) -> list:
    """Last-resort grounding: Wikipedia REST search. Authoritative, never a
    block page — better a real encyclopedia hit than a fabricated answer."""
    resp = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query,
                "format": "json", "srlimit": 5},
        headers=_UA, timeout=15)
    resp.raise_for_status()
    hits = []
    for r in resp.json().get("query", {}).get("search", []):
        title = r.get("title", "")
        url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
        hits.append((title, url, _strip_html(r.get("snippet", ""))))
    return hits


def _web_search(args, ctx):
    """Search the web with layered fallbacks. Each backend is tried in turn;
    the first that yields results wins. If ALL fail, return an explicit
    SEARCH_FAILED marker so the model grounds on fetch_url / says it doesn't
    know — it must NOT invent facts to fill the gap."""
    query = args["query"]
    errors = []
    for backend in (_search_ddg_html, _search_ddg_lite, _search_wikipedia):
        try:
            hits = backend(query)
        except Exception as exc:  # try the next backend, remember why
            errors.append(f"{backend.__name__}: {type(exc).__name__}")
            continue
        if hits:
            out = [f"{i+1}. {title}\n   {url}\n   {snippet}"
                   for i, (title, url, snippet) in enumerate(hits[:5])]
            return "\n".join(out)
    detail = "; ".join(errors) if errors else "all backends returned 0 results"
    return ("SEARCH_FAILED: no results from any backend "
            f"({detail}). Do NOT invent facts. Either call fetch_url on a known "
            "authoritative source (see the web_research_with_fallback skill) or "
            "tell the user you could not retrieve current data.")


def _fetch_url(args, ctx):
    resp = requests.get(args["url"], headers=_UA, timeout=25, allow_redirects=True)
    resp.raise_for_status()
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", resp.text)
    text = _strip_html(text)
    return text[:8000] + ("\n[truncated]" if len(text) > 8000 else "")


def _strip_html(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}


def _calc_eval(node):
    if isinstance(node, ast.Expression):
        return _calc_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_calc_eval(node.left), _calc_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_calc_eval(node.operand))
    raise ValueError(f"unsupported expression element: {ast.dump(node)[:60]}")


def _calculate(args, ctx):
    return repr(_calc_eval(ast.parse(args["expression"], mode="eval")))


def _use_skill(args, ctx):
    body = skills.load(args["name"])
    return body if body else f"ERROR: no skill named '{args['name']}'"


def _create_skill(args, ctx):
    name = skills.sanitize(args["name"])
    if not name:
        return "ERROR: invalid skill name"
    if skills.get(name) is not None:
        return f"ERROR: skill '{name}' already exists — use improve_skill to upgrade it"
    skills.save(name, args["description"], args["body"])
    ctx.store.log_event("system", "skill_created",
                        json.dumps({"name": name}, ensure_ascii=False), ctx.chat_id)
    return f"Skill '{name}' saved (v1, active). It will appear in your context from now on."


def _improve_skill(args, ctx):
    name = skills.sanitize(args["name"])
    meta = skills.get(name)
    if meta is None:
        return f"ERROR: no skill named '{name}'"
    new_version = skills.bump(name, args.get("description", ""), args["body"])
    ctx.store.log_event(
        "system", "skill_revision",
        json.dumps({"name": name, "version": new_version,
                    "changelog": args["changelog"]}, ensure_ascii=False),
        ctx.chat_id)
    return (f"Skill '{name}' upgraded to v{new_version} "
            f"(v{meta['version']} archived in skills/history/).")


def _run_python(args, ctx):
    proc = subprocess.run([sys.executable, "-c", args["code"]],
                          capture_output=True, text=True, timeout=10,
                          cwd=str(config.WORKSPACE_DIR))
    out = proc.stdout[-4000:]
    err = proc.stderr[-2000:]
    return f"exit={proc.returncode}\nstdout:\n{out}\nstderr:\n{err}"


# ---------------- digital-assistant tools ----------------

def _list_dir(args, ctx):
    return desktop.list_dir(args.get("path", "."))


def _read_file(args, ctx):
    return desktop.read_file(args["path"])


def _write_file(args, ctx):
    p = desktop.write_file(args["path"], args["content"])
    return f"Wrote {desktop._rel(p)} ({p.stat().st_size} bytes)."


def _make_dir(args, ctx):
    p = desktop.make_dir(args["path"])
    return f"Created folder {desktop._rel(p)}."


def _move_file(args, ctx):
    p = desktop.move(args["src"], args["dst"])
    return f"Moved to {desktop._rel(p)}."


def _delete_file(args, ctx):
    return desktop.delete(args["path"])


def _search_files(args, ctx):
    return desktop.search_files(args["query"], args.get("path", "."))


def _create_document(args, ctx):
    if not args.get("sections") and not args.get("source_path"):
        return ("ERROR: provide sections OR source_path (for long reports: "
                "write_file .md first, then create_document with source_path).")
    # Weaker models pass structured args as JSON strings — accept both, or the
    # generator crashes with 'str has no attribute get' and the user, expecting
    # a file, silently gets nothing.
    sections = _coerce_json(args.get("sections", []), list)
    table = args.get("table")
    if table is not None:
        table = _coerce_json(table, dict) or None
    chart = args.get("chart")
    if chart is not None:
        chart = _coerce_json(chart, dict) or None
    p = desktop.create_document(
        filename=args["filename"], doc_format=args["format"], title=args["title"],
        sections=sections, table=table, chart=chart,
        source_path=args.get("source_path"))
    if not p.is_file() or p.stat().st_size == 0:
        return "ERROR: document file was not created"
    ctx.deliver_file(p)
    return (f"Document created and sent to the user: {desktop._rel(p)} "
            f"({p.stat().st_size} bytes).")


def _coerce_json(value, expected_type):
    """Some models pass structured args as JSON strings — accept both."""
    if isinstance(value, str) and value.strip():
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return expected_type()
    return value if isinstance(value, expected_type) else expected_type()


def _send_file(args, ctx):
    p = desktop.resolve(args["path"], must_exist=True)
    ctx.deliver_file(p)
    return f"Sending {desktop._rel(p)} to the user."


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _view_image(args, ctx):
    p = desktop.resolve(args["path"], must_exist=True)
    if p.suffix.lower() not in _IMAGE_EXTS:
        return (f"ERROR: '{p.suffix}' is not a supported image type "
                f"({', '.join(sorted(_IMAGE_EXTS))})")
    if p.stat().st_size > config.MAX_IMAGE_BYTES:
        return "ERROR: image too large to view (max ~4.5MB)"
    ctx.pending_images.append(str(p))
    return f"Image attached: {desktop._rel(p)} — you can now see it in this conversation."


def _git(args, ctx):
    return desktop.git(args["repo_path"], args["command"])


def _send_email(args, ctx):
    from . import connectors

    return connectors.send_email(args["to"], args.get("subject", ""),
                                 args.get("body", ""))


def _run_shell(args, ctx):
    return desktop.run_shell(args["command"])


_HANDLERS = {
    "recall": _recall,
    "remember": _remember,
    "belief_history": _belief_history,
    "manage_goal": _manage_goal,
    "schedule_reminder": _schedule_reminder,
    "list_reminders": _list_reminders,
    "schedule_task": _schedule_task,
    "list_tasks": _list_tasks,
    "cancel_task": _cancel_task,
    "web_search": _web_search,
    "fetch_url": _fetch_url,
    "calculate": _calculate,
    "use_skill": _use_skill,
    "create_skill": _create_skill,
    "improve_skill": _improve_skill,
    "list_dir": _list_dir,
    "read_file": _read_file,
    "write_file": _write_file,
    "make_dir": _make_dir,
    "move_file": _move_file,
    "delete_file": _delete_file,
    "search_files": _search_files,
    "create_document": _create_document,
    "send_file": _send_file,
    "view_image": _view_image,
    "git": _git,
    "send_email": _send_email,
    "run_python": _run_python,
    "run_shell": _run_shell,
}
