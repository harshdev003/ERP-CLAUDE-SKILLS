---
name: update-nodejs
description: >
  Walk through a Node.js runtime version bump across every Aura backend repo on the Node
  track, mirroring the recurring "Update nodejs to version X.Y.Z" Linear card — automation
  with a human confirmation gate before each phase. Creates the ENGBACK card from the
  template, then steps through Prepare (install node via nvm, deploy the Ingestor and Feed
  canaries, install firebase-tools) and Update (Services, Data, Models, CLI, Functions),
  bumping `.nvmrc` and the Dockerfile base image (plus engines.node for Functions) in each
  repo under the Aura workspace, checking off each card item as it goes. Trigger on "update
  nodejs", "update node", "bump the node version", "upgrade node to X", "update nodejs in all
  environments", "new node release, let's roll it out", "run the node update card", or "bump
  node across the services". Do NOT use for npm dependency bumps — that is the separate
  "Update node packages" card — and do NOT touch Web or Mobile, which are on their own Node
  tracks.

metadata:
  version: "3.1.1"
knowledge:
  reads:
    - Guide/Index
    - Tools/Linear
    - Tools/GitHub
    - Tools/CircleCI
---

# update-nodejs

## Goal

Move the pinned Node.js runtime version forward across every Aura **backend**
repo on the Node track, following the recurring "Update nodejs to version
X.Y.Z" Linear card step by step, pausing for the user to confirm before each
phase and before any deploy.

## What "all environments" means

The Node version is pinned in a few files per repo, grouped into the five phases
of the card checklist. Full map (repos, pin files, deploy commands) is in
[`references/environment-map.md`](references/environment-map.md). Summary:

| Phase | Repos | Pins |
|---|---|---|
| Services | `Services/*` (13) | `.nvmrc` + `Dockerfile` + `.circleci/config.yml` (`cimg/node`) |
| Data | `Data/Ingestor` | `.nvmrc` + `Dockerfile` + `.circleci/config.yml` |
| Models | `AI/4U-engine`, `AI/LivekitVoiceAgent` | `.nvmrc` (+ `.circleci/config.yml` where present) |
| CLI | `Tools/CLI` | `.nvmrc` + `.circleci/config.yml` |
| Functions | `Functions/*` (9) | `.nvmrc` + `.circleci/config.yml` + `functions/package.json` engines.node (major only) |

The `.circleci/config.yml` `cimg/node:<ver>` pin is the **CI executor** — it
runs lint/test/build. If it lags the Dockerfile, tests run on the *old* Node
while the image deploys the *new* one, so a "canary" isn't truly validated.
The scanner covers it; before applying, confirm `cimg/node:<version>` exists on
Docker Hub (`https://hub.docker.com/v2/repositories/cimg/node/tags/<version>`)
— cimg does not always publish every Node patch immediately.

Web and Mobile are a **different track** — never bumped here.

## Inputs

