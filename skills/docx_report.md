---
name: docx_report
description: Generic long-form report (laporan, makalah, white paper) as polished docx or pdf. USE WHEN: "buatkan laporan", "tulis makalah tentang X", "buatkan paper". NOT for letters (→ official_letter), CV (→ cv_resume), or proposals (→ proposal_writer).
status: active
version: 3
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
3. Write the content using the document engine's rich markup (docx supports
   all of this and renders it beautifully):
   - `**text**` → bold (use for key figures and conclusions)
   - Lines starting `- ` → bullets; two leading spaces → sub-bullet
   - Lines starting `1. ` → numbered steps
   - Plain lines → paragraphs
4. Structure as title + sections `{heading, body}`:
   - Ringkasan Eksekutif (3–5 sentences, the answer up front)
   - Latar Belakang
   - Temuan / Analisis (the substance — use bullets and **bold** figures)
   - Kesimpulan & Rekomendasi (numbered, actionable)
   - Sumber (if web research was used)
5. Tabular data goes in `table {headers, rows}` — it renders with a colored
   header row and banded rows. Numbers in rows should be plain digits
   (e.g. "1500.75") so they stay clean.
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
