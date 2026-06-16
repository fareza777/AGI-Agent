---
name: news_digest
description: News briefing on topics the user follows, on demand or as a scheduled daily/weekly digest. USE WHEN: "berita apa hari ini", "update soal X", "kabarin tiap pagi". NOT for in-depth research (→ research_brief) or morning full briefing (→ daily_briefing).
status: active
version: 1
---
# News Digest

When the user asks "ada berita apa", "update soal X", or wants routine news:

1. `recall` the user's standing interests (predicate=news_interests) and
   merge with the asked topic.
2. Research: `web_search` per topic (2–3 phrasings if the first is thin),
   `fetch_url` only the 2–3 most substantive results. Prefer primary/major
   outlets; note the date of each item — discard stale "news".
3. Brief in chat, per topic: **headline takeaway** + 2–3 bullets + source
   names. Max ~10 bullets total; a digest is a filter, not a firehose.
   Separate fact from speculation explicitly.
4. Routine setup ("kabari tiap pagi"): `schedule_task` with recurrence=daily
   and a prompt like "Buat news digest pagi: cari berita terbaru tentang
   [topics], ringkas per topik dengan sumber, kirim ke user." Confirm the
   delivery hour in WIB (convert to UTC for due_at).
5. Maintain interests over time: `remember` new topics they keep asking
   about; drop topics they ignore (update news_interests). On "stop berita
   X", update the task via cancel + re-schedule.
