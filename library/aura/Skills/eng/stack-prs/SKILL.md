---
name: stack-prs
description: >
  Build and drive a stack of dependent pull requests with the `gh stack` CLI extension — one
  reviewable PR per layer, each based on the layer below. Two entry points: from a Linear
  parent issue, turning its sub-issues into one stack layer each in dependency order within a
  single repo; or ad hoc, splitting multi-part work in the current repo into layers before
  writing code. Owns the full lifecycle: layer design, init/add/submit, PR title and body
  rewrite, per-PR Cursor BugBot review, sync and rebase when a lower layer changes, and
  bottom-first merge. Trigger on "create stacked PRs", "stack these PRs", "make a PR stack",
  "split this into stacked PRs", "implement all subissues in this linear issue", "one PR per
  sub-issue", "break this into dependent PRs", "rebase the stack", "sync my stack", "merge the
  stack", "gh stack", or when a `gh stack` command fails and they want it unstuck. Do NOT use
  for a single self-contained PR (`eng:linear-task`) or reviewing an existing PR
  (`eng:review-pr`).

metadata:
  version: "1.1.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/GitHub  # gh auth, PR conventions, BugBot cycle
    - Tools/Linear  # issue + sub-issue access via MCP
---

# Stacked PRs

A **stack** is an ordered chain of branches rooted on trunk, where each branch carries one PR
based on the branch below it — so a reviewer sees only that layer's diff instead of a 2,000-line
monolith. `gh stack` prints a stack trunk-first, left to right:

```
(main) <- eng-124-user-model <- eng-125-user-api <- eng-126-user-ui
```

Left is the **bottom**, right is the **top**. `eng-124-user-model` is based on `main` and merges
first; `eng-126-user-ui` merges last. `up` moves toward the top, `down` toward trunk. Foundational
work goes at the bottom, code that depends on it above.

