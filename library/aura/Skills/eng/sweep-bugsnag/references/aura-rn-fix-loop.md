# Aura RN fix loop — worktree → PR → review → merge

The per-error mechanics for fixing a Bugsnag error in the **Aura React Native**
repo (`Mobile/Aura`, the `AuraHealth/Aura` GitHub repo). These are the
conventions that were re-derived on every card during the first big sweep;
follow them so each fix is fast and lint-clean.

Assume `MAIN` = the main Aura checkout (e.g. `~/Development/Aura/Mobile/Aura`).

## 1. Isolated worktree off fresh master

```bash
cd "$MAIN"
git fetch origin
git worktree add -b jcurlier/<card>-<slug> .claude/worktrees/<card> origin/master
```

- Worktrees live under `.claude/worktrees/<card>` (repo convention; gitignored).
- One card → one worktree → one branch → one PR.
- If the fix **depends on another unmerged fix** (stacked), branch off that
  branch instead of `origin/master`:
  `git worktree add -b <branch> .claude/worktrees/<card> <dependency-branch>`.

## 2. Copy the gitignored `src/cask` (REQUIRED for lint)

A fresh worktree does **not** contain `src/cask/meals.js` and friends — they're
gitignored, so they exist only in the main checkout. Without them,
`npx eslint` fails with `Unable to resolve path to module '../cask/meals'`
(imported at the top of `app.js` and others). Copy them in:

```bash
cp -R "$MAIN/src/cask/." "<worktree>/src/cask/"
```

This is a lint-only artifact; it's gitignored so it never shows in `git status`
or the diff. Do this once per worktree, right after creating it.

## 3. Apply the fix — common Bugsnag fix shapes

Anchor every change to the Aura repo rules (`.claude/rules/`): `error-handling.md`,
`error-handling-bugsnag.md`, `security.md`, `comments.md`.

- **Expected-flow noise → not a Bugsnag error.** Network-offline
  (`/Network request failed|Network Error/i` on the caught error), permission
  denied, expected new-user / SSO "no account → onboarding" branches, etc. are
  **not** errors. Downgrade to `Trackings('<snake_event>', {...})` analytics or
  skip, per `error-handling.md`'s network-offline rule. Keep any existing
  `Trackings(...)` call; just drop the redundant `bugsnag.report`.
- **`{ e }` / `{ error }` raw-Error metadata.** A raw `Error` JSON-serializes to
  `{}`. Spread the useful fields: `{ e: e?.message, stack: e?.stack }`.
- **PII / tokens in metadata.** Never log tokens or full user identity
  (`security.md`). Drop device/VOIP tokens, full response bodies, emails. Keep a
  non-sensitive signal (status, error codes/arrays). The logged-in user id is
  already on every event via the global `setUser` (see
  `error-handling-bugsnag.md`), so **don't** re-add `userId: profile?.id` to
  metadata; keep an id only when it's a *different* entity (coach, other user).
- **Interpolated titles fragment groups.** The first arg is the `groupingHash`.
  A `` bugsnag.report(`X ${id}`) `` makes a new group per value. Make the title a
  **constant string** and move the variable into metadata:
  `bugsnag.report('X', { id })`. A `` `${CONST} ...` `` where `CONST` is a
  module constant is fine (one stable value) — leave it.
- **Preserve comments** and add a one-line "why" when the change is non-obvious
  (e.g. "expected offline, log as analytics per error-handling.md").

## 4. Lint (and test if logic changed)

```bash
export PATH="$HOME/.nvm/versions/node/v18.15.0/bin:$PATH"   # node pinned in .nvmrc
npx eslint <changed files>
```

- Ignore the `nvm is not compatible with the npm config "prefix"` warning — it's
  noise, not a failure. `ESLINT_EXIT=0` (no output) means clean.
- A surviving `Unable to resolve '../cask/meals'` error is the missing-`cask`
  artifact from Step 2, **not** your change — copy `src/cask` and re-lint.
- If you removed the last use of an import/var, lint will catch the orphan — fix
  it (or it wasn't actually the last use).
- Run the matching `npm test` suite if you touched a store / `src/logic` /
  `src/services`. Many Bugsnag call sites have no unit coverage — note that in
  the PR rather than inventing tests.

## 5. Commit (no heredoc)

Write the conventional-commit message to a **temp file**, then:

```bash
git add <files>
git commit -F /path/to/commit-msg.txt
```

Never pipe a heredoc into `git commit`, and avoid `-m` with embedded newlines.
End the message with the repo's required trailer
(`Co-Authored-By: Claude <...>`). Push: `git push -u origin <branch>`.

## 6. Draft PR

Open **draft** (`gh pr create --draft`) with the repo PR template
(Description / Changes / Testing checklist / Related) and link the Linear card.
For a stacked PR, set `--base <dependency-branch>` so the diff shows only your
delta; flip it to `master` after the dependency merges (see Step 6 in SKILL.md).

## 7. Review gate

BugBot (`eng:bugbot-review`) **then** a synchronous `general-purpose` sub-agent
review of the diff. Address both; reply in-thread with the fix SHA; then
`gh pr ready`. See SKILL.md Step 5 for the polling caveats (summary lands before
inline findings; re-runs edit the summary in place).

## 8. Post-merge

```bash
git worktree remove --force .claude/worktrees/<card>
git branch -D <branch>
git -C "$MAIN" merge --ff-only origin/master
```

For a stacked dependent PR after its dependency merged: rebase
(`git rebase --onto master <old-base-sha>` or `git rebase origin/master`),
`git push --force-with-lease`, `gh pr edit <n> --base master`. A stale
"conflicts" banner right after the base flip clears once you rebase onto latest
master and force-push — verify the resulting diff contains only your own files.
