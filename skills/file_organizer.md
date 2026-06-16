---
name: file_organizer
description: Organize, rename, or tidy files inside the workspace folder. USE WHEN: "rapikan workspace", "rename file ini", "kategorikan file". NOT for D:/G:/ drives (→ drive_navigator) or editing content (→ file_editor).
status: active
version: 1
---
# File Organizer

When the user wants to organize, sort, rename, or tidy files:

1. Call `list_dir` (and `search_files` if needed) to see what's there before
   touching anything.
2. Propose the plan in one short message (which files go where, what gets
   renamed) and proceed unless the action is destructive.
3. Use `make_dir`, `move_file`, `write_file`. Use `delete_file` ONLY when the
   user explicitly asked to delete something, and confirm first — it refuses to
   remove non-empty folders by design.
4. Report the final structure with `list_dir` so the user sees the result.
