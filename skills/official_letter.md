---
name: official_letter
description: Formal Indonesian letters with standard anatomy (surat lamaran, izin, pengunduran diri, penawaran, kuasa) as docx. USE WHEN: "buatkan surat lamaran", "surat izin", "surat pengunduran diri", "surat kuasa". NOT for emails (→ email_drafter) or full proposals (→ proposal_writer).
status: active
version: 1
---
# Surat Resmi Indonesia

When the user asks for a surat resmi of any kind:

1. `recall` the user's full name, address, occupation, and any data the letter
   needs. Ask only for what memory doesn't hold (e.g. recipient name/address,
   dates). Never leave placeholder brackets in the final letter.
2. Follow standard Indonesian formal letter anatomy, in this order:
   - Tempat, tanggal (right-aligned by convention — put it as the first line)
   - Perihal / Lampiran (if applicable)
   - Recipient block: "Kepada Yth. ..." + address
   - Salam pembuka ("Dengan hormat,")
   - Isi: pembuka (identitas & maksud), inti, penutup (harapan + terima kasih)
   - Salam penutup ("Hormat saya,") + nama lengkap
3. Use formal baku Indonesian throughout — no slang, no abbreviations.
4. Build it as `create_document` format `docx`: title = perihal, sections
   carrying the blocks above as bodies (plain paragraphs, no bullets).
5. Common types and their key content:
   - Lamaran kerja: position + source of vacancy, qualifications, lampiran list
   - Izin: who, what event/absence, date range, commitment to catch up
   - Pengunduran diri: position, effective date, gratitude, handover offer
   - Kuasa: pemberi & penerima kuasa identities, specific delegated act
6. Summarize in chat in one sentence; deliver the docx. Offer a PDF version.