This skill wraps the open-source [`github/gh-stack`](https://github.com/github/gh-stack) extension
(MIT) and layers AuraHealth conventions on top: our branch naming, draft-PR-then-BugBot review
cycle, PR body shape, and the Linear sub-issue entry point. Where the two disagree, **our
conventions win** — upstream's own guidance says repository conventions take precedence.

## Step 0: Prerequisites gate

Run this before anything else. Do not proceed on a failure — fix it or tell the user.

```bash
gh extension list | grep -q gh-stack && echo "EXT_OK" || echo "EXT_MISSING"
gh auth status >/dev/null 2>&1 && echo "AUTH_OK" || echo "AUTH_MISSING"
git config --get rerere.enabled     # want: true
git remote | wc -l                  # >1 means remote.pushDefault is required
```

If `EXT_MISSING`, install it and set the two configs — `rerere` makes a conflict you resolve once
replay automatically on every layer above, which matters a lot in a stack:

```bash
gh extension install github/gh-stack
git config rerere.enabled true
git config remote.pushDefault origin
```

**Stacked PRs must be enabled on the repository.** If not, `submit` exits **9**. There is no way
to work around this from the CLI — surface it to the user and stop.

For the Linear entry point, the **Linear MCP server must be connected** (see `/CLAUDE.md` in the
workspace root — never fall back to the Linear API or browser scraping). If it isn't, stop and ask
the user to authenticate it.

## Step 1: Pick the mode

| Situation | Mode |
|---|---|
| User names a Linear parent issue and wants its sub-issues implemented | **Mode A** (Step 2) |
| Another skill hands over an explicit, already-grouped set of sub-issues | **Mode A, scoped** (see below) |
| User wants current/planned work split into dependent PRs, no Linear parent | **Mode B** (Step 3) |
| A stack already exists and the user wants to edit, extend, sync, or land it | **Step 5+** |

### Scoped hand-off from another skill

When the caller supplies an **explicit list of sub-issue ids plus one target repo** — as
[`eng:linear-task`](../linear-task/SKILL.md) multi-agent mode does for a same-repo stack group —
treat that list as the whole job:

- **Do not re-query `list_issues(parentId)`** to rediscover the set. The caller has already grouped
  by repo and owns the sub-issues it did not hand you.
- **Skip the cross-repo hard stop below.** It exists to catch a *user* pointing at a mixed-repo
  parent; applying it to a pre-grouped set would reject work that is correctly scoped, and the
  offer it prints ("hand off to `eng:linear-task`") would bounce the job straight back to the
  caller that sent it — a loop neither side breaks.
- **Touch only the ids you were handed.** Sub-issues in other repos already have their own
  executor; opening a PR for one would duplicate that work.

Still fetch each handed-over sub-issue's content (Step 2a) and order the layers by dependency
(Step 2b) — the caller supplies membership, not layer order.

**Unscoped Mode A only applies when every sub-issue targets the same repo.** A stack is strictly
linear within one repository — one parent, at most one child. If the sub-issues span repos (say
`UserService` + `UserWeb`), a single stack cannot express that. Say so and offer the split:

> ENG-124 and ENG-126 are both UserService, ENG-125 is UserWeb. A stack only works inside one
> repo, so this is either one stack of 2 (UserService) plus a standalone PR for ENG-125, or a
> hand-off to `eng:linear-task` multi-agent mode which fans out across repos. Which do you want?

## Step 2 (Mode A): Linear sub-issues → stack layers

### 2a. Load the parent and its sub-issues

**On a scoped hand-off, skip the `list_issues` call** — read the parent for context and
`get_issue` each id the caller handed you, nothing more.

Use the Linear MCP tools:

```
get_issue(id: "ENG-123")
list_issues(parentId: "ENG-123")
```

Read each sub-issue's title, description, labels, and any `[repo=]` tag. Also read the parent
description — it usually carries the ordering intent the sub-issues don't restate.

### 2b. Order the layers by dependency, not by Linear order

Linear sub-issue order is creation order, and it is **not** a dependency order. Derive the real
order yourself: schema and shared types at the bottom, then service/API code that consumes them,
then UI, then integration tests. A layer must not reference code introduced above it.

Present the plan and **wait for confirmation before creating any branch**:

```
Stack for ENG-123 in UserService (trunk: main)

  1 (bottom)  ENG-124  Add user preference model      rohan/eng-124-user-preference-model
  2           ENG-125  Expose preferences on /users   rohan/eng-125-user-preferences-endpoint
  3 (top)     ENG-126  Preferences tab in settings    rohan/eng-126-preferences-tab

Merge order is bottom-first. Each layer opens as a draft PR.
```

If a sub-issue is blocked, or so vague you'd be guessing, **do not** put it in the stack — ask one
focused question, or leave it out and say why. A wrong layer near the bottom is the expensive
mistake: there is no non-interactive reorder, so fixing it means `unstack` + `init` again.

### 2c. Branch names come from our convention, not upstream's

Upstream suggests `<topic>/<concern>` (`billing/schema`). **We use the repo convention**
(`/CLAUDE.md` → "Naming conventions" → Branch names): `<firstname>/<ticket-id>-<slug>`, one per
sub-issue, slug kebab-cased from the sub-issue title. `gh stack add` takes names verbatim and
keeps slashes, so `rohan/eng-124-user-model` is created exactly as written.

Then go to Step 4.

## Step 3 (Mode B): Ad-hoc stack

No Linear parent. Decide the layers yourself before writing any code, then confirm with the user.
Read [`references/stack-design.md`](references/stack-design.md) — it covers how many layers, what
belongs in each, and when work deserves a separate stack. Branch names still follow
`<firstname>/<ticket-id>-<slug>` when a ticket exists; otherwise `<firstname>/<topic>-<concern>`.

The failure mode this exists to prevent: writing everything on one branch and trying to split it
afterwards. If the work warrants a stack, create the stack first.

## Step 4: Build the stack

Work inside **one worktree** for the whole stack — `gh stack up`/`down`/`checkout` switch branches
in the current working directory, so a worktree per layer defeats the tool. Per `/CLAUDE.md` → "PR
workflow", the worktree lives inside the repo:

**`--base` names a local branch, so the local `main` ref must be current before you start.** The
worktree is detached at `origin/main` (you can't check `main` out twice, and the shared checkout
already holds it) — but `gh stack init --base main` resolves the *local* `main`, which `git fetch`
does not move. If the shared checkout's `main` is behind, the whole stack is built on stale trunk
and every layer needs re-rebasing later. Assert it first, and fast-forward in the shared checkout
if it's behind:

```bash
git -C "$REPO_DIR" fetch origin --quiet
[ "$(git -C "$REPO_DIR" rev-parse main)" = "$(git -C "$REPO_DIR" rev-parse origin/main)" ] \
  && echo "TRUNK_CURRENT" || echo "TRUNK_STALE — ff the shared checkout before continuing"
# if stale, and the shared checkout is on main and clean:
#   git -C "$REPO_DIR" pull --ff-only

WT="$REPO_DIR/.claude/worktrees/eng-123-user-preferences"
git -C "$REPO_DIR" worktree add "$WT" origin/main --detach
cd "$WT"
```

Then lay the stack down, bottom to top, committing each layer's changes before adding the next.
Pass `--base` explicitly rather than letting `init` infer the trunk from a detached HEAD:

```bash
gh stack init --base main rohan/eng-124-user-preference-model
git add <only this layer's files> && git commit -m "Add user preference model [ENG-124]"

gh stack add rohan/eng-125-user-preferences-endpoint
git add <only this layer's files> && git commit -m "Expose preferences on /users [ENG-125]"

gh stack add rohan/eng-126-preferences-tab
git add <only this layer's files> && git commit -m "Add preferences tab to settings [ENG-126]"
```

Three rules that bite:

- **`gh stack add` must run from the top branch.** Anywhere else it exits **5**. Run `gh stack top`
  first if you've navigated away.
- **Stage deliberately — never `git add -A`.** The entire point is control over which change lands
  in which layer. `add` without `-Am` does not touch the working tree, so uncommitted changes
  follow you onto the new branch; commit or stash before adding a layer.
- **`[ENG-XXX]` in a commit is fine; in a PR *title* it is an assertion the PR closes that card**
  (`/CLAUDE.md` → "PR workflow"). In a stack each layer genuinely completes its own sub-issue, so
  each layer's PR title carries its own sub-issue id. The **parent** id goes in a PR title only if
  merging that one PR completes the parent — usually it doesn't, so link the parent in the body.

## Step 5: Submit as drafts

```bash
gh stack submit --auto      # pushes every branch, opens a draft PR per layer, links the stack
gh stack view --json        # confirm: branch → PR number/url/state
```

**Never pass `--open`.** AuraHealth repos run Cursor BugBot and merge manually, so PRs open as
**drafts** and are flipped ready only after review — a PR opened ready can be merged before the
review lands, stranding the follow-up fix on a merged branch. Bare `submit --auto` already creates
drafts, which is exactly what we want.

`submit` is **not atomic**. If a later push is rejected, earlier pushes and PR updates stand — fix
the rejection and rerun the same command; rerunning is safe.

## Step 6: Rewrite the auto-generated PR titles and bodies

`gh stack submit` auto-generates titles and bodies — a single-commit branch reuses the commit
subject, a multi-commit branch just humanizes the branch name. Neither meets our PR convention.
There is no flag for this; fix it afterwards with `gh pr edit`.

Every PR needs a **Summary** and a **Test plan** checklist, plus its position in the stack so a
reviewer knows what they're looking at. Write the body to a file and pass `--body-file` — never the
`--body "$(cat <<'EOF' … )"` form, which mis-parses on apostrophes, em-dashes and code fences:

```bash
cat > /tmp/pr-body-eng-125.md <<'EOF'
## Summary

Exposes user preferences on `GET /users/:id`, reading the model added in the layer below.

## Stack

- #401 Add user preference model  <- base for this PR
- **#402 Expose preferences on /users  <- this PR**
- #403 Preferences tab in settings

Part of [ENG-123](https://linear.app/aura-health/issue/ENG-123).

## Test plan

- [ ] `npm test` passes
- [ ] `GET /users/:id` returns the new `preferences` object
- [ ] Existing clients that ignore the field are unaffected
EOF
gh pr edit 402 --title "Expose preferences on /users [ENG-125]" --body-file /tmp/pr-body-eng-125.md
```

Do this for every layer. The stack listing is what makes a stacked PR reviewable — without it a
reviewer opening PR #402 has no idea #401 must land first.

## Step 7: Review cycle — BugBot per PR, bottom-first

BugBot reviews **one PR at a time**; there is no stack-wide review. Run it per layer, starting at
the bottom, since a finding low in the stack changes every layer above it.

Post the trigger as its **own comment** — `bugbot run` appended to a longer reply silently does not
fire, and nothing errors:

```bash
gh pr comment 401 --body "bugbot run"
```

Delegate the wait-and-report to [`eng:bugbot-review`](../bugbot-review/SKILL.md) rather than
reimplementing the poll here — it already handles the two traps (the summary comment is edited in
place so `created_at` lies, and a "0 findings" pass can surface a finding on the ready-flip re-run).

**When you fix a BugBot finding on a lower layer, every layer above must be replayed:**

```bash
gh stack checkout rohan/eng-124-user-preference-model   # or: gh stack down
git add ... && git commit -m "Address BugBot finding: null-check preferences [ENG-124]"
gh stack rebase --upstack     # replay every branch above onto the change
gh stack top
gh stack push                 # updates branches only; does not touch PRs
```

Then re-run BugBot on the changed PR *and* on every PR above it — their diffs moved.

Once a layer is clean, flip it ready (`gh pr ready` is per-PR, there is no stack equivalent):

```bash
gh pr ready 401
```

## Step 8: Keep the stack in sync

`sync` is the routine command — fetch, reconcile with GitHub, fast-forward trunk, cascade-rebase,
push, refresh PR state:

```bash
gh stack sync              # after trunk moves or someone edits a PR on github.com
gh stack sync --prune      # also delete local branches for merged PRs
```

Two traps:

- **`Sync aborted` exits 0.** When local and remote stacks have diverged, `sync` prints both
  chains, changes nothing, and exits **0**. A zero exit code does not mean it synced — grep the
  output for `Sync aborted`, or re-run `gh stack view --json` and compare.
- **A squash merge is handled automatically.** `sync` detects it and rebases with `--onto` against
  the correct target, skipping the merged branch. No manual action.

On a conflict (exit **3**), `sync` has already restored every branch — nothing is half-applied. Run
`gh stack rebase` to recreate the conflict, resolve, `git add`, then `gh stack rebase --continue`.

## Step 9: Merge, bottom-first

```bash
gh stack merge 403 --yes --squash    # PR #403 plus every unmerged PR below it
gh stack merge 7 --yes --squash      # every unmerged PR in stack #7
```

- **`gh pr merge` cannot merge a stack.** Always `gh stack merge`.
- **All-or-nothing.** If any PR in the merge set can't merge, none do, and the reason is reported.
- Only open-and-not-draft is checked. Every layer must be flipped ready (Step 7) first.
- **A merge queue on the base branch overrides the method flag** — the stack is queued, the queue
  picks the method, and queued PRs may land in separate groups rather than together.

**Merging is the user's call.** Present the ready state and ask; don't merge unprompted.

After the merge, close out Linear (Mode A): move each sub-issue to Done and comment the PR link on
the parent with `save_issue` / `save_comment`. Then drop the worktree:

```bash
git -C "$REPO_DIR" worktree remove "$WT"
```

## Non-interactive use — always pass these flags

`gh stack` branches on whether stdout is a TTY. Piped, most commands error cleanly; under a PTY the
same commands open a prompt or full-screen TUI and **block forever**. Agent harnesses differ, so
never rely on that detection.

| Always run | Never run bare | Why |
|---|---|---|
| `gh stack view --json` | `gh stack view` | opens a TUI under a PTY |
| `gh stack submit --auto` | `gh stack submit` | prompts for a title per new PR |
| `gh stack merge <target> --yes` | `gh pr merge` | `gh pr merge` cannot merge a stack |
| `gh stack init <branch>...` | `gh stack init` | prompts for branch names |
| `gh stack add <branch>` | `gh stack add` | prompts for a name, and fails even when piped |
| `gh stack checkout <target>` | `gh stack checkout` | opens a selection menu |
| `gh stack up` / `down` / `top` / `bottom` | `gh stack switch` | `switch` is menu-only |
| — | `gh stack modify` | TUI-only, no non-interactive path |

**With more than one remote**, `push`, `submit`, `sync`, `rebase` and `link` need `--remote <name>`
unless `remote.pushDefault` is set. `checkout` and `trunk` have no `--remote` flag and *require* the
config — which is why Step 0 sets it.

`gh stack view --json` writes JSON to **stdout**; status messages go to **stderr**. Parse stdout,
branch on exit codes, never parse stderr.

```
trunk           string
currentBranch   string
branches[]      name, head, base, isCurrent, isMerged, isQueued, needsRebase
branches[].pr   number, url, state ("OPEN" | "MERGED" | "QUEUED"); absent when no PR exists
```

`base` is the saved SHA of the parent branch this branch was last known to contain — it may be
older than the parent's current tip. `needsRebase` is true when the parent's tip is no longer an
ancestor.

## Exit codes

| Code | Meaning | Recovery |
|---|---|---|
| 0 | Success | — (but check for `Sync aborted`) |
| 1 | Generic error | Read stderr |
| 2 | Not in a stack | `gh stack init`, or `gh stack checkout <target>` |
| 3 | Rebase conflict | Resolve, `git add`, `gh stack rebase --continue` |
| 4 | GitHub API failure | Check `gh auth status`, retry |
| 5 | Invalid arguments | Fix the invocation; `<command> --help` |
| 6 | Disambiguation required | Branch is in several stacks; check out a non-shared branch |
| 7 | Rebase already in progress | `gh stack rebase --continue` or `--abort` |
| 8 | Stack file locked | Another `gh stack` process is writing; retry after ~5s |
| 9 | Stacked PRs unavailable | Not enabled on the repository; tell the user and stop |
| 10 | Modify recovery required | `gh stack modify --abort` |

## Constraints

- **Stacks are strictly linear** — one parent, at most one child. Parallel work needs separate
  stacks (and cross-repo work needs `eng:linear-task` multi-agent mode, not a stack).
- **No non-interactive reorder or removal.** Errors may suggest `gh stack modify`; it is TUI-only.
  Restructure with `unstack` then `init` — see [`references/troubleshooting.md`](references/troubleshooting.md).
- **`checkout <branch-name>` resolves against local stacks only.** Use a stack or PR number to pull
  a stack down from GitHub.
- **In AINative itself**, every layer touching `plugins/**` must bump the skill's `metadata.version`
  *and* all three plugin manifests, and each layer must bump **past the layer below it**, not merely
  past `origin/main`. Checking each layer with `--base origin/main` does **not** catch this: an upper
  layer that simply inherits the lower layer's bump still shows an increase against main, so the gate
  passes and the layer ships under a version already installed. Point the check at the layer below —
  `python3 scripts/check-version-bumps.py --base <lower-layer-branch>` — and reserve
  `--base origin/main` for the bottom layer only.

## More detail

`gh stack <command> --help` is authoritative for flags. Note `gh stack help <command>` does **not**
work — it prints the top-level help. Open the reference whose trigger matches; no need to preload
all three.

- [`references/stack-design.md`](references/stack-design.md) — read before creating a stack: how
  many layers, what belongs in each, when work deserves its own stack, and how Linear sub-issues
  map onto layers.
- [`references/commands.md`](references/commands.md) — read when a command fails unexpectedly or
  you need its preconditions, side effects, atomicity, or ordering guarantees.
- [`references/troubleshooting.md`](references/troubleshooting.md) — read on a rebase conflict,
  after a squash merge, on local/remote divergence, when restructuring a stack, or when driving
  stacks from another tool or worktree.

## Attribution

`references/commands.md`, `references/stack-design.md` and `references/troubleshooting.md` are
adapted from [`github/gh-stack`](https://github.com/github/gh-stack) (MIT), `skills/gh-stack/`, with
AuraHealth conventions layered in. When upstream ships changes, re-fetch and re-apply the
amendments rather than editing blind — the amendments are marked `**AuraHealth:**` in each file.
