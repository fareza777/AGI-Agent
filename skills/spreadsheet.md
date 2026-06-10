---
name: spreadsheet
description: Build a spreadsheet (xlsx/csv) from data the user provides or you collect.
status: active
version: 1
---
# Spreadsheet

When the user wants a spreadsheet, tabel, budget, tracker, or data export:

1. Determine the columns (headers) and gather the rows. Ask only if the schema
   is genuinely ambiguous; otherwise infer a sensible structure.
2. If the data must be computed, do the arithmetic with `calculate` (or
   run_python if enabled) rather than guessing numbers.
3. Build a `table` object: `{"headers": [...], "rows": [[...], ...]}`.
4. Call `create_document` with format `xlsx` (or `csv` if they want raw data),
   a filename, a title, and the table. It is sent to the user automatically.
5. Reply with a one-line summary (columns and row count). Offer to add formulas
   or another sheet if useful.
