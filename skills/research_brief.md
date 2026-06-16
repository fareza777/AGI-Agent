---
name: research_brief
description: Web research with 1–3 queries + fetch top sources + short sourced brief. USE WHEN: "riset tentang X", "cari info soal Y", "apa yang terbaru tentang Z". NOT for routine news (→ news_digest) or when web_search is broken (→ web_research_with_fallback).
---
# Research Brief

When the user asks you to research or look into a topic:

1. Call `web_search` with 1–3 distinct queries (different phrasings/angles).
2. Pick the 2–3 most credible results and call `fetch_url` on each.
3. Write a brief: 3–6 bullet points of findings, each with its source URL.
   Note disagreements between sources explicitly.
4. If the topic relates to something in memory (a goal, a project the user
   mentioned), call `recall` and connect the findings to it.
5. Offer to `remember` the key takeaway if it's durably useful to the user.

Never present a guess as a sourced finding — if the pages didn't load or
results are thin, say so.
