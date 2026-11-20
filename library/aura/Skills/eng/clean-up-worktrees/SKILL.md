---
name: clean-up-worktrees
description: >
  Find and remove stale git worktrees, the iOS simulators sim-test created for
  them, and local branches across a directory of repositories. Use when the user
  wants to clean up old worktrees, free disk space taken by Claude worktrees or
  their simulators, remove merged or abandoned branches, or asks about stale
  branches/checkouts. Accepts a working directory and an inactivity threshold in
  days. Also the unattended mode: "auto clean worktrees", "remove merged
  worktrees", "clear idle DerivedData-CLI", "worktrees pile up after a PR merges".
metadata:
  version: "3.3.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/GitHub                   # gh CLI auth + PR conventions
---

## Goal

Scan all git repositories under a given directory, find worktrees that haven't been modified in more than N days, report them with sizes, and remove the ones the user approves.

## Arguments

- **directory** — root directory containing repos (e.g. `~/repos`). Default: current working directory.
- **days** — inactivity threshold (e.g. `7`). Default: `7`.

Use defaults for any arguments not provided — do not ask the user before proceeding.

## Step 1: Discover worktrees

For every git repo under the given directory, list non-primary worktrees:

```bash
for repo in <directory>/*/; do
  if [ -d "$repo/.git" ]; then
    git -C "$repo" worktree list --porcelain 2>/dev/null \
      | grep "^worktree " \
      | tail -n +2 \
      | sed "s|^worktree ||"
  fi
done
```

This gives the filesystem path of every linked worktree across all repos. git always lists the primary checkout first, so `tail -n +2` drops it by position. Matching it by path does not work: git prints the resolved real path (`/Volumes/SSD/...` when the repo sits behind a symlink), while `$repo` keeps the symlinked path and a trailing slash, so the primary checkout would be reported as a removable worktree.

## Step 2: Filter by age and collect metadata

For each worktree path, check last-modified time and compare against the threshold:

```bash
CUTOFF=$(date -v-${DAYS}d +%s)   # macOS
# Linux: CUTOFF=$(date -d "-${DAYS} days" +%s)

for wt in <worktree_paths>; do
  if [ -d "$wt" ]; then
    mtime=$(stat -f %m "$wt")          # macOS
    # Linux: mtime=$(stat -c %Y "$wt")
    mdate=$(stat -f %Sm -t "%Y-%m-%d" "$wt")
    days_old=$(( ($(date +%s) - mtime) / 86400 ))
    if [ "$mtime" -lt "$CUTOFF" ]; then
      size=$(du -sh "$wt" 2>/dev/null | cut -f1)
      echo "$wt | $mdate | ${days_old}d | $size"
    fi
  else
    echo "$wt | MISSING | - | -"   # dangling reference
  fi
done
```

Run `du -sh` for all stale paths in one pass for speed.

## Step 3: Look up the PR for each stale worktree

This step is **required** — always include PR info in the report so the user can make informed decisions (a MERGED PR's worktree is safe to drop; an OPEN PR's worktree is active work).

For each stale worktree, resolve its branch name and then look up its PR via `gh`:

```bash
# Get branch name for a worktree
cd <repo_root>
branch=$(git worktree list --porcelain \
  | awk -v wt="$wt" '\$1=="worktree" && \$2==wt {found=1} found && \$1=="branch" {sub("refs/heads/","",\$2); print \$2; exit}')

# Look up the PR for that branch (any state — open, merged, closed)
gh pr list --head "$branch" --state all \
  --json number,title,state,url --limit 1
```

Batch the `gh` calls per repo to keep things fast. Record `number`, `title`, `state`, and `url` for each branch; if `gh` returns `[]`, the branch has no associated PR (mark as "no PR").

## Step 3b: Find each worktree's simulator

The AuraConsumerApp sim-test scripts give every worktree its own iOS simulator, named `"<device type> (<worktree dir name>)"` — e.g. `iPhone 17 Pro (slack-message-link-63b8ca)` — and nothing deletes it when the worktree goes. Each one holds 4–13 GB. Look them up for all stale worktrees (dangling references included) in one call:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/clean-up-worktrees/scripts/worktree_sims.py" \
  report <worktree_path> <worktree_path> ...
