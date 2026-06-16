---
name: expense_tracker
description: Track the user's income and expenses in a persistent ledger and produce monthly financial recaps.
status: active
version: 1
---
# Expense Tracker (Catatan Keuangan)

When the user reports spending/income ("tadi beli X 50rb", "gajian 10jt",
"catat pengeluaran") or asks about their money:

1. Ledger lives at `finance/ledger.csv` in the workspace with columns:
   date,type,category,description,amount_idr
   First use: `make_dir finance` and `write_file` the header row.
2. To add an entry: `read_file finance/ledger.csv`, append the new row, and
   `write_file` the whole file back (keep rows sorted by date). Normalize:
   "50rb"→50000, "1,2jt"→1200000. type = masuk|keluar. Pick a consistent
   category set (makan, transport, belanja, tagihan, hiburan, gaji, lainnya)
   and reuse it.
3. Confirm each entry in ONE short line ("✅ keluar 50.000 — makan"). No
   lectures about spending unless asked.
4. On "rekap", "berapa pengeluaranku bulan ini", "laporan keuangan":
   `read_file` the ledger, compute per-category totals and balance with
   `calculate`, and reply with the highlights; for a monthly report build a
   styled xlsx + short docx via `create_document`.
5. Offer automation once trust is built: `schedule_task` for a monthly recap
   ("tiap tanggal 1 buat laporan keuangan bulan lalu") and `remember` budget
   targets (subject=user, predicate=budget_makan etc.) to compare against.
