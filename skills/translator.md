---
name: translator
description: Translate text, documents, or web pages between Indonesian, English, and others. USE WHEN: "terjemahkan", "translate", "alihbahasakan". Keeps tone, formatting, proper nouns. NOT for rewriting in same language (→ docx_report).
status: active
version: 1
---
# Translator

When the user asks to terjemahkan, translate, or alihbahasakan anything:

1. Identify source material: pasted text (translate directly), a file path
   (`read_file` it — files in workspace/inbox/ arrive from Telegram), or a
   URL (`fetch_url`).
2. Default direction is Indonesian ↔ English; other languages on request.
3. Translate meaning, not words: keep tone and register (formal stays formal),
   keep proper nouns, keep formatting (bullets, headings, numbering).
   For ambiguous terms add a translator's note [catatan: ...] rather than
   guessing silently.
4. Delivery by size: short text → directly in chat; documents → rebuild the
   structure with `create_document` (same format family as the source, e.g.
   docx) and send it.
5. For recurring needs ("terjemahkan email dari klien Jepang tiap datang"),
   offer to make it a routine and `remember` the language pair preference.
