# The four release shapes

`scripts/repo_shape.py` detects these automatically. Read this when a plan looks
wrong for a repo, when adding a repo that fits none of them, or when verifying CI
after a release.

Detection is by **positive evidence**, in this order, so an unrecognised repo
raises `ShapeError` rather than being mis-released as a plain node repo:

1. `lerna.json` present → `lerna`
2. `functions/package.json` has a version **and** the root `package.json` does not → `functions`
3. root `package.json` has a version, and a `pyproject.toml` carries one too → `python`
4. root `package.json` has a version → `node`

Order matters between 1 and 2: a lerna root also has no version, so checking the
Functions heuristic first would misclassify it.

---

## 1. `node` — Node service / web / data

The common case. 24 repos: `Services/*`, `Web/*`, `Legacy/AuraWeb`,
`AI/EmotionSystem`, `Data/Ingestor`.

| | |
|---|---|
| Version file | `package.json` at the repo root |
| Tag | `v<x.y.z>` |
| CI trigger | the tag |
| CI jobs | `test` → `push` (build + push the image — which triggers the Spinnaker rollout, see below) |
| Re-trigger needed | no |

Six of these also carry a `release:dev` script (`release-it --preRelease=dev`) —
CoachService, ContentService, EmailService, IntelligenceService, PaymentService,
UserService. `--pre-release dev` is the equivalent.

---

## 2. `functions` — Firebase Functions

9 repos under `Functions/*`. **The version is not at the repo root**, which is the
whole reason this is a separate shape.

| | |
|---|---|
| Version file | `functions/package.json` — the root `package.json` has **no `version` field at all** |
| Bump runs in | `functions/` |
| Git work runs in | the repo root |
| Tag | `v<x.y.z>` |
| CI trigger | the tag, and **only** the tag |
| CI jobs | `test` → `deploy` (dev) → `promote` (prod) |
| Re-trigger needed | no |

The root `package.json` carries a delegating shortcut,
`"release": "cd functions && npm run release"`, so `npm run release` appears to
work from the root while bumping a nested file.

CI is gated `tags: only /^v.*/` with `branches: ignore /.*/`. Nothing runs on a
branch push, so the release PR gets no pipeline and the tag is the sole trigger —
unlike `lerna` below, no re-trigger is needed.

`engines.node` is major-only (`"24"`), so a minor/patch bump moves no deployed
runtime.

---

## 3. `python` — LiveKit voice agent

One repo: `AI/LivekitVoiceAgent`. As `node`, plus a second version file.

| | |
|---|---|
| Version files | `package.json` **and** `pyproject.toml`, which must move in the **same commit** |
| Tag | `v<x.y.z>` |
| CI jobs | `test` → `push` |

`src/version.py` reads the version from `pyproject.toml` at startup and CI builds
the image from the tagged commit, so a tag whose commit bumped only
`package.json` deploys an agent that misreports its own version. `run` refuses to
tag unless **every** version file in the commit carries the target version.

---

## 4. `lerna` — independent versioning

1 repo: `AI/4U-engine` (23 packages under `models/*`, active).

| | |
|---|---|
| Version files | one `package.json` per package |
| Tag | `<pkg>@<x.y.z>` — **not** `v<x.y.z>` |
| Tags per release | one per *changed* package, all on the one merge commit |
| CI trigger | the **default-branch** pipeline, which inspects tags at HEAD |
| CI jobs | `test` → `push` |
| Re-trigger needed | **yes** — see below |

**Change detection.** A package is released only if it changed since **its own**
last tag, ignoring `*.md` and `.git*` — mirroring lerna's
`ignoreChanges: ["*.md", ".git*"]`. A docs-only touch is not a release. A package
with no tag at all has never shipped and counts as changed.

**Why a re-trigger.** 4U-engine's `push` job runs on the default-branch pipeline
and builds every `<pkg>@<version>` tag pointing at HEAD. Under a PR flow the
branch merge — and therefore that pipeline — happens *before* the tags exist, so
the pipeline sees no tags and skips. `run` re-triggers the default-branch pipeline
after the tags land. The job is explicitly idempotent (it skips images that already
exist), so re-triggering is safe.

**Why the commit subject matters.** That same job treats a commit whose subject
matches `^Publish` with no tags at HEAD as a hard error and `exit 1`s — a guard
against silently building zero images. lerna's own subject is literally `Publish`.
This skill commits `Release <…>`, which halts the job cleanly instead. `build_plan`
refuses to proceed if the configured subject would trip the guard, so the guard
cannot be re-broken by editing `COMMIT_SUBJECT`.

**The slug always comes from the git remote, never the directory name.** The two
diverge often enough that inferring one from the other is unsafe, and a wrong slug
sends every `gh` call to the wrong repo. The clearest example the fleet has produced
is the retired `DataJobs`: the directory was `Data/DataJobs`, the canonical repo
`AuraHealth/DataJobs`, and the remote pointed at `AuraHealth/AuraDataJobs` — a
redirect, so it resolved without erroring and disagreed with both names.

---

## The deploy chain: tag → CircleCI → image → Spinnaker → cluster

