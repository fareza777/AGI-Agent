---
name: tech_comparison_report
description: Generate polished docx comparison report between two tech products/models (pricing, benchmarks, capabilities) using web research + python-docx low-level with native tables and citations. USE WHEN: "bandingkan X vs Y", "laporan perbandingan X dan Y", "buat laporan word yang detail dan rapi" untuk komparasi produk/model AI. NOT for generic long-form report (→ docx_report) or just web research (→ research_brief).
status: draft
version: 1
---
1. Confirm scope via short question or infer from phrasing: which two products/versions, target audience, dimensions to cover (price / benchmark perf / features / capabilities / recommendations).
2. Run multi-query web_research via web_search in parallel:
   - Pricing per million tokens (input & output) — both official + secondary
   - Benchmarks: SWE-Bench, MMLU, GPQA, HumanEval, τ²-bench, agentic-coding scores
   - Capabilities: context window, multimodal, tools/function-calling, sparse-attention, fine-tuning
   - Release notes / changelog / known limitations
3. fetch_url the official pricing/release pages (e.g. platform docs, vendor news) — if 404, fall back to authoritative secondary sources and note them.
4. If either version label is speculative or not verifiably released, prepend an explicit disclaimer at the top of the report (e.g. "Label versi mengikuti permintaan user; sebagian mungkin belum dirilis publik — verifikasi sebelum adopsi").
5. Write a structured markdown draft to `workspace/` containing:
   - Title + date + disclaimer
   - Executive summary (3–5 bullets)
   - Pricing comparison table (rows: input, output, cached, batch; cols: product A, product B)
   - Benchmark comparison table (rows: benchmark names; cols: scores + source)
   - Capabilities comparison table (rows: feature; cols: A, B, notes)
   - Pros/cons / use-case recommendation
   - Sources / footnotes section
6. Convert to polished `.docx` via `run_python` + `python-docx` (NOT `create_document` for full control) — apply:
   - Corporate-blue Heading 1, monochrome Heading 2 styles
   - Justified body 11pt
   - Banded native tables (header row shaded, alternating row fill)
   - Bold key numbers, footnote-style citations `[1]` `[2]`
   - Page numbers in footer, A4 margins
7. Save to workspace folder with descriptive filename (e.g. `Laporan_Perbandingan_X_vs_Y.docx`) and deliver to user.
8. Offer iteration hooks: tambah diagram ASCII/bagan, tambah bab rekomendasi, revisi angka benchmark, swap dimensi perbandingan.
