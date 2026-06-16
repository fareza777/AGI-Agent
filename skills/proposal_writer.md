---
name: proposal_writer
description: Write business, project, or sponsorship proposals as structured professional documents.
status: active
version: 1
---
# Proposal Writer

When the user asks for a proposal (bisnis, proyek, kerja sama, sponsorship,
pengajuan dana):

1. Establish the three anchors before writing: who decides (audience), what
   exactly is being asked for (budget? approval? partnership?), and the
   deadline. `recall` prior context about the venture; `web_search` for
   market numbers if the proposal needs external evidence.
2. Standard skeleton (adapt names to context):
   - Ringkasan Eksekutif — the entire ask in 5 sentences, **bold** the number
   - Latar Belakang & Masalah
   - Solusi / Deskripsi Proyek
   - Ruang Lingkup & Deliverables (bullets)
   - Timeline (numbered phases with dates)
   - Anggaran — as `table {headers, rows}` with a TOTAL row, computed via
     `calculate`
   - Tim / Kredensial
   - Penutup & Call to Action (what to do, by when)
3. Write persuasively but concretely: every claim has a number or a source.
   The budget table must add up exactly.
4. `create_document` format `docx` (pptx version via the pptx_presentation
   skill if they'll pitch it live).
5. Offer to `schedule_reminder` for the follow-up date ("follow up proposal
   ke X") — proposals die without follow-up.
