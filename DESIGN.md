# ENGRAM: An Agent Architecture Beyond Hermes and OpenClaw

**A practical design for a continuously-learning, permanent-memory AI agent built with today's technology.**

---

## Part 1 — Why Hermes and OpenClaw Hit a Wall

Both agents share the same generation-one architecture: *an LLM in a loop, with memory
bolted on as text*. Their specific limitations:

### L1. The context window is the only working memory — and also the long-term memory
Memory lives in flat files (`MEMORY.md`, daily logs, soul/persona files) that get loaded
into the prompt. Everything the agent "knows" must fit in ~200K tokens. Past that point
memory is silently truncated or compacted into lossy summaries. There is no architectural
separation between *what the agent knows* and *what the agent is thinking about right now*.

### L2. Memory is write-only; nothing is processed at write time
Experiences are appended raw. Nothing deduplicates, links, scores, or reconciles new
memories against old ones. After a year, the memory store is a landfill: the signal is in
there, but every retrieval has to dig through noise, and cost grows with total history.

### L3. Retrieval is keyword grep or naive vector search
Neither can answer "what changed in my user's priorities between 2024 and 2026?" or
"which past projects failed for similar reasons?" There is no temporal index, no entity
graph, no way to traverse *relationships* between memories.

### L4. No contradiction handling
If the user said "I'm vegetarian" in March and "grill me a steak" in June, both strings
sit in the store. Whichever one retrieval happens to surface wins. There is no notion of
a *claim slot* that can hold only one current value, no validity intervals, no supersession.

### L5. No learning loop
Behavior is fixed by the system prompt and hand-written skills. The agent repeats the same
mistakes forever because failures are never captured as structured lessons, and successes
are never compiled into reusable procedures. "Learning" means the user manually edits a
markdown file.

### L6. Identity drift
Persona is a static prompt file that anyone (including the agent itself, mid-task) can
mutate ad hoc. Over months of self-edits and compactions, the agent's values and
self-model drift with no change control, no history, no rollback.

### L7. The agent only thinks when poked
Both are request-driven. No background process consolidates memory, reviews plans,
generates insights, or notices that a commitment made three weeks ago is now due. All
cognition is crammed into user-facing latency.

### L8. Plans die with the session
Long-term plans live in chat context or scratch files. There is no persistent goal
structure that survives sessions, gets reviewed on a schedule, and connects daily actions
to multi-month objectives.

### L9. No provenance or confidence
A user's explicit statement, an agent's guess, and a random web page all become
indistinguishable lines of text. The agent cannot reason about *how much to trust* a
memory or *where it came from*, which makes it both gullible and unable to gracefully
revise beliefs.

These are not tuning problems. They are consequences of the core design decision —
**memory as text in context** — and fixing them requires a different architecture.

---

## Part 2 — The ENGRAM Architecture

**Core principle:** the LLM is a *stateless reasoning CPU*. All state — memory, identity,
goals, skills — lives in a database-backed cognitive substrate outside the model. Context
windows are *caches assembled per-task*, never the system of record.

```
                         ┌─────────────────────────────┐
                         │   LLM (stateless reasoner)  │
                         └──────────────▲──────────────┘
                                        │ assembled context
                         ┌──────────────┴──────────────┐
                         │  S6 Working-Memory Composer │
                         └──▲───────▲───────▲───────▲──┘
                            │       │       │       │
        ┌───────────┐ ┌─────┴───┐ ┌─┴─────┐ ┌┴──────────┐
        │ S5        │ │ S2      │ │ S7    │ │ S8        │
        │ Identity  │ │ Semantic│ │ Goal  │ │ Skill     │
        │ Core      │ │ Graph   │ │ Tree  │ │ Library   │
        └───────────┘ └────▲────┘ └───────┘ └───────────┘
                           │ claims distilled from episodes
   ┌───────────────────────┴────────────────────────────────┐
   │ S3 Consolidation Engine + S4 Contradiction Detector    │
   │ S9 Insight Generator + S10 Learning Loop (background)  │
   └───────────────────────▲────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │ S1 Episodic Event Log   │  (append-only, permanent)
              └─────────────────────────┘
```

Everything below is buildable today with: Postgres (+ pgvector), an object store, a job
queue (e.g. a worker pool with cron), embedding models, and frontier LLM APIs. No new
research required.

