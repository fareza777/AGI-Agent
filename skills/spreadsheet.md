---
name: spreadsheet
description: Build a styled xlsx (or csv) workbook from user data — header row, formulas-ready numbers, styled. USE WHEN: "buatkan spreadsheet", "tabel xlsx", "export ke Excel", "tracker tabel". NOT for analyzing numbers (→ data_analyst) or building docx tables.
status: active
version: 2
---
# Spreadsheet (Excel)

When the user wants a spreadsheet, tabel, budget, tracker, rekap, or data export:

1. Determine the columns (headers) and gather the rows. Ask only if the schema
   is genuinely ambiguous; otherwise infer a sensible structure.
2. Compute derived numbers with `calculate` — never guess arithmetic. Include
   a total/summary row at the bottom when it makes sense (label it "TOTAL").
3. Build `table: {"headers": [...], "rows": [[...], ...]}`. Write numbers as
   plain digits without thousand separators ("1500000", "950.5") — the xlsx
   engine converts them to real numeric cells so the user can sum and sort.
   Put units or currency in the header ("Harga (Rp)"), not in every cell.
4. Call `create_document` with format `xlsx`, a filename, a short title, and
   the table. The result is automatically styled: colored header row, banded
   rows, frozen header, autofilter, fitted column widths — and sent to the user.
   Use `csv` instead only when they want raw data for import elsewhere.
5. Reply with a one-line summary (columns, row count, and the key total).
   Offer follow-ups: monthly update via `schedule_task`, or a docx report of
   the same data.
