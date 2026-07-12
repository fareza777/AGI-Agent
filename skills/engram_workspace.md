---
name: engram_workspace
description: Save agent outputs to the user's personal Engram workspace on G: drive (G:\My Drive\engram workspace) inside type-based subfolders (Laporan, Foto, Dokumen, Surat, Spreadsheet, etc.). USE WHEN: user says "simpan di workspace", "save ke engram", "simpan ke G", or asks to save generated files anywhere by default.
status: draft
version: 1
---
1. **Confirm the base path** is `G:\My Drive\engram workspace\` (this is the user's standing Engram workspace on Google Drive, established 22 Juni 2026). If unsure, call `list_dir` on `G:\My Drive\` first — DO NOT invent folder names from memory.

2. **Identify the file type** of the output the user wants saved:
   - `.docx` laporan/makalah → `Laporan/`
   - Foto/gambar (.jpg, .png) → `Foto/`
   - Surat resmi (lamaran, izin, dll) → `Surat/`
   - Proposal → `Proposal/`
   - Spreadsheet (.xlsx) → `Spreadsheet/`
   - Email draft (text file) → `Email/`
   - Dokumen umum/other → `Dokumen/`
   - Source code → `Code/`

3. **Check if the type subfolder already exists** with `list_dir` on the base path. If it doesn't, create it with `run_shell`: `mkdir "G:\My Drive\engram workspace\<Type>"` (use `run_shell`, not `make_dir` — `make_dir` fails on paths with spaces in the parent segment).

4. **Avoid duplicate folders**: if a variant name exists (e.g. both `Engram Workspace` and `engram workspace`, or `images/` vs `Foto/`), reconcile by moving files into the canonical lowercase type-named folder and removing the duplicate with `run_shell` `rmdir` or `move`.

5. **Save the file** to `G:\My Drive\engram workspace\<Type>\<filename>` using the appropriate tool:
   - For `create_document` output: the file is created in the local workspace, then `run_shell` `move` it to the target folder.
   - For images / raw files: `run_shell` `copy` from `inbox\file_N.ext` to the target folder with a descriptive kebab-case filename (e.g. `kegiatan-upacara-rawajati-22juni2026.jpg`).

6. **Verify with `list_dir`** on the destination subfolder to confirm the file landed where intended.

7. **Save user identity facts** (full_name, position/jabatan, NIP) via `remember` whenever the user provides them in context — these get used as signature blocks in official documents. Subject: `user`, predicates: `full_name`, `jabatan`, `nip`. Kind: `fact`, confidence 0.95+.
