---
name: engram_workspace_save
description: Save generated deliverables (laporan, foto, spreadsheet, presentasi, surat, dokumen) to the user's engram workspace folder on Google Drive, organized into subfolders by file type. USE WHEN: user says 'save ke engram workspace', 'simpan di G:', or any time a final deliverable is produced for Fajar. NOT for intermediate drafts in the local E: workspace (→ file_organizer) or when user names a different save path.
status: draft
version: 1
---
1. Resolve the canonical save root: `G:\My Drive\engram workspace\`. Confirm it exists with `list_dir` on `G:\My Drive\`; if missing, create with `make_dir`.
2. Map each file type to a subfolder under the root (create with `make_dir` if absent):
   - `.docx` laporan/surat/proposal/official letter → `Laporan/`
   - `.xlsx` spreadsheet/tracker → `Spreadsheet/`
   - `.pptx` presentasi/deck → `Presentasi/`
   - `.jpg`/`.png`/`.jpeg` foto/gambar/dokumentasi → `Foto/`
   - `.pdf` → `PDF/`
   - `.md`/`.txt` draft/catatan kerja → `Draft/`
   - mixed/uncategorized → `Lainnya/`
3. If a tool (e.g. `create_document`) auto-extracted images into a stray `images/` folder, move the files into the correct subfolder with `run_shell` `move` and `rmdir` the empty folder.
4. Copy/move the final file to the resolved subfolder path using `run_shell` `copy` or `move`.
5. Deliver to user with `send_file` using the full G: path.
6. In the reply, state the exact final path and the subfolder used so the user can verify.
7. Note: the agent's local scratch workspace (`E:\agents\engram-ai-agents\Engram-Agent\workspace\`) is NOT the same as the user's `G:\My Drive\engram workspace\` — do not confuse them; local path is for drafts only.
