"""Tool layer — what the agent can *do*, not just say.

Two classes of tools:

Memory-native (the part Hermes-style agents can't have, because their memory
is flat text):
  recall          — re-enter the retrieval pipeline mid-reasoning (iterative S6)
  remember        — write an explicit claim into a belief slot (with supersession)
  belief_history  — read the full timeline of one belief slot
  manage_goal     — add/complete/list goals in the persistent goal tree
  schedule_reminder / list_reminders — future-dated messages the scheduler delivers

World-facing (parity with Hermes-style agents):
  web_search      — DuckDuckGo, no API key needed
  fetch_url       — fetch a page and return readable text
  calculate       — safe arithmetic evaluator
  use_skill       — load the full body of a markdown skill on demand
  run_python      — sandboxed-ish subprocess; OFF by default (ENGRAM_ENABLE_CODE_TOOL=1)

Every tool execution is appended to the episodic event log, so the
consolidation engine can later distill lessons from what worked and failed.
"""

import ast
import html
import json
import logging
import operator
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone

import requests

from . import config, skills
from .store import Store

log = logging.getLogger("engram.tools")


class ToolContext:
    def __init__(self, store: Store, chat_id: str):
        self.store = store
        self.chat_id = chat_id


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
    ]
    if config.ENABLE_CODE_TOOL:
        specs.append(_spec(
            "run_python",
            "Run a short Python script in a subprocess (10s timeout) and return "
            "its stdout/stderr. Use print() for output.",
            {"code": {"type": "string"}}, ["code"]))
    return specs


def openai_tool_specs() -> list:
    return [{"type": "function",
             "function": {"name": s["name"], "description": s["description"],
                          "parameters": s["input_schema"]}}
            for s in tool_specs()]


# ---------------- dispatcher ----------------

def run_tool(name: str, args: dict, ctx: ToolContext) -> str:
    handler = _HANDLERS.get(name)
    if handler is None or (name == "run_python" and not config.ENABLE_CODE_TOOL):
        return f"ERROR: unknown tool '{name}'"
    try:
        result = handler(args or {}, ctx)
    except Exception as exc:  # tool errors go back to the model, not up the stack
        log.exception("tool %s failed", name)
        result = f"ERROR: {type(exc).__name__}: {exc}"
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


# ---------------- world-facing tools ----------------

_UA = {"User-Agent": "Mozilla/5.0 (compatible; EngramAgent/0.1)"}


def _web_search(args, ctx):
    resp = requests.post("https://html.duckduckgo.com/html/",
                         data={"q": args["query"]}, headers=_UA, timeout=20)
    resp.raise_for_status()
    page = resp.text
    links = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                       page, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.DOTALL)
    out = []
    for i, (href, title) in enumerate(links[:5]):
        url = href
        m = re.search(r"[?&]uddg=([^&]+)", href)
        if m:
            url = urllib.parse.unquote(m.group(1))
        snippet = _strip_html(snippets[i]) if i < len(snippets) else ""
        out.append(f"{i+1}. {_strip_html(title)}\n   {url}\n   {snippet}")
    return "\n".join(out) if out else "No results."


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


def _run_python(args, ctx):
    proc = subprocess.run([sys.executable, "-c", args["code"]],
                          capture_output=True, text=True, timeout=10)
    out = proc.stdout[-4000:]
    err = proc.stderr[-2000:]
    return f"exit={proc.returncode}\nstdout:\n{out}\nstderr:\n{err}"


_HANDLERS = {
    "recall": _recall,
    "remember": _remember,
    "belief_history": _belief_history,
    "manage_goal": _manage_goal,
    "schedule_reminder": _schedule_reminder,
    "list_reminders": _list_reminders,
    "web_search": _web_search,
    "fetch_url": _fetch_url,
    "calculate": _calculate,
    "use_skill": _use_skill,
    "run_python": _run_python,
}
