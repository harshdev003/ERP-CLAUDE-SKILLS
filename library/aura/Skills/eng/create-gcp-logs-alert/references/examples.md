# Worked examples

Real signals covering both payload shapes, both condition types, and both log sources
(GKE containers and Cloud Functions).

## A. Absence alert on a `textPayload` (print) service — activation loader publish

**Signal:** the activation-model loader logs `Published 13260 keys to /mnt/nfs/activation-model/4.23/category_ml422_b.json` via Python `print()` → `textPayload`. If it stops, recommendations go stale.

**Verify (Step 1):**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging read \
  'resource.type="k8s_container" AND resource.labels.namespace_name="aura-ai" AND resource.labels.container_name="activation-model-loader" AND "Published"' \
  --project=firebase-auratech16 --limit=1 --freshness=1d --format=json
# -> textPayload: "Published 13260 keys to /mnt/nfs/activation-model/4.23/category_ml422_b.json"
```

**Metric `ActivationModelLoaded`** filter:
```
resource.type="k8s_container"
resource.labels.cluster_name="aura-prod-cluster-a"
resource.labels.namespace_name="aura-ai"
resource.labels.container_name="activation-model-loader"
textPayload:"Published" AND textPayload:"keys to /mnt/nfs/activation-model/"
```
Label extractor: `"Service": "EXTRACT(resource.labels.container_name)"`.

**Policy** `Activation Model Loaded Check on Activation Model Policy` — `policy-absence.json`
with `metric.type = "logging.googleapis.com/user/ActivationModelLoaded"`, 12h window.

## B. Absence alert on a `jsonPayload` (structured) service — neural trainer

**Signal:** `neural-model-trainer` calls `logger.audit("Trained 'neural-model'")` — a
structured entry with `jsonPayload.message`. Backs the "Neural Model Not Trained" alert.

**Metric `NeuralModelTrained`** filter:
```
resource.type="k8s_container"
resource.labels.cluster_name="aura-prod-cluster-a"
resource.labels.namespace_name="aura-ai"
jsonPayload.serviceContext.service="neural-model-trainer"
jsonPayload.message="Trained 'neural-model'"
```
Label extractor: `"Service": "EXTRACT(jsonPayload.serviceContext.service)"`.

**Policy** `Neural Model Not Trained Check on Neural Model Trainer Policy` — `policy-absence.json`
with `metric.type = "logging.googleapis.com/user/NeuralModelTrained"`, 12h window,
`alertStrategy` `autoClose 604800s` + `notificationPrompts ["OPENED"]`.

> Naming: the metric is the event (`...Trained`); the policy displayName describes the
> failure being watched (`... Not Trained Check on ... Policy`). Full rules:
> [`naming.md`](naming.md).

## C. Threshold alert — error count in a window (pattern)

**Signal:** count `severity="ERROR"` (or a `textPayload:"..."` error string) for a service
and alert when the count within a window exceeds a value.

**Metric** filter (example):
```
resource.type="k8s_container"
resource.labels.cluster_name="aura-prod-cluster-a"
resource.labels.namespace_name="aura-ai"
resource.labels.container_name="<service>"
severity="ERROR"
```

**Policy** — `policy-threshold.json` with `metric.type = "logging.googleapis.com/user/<Name>"`,
`comparison COMPARISON_GT`, `thresholdValue <N>`, `perSeriesAligner ALIGN_DELTA` with
`alignmentPeriod 300s` (so each point is the count in a 5-minute window), `duration 0s` — fires
when more than N errors occur in a 5-minute window. Do **not** use `ALIGN_RATE`: on a DELTA
counter it yields a per-second rate, so `thresholdValue: N` would mean ~N errors/second.

## D. Threshold alert on a Cloud Function — FirebaseAuth unresolved userId claim

**Signal:** the FirebaseAuth `authUserClaim` function (a `beforeUserSignedIn` blocking
trigger) logs a structured WARNING `User claim unresolved` when a sign-in can't resolve a
`userId` claim, with a `status` field (`no-mapping`, `ambiguous`, `skipped-unverified`).
Backs ENGBACK-1776.

**Verify (Step 1)** — the source is a Cloud Function, not a GKE container:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging read \
  'resource.type="cloud_function" AND resource.labels.function_name="authUserClaim" AND severity>=WARNING' \
  --project=firebase-auratech16 --limit=1 --freshness=3d --format=json
# -> logName: projects/firebase-auratech16/logs/firebase-auth
#    resource: {type: cloud_function, labels: {function_name: authUserClaim, region: us-central1, project_id: firebase-auratech16}}
#    jsonPayload: {message: "User claim unresolved", status: "ambiguous", provider: …, uidIsProfile: …}
```

**Metric `UserClaimUnresolved`** filter:
```
logName="projects/firebase-auratech16/logs/firebase-auth"
AND resource.type="cloud_function"
AND resource.labels.function_name="authUserClaim"
AND severity=WARNING
AND jsonPayload.message="User claim unresolved"
```
Label extractor: `"status": "EXTRACT(jsonPayload.status)"`, declared in
`metricDescriptor.labels` as `{"key": "status", "description": "no-mapping, ambiguous or skipped-unverified"}`.
No `Service` label — one function feeds this metric.

**Policy** `User Claim Unresolved Check on Firebase Auth Policy` —
`userLabels {"context": "security"}`, two `conditionThreshold` conditions (combiner `OR`),
both filtering on `resource.type="cloud_function"` (a `k8s_container` filter matches
nothing here):

| Condition | Filter addition | Aggregation | Threshold |
|---|---|---|---|
| More than 5 unresolved claims in a day | — | `ALIGN_SUM`, `REDUCE_SUM`, `alignmentPeriod 86400s` | `> 5` |
| Any ambiguous claim | `metric.label.status="ambiguous"` | `ALIGN_SUM`, `REDUCE_SUM`, `alignmentPeriod 300s` | `> 0` |

`alertStrategy` `autoClose 604800s` + `notificationPrompts ["OPENED"]`, the three default
channels, and a `documentation.content` telling the on-call how to query and break down the
entries. Tested end to end with a synthetic `test:true` entry — see
[`testing-and-renaming.md`](testing-and-renaming.md).

## Payload-shape cheat sheet

| Service style | Field to filter on | Example |
|---|---|---|
| Python `print()` / stdout | `textPayload` | activation loaders |
| winston / structured `logger.*` | `jsonPayload.message` (+ `jsonPayload.serviceContext.service`) | neural-model, neural-model-trainer |
| Cloud Function structured logger | `jsonPayload.message` (+ `resource.labels.function_name`) | FirebaseAuth `authUserClaim` |

When in doubt, run the Step 1 read and look — never assume.