---

### S1. Episodic Event Log — *permanent memory foundation*

**Purpose.** An immutable, append-only record of every experience: messages, tool calls,
observations, decisions, outcomes. This is the ground truth nothing else can corrupt.

**How it works.**
- Every event is a row: `(id, timestamp, session_id, actor, type, content_ref, metadata)`.
  Large payloads (files, transcripts) go to content-addressed object storage; the row
  holds a hash reference.
- Events are never edited or deleted — corrections are new events that reference old ones.
- Tiered storage: recent events in Postgres (hot), older events batch-archived to
  Parquet/object storage (cold) but still indexed and queryable. A decade of a personal
  agent's life is on the order of tens of GB — trivial for object storage. This is what
  makes memory *effectively permanent* and *near-unlimited*: cost per GB-year is cents.

**Why it's better.** Hermes/OpenClaw conflate the record of experience with the working
representation of it; when they compact, history is destroyed. ENGRAM can re-derive any
higher-level memory from the log — bad summaries, wrong beliefs, and broken indexes are
all recoverable errors, because the source of truth is never lossy.

**Difficulty: Easy.** Append-only logs and object stores are commodity engineering.

---

### S2. Semantic Graph — *structured beliefs, not text soup*

**Purpose.** Hold the agent's distilled knowledge as discrete, addressable **claims**
about **entities**, so beliefs can be queried, compared, updated, and trusted individually.

**How it works.**
- Entities: people, projects, tools, places, recurring topics. Each has a stable ID and
  embedding.
- Claims: `(subject_entity, predicate, value, confidence, source_event_ids[],
  valid_from, valid_to, superseded_by)`. Example:
  `(user, dietary_preference, "vegetarian", 0.95, [evt_8812], 2024-03-02, 2026-06-01, claim_4410)`.
- **Bitemporal**: each claim records both *when it was true in the world* and *when the
  agent learned it*. "The user used to work at X" and "the user works at Y" coexist
  without conflict.
- Every claim links back to the episodic events it was derived from (provenance), and
  carries a confidence score based on source type (user statement > agent inference >
  third-party content) and corroboration count.
- Relations between entities form a graph (`user —manages→ project_X`), enabling
  multi-hop queries ("everything connected to the failed Q3 launch").
- Implementation: plain Postgres tables + pgvector for entity/claim embeddings. A
  dedicated graph DB is optional, not required.

**Why it's better.** This is the single biggest break from Hermes/OpenClaw. Text-blob
memory can only be *searched*; claims can be *reasoned over*: detect contradictions
(L4), track change over time (L3), weigh trust (L9), and answer questions across years
of history by querying structure instead of stuffing transcripts into context.

**Difficulty: Medium.** The schema is straightforward; the care goes into the extraction
prompts (S3) and entity resolution (deduplicating "Bob", "Robert", "my manager").

---

### S3. Consolidation Engine — *the sleep cycle*

**Purpose.** Continuously transform raw episodes into structured, deduplicated,
decay-scored knowledge — the write-time processing Hermes/OpenClaw completely lack (L2).

**How it works.** Background jobs (cheap model for bulk passes, frontier model for hard
cases) run on schedules, like sleep stages:
1. **Hourly — extraction:** new episodes → candidate claims, entity mentions, outcome
   labels. Each candidate carries provenance links.
2. **Daily — integration:** entity resolution; merge duplicate claims (raising
   confidence); route conflicting claims to S4; write a compact daily narrative summary
   (itself a derived memory pointing at its source events).
3. **Weekly — re-ranking:** update each memory's *salience score* =
   f(recency, retrieval frequency, emotional/importance markers, goal relevance).
   Low-salience detail demotes to cold tier — still stored, just not competing for
   retrieval. Nothing is ever deleted; forgetting is *de-prioritization*.
4. **Monthly — reorganization:** re-cluster topics, split overgrown entities, rebuild
   stale summaries from the log.

**Why it's better.** Retrieval quality and cost stay roughly constant as history grows,
because retrieval competes over distilled claims and summaries, not raw history. The
landfill problem (L2) disappears: a year-old fact is exactly as accessible as
yesterday's, if it's still salient.

**Difficulty: Medium.** It's an ETL pipeline with LLM steps. The engineering risk is
extraction quality, which is mitigated by provenance: every consolidation is auditable
and re-runnable against the immutable log.

