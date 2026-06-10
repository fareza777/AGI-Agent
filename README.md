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
| S7 Goal Tree | ✅ flat goals | `engram/store.py`, `/goal` commands |
| S9 Insight Generator | ✅ minimal | `consolidator.reflect()` |
| S8 Skill Compiler | 🔜 roadmap | — |
| S10 Learning Loop (outcomes) | 🔜 roadmap | — |

Deliberate simplifications in this minimal version, and the upgrade path:

- **SQLite instead of Postgres** — same schema, swap when multi-process scale
  is needed. FTS5/BM25 stands in for hybrid (vector + keyword) retrieval;
  adding pgvector later changes only `composer.py`.
- **Flat goals instead of a goal tree** — the table gains a `parent_id` when
  decomposition lands.
- **Supersession-only contradiction handling** — the dispute/refinement
  branches from S4 come with the LLM judgment step.

## Configuration

Everything via environment variables (see [.env.example](.env.example)):

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | required |
| `TELEGRAM_BOT_TOKEN` | — | required |
| `ENGRAM_CHAT_MODEL` | `claude-opus-4-8` | model for replies (adaptive thinking) |
| `ENGRAM_CONSOLIDATE_MODEL` | `claude-opus-4-8` | model for background jobs — set `claude-haiku-4-5` to cut costs |
| `ENGRAM_DB_PATH` | `./engram.db` | where the mind lives — **back this file up** |
| `ENGRAM_CONSOLIDATE_INTERVAL_MIN` | `30` | sleep-cycle period |
| `ENGRAM_ALLOWED_CHAT_IDS` | open | comma-separated chat ID allowlist |

## Project layout

```
DESIGN.md               the full architecture (read this first)
identity/CORE.md        S5 — the agent's version-controlled self
run.py                  entry point
engram/
  store.py              S1 + S2 + S7 — SQLite substrate, FTS5 retrieval
  consolidator.py       S3 + S4 + S9 — sleep cycle, supersession, insights
  composer.py           S6 — budgeted context assembly
  agent.py              orchestrator + background thread
  llm.py                Claude API wrapper (chat + structured extraction)
  telegram_bot.py       the Telegram interface
tests/test_substrate.py offline tests for the whole memory layer
```
