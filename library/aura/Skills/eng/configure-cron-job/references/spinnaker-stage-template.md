# Spinnaker cronjob stage template

> **Status.** The stage-JSON shape below (implemented in
> `scripts/patch_spinnaker_pipeline.py`) was validated by reconstructing the pre-edit
> intelligence-service pipeline and re-generating the exact PROD-3406/PROD-3461 edit from
> it — 0 differences across all 33 stages, including the precondition-stage wiring. The
> **manual UI walkthrough** (Path B below) is still a first-pass approximation written
> without ever driving the Spinnaker UI directly for this — **refine it with a real
> screenshot the first time this skill uses Path B end-to-end.** See `Tools/Spinnaker` in AI
> Knowledge for the canonical, kept-current version once it exists (ENG-907).

## Two paths: `spin` CLI vs manual UI

Gate (Spinnaker's API) has no static credential in this org — it's gated behind Google
OAuth2 restricted to the `aurahealth.io` Workspace domain
(`hal config security authn oauth2 edit --provider google --user-info-requirements
hd=aurahealth.io` in AuraInfra's Makefile), confirmed live: `api.spin.aurahealth.io` returns
an OAuth redirect (`302`), not a bare JSON response. So this skill never authenticates
itself — it only uses a session the user already established.

**One-time human setup for Path A** (not run by the skill):
1. Install `spin` (no Homebrew formula — direct binary):
   `curl -LO https://storage.googleapis.com/spinnaker-artifacts/spin/$(curl -s https://storage.googleapis.com/spinnaker-artifacts/spin/latest)/darwin/arm64/spin && chmod +x spin && sudo mv spin /usr/local/bin/spin`
   (use `darwin/amd64` on Intel Macs). Note: the `spinnaker/spin` GitHub repo was archived
   Oct 2025 — this GCS artifact path is the maintained install method regardless.
2. Configure `~/.spin/config` with the Gate endpoint (`https://api.spin.aurahealth.io`) and
   an `oauth2` auth block — see [Install and Configure Spin
   CLI](https://spinnaker.io/docs/setup/other_config/spin/).
3. Run any read command, e.g. `spin application list` — **`spin login` is not a real
   subcommand**; the *first* invocation itself triggers a browser-based OAuth flow (local
   callback on `localhost:8085`) and caches the resulting token in `~/.spin/config` for
   reuse. Later calls in the same or later sessions reuse the cached token silently, with
   auto-refresh via the refresh token — no repeated browser round-trip, until the refresh
   token itself is revoked/expired (undocumented duration, provider-dependent).

Once that's done, the skill can call `spin pipeline get`/`spin pipeline save` headlessly.
**If the user hasn't done this setup, fall back to Path B (manual UI) — never attempt the
OAuth flow from within the skill.**

## What you're adding

Every AuraInfra service app in Spinnaker has one pipeline with a `pubsub` trigger on new GCR
image tags. It fans out to a `patchManifest` stage per deployment/cronjob, per environment,
gated by a `checkPreconditions` stage ("Check Tag Version") that blocks promotion to prod
until every dev-side stage has completed and the pushed tag isn't a `-dev` build.

Adding one new cronjob means adding **two** new stages (dev + prod) and updating **one**
existing stage (the precondition gate).

## Stage JSON template

`scripts/patch_spinnaker_pipeline.py` implements this template directly (auto-detecting the
refIds by stage name where possible) — this section is a reference for understanding what
it generates, or for driving Path B by hand if the script isn't used.

Substitute `{{APP}}`, `{{CRONJOB_NAME}}` (e.g. `intelligence-service-feed-schedule-reminders`),
`{{CONTAINER_NAME}}` (same as `{{CRONJOB_NAME}}` in every existing example), `{{DEV_DEPLOY_REFID}}`
(the app's existing "Deploy to dev" stage refId — usually `"1"`), `{{PROMOTE_PROD_REFID}}` (the
app's existing "Promote to prod" stage refId — usually `"2"`), and `{{NEXT_REFID_DEV}}` /
`{{NEXT_REFID_PROD}}` (the next two unused refIds in the pipeline, dev first then prod, matching
existing numbering convention).

**Dev stage:**

```json
{
  "account": "kubernetes-dev-account",
  "app": "{{APP}}",
  "cloudProvider": "kubernetes",
  "location": "aura-services",
  "manifestName": "cronJob {{CRONJOB_NAME}}",
  "mode": "static",
  "name": "Deploy {{HUMAN_JOB_NAME}} job to dev",
  "options": { "mergeStrategy": "strategic", "record": false },
  "patchBody": [
    {
      "spec": {
        "jobTemplate": {
          "spec": {
            "template": {
              "spec": {
                "containers": [
                  { "image": "${ trigger['payload']['tag']}", "name": "{{CONTAINER_NAME}}" }
                ]
              }
            }
          }
        }
      }
    }
  ],
  "refId": "{{NEXT_REFID_DEV}}",
  "requisiteStageRefIds": ["{{DEV_DEPLOY_REFID}}"],
  "source": "text",
  "type": "patchManifest"
}
```

**Prod stage** — identical shape, with these changes:
- `"account": "kubernetes-prod-account"`
- `"name": "Deploy {{HUMAN_JOB_NAME}} job to prod"`
- `"refId": "{{NEXT_REFID_PROD}}"`
- `"requisiteStageRefIds": ["{{PROMOTE_PROD_REFID}}"]`

**Precondition stage patch** — find the `checkPreconditions` stage named "Check Tag Version"
and add `{{NEXT_REFID_DEV}}` to its `requisiteStageRefIds` array (alongside every other
existing dev-side stage refId). **Do not add the prod refId here** — only dev-side stages
gate the precondition check; the prod stages depend on the precondition passing, not the
other way around.

## Path B: manual UI walkthrough (approximate — refine with a screenshot)

1. Open `https://spin.aurahealth.io` and navigate to the target app's pipeline
   (e.g. the "intelligence" app's main deploy pipeline).
2. Open the pipeline's configuration and switch to its JSON/text editor view (labelled
   something like "Edit as JSON" in the pipeline config UI — confirm exact label/location on
   first real use).
3. Locate the `stages` array. Insert the two new stage objects above (with real values
   substituted), and append the new dev refId to the "Check Tag Version" stage's
   `requisiteStageRefIds`. (Or: paste the pipeline's exported JSON into
   `scripts/patch_spinnaker_pipeline.py --pipeline-file <exported.json> ...` to generate this
   automatically, then paste its output back into the editor.)
4. Save. Spinnaker validates the JSON on save — if it rejects the change, the error usually
   points at a duplicate `refId` or a `requisiteStageRefIds` entry that doesn't exist.
5. Export/copy the saved pipeline JSON and paste it back to the skill so it can verify the
   wiring landed correctly.

## Verification checklist (what the skill checks against your paste-back)

- [ ] Both new stages have unique `refId`s not used elsewhere in the pipeline.
- [ ] Dev stage `account` is `kubernetes-dev-account`; prod stage `account` is
      `kubernetes-prod-account`.
- [ ] `manifestName` is exactly `"cronJob {{CRONJOB_NAME}}"` on both stages, matching the
      CronJob's actual `metadata.name`.
- [ ] Dev stage `requisiteStageRefIds` points at the app's "Deploy to dev" stage; prod stage
      points at "Promote to prod".
- [ ] The "Check Tag Version" precondition stage's `requisiteStageRefIds` now includes the
      new dev stage's `refId`.
