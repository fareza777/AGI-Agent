---
name: activity_feed_protocol
description: Hermes-style live activity feed — emit short status messages to the chat while tools run, so the user sees progress instead of silence.
status: active
version: 1
---
# Activity Feed Protocol (Live Status)

The user is on Telegram. While you do work that takes more than ~2
seconds (multi-tool, multi-step, file generation, web research), **emit
activity messages** so they see what's happening. Hermes-style agents
do this automatically; you should too.

## How to emit

The `run_python` / `run_shell` / `create_document` tools and several
others already emit their own activity through `ctx.emit_activity`
when an activity callable is wired up. For tool calls that don't (file
reads, `recall`, `web_search`, internal reasoning), drop a one-line
status message **in your own reply** before the tool result, like:

> Sedang membaca file `engram/agent.py`...
> Mencari tahu jadwal pesawat ke Bali...
> Menghitung total dari 47 baris...
> Menyusun 6 slide presentasi...

This goes BEFORE the tool call (or interleaved with several), not at
the end. The user sees progress, not a 30-second blank.

## When to emit (and when not to)

Emit when:
- The task has 3+ steps ("analisa data ini lalu buat grafik lalu
  rekap di xlsx")
- A single tool is slow (`fetch_url`, `view_image`, big `read_file`,
  `create_document` with a long body)
- The user is waiting and might wonder if you're stuck
- You're about to do something destructive and want a beat to react

Do NOT emit when:
- The tool returns in <1s and you already have the answer ready
- The activity would be noise ("thinking...", "working..." with no
  specifics) — that's worse than silence
- You're emitting more than once per tool call. One short line per
  tool, not five.
- The user said "jangan ceramah", "langsung jawab", or similar

## Tone

- Bahasa Indonesia kalau user pakai Indonesia; English kalau English.
- Singkat: 5–10 kata, satu kalimat, **kata kerja di awal**.
- Spesifik, bukan generik: "Membaca `skills/spreadsheet.md`" beats
  "Reading file...".
- Tanpa emoji berlebihan. Satu emoji工具-glyph yang sudah ada
  (`🔧`, `📄`, `🌐`, `🐍`) cukup, kalau ada.
- Kalau langkah gagal, emit what failed and next move: "❌ fetch_url
  timeout — coba URL mirror..."

## Multi-tool parallelism

When you call several tools in one turn, emit a single batched
activity line that lists them: "Membaca 3 file sekaligus untuk
diffs..." — not three separate lines. Telegram shows messages in
order, so three rapid-fire lines feel spammy.

## Final reply

After the work is done, the regular reply is the deliverable, not
a recap of the activity feed. The feed was for progress; the reply
is the answer. Don't repeat the activity messages in the reply.
