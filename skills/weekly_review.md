---
name: weekly_review
description: Structured weekly review — goals progress, lessons, suggested next steps. USE WHEN: "review mingguan", "gimana minggu ini", "rekap minggu ini". NOT for daily briefing (→ daily_briefing) or news (→ news_digest).
---
# Weekly Review

When the user asks for a weekly review (or similar), follow this procedure:

1. Call `manage_goal` with action=list to get active goals.
2. Call `recall` with queries derived from each goal title to find recent
   progress or blockers in memory.
3. For each goal report: status, evidence of progress this week (with dates),
   and one concrete suggested next step.
4. Surface any *insight* or *lesson* claims from memory that are relevant.
5. Ask whether any goal should be marked done (`manage_goal` action=done) or
   re-scoped, and whether to schedule a reminder for next week's review
   (`schedule_reminder`).

Keep the output compact: one short block per goal, no filler.