- **Target version** — **the latest Node LTS**, unless the user names a specific
  version. Resolve it rather than assuming a value, and always a full
  `MAJOR.MINOR.PATCH`:

  ```bash
  curl -s https://nodejs.org/dist/index.json | python3 -c \
    "import json,sys; print([v for v in json.load(sys.stdin) if v['lts']][0]['version'].lstrip('v'))"
  ```

  Read the LTS line from `nodejs.org`, not from the machine. `nvm ls-remote --lts`
  is a shell **function**, so it resolves to nothing in a non-interactive shell,
  and the locally installed `node --version` is whatever that laptop happens to
  run — often a `current` release ahead of LTS, which is not the fleet target.

  **Two targets when the LTS carries a new major** (Jerome, 2026-11-02):
  - **Services, Data, Models and CLI follow Node LTS**: the resolved LTS is
    their target, new major included. State the jump in the plan.
  - **Functions move to a new major only once Cloud Functions lists that
    runtime as GA**, never while it is Preview. Read the major's row on
    [the runtime doc](https://cloud.google.com/functions/docs/concepts/nodejs-runtime):
    GA → Functions take the LTS too; Preview, or not listed → Functions take
    the newest patch within **their own** major (the command below), and the
    plan says which runtime status held them back. `$FUNCTIONS_MAJOR` is the
    `engines.node` major from `scan_node_versions.py --phase Functions`
    (`functions_engine`), never the all-phase dominant version: once services
    move, that is the Preview line itself. A new major rewrites
    Functions `engines.node`, turning all 9 Functions repos from merge-only
    into releases, so confirm that phase with the user either way.

  ```bash
  curl -s https://nodejs.org/dist/index.json | python3 -c \
    "import json,sys; m=sys.argv[1]; print([v for v in json.load(sys.stdin) if v['version'].lstrip('v').split('.')[0]==m][0]['version'].lstrip('v'))" \
    "$FUNCTIONS_MAJOR"
  ```
- **Root** — the Aura workspace. Auto-discovered from `$AURA_ROOT` or common
  checkout locations (never hardcoded to one contributor's layout); pass `--root`
  to override, or `export AURA_ROOT=<path>` once.

## Role & pacing

You are driving a production runtime rollout. Move deliberately:

- **Scan before you plan, plan before you touch anything.**
- **One phase at a time, in card order** — Services → Data → Models → CLI →
  Functions. Before starting each phase, state what it will change and **wait for
  the user to say go**.
- **Never deploy without explicit confirmation.** File edits are safe and
  reversible; the release pushes to production.

## Workflow

### 0. Resolve inputs

Resolve the latest-LTS candidate with the first command in **Inputs** and
confirm the root. A version the user names overrides the LTS default outright.
Establish the *current* version in step 1 — don't assume it — then apply the
two-target rule from **Inputs**: the candidate is the target for every phase
but Functions; Functions take it only when its major is GA on Cloud Functions,
else the second **Inputs** command's current-major patch. Echo the resolved
targets back alongside the current version, so the rollout is stated as
`<current> → <target>` (and `Functions <current> → <functions target>` when they
differ) before anything is written. **From here on, `<version>` means the
target of the phase in hand:** in the Functions phase, in the per-repo landing
flow for a Functions repo, and when folding a drifted Functions repo, it is the
Functions target, never the LTS.

### 1. Scan — build the plan

Run the scanner (human table on stderr, JSON on stdout):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/update-nodejs/scripts/scan_node_versions.py" \
  --root "$AURA_ROOT"
```

Report to the user:
- The current dominant version and the target.
- **Any drift** the scan flags (a repo whose `.nvmrc`/`Dockerfile` is off the
  dominant version — e.g. a Dockerfile left behind in a past bump). Offer to
  fold the drifted repo into this bump so it lands correct. **Functions held on
  their own major for GA are not drift:** an all-phase scan votes one dominant
  version, so it lists them. Read drift for Functions with `--phase Functions`
  on its own, and never fold a held Functions repo onto a Preview major.
- **Any repo marked `ARCHIVED`** — archived on GitHub, so it is excluded from
  the rollout (see **Archived repos** below). Name it so the user knows it was
  deliberately skipped, not missed.
- **Any repo marked `archived=unknown`** — the `gh` lookup could not answer, so
  it is treated as live and *will* be bumped. If one of these later 403s on
  push, it is almost certainly archived; check it before blaming credentials.
- The full list of repos that will change, per phase.

This scan **is** the plan. Show it and get a go-ahead before any writes.

### Archived repos

A local checkout says nothing about whether its GitHub repo is still live. The
scanner resolves each repo's archived state (`gh repo view <owner>/<repo> --json
isArchived`) and reports it three ways: `archived: true`, `false`, or `null`
(the lookup could not answer — no `gh`, not authenticated, no GitHub origin).

- **Archived → excluded from `--apply`.** Nothing is written. `--include-archived`
  overrides, but there is normally no reason to: the push cannot land.
- **Unknown → treated as live** and still bumped. That direction is deliberate:
  a false "archived" would silently drop a live repo from the rollout, which is
  worse than the failed push this exclusion prevents.
- **`--no-archived-check`** skips the lookups entirely (offline / no `gh`);
  everything then reads as unknown.

**A `git push` 403 in this flow means the repo is archived — not a credential
problem.** It looks identical to the `GITHUB_TOKEN`-shadowing fault in CLAUDE.md
(ENG-971), and that is the wrong diagnosis. Check
`gh repo view AuraHealth/<Repo> --json isArchived` *before* touching
`gh auth status`. If a bump was already committed locally, revert the branch —
nothing else to undo.

### 2. Create the Linear card from the template

Create the card via Linear MCP (`save_issue`), matching the template:

- **Team:** Backend (`ENGBACK`). **Project:** "Aura - Core App". **Estimate:** 5.
  **Title:** `Update nodejs to version <version>`.
- **Description** — the template checklist (see
  [`references/environment-map.md`](references/environment-map.md) for the
  canonical text):

  ```
  Existing: <current-version>

  Prepare

  - [ ] Install latest node
  - [ ] Verify cimg/node:<version> exists on Docker Hub (CI executor)
  - [ ] Deploy Ingestor service with latest
  - [ ] Deploy Feed service with latest
  - [ ] Globally install firebase tools  npm install -g  firebase-tools

  Update

  - [ ] Services
    - [ ] Pull latest code
  - [ ] Data
  - [ ] Models
  - [ ] CLI
  - [ ] Functions (new major only when GA on https://cloud.google.com/functions/docs/concepts/nodejs-runtime, never Preview)

  Notes for future runs (learned on ENGBACK-1216, node 24.15.0 -> 24.18.0):
  - Each repo pins node in THREE places: .nvmrc, Dockerfile (FROM node:<ver>-alpine),
    and .circleci/config.yml (cimg/node:<ver>). Bump all three.
  - Watch per-service apk pins: CoachService pins ffmpeg=<ver> tied to the Alpine
    that ships with the node base (PROD-2270). A node bump can break the build or
    change ffmpeg major (6->8) — check services using fluent-ffmpeg.
  - Monorepos (4U-engine): node lives in models/*/Dockerfile, not the repo root;
    release with `eng:release-version`. It treats a package as changed when its
    directory differs from that package's own last tag, ignoring only `*.md` and
    `.git*`, so a Dockerfile-only bump counts as changed. `lerna version` must not
    be used: it pushes straight to the default branch, which the ruleset rejects.
  - Merge-only (no release) where the runtime doesn't change: CLI, LivekitVoiceAgent
    (python), Functions on a minor bump (engines.node major-only, deploy tag-gated).
  - Stage only pin files when committing — never `git add -A` (repos may carry
    large untracked local data).
  ```

**Idempotency guard:** first search Linear for an open `Update nodejs to version
<version>` card. If one exists, reuse it instead of creating a duplicate.

Show the draft and confirm before creating. Keep the card's identifier; you will
check off each item as its phase completes (re-save the description with the box
ticked, `[ ]` → `[X]`).

### 3. Prepare phase (confirm before running)

Guide, running each on confirmation:

1. **Install node locally:** `nvm install <version> && nvm alias default <version>`
   → check off *Install latest node*.
2. **Verify `cimg/node:<version>` exists** on Docker Hub before any bump — the
   CI executor pull fails otherwise (see the per-repo flow). One check covers
   the whole rollout:
   `curl -s https://hub.docker.com/v2/repositories/cimg/node/<version-as-tag> | ...`
   (`https://hub.docker.com/v2/repositories/cimg/node/tags/<version>`).
3. **Canary deploys** — roll **Ingestor** then **Feed** onto the new Node first
   (they validate the runtime end-to-end): run the **per-repo landing flow**
   below for each, with `--repo Ingestor` / `--repo FeedService`. Verify the
   canary's CircleCI tag pipeline is green (`test` on the new `cimg/node`, then
   `push`) before continuing → check off *Deploy Ingestor* and *Deploy Feed*.
4. **firebase-tools:** `npm install -g firebase-tools` (rebuilds it against the
   new Node) → check off that item.

Do not proceed to the Update phases until both canaries are green.

**Canaried repos are already done — do not re-land them.** Ingestor and
FeedService are fully bumped, committed, and released in Prepare. In their Update
phase the scanner's apply is a no-op (reports "already at <version>") → there's
nothing to commit and no second release to cut; skip them. In particular the
**Data** phase's only repo *is* Ingestor, so once the Ingestor canary is green
the Data phase is already complete — just check it off.

### Per-repo landing flow (PR per repo)

Every repo's bump lands through its **own PR**, then releases from master. This
is the agreed rollout process (a review + CI gate before master).

```bash
cd <repo>
git checkout master && git pull
git checkout -b update-nodejs-<version>
# bump pins (whole phase, or a single repo with --repo):
python3 "${CLAUDE_PLUGIN_ROOT}/skills/update-nodejs/scripts/scan_node_versions.py" \
  --root "$AURA_ROOT" --apply --version <version> --repo <RepoName>
git --no-pager diff                      # review: .nvmrc, Dockerfile, .circleci, (Functions) engines.node
# Stage ONLY the pin files — NEVER `git add -A`. Some repos (e.g. Tools/CLI)
# carry large untracked/bloated local data files that `-A` would sweep into the
# commit (a 500MB+ file then fails the push). Add the known paths explicitly and
# verify nothing else is staged:
# Stage every CHANGED pin file the scanner touched, including NESTED Dockerfiles
# (e.g. 4U-engine's models/*/Dockerfile) — a fixed root-only list would leave those
# nested runtime bumps unstaged and merge an incomplete image bump. Match by name
# from the actual diff so data files (.json/.jsonl/.csv under data/) never sneak in:
git add $(git diff --name-only | grep -E '(^|/)\.nvmrc$|(^|/)Dockerfile$|(^|/)\.circleci/config\.yml$|(^|/)functions/package\.json$') 2>/dev/null
git diff --cached --name-only   # must be only pin files — abort if anything else appears
git commit -m "Update nodejs to <version>"
git push -u origin update-nodejs-<version>
gh pr create --fill --title "Update nodejs to <version>"
# → CircleCI runs the build/test on the PR branch. Wait for it green.
gh pr merge --squash --delete-branch      # confirm with the user before merging
git checkout master && git pull
# RELEASE ONLY IF THIS REPO'S RUNTIME ACTUALLY CHANGED — see the release matrix in
# references/environment-map.md. Do NOT blindly release: a merge-only repo whose
# CircleCI deploy is tag-gated would fire an unnecessary prod deploy.
# Release with the eng:release-version skill — it detects the shape per repo, so the
# per-tier commands below collapse into one call. What still differs is WHETHER to
# release at all:
#   Services / Data ........ release
#   Functions .............. release ONLY on a major bump (engines.node changed);
#                            merge-only on minor. The skill bumps functions/package.json.
#   4U-engine (Models) ..... release — one <pkg>@<x.y.z> tag per changed model
#   CLI, LivekitVoiceAgent . merge-only — NO release
# Never run npm run release / lerna version: both push straight to the default
# branch, which the rulesets reject.
```

The bump touches `.nvmrc` + `Dockerfile` (root **and** nested, e.g.
`models/*/.nvmrc` and `models/*/Dockerfile`; nested pins that disagree scan as `MIXED:`) + `.circleci/config.yml` (+ Functions `engines.node` on a
major) in **one commit**. Confirm before **merge** and before **release** — both
are production-affecting. Verify the tag pipeline (`test` → `push`) after any release.

### 4. Update phases — one at a time, each behind a gate

For **each** phase in order (Services → Data → Models → CLI → Functions):

**Skip the Prepare canaries.** Ingestor and FeedService were fully landed +
released in Prepare, so exclude them here — the apply is a no-op for them and
there's nothing to re-land. In practice the **Data** phase is *already complete*
once the Ingestor canary is green (Ingestor is its only repo); just check Data
off. In Services, run the flow for every repo **except FeedService**.

1. **Announce & confirm.** "Ready to update **<phase>**? This will bump N repos
   and (for containerised phases) redeploy them." Wait for go.

2. **Apply the version bump** (deterministic, idempotent — safe to re-run):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/update-nodejs/scripts/scan_node_versions.py" \
     --root "$AURA_ROOT" --apply --version <version> --phase <PhaseName>
   ```

   The script rewrites `.nvmrc` and `Dockerfile` `FROM node:<ver>-alpine` in
   that phase's repos, and for **Functions** also bumps
   `functions/package.json` `engines.node` — but only on a **major** change
   (24→26); a patch/minor bump leaves engines untouched. It reports each repo as
   *updated*, *already at <version>*, or *SKIPPED — archived on GitHub*. An
   archived repo has no PR and no release; state that it was skipped and move on.

3. **Land each repo via the per-repo landing flow above** (branch → PR → CI →
   merge → release). The `git pull` at the start of that flow is the Services
   phase's *Pull latest code* sub-item. Confirm before merge and before release.
   **CLI has no release** — the merged PR is the whole change.

4. **Verify** each repo's CircleCI tag pipeline is green (`test` → `push`;
   Functions deploy via Firebase honouring `engines.node`).

5. **Check off** the phase's card item (and the Services *Pull latest code*
   sub-item). Move to the next phase only after the user confirms this one is
   healthy.

