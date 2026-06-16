---
name: email_drafter
description: Draft emails, chat messages, or short replies in formal/casual tone. USE WHEN: "buatkan email", "draft balasan", "tolong tulis pesan". NOT for formal letters with kop surat (→ official_letter) or full proposals (→ proposal_writer).
status: active
version: 1
---
# Email & Message Drafter

When the user asks to draft an email, balasan, pesan WA/Telegram, or surat elektronik:

1. `recall` the recipient and context first — past dealings, the user's role,
   tone preferences, ongoing threads. Personal context makes drafts usable.
2. Determine register from the relationship: formal Indonesian (Bapak/Ibu,
   "Dengan hormat") for officials/clients, semi-formal for colleagues, casual
   for friends. English on request or when the recipient is international.
3. Structure: subject line (for email), opening, purpose in the first two
   sentences, supporting detail, clear ask or next step, closing.
4. Deliver the draft directly in chat inside a code block so it's easy to
   copy. For long/important letters also offer a docx via `create_document`.
5. Always offer one alternative tone ("mau versi yang lebih santai/tegas?").
6. If the user corrects style ("jangan terlalu kaku"), `remember` the
   preference (subject=user, predicate=email_style) so future drafts match.
