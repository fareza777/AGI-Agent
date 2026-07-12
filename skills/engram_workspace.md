---
name: engram_workspace
description: Save finished deliverables (laporan, foto, spreadsheet, presentasi, surat, dokumen) to the user's Google Drive workspace, organized into type-based subfolders. USE WHEN the user says "simpan di workspace", "save ke engram", "simpan ke G:", or asks to store a generated file on their Drive.
status: active
version: 2
---

Save the user's finished files to their Google Drive workspace.

## Canonical root (do NOT guess)

The root is exactly:

    G:\My Drive\engram workspace\

NEVER use `G:\engram workspace` — that path does not exist. `G:\` is the Google
Drive mount and you **cannot create folders at its root** (make_dir/mkdir there
fail with WinError 2). Real files live under `My Drive\`. Do not probe the drive
root and do not loop retrying it — if a write fails, the path is wrong, not the
tool.

## Build local first, then copy (avoids the folder dance)

1. **Generate the document in the LOCAL workspace first** (fast, no Drive quirks):
   `create_document` / `officecli` write the file into `workspace/`.
2. **Ensure the destination ONCE** with `make_dir`:
   `make_dir("G:\My Drive\engram workspace\<Subfolder>")` — parents are created
   automatically. make_dir handles spaces fine; you do NOT need run_shell/mkdir.
3. **Move the finished file** there with `move_file`. The file is also delivered
   to the user over Telegram regardless of the Drive copy.

## Subfolder map (by file type; default `Lainnya\`)

- `.docx` laporan / surat / proposal / makalah  → `Laporan\`
- `.xlsx` spreadsheet / tracker                  → `Spreadsheet\`
- `.pptx` presentasi / deck                      → `Presentasi\`
- `.jpg` / `.png` foto / gambar / dokumentasi    → `Foto\`
- anything else                                  → `Lainnya\`

## Don't burn tool turns

One correct `make_dir` under `G:\My Drive\engram workspace\` is all it takes. If
a path fails, STOP and fix the path (almost always the missing `My Drive\`
prefix) — never fire a chain of run_shell / run_python / mkdir attempts.

## Signature facts

When the user gives identity facts used in official documents (full_name,
jabatan, NIP), store them with `remember` (subject `user`; predicates
`full_name`, `jabatan`, `nip`; kind `fact`, confidence 0.95).
