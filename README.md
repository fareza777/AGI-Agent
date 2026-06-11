# 🧠 Engram — an AI agent that never forgets

> *Hermes remembers until the context window ends. OpenClaw remembers until the
> memory file bloats. Engram remembers — period.*

**Engram** is a personal AI agent with **effectively permanent memory**, built
on the architecture described in [DESIGN.md](DESIGN.md). It runs on the Claude
API (Opus 4.8), stores its mind in SQLite, and talks to you through a
**Telegram bot**.

The core idea, in one sentence: **the LLM is a stateless reasoning CPU; the
agent's mind lives in a database-backed substrate outside the model.** Context
windows are caches assembled per-turn — never the system of record.

```
You (Telegram) ──▶ Working-Memory Composer ──▶ Claude (Opus 4.8) ──▶ reply
                        ▲        ▲       ▲
                  Identity   Claims    Goals          ← what the agent believes
                   Core      (S2/S4)   (S7)
                        ▲
                  Consolidation Engine (S3, background "sleep cycle")
                        ▲
                  Episodic Event Log (S1, append-only, permanent)
```

## What it actually does

- **Remembers everything.** Every message is appended to an immutable event
  log. Nothing is ever deleted or compacted away.
- **Learns while you sleep.** A background *consolidation engine* periodically
  distills raw conversation into structured **claims** — atomic beliefs like
  `user | works_at | Globex [fact, conf 0.95, since 2026-06-10]` — each with
  provenance (which events it came from), confidence, and validity dates.
- **Notices when things change.** Claims live in *(subject, predicate)* slots.
  Tell it in March you're vegetarian and in June that you eat steak, and the
  old belief is *superseded*, not overwritten — `/history user dietary_preference`
  shows the full timeline of what it believed and when.
- **Generates insights.** A reflection pass periodically looks across all
  beliefs and recent episodes and writes *insight* claims — patterns no single
  message contains ("user's interest shifted from X to Y").
- **Keeps goals across sessions.** `/goal`, `/goals`, `/done` — stored in the
  substrate, injected into every relevant context, alive for months.
- **Has a stable identity.** Its persona lives in [`identity/CORE.md`](identity/CORE.md),
  version-controlled in git, loaded verbatim into every turn, and never edited
  at runtime. Identity drift is a diff you can review, not an accident.
- **Costs stay flat as memory grows.** Each turn retrieves a *budgeted* slice
  of memory (BM25 over claims + episodes, ranked, capped) instead of stuffing
  the whole history into the prompt.
- **A real digital assistant, not just a chatbot.** Generates documents
  (docx/xlsx/pdf), manages files, inspects git repos — and delivers produced
  files straight to your Telegram chat.

## Tools