```

It prints JSON: `simulators[]` (each with `worktree`, `name`, `udid`, `state`, `size`) and a `total`. A worktree without a simulator simply has no entry. On a machine without Xcode the list is empty.

## Step 3c: Check what each worktree would lose

`git worktree remove --force` deletes untracked and **gitignored** files without a word, and `git status`
doesn't show ignored files, so a worktree can look clean and still hold the only copy of a run's data:
a gitignored, mode-600 `users.json` of 7.4M uids and its sample were lost that way (2026-11-06). Check
every stale worktree in one call:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/clean-up-worktrees/scripts/worktree_data.py" \
  report <worktree_path> <worktree_path> ...
```

Per worktree it reports ignored and untracked files outside regenerable folders (`node_modules`, build
output, caches, venvs), with path, size and time (never content), uncommitted changes to tracked files,
and commits on no branch and no remote (a detached HEAD). `atRisk: true` means removing it loses
something, or that it couldn't be inspected (`error: "git status failed"`): keep it either way. A
`missing` worktree is a dangling reference with nothing on disk. Commits on the worktree's branch but on no remote are reported as `unpushedBranchCommits`:
they survive the worktree's removal, but not a later branch delete.

## Step 4: Report findings

Present a table grouped by repo. The PR column is **mandatory** — always include it. The Simulator column shows the size of the worktree's simulator from Step 3b, or `—` when it has none; mark a `Booted` one as `booted` so the user knows it will be shut down. The **Local data** column comes from Step 3c: `—` when nothing is at risk, otherwise the counts and size (`3 ignored files, 1.2 GB`, `2 modified`, `1 commit on no branch`).

```
### RepoName — N worktrees

| Worktree | Last Modified | Age | Size | Simulator | Local data | PR | State |
|----------|--------------|-----|------|-----------|------------|----|----|
| branch-name | 2026-04-15 | 29d | 4.7 GB | 7.0 GB | — | [#123](url) short title | MERGED |
| other-branch | 2026-04-10 | 34d | 2.1 GB | — | 3 ignored files, 1.2 GB | — no PR — | — |
```

Show total size across all stale worktrees, with the simulator total alongside it. Note any **dangling references** (git metadata pointing to a path that no longer exists on disk).

When proposing removal, group by PR state so the user can make a clear call: MERGED/CLOSED PRs are typically safe; OPEN PRs are active work; no-PR worktrees are often abandoned experiments.

**A worktree with local data at risk is kept by default**, whatever its PR state. List it apart, with its
largest files (path and size), and remove it only on the user's yes for that worktree after they've seen
the list. Before removing one, log what it held:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/clean-up-worktrees/scripts/worktree_data.py" \
  log <worktree_path> --log ~/.claude/clean-up-worktrees/removed.jsonl
```

## Step 5: Confirm and remove

Ask the user: "Remove all N worktrees (~X GB)?" or offer to remove by repo.

On confirmation, remove each worktree using `git worktree remove --force` from the **owning repo's root** — not `rm -rf`. Then prune dangling references with `git worktree prune`.

```bash
# For each stale worktree, determine its owning repo:
# The repo root is the closest ancestor that contains a .git directory.

cd <repo_root>
git worktree remove --force <worktree_path>

# After all removals, prune dangling refs:
git worktree prune
```

Remove them repo by repo (batch all removals within a repo into one shell command for speed). Always run `git worktree prune` in each affected repo after removals.

Then delete the simulators of the worktrees that were removed — pass exactly those paths, never a worktree the user chose to keep:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/clean-up-worktrees/scripts/worktree_sims.py" \
  delete <removed_worktree_path> ... --confirm
```

The script deletes each match with `xcrun simctl delete`, shutting down a `Booted` one first — its worktree is gone, so nothing can still need it. It returns `deleted[]`, `freed` and `skipped[]` (a simulator `simctl` refused to delete, with its `reason`).

## Step 5b: Offer the primary-checkout simulators

