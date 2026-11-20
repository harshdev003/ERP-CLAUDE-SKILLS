---
name: sweep-bugsnag
description: >
  Orchestrate an end-to-end sweep of the top Bugsnag errors for a project and fix them one at
  a time — the middle loop that ties triage to shipped fixes. Chains the existing
  building-block skills: report the errors, investigate each, file Linear issues, fix
  per-error in a git worktree, then BugBot plus a sub-agent PR review and a Linear update.
  Works across every aura-health Bugsnag project — it resolves the project slug to its repo
  and stack (Aura React Native, Aurie Swift/iOS, Aurie and Aura web, Electron desktop) and
  follows that repo's own worktree, lint, PR and review conventions. Trigger on "sweep
  bugsnag", "sweep the top bugsnag errors", "let's fix the top bugsnag issues", "work through
  the bugsnag errors", "fix the weekly bugsnag errors", "triage and fix bugsnag", "sweep
  bugsnag for aurie", "sweep the aiweb errors", or a request to take the top-N Bugsnag errors
  through to merged PRs. Do NOT use for a one-off digest (`eng:report-bugsnag-errors`) or a
  single error (`eng:investigate-bugsnag-errors`).

metadata:
  version: "3.2.2"
knowledge:
  reads:
    - Guide/Index
    - Tools/Bugsnag  # snake_case response fields, auth shape, project inventory, PII rules
    - Tools/Linear
    - Tools/GitHub
    - Compliance/Privacy
---

# Sweep Bugsnag

Take the loudest open Bugsnag errors for a project and drive each one to a
merged fix, one at a time. This skill is the **orchestrator** — it does not
re-implement triage, Linear, or review logic; it calls the skills that own
each of those and adds the repeatable per-error fix loop.

The projects span four stacks and several repos, so the loop is **routed, not
assumed**: resolve the target repo before the first fix (Step 1a) and follow
that repo's conventions.

## Role & posture

You are the on-call engineer running a Bugsnag cleanup. Move **one error at a
time** to keep each PR small and reviewable. **Slow down at two points:** the
scope decision in Step 2 (how aggressive to silence vs. re-triage) and every
review finding in Step 5 — both are judgment calls the user should weigh in on,
not defaults you assume.

## Building blocks this skill composes

| Step | Delegates to |
|------|--------------|
| Triage / digest | `eng:report-bugsnag-errors` |
| Drill into one error | `eng:investigate-bugsnag-errors` |
| File tracking cards | `shared:add-linear-issues` |
| Update / close cards | `shared:update-linear-issues` |
| Automated review | `eng:bugbot-review` + a sub-agent review (Step 5) |
| Mark errors resolved in Bugsnag (optional, after release) | `eng:resolve-bugsnag-errors` |

Never duplicate their internals — invoke them.

## Inputs

| Flag | Default | Meaning |
|------|---------|---------|
| `--project <slug>` | `aura-ios` | Bugsnag project to sweep. Each slug routes to one repo + stack — see [`references/repo-fix-loops.md`](references/repo-fix-loops.md). |
| `--since <dur>` | `7d` | Window (`1h`/`24h`/`7d`/`30d`) |
| `--limit <n>` | `5` | How many top errors to take into the sweep |
| `--parent <PROD-xxxx>` | — | Linear parent issue to file cards under (e.g. the standing "Bugsnag" epic) |

## The critical Bugsnag-grouping nuance (read before triaging)

**Applies to the Aura React Native repo** (`aura-ios` / `aura-android`), whose
manual `bugsnag.report('<title>', {...})` calls group badly. Account for it when
reading the digest for those two projects:

- **Catch-all buckets.** A string report fired from inside a Firebase / RN
  callback captures a **vendor-only minified stack** (no app frames). Bugsnag
  groups by stack, so *many unrelated reports collapse into one group*, named
  after whichever representative event it first saw. **The group name lies** —
  read the **latest event's actual message** (Mode 2 focus / Mode 3 trace),
  then **grep the repo for that message string** to find the true call site.
- **Release fragmentation.** The *same* report can also **split across several
  groups** because the minified frame shifts each release. So a "new" group may
  be the same call site you already fixed.
- **Consequence:** before fixing group #2, check whether it's the **same call
  site** as group #1. If so it's a **duplicate** — cancel the card (Step 7), do
  not double-fix.

The `groupingHash` fix (title-based grouping) and the global `setUser` pattern
are shipped in that repo — see its `.claude/rules/error-handling-bugsnag.md`.
Post-release, these buckets split and shrink; factor that into the Step 2 scope
decision.

**On the other stacks**, one symptom still splits across several groups whenever a
release shifts the stack frames, so the duplicate check below applies everywhere:
before fixing group #2, confirm it is not the same call site as group #1. What does
*not* carry over is the catch-all bucket — the Swift and web surfaces report through
typed helpers rather than free-text titles, so a group name there is usually
trustworthy.

