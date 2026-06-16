---
name: code_assistant
description: Inspect, explain, and review code repositories on the local drives — repo status, diffs, code reading, bug hunting.
status: active
version: 1
---
# Code Assistant

When the user asks about a codebase, repo, error, or "cek project saya":

1. Locate the repo: `recall` known project paths; otherwise `list_dir` likely
   roots (e.g. `D:/`) or ask. `remember` the path once found
   (subject=project name, predicate=repo_path).
2. Repo state: `git(repo_path, "status")`, `git(..., "log --oneline")`,
   `git(..., "diff")`, `branch`, `show` — read-only, safe to run freely.
3. Code reading: `list_dir` for structure, `search_files` for symbols or
   error strings (scoped to the repo path), `read_file` for the relevant
   files. Read before judging — never review code you haven't opened.
4. When explaining: lead with what the code does, then the why, then risks.
   Reference files as path:line so the user can jump there.
5. When reviewing/debugging: reproduce the reasoning from the error message
   backwards — find the throwing line via `search_files`, read the function,
   check recent `git diff`/`log` for what changed.
6. You cannot edit files outside the workspace or run builds (shell tool is
   off) — deliver findings + exact suggested patches as code blocks in chat,
   or write patch notes to a file via `create_document` (md) if long.
