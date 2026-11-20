# Failure modes

Things that look like one problem and are another. Read this when a release fails,
before concluding what broke.

---

## A red "test job has failed" alert after a blocked release is usually not a test failure

The classic symptom of a direct-push release attempt against a protected branch.
The sequence:

1. one `git push --follow-tags` carries both the branch ref and the tag ref
2. git updates refs independently — the **tag lands**
3. CI starts a pipeline for that tag
4. the **branch is rejected** (`GH013: Changes must be made through a pull request`)
5. the tool rolls back and **deletes the tag**
6. CI reaches checkout and dies on `error: pathspec 'v1.4.2' did not match any file(s)`

The alert says a test job failed. Nothing is wrong with the tests — one blocked push
produced it. Four such alarms fired in one evening before the cause was found.

**This skill cannot produce it**: the branch and the tags are pushed as separate,
ordered steps, and the tags are only pushed after the merge has already succeeded.

---

## "I can't tag anymore" is a misread

Tags were never restricted. The rulesets' rules — `deletion`, `non_fast_forward`,
`pull_request` — are all **branch**-scoped. What happened above is that the release
tool deleted its own tag during rollback.

Don't go looking for a tag protection rule; there isn't one.

---

## A `403` from `gh api` on check-runs is a credential fault, not a CI verdict

`gh` gives the `GITHUB_TOKEN` environment variable precedence over its own keyring
credential, and a fine-grained PAT cannot read Checks. Pushes, PR reads and
`gh api user` keep working, so the failure looks selective and CI-specific.

It does **not** mean CI is failing or unknowable. Check `gh auth status` before
concluding anything, and escape with `env -u GITHUB_TOKEN -u GH_TOKEN gh api …`.

---

## `gh pr merge --auto` fails on these repos

`allow_auto_merge` is `false` fleet-wide, so `--auto` errors rather than queueing.
Plain `--squash` is used instead, and it works because the rulesets require **0
approvals and no required status checks**.

If a repo later gains a required status check, `--squash` starts failing until the
check passes — at which point `--auto` becomes the right call *and* auto-merge has
to be enabled on the repo. Don't switch to `--auto` before checking both.

---

## A tag on the branch head is a tag on a commit that is not on the default branch

Squash merge creates a **new** commit. The release branch head is not what landed.
`run` reads the merge commit SHA from the PR, verifies it is an ancestor of the
default branch, and tags **that**.

---

## A tag whose commit lacks the bump deploys a wrong version

CI builds the image from the tagged commit, and a service reports its own version
from the file bumped in that commit. Tagging before the merge — or tagging a commit
where only one of two version files moved — produces an image that misreports
itself. Nothing errors; the version is simply wrong wherever it is displayed.

`run` calls `_verify_bump_in_commit` before tagging and refuses when any version
file in the target commit does not contain the target version. This is the check
that makes the ordering rule real rather than aspirational.

---

## Resuming: state is derived, never stored

There is no state file. `status` reconstructs where a release stands by asking the
repo and GitHub:

| Observation | Resume from |
|---|---|
| GitHub release exists for every tag | nothing left to create — but see the re-trigger note below |
| tags pushed, release missing | the GitHub release step |
| PR merged, tags missing | the tag step |
| PR open | the merge step |
| branch pushed, no PR | the PR step — **not** the bump |
| release branch has the bump committed but unpushed | the push step |
| nothing | the bump |

So a run interrupted anywhere resumes correctly, and **a partially-completed release
never gets a second version bump**.

**What counts as done: a tag AND a GitHub release.** Both halves matter, because the
version on the default branch is what decides whether a release is in flight. A version
there that is not *fully* released is targeted directly — the skill finishes it rather
than bumping past it.

Keying that on the tag alone breaks the middle row of the table above: once the tag
exists, the commit range is empty, so planning reported `nothing to release` (exit 3) over
a release whose GitHub release had never been created. It looked exactly like a finished
release.

**One rule, every repo — there is no "this repo does not use releases" exemption.**
`lerna version` only ever created tags, so a lerna repo carries many tags and zero
releases — 4U-engine at 30/0, and the since-retired AuraDataJobs at 11/0. An empty
release listing cannot distinguish that from a first release interrupted before
`gh release create`, and special-casing either
reading breaks the other — inferring it produced a bug in both directions on consecutive
review rounds. Requiring both artifacts everywhere removes the ambiguity instead of picking
a side of it, and keeps this in agreement with `State.done`.

**So the first run on a repo with tags but no releases proposes finishing those versions.**
On 4U-engine that is all 23 packages at level `resume`: it creates the missing GitHub
release for each already-shipped tag and touches no code. It self-heals — every run after
that bumps normally — and the proposal goes through the approval gate like any other. Read
the plan before approving; `resume` on a version you thought was shipped is expected here,
exactly once.