## Workflow

### Step 1 — Triage

Invoke `eng:report-bugsnag-errors` with the project/window/limit
(`--project <slug> --since <dur> --limit <n>`). Present the ranked
top-by-events list to the user verbatim, each row carrying its error id and the
`investigate bugsnag --error <id>` drill-in.

**Release scope is offered, not defaulted.** `eng:report-bugsnag-errors` can
scope to a version (`--release <v> --release-mode seen-in`, see its SKILL.md),
and where monitoring is release-anchored — Aurie baselines on the latest
release — that is usually the sweep the user wants: an all-releases ranking is
dominated by whatever accumulated on versions nobody is shipping fixes for.

This skill still does **not** pass one by default, for two reasons. A sweep ends
in *fixes*, and a bug worth fixing is worth fixing whether or not it happens to
have been seen on the newest build — a release filter silently drops
still-live errors from the list of candidates, and a dropped row has no shape.
And the right version is per-project (1.29.0 of the iOS app is not 1.29.0 of
UserWeb) and moves every ship, so a baked-in default would go stale invisibly.

So: when the user names a project whose monitoring is release-anchored, **say
what the unscoped ranking includes and offer the scoped run**, then use whatever
they choose. Do not substitute one for the other silently — the two produce
very different tables from the same window.

### Step 1a — Resolve the target repo

Before any card or code, establish **which repo you are about to edit**. The slug
does not tell you: `aura-ios` is the React Native app, `ai-aurie-ios` is a separate
Swift codebase, and the default branch is `master` on some repos and `main` on
others.

Take the repo and app family from the `eng:investigate-bugsnag-errors` output (it
resolves the same routing for its code-context mode), or read the table in
[`references/repo-fix-loops.md`](references/repo-fix-loops.md). Then confirm the
local checkout and its default branch:

```bash
git -C "$MAIN" remote get-url origin                                   # must match the routed repo
git -C "$MAIN" symbolic-ref --short refs/remotes/origin/HEAD           # origin/master or origin/main
```

A mismatch here is the failure worth catching early: editing the wrong repo does not
error, it just produces a fix that lands nowhere near the crash. If the slug routes
to `aura-aimacos` — deliberately unmapped — stop and ask rather than guessing.

**Say which repo and stack you're sweeping when you present the triage**, so the user
can correct a bad route before cards exist.

### Step 2 — File tracking cards (confirm scope first)

For the top-N the user wants to pursue, use `shared:add-linear-issues` to create
one sub-issue per error under `--parent` (or ask which parent). Each card gets:
Bugsnag error id, events/users impact, the `investigate bugsnag --error <id>`
command, and acceptance criteria — *root cause identified* / *fix shipped in an
iOS release* / *event volume drops after release*.

**Confirm the sweep scope with the user before writing code** — how aggressive
to be (silence expected-flow noise now vs. wait for the grouping split to reveal
true per-error volume, and which errors are in-scope this pass). Don't assume.

### Step 3 — Investigate one error

For the card you're working, run `eng:investigate-bugsnag-errors --error <id>`
(focus, then trace / code-context as needed). Apply the grouping nuance above:
confirm the real call site by grepping the repo for the latest event's message,
and confirm it isn't a duplicate of an already-fixed group.

### Step 4 — Fix it in an isolated worktree

The loop is the same everywhere — worktree off the fresh default branch, apply the
fix, lint, commit to a temp-file message, draft PR — but the commands and the
error-reporting idiom are per stack.

**Read the loop for the stack you resolved in Step 1a before your first fix:**
[`references/repo-fix-loops.md`](references/repo-fix-loops.md) carries the routing
table, the universal skeleton, the fix shapes that hold on every stack, and the
Swift / web / Electron specifics; the React Native detail lives in
[`references/aura-rn-fix-loop.md`](references/aura-rn-fix-loop.md).

**A repo's own `.claude/rules/` outranks this skill.** `AuraHealth/Aura` and
`AuraHealth/AuraConsumerApp` both ship one — including `pr-process.md`, which owns
that repo's review gate. Read it and follow it where it differs from Step 5.

The React Native loop (`aura-ios` / `aura-android`), in brief:

1. Worktree off fresh master: `git worktree add -b jcurlier/<card>-<slug>
   .claude/worktrees/<card> origin/master` (Aura convention: worktrees under
   `.claude/worktrees/`).
2. **Copy the gitignored config so lint resolves:**
   `cp -R <mainRepo>/src/cask/. <worktree>/src/cask/` — a fresh worktree lacks
   `src/cask/meals.js` (gitignored) and `npx eslint` otherwise errors on
   `import code from '../cask/meals'`. This bites on **every** worktree.
