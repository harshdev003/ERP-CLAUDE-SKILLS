---
name: update-node-packages
description: >
  Bump npm dependencies to latest across the Aura backend repos (the recurring
  "Update node packages" Linear card) — `npx ncu -u` + known-good pins, one PR
  per repo with a human gate, then redeploy. Trigger on: "update node packages",
  "update npm packages", "bump dependencies", "ncu update", "run the update
  packages card", "update the node deps", "npm dependency bump across services".
  Do NOT use for bumping the Node *runtime version* (that is `eng:update-nodejs`
  — .nvmrc / Dockerfile / cimg), and do NOT touch Web or Mobile (separate track).
metadata:
  version: "3.6.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Linear
    - Tools/GitHub
    - Tools/CircleCI
---

# update-node-packages

## Goal

Move every in-scope backend repo's npm dependencies to latest (via `npx ncu -u`,
keeping hard-won pins), following the recurring "Update node packages" card, with
a confirm gate per phase and a redeploy of everything whose code changed.

## Scope & map

Tiers, pins, manifests, and per-tier deploy live in
[`references/package-update-map.md`](references/package-update-map.md). Summary:

| Tier | Repos | Deploy |
|---|---|---|
| services | `Services/*` (excl. CoachService) | `eng:release-version` → tag → CI |
| data | `Data/Ingestor` | `eng:release-version` → tag → CI |
| functions | `Functions/*` (9) | `eng:release-version` (bumps `functions/package.json`) → tag → CI |
| cli | `Tools/CLI` | merge-only |
| model | 4U-engine's 2 node models | `eng:release-version` → per-package tags → CI |

