---
name: habit_tracker
description: Track habits, streaks, and personal logs (olahraga, baca, berat badan) with check-in reminders and progress recaps.
status: active
version: 1
---
# Habit Tracker

When the user wants to build/track a habit ("aku mau rutin olahraga",
"catat berat badanku", "udah 3 hari nggak bolong"):

1. Log storage: `habits/<habit>.csv` in the workspace, columns
   date,value,note (value = 1 for done, or the metric e.g. 72.5 for weight).
   Create with `make_dir habits` + `write_file` on first use.
2. Check-in: append today's row (read, modify, `write_file` back). Reply
   with the current streak — compute it from the dates, don't guess.
3. Reminders: offer a daily `schedule_task` whose prompt says to ask the
   user whether they did the habit today and log the answer. Time it to
   their routine (`recall` their schedule; user timezone is WIB/UTC+7).
4. Weekly recap (or on "gimana progressku"): read the csv, compute
   completion rate and trend with `calculate`, present: streak, best week,
   honest trend. For metric habits (weight) include the delta since start.
5. Missed days: matter-of-fact, no guilt ("bolong 2 hari, streak reset —
   mulai lagi hari ini?"). `remember` what derails them (kind=lesson) and
   what works, and use it.
