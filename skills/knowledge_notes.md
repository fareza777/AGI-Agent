---
name: knowledge_notes
description: Personal notes and knowledge base — save ideas, web captures, references; retrieve later. USE WHEN: "catat ini", "simpan ide", "mana catatanku tentang X", "simpan artikel ini". NOT for tasks (→ task_manager) or facts-about-the-user (→ remember tool).
status: active
version: 1
---
# Knowledge Notes (Catatan Pribadi)

When the user says "catat ini", "simpan ide", "note", or asks "mana catatanku
tentang X":

1. Notes live in `notes/` in the workspace, one markdown file per topic
   (`notes/ide-bisnis.md`, `notes/resep.md`). Maintain `notes/INDEX.md`
   listing every note with a one-line description — update it whenever you
   create a note.
2. Capturing: append to the topic file (read + `write_file` back) with a
   `## YYYY-MM-DD` heading per entry. New topic → new file + index entry.
   Distinguish notes (verbatim content the user wants kept) from beliefs:
   facts about the user's life still go to `remember`.
3. Retrieving: `search_files(query, "notes")` then `read_file` the hits.
   Quote the user's own words back; cite which note it came from.
4. Web captures: "simpan artikel ini" → `fetch_url`, store a summary + key
   quotes + the URL in the right topic note.
5. Periodically (when a note grows messy) offer to reorganize it; show the
   proposed structure before rewriting. Deliver any note as a file via
   `send_file` or formatted docx on request.
