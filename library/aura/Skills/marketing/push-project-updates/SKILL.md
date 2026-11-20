---
name: push-project-updates
description: >
  Pull the latest upstream changes, resolve any conflicts, then review and push all local
  changes directly to the project's main branch. Use when someone says "push my changes",
  "sync my work", "push updates to the project", "commit and push", "save my changes",
  "push to main", or "share my updates with the team".
metadata:
  version: "3.0.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
---

# Push Project Updates

Pull upstream changes, resolve any conflicts, then push local changes directly to main.

---

## Phase 1: Check Git State

Confirm we're in a git repo and capture the current state:

```bash
git status 2>&1
git rev-parse --abbrev-ref HEAD 2>&1
git remote get-url origin 2>&1
```

If not in a git repo, tell the user to open Claude Code from inside the project folder.

Check for uncommitted changes:

```bash
git diff --stat 2>&1
git diff --cached --stat 2>&1
```

Note any staged or unstaged changes — we'll return to these after pulling.

---

## Phase 2: Pull Latest from Upstream

Stash any uncommitted changes so the pull is clean:

```bash
git stash 2>&1
```

Note whether anything was stashed.

Pull latest from main:

```bash
git pull origin main 2>&1
```

Capture the result. Three possible outcomes:

### A — Clean pull (no conflicts)

Output contains "Already up to date" or a clean fast-forward. Restore stash and proceed to Phase 4:

```bash
git stash pop 2>&1
```

### B — Merge conflicts

Output contains "CONFLICT". Go to Phase 3.

### C — Pull fails (network, auth, etc.)

Tell the user what went wrong and stop. Common fixes:
- **Auth error**: Run `gh auth status` and re-authenticate if needed
- **Network error**: Check VPN or internet connection
- **No upstream**: The branch may not have a remote set — run `git push -u origin main` first

---

## Phase 3: Resolve Conflicts (only if conflicts exist)

List the conflicting files:

```bash
git diff --name-only --diff-filter=U 2>&1
```

Tell the user:

> There are conflicting changes from upstream in these files:
> [list the files]
>
> Would you like me to resolve the conflicts automatically? I'll accept both sets of changes
> where possible, and for true conflicts I'll keep the upstream version and append your
> local changes below with a clear marker.

**If the user says yes**, resolve each conflicted file:

For each file in the conflict list:
1. Read the file and identify all conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
2. For each conflict block:
   - If the changes are in different locations in the file (non-overlapping): keep both
   - If the changes directly conflict: keep the upstream (`HEAD`) version, append the local version below with a comment `# Local change — review before keeping`
3. Write the resolved file
4. Stage it:

```bash
git add <file> 2>&1
```

After all files are resolved, complete the merge commit:

```bash
git commit -m "Merge upstream changes into local updates" 2>&1
```

Restore the stash if one exists:

```bash
git stash pop 2>&1
```

Tell the user which conflicts were resolved automatically and flag any that need manual review.

**If the user says no**, restore the stash and abort:

```bash
git merge --abort 2>&1
git stash pop 2>&1
```

Tell the user their local changes are preserved and the push was cancelled.
Stop here.

---

## Phase 4: Review Local Changes

Show all files that have changed relative to upstream:

```bash
git diff origin/main --stat 2>&1
git status --short 2>&1
```

For each changed file, read a brief diff:

```bash
git diff origin/main -- <file> 2>&1
```

Group the changes by type and summarize them to the user. Example format:

> Here's what will be pushed to main:
>
> **Skills updated**
> - `skills/generate-ads/SKILL.md` — updated prompt template for the anxiety vertical
>
> **References updated**
> - `references/character-building-learnings.md` — added 3 new pose learnings
>
> **Output**
> - `output/data.json` — 5 new briefs, 12 new creatives scored
> - `output/tracker.html` — rebuilt tracker

Keep the summary readable — focus on what changed, not the raw diff.

If there are no local changes beyond the merge, tell the user everything is already up to date and stop.

---

## Phase 4.5: Confirm Before Pushing

**Hard gate. Do not stage, commit, or push until the user approves.** This skill pushes
directly to the shared `main` branch — there is no PR review step to catch a mistake, so the
user's explicit go-ahead is the only checkpoint.

After showing the Phase 4 summary, ask:

> Push these changes to `main`? This commits everything above and pushes directly to the
> shared branch — no PR review. Reply yes to proceed, or tell me what to adjust.

Wait for a clear yes. If the user wants to exclude a file, review a specific diff first, or
cancel, follow that — nothing is committed or pushed until they confirm. A summary shown is
not a summary approved; do not treat silence or an unrelated reply as consent.

---

## Phase 5: Stage and Commit

Stage all changes:

```bash
git add -A 2>&1
```

Generate a commit message that reflects the actual changes (use the summary from Phase 4 to write it):

```bash
git commit -m "<generated message based on changes>" 2>&1
```

The commit message should be concise and descriptive — e.g.:
- "Add anxiety vertical briefs and update character learnings"
- "Score batch 3 creatives and update tracker"
- "Update generate-ads workflow and add 2 new reference formats"

---

## Phase 6: Push to Main

Push directly to main:

```bash
git push origin main 2>&1
```

**If push succeeds**, confirm to the user:

> ✓ Changes pushed to main. Your team will see the updates next time they pull.

**If push is rejected** (remote has new commits since our pull):

```bash
git pull origin main --rebase 2>&1
git push origin main 2>&1
```

If this also fails, tell the user and offer to run the full pull + resolve flow again.

---

## Edge Cases

**No changes to push after pulling:**
Tell the user everything is already in sync with main.

**Untracked files the user may not want to push:**
Before staging, flag any untracked files that look like generated output or secrets (e.g., `*.env`, large image files, `__pycache__`). Ask the user to confirm before including them. Check `.gitignore` first — if they're already ignored, skip them silently.

**Merge produces a very large diff:**
If more than 20 files changed, summarize by directory rather than file-by-file.

**User is on a feature branch, not main:**
Tell the user they're on `<branch>` and ask if they want to merge to main or push the branch as-is.