3. Apply the fix. Common Bugsnag fix shapes (see the reference + the Aura
   `error-handling*.md` rules):
   - Stop reporting **expected flows** (network-offline, permission-denied,
     expected new-user/SSO branches) — downgrade to `Trackings(...)` analytics
     or skip, per `error-handling.md`.
   - Fix `{ e }` raw-`Error` metadata → `{ e: e?.message, stack: e?.stack }`.
   - Strip **PII / tokens** from metadata per `security.md`.
   - Make `bugsnag.report` **titles constant** (title = `groupingHash`) and move
     interpolated values into the metadata object.
4. Lint the changed files (node 18 on PATH):
   `export PATH="$HOME/.nvm/versions/node/v18.15.0/bin:$PATH"; npx eslint <files>`
   (ignore the `nvm ... prefix` warning). Run the relevant `npm test` if a
   store/logic/service changed.
5. Commit with a conventional message written to a **temp file** +
   `git commit -F <file>` (never a heredoc, never `-m` with embedded newlines).
6. Push; open a **draft** PR with the repo PR template + the Linear link.

### Step 5 — Review gate (mandatory, per PR)

1. **BugBot:** `eng:bugbot-review` (posts `bugbot run`). Poll for its review.
   BugBot posts its **summary ~1 min before the inline findings**, and on a
   re-run edits the summary in place — so **never trust an early "no findings"**;
   re-check the inline review comments (non-null `line`) until the summary
   references the PR's actual head SHA and the inline set is stable.
2. **Sub-agent review:** spawn a `general-purpose` sub-agent (synchronous) with
   the PR diff inlined and a tight prompt — correctness, scope/regressions, PII,
   and (for auth/logout-touching changes) logout robustness. Keep it simplistic;
   it's a sanity pass, not an audit.
3. **Address every** BugBot + sub-agent finding with a fix commit, and **reply
   in-thread** citing the fix SHA. Only when both are clean: `gh pr ready <n>`.

For a **deeper** review than the simplistic sub-agent pass (larger or riskier
fixes), use `eng:review-pr` instead of the ad-hoc sub-agent.

### Step 6 — Post-merge cleanup & stacked PRs

After each PR merges:

`$BASE` below is the repo's own default branch, resolved in Step 1a — `master` on
some Aura repos, `main` on others.

- `git worktree remove --force .claude/worktrees/<card>`; delete the branch;
  `git merge --ff-only origin/$BASE` on the main checkout.
- **Stacked PRs** (one fix depends on another not-yet-merged): base the
  dependent PR on the dependency's branch. After the dependency merges,
  `git rebase --onto "$BASE" <old-base-sha>` (or `git rebase "origin/$BASE"`),
  `git push --force-with-lease`, and flip the base with
  `gh pr edit <n> --base "$BASE"`. A GitHub **"conflicts"** state right after a
  base flip is often **stale** — rebasing onto the latest base + force-push clears
  it (verify the diff is only your own files afterward).

### Step 7 — Linear bookkeeping

Keep each card current as work progresses (`shared:update-linear-issues`): add
the root-cause writeup, tick acceptance criteria, note the shipping PR and any
relationships. On merge, move PROD cards to **In Review** (merged) or **Done**
per the team's workflow states. **Duplicates** → **Canceled** with a note
explaining the shared call site + linking the primary card (Linear only accepts
its formal *Duplicate* state when a duplicate *relation* already exists, which
the API helper can't create — Canceled + a note is the pragmatic path).

## PII guardrail

Bugsnag error metadata can carry Firebase UIDs, and this skill writes to
external surfaces (Linear cards, GitHub PRs). Per `Compliance/Privacy`, when a
card or PR body would include end-user identity, prefer a **UID prefix + email
domain**, never a full email / name. Include only what the task genuinely needs.

## Idempotency & safety

- Read/triage steps are safe to re-run. Card creation is not — before re-filing,
  check whether the sub-issues already exist under the parent.
- One error → one branch → one PR. Don't batch unrelated fixes into a PR.
- Never `--force` push a default branch; force-with-lease only your own feature branch.

## References

- [`references/repo-fix-loops.md`](references/repo-fix-loops.md) — the project →
  repo → stack routing table, the loop skeleton shared by every stack, the fix
  shapes that hold everywhere, and the Swift / web / Electron specifics.
- [`references/aura-rn-fix-loop.md`](references/aura-rn-fix-loop.md) — the full
  worktree → lint → commit → PR → review → merge loop with the Aura-RN gotchas.
- Target-repo rules, where the repo ships them (`AuraHealth/Aura`,
  `AuraHealth/AuraConsumerApp`): `.claude/rules/error-handling.md`,
  `error-handling-bugsnag.md`, `security.md`, `pr-process.md`. These are
  authoritative for their repo.
- Building-block skills: `eng:report-bugsnag-errors`,
  `eng:investigate-bugsnag-errors`, `eng:resolve-bugsnag-errors`,
  `shared:add-linear-issues`, `shared:update-linear-issues`,
  `eng:bugbot-review`.
