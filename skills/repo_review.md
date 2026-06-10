---
name: repo_review
description: Inspect a git repository's state and summarize it for the user.
status: active
version: 1
---
# Repo Review

When the user asks to check a repo, review changes, or "what's going on in
<repo>":

1. Use `git` (read-only) on the repo path:
   - `status` — uncommitted changes, current branch
   - `log --oneline` — recent commits (capped automatically)
   - `diff` — what changed, if they want detail
   - `branch` / `remote` — context
2. If they want to see specific files, use `read_file` / `list_dir` /
   `search_files` within the repo (the repo path must be in an allowed dir).
3. Summarize plainly: branch, how many uncommitted changes, what the last few
   commits did, anything that looks risky or unfinished.
4. If they ask for a written summary, generate it with `create_document`.
5. You cannot push or commit — git is read-only here. Say so if asked.
