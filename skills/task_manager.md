---
name: task_manager
description: Capture tasks, goals, deadlines, reminders; list/prioritize; follow through. USE WHEN: "tugasku", "ingetin aku besok", "todo", "agenda", "target bulan ini". NOT for morning briefing (→ daily_briefing) or habits/streaks (→ habit_tracker).
status: active
version: 1
---
# Task & Goal Manager

When the user mentions something they need to do, a deadline, target, or asks
"apa tugasku", "ingatkan aku", "todo":

1. Capture immediately, in the right container:
   - Multi-week ambition → `manage_goal(action=add)` (e.g. "lulus sertifikasi
     AWS", "launch toko online")
   - Dated obligation → `schedule_reminder` with a concrete due_at (convert
     "besok pagi" etc. to ISO UTC using the current time in your context;
     the user is in WIB = UTC+7)
   - Recurring duty → `schedule_task` so future-you actually does the work
2. Reviews: on "apa tugasku/agenda", show `manage_goal(list)` +
   `list_reminders` + `list_tasks` merged into one prioritized view: overdue
   first, then today, then this week, then goals without recent progress.
3. Push for next actions: a goal with no next step is a wish — when listing
   goals, ask about the stalled ones and offer to schedule the next step.
4. Completion: mark done via `manage_goal(action=done)`, celebrate briefly,
   and `remember` notable achievements (kind=fact).
5. Learn the user's rhythm: when they consistently ask for evening reminders
   or weekly reviews, `remember` it and propose the recurring version once.
