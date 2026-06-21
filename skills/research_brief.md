---
name: research_brief
description: Quick web research — 1–3 searches + short sourced brief (3–6 bullets). USE WHEN: "riset singkat", "cari info soal Y", quick factual question. NOT for structured data collection or comparisons (→ web_data_hunter), routine news (→ news_digest), or broken search (→ web_research_with_fallback).
status: active
version: 2
---
# Research Brief (quick)

When the user wants a **quick answer** from the web (not deep data collection):

1. Call `web_search` with 1–3 distinct queries (different phrasings/angles).
2. Pick the 2–3 most credible results and call `fetch_url` on each.
3. Write a brief: 3–6 bullet points of findings, each with its source URL.
   Note disagreements between sources explicitly.
4. If the topic relates to something in memory (a goal, a project the user
   mentioned), call `recall` and connect the findings to it.
5. Offer to `remember` the key takeaway if it's durably useful to the user.

For **deeper browsing, tables, price comparisons, or bulk data collection**,
switch to `use_skill web_data_hunter` instead.

Never present a guess as a sourced finding — if the pages didn't load or
results are thin, say so.
