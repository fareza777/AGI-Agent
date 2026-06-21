---
name: automation_scheduler
description: Set up recurring cron-like jobs — daily Word reports, weekly digests, article drafts, price checks, anything the agent should DO on a schedule. USE WHEN: "tiap hari", "setiap pagi", "cron", "jadwal otomatis", "rutin", "schedule task", "otomatis buat laporan". NOT for one-line reminders without work (→ schedule_reminder) or todo lists (→ task_manager).
status: active
version: 1
---
# Automation Scheduler (cron jobs)

When the user wants something done **automatically on a schedule** (daily
report, weekly article draft, hourly price check, Monday morning briefing):

## Two tools — pick the right one

| Need | Tool |
|------|------|
| Agent **does work** (research, write docx, scan files, web search) | `schedule_task` |
| Only send a **fixed text** at a time ("ingetin meeting jam 3") | `schedule_reminder` |

Most "cron job" requests → **`schedule_task`**.

## Setup flow

1. **Clarify once** if missing: what to produce, how often, what time (WIB).
2. **Write a self-contained prompt** — future-you only sees this prompt, not
   the chat. Include:
   - Topic / scope ("laporan penjualan", "artikel blog tentang AI")
   - Output format ("docx via create_document", "ringkas di chat", "md draft")
   - Language (Indonesian)
   - Which skill steps to follow (`use_skill docx_report` etc.)
3. **Convert WIB → UTC** for `due_at` (user is UTC+7):
   - 07:00 WIB → `00:00:00Z` same calendar day
   - 19:30 WIB → `12:30:00Z`
4. Call `schedule_task` with:
   - `prompt`: the self-contained instruction
   - `due_at`: ISO 8601 UTC for **first run**
   - `recurrence`: `once` | `hourly` | `daily` | `weekly` | `every:<minutes>`
5. Confirm back in **WIB + UTC**: task id, recurrence, next run, what it will do.
6. User can review anytime: `list_tasks` or Telegram `/tasks`; cancel with
   `cancel_task` or `/canceltask <id>`.

## Example prompts (copy pattern, adapt topic)

**Daily Word report (07:00 WIB):**
```
Buat laporan harian docx tentang [topik]. recall preferensi user dulu,
web_search untuk berita terbaru 24 jam, susun laporan (Ringkasan, Temuan,
Rekomendasi), create_document format docx dan kirim. Balas chat 2 kalimat
ringkas saja.
```

**Daily website article draft (08:00 WIB):**
```
Draft artikel blog ~800 kata tentang [topik/niche]. web_search 3 sumber,
tulis write_file ke workspace/articles/YYYY-MM-DD-judul.md dengan SEO title +
meta description + body. Kirim file md ke user. Jika repo website ada di
[path], offer git commit — jangan push tanpa konfirmasi.
```

**Weekly review (Senin 09:00 WIB):**
```
use_skill weekly_review — jalankan langkah-langkah skill untuk user ini.
```

## Execution (what happens at fire time)

The background scheduler runs a **full agent turn** with all tools. Results
and files are delivered to the same Telegram chat with prefix
"🤖 Tugas terjadwal #N". Survives bot restarts. Missed runs while offline are
**skipped** (not replayed in bulk) — next fire is the next slot.

## Limits (be honest)

- No direct WordPress/CMS API unless user provides one — default is **deliver
  docx/md file** for manual upload.
- Tasks run only while `run.py` / `start_engram.bat` is running.
- Heavy jobs.constants cost API tokens each run — warn if hourly on big reports.

## Maintenance

- User says "stop" / "ganti jadwal" → `cancel_task` old id, `schedule_task` new.
- After a bad run, `improve_skill` or edit the task prompt via cancel + reschedule.
