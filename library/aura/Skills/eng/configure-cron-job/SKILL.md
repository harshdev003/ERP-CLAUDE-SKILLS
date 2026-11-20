---
name: configure-cron-job
description: >
  Registers a new Kubernetes CronJob for an AuraInfra service end-to-end: scaffolds the
  manifest from that service's existing sibling cronjobs, verifies the target Docker image tag
  actually contains the required npm script before anything is deployed (walking forward
  through the source repo's releases if the pinned tag predates it), applies the manifest to
  dev then prod via kubectl with an explicit approval gate at each step, and computes the
  Spinnaker pipeline stage JSON plus manual UI walkthrough needed to wire the new job into
  that service's deploy pipeline, then verifies the wiring once you paste back the saved
  pipeline JSON. Trigger on "add a cronjob for {service}", "register a new cron job in
  AuraInfra", "schedule {npm script} as a cronjob", "configure-cron-job", "deploy this job to
  k8s and wire it into Spinnaker", or a description of standing up a new scheduled job for an
  AuraInfra service. Do NOT use for modifying an existing CronJob's schedule or resources, or
  deploying a regular Deployment or Service.

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index                # baseline routing aid (universal)
    - Tools/GitHub                # gh CLI auth + API pattern used for the tag-gate check
    - Tools/Kubernetes            # AuraInfra cluster-switch / kubectl / CronJob conventions
    - Tools/Spinnaker             # pipeline JSON shape, patchManifest stages, precondition gating
---

# configure-cron-job

Registers a new AuraInfra CronJob for a service job and wires it into that service's
Spinnaker deploy pipeline — the full workflow behind
[PROD-3406 / PROD-3461](https://linear.app/aura-health/issue/PROD-3406) (intelligence-service
`feedScheduleReminders` + `feedInactivityNudge`), codified so the next one doesn't repeat two
mistakes made doing it by hand: nearly shipping a CronJob pinned to an image tag that predated
the npm script it runs, and hand-editing Spinnaker pipeline JSON from scratch with no template.

**Core contract:**
- **Deterministic discovery + tag-gate.** Reading sibling manifests and checking the target
  image tag against the source repo's releases happen in
  [`scripts/scaffold_cronjob.py`](scripts/scaffold_cronjob.py) and
  [`scripts/verify_tag_has_script.py`](scripts/verify_tag_has_script.py) — no LLM guessing
  about whether a tag has the script.
- **Plan before any cluster mutation.** Show the scaffolded manifest + tag-gate result and
  wait for explicit approval before any `kubectl apply`.
- **Dev before prod, always with a checkpoint.** Never apply to both clusters in one step —
  confirm dev looks right (via `get-services-cronjob-versions`) before touching prod.
- **Idempotent re-runs.** `kubectl apply` is declarative, so re-applying the same manifest is
  a safe no-op. The Spinnaker stage patch is **not** inherently idempotent: appending blindly
  on a second run duplicates the two stages. Before appending (Step 6), check whether the
  fetched pipeline already contains stages for this cronjob
  (`manifestName: "cronJob <cronjob-name>"`) — if present, skip the append and report "already
  wired" instead of adding duplicates.
- **Spinnaker: `spin` CLI if configured, manual paste-back otherwise.** Gate (Spinnaker's
  API) has no static credential — it's gated behind Google OAuth2 restricted to the org's
  Workspace domain, so this skill never authenticates itself. If `spin` is already installed
  and has a cached, working session in `~/.spin/config` (the user completed the one-time
  OAuth flow themselves, outside this skill), the skill shells out to `spin pipeline get` /
  `spin pipeline save` via
  [`scripts/patch_spinnaker_pipeline.py`](scripts/patch_spinnaker_pipeline.py) — no browser
  round-trip needed for that run. If `spin` isn't set up, it falls back to computing the
  stage JSON and walking the user through the Spinnaker UI, then verifying their paste-back.
  See [`references/spinnaker-stage-template.md`](references/spinnaker-stage-template.md) for
  both paths — the manual UI walkthrough there is a first-pass approximation, refine it with
  a screenshot the first time you actually drive it end-to-end.

## Prerequisites

- **`gh` CLI** authenticated (`gh auth status` should pass) — used for the tag-gate check and
  to read the source service repo's releases.
- **Local AuraInfra checkout** with `kubectl` configured and the `make ENVIRONMENT=<env>
  switch` target available (see `Tools/Kubernetes`).
- **`kubectl` context access** to both the dev and prod GKE clusters.

## Inputs

- `service` — the AuraInfra service directory name, e.g. `intelligence-service`
  (`k8s/apps/services/<service>/`).
- `job_script` — the npm script the job runs, e.g. `job:feedScheduleReminders`.
- `source_repo` — the `org/repo` that owns `job_script` and cuts the releases the image is
  built from, e.g. `AuraHealth/IntelligenceService`.
- `schedule` — the cron expression, e.g. `*/8 * * * *`.
- `target_tag` (optional) — the image tag to pin to. Defaults to the tag currently used by
  a sibling cronjob for the same service (discovered in Step 1), which is usually the right
  choice since Spinnaker will bump it on the next release anyway.
- `env_overrides` (optional) — explicit `key=value` env vars if the job needs non-default
  batch/limit sizes.

## Step 1 — Discover this service's conventions

Run `scripts/scaffold_cronjob.py --service-dir <path-to-service> --discover-only` to read
every sibling `*.yaml` CronJob manifest directly under that service directory (not its
`dev/`/`prod/` subfolders — those are separate per-environment overlays, not sibling
cronjobs) and report: the slug style in use (hyphenated vs camelCase — default to hyphenated,
matching the intelligence-service convention established in PROD-3406), the
`resources.requests` most commonly used, whether `activeDeadlineSeconds` is set on this
service's jobs, the `envFrom` secret name, and the image reference currently in use.

Surface this to the user before scaffolding — if the service has no existing cronjobs to
learn from, ask for the missing values explicitly rather than guessing.

## Step 2 — Tag-gate verification

Run `scripts/verify_tag_has_script.py --repo <source_repo> --script <job_script> --tag
<target_tag>`. This checks `package.json` at that tag via `gh api
repos/<org>/<repo>/contents/package.json?ref=<tag>` for the script. If it's missing, the
script walks forward through the repo's releases (ordered by `published_at`) and reports the
first tag that actually has it — this is the exact check that caught intelligence-service
`v0.273.0` predating the scripts it needed to run, before it shipped to prod.

**Do not proceed to Step 3 if the target tag lacks the script** — use the first tag the
script reports as having it instead, and tell the user why the originally-requested tag was
rejected.

## Step 3 — Scaffold the manifest

Run `scripts/scaffold_cronjob.py --service-dir <path> --job-script <job_script> --schedule
<schedule> --image-tag <verified_tag> [--write]` to render
`k8s/apps/services/<service>/<slug>cronjob.yaml` matching the conventions discovered in
Step 1. Without `--write` it prints the manifest for review; with `--write` it writes the
file directly into the target AuraInfra checkout.

## Step 4 — Plan/approve gate

Present the new manifest and the Step 2 tag-gate result as a single summary. **Wait for
explicit user approval before any `kubectl apply`.** This mirrors the plan-then-approve
pattern in `eng:sync-translations` — nothing touches a live cluster until the user has seen
the diff.

## Step 5 — Apply: dev, confirm, then prod

On approval:
1. `make ENVIRONMENT=dev switch && kubectl apply -f <manifest-path>` in the AuraInfra
   checkout.
2. Confirm via `make get-services-cronjob-versions | grep <cronjob-name>` — check the
   reported image digest/tag matches the verified tag from Step 2.
3. Only after the user confirms dev looks right, repeat for prod
   (`make ENVIRONMENT=prod switch && kubectl apply -f <manifest-path>`, then re-verify).

Never apply to both clusters in a single unconfirmed pass.

## Step 6 — Spinnaker stage wiring

First, check whether `spin` is usable non-interactively: `spin application list` (or any
read-only `spin` call) either succeeds silently (cached session in `~/.spin/config` is
valid) or fails/hangs waiting on a browser (no session, or expired). Never attempt to drive
the OAuth flow yourself — it needs a human at a browser. Branch on the result:

**Path A — `spin` is configured (preferred):**

1. `spin pipeline get --application <app> --name <pipeline-name> > pipeline.json` to fetch
   the live pipeline config.
2. **First, guard against a duplicate wiring:** scan the fetched `pipeline.json` for stages
   whose `manifestName` is `cronJob <cronjob-name>`. If they already exist, this cronjob was
   wired on a prior run — skip the append, report "already wired," and go straight to the
   step 5 verification below (confirm the existing stages and precondition wiring are
   correct). Only if none exist, run
   `scripts/patch_spinnaker_pipeline.py --pipeline-file pipeline.json --app <app>
   --cronjob-name <cronjob-name> --human-job-name "<human job name>" --write patched.json` —
   it auto-detects the "Deploy to dev" / "Promote to prod" / "Check Tag Version" stages by
   name, computes the next two unused `refId`s, and appends the two new `patchManifest`
   stages (dev depends on "Deploy to dev", prod depends on "Promote to prod"), then adds the
   new dev stage's `refId` to the precondition stage's `requisiteStageRefIds`. If the script
   reports `ambiguous_stage_match` or `stage_not_found`, pass the relevant
   `--dev-deploy-refid` / `--promote-prod-refid` / `--precondition-refid` override.
3. Show the diff (just the two new stages + the precondition's changed
   `requisiteStageRefIds`, not the whole pipeline) and get explicit approval before applying.
4. On approval: `spin pipeline save --file patched.json`.
5. Verify: `spin pipeline get --application <app> --name <pipeline-name>` again and confirm
   the new stages and precondition wiring are present as expected.

**Path B — `spin` not configured (fallback):** use
[`references/spinnaker-stage-template.md`](references/spinnaker-stage-template.md)'s manual
flow — compute the same stage JSON with `patch_spinnaker_pipeline.py` (it accepts a
hand-exported pipeline JSON just as well as one from `spin pipeline get`), print it plus a
manual Spinnaker UI walkthrough (open the app's pipeline config → Edit as JSON → merge the
new stages → Save — flagged as a first-pass approximation pending refinement from a real
screenshot), then ask the user to paste back the saved pipeline JSON and verify the same
checklist as Path A step 5.

In both paths: confirm the new stages have the correct `refId`s, the correct `account` per
environment (`kubernetes-dev-account` / `kubernetes-prod-account`), correct
`manifestName: "cronJob <cronjob-name>"`, and that the precondition stage's
`requisiteStageRefIds` includes the new dev stage. Report any mismatch back to the user
rather than assuming it's correct.

## Step 7 — Summary

Report: manifest file path, tag-gate result (target tag vs. tag actually used), dev/prod
apply confirmation (image digest observed), and Spinnaker verification result — or "waiting
on your paste-back" if Step 6.3 hasn't happened yet.
