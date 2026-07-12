---
name: officecli_report
description: Build the highest-quality Word (.docx) and PowerPoint (.pptx) reports using the officecli tool — precise headings, styles, tables, charts, and slide layouts. USE WHEN the user asks for a laporan/dokumen/presentasi/slide "yang bagus/rapi/profesional", or any docx/pptx where layout quality matters. Prefer this over create_document for polished deliverables.
status: active
version: 1
---

Use the `officecli` tool to build polished Office files. It runs the officecli
binary in your workspace; you pass the subcommand as `command` (without the word
"officecli"). Deliver the finished file with `send_file`.

**Facts first.** If the report is about the outside world (a product, company,
person, market, benchmark, event), you MUST web_search / fetch_url for the facts
BEFORE building the document — never invent numbers, benchmarks, prices, or
sources. If you can't verify the topic even exists, say so and don't build a
document. See the GROUNDING directive in your context.

**Build in the LOCAL workspace**, not on `G:\`. Create and edit the file in
`workspace/` (fast, no Google-Drive quirks). Only after it's finished and sent,
if the user wants it archived, copy it to `G:\My Drive\engram workspace\<Sub>\`
(see the engram_workspace skill). Never try to build directly under `G:\`.

## Golden workflow (do it in this order)

1. **Create the file** (type comes from the extension):
   - `officecli` → `create laporan.docx`
   - `officecli` → `create slides.pptx`
   If the tool replies that officecli belum terpasang, fall back to
   `create_document` and tell the user how to install it — do NOT pretend the
   file was made.

2. **Add content with real styles** (this is what makes it look professional):

   Word:
   - `add laporan.docx /body --type paragraph --prop text="Ringkasan Eksekutif" --prop style=Heading1`
   - `add laporan.docx /body --type paragraph --prop text="Pendapatan naik 25% YoY."`
   - `add laporan.docx /body --type paragraph --prop text="Metode" --prop style=Heading2`
   - Table: `add laporan.docx /body --type table --prop rows=3 --prop cols=2`
     then fill cells with `set laporan.docx '/body/tbl[1]/tr[1]/tc[1]' --prop text="Kota"`.
   - Chart from data: `add laporan.docx /body --type chart --prop type=bar ...`
     (run `help docx chart` first for exact props).

   PowerPoint:
   - `add slides.pptx / --type slide --prop title="Q4 Report" --prop background=1A1A2E`
   - `add slides.pptx '/slide[1]' --type shape --prop text="Revenue grew 25%" --prop x=2cm --prop y=5cm --prop size=24 --prop color=FFFFFF`
   - Note: `shape[1]` is usually the title placeholder — use `shape[2]+` for body.

3. **Check quality before sending:**
   - `view laporan.docx issues` — fix formatting/structure problems it reports.
   - `validate laporan.docx` — must pass.

4. **Deliver:** call `send_file` with the workspace path (e.g. `laporan.docx`).
   Only say it was sent AFTER send_file succeeds this turn.

## Rules that keep it from breaking

- When unsure of a property or command, run help FIRST instead of guessing:
  `help docx paragraph`, `help pptx shape`, `help docx chart`.
- Paths are quoted and 1-based: `'/body/p[3]'`, `'/slide[1]/shape[2]'`.
- Every attribute goes through `--prop key=value` (there is no `--name`).
- Text with `$` must be single-quoted inside the command: `--prop text='$15M'`
  (otherwise `$15` is stripped).
- Prefer stable-id paths (`@id=`, `@paraId=`, `@name=`) in multi-step edits —
  positional indices shift after insert/remove.
- One artifact at a time; finish and send it before starting the next.

## For specialized decks/docs

officecli ships deeper sub-skills: `load_skill pptx`, `load_skill pitch-deck`
(fundraising only), `load_skill word`, `load_skill academic-paper`. Load ONE
that matches the task via the officecli command `load_skill <name>`, then follow
its printed rules.
