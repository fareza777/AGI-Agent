---
name: web_research_with_fallback
description: Web research FALLBACK — use when web_search fails (SSL/timeout) by fetching authoritative sources directly. USE WHEN: web_search returned SSL/network error. NOT for normal research (→ research_brief).
status: active
version: 1
---
1. Attempt `web_search` once with a clear, specific query.
2. If `web_search` returns an SSL/network error (e.g. `SSLV3_ALERT_HANDSHAKE_FAILURE` from DuckDuckGo, or any non-2xx), do NOT retry the same search — switch immediately to step 3.
3. Identify the topic domain and fetch from a known authoritative source via `fetch_url`. Use this mapping as the default shortlist:
   - Anthropic models/news: `https://www.anthropic.com/news` and `https://docs.anthropic.com/en/docs/about-claude/models`
   - OpenAI models/news: `https://openai.com/news/` and `https://platform.openai.com/docs/models`
   - Google/Gemini: `https://blog.google` (search within) and `https://ai.google.dev/gemini-api/docs/models`
   - General concepts/definitions: `https://en.wikipedia.org/wiki/<Topic>` (URL-encode the title)
   - Open-source models: `https://huggingface.co/<org>/<model>`
4. If the first `fetch_url` returns an error or empty body, try at most 1–2 alternative authoritative URLs from the same domain list before giving up.
5. Extract the relevant facts; if multiple sources contradict, prefer the vendor's own docs/newsroom over third-party blogs.
6. Cite every claim with the source URL you actually fetched. Tell the user explicitly that `web_search` failed and you used `fetch_url` fallback, so they understand the source path.
7. For any new long-lived fact discovered (model names, prices, release dates), store it via `remember` with a confidence ≤ 0.9 and a note about source + date, since vendor pages change.
