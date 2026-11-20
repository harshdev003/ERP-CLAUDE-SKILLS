---
description: Pull request workflow for Aura — quality gates, automated review cycle, PR template
alwaysApply: true
---

# Pull Request Process

## Related Rules

- `project-rules.md` for the overall validation workflow.
- `documentation.md` for PR description expectations.

## Submission Workflow

Complete every step in order before marking a PR as ready for review.

### 1. Code Completion

- All requested features implemented
- All acceptance criteria from the Linear ticket met
- Error handling in place (`try/catch` + `bugsnag.report` per `error-handling-bugsnag.md`)
- Analytics events added for user-meaningful actions (per `logging.md`)
- New i18n keys added to `src/localization/en/<namespace>.json` + scanner re-run

### 2. Quality Gates (BEFORE Committing)

```bash
npm run lint           # must pass with zero errors
npm test               # if you touched logic, services, or stores
```

**If lint fails in CI:**

- Don't ignore — fix immediately.
- Common offenders: `no-console`, `react/prop-types`, `consistent-return`.
- After fixing, push another commit; do not amend an already-pushed commit unless you're the only one on the branch.

### 3. Manual Smoke Test

Any PR that touches UI (a screen, component, or theme value) must be smoke-tested on at least one platform (iOS or Android). PRs that touch IAP, audio playback, deep links, or push notifications should be tested on **both** if at all possible — these areas have platform-specific gotchas.

### 4. Git Operations

- Commit with a clear, conventional-commit-style message:
  - `feat(family-plan): add invitation flow`
  - `fix(iap): handle Android InvalidObject when productId is non-string`
  - `chore(deps): bump react-native-iap to 12.15.1`
- Push to your feature branch.
- Sync with `master` if needed before opening the PR.

### 5. PR Creation

- Descriptive title that mirrors the commit summary.
- Use the template below.
- Link the Linear issue.
- Mark as **Draft** until you've completed step 6.

#### PR Template

```markdown
## Description
[One-paragraph summary of what this PR does and why.]

## Changes
- [Bullet list of meaningful changes]

## Testing
- [ ] `npm run lint` passes
- [ ] Relevant `npm test` suite passes
- [ ] Smoke-tested on iOS / Android (specify which)
- [ ] (For UI) VoiceOver / TalkBack pass on the new flow
- [ ] (For IAP / payments) tested with sandbox account

## Screenshots / Recordings
[Before / after, or a short Loom for flows]

## Related
- Linear: [PROD-XXXX](https://linear.app/aura-health/issue/PROD-XXXX)
```

### 6. **MANDATORY: Automated Review**

After opening the PR, run an automated review pass — either cursor-bot (if configured for the repo) or the in-repo `code-reviewer` agent (`.claude/agents/code-reviewer.md`). Then:

1. **Wait** for the review to complete (~2–5 min, longer for large PRs).
2. **Read every comment** and categorize by severity.
3. **Fix every must-fix and should-fix** before requesting human review.
4. **Reply on each thread** with a one-line "Fixed in `<sha>`. Brief explanation." — don't just push and leave the comment dangling.
5. **Push the fix commits** and verify CI is green.

**Never skip the automated review.** It catches state-management mistakes, missing prop-types, hardcoded styles, and wrong Bugsnag patterns that human review usually misses.

### 7. Final Checks

- All automated-review threads addressed (resolved or replied to with rationale).
- CI green (lint + tests).
- PR description reflects the final state if scope changed.

### 8. Request Human Review

Convert the draft PR to "Ready for Review" only when 7 is true.

## Addressing Automated Review Comments

**Bad reply:**

> ignored

**Good reply:**

> Fixed in `abc1234`.
> **Issue**: hardcoded `marginTop: 20` on the donate CTA.
> **Fix**: replaced with `responsiveDimension(20)` and pulled the value from `theme.spaces.md`.
> **Testing**: re-tested on iPhone SE (smallest) and iPhone 15 Pro Max — spacing visually matches design.

## AI Agent PR Workflow

When an AI agent (Cursor agent, Claude Code) creates or pushes to a PR, it **must** complete the automated review cycle before considering the work done:

1. Push changes.
2. Create / update the PR via `gh pr create`.
3. Poll for automated reviews (`gh api repos/{owner}/{repo}/pulls/{pr}/reviews` and `.../comments`).
4. Address every must-fix and should-fix.
5. Reply on each thread with a fix summary referencing the commit SHA.
6. Push fix commits, confirm CI green.
7. Only then mark the PR ready for review.

## master → master-android syncs

The repo allows **squash merges only**. A squash turns a sync into a single-parent commit, so `master-android` stops having `master` as an ancestor, and every later sync re-diffs from the old base and conflicts on any line `master` has changed since.

- **Cherry-picking your own commits** (instead of a full sync): `git cherry-pick -x <sha>` onto `master-android`, then a fast-forward push. Never force-push it, and leave other people's commits for them to sync.
- **Push a sync directly, as a merge commit:** on `master-android`, run `git merge --no-ff origin/master`, then push (a fast-forward of `master-android`).
- **If you want a PR for CI or BugBot,** open it, but land it by fast-forwarding `master-android` to the PR head. Never use the merge button.
- **Repair a squashed sync** without changing any file: `git commit-tree <master-android>^{tree} -p <master-android> -p <master>`, then fast-forward `master-android` to the new commit. The tree stays the same and `master` becomes an ancestor again. Check with `git merge-base --is-ancestor origin/master origin/master-android`.

Why: on 2026-10-26 the sync in #7447 was squash-merged through the button; it was repaired as fd8a77c837 (ENGMOB-3081).

## Cards, holds and merges

- **One PR per Linear card.** Name the branch after the card (`<you>/engmob-1234-short-slug`). A branch name with a card id links the PR, and a merge moves that card's state, so name a sub-task branch after its sub-card, not the parent.
- **Open as a draft.** Mark it ready only when CI is green and every BugBot thread, from every run, is fixed or answered and resolved (0 open threads). A re-run reports only new findings, so a clean latest run doesn't mean the earlier threads are closed.
- **Humans merge.** An agent builds and updates PRs, and never merges.
- **A PR waiting on another repo's change** gets `[On Hold]` at the start of its title and stays a draft. Undo both when the hold lifts.
- **Name an Android check only for Android-specific changes:** anything under `android/`, or JS that runs only on Android. Put the check in the description; it is a check to run, never a release blocker. Shared JS is tested on iOS and needs no Android flag. Mark security changes as such.
- **Releases are cut by the engineers.** An agent runs `release-ios` only when an engineer asks it to, never on its own.

## Don't

- Don't `--no-verify` a commit to skip hooks.
- Don't `--force` push to `master` or any shared branch.
- Don't merge your own PR without an approving human review.
- Don't bundle unrelated cleanup into a feature PR.
