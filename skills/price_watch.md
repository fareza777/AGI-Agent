---
name: price_watch
description: Monitor prices, exchange rates, crypto, stock, or product availability and alert the user on changes or thresholds.
status: active
version: 1
---
# Price Watch

When the user wants to track a price ("pantau harga emas", "kabari kalau
kurs USD di bawah 15.500", "cek harga iPhone tiap hari"):

1. Establish: what exactly (item + market/source), the trigger (any change?
   below/above threshold? daily report?), and how often to check.
2. Find a reliable check method NOW, before scheduling: `web_search` then
   `fetch_url` a page that actually shows the price in its text. Verify you
   can extract the number. `remember` the working URL
   (subject=watch item, predicate=price_source).
3. Log each reading to `watch/<item>.csv` (date,price) in the workspace so
   trends are computable later.
4. Schedule: `schedule_task` (hourly/daily/every:N) with a self-contained
   prompt: "Cek harga [item] di [URL] via fetch_url. Bandingkan dengan
   pembacaan terakhir di watch/<item>.csv, append hasil baru. Kirim pesan
   HANYA jika [trigger]; kalau tidak, jangan kirim apa-apa." Silent checks
   beat spam.
5. On "gimana trennya": read the csv, compute change with `calculate`,
   summarize (start → now, %change, high/low).
6. House-keep: `list_tasks` and offer to cancel watches that have served
   their purpose.
