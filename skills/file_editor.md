---
name: file_editor
description: Edit existing files incrementally — surgical search/replace, multi-spot patches, and read-before-write. Use when user says "edit", "fix", "ganti baris ini", "rename function", "patch".
status: active
version: 1
---
# File Editor (Edit Incremental)

When the user wants to **change** a file (not create a new one, not read it):
"edit file X", "ganti baris ini jadi itu", "fix bug di line N", "rename
function Y jadi Z", "tambah field ini", "hapus blok ini".

The only editing tool is `write_file` (full overwrite). Use it surgically —
never rewrite a whole 500-line file to change 3 lines.

## Procedure

1. **Locate**: `read_file` the target (or use the path the user gave).
   Confirm it's the right file and the right section before any write.
2. **Plan the patch**: identify the smallest unique anchor — a few lines
   of context (3 lines above + the changed line + 3 below) is unique enough
   to avoid wrong-spot hits. For function/variable renames, search across
   the whole file first with `search_files` to find every occurrence.
3. **Edit in memory, not in the model**: reconstruct the entire new file
   content in your own context by mentally applying the patch, then
   `write_file` once with the full new content. Do not `read_file`,
   patch one line, `write_file`, `read_file` again to check, etc. —
   that round-trips cost and loses track of state.
4. **Verify after write**: `read_file` the same range (or a few lines
   around the change) and confirm the patch landed. Skim for accidental
   damage (encoding mojibake, lost trailing newline, broken indentation).
5. **One concern per edit cycle**. If the user asked for 3 unrelated changes,
   do them in 3 separate `write_file` calls with a 1-line progress note
   between them, so a failure on #2 doesn't lose #1's work.

## Anti-patterns to refuse

- "Rewrite this file from scratch" when the user asked for a small change —
  ask whether they really want a full rewrite.
- Editing without reading first — you will misplace the anchor and corrupt
  the file. Always `read_file` before any `write_file` that modifies an
  existing file.
- Multiple `write_file` calls in a row on the same file without re-reading —
  each call overwrites the previous, so later edits need the post-patch
  state, not the pre-patch state.
- "Edits" that are actually creates — if the file doesn't exist yet,
  this is not the right skill; just `write_file` it once.

## When the file is huge (>50KB or >1000 lines)

Read in chunks using `read_file` (it truncates at `MAX_FILE_READ_BYTES`,
default 200KB — adjust in config if needed), edit the chunk you have in
context, then read the next chunk. Never load more of a file than you need
to make the change. For multi-spot renames across a large file, prefer
`search_files` to enumerate every occurrence, plan the new content, then
one full `write_file`.

## Diff style in chat reply

After editing, reply with what changed in 2–4 bullets:
- "Renamed `foo()` → `bar()` in `engram/agent.py:142`"
- "Fixed off-by-one in `engram/store.py:88`"
- "Added `import re` at top of `skills/data_analyst.md`"

Cite file:line so the user can jump there. If the edit might have ripple
effects ("renamed a public function"), flag that and ask whether the user
wants to update the callers too — don't silently do it.
