---
name: release-version
description: >
  Release an Aura backend repo by cutting a version: bump it, land it on the default branch
  through a pull request, tag the merged commit, create the GitHub release, then verify the
  deploy pipeline. Covers all four release shapes — Node services, Firebase Functions, the
  LiveKit voice agent, and lerna repos with per-package tags — one repo or a fleet sweep.
  Trigger on "release IntelligenceService", "release the version", "cut a release", "ship a
  patch release", "release all the services", "do a fleet release", "bump and release",
  "publish a new version", "tag a release", "npm run release doesn't work anymore", "release
  the LiveKit agent", "make a dev prerelease", or a request to resume a half-finished release.
  Also triggers on "release a minor version", "release 4U-engine". Do NOT use to write a
  CHANGELOG (`eng:update-changelog`), bump k8s image tags (`eng:bump-k8s-images`), bump npm
  deps (`eng:update-node-packages`), or bump the Node runtime (`eng:update-nodejs`).

metadata:
  version: "3.6.0"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Procedures/Engineering/Release Process  # the narrative half: why the flow has this shape
    - Tools/GitHub                 # PR + release mechanics, gh auth gotchas
    - Tools/CircleCI               # pipeline verification, token resolution
    - Tools/Spinnaker              # the rollout the image push triggers; #deployment posts
---

# Release a version

## Goal

Get a version bump onto a repo's default branch and cut the tag + GitHub release that
deploys it — through a pull request, because that is the only route the branch rulesets
leave open.

## What a release is here

Two artifacts, plus the deploy they trigger:

1. **An annotated tag** — `v<x.y.z>`, or `<pkg>@<x.y.z>` on lerna repos. **The tag is the
   deploy trigger, not a label.** CI builds and deploys the image from the tagged commit.
2. **A GitHub Release** titled `Release <x.y.z>`, whose body is the commit list since the
   previous tag.

There is **no `CHANGELOG.md`** anywhere in this fleet, and this skill does not create one.
The "doc in the repo" is the GitHub Release page. For a `CHANGELOG.md` with Linear issue
linkage, that is `eng:update-changelog` — a different artifact for a different purpose.

## A release is its own act

Features land on the default branch through the **normal PR process**. A release then runs
on top of an already-merged branch, and its PR carries **only the version bump** — no
feature code. The release notes span `<last tag>..<default-branch HEAD>`: every feature
merged since the previous release.

So this skill is never how a feature ships. It is how what already merged gets a version.

## What it depends on — and what it deliberately does not

**It never runs `npm run release`, `release-it`, `lerna`, or npm at all.** The scripts shell
out to exactly two commands: `git` and `gh`. Version files are edited in place rather than
via `npm version`. So the `release` / `release:dev` scripts, the root delegating shortcuts,
and `.release-it.json` are all free to go — detection reads version fields, not scripts.

Editing in place means the bump also has to reach the copies of the version that `npm
version` would have moved for free. `package-lock.json` holds two — the top-level
`version` and `packages[""].version` — and the bump rewrites both in the same commit,
beside the `package.json` they belong to. Only those two fields move: the dependency tree
is never touched, because a release must ship the tree that CI already tested. A lock npm
did not write, or one carrying no version of its own, is reported and left alone rather
than reformatted; nothing resolves from these fields, so a cosmetic mismatch never blocks
a tag.

**lerna stays, including its devDependency and `lerna.json`.** lerna is the package manager
for the two multi-package repos, and only its *release* path is retired: `lerna version`
pushes the bump commit straight to the default branch, which the ruleset rejects, exactly as
release-it does. Everything else lerna does there is untouched.

`lerna.json` is also load-bearing for this skill: it is how a multi-package repo is
recognised, and it supplies the `packages` glob that locates the packages. Without it both
repos stop being detectable and `plan` fails with `no releasable version` rather than
silently mis-releasing — but it fails. Note the file needs no lerna installed to be read
here; it is parsed as a plain manifest.

Everything else the skill reads is a version field or a git/GitHub fact:

| Reads | For |
|---|---|
| `package.json` `version` (root, or `functions/`, or per lerna package) | the baseline and the bump target |
| `pyproject.toml` `version` | the second version file on the LiveKit agent |
| `package-lock.json` `version` and `packages[""].version` | npm's own copies of the same version, moved with it |
| `lerna.json` `packages` | which directories are independently versioned |
| git remote, tags, commits | slug, last release, notes, change detection |
| `gh` — default branch, PRs, releases | routing and release state |

## Inputs

