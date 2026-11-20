---
name: pull-project-updates
description: >
  Sync the local project with the latest upstream changes on main/master. Stashes any
  working changes, pulls latest, reapplies stash, and resolves conflicts with user
  confirmation. Use when someone says "pull latest", "sync from main", "get latest changes",
  "update my project", "pull updates from the team", or "am I up to date?".
metadata:
  version: "3.0.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
---

# Pull Project Updates

Pull the latest changes from upstream, reapply any local working changes, and resolve
conflicts with user confirmation.

---

## Phase 1: Check Git State

Confirm we're in a git repo and capture current state:

```bash
git status 2>&1
git rev-parse --abbrev-ref HEAD 2>&1
git remote get-url origin 2>&1
```

If not in a git repo, tell the user to open Claude Code from inside the project folder.

Note the current branch. We'll switch to main for the pull.

---

## Phase 2: Switch to Main and Stash Working Changes

### Step 1 — Check for working changes

```bash
git status --short 2>&1
```

If there are uncommitted changes, stash them before switching branches:

```bash
git stash push -m "pull-project-updates: pre-pull stash" 2>&1
```

Note whether anything was stashed.

### Step 2 — Switch to main/master

```bash
git checkout main 2>&1 || git checkout master 2>&1
```

If switching fails (e.g. merge conflict prevents checkout), tell the user:

> You have changes that conflict with switching to main. I'll need to commit or discard
> them before continuing. Would you like me to stash all changes and switch?

If user confirms, run:

```bash
git stash push --include-untracked -m "pull-project-updates: force stash" 2>&1
git checkout main 2>&1 || git checkout master 2>&1
```

---

## Phase 3: Pull Latest from Upstream

```bash
git pull origin main 2>&1
```

Three possible outcomes:

### A — Already up to date

Output contains "Already up to date." Tell the user:

> Your project is already up to date with the latest changes from the team.

If a stash was saved in Phase 2, reapply it:

```bash
git stash pop 2>&1
```

Tell the user their local working changes have been restored. Stop here.

### B — Clean fast-forward

New commits pulled with no conflicts. Continue to Phase 4.

### C — Pull fails (auth, network, no upstream)

Tell the user what went wrong and stop. Common fixes:
- **Auth error**: Run `gh auth status` and re-authenticate
- **Network error**: Check VPN or internet connection

---

## Phase 4: Reapply Stashed Working Changes

If a stash was saved in Phase 2, reapply it on top of the freshly pulled main:

```bash
git stash pop 2>&1
```

Two outcomes:

### A — Clean reapply

No conflicts. Continue to Phase 5 to report success.

### B — Conflicts during stash pop

Output contains "CONFLICT". List conflicting files:

```bash
git diff --name-only --diff-filter=U 2>&1
```

Tell the user:

> After pulling the latest changes, your local working changes conflict with what was just
> pulled in these files:
> [list files]
>
> Would you like me to resolve the conflicts automatically? I'll merge both sets of changes
> where possible. For true conflicts I'll keep the upstream version and mark your local
> changes for review.

**If user says yes**, go to Phase 5 (resolve conflicts).

**If user says no**, abort the stash reapply and restore state:

```bash
git checkout -- . 2>&1
git stash drop 2>&1
```

Tell the user:

> The pull completed but your local working changes were not reapplied. They are saved
> in the stash — run `git stash pop` when you're ready to reapply them manually.

Stop here.

---

## Phase 5: Resolve Conflicts

For each conflicting file:

1. Read the file and identify conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
2. For each conflict block:
   - If changes are in non-overlapping regions: keep both
   - If changes directly conflict: keep the upstream (`HEAD`) version, append the local version below with a comment `# Local change — review before keeping`
3. Write the resolved file
4. Stage it:

```bash
git add <file> 2>&1
```

After all files are resolved, complete the merge:

```bash
git commit -m "Merge upstream pull into local working changes" 2>&1
```

Tell the user which files were auto-resolved and flag any that need manual review.

---

## Phase 6: Report Status

After a successful pull (with or without conflict resolution), confirm to the user:

> Your project is now up to date with the latest changes from the team.
>
> **What was pulled:** [N commits, brief summary from `git log --oneline ORIG_HEAD..HEAD`]
> **Local changes:** [restored from stash / none]
> **Conflicts resolved:** [list files if any / none]

If no stash and no conflicts:

> Your project is up to date with the latest. No local changes were affected.

---

## Edge Cases

**User is already on main with a clean working tree:**
Skip the stash and branch-switch steps. Just pull and report.

**Pull produces a very large changeset (>20 files):**
Summarize by directory rather than file-by-file.

**Remote has diverged (non-fast-forward pull):**
This should not happen on a pull-only workflow. If it does, tell the user the remote
history has changed and offer to run `git pull --rebase` instead.

**No remote configured:**
Tell the user there's no upstream remote set and suggest they check with the team for
the repo URL.