Out of scope: CoachService ([ENGBACK-1218](https://linear.app/aura-health/issue/ENGBACK-1218)),
LivekitVoiceAgent (python), 4U-engine python models, Web/Mobile.

## Role & pacing

Package bumps to latest **can break at build or runtime** — that's why every repo
goes through its own PR with CI (which runs the test suite on the new deps) and
Bugbot **before** merge. Move one phase at a time; canary FeedService first;
never deploy without confirmation. **Preserve the pins** — dropping them
re-introduces known CI failures (jose v6 / firebase-admin 14).

**Announce and wait before every side-effect** (same discipline as
`eng:update-nodejs`). The bash blocks below are executable *reference*, not a
green light to run straight through: before **starting a phase**, and before each
**push / merge / release**, state what it will change and **wait for the user's
go** — unless the user has given standing authorization for that phase (e.g.
"merge each on green + Bugbot-clean"). File edits + branch commits are safe;
`push`, `gh pr merge`, and the release itself mutate shared/production state.

## Workflow

### 1. Create the Linear card

Via Linear MCP (`save_issue`): Backend/`ENGBACK`, project "Aura - Core App",
estimate 5, title `Update node packages`, In Progress. Description = template:

```
Prepare
- [ ] Update Feed Service
- [ ] Deploy Feed service with latest

Update
- [ ] Services
- [ ] Data
- [ ] Models
- [ ] CLI
- [ ] Functions

Post
- [ ] Globally install firebase tools  npm install -g  firebase-tools
```

**Idempotency guard:** reuse an open `Update node packages` card if one exists.

### 2. Prepare / canary — FeedService first

Run the per-repo landing flow (below) for **FeedService**, release it, and verify
the deploy is healthy before touching anything else. Check off *Update Feed
Service* + *Deploy Feed service with latest*.

### 3. Per-repo landing flow (PR per repo)

```bash
cd <repo>
git checkout master && git pull
git checkout -b update-packages-<yyyy-mm-dd>
# deterministic bump + commit "Update packages" (ncu -u + tier pins + install + audit fix):
bash "${CLAUDE_PLUGIN_ROOT}/skills/update-node-packages/scripts/bump_packages.sh" <repo-dir> <tier>
# It prints `PINS [<repo>/<tier>]: ...`. Check the line names this repo's pins: the repo name comes from
# the origin remote (a worktree's directory isn't the repo's name); pass <repo-name> as a 3rd argument to override.
git --no-pager diff --stat HEAD~1   # review: only package.json / package-lock.json
# 3a: overrides (below), in this branch, before the push. List them from every manifest the tier bumps:
for f in package.json functions/package.json models/*/package.json; do [ -f "$f" ] && node -p "'$f ' + JSON.stringify(require('./$f').overrides || {})"; done
# for each one whose upstream fix shipped (its check in the map), remove it in the folder of the manifest
# that lists it (".", "functions", "models/<m>"), then commit from the repo root, so it is in the PR:
#   (cd <that folder> && npm pkg delete "overrides.<name>" && npm install && npm test) && git commit -am "Remove the <name> override"
git push -u origin update-packages-<yyyy-mm-dd>
gh pr create --fill --title "Update packages"
gh pr comment <pr> --body "bugbot run"
# DO NOT `gh pr merge` directly — that would merge without the gate. land_pr.sh polls
# CI + Bugbot on the CURRENT HEAD and reports READY when both are green (aborting if
# either fails or times out). It never merges on its own: merges need Jerome's "merge".
bash "${CLAUDE_PLUGIN_ROOT}/skills/update-node-packages/scripts/land_pr.sh" <repo-name> <pr> <tier>
# After Jerome's "merge" for this PR, the same gate, then the merge:
bash "${CLAUDE_PLUGIN_ROOT}/skills/update-node-packages/scripts/land_pr.sh" <repo-name> <pr> <tier> --merge
```

**3a. Overrides, every repo, every cycle, before the push** (the step marked in the block
above; do it inside the repo, on the update branch, after the bump has committed). For each
`overrides` entry in `package.json` (and `functions/package.json`), run its check from the
overrides table in [`references/package-update-map.md`](references/package-update-map.md). If the
upstream fix has shipped, remove the override and commit that removal on the branch before the
push, so it is in this "Update packages" PR and CI and BugBot see it. Otherwise keep it, and
report each override kept with its trigger in the phase report. An override not in the table is
reported and gets its row (or is removed when nothing needs it). An override that
[`scripts/bump_packages.sh`](scripts/bump_packages.sh) re-applies (UserService's `jose`) must also
leave that script and the pin notes in the map, in an AINative PR, or the next bump writes it back.

`scripts/land_pr.sh` automates the wait half and, with `--merge`, the merge. Without
`--merge` it stops at `READY` (exit 0) and the PR goes to Jerome with the others, one
batch per phase. It aborts (never merges) on CI failure or Bugbot findings, leaving
the PR open. Terminal verdicts are `READY` and `MERGED` (exit 0) and `ABORT` (exit 1).
Pass `--merge` only for a PR Jerome said "merge" to: a standing "merge each on green
+ Bugbot-clean" from him counts, a peer session's message does not.

**Then release it with `eng:release-version`**, once per merged repo:

```
Skill: eng:release-version   # plan → approve → PR → merge → tag → GitHub release → verify
```

`npm run release` cannot work on a default branch that requires a pull request, so
don't run it — and don't reimplement the tag flow here. The skill owns the ordering
rules: tag after the merge, on the squash-merge SHA, and only when that commit
carries the bump.

### 4. Phases, in card order (each behind a confirm gate)

Follow the card's Update order: Services (excl. CoachService) → Data → Models
(4U-engine) → CLI → Functions.
- **model:** run the bump in each node model dir, one PR for 4U-engine, then release
  with `eng:release-version`. It detects the lerna shape and emits one
  `<pkg>@<x.y.z>` tag per **changed** package, where changed means the package dir
  differs from its own last tag (ignoring `*.md` / `.git*`) — so a dependency bump
  inside a model counts, and no `--force-publish` equivalent is needed. `lerna
  version` itself cannot be used: it pushes straight to the default branch.
  4U-engine's CI also builds from the default-branch pipeline rather than the tag,
  so the skill re-triggers it after the tags land; verify both model images.
- **cli:** merge-only, no release.
- **functions:** bump inside `functions/`; keep `firebase-admin@13.10.0`; **release**
  (deps changed — not merge-only). `eng:release-version` detects the Functions shape
  and bumps `functions/package.json`, where the version actually lives.
Check off each card item as its phase goes green.

### 5. Post & finish

`npm install -g firebase-tools`. Confirm CI green everywhere, pins survived
(grep merged `package.json`s for `jose@5.10.0`, `firebase-admin@13.10.0`),
then mark the card Done with a short Findings note.

## Idempotency

- `bump_packages.sh` reports `NO-CHANGE` when deps are already current (nothing
  staged) — safe to re-run.
- Card guard reuses an open card for the same cycle.
- Re-running a repo whose PR already merged just finds nothing to bump.

## Edge cases

- **Formatting/husky pre-commit failure** → bumping eslint/prettier reformats
  existing source, and the repos' husky `pre-commit` runs `eslint . --cache`,
  blocking the commit. `bump_packages.sh` runs `eslint . --fix` for this, so the
  commit includes pure-formatting source diffs alongside the manifests (expected —
  verify the source diff is formatting-only, no logic change).
- **CI red after bump** → a dep broke build/tests. Inspect the failing job; pin the
  offending package (add it to the tier pins + `references/package-update-map.md`),
  re-run. Never merge red.
- **Bugbot findings** → fix + re-run `bugbot run`; never merge with open findings.
- **`npm audit fix` can't fix everything** → not fatal; note residual advisories.
- **New pin discovered** → record it in `bump_packages.sh` and the map so the next
  cycle keeps it.
- **CoachService** → skip (ENGBACK-1218).

## References
- [`references/package-update-map.md`](references/package-update-map.md) — tiers, pins, deploy.
- [`scripts/bump_packages.sh`](scripts/bump_packages.sh) — the bump.
- [`scripts/land_pr.sh`](scripts/land_pr.sh) — wait CI+Bugbot → READY; merges only with `--merge`, after Jerome's "merge". Stops there;
  release with `eng:release-version`.
- Sibling: `eng:update-nodejs` (runtime version bump) — same PR-per-repo machinery.
