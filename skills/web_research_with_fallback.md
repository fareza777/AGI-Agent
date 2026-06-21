---
name: web_research_with_fallback
description: Web research FALLBACK — use when web_search fails (SSL/timeout) by fetching authoritative sources directly. USE WHEN: web_search returned SSL/network error. NOT for normal research (→ research_brief or web_data_hunter).
status: active
version: 2
---
1. Attempt `web_search` once with a clear, specific query.
2. If `web_search` returns an error or SSL/network failure, do NOT retry the same
   search — switch immediately to step 3. On this network DuckDuckGo is often
   blocked; Mojeek fallback usually works. If search still fails, use the
   suggested `fetch_url` URLs in the error message when present.
3. Fetch from **verified** authoritative URLs via `fetch_url` (lowercase hostnames
   only — never invent URLs like `platform.MiniMax.io`):
   - **MiniMax / Hailuo pricing:** `https://platform.minimax.io/docs/pricing/overview`
   - **OpenAI / ChatGPT pricing:** `https://openai.com/api/pricing/` and
     `https://developers.openai.com/api/docs/pricing`
   - **Anthropic / Claude:** `https://www.anthropic.com/pricing` and
     `https://docs.anthropic.com/en/docs/about-claude/models`
   - **Google / Gemini:** `https://ai.google.dev/gemini-api/docs/pricing`
   - General concepts: `https://en.wikipedia.org/wiki/<Topic>` (URL-encode title)
4. If the first `fetch_url` returns HTTP 404, try the **next URL from the list
   above** — max 2 attempts per vendor. Do not guess path variants.
5. Extract facts from fetched text only; prefer vendor docs over blogs.
6. Cite every claim with the URL you actually fetched. Say briefly if search failed
   and you used direct fetch.
7. Durably useful facts → `remember` with source URL + date, confidence ≤ 0.9.
