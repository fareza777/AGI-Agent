---
name: invoice_generator
description: Generate invoices, kwitansi, or payment receipts as styled docx/pdf with itemized tables.
status: active
version: 1
---
# Invoice / Kwitansi Generator

When the user asks for an invoice, tagihan, kwitansi, nota, or receipt:

1. `recall` the user's business identity (name, address, bank account, npwp
   if any) and the client's details. Ask for missing pieces and `remember`
   them (subject=client name, or subject=user predicate=bank_account).
2. Required content: invoice number (format INV/YYYY/MM/NNN — keep a running
   counter in memory via remember), issue date, due date, seller block,
   buyer block, itemized table, payment instructions.
3. Compute every line and the totals with `calculate`: subtotal, discount,
   tax (PPN 11% only if the user is PKP — ask once, remember), grand total.
   Never eyeball arithmetic on an invoice.
4. Build `table {headers: [No, Deskripsi, Qty, Harga Satuan (Rp), Jumlah (Rp)],
   rows: [...]}` with plain-digit numbers, final row "TOTAL".
5. Sections: Informasi Penjual, Informasi Pelanggan, then the table, then
   Pembayaran (bank, account, due date) and Catatan.
6. `create_document` format `docx` (or `pdf` if they'll send it directly).
7. Offer: a matching xlsx ledger row for bookkeeping, and a
   `schedule_reminder` on the due date to chase payment.