---

### S4. Contradiction Detector — *belief hygiene*

**Purpose.** Notice when new information conflicts with existing beliefs, and resolve or
escalate — instead of silently holding both (L4).

**How it works.**
- Trigger: whenever S3 writes a claim into a `(subject, predicate)` slot that already
  holds an active claim with a different value.
- Cheap structural check first (same slot, different value), then an LLM judgment:
  *supersession* (preference changed → close old claim's `valid_to`, link
  `superseded_by`), *refinement* (both true at different scopes → narrow the claims),
  or *genuine conflict* (sources disagree → mark both `disputed`, lower confidence,
  enqueue for S9 reflection or a user question).
- A periodic sweep also samples high-confidence claim pairs within an entity
  neighborhood and asks an LLM to spot *implicit* contradictions structure alone misses
  ("works in Berlin" + "commutes to the Paris office daily").
- Disputed claims surface their dispute status whenever retrieved, so the reasoning LLM
  knows the ground is uncertain.

**Why it's better.** Hermes/OpenClaw resolve contradictions by retrieval lottery. ENGRAM
resolves them by explicit state transition with full history — the agent can answer
"when did I learn the user stopped being vegetarian, and how do I know?"

**Difficulty: Medium.** The slot mechanism is easy; the implicit-contradiction sweep is
an accuracy-tuning exercise, and it degrades gracefully (worst case: some contradictions
go unnoticed — which is the *status quo* for existing agents).

---

### S5. Identity Core — *stable self under change control*

**Purpose.** Keep the agent's values, persona, commitments, and self-model stable over
years while still allowing deliberate growth (fixes L6).

**How it works.**
- A small (~2–4K token), versioned document: values and hard constraints, communication
  style, standing commitments, relationship summary with the user, self-model
  ("what I'm good and bad at" — fed by S10's track record).
- Stored in git or a versioned table. **The agent cannot edit it inline during a task.**
  Changes happen only through a dedicated reflection workflow (part of S9) that proposes
  a diff with cited evidence, applies rate limits (e.g. max one values change per week),
  and optionally requires user approval for the values section.
- Loaded verbatim into *every* context assembly — it is the one unconditional inclusion.

**Why it's better.** Identity stability becomes a *mechanical guarantee* (versioning +
change control + rate limiting) rather than a hope that the persona file survives
compaction. The full edit history means drift is visible and reversible.

**Difficulty: Easy.** It's a versioned config file with a review process. High value for
almost no engineering.

---

### S6. Working-Memory Composer — *context as a budgeted cache*

**Purpose.** Assemble the best possible context window for the current task from the
substrate, under an explicit token budget (fixes L1).

**How it works.** Per turn (or per agent step):
1. Fixed allocations: Identity Core (~3K), active task state from S7 (~4K), recent
   conversation tail (~8K).
2. Query formulation: an LLM pass turns the current task into retrieval queries (entity
   lookups, semantic search, temporal ranges).
3. Hybrid retrieval against S2 + episodic summaries: BM25 + vector + graph expansion +
   temporal filter, fused and re-ranked by `relevance × salience × confidence`.
4. Budgeted packing (~20–40K tokens of memory): claims first (dense, trusted), then
   summaries, then raw episode excerpts only when fine detail is needed.
5. Every retrieved item is rendered **with its provenance and confidence**
   (`[claim, conf 0.95, from user 2026-03-02]`), so the reasoner can weigh evidence.
6. If the reasoner determines mid-task that it lacks information, it calls a
   `recall(query)` tool that re-enters this pipeline — retrieval is iterative, not
   one-shot.

**Why it's better.** Hermes/OpenClaw ship their whole memory file and pray. ENGRAM ships
a curated brief. Token cost per turn is *flat regardless of total history size*, and the
context is higher quality because it's claims and summaries, not transcript sludge.

**Difficulty: Medium.** Hybrid retrieval + rerank is well-trodden RAG engineering; the
novel part is only the ranking signal mix, which is tunable in production.

---

### S7. Goal Tree — *plans that outlive sessions*

**Purpose.** Persistent long-term planning: multi-month goals decomposed into reviewable,
schedulable work (fixes L8).

**How it works.**
- A database-backed tree: `goal → subgoal → task`, each node with status, deadline,
  priority, success criteria, review cadence, and links to relevant entities and the
  episodes where progress happened.