For every repo that runs on the Kubernetes clusters — the `node` services and
web apps, and `lerna`'s models:

1. **The tag** starts the CircleCI tag pipeline: `test`, then `push`.
2. **`push`** builds and pushes
   `us-central1-docker.pkg.dev/aura-build/aura-repository/<image>:<tag>`. CI green
   means *this* happened — nothing has been deployed yet.
3. **The image push** publishes to Pub/Sub, which triggers the service's Spinnaker
   pipeline at [spin.aurahealth.io](https://spin.aurahealth.io). Each service has a
   **Quick deployment** pipeline (named `Quick deployment`, or `… for Service` /
   `… for Web` where two repos share an application). It patches the Deployment on
   **dev**, then **promotes to prod** — no manual judgment.
4. **spinbot** posts `Pipeline starting for <APP>` and then `Pipeline complete|failed
   for <APP>` in **#deployment** (`GG6CJ6P0D`), linking the execution.

Observed on UserService `v2.83.0` (2026-10-06): CI green ~10 min after the tag,
Spinnaker started 08:01:12 UTC, dev served the new code by 08:02:01, and the
pipeline completed (prod included) at 08:02:50.

**AuraInfra's `deployment.yaml` is not the running version.** AuraInfra keeps one
`k8s/apps/<group>/<service>/deployment.yaml` per service, shared by dev and prod
(per-env secrets sit in `dev/` and `prod/`). Spinnaker patches the image by digest
and bypasses that file, so its tag lags. Ask the cluster, or the rollout line.

`functions` repos are outside this chain: CircleCI's `deploy` (dev) and `promote`
(prod) jobs deploy Firebase directly, so their CI line is the deploy.
The LiveKit agent (`python`) has no Spinnaker application either; #deployment
has never carried it.

## Verifying the deploy

The tag starts the pipeline; it is not the outcome. `verify` polls CircleCI and
checks the job names for the detected shape — a verifier hardcoding one shape's
names reports a false green on another. Once CI is green (or could not be read),
it reads the Spinnaker rollout from #deployment — see "Reading the rollout" below.

```bash
python3 scripts/release.py verify <repo> --tag v1.2.3
```

Statuses: `success`, `running`, `failed`, `no-pipeline`, `unverified`.

**`unverified` is not a pass.** It means no CircleCI token was in the environment,
so the deploy could not be confirmed either way. Report it as unverified and check
manually — never as green.

**`running` covers more than CircleCI's `running`.** A job is pending at `running`,
`blocked`, `queued`, `not_running` or `on_hold`, and `verify` treats all five as
in-flight. `blocked` matters most: in a `test → push` fan-in, `push` sits at
`blocked` for the whole time `test` is running, so it appears on essentially every
release. Classifying by "not `success`" instead reports that healthy pipeline as
`failed` — a false red that sends the reader hunting a CI break that does not
exist, which is the same phantom failure [`failure-modes.md`](failure-modes.md)
opens with. Genuine terminal-bad statuses (`failed`, `canceled`, `unauthorized`,
`timedout`, …) still report `failed`; `not_run` is a config-filtered job, not a
failure. Found on the first real release, where `{test: running, push: blocked}`
reported `failed`.

**`no-pipeline` on a `lerna` repo is expected**: that shape builds from the
default-branch pipeline, not a tag pipeline. Check the default-branch pipeline
instead.

## Reading the rollout

`scripts/rollout.py`. Statuses: `complete`, `running`, `failed`, `not-started`,
`unknown`.

- **The match is application + pipeline, never application alone.** A spinbot
  message carries no tag and no image, only `<App>'s <link|pipeline name>`. The
  `user` application runs both UserService's `Quick deployment for Service` and
  UserWeb's `Quick deployment for Web`, so an application-only match confirms one
  repo's release with the other's rollout. `PIPELINES` maps each repo explicitly,
  and a test holds every entry to the pairs spinbot actually posted; a repo absent
  from it reports `unknown` rather than a name-derived guess. Names compare
  casefolded — the producer spells them both ways (`Quick Deployment for Service`).
- **The execution must have *started* after the tag.** The read is windowed at the
  tag's creation time, so it can hold the `complete` of an earlier image whose
  `starting` fell before the window. Counting it would be a false green for an image
  that has not rolled out at all.
- **The first execution after the tag is this release's.** When more follow, the
  output notes it: a later image push may already have replaced this one.
- **Statuses are classified by explicit sets.** Echo emits `starting`, `complete`
  and `failed`; anything else reads as `unknown`, never as a pass.
- **The read path.** `SLACK_BOT_TOKEN` + `conversations.history` through
  `slack_read.py`, a byte-identical copy of `shared:read-slack`'s canonical helper
  (the bot is a member of #deployment). Without it, read the channel with
  `shared:read-slack` (its MCP path, paginated to the end of the window) and pass
  the saved result as `--deployment-log`; the parser accepts both the API JSON and
  the MCP rendering.
  Spinnaker's API (`api.spin.aurahealth.io`) redirects to an OAuth login, so it has
  no headless read today.
