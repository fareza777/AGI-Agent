---
name: meeting_notes
description: Turn raw meeting notes or transcript into structured minutes with action items. USE WHEN: "buat notulen", "rangkum rapat", "ringkaskan transcript meeting". NOT for general summarization of files (→ summarizer).
status: active
version: 1
---
# Meeting Notes

When the user pastes meeting notes, a transcript, or asks you to write minutes:

1. Read the raw input carefully. If a file was referenced, `read_file` it.
2. Produce structured minutes:
   • Attendees (if mentioned)
   • Key decisions
   • Discussion summary (concise)
   • Action items — each with owner and, if stated, a due date
3. For any action item with a date and owned by the user, offer to
   `schedule_reminder`.
4. `remember` durable decisions or commitments as claims (subject = the person
   or project), so they surface later.
5. Offer to save the minutes as a `create_document` (docx/md) if the user wants
   a file.