The agent decides when to use these mid-conversation ("buatkan laporan
penjualan dalam docx", "ingatkan aku besok jam 9", "cek status repo di
/path/repo") — no commands needed.

**Memory-native tools** — these operate on the structured claim store, which is
why a Hermes/OpenClaw-style agent (flat text memory) can't replicate them:

| Tool | What it does |
|---|---|
| `recall` | Re-enters the retrieval pipeline mid-reasoning — iterative memory search, not one-shot |
| `remember` | Writes a belief into its slot *immediately*, with automatic supersession of the old value |
| `belief_history` | Reads the full timeline of one belief — "what did I believe and when" |
| `manage_goal` | Add/complete/list goals in the persistent goal tree |
| `schedule_reminder` / `list_reminders` | Future-dated messages, delivered by a background scheduler even days later |
| `schedule_task` / `list_tasks` / `cancel_task` | **Proactive autonomy**: "kirim analisis saham tiap pagi jam 7" — at the due time the agent *executes the prompt itself* (with all tools, including document generation) and sends you the result; supports once / hourly / daily / weekly / every:N-minutes, survives restarts, skips missed runs instead of replaying them |

**Digital-assistant tools** — turn it into a do-things assistant:

| Tool | What it does |
|---|---|
| `create_document` | Generate a real **docx / xlsx / pdf / md / html / csv** and send it to you over Telegram |
| `view_image` | **Vision**: look at an image in the workspace (a photo you sent, a chart) and analyze it |
| `read_file` / `write_file` / `list_dir` / `search_files` / `make_dir` / `move_file` / `delete_file` | Manage files in a sandboxed workspace |
| `git` | Read-only repo inspection (status, log, diff, branch, …) |
| `send_file` | Deliver any existing workspace file to your chat |
| `run_shell` | Shell commands in the workspace — **off by default** (`ENGRAM_ENABLE_SHELL_TOOL=1`) |

**World-facing tools** — parity with Hermes-style agents:

| Tool | What it does |
|---|---|
| `web_search` | DuckDuckGo search, no API key needed |
| `fetch_url` | Fetch a page, return readable text |
| `calculate` | Exact arithmetic via a safe AST evaluator (no `eval`) |
| `use_skill` | Load a markdown skill from `skills/` on demand |
| `run_python` | Run model-written code in a subprocess — **off by default** (`ENGRAM_ENABLE_CODE_TOOL=1`) |

**Safety:** all file/document tools are sandboxed to the workspace (plus any
roots you opt into via `ENGRAM_ALLOWED_DIRS`); path traversal is blocked. `git`
is read-only. Code/shell execution is opt-in.

**Document generation never fails on dependencies.** With
`python-docx`/`openpyxl`/`reportlab` installed you get richly styled output;
without them, built-in zero-dependency generators (`engram/docgen.py`) write
valid docx/xlsx/pdf using only the standard library — plainer styling, but the
file always arrives. `/doctor` shows which engine each format is using, plus
provider/workspace/git health.

**Honest tool reporting.** Tool failures surface as ❌ lines in the live
activity feed, `create_document` verifies the file actually exists on disk
before claiming success, and the system prompt hard-forbids telling you a file
was sent unless the tool result confirmed it.

**Clean Telegram output:** model replies are rendered to Telegram's HTML
(tidy bullets, bold, code), markdown tables are flattened to readable lines
instead of walls of `|`, and any leaked `<think>` reasoning from
reasoning-models is stripped before it reaches you.

**Send files TO the agent:** drop a document or photo into the chat — it lands
in `workspace/inbox/` (sanitized filenames, deduped) and the agent is told
about it in the same turn, so "ini notulen rapat, rapikan jadi minutes" works:
it reads the file, processes it, and can send back a formatted document.

**Vision:** photos you send are passed to the model as images in the same turn
— "ini screenshot error-nya, kenapa ya?" just works. The `view_image` tool
lets the agent look at any workspace image later. On OpenAI-compatible
providers whose model lacks vision, the request automatically retries without
the image instead of failing.

**Live activity feed (Hermes-style):** while the agent works you see compact
status lines in the chat — `🔎 web_search: berita AI`, `✍️ write_file:
laporan.md`, `💻 run_shell: pip install …` — one per tool call, sent silently
(no notification sound). Disable with `ENGRAM_SHOW_ACTIVITY=0`.

**Learning loop (S10):** lessons distilled from corrections and failed tool
calls become `lesson` claims, and the five most recent are *always* in the
agent's context — not only when keywords match — so the same mistake isn't
repeated next week.

Every tool call is appended to the episodic event log, so the consolidation
engine can distill *lessons* from what worked and what failed — tool use feeds
the memory, which improves future tool use.

### Skills — and how the agent learns new ones

Drop a markdown file into `skills/` and it's live — no restart logic, no code:

```markdown
---
name: my_skill
description: One line the model sees in every context.
status: active
version: 1
---
Full instructions, loaded only when the agent calls use_skill("my_skill").
```

Only the name+description index sits in the prompt; the body loads on demand
(progressive disclosure), so 50 skills cost barely more than 2. Ships with a
working set — `docx_report`, `spreadsheet`, `repo_review`, `file_organizer`,
`daily_briefing`, `meeting_notes`, `research_brief`, `weekly_review` — that
orchestrate the tools above. `/skills` lists them.

But hand-written skills are just the floor. **The agent learns skills from
experience** (S8 Skill Compiler, staged rollout per the design):

1. **Taught in conversation → active immediately.** Tell it *"kalau aku minta
   laporan mingguan, formatnya begini: ..."* and it calls `create_skill` —
   the procedure is saved and you never have to explain it again.
2. **Upgraded from experience → versioned.** When a skill's steps prove wrong
   or you correct how a task should be done, the agent calls `improve_skill`:
   the old version is archived to `skills/history/<name>.v<N>.md`, the version
   bumps, and the changelog lands in the event log. Every revision is
   auditable and reversible.
3. **Mined from patterns → draft, needs your approval.** A background job
   periodically scans the event log (messages *and* tool calls) for procedures
   the agent has repeated or fumbled, and drafts a skill. Drafts are invisible
   to the model until you run `/approve <name>` — the agent proposes, you
   decide. The bar is deliberately high; most passes produce nothing.

Skill lifecycle commands: `/skills` (list with version/draft badges),
`/skill <name>` (inspect), `/approve <name>` (activate a draft). `/reflect`
also runs a mining pass on demand.

## Quickstart

You need two things: an [Anthropic API key](https://platform.claude.com/) and a
Telegram bot token (message [@BotFather](https://t.me/BotFather), `/newbot`,
copy the token).

```bash
git clone https://github.com/fareza777/AGI-Agent.git
cd AGI-Agent
pip install -r requirements.txt

cp .env.example .env       # then edit: ANTHROPIC_API_KEY + TELEGRAM_BOT_TOKEN
python run.py
```

Open your bot in Telegram and say hi. That's it — no webhook, no server setup;
it uses long polling, so it runs anywhere Python runs (laptop, VPS, Raspberry Pi).

Run the offline tests (no API key needed):

```bash
python tests/test_substrate.py
```

## Talking to it

Just chat — in any language. Tell it about yourself, your projects, your plans.
Then come back tomorrow, next week, next year, and ask. Commands:

| Command | What it does |
|---|---|
| `/memory <query>` | Search what the agent believes, with confidence + dates |
| `/history <subject> <attribute>` | Timeline of one belief slot — see supersession in action |
| `/goals` / `/goal <text>` / `/done <id>` | Long-term goal tree |
| `/reminders` | Pending scheduled reminders |
| `/skills` | List available skills |
| `/reflect` | Force a consolidation + insight pass right now |
| `/identity` | Show the version-controlled Identity Core |
| `/stats` | Active beliefs, pending events, models in use |

### A 60-second demo of the memory in action

```
You:    aku kerja di Acme Corp sebagai backend engineer
Engram: Noted! ...
You:    /reflect
Engram: Konsolidasi: 2 event diproses, 2 keyakinan baru, 0 diperbarui.
        ...weeks later...
You:    btw aku pindah kerja ke Globex
You:    /reflect
Engram: Konsolidasi: ... 1 diperbarui.
You:    /history user works_at
Engram: Riwayat user.works_at:
        • 2026-06-10: Acme Corp (diganti 2026-07-02)
        • 2026-07-02: Globex (AKTIF)
```

## How the code maps to the design

[DESIGN.md](DESIGN.md) specifies ten subsystems. This repo is the minimal
runnable core — every implemented piece follows the design's data model so the
rest can be layered on without rewrites:

| Design subsystem | Status | Where |
|---|---|---|
| S1 Episodic Event Log | ✅ implemented | `engram/store.py` (`events`, append-only) |
| S2 Semantic Graph (claims) | ✅ implemented | `engram/store.py` (`claims`: bitemporal, provenance, confidence) |
| S3 Consolidation Engine | ✅ implemented | `engram/consolidator.py` + background thread in `engram/agent.py` |
| S4 Contradiction Detector | ✅ slot-based supersession | `Store.add_claim()` |
| S5 Identity Core | ✅ implemented | `identity/CORE.md` + `engram/identity.py` |
| S6 Working-Memory Composer | ✅ implemented (BM25) | `engram/composer.py` |
| S7 Goal Tree | ✅ flat goals | `engram/store.py`, `/goal` commands + `manage_goal` tool |
| S9 Insight Generator | ✅ minimal | `consolidator.reflect()` |
| Tool layer + skill library | ✅ implemented | `engram/tools.py`, `engram/skills.py`, `skills/` |
| S8 Skill Compiler | ✅ staged rollout | `engram/skill_compiler.py` — create_skill/improve_skill tools + background mining with draft→approve gate |
| S10 Learning Loop | ✅ lessons loop | consolidation distills corrections/failures into `lesson` claims; recent lessons are pinned into every context (`composer.py`); explicit outcome scoring comes later |
| Proactive scheduling (S7 scheduler) | ✅ implemented | recurring agent tasks: the scheduler runs full agent turns and delivers results + files |

Deliberate simplifications in this minimal version, and the upgrade path:

- **SQLite instead of Postgres** — same schema, swap when multi-process scale
  is needed. FTS5/BM25 stands in for hybrid (vector + keyword) retrieval;
  adding pgvector later changes only `composer.py`.
- **Flat goals instead of a goal tree** — the table gains a `parent_id` when
  decomposition lands.
- **Supersession-only contradiction handling** — the dispute/refinement
  branches from S4 come with the LLM judgment step.

## Using OpenRouter, MiniMax, or any OpenAI-compatible API

The default provider is Anthropic (Claude Opus 4.8 via the official SDK), but
the LLM layer is pluggable — no code changes needed, just `.env`:

```bash
# OpenRouter
ENGRAM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
ENGRAM_CHAT_MODEL=minimax/minimax-m2        # any OpenRouter model ID works

# MiniMax direct
ENGRAM_PROVIDER=minimax
MINIMAX_API_KEY=...
ENGRAM_CHAT_MODEL=MiniMax-M2

# Anything else with an OpenAI-compatible /chat/completions endpoint
ENGRAM_PROVIDER=openai
ENGRAM_LLM_API_KEY=...
ENGRAM_OPENAI_BASE_URL=https://your-endpoint/v1
ENGRAM_CHAT_MODEL=your-model-id
```

Notes:
- Model IDs are provider-specific — for non-Anthropic providers you must set
  `ENGRAM_CHAT_MODEL` explicitly (the startup check will tell you if you forget).
- The OpenAI-compatible path **streams (SSE)** and accumulates deltas
  client-side — long reasoning turns (MiniMax M2/M3, DeepSeek-R1) aren't
  killed by gateway idle-timeouts. Transient failures retry with backoff.
- Histories are normalized for strict providers (roles forced to alternate),
  assistant echoes are reduced to standard fields, and MiniMax gets
  `reasoning_content` passed back per their multi-turn recommendation.
- Memory consolidation needs the model to return clean JSON. On Anthropic this
  is enforced by native structured outputs; on other providers Engram instructs
  the model and parses defensively — strong instruction-following models
  (MiniMax-M2, large open models) work well, very small models may produce
  noisier memory.
- If your MiniMax account uses a different endpoint, override
  `ENGRAM_OPENAI_BASE_URL`.

## Configuration

Everything via environment variables (see [.env.example](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | required |
| `ENGRAM_PROVIDER` | `anthropic` | `anthropic`, `openrouter`, `minimax`, or `openai` |
| `ANTHROPIC_API_KEY` | — | required for the `anthropic` provider |
| `ENGRAM_LLM_API_KEY` / `OPENROUTER_API_KEY` / `MINIMAX_API_KEY` | — | key for the other providers |
| `ENGRAM_OPENAI_BASE_URL` | per provider | OpenAI-compatible base URL |
| `ENGRAM_CHAT_MODEL` | `claude-opus-4-8` (anthropic only) | model for replies |
| `ENGRAM_CONSOLIDATE_MODEL` | same as chat model | model for background jobs — a cheaper model cuts costs |
| `ENGRAM_ENABLE_CODE_TOOL` | `0` | set `1` to enable the `run_python` tool (runs model-written code on your machine — only enable if you trust everyone who can message the bot) |
| `ENGRAM_DB_PATH` | `./engram.db` | where the mind lives — **back this file up** |
| `ENGRAM_CONSOLIDATE_INTERVAL_MIN` | `30` | sleep-cycle period |
| `ENGRAM_ALLOWED_CHAT_IDS` | open | comma-separated chat ID allowlist |

## Project layout

```
DESIGN.md               the full architecture (read this first)
identity/CORE.md        S5 — the agent's version-controlled self
run.py                  entry point
skills/                 markdown skill library (drop a .md file in, it's live)
engram/
  store.py              S1 + S2 + S7 — SQLite substrate, FTS5 retrieval, reminders
  consolidator.py       S3 + S4 + S9 — sleep cycle, supersession, insights
  composer.py           S6 — budgeted context assembly
  tools.py              tool dispatcher: memory-native + digital-assistant + web
  desktop.py            files, document generation (docx/xlsx/pdf), git, shell
  skills.py             skill index/loader (progressive disclosure)
  skill_compiler.py     S8 — learn/upgrade skills from experience
  agent.py              orchestrator + sleep-cycle + reminder scheduler threads
  llm.py                provider layer: Anthropic SDK or OpenAI-compatible, with tool loop
  telegram_format.py    markdown → clean Telegram HTML, table flattening
  telegram_bot.py       the Telegram interface (renders HTML, delivers files)
tests/test_substrate.py offline tests for memory, tools, desktop, formatting
```
