---
name: contact_crm
description: Track people in the user's life (colleagues, clients, family) and recall profiles. USE WHEN: "siapa X", "kapan terakhir bahas X", user mentions a person. NOT for tasks/appointments (→ task_manager).
status: active
version: 1
---
# Personal CRM (Orang-orang Penting)

Whenever the user mentions a person (colleague, client, family, friend):

1. Capture quietly with `remember`, subject = the person's lowercase name:
   - predicate=relationship ("klien di proyek X", "adik ipar")
   - predicate=birthday, works_at, phone, preference, last_discussed
   Only store what the user actually said; confidence per certainty.
2. On "siapa X" / "kapan terakhir bahas X" / before a meeting with X:
   `recall` the name and present a compact profile: who they are, open
   threads, important dates, preferences.
3. Birthdays & important dates: when one is stored, offer a yearly heads-up.
   Use `schedule_task` (recurrence weekly is wrong here — schedule a one-off
   reminder a few days before, and re-schedule next year's when it fires;
   put that instruction inside the task prompt itself).
4. Relationship upkeep: if the user wants ("ingatkan aku follow up klien"),
   schedule recurring nudges listing contacts not mentioned recently.
5. Corrections supersede: if the user updates a fact ("dia sudah pindah
   kerja"), `remember` the new value — the old one is archived automatically;
   `belief_history` can show the timeline if asked.
