---
name: repo_writer
description: Commit and push local git changes safely. USE WHEN: "commit perubahan", "push ke remote", "bikin branch", "stash dulu". NOT for reading repo state (→ repo_review) or explaining code (→ code_assistant).
status: active
version: 1
---
# Repo Writer (Commit & Push)

The git tool is **read-only by default** (`ENGRAM_GIT_READ_ONLY=1`). When
the operator flips it to `0`, write subcommands become available: `add`,
`commit`, `checkout`, `branch -d`, `stash`, `tag`, `fetch`, `pull`, `push`,
`merge`, `rebase`, `cherry-pick`, `restore`, `rm`.

**Destructive patterns are blocked in BOTH modes** — never overrideable by
this skill: `push --force/-f`, `reset --hard`, `clean -fd/-fdx`. To run
those, do them in your own terminal, not via the bot.

## When to use (not the read-only skills)

- `repo_review` is for `git status` / `log` / `diff` only — use it for read.
- `code_assistant` is for understanding code, not for committing it.
- This skill is **only** for write actions. If you only need to read,
  don't load it — `git` tool with read subcommands is enough on its own.

## Procedure for a commit

1. `git` with `status` (read, fine in both modes) to see what's dirty.
2. `git` with `diff` to review the actual change.
3. `git` with `add <file>` for each file. **Never** `add -A` / `add .`
   unless the user just asked for "commit everything" — staged-but-unintended
   files happen that way.
4. `git` with `commit -m "short subject"` — the message should be a real
   sentence describing WHY, not WHAT (the diff shows what).
5. `git` with `log --oneline -n 3` to confirm the commit landed.

If the commit message convention is unclear (e.g. "fix:", "feat:"),
**ask the user once**, then `remember` the answer (subject=user,
predicate=commit_style).

## Procedure for a push

1. Confirm the user actually wants to push. "commit" alone stops at the
   local repo. Push only when they said "push", "kirim ke remote", or
   gave a workflow that obviously implies it.
2. `git` with `remote -v` to see the remote URL and target branch.
3. `git` with `log --oneline origin/<branch>..HEAD` to see what would go
   up. If empty, there's nothing to push.
4. `git push origin HEAD:<branch>` — push the current HEAD to the
   named branch. **Do not** use `git push` without a refspec when the
   local branch name differs from the remote tracking branch.
5. `--force` and `-f` are blocked. If the push is rejected ("non-fast-
   forward"), stop and tell the user — don't try to bypass.

## Branching

- New work: `git checkout -b feat/<short-name>` based on the current branch.
- Confirm with the user before deleting branches (`branch -d` is safe,
  `-D` is not in the allowlist and would be refused).

## Stashing

- `git stash push -m "msg"` to set aside WIP before switching context.
- `git stash pop` to bring it back. If conflicts, stop and surface them
  to the user — don't auto-resolve.

## Things this skill will refuse

- `git push --force`, `git push -f`, `--force-with-lease` — blocked, always.
- `git reset --hard` — blocked, always.
- `git clean -fd/-fdx` — blocked, always.
- `git add -A` / `add .` without explicit user ask — propose the file list first.
- Any subcommand not in the allowlist (config error or new git feature) — say so.

## Trust caveat

Write access means the agent can change history on any repo inside the
allowed directories. Keep commits small, review the diff before staging,
and never commit secrets (`remember` doesn't put them in commits, but a
user's `git diff` output might surface them — `remember` the rule, not
the value).