- **repo** — a path to a local checkout, or a repo name to resolve under `~/Development/Aura`.
- **level** — `patch` (the fleet default), `minor`, or `major`. Omit to let the skill
  propose one from conventional commits.
- **version** — an explicit `x.y.z`, overriding any inference.
- **pre-release** — an identifier such as `dev`, matching the `release:dev` script that six
  repos carry.
- **fleet** — a list of repos for a sweep.
- **force** — release even when nothing changed since the last tag. Off by default; see
  "Forcing a release with no new work" below.

## Workflow

### 1. Resolve and plan — always, before anything else

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/release-version/scripts/release.py plan <repo>
```

This writes nothing. It prints the resolved repo, GitHub slug, default branch, detected
shape, the version file paths, current → proposed version with the reason, the tag, the
rendered release notes, and every mutating step that `run` would take.

Three things in that output are worth actually reading rather than skimming:

- **The version file path.** On a Functions repo it is `functions/package.json`, because
  the root `package.json` has no `version` field. If the plan shows the root file for a
  `Functions/*` repo, the shape was misdetected — stop. The lock file listed under it is
  the one beside that same `package.json`, so a Functions repo shows
  `functions/package-lock.json` and never the root lock, which carries no version.
- **The proposed level and its reason.** Inference defaults to `patch`, which is the safe
  direction: an under-called bump ships a correct artifact under a modest number, while an
  over-called one claims a breaking change that did not happen.
- **Warnings.** "no commits since `<tag>`" means the release notes will be empty — usually
  a sign the repo has nothing to release.

### 2. Show the plan and get approval

**Present the plan to the user and wait.** This step is not optional and not a formality:
`run` opens a PR, merges it to the default branch, pushes tags, and triggers a production
deploy. A wrong version or a wrong repo is visible in the plan and expensive afterwards.

State plainly what will happen: *"This will merge a version bump to `master` on
`AuraHealth/<repo>` and deploy `v<x.y.z>` to production."*

For a **fleet** sweep, show the whole table and get one approval for the set — then report
per-repo results, never a single aggregate "done".

**The go covers the plan's range head.** The plan prints `Range head: <sha>`; pass it to `run`
as `--expect-head <sha>`. After the bump PR merges, `run` refuses to tag a commit that carries
anything merged after that head (on lerna, anything touching a releasing package). That stops a
PR merged while the bump waited for CI from shipping outside the go. The refusal lists the extra
commits; the bump stays merged and nothing is tagged. Get a go that covers them, then re-run with
the `--expect-head` the refusal prints. Without the flag, `run` checks against its own plan's head,
which still catches merges during the bump PR's CI. A run that starts after the bump merged refuses
to tag without the flag: its own head already holds the bump, so it would approve anything. Each run prints how many commits each tag covers.

**Lerna: release one package with `--package <name>`** (repeatable, on `plan`, `run` and
`status`). Without it every package that changed since its own tag is released, so one commit
touching every model's `.nvmrc` would tag them all under a go for one. With it, only the named
packages are bumped and tagged; the plan warns about each changed package it leaves out. The range
check answers per package: a late commit counts only against the package whose directory it touches.

### 3. Run

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/release-version/scripts/release.py run <repo> --expect-head <sha> [--package <name>] [--level minor] [--pre-release dev]
```

The steps, in this order:

1. bump every version file for the release, and the lock beside it, in an isolated worktree at
   `<tmp>/aura-release-worktrees/<repo>/release-<version>` — the user's working tree is
   never touched, so a dirty checkout does not block a release. The worktree sits
   **outside** the repo so no repo-wide tool walks it: it is a full second copy with no
   `node_modules`, and a pre-push lint hook that reaches it fails the tag push
2. commit `Release <x.y.z>`, push the branch **with `--no-verify`** — a pre-push hook
   cannot succeed from a worktree, so running it only turns a working release into a
   half-done one. A worktree holds tracked files only, and a husky hook needs two
   things that are not tracked: the generated `.husky/_/husky.sh` it sources (gitignored,
   so absent wherever the worktree lives) and the `node_modules` its `npm run lint`
   body needs. Nothing is traded away — the branch changes version files only, and its
   PR runs the repo's real CI before the merge this release depends on. The **tag** push
   in step 6 keeps its hooks: it runs in the main checkout, where they work
3. open a PR against the default branch
4. **wait until GitHub reports the PR mergeable**, then merge it by whichever method the
   repo permits. Both facts are read from GitHub rather than assumed:
   - a repo whose default branch requires a status check is not mergeable the instant its
     PR opens, and a merge attempted then is refused. `run` polls `mergeStateStatus`, so
     GitHub's own view of its rules is the authority and no copy of that policy lives here.
     `UNSTABLE` counts as mergeable — it means a *non-required* check is failing, which the
     repo itself accepts. The wait gives up after 15 minutes naming the check states, so a
     requirement no wait can satisfy — a required review — surfaces instead of hanging
   - **squash** is preferred and is the fleet norm, but not every repo permits it, so the
     method comes from the repo's own squash / merge-commit / rebase settings, in that
     order. `gh` names those fields differently across versions, so both spellings are
     tried; a `gh` that accepts neither is reported as a `gh` fault, not as a repo that
     forbids every method
5. read the **merge commit SHA** from the PR, confirm it is an ancestor of the default
   branch, and **confirm it contains the bump**. This is what makes step 4's choice safe:
   every method lands a new commit, and the SHA to tag is always read back, never assumed
6. tag that SHA — one tag per released package — and push the tags from the main
   checkout, **with** its pre-push hook. A hook that fails only on files git does not
   track (every file it reports sits under an untracked or ignored path, such as a stray
   nested worktree copy) is retried with `--no-verify`, and the run names those paths
   in its output so they can be removed. A hook failure on any tracked file, or one
   that names no file, stops the release — see
   [`references/failure-modes.md`](references/failure-modes.md)
7. create the GitHub release(s)
8. on shapes whose CI reads tags at HEAD, re-trigger the default-branch pipeline

`--dry-run` reports what it would do without writing.

### 4. Verify the deploy, not the tag

The tag starts a chain — CircleCI `test` → `push` builds and pushes the image, the push
fires the service's Spinnaker **Quick deployment** pipeline, and that pipeline patches dev,
then promotes to prod. `verify` reads each link:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/release-version/scripts/release.py verify <repo> --tag v1.2.3
```

| Line | Meaning |
|---|---|
| `success` / `running` / `failed` / `unverified` | the CircleCI pipeline for the tag |
| `rolled out: Spinnaker "<pipeline>" for <APP> completed (dev, then prod)` | the cluster was patched in both environments |
| `rolling out` / `rollout NOT STARTED` | in flight, or no execution since the tag yet — re-run in a minute |
| `ROLLOUT FAILED` | Spinnaker failed the execution; exit `1`, open the linked execution |
| `rollout UNCONFIRMED: …` | no pipeline mapped for this repo, or #deployment could not be read |
| `deployed: v1.2.3 is serving traffic` / `NOT SERVING` | IntelligenceService only — the version on the newest `llmCall` rows (BigQuery `llm.llm_call`, `GCP_KEY`) |

**`no-pipeline` on a tag-built repo means nothing built or deployed the tag.** A lost
GitHub event does this (CoachService v1.24.5). The line says so. Starting the pipeline
builds and deploys, so it is a write: name it (repo, tag, "starts the tag pipeline, which
deploys") and wait for the user's explicit yes, even inside a release that already has its
go. Only then re-run with `--trigger-missing` to start `POST /pipeline {"tag": "v1.2.3"}`. It refuses while the tag is
under 5 minutes old (the pipeline may still be on its way, and two would deploy twice) or
when the tag's age can't be read. A started pipeline reports `triggered` (exit 1: started is
not deployed); re-run `verify` to follow it. Repos whose CI builds from the default branch
(4U-engine) never get a tag pipeline: `no-pipeline` is expected there.

**`unverified` and `UNCONFIRMED` are not passes** — the check could not run, so the
deploy was confirmed neither way. Report them as such. For deeper pipeline triage, compose
`eng:investigate-circleci`.

**How the rollout is read.** spinbot posts `Pipeline starting|complete|failed for <APP>` in
#deployment for every execution; Spinnaker's own API sits behind OAuth, so Slack is the only
headless record. A message names no tag or image, so the match is the repo's
**application and pipeline** (`rollout.PIPELINES`) on the first execution that **started**
after the tag. The pipeline name is load-bearing: UserService and UserWeb share the `user`
application. The script reads #deployment with `SLACK_BOT_TOKEN`, through its copy of
`shared:read-slack`'s `slack_read.py` — it leads with the token because a script cannot call
the Slack MCP. When the token is missing or Slack refuses, the result says so; then compose
**`shared:read-slack`** for #deployment (`GG6CJ6P0D`) with `oldest` at the tag time, follow its
pagination to the end of the window, save what it returns verbatim, and pass it back:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/release-version/scripts/release.py verify <repo> --tag v1.2.3 --deployment-log <file>
```

The same parser classifies it, so the verdict never rests on reading the messages by eye.

Do not act on a release until the rollout line says `rolled out` — testing a newly shipped
field against a pod still running the previous version makes a missing field look like a
behavioural result rather than a stale rollout. Functions repos deploy from CircleCI
(`deploy` → `promote`), not Spinnaker, and lerna packages have no mapping yet; both report
`UNCONFIRMED` on the rollout line. Full chain in [`references/shapes.md`](references/shapes.md).

### 5. Resuming a half-finished release

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/release-version/scripts/release.py status <repo>
```

State is **derived from the repo and GitHub, never from a local file**, so `run` picks up
wherever it stopped: already-released is a no-op, tags-but-no-release creates the releases,
merged-but-untagged tags the merge commit. A resumed run never applies a second version
bump. **A release branch with no open PR is resumed only if it is built on the current default
branch.** One left by a stopped run before a fix landed would ship the old commit, so `run`
refuses it, pushes nothing, and prints the commands that delete the worktree, the local
branch and the remote branch; re-run after them. Full table in [`references/failure-modes.md`](references/failure-modes.md).

**A resume reports as `resumed`, not `released`, and lists what is still unreleased.** The
distinction is the one worth reading in the output, because the two outcomes are opposite in
the only way that matters: a resume finishes a version that already exists on the default
branch, so it ships **none of the caller's changes**. Whatever they wanted released is still
sitting there unreleased.

```
resumed: v2.81.9
  this FINISHED AN EXISTING release and shipped no new version
  still unreleased in @aura/user-service:
    - Update nodejs to 24.19.0 [ENGBACK-1308] (#103)
  re-run to release them
```

So on `resumed`, **re-run** — the second invocation cuts the version the caller asked for.
A mixed plan, where some packages resume and others cut new versions, reports `released`:
real work is shipping. `--json` carries the same thing as `status`, `unreleased` and `next`,
so a fleet driver can branch on it rather than parsing prose. **A driver must not treat
`resumed` as "shipped"** — that is exactly the reading that lets a sweep report success while
every repo in it is still unreleased.

## The three ordering rules

Everything above hangs on these. They are enforced in the script, not left to care:

1. **Tag after the merge.** CI builds from the tagged commit and services report their own
   version from the file bumped in it, so a tag placed earlier deploys a wrong version.
2. **Tag the merge commit by SHA.** A squash merge creates a new commit; the branch head is
   not what landed on the default branch.
3. **Push the branch and the tags separately.** One `git push --follow-tags` is not atomic —
   the tag can land while the branch is rejected, starting a pipeline for a tag that
   rollback then deletes. That produces a red "test job failed" alert for what is really one
   blocked push. See [`references/failure-modes.md`](references/failure-modes.md).

## Fleet mode

Replaces the loop in `Services/release.sh`, whose commented-out repo lists were the
interface. Plan every repo first, show one combined table, get one approval, then run them
in sequence.

Each repo is an independent PR, so a fleet release is N PRs. Report a per-repo row with the
tag, the pipeline verdict and any error — and **carry failures into the summary rather than
dropping them**. A repo that failed to release must not read as one that had nothing to
release; the script keeps those distinguishable by exit code:

| Exit | Meaning | In a sweep |
|---|---|---|
| `0` | released, or already released (no-op) | success |
| `2` | genuine error — unresolvable shape, refused tag, failed push | **needs attention** |
| `3` | nothing to release — no package changed since its last tag | normal, report as skipped |

Never fold `3` into `2`, or `2` into a skip.

## Shapes

Four, detected automatically from the repo layout. The differences that matter — where the
version file lives, the tag format, which CI jobs to check, and whether the pipeline needs
re-triggering — are in [`references/shapes.md`](references/shapes.md). Read it when a plan
looks wrong for a repo, or when adding a repo that fits none of them.

Summary:

| Shape | Repos | Version file | Tag |
|---|---|---|---|
| `node` | 24 — `Services/*`, `Web/*`, `AI/EmotionSystem`, `Data/Ingestor`, `Legacy/AuraWeb` | `package.json` | `v<x.y.z>` |
| `functions` | 9 — `Functions/*` | **`functions/package.json`** | `v<x.y.z>` |
| `python` | 1 — `AI/LivekitVoiceAgent` | `package.json` **+** `pyproject.toml` | `v<x.y.z>` |
| `lerna` | 1 — `AI/4U-engine` | one per package | `<pkg>@<x.y.z>` |

An unrecognised repo raises an error rather than being released as a plain `node` repo.

## Output

For one repo: the resolved plan, the steps taken, the tag(s), the merge commit, the CI
verdict and the rollout verdict. For a fleet: one row per repo with the same fields plus an explicit failure column.

## Forcing a release with no new work

`--force` releases a repo whose commit range is empty. Two cases call for it: forcing a
fresh image build of what is already on the default branch, and bumping past a version that
shipped broken.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/release-version/scripts/release.py \
  run <repo> --force --version 1.8.0
```

It only widens what counts as releasable. **Every guarantee downstream is unchanged** — the
bump is still verified present in the commit being tagged, the tag still lands after the
merge on the squash-merge SHA, the GitHub release is still created. A forced release is a
normal release over an empty range.

Two things to expect, both of which the plan states outright as a `FORCED` warning:

- **The image is identical.** Same tree, so CI rebuilds the same artifact under a new
  number. That is the point when the goal is a rebuild; it is waste otherwise.
- **The release notes are empty.** There are no commits to list.

`--force` does not bypass the approval gate, and it is not a way past a *failure* — an
unresolvable shape, a refused tag, or a rejected push all still stop the run. It only
answers "nothing changed", which is a fact about the range, not an error.

**Forcing a version that is already fully released is refused** (exit `2`). `run` would
read the same state, find the tag and the release present, and return `already-released`
without tagging — so a plan showing the bump steps would promise work that never happens.
`--force` re-cuts a **new** version; re-cutting a live tag would need the tag deleted
first, which is the rollback hazard behind the phantom-CI-alarm in
[`references/failure-modes.md`](references/failure-modes.md). To rebuild an existing tag,
re-run its pipeline directly with `eng:rerun-circleci`.

## Edge cases

- **Nothing to release** — no commits on the default branch since the last tag (or, on a
  lerna repo, no package changed, ignoring `*.md` and `.git*`). Reported as exit `3`, not an
  error. This is also what makes a second `run` on a finished release safe: the tag points
  at the branch head, so the range is empty and there is nothing to cut. `--force` overrides
  it deliberately; `--version` alone does **not** — the guard is evaluated before version
  resolution, so an explicit version on a finished repo still exits `3`.
- **A version on the default branch that is not fully released** — no tag, or a tag with no
  GitHub release. A previous run stopped part-way, or someone bumped by hand. The skill
  **finishes that version** rather than bumping past it; bumping would leave it undeployed
  and skip the version entirely. Shown in the plan as level `resume`, naming which artifact
  is missing. Note this means the **first** run on a repo carrying tags without releases
  proposes creating those releases — on 4U-engine, all 23 packages, once. It touches no code
  and self-heals; see [`references/failure-modes.md`](references/failure-modes.md).
- **A stale local checkout** — the baseline comes from the default branch, not the working
  tree, and the plan warns when the two disagree. A working-tree baseline could otherwise
  propose a version below one already shipped.
- **Unrecognised repo shape** — stop and report what was missing. Never fall back to
  releasing it as a `node` repo.
- **`origin/<default-branch>` not resolvable** — the plan fetches first and then reports the
  exact fetch command. A stale local branch would compute notes over the wrong range.
- **Dirty working tree** — irrelevant: the bump happens in an isolated worktree.
- **A retired repo** — archived repos reject a push with a `403`, so the release fails after
  the bump commit exists. `AuraHealth/DataJobs` is the retired one today (PROD-4297) and is
  not releasable.
- **A `403` from `gh` on check-runs** — a credential fault, not a CI verdict. See
  [`references/failure-modes.md`](references/failure-modes.md).
- **Re-run on a finished release** — a no-op by design.

## References

- [`references/shapes.md`](references/shapes.md) — the four shapes, detection order, per-shape
  CI jobs, and how `verify` reads them.
- [`references/failure-modes.md`](references/failure-modes.md) — the phantom CI alarm, the
  tag-safety guards, resume-state derivation, and why a bypass actor is the wrong fix.
- [`Procedures/Engineering/Release Process`](https://www.notion.so/3b375e23af208166b8a5fef6e96bb82f)
  in AI Knowledge — the narrative half of this skill: what a release is, when it happens, who
  owns verifying the deploy, and why a pull request is the only route onto the default branch.
  Declared in `knowledge.reads`, so `retrieve-knowledge` pre-fetches it. It deliberately holds no
  steps or flags — those live here.