- A scheduler wakes the agent for due tasks and reviews — planning is driven by the
  clock, not only by user messages.
- **Plan reviews are first-class background jobs**: weekly per active goal, the agent
  loads the goal node + linked progress episodes + relevant S9 insights and asks: on
  track? assumptions still valid (check against S2 — a superseded claim can invalidate
  a plan)? re-decompose?
- The active task's node is always injected into context by S6, so every action is
  taken *in sight of* the goal it serves.

**Why it's better.** Long-horizon coherence stops depending on the user re-explaining
the plan every session. Plans are durable state with audit trails, connected to memory:
when a belief underlying a plan is superseded (S4), the affected goal is automatically
flagged for review — something no text-file agent can do.

**Difficulty: Medium.** The data model is a task tracker; the cron-driven review loop is
standard. The LLM does the actual planning, which frontier models already do well when
given clean state.

---

### S8. Skill Library & Compiler — *procedural learning*

**Purpose.** Turn repeated successful behavior into reusable, versioned procedures
automatically — so competence compounds (fixes L5, the procedural half).

**How it works.**
- A skill = manifest (trigger conditions, prompt template, optional code, required
  tools) + version history + a live scorecard (uses, success rate, last failure).
- **Mining:** a weekly job scans the episodic log for recurring action sequences with
  good outcomes (same tool-call shapes, similar task descriptions, success labels from
  S10).
- **Compilation:** a frontier model drafts a skill from those episodes; it's tested
  against the original cases replayed in a sandbox before activation.
- **Selection:** S6 retrieves matching skills into context; the reasoner follows the
  procedure instead of re-deriving it.
- **Maintenance:** failures recorded by S10 trigger revision proposals; a skill whose
  success rate drops gets demoted to "draft" and stops auto-loading.

**Why it's better.** OpenClaw has skills, but humans write them. ENGRAM *earns* them
from its own experience and retires them when they rot. Over years this is the
difference between an agent with a static toolbox and one whose toolbox grows with its
job.

**Difficulty: Hard.** Mining recurring patterns and auto-validating generated procedures
safely is the most ambitious subsystem. De-risk by shipping in stages: (1) agent
*proposes* skills for user approval, (2) auto-activate prompt-only skills, (3)
auto-activate sandboxed code skills.

---

### S9. Insight Generator — *reflection that produces new knowledge*

**Purpose.** Generate knowledge that exists in no single memory: patterns, trends,
hypotheses across months of experience (the "generate insights" requirement).

