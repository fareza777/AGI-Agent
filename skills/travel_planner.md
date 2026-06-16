---
name: travel_planner
description: Trip planning — research destination, build day-by-day itinerary, budget, packing list. USE WHEN: "mau ke Bali", "rencana liburan ke X", "itinerary 5 hari". NOT for flight/hotel booking transactions.
status: active
version: 1
---
# Travel Planner

When the user plans a trip ("mau ke Bali bulan depan", "buatkan itinerary"):

1. Anchor the constraints: dates, origin, budget, party size, travel style.
   `recall` preferences from past trips (predicate=travel_style, dietary
   needs, dll) before asking.
2. Research with `web_search` + `fetch_url`: attractions, areas to stay,
   local transport, typical costs, weather/season for the dates, and any
   entry requirements for international trips. Use current sources — prices
   and rules go stale.
3. Build the itinerary day-by-day: each day = one section, body bullets
   morning/afternoon/evening with realistic travel time between spots.
   Cluster geographically; don't zigzag the map.
4. Budget as `table {headers: [Pos, Estimasi (Rp), Catatan], rows}` with a
   TOTAL computed via `calculate`.
5. Deliver as docx via `create_document` (itinerary sections + budget
   table). Offer a packing list section tailored to the destination/season.
6. Follow-through: `schedule_reminder` for booking deadlines and a D-3
   "siap-siap" reminder. `remember` where they went and what they liked
   afterwards — it improves every future trip plan.
