---
name: shell_runner
description: Run shell commands for build/test/git/install — only when sandboxed file tools can't do it. Use when user says "run", "build", "test", "install", "npm", "pip", "git clone".
status: active
version: 1
---
# Shell Runner (Terminal / CLI)

`run_shell` is a sandboxed subprocess in the workspace. **It is OFF by
default** — the operator must set `ENGRAM_ENABLE_SHELL_TOOL=1` in `.env`.
Check first: if the env var is unset, you cannot run shell, so the only
option is `read_file` / `write_file` / `create_document` and a written
suggestion in chat. Don't pretend a command ran if it didn't.

## When to use shell (not the file tools)

- `git clone`, `git pull`, `git push`, `git checkout -b` — these are
  stateful and the file tools can't do them.
- `pip install`, `npm install`, `npm run build`, `npm test` — package
  management and build tools.
- `python -m pytest`, `node script.js`, `npx tsc` — running test suites
  and scripts the user wrote.
- Anything that spawns a long-running process (`npm run dev` with
  watch) — set a short `timeout` parameter; the run_shell tool has a
  hard ceiling of `config.SHELL_TIMEOUT_SEC` (default 30s). Tell the
  user when you hit it.
- One-off `curl`/`wget` for binary downloads — note that
  `web_search`/`fetch_url` are better for text content; shell is for
  binaries, archives, raw HTTP that needs auth.

## When NOT to use shell

- Reading a file → `read_file` (safer, sandboxed, no encoding surprises).
- Editing a file → `write_file` (atomic, no shell quoting hell).
- Searching text → `search_files` (scoped, walks allowed dirs only).
- The user can just run it themselves — say so. Don't burn a tool call
  on `pip list` when the user can paste the output in 2 seconds.

## Procedure

1. **Confirm what's needed**: name the command, what it does, and the
   expected output before running it. "I'll run `pytest tests/` in the
   workspace to confirm the substrate tests still pass." The user can
   cancel if it's the wrong thing.
2. **Run with the right cwd**: `run_shell` defaults to `WORKSPACE_DIR`.
   For commands that must run inside a specific repo, prepend
   `cd /path && ...` or use a single `cd /path && pytest` chain —
   don't rely on the tool to follow you across calls (subprocesses
   don't persist cwd).
3. **Capture and interpret**: stdout AND stderr come back. On non-zero
   exit, the answer is in stderr; quote the relevant lines. Don't
   paraphrase errors — paste them.
4. **Timeouts and runaway processes**: the tool kills the process at
   `config.SHELL_TIMEOUT_SEC`. For long jobs, tell the user to run it
   themselves and `send_file` the output, or set up a `schedule_task`.
5. **Idempotency**: prefer commands you can run twice safely
   (`pip install -e .`, `pytest -q`) over one-shots that mutate state
   irreversibly (`rm -rf`, `git reset --hard`). Ask before the latter.

## Output format in chat

One line: "exit=N, took ~Xs" + the most relevant snippet of output
(truncated to ~20 lines). Then 1–2 sentences of interpretation. If the
user asked a build/test question, lead with **pass/fail**, not the
logs.

## If shell is disabled

Tell the user explicitly: "I can't run shell commands — set
`ENGRAM_ENABLE_SHELL_TOOL=1` in `.env` and restart me." Then offer the
nearest equivalent (write a script file for them to run, paste
suggested commands, etc.). Don't say "I'll just run it" and then not.
