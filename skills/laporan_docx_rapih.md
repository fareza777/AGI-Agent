---
name: laporan_docx_rapih
description: Generate a polished Indonesian technical Word report with fully justified narrative paragraphs, no list markers, and bold emphasis for key points (avoids the empty-bullet bug in the markdown→docx converter).
status: draft
version: 1
---
When the user asks for a Word/.docx report (laporan) on a technical topic — especially AI, LLM, or agent — and wants a clean, tidy, justify-aligned look:

1. Call `recall` first to retrieve project context (ongoing doc project, theme, prior versions, page count target, user preferences such as favorite color for accents).
2. If the topic matches an existing project (e.g., the 'Memahami LLM' 13-section guide), reuse the established 13-section template: Ringkasan Eksekutif → Latar Belakang → Definisi → Anatomi → Siklus Hidup → Inferensi → Kemampuan & Batasan → Model/Landscape → Framework 4D → Prompt Engineering → Agent Loop → Praktik Terbaik → Kesimpulan. Otherwise use a 10-section default (drop the three most topic-specific sections).
3. If current external facts are needed, attempt `web_search` once. If it fails with SSL or network errors, do NOT retry more than once more — fall back to `recall` and stable, well-established knowledge only. Mark time-sensitive claims as 'per Juni 2026'.
4. Compose the markdown with these STRICT formatting rules (lesson learned from v1/v2 failures):
   a. Start the file with frontmatter: `---\nalign: justify\n---` on its own lines at the top.
   b. NEVER use list markers in body text: no `- `, no `1. `, no `2. `, no `  - ` (indented sub-bullets). These produce empty bullets in the server-side markdown→docx converter.
   c. ALL body content must be flowing narrative paragraphs. Convert any list-like content into prose.
   d. Use `**bold**` liberally to highlight key terms, model names, and important conclusions.
   e. Use `## N. Section Title` for section headings (numbered, no skipped numbers).
   f. Markdown tables ARE allowed and recommended for comparisons (e.g., model landscape).
5. Save the markdown to the workspace with a versioned filename: `laporan_<topic_slug>_<YYYYMMDD>_v<N>.md` (start at v1; bump to v2/v3 only on revision after user feedback).
6. Call `create_document` with `format: "docx"`, a matching `title`, and the saved `source_path`.
7. Call `send_file` to deliver the resulting .docx to the user via Telegram.
8. If the user sends a screenshot showing empty bullets, do NOT try to fill the markers with text — the fix is to STRIP all list markers from that section and rewrite as a single narrative paragraph with **bold** for the key items. Save as next version (v2/v3) and re-deliver.
9. After a successful delivery, call `remember` to log any new style preferences the user confirms (e.g., 'hapus list marker, pakai paragraf naratif justify dengan bold') so future runs default to this style.
