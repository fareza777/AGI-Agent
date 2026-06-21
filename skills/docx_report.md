---
name: docx_report
description: Generic long-form report (laporan, makalah, white paper) as polished docx or pdf. USE WHEN: "buatkan laporan", "tulis makalah tentang X", "buatkan paper". NOT for letters (→ official_letter), CV (→ cv_resume), or proposals (→ proposal_writer).
status: active
version: 4
---
# Report Document (Word / PDF)

When the user asks for a report, laporan, dokumen, proposal singkat, or memo:

**Engine (default):** always `create_document` format=docx. It runs **python-docx**
internally (styled title block, colored headings, justified paragraphs, banded
tables, page numbers). If the user says "pakai python-docx" — use
create_document; do **NOT** use run_python.

1. Clarify the format only if genuinely ambiguous — default to `docx`.
2. Gather material:
   - About the user's own world → call `recall` first.
   - Needs current/external facts → `web_search` then `fetch_url` on the best
     sources. Never invent figures — note where each came from.
3. Write the content using the document engine's rich markup (docx renders all
   of this beautifully — use it instead of walls of plain text):
   - `**text**` → bold (use for every key figure and conclusion)
   - `### Subjudul` → a sub-heading nested under the section (use to break a
     long section into labelled parts instead of one giant block)
   - Lines starting `- ` → bullets; two leading spaces → sub-bullet
   - Lines starting `1. ` → numbered steps
   - A `|  col | col |` line followed by `|---|---|` then rows → a real styled
     table (colored header, banded rows) right inside the body — use this for
     small inline tables; no need for a separate file
   - `---` on its own line → a thin horizontal rule (section break)
   - Plain lines → justified paragraphs
   With 3+ sections a Daftar Isi (table of contents) and page numbers are
   added automatically.
4. Structure as title + sections `{heading, body}`:
   - Ringkasan Eksekutif (3–5 sentences, the answer up front)
   - Latar Belakang
   - Temuan / Analisis (the substance — use bullets and **bold** figures)
   - Kesimpulan & Rekomendasi (numbered, actionable)
   - Sumber (if web research was used)
5. Tabular data: a big/primary table goes in the `table {headers, rows}` param
   (colored header, banded rows); small inline tables can be a markdown pipe
   table inside a section body (point 3). Numbers in rows should be plain
   digits (e.g. "1500.75"). For a data table, also pass a `chart`
   (bar/line/pie over the table) — it's embedded as an image and makes the
   report look far more professional than numbers alone.
6. Call `create_document` (format docx or pdf). It is delivered automatically.
   The docx gets a styled title page block, colored headings, **justified body
   paragraphs**, and page numbers. For revisions ("justify", "revisi format"):
   read the existing `.md` source (or prior draft) once, edit if needed, then
   `create_document` with `source_path` — do NOT loop `search_files`/`read_file`
   dozens of times.
7. In the chat reply give a 2–3 sentence summary only — the file carries the
   detail. Offer to `remember` key conclusions or `schedule_reminder` for
   follow-up.

Quality bar: every section has substance (no one-line filler), figures are
sourced, recommendations are concrete. If the report is long, the structure
must let a reader get the point from the Ringkasan alone.