The `resume` reason names which artifact is missing (`no tag` vs `the tag but no GitHub
release`), because the two send you looking in different places.

**This whole case is invisible to tests that seed release state.** It was caught only by
running `plan` against every real repo. When changing this logic, do that rather than
trusting the suite.

**An unknowable release state is never "no release".** A failed or limit-saturated
`gh release list` must not collapse to an empty set: that makes the current version look
unreleased, so it gets resumed instead of bumped, and the run then reports
`already-released` while silently skipping commits that needed shipping. The listing
returns `None` for "cannot establish", which falls through to a per-tag lookup that raises
on anything other than a clean 404.

Three of these rows are easy to get wrong, and all three were:

- **`branch pushed, no PR`.** Reading only "open PR" and "merged SHA" sends a resume
  back to the bump step, where writing the same versions into an already-bumped
  worktree leaves it clean — so it fails with "bump produced no change" and strands
  the pushed branch. `_branch_has_bump` is what distinguishes an already-committed
  bump from a genuine no-op; they are otherwise identical.
- **Already-released is not always fully deployed.** On a shape whose CI reads tags
  at HEAD, the pipeline re-trigger is the step that actually ships, and it is the
  likeliest to have been missed: it runs last, and with no CircleCI token it only
  prints `ACTION NEEDED`. So the already-released path still performs the
  re-trigger rather than returning early — otherwise a re-run reports success over
  a release that never deployed. The job is idempotent, so re-triggering is safe.

A stored watermark would have to be kept in sync with the world and can disagree
with it; asking GitHub cannot.

---

## A tag push refused by the pre-push hook over untracked files

The tag is pushed from the main checkout so the repo's pre-push hook runs — usually
`npm run lint:cache`, an `eslint .` over the **working tree**. Untracked files are part
of that tree. A stray copy of the repo inside itself — typically an abandoned nested
worktree such as `<Repo>/<Repo>/.claude/worktrees/<name>`, which the root
`ignores: ['.claude/**']` does not match — fails the lint for a tag whose commit cannot
contain it.

`run` keeps the hook and bypasses it only when the failure is provably about such files:
the output reports a pre-push failure, names at least one file, and **every** named file
lies under an untracked or ignored path from `git status --porcelain --ignored`. The tag is
then pushed with `--no-verify` and the step output names the paths:

```
→ tagged v2.208.1 at 523ce1042ca7
→ v2.208.1 pushed with --no-verify: the pre-push hook failed only on untracked files
  under PaymentService/ — remove them (a stray nested worktree goes with
  `git worktree remove <path>`)
```

Nothing the release ships goes unchecked: the tagged commit is the merge commit whose PR
passed the repo's required checks, which run the same lint. A hook failure on a tracked
file, or one that reports no file, still stops the run with exit `2`.

**Clean up what it names.** A nested worktree is still registered with the outer repo, so
remove it through git rather than deleting the folder — check it first:

```bash
git -C <repo> worktree list                         # the nested path is listed here
git -C <repo>/<Repo>/.claude/worktrees/<name> status --porcelain   # empty: nothing unsaved
git -C <repo> worktree remove <repo>/<Repo>/.claude/worktrees/<name>
```

---

## Adding a bypass actor is the wrong fix

It would restore the old direct-push flow, and it is worth understanding why it was
rejected rather than re-proposing it:

- Rulesets **cannot name individual users** — only roles, teams, GitHub Apps,
  Dependabot, and admins/org owners. The narrowest expression of two named people
  is a team; a repository-role grant is broader still.
- Of the two bypass modes, only "Always allow" would help. "For pull requests only"
  still requires a PR — exactly what the old tooling could not do.
- Bypass is scoped **per actor, not per change**. There is no way to permit only
  version-bump commits. It grants permanent unrestricted direct push of anything,
  to solve a once-per-release problem — reopening the path the hardening closed.

Upstream issues on both release-it and semantic-release answer this question with
"add a bypass actor", which is precisely what is not available here.

---

## "refusing to tag … merged after <sha>, the head the go approved"

Not a failure of the release. The bump PR is merged; a PR landed on the default branch
between the approved plan and that merge, so the tag would ship work the go never named.
ReportService v1.34.17 and two 4U-engine models shipped exactly that way before this check.

Nothing is tagged, so nothing deploys. Show the listed commits, get a go that covers them,
and re-run with the `--expect-head` the refusal prints: the run resumes at the tag.

A re-run **without** `--expect-head` after the bump merged is refused too: its plan is computed
on a tip that already holds the bump, so its own head would wave the refused commits through.