### 5. Finish

Re-run the scan with **`--expect <version>`** — this asserts every pin (nvmrc,
Dockerfile incl. nested, circleci, Functions engines major) is actually on the
target. *"Zero drift" alone is not proof:* the drift check only tests internal
consistency, so a workspace uniformly on the **old** release passes it. `--expect`
exits non-zero and lists any repo not on `<version>`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/update-nodejs/scripts/scan_node_versions.py" --expect <version>
```

**Split rollout** (Functions held for GA): run the check once per target, as
`--expect <version> --phase <P>` for each of Services, Data, Models and CLI,
and `--expect <functions target> --phase Functions`. The single all-phase
`--expect` fails a correct hold.

Archived repos are reported in their own `archived_off_target` bucket and do
**not** fail the check — they are frozen on whatever they last released with and
can never reach the target. A repo listed as `archived=unknown`, however, lands
in `off_target` and does fail: resolve whether it is archived before calling the
rollout done.

The only repos it should otherwise flag are known, deliberate exceptions (e.g.
CoachService deferred to ENGBACK-1218). Confirm all checklist boxes are ticked,
then mark the card Done (the user closes it, or confirm and set state via Linear MCP).
When Functions were held, first comment on the card
`Functions held on <functions target>: Node <major> is Preview on Cloud Functions`.
`eng:setup-monthly-tasks` reads that line so the next month still files a card
for the Functions join.

## Idempotency

- **Scan-first, apply-idempotent:** `--apply` only writes files that differ;
  re-running a phase reports "already at <version>" and changes nothing.
- **Card guard:** reuse an existing open card for the same version rather than
  creating a duplicate.
- Re-running the whole skill on an already-updated workspace is a no-op that
  simply reports no drift.

## Edge cases

- **Drift found in scan** (e.g. `CoachService` Dockerfile behind) → surface it,
  and fold that repo into the bump so it lands on `<version>`.
- **Major version change** (e.g. 24 → 26) → the script also bumps Dockerfile
  majors, and Functions `engines.node` when the Functions phase gets the new
  major. That happens only when the Cloud Functions runtime doc
  (https://cloud.google.com/functions/docs/concepts/nodejs-runtime) lists the
  major as GA; while it is Preview, Functions stay on their current major.
- **Non-node Dockerfile** (LivekitVoiceAgent → `python:*`) → the script leaves it
  alone; only `.nvmrc` changes there. Expected, not an error.
- **Deploy failure** → stop the rollout, leave remaining phases unchecked, report
  which repo failed. Do not continue past a failed canary.
- **New repo added to a parent dir** → auto-discovered by the scan (it globs for
  `.nvmrc`); no skill edit needed.
- **Archived repo** (e.g. `Services/KlaviyoService`, archived 2026) → the
  checkout still has a `.nvmrc`, so it is discovered, but the scan marks it
  `ARCHIVED`, `--apply` skips it, and `--expect` buckets it separately. Nothing
  to land. **If you somehow reach a push, its 403 is the archive, not
  credentials** — see **Archived repos** in step 1. Do not go down the
  `GITHUB_TOKEN` / `gh auth status` path (CLAUDE.md, ENG-971); confirm with
  `gh repo view AuraHealth/<Repo> --json isArchived`, then revert the branch.
- **`archived=unknown` on a repo** (no `gh`, not authenticated, non-GitHub
  origin) → the scan treats it as live and bumps it. Resolve it before the
  finish check, which will keep failing on it.

## References

- [`references/environment-map.md`](references/environment-map.md) — full repo
  map, pin-file formats, per-phase deploy commands, out-of-scope list.
- [`scripts/scan_node_versions.py`](scripts/scan_node_versions.py) — scan / drift
  report / archived-repo resolution / idempotent apply. Behaviour is covered by
  [`tests/test_scan_node_versions.py`](../../../../tests/test_scan_node_versions.py)
  at the repo root.
