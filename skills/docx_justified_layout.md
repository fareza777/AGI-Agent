---
name: docx_justified_layout
description: Write and convert markdown to a clean .docx with justified paragraphs, no empty bullets, and bold key points — avoiding the markdown→docx converter's empty-marker bug.
status: draft
version: 1
---
When the user asks for a Word/.docx report with clean formatting (rapih, justify, no empty bullets):

1. Gather material — call `recall` first to check for prior versions of the same report and reuse/extend content rather than rewrite from scratch.

2. Write the markdown source with `write_file` using these STRICT formatting rules (learned from v1→v2→v3 iteration, validated by user on 2026-06-11):
   - DO NOT use bullet markers `- ` anywhere — the server-side markdown→docx converter renders them as empty bullets.
   - DO NOT use numbered list markers `1. `, `2. ` inline (start-of-line) — same bug, produces empty numbered items like "1. \n2. \n3. \n".
   - DO NOT use sub-bullets `  - ` (indented hyphens) — same bug.
   - DO use `## N. Section Title` for section headers (H2 heading, not a list).
   - DO use `**bold**` inline within narrative paragraphs for key points.
   - DO use markdown tables `| col | col |` for comparisons/data.
   - DO use `>` blockquotes for callouts/highlights.
   - DO use `---` horizontal rules between major sections.
   - To list items, embed them inline as **bold** spans: "Tiga pilar utamanya adalah **A**, **B**, dan **C**." — never as `- A` / `- B` / `- C`.

3. Follow this structural template:
   - H1 title (with year/edition if recurring topic).
   - Metadata line: **Panduan ...** | Versi X.X | Tanggal | Penulis.
   - `---` separator.
   - `## Ringkasan Eksekutif` — 5–7 kalimat naratif, justify by default, **bold** the thesis sentence.
   - `---` separator.
   - Numbered H2 sections: `## 1. ...`, `## 2. ...`, dst. (each 1–3 paragraf naratif justify).
   - Optional inside sections: 1 tabel komparasi, 1 blockquote highlight, inline-bold list.
   - Closing: `## N. Kesimpulan dan Rekomendasi` with bold "Rekomendasi:" line.

4. Convert with `create_document` (format `docx`, source_path = full workspace path to the .md, filename without `.docx`). The server converter handles `align: justify` by default when bullets are absent.

5. Verify delivery: `list_dir` to confirm file size is non-trivial (>30 KB for ~10-section report), then `send_file` to push to user's Telegram.

6. If user reports "bullet kosong", "justify belum", or "masih ngaco":
   - Run `search_files` query for `- `, `1. `, or `  - ` patterns inside the source .md.
   - Re-write the source removing ALL list markers entirely (do not try to "populate" empty ones — delete the line or merge into a narrative sentence with **bold** spans).
   - Re-run `create_document` with a new filename (v2, v3, ...) and `send_file` again.
   - Do not propose generating a separate Python script — the server-side converter is the only docx pipeline; the fix is in the source markdown, not the toolchain.