**How it works.**
- Scheduled reflection jobs sample memory along rotating lenses: per-entity ("everything
  about project X this quarter"), temporal ("compare this month's claims to six months
  ago"), outcome-based ("cluster the 14 failed tasks — common causes?"), and
  contradiction-driven (disputes queued by S4).
- The LLM produces **insight claims**: stored in S2 with `predicate = insight`,
  provenance links to every supporting memory, an explicit confidence, and — key — a
  **falsification condition** ("user seems to deprioritize project X — invalidated if
  they initiate work on it twice in the next month").
- Future consolidation passes check pending insights against new evidence, promoting
  confirmed ones (raising confidence, feeding the Identity Core's self-model or S7's
  plans) and retiring falsified ones.
- Output is bounded and ranked; only high-value insights ever reach the user, the rest
  enrich retrieval silently.

**Why it's better.** Hermes/OpenClaw can only retrieve what was explicitly stored. ENGRAM
manufactures higher-order memories — and because insights are claims with provenance and
falsification conditions, reflection self-corrects instead of accumulating confident
nonsense (the failure mode of naive "let the LLM journal about itself" designs).

**Difficulty: Medium.** It's prompting + scheduling on top of S1–S3. The falsification
mechanism is what keeps quality honest, and it's just another consolidation check.

---

### S10. Learning Loop — *outcome tracking and lessons*

**Purpose.** Make the agent measurably better over time by closing the feedback loop on
every significant action (fixes L5, the evaluative half).

**How it works.**
- Every task execution gets an **outcome record**: explicit signals (user corrections,
  thanks, edits to agent output, task completion) plus an LLM self-assessment pass
  during daily consolidation labeling episodes success / partial / failure with reasons.
- Failures are distilled into **lesson claims** ("when deploying service X, staging env
  vars differ from prod — check first"), which S6 retrieves whenever a similar task
  context appears. A lesson attached to the right retrieval context is a mistake that
  doesn't repeat.
- A **regression suite of past failures**: representative failed tasks become replayable
  test cases (sandboxed). When prompts, skills, or models change, the suite runs and the
  scorecard shows whether the agent is actually improving — improvement becomes a
  *measured property*, not a vibe.
- Aggregate statistics (success rate by task type, lesson recurrence) feed the Identity
  Core's self-model, so "what I'm bad at" is empirical.

**Why it's better.** Existing agents have no notion of their own track record. ENGRAM's
behavior changes through three concrete channels — lessons in retrieval, skill revisions,
self-model updates — all without fine-tuning, all auditable.

**Difficulty: Medium-Hard.** Outcome labeling from implicit signals is noisy at first;
start with explicit signals (corrections, redo-requests) which are high-precision, and
expand. The regression suite is plain test infrastructure.

---

## Part 3 — Why This Is Practical Now

| Component | Technology | Status |
|---|---|---|
| Event log + cold tier | Postgres + S3/Parquet | commodity |
| Semantic graph | Postgres + pgvector | commodity |
| Hybrid retrieval | BM25 + embeddings + rerank | standard RAG practice |
| Consolidation / reflection / learning jobs | job queue + cron + LLM APIs | commodity |
| Cheap bulk extraction | small/fast models (e.g. Haiku-class) | available, cheap |
| Skill sandbox | containerized execution | commodity |
| Identity versioning | git / versioned rows | trivial |

Estimated steady-state background cost for a single-user agent: a few dollars/day of
small-model calls plus occasional frontier-model reflection — comparable to what
Hermes/OpenClaw already burn re-reading bloated memory files every single turn, and it
buys compounding returns instead of compounding sludge.

**Build order (each stage is independently useful):**
1. S1 + S6-minimal (log everything; basic hybrid retrieval) — already beats flat files.
2. S2 + S3 (claims, consolidation) — beliefs become structured.
3. S4 + S5 (contradiction handling, identity core) — beliefs become *trustworthy*, self becomes stable.
4. S7 (goal tree) — long-horizon work.
5. S9 + S10 (insights, learning loop) — the agent starts compounding.
6. S8 (skill compiler) — the ambitious capstone, staged behind human approval first.

---

## Part 4 — Top 10 Features Ranked by Improvement Over Hermes/OpenClaw

1. **Claims-based semantic memory with provenance and confidence (S2).** The
   foundational break from text-blob memory; almost every other win depends on beliefs
   being discrete, sourced, and trust-weighted.
2. **Write-time consolidation with tiered salience (S3).** Memory quality and per-turn
   cost stay flat as history grows for years — directly defeats the scaling wall both
   agents hit.
3. **Budgeted working-memory composer (S6).** Decouples "what the agent knows"
   (unbounded) from "what the agent is thinking about" (curated, flat-cost) — the
   architectural fix for L1.
4. **Background cognition / sleep cycles (S3, S7, S9 schedulers).** The agent
   consolidates, reviews plans, and reflects between sessions instead of only thinking
   when poked — a categorical capability neither baseline has.
5. **Persistent goal tree with belief-linked plan reviews (S7).** Multi-month plans that
   survive sessions and get automatically flagged when underlying facts change.
6. **Bitemporal contradiction detection and supersession (S4).** Beliefs update like
   state machines instead of by retrieval lottery; the agent can explain *when and why*
   its beliefs changed.
7. **Outcome tracking + lesson claims + failure regression suite (S10).** Improvement
   becomes measurable, and past mistakes become retrieval-time guardrails.
8. **Version-controlled Identity Core with change rate-limits (S5).** Years-scale persona
   stability as a mechanical guarantee — cheap to build, disproportionate payoff.
9. **Falsifiable insight generation (S9).** Manufactures cross-memory knowledge no single
   episode contains, with built-in self-correction against confident nonsense.
10. **Automatic skill compilation from experience (S8).** Highest ceiling, hardest to
    build — ranked last not for value but for risk; staged rollout keeps it practical.

---

*Everything in this document is implementable with 2026-era databases, job queues, and
LLM APIs. The leap over Hermes and OpenClaw is not a smarter model — it is moving the
agent's mind out of the prompt and into a substrate built to last.*
