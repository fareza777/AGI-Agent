---
name: docx_report
description: Produce a polished report document (docx/pdf) from a topic or data.
status: active
version: 1
---
# Report Document

When the user asks for a report, laporan, or formatted document:

1. Clarify the format if unstated — default to `docx`. Other options: pdf, md.
2. Gather material:
   - If it's about the user's own world, call `recall` for relevant memory.
   - If it needs current/external facts, call `web_search` then `fetch_url`
     on the best sources. Never invent figures — cite where each came from.
3. Structure the content as title + sections, each `{heading, body}`:
   - Executive summary / ringkasan
   - Background / latar belakang
   - Findings or analysis (the substance)
   - Conclusion & recommendations
   - Sources (if web research was used)
   If there is tabular data, pass it as `table {headers, rows}` — it renders as
   a real table in docx/pdf. Do NOT put tables in the chat reply.
4. Call `create_document` with filename, format, title, sections (and table).
   It is delivered to the user automatically.
5. In your chat reply, give a 2–3 sentence summary of what's in the document —
   not the whole thing. The file carries the detail.
6. Offer to `remember` key conclusions or `schedule_reminder` for follow-up.
