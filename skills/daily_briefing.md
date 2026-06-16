---
name: daily_briefing
description: Personalized morning briefing — goals, reminders, news on followed topics, all in one chat message. USE WHEN: "briefing pagi", "apa agenda hari ini", "ringkasan hari ini". NOT for raw news (→ news_digest) or weekly (→ weekly_review).
status: active
version: 1
---
# Daily Briefing

When the user asks for a briefing, "apa agenda hari ini", or a morning summary:

1. Call `manage_goal` (list) for active goals and `list_reminders` for what's
   scheduled.
2. Call `recall` for recent context (ongoing projects, commitments, people).
3. If the user follows specific topics/markets/news, call `web_search` for the
   latest and `fetch_url` on the top source for each topic.
4. Assemble a short briefing in chat (bullets, no tables):
   • Today's reminders and deadlines
   • Goal progress / next steps
   • 2–4 relevant news/updates with one-line takeaways and links
5. Offer to turn it into a document, or to `schedule_reminder` for any action.
6. If this becomes a routine, suggest saving the user's topic list with
   `remember` so future briefings are automatic.
