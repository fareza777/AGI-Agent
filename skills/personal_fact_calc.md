---
name: personal_fact_calc
description: Calculate personal time-based facts (age, tenure, future projections) from stored user memory facts.
status: draft
version: 1
---
1. Identify which personal time-based fact the user is asking about (current age, future age, tenure at job/organization, time since event, time until next anniversary, etc.).
2. Use `recall` to fetch the relevant stored fact (date_of_birth, asn_start_date, works_at, etc.). If the exact predicate is missing from memory, ask the user to supply it rather than guessing.
3. If the stored fact lacks day/month precision (e.g., only year stored), explicitly flag the result as having a ±1 month or ±1 year margin in the final answer.
4. Use `calculate` to compute the duration or projected date, with the current date (2026-06-10 in this case, or `recall` the session date) as the reference point. For multi-step calcs (e.g., days until next birthday, years + months + days tenure), break them into separate `calculate` calls and present each component.
5. If the user supplies a new precise date (e.g., "saya lahir 9 Agustus 1984"), use `remember` to store/update the predicate with the exact value, then re-run the calculation to confirm the new precise answer.
6. Present the final answer as a table containing: input fact, reference date, computed result (in years, years+months+days, or projected date as appropriate), confidence, and any margin/precision notes.
7. Proactively offer to store any newly supplied precise dates (`remember`) so future calculations of the same fact can be automatic and exact.
