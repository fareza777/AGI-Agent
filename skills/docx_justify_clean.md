---
name: docx_justify_clean
description: Produce a docx report with justified paragraphs and no empty bullets, by working around the server-side markdown→docx converter's list-marker bug.
status: draft
version: 1
---
When the user asks for a Word/`.docx` report with clean justified paragraphs and professional typography:

1. Confirm topic with `recall` first (user's projects, prior reports) to avoid re-asking.
2. Plan structure: Ringkasan Eksekutif, 8–13 numbered sections (narrative prose, NOT a markdown list), Kesimpulan + Rekomendasi.
3. Write the markdown to `workspace/<topic>_justified.md` using these RULES — they are hard-won from prior failures:
   - **Never** use markdown list markers: no `- `, no `1. `, no `  - `, no `* ` — the converter parses them as bullets with empty bodies and produces `•` / `1.` rows with no text.
   - **Never** leave a blank line immediately after a list marker.
   - Use **bold** (`**...**`) for inline emphasis, headings (`##`) for sections, blockquotes (`>`) for callouts.
   - Add YAML-style frontmatter at the top: `---\nalign: justify\nnumbering: continuous\n---` — the server-side converter reads this.
   - If you need an enumeration, write it as a continuous prose paragraph: "Tahap pertama: ... Tahap kedua: ..." or use a **table** instead of a list.
   - If a table is needed, use standard pipe-table markdown — these render correctly.
4. Convert with `create_document(format="docx", source_path=..., filename=...)` and confirm success.
5. If the user sends a screenshot showing empty bullets or non-justified text, the fix is to **strip every line that begins with `-`, `*`, or `\d+\.`** from the source markdown and regenerate — do not try to patch the .docx directly.
6. Send the final file to the user via `send_file` and confirm with a short bullet summary of what changed vs. the previous version (if any).
