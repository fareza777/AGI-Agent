---
name: gdrive_workspace_saver
description: Save created output files (laporan, foto, spreadsheet, presentasi, surat) to the user's Google Drive workspace at G:\My Drive\engram workspace\ inside the right file-type subfolder.
status: draft
version: 1
---
When you've created an output file for the user, save it to their Google Drive workspace — `G:\My Drive\engram workspace\` — inside a subfolder that matches the file type. Use this every time the user says things like "simpan di workspace", "save ke G: drive", or after creating any laporan/foto/spreadsheet/surat/presentasi.

**Root path (non-negotiable):** `G:\My Drive\engram workspace\`. The path `G:\engram workspace\` does **not** exist on the Google Drive mirror — `G:\` is the drive root and user files live under `My Drive\`.

**Subfolder map** (pick by file type; default to `Lainnya\`):
1. `Laporan\` — .docx laporan, notulen, makalah, white paper
2. `Foto\` — .jpg, .png, .pdf gambar/dokumentasi (including extracted from `create_document`)
3. `Spreadsheet\` — .xlsx
4. `Presentasi\` — .pptx
5. `Surat\` — surat resmi, lamaran, izin, pengunduran diri, kuasa
6. `Lainnya\` — anything that doesn't fit

**Critical tool quirks** (Windows + Google Drive mirror):
- The `make_dir` tool **fails** on G: drive paths with `FileNotFoundError`. Always create directories via `run_shell` with `mkdir`, and wrap paths containing spaces in double quotes.
- The `create_document` tool auto-extracts embedded images into a stray `images\` subfolder inside the workspace root. Move its contents into `Foto\` and remove the empty folder.

**Procedure:**
1. Classify the created file → pick the matching subfolder from the map above.
2. Run `list_dir` on `G:\My Drive\engram workspace\<subfolder>` to check if it exists.
3. If it does not exist, create it via `run_shell`: `mkdir "G:\My Drive\engram workspace\<subfolder>"`.
4. Move the file via `run_shell`: `move "<source_full_path>" "G:\My Drive\engram workspace\<subfolder>\<filename_with_ext>"`.
5. If `create_document` was the source, run `list_dir` on `G:\My Drive\engram workspace\images`. If it exists, `run_shell`: `move "G:\My Drive\engram workspace\images\*" "G:\My Drive\engram workspace\Foto\"` then `rmdir "G:\My Drive\engram workspace\images"`.
6. Verify the final layout with `list_dir` on the destination subfolder.
7. Send the file to the user via `send_file` using its final G: drive path.
8. If the workspace root or subfolder structure changed, update the user fact via `remember` (subject=`user`, predicate=`engram_workspace_folder`, value=`G:\My Drive\engram workspace\`) so future sessions know where to save.

Report the final G: drive path back to the user (e.g., `📄 G:\My Drive\engram workspace\Laporan\<file>.docx`).
