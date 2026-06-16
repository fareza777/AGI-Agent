---
name: drive_navigator
description: Browse, search, organize, and manage files across the full D: drive and G: (Google Drive) — finding documents, tidying folders, locating projects.
status: active
version: 1
---
# Drive Navigator (D: dan G:)

You have FULL file access to drive `D:/` (local) and `G:/` (Google Drive
mirror) plus the workspace. Use absolute paths like `D:/Projects/...` or
`G:/My Drive/...` with the file tools. `C:` and system folders stay blocked.

When the user asks to find, list, organize, or clean up files ("cari file",
"rapikan folder", "ada apa di drive D"):

1. Orient first: `list_dir` the target root (e.g. `G:/`) before assuming
   structure. Google Drive usually has a `My Drive` folder at the top.
2. Searching: `search_files(query, path)` walks recursively and matches
   filenames AND text content. NEVER search from `D:/` or `G:/` directly —
   it is slow and noisy. Narrow to the likeliest subfolder first via
   `list_dir`, then search there.
3. Reading: `read_file` handles UTF-8 text up to ~200KB. Binary files
   (docx/xlsx/images) can't be read as text — for images use `view_image`,
   for documents tell the user what you can/can't open.
4. Organizing: `make_dir` + `move_file` to restructure; propose the plan
   first ("saya akan pindahkan 12 file PDF ke D:/Dokumen/2026/"), execute
   after the user agrees. `move_file` works across the allowed roots.
5. Deleting: `delete_file` removes files and EMPTY folders only. Treat
   deletion as irreversible — always list exactly what will be deleted and
   get explicit confirmation first. When in doubt, move to a `_trash/`
   folder in the workspace instead.
6. Delivering: `send_file` sends any file from the drives to the user on
   Telegram (e.g. "kirim file laporan itu ke sini").
7. `remember` the locations of folders the user cares about (subject=user,
   predicate like folder_keuangan) so next time you navigate straight there.
