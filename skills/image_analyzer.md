---
name: image_analyzer
description: Analyze photos and images the user sends — read documents/receipts, describe screenshots, extract text or data from pictures.
status: active
version: 1
---
# Image Analyzer

When the user sends a photo or asks about an image ("ini apa", "baca struk
ini", "lihat screenshot ini"):

1. Telegram photos land in `inbox/` — `list_dir inbox` to find the newest
   file, then `view_image` it. After that call you can actually SEE it.
2. Analyze for the purpose, not generically:
   - Struk/nota → extract merchant, date, line items, total; offer to log it
     via the expense tracker ledger
   - Screenshot error → read the error text, diagnose, suggest the fix
   - Document/whiteboard photo → transcribe the text faithfully, then offer
     a clean docx via `create_document`
   - Chart/graph → read the axes and trend, state the numbers visible
   - General photo → describe what's asked, no more
3. Extracted tabular data (receipts, tables in photos) → offer xlsx/csv
   export via `create_document`.
4. Limits: jpg/png/webp/gif up to ~4.5MB; tell the user to re-send smaller
   if oversized. If text in the image is unreadable, say which part —
   don't fabricate.
5. `remember` durable facts an image reveals when relevant (e.g. a KTP photo
   for a form → with the user's consent only).