Outside worktrees, sim-test labels its simulator `main` (a repo's primary checkout) or `ci` — e.g. `iPhone 17 Pro (main)`. These belong to no worktree, so Steps 3b–5 never touch them; they are offered separately. Run this step even when no worktrees were stale:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/clean-up-worktrees/scripts/worktree_sims.py" \
  report --checkout
```

If `simulators[]` is empty, skip to Step 6. Otherwise list them in their own table and ask on their own — never fold them into the worktree confirmation:

```
### Primary-checkout simulators — N (~X GB)

| Simulator | Runtime | State | Size |
|-----------|---------|-------|------|
| iPhone 17 Pro (main) | iOS-27-0 | Shutdown | 6.1 GB |

sim-test recreates these on the next run in the primary checkout. Delete them?
```

On a yes:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/clean-up-worktrees/scripts/worktree_sims.py" \
  delete --checkout --confirm
```

A `Booted` one is skipped (`skipped[]`), since a session in the primary checkout may be running a test on it.

## Step 6: Summary

Report what was removed and total space freed, including simulators and any that were skipped:

```
Removed N worktrees across M repos — freed ~X GB
Deleted K worktree simulators (B of them booted, shut down first) — freed ~Y GB
Deleted P primary-checkout simulators (main/ci) — freed ~Z GB
Skipped J simulators (simctl refused):
  - iPhone 17 Pro (branch-name) — <reason>
```

## Unattended mode: remove merged worktrees and idle build output

Nothing removes a worktree when its PR merges, and each iOS worktree rebuilds ~10 GB of `DerivedData-CLI`. `auto_clean.py` does the two removals that are safe without asking. It is a dry run unless `--apply` is passed:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/clean-up-worktrees/scripts/auto_clean.py --root "$AURA_REPOS_DIR"            # report what it would do
python3 ${CLAUDE_PLUGIN_ROOT}/skills/clean-up-worktrees/scripts/auto_clean.py --root "$AURA_REPOS_DIR" --apply    # do it, log to ~/.claude/clean-up-worktrees/auto.jsonl
```

`--root` (or `AURA_REPOS_DIR`) is the folder holding the clones; there is no default. A worktree is removed only when its newest PR is MERGED, its HEAD is that PR's head or an ancestor, Step 3c finds nothing at risk (`DerivedData-CLI` and `TestResults` count as regenerable, not data), its HEAD has not moved for `--worktree-idle-days` (1), and no process or booted simulator uses it. Its sim-test simulators are deleted with it; a simulator that cannot be deleted is printed under `simulators` and makes the run exit 1 (the worktree is gone by then, so `reclaim_disk.py --only orphan-simulators` is the retry). `DerivedData-CLI` under any checkout (primary included) is deleted when nothing inside it changed for `--derived-idle-days` (2) and it is not in use; an empty `.keep-derived-data` file in the checkout keeps it. A `gh` failure skips that repo, and a failed `lsof`, `ps` or `simctl` check removes nothing; an unknown PR state or process state is never "merged" or "idle". Primary checkouts, open or on-hold PRs, detached HEADs and trees with data at risk are never touched. Branches are kept; Phase 2 handles them. Read the printed `skippedByReason` before trusting a quiet run. Scheduling it is a separate decision for Jerome.

## Key rules (worktrees)

- Use `git worktree remove --force`, never `rm -rf` — this keeps git metadata consistent
- Always `git worktree prune` after removals to clean up dangling references
- Group removals by repo so each repo only needs one `cd`
- Dangling references (missing paths) are cleaned with `git worktree prune` only — no `rm` needed
- Never remove the primary worktree (the repo root itself)
- Never remove a worktree whose Step 3c check shows data at risk (ignored or untracked files outside regenerable folders, uncommitted changes, commits on no branch) without the user's yes for that worktree, and log what it held first. A worktree that "looks clean" in `git status` may still hold gitignored data
- Show size per worktree so the user can make informed decisions
- If a path is inside `.claude/worktrees/`, it's a Claude Code agent worktree — safe to remove if stale and Step 3c shows nothing at risk
- Always include PR info (number, title, state, URL) in the report — never skip the `gh pr list` lookup, even for `.claude/worktrees/` branches
- Delete a worktree's simulator only together with its worktree, and only through `worktree_sims.py` — it matches the exact name `"<device type> (<worktree dir name>)"` and never touches shared devices like a plain `iPhone 17 Pro` or Xcode's stock `iPad Pro 13-inch (M4)`
- A `Booted` simulator of a worktree being removed is listed like any other and shut down before deletion
- `main`/`ci` simulators get their own table and their own yes (Step 5b); a booted one is skipped

---

## Phase 2: Stale local branches

After the worktree cleanup is complete, check for stale local branches across all repos.
Run this phase unconditionally — it catches merged or abandoned work that no worktree cleanup reaches.

### Step 7: Discover stale local branches

For each repo under the directory, list local branches not updated in more than **7 days**
(use the same `--days` threshold as Phase 1 if the user provided one, otherwise default to 7).
Exclude `main`, `master`, and `develop`.

```bash
DAYS=7
CUTOFF=$(date -v-${DAYS}d +%s)   # macOS; Linux: date -d "-${DAYS} days" +%s
NOW=$(date +%s)

for repo in <directory>/*/; do
  if [ ! -d "$repo/.git" ]; then continue; fi
  reponame=$(basename "$repo")

  git -C "$repo" for-each-ref \
    --format='%(refname:short)|%(committerdate:unix)|%(committerdate:short)' \
    refs/heads/ 2>/dev/null \
    | grep -vE '^\(main|master|develop\)\|' \
    | while IFS='|' read -r branch epoch date; do
        [ -z "$branch" ] && continue
        days_old=$(( (NOW - epoch) / 86400 ))
        if [ "$epoch" -lt "$CUTOFF" ]; then
          default=$(git -C "$repo" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null \
                    | sed 's|refs/remotes/origin/||')
          [ -z "$default" ] && default="main"
          merged=$(git -C "$repo" branch --merged "$default" 2>/dev/null \
                   | grep -c "^\*\? *${branch}$" || true)
          echo "$reponame|$branch|$date|${days_old}d|$merged"
        fi
      done
done
```

### Step 8: Look up PR status for each stale branch

Batch per repo — one `gh pr list` call per repo is far faster than one call per branch.
Use `python3` to parse the compact JSON output (avoids `jq` newline issues).

```bash
gh pr list --repo <org>/<repo> --state all \
  --json number,title,state,url,headRefName --limit 300 2>/dev/null \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for pr in data:
    print(f\"{pr['headRefName']}|||{pr['number']}|||{pr['state']}|||{pr['url']}|||{pr['title']}\")
" > /tmp/prs_<repo>.txt
```

Match branches to PRs in a second pass using the cached files.

### Step 9: Report findings

Group branches into three buckets — present all three so the user can decide in one go:

```
### Group A — Safe to delete (merged PR or merged into main): N branches

| Repo | Branch | Age | PR | Status |
|------|--------|-----|----|--------|
| AuraConsumerApp | claude/some-branch | 19d | [#880](url) feat: ... | MERGED |
| Aura | fix/old-fix | 40d | — | merged-into-main |

### Group B — No PR, not merged (review needed): N branches

| Repo | Branch | Age | Note |
|------|--------|-----|------|
| AIVoiceDemo | feat/abandoned | 61d | no PR |

### Group C — Keep (OPEN PR): N branches

| Repo | Branch | Age | PR |
|------|--------|-----|----|
| AuraConsumerApp | rohan/active-work | 11d | [#985](url) OPEN |
```

**Classification rules:**
- **Group A (safe):** merged into default branch (`git branch --merged` returns 1) **or** has a MERGED or CLOSED PR
- **Group B (uncertain):** no PR found and not merged into main — may be abandoned experiments or in-progress work
- **Group C (keep):** has an OPEN PR — do not delete

### Step 10: Confirm and delete

Ask the user which groups to delete. Offer Group A as the recommended default.

On confirmation, delete repo by repo using `git branch -D` (force delete — required for
branches not merged into local main even when the upstream PR is merged):

```bash
cd <repo_root>
git branch -D branch-a branch-b branch-c
```

If a branch is currently checked out (git error: "used by worktree"), skip it silently
and note it in the summary — the user needs to `git checkout main` in that repo first.

### Step 11: Final summary

Report what was deleted and what was skipped:

```
Deleted N branches across M repos.
Skipped K branches (currently checked out — switch to main first to delete):
  - <repo>: <branch>
```

## Key rules (branches)

- Use `git branch -D` (force), never manually deleting `.git/refs/heads/` files
- Deleting branches **does not free disk space** — only worktree deletions do; state this clearly if the user asks
- Batch `gh pr list` per repo, not per branch — 1 API call per repo vs 1 per branch
- Never delete a branch that is currently checked out; git will error gracefully — surface the skips
- Group A is safe to auto-suggest; Group B warrants user review; Group C must never be deleted
- A branch merged into main is always safe to delete even if it has no GitHub PR
