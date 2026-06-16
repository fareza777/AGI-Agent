---
name: summarizer
description: Summarize documents, files, articles, or URLs into key points, decisions, and action items.
status: active
version: 1
---
# Summarizer

When the user sends a file or link and asks "ringkas", "rangkum", "summarize",
"apa isinya", or "TL;DR":

1. Get the content:
   - File from Telegram → it lands in workspace/inbox/ — `list_dir` inbox
     then `read_file` it (UTF-8 text up to ~200KB; for binary formats like
     docx say so and ask for a text/pdf export — or read what's readable).
   - URL → `fetch_url` (if truncated, note that the summary covers the
     fetched portion).
   - Long chat history → summarize from context.
2. Output shape (in chat, in the user's language):
   - **Inti** — one sentence
   - **Poin Kunci** — 3–7 bullets
   - **Keputusan / Angka Penting** — if any
   - **Action Items** — who does what, if any
3. Stay faithful: no new claims, no opinions unless asked. Flag anything
   suspicious or contradictory in the source.
4. For long source material offer a docx summary via `create_document`.
5. Offer to `remember` durable facts the document reveals about the user's
   world (projects, deals, people) and `schedule_reminder` for any deadline
   mentioned inside.
