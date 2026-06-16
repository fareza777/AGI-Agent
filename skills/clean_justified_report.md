---
name: clean_justified_report
description: Build a polished Word (.docx) report with justified paragraphs, NO list markers, narrative style with bold emphasis — for users who want clean book-like AI/tech reports.
status: draft
version: 1
---
When the user asks for a Word report, laporan .docx, or document that must look rapi/rapih with paragraf justify (especially on AI/tech topics):

1. Recall user's docx style preferences via `recall` with query `docx_style_learned` to load any saved formatting lessons.
2. Confirm the topic with the user only if ambiguous — for AI/LLM/agent topics proceed directly.
3. Compose the markdown source with these NON-NEGOTIABLE rules:
   - Use **narrative paragraphs**, NOT bullet lists. NEVER write lines starting with `- `, `1. `, `  - `, `* `, or `2. `.
   - Use `**bold**` for emphasis on key terms, model names, framework names, section anchors.
   - Add YAML frontmatter at the very top: `---\nalign: justify\nnumbering: continuous\n---`
   - Structure: Ringkasan Eksekutif → 8–13 numbered sections (## 1. ..., ## 2. ...) → Kesimpulan.
   - Include at least one comparison table for landscape/competitor sections.
   - Keep paragraph length 3–6 sentences, justify-aligned.
4. Save the .md via `write_file` to a path inside the workspace folder (e.g. `workspace\<topic>_v1.md`).
5. Convert via `create_document` with `format: docx` and `source_path` pointing to the .md file.
6. If the user sends a screenshot showing formatting issues (empty bullets, missing justify), use `view_image` to confirm the bug, then rebuild the .md by REMOVING the offending list markers (do NOT add content to empty bullets — delete them entirely), regenerate the .docx, and resend via `send_file`.
7. After successful delivery, ask whether the user wants the styling saved as a permanent preference via `remember`.
