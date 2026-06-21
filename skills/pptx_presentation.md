---
name: pptx_presentation
description: PowerPoint deck (pptx) on any topic — 6–10 slides with title, content, table, closing. USE WHEN: "buatkan presentasi", "buat slide", "PPT tentang X", "deck untuk pitch". NOT for docx reports (→ docx_report).
status: active
version: 3
---
# Presentation (PowerPoint)

When the user asks for a presentasi, slide, deck, PPT, or pitch:

**Engine (default):** `create_document` format=pptx — uses **python-pptx** internally
(16:9 slides, styled theme). Never run_python for pptx.

1. Confirm audience and rough length only if unclear; default 6–10 slides.
2. Gather material: `recall` for personal context, `web_search` + `fetch_url`
   for external facts. Real numbers beat vague claims.
3. Design the deck as title + sections — **each section becomes one slide**:
   - Slide 1 is generated automatically from `title` (styled title slide).
   - Per section: `heading` = slide title (max ~6 words, punchy).
   - `body` = **3–6 short bullet lines** starting with `- ` (max ~10 words
     each — ONE idea per bullet; slides are not documents). Two leading
     spaces give a sub-bullet, `1. ` lines give numbered steps, `**text**`
     renders bold. Don't paste long paragraphs onto a slide.
   - If a section has more than ~7 lines it auto-splits into a "(lanjutan)"
     slide — that usually means the content is too dense; tighten it.
4. **Tabular or ranked data (e.g. "Top 10", price lists, comparisons) MUST be
   a table, never a long bullet/numbered list** — a 10-item list crammed on
   one slide reads tiny and ugly. Put it in the `table {headers, rows}` param,
   OR write a markdown pipe table in a section `body`:
   `| Kol1 | Kol2 |` then a `|---|---|` separator then the rows. Both render
   as a real styled grid slide (colored header, banded rows) and auto-split
   onto continuation slides when long — no row cap, no manual xlsx needed.
   **Designed layouts that lift a deck to pro level — use them:**
   - **Stat cards**: a slide of key metrics renders as big number cards when
     the body is 2–6 lines of `Label: Value` (e.g. `- BTC: $64,195` /
     `- Dominance: 56.2%`). Far stronger than a plain bullet list for numbers.
   - **Section divider**: to break the deck into parts, add a section with a
     `heading` and an EMPTY `body` — it becomes a full-colour divider slide.
     Use one before each major part (Pasar, Analisa, Rekomendasi).
5. A good deck arc: Masalah/Konteks → Data/Temuan → Opsi/Analisis →
   Rekomendasi → Next Steps.
6. Call `create_document` with format `pptx`. Slides are 16:9 and designed:
   a full-colour title cover, a filled heading band per slide, accent bullet
   markers, footer page numbers, and real styled tables. Pick a `theme` that
   fits the topic — `midnight` (default, corporate blue), `emerald` (green,
   finance/growth), `sunset` (warm orange), `slate` (neutral grey), or
   `violet` (creative). The file is delivered automatically.
7. Reply with the slide list (one line per slide) so the user can request
   changes fast. Offer a docx handout version of the same content.
