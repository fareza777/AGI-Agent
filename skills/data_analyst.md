---
name: data_analyst
description: Analyze numbers — totals, trends, comparisons, outliers, and the story behind them. USE WHEN: "analisa data ini", "hitung dari CSV", "apa insightnya", "trennya gimana". NOT for just building an xlsx (→ spreadsheet).
status: active
version: 1
---
# Data Analyst

When the user asks to "analisis data", "hitung dari file ini", or sends a CSV:

1. Load the data: `read_file` the CSV (inbox/ for Telegram uploads, or a
   D:/ / G:/ path), or take pasted rows from chat.
2. Understand it before computing: identify columns, units, time range, and
   obvious junk rows. State your reading back in one sentence so wrong
   assumptions surface early.
3. Compute with `calculate` for every figure: totals, averages, growth
   percentages, shares. Show your work on key numbers (e.g.
   "(1750-1200)/1200 = 45.8%"). Never present a number you didn't compute.
4. Find the story, not just the stats: biggest mover, outlier, trend
   direction, and the one thing the user should act on.
5. Deliver:
   - Quick question → answer in chat with the 2–3 key numbers **bold**.
   - Real analysis → `create_document` docx (Ringkasan, Temuan with bullets,
     table of the processed data, Rekomendasi) and/or styled xlsx of the
     cleaned/derived table.
6. Offer recurring analysis via `schedule_task` ("rekap penjualan tiap
   Senin") and `remember` where the source data lives.
