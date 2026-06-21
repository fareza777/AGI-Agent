---
name: web_data_hunter
description: Browse the web and collect structured data — multi-query search, open pages, extract numbers/facts/tables, compare sources, export results. USE WHEN: "cari data", "browse", "cari di internet", "kumpulin info", "bandingkan harga/spec", "riset online", "ambil data dari web", "cek di situs X". NOT for quick 3-bullet summary (→ research_brief), broken search only (→ web_research_with_fallback), or analyzing user's CSV (→ data_analyst).
status: active
version: 1
---
# Web Data Hunter (browse & collect)

When the user wants to **find, browse, or collect data from the web** — not
just a short answer:

## 0. Clarify output (only if ambiguous)

Ask ONE question if needed: comparison table? bullet facts? full docx report?
Or infer from phrasing ("bandingkan" → table; "kumpulin" → structured list).

## 1. Search strategy (always multi-angle)

Run **2–4 `web_search` queries** with different phrasings:

| Angle | Example |
|-------|---------|
| Indonesian | "harga iPhone 16 Indonesia 2026" |
| English | "iPhone 16 MSRP official" |
| Site-specific | `site:tokopedia.com` or `site:apple.com` in query |
| Freshness | add "2026", "terbaru", "latest" when topic is time-sensitive |

Pick **3–5 URLs** to open — prioritize in this order:

1. **Primary source** — vendor docs, government portal, official pricing page
2. **Major outlet** — Reuters, BBC, TechCrunch, Kompas, Detik (verify date)
3. **Aggregator** — only if primary is unreachable; note it's secondary
4. Skip SEO spam, Pinterest, Quora one-liners, AI-slop listicles

## 2. Browse & extract (`fetch_url`)

For each chosen URL:

1. `fetch_url` — read the returned text (max ~8 KB per page; truncated pages
   are noted in output).
2. Extract **concrete data points**: numbers, dates, names, specs, prices,
   percentages — with the **exact URL** each fact came from.
3. If the page is truncated but clearly has a table/list, try a more specific
   sub-URL from the same domain (e.g. `/pricing`, `/specs`) — max **2 extra
   fetches per domain**.
4. If `fetch_url` fails (403, timeout, empty): log it, try next URL — do not
   invent content from the failed page.

**Never** present data you did not read from a fetched page or search snippet.

## 3. Verify & reconcile

- **2+ sources agree** → high confidence, cite both.
- **Sources conflict** → show both values, say which you trust and why
  (official > reseller > blog).
- **Only snippets, no fetch** → label as "dari snippet search, belum dibuka
  halaman penuh" and offer to fetch.
- **Stale date** (>12 months for prices/specs) → warn user.

## 4. Deliver structured output

Choose format by task:

### Quick lookup
3–6 bullets, each ending with `(sumber: URL)`.

### Comparison / "bandingkan"
Markdown table in chat:

| Item | Harga | Spesifikasi | Sumber |
|------|-------|-------------|--------|

### Data collection / "kumpulin"
1. `write_file` → `workspace/research/<topic>_YYYY-MM-DD.md` with sections:
   - Query yang dipakai
   - Data mentah (quoted facts + URL per row)
   - Ringkasan
2. Optional: `create_document` docx if user wants a polished report.
3. Optional: `create_document` xlsx if tabular data is large (use `table`
   with headers/rows).

### Deep dive
`use_skill docx_report` after collection — research file becomes source_path.

## 5. Failure chain

```
web_search (DuckDuckGo → Mojeek fallback on this network)
  → fetch_url on URLs from search OR from ERROR hint list
web_search total FAIL → use_skill web_research_with_fallback
fetch_url 404 → try next verified URL from skill list; never guess paths
fetch_url all fail → tell user honestly; suggest paste URL or try later
```

Do NOT loop `web_search` with the same query more than once.
Do NOT invent vendor URLs — use lowercase hostnames (minimax.io not MiniMax.io).

## 6. Indonesia & local data

When topic is Indonesian (harga lokal, regulasi, statistik nasional):

- Prefer: **BPS**, **Kemenkeu**, **BI**, **Kemkominfo**, official `.go.id`
- News: Kompas, Tempo, CNBC Indonesia — cross-check with primary when possible
- Marketplace prices: Tokopedia/Shopee/Blibli listing pages via search +
  fetch (note: harga fluktuatif, tanggal penting)

## 7. Long jobs — activity feed

For 3+ fetches or multi-topic collection, emit short progress lines per
`activity_feed_protocol` ("🔎 web_search: …", "📄 fetch_url: domain.com …").

## 8. Memory & follow-up

- Durable facts (official price, release date, regulation) → `remember` with
  source URL + date, confidence ≤ 0.85.
- Recurring checks ("pantau harga X") → `schedule_task` with self-contained
  prompt referencing this skill's steps.
- Offer: export docx/xlsx, schedule daily refresh, or dig deeper on one source.

## Anti-patterns (never do)

- Guess prices, stats, or specs from training data when user asked for web data.
- Cite a URL you never fetched.
- Stop after one thin search result when user asked to "kumpulin" or "bandingkan".
- Dump 20 URLs without extracting the actual data points.
