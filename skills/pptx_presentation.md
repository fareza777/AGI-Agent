---
name: pptx_presentation
description: Create a clean, professional PowerPoint presentation (pptx) on any topic or from user data.
status: active
version: 1
---
# Presentation (PowerPoint)

When the user asks for a presentasi, slide, deck, PPT, or pitch:

1. Confirm audience and rough length only if unclear; default 6–10 slides.
2. Gather material: `recall` for personal context, `web_search` + `fetch_url`
   for external facts. Real numbers beat vague claims.
3. Design the deck as title + sections — **each section becomes one slide**:
   - Slide 1 is generated automatically from `title` (styled title slide).
   - Per section: `heading` = slide title (max ~6 words, punchy).
   - `body` = 3–6 bullet lines starting with `- ` (max ~12 words each;
     slides are not documents). Two leading spaces give a sub-bullet,
     `1. ` lines give numbered steps, `**text**` renders bold.
   - If a section has more than 8 lines it auto-splits into a
     "(lanjutan)" slide — better to keep bullets tight instead.
4. A good deck arc: Masalah/Konteks → Data/Temuan → Opsi/Analisis →
   Rekomendasi → Next Steps.
5. Numeric comparisons go in `table {headers, rows}` — it renders as a styled
   table slide (max 12 rows shown; ship the full data as a separate xlsx via
   another `create_document` call if larger).
6. Call `create_document` with format `pptx`. Slides are 16:9 with a
   consistent color theme; the file is delivered automatically.
7. Reply with the slide list (one line per slide) so the user can request
   changes fast. Offer a docx handout version of the same content.
