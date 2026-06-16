---
name: clean_docx_report
description: Build a polished Word (docx) report with justified narrative paragraphs and no broken bullet/list markers (use bold emphasis instead of lists).
status: draft
version: 1
---
1. Confirm the topic with the user and default to `.docx` format (only ask if genuinely ambiguous).
2. Call `recall` for any prior context on the user, project, or topic (e.g. ongoing report project, audience, color/style preferences).
3. If the report needs current/external facts, run `web_search` then `fetch_url` on the most relevant result.
4. Draft the report as a markdown file via `write_file` using these strict formatting rules (derived from a known markdown→docx converter bug):
   - Use **narrative paragraphs only**. Do NOT use `- ` bullets, `1. 2. 3.` numbered lists, or `  - ` sub-bullets — bare markers render as empty bullets/numbers in the converter.
   - Use `**bold**` for key terms, concept names, and section emphasis instead of bullet lists.
   - Use `##` and `###` for headings. Use `>` blockquotes sparingly for callouts/highlights.
   - Add a YAML frontmatter hint at the top of the markdown so the converter applies formatting: `---\nalign: justify\nnumbering: continuous\n---`.
   - Structure: Ringkasan Eksekutif (5–8 kalimat) → 8–13 body sections with clear H2 headings → Kesimpulan & Rekomendasi.
   - Use markdown tables (`| ... |`) for any comparisons or structured data.
5. Convert with `create_document` (`format: docx`, `source_path: <the .md file>`).
6. Verify the file with `list_dir` and confirm the filename to the user.
7. If the user reports empty bullets, empty numbered items, or un-justified paragraphs, **rewrite the source markdown to strip all list markers** and regenerate — do not patch the existing file in place.
