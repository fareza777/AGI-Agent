---
name: meal_planner
description: Plan weekly menus, suggest recipes from available ingredients, and build grocery shopping lists.
status: active
version: 1
---
# Meal Planner

When the user asks "masak apa ya", wants a weekly menu, resep, or daftar
belanja:

1. `recall` food constraints first: alergi, diet, dislikes, budget, anggota
   keluarga (predicates like food_allergy, diet, food_dislikes). These are
   safety-relevant — never suggest something memory says they can't eat.
   `remember` new constraints the moment they're mentioned.
2. "Masak apa dari bahan ini" → suggest 2–3 realistic dishes using mostly
   those ingredients; `web_search` a recipe only if you need exact steps or
   it's an unfamiliar dish.
3. Weekly menu: 7 sections (Senin–Minggu) with makan utama per day; vary
   protein and method; reuse ingredients across days to cut waste and cost.
4. Grocery list: aggregate ingredients across the menu into a
   `table {headers: [Bahan, Jumlah, Perkiraan Harga (Rp)], rows}` grouped by
   category (protein, sayur, bumbu...), total via `calculate`.
5. Deliver menu+list as docx or xlsx via `create_document`; short answers
   stay in chat. Offer a weekly `schedule_task` ("tiap Minggu sore buat
   menu minggu depan") and log favorite dishes to memory so menus improve.
