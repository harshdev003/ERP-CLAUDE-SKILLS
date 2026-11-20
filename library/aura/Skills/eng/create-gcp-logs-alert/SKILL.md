---
name: create-gcp-logs-alert
description: >
  Create a GCP log-based metric plus a Cloud Monitoring alert policy for the Aura
  prod cluster, in one guided flow. Use when the user wants to be alerted based on
  log output — either a "success log stopped" absence alert (e.g. "alert when the
  activation loader stops publishing", "alert if neural-model-trainer stops logging
  'Trained'", "page us if a service stops emitting its heartbeat log") or a
  "too many of X" threshold alert (e.g. "alert if there are more than N ERROR logs
  in 5 minutes"). Trigger phrases: "create a log-based alert", "add a gcp log alert",
  "alert when X stops logging Y", "alert on a log metric", "log-based metric alert",
  "create an alert if a service stops logging", "alert if too many errors in the logs".
  This is the write/alerting complement to `eng:investigate-gcp-logs` (read-side). Do
  NOT use it to read, search, or triage logs (use `eng:investigate-gcp-logs` /
  `eng:search-gcp-by-user`), or to create non-log-based metric alerts.
metadata:
  version: "3.4.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/GCP
    - Tools/Kubernetes               # kubectl access to the cluster
    - Tools/Slack                    # confirming a test alert landed in its channel
---

# Create GCP Logs Alert

Create a **log-based counter metric** + a **Cloud Monitoring alert policy** in the Aura
prod project, from logs emitted by a GKE container or a Cloud Function (Firebase Functions),
following the conventions already used by `ModelLoaded`,
`NeuralModelTrained`, and the activation alerts. This is the write-side complement to
[`eng:investigate-gcp-logs`](../investigate-gcp-logs/SKILL.md) — that skill reads logs;
this one turns a log signal into an alert.

Two alert shapes are supported:

- **Absence** (default) — "a success log stopped." Fires when the metric receives **no**
  data for a window. Use for heartbeat / did-this-job-run signals (loader published,
  model trained/loaded, cron completed).
- **Threshold** — "too many of X." Fires when the metric's rate/count exceeds a value.
  Use for error-rate signals (e.g. `> N` `ERROR` logs in 5 minutes).

## Auth

Every `gcloud` call goes through the bundled `scripts/gcloud_sa.py`, which runs it as the
`GCP_KEY` service account in a private config and falls back to your own gcloud login only
when `GCP_KEY` is unset. The account needs **Logs Configuration Writer** + **Monitoring
AlertPolicy Editor** on `firebase-auratech16` for the create steps (reads need only Logs
Viewer + Monitoring Viewer). A failure line names the credential it ran as. Check it with
`python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py config get-value account`.

## The one rule that matters: verify the log shape first

**You cannot write the metric filter from memory.** The filter field depends on how the
service logs:

- **`textPayload`** — plain `print()` / stdout lines (e.g. the Python activation loaders).
- **`jsonPayload.message`** — structured loggers (winston, the neural `logger.audit`).

Guessing wrong produces a metric that silently counts nothing. So **Step 1 always reads a
real log entry** to confirm the payload field and the exact resource labels, and builds the
filter from what's actually there. `eng:investigate-gcp-logs`'s
[`references/log-schema.md`](../investigate-gcp-logs/references/log-schema.md) documents
the two formats in depth.

## Inputs

The user provides **one** of:

1. **An example log entry** (pasted JSON) — parse the payload field (`textPayload` vs
   `jsonPayload.message`) and resource labels from it.
2. **A Logs Explorer filter** — validate it returns entries, then reuse/refine it.
3. **A description** ("alert when `activation-model-loader` stops logging `Published … keys`")
   — derive a candidate filter and confirm it against real entries in Step 1.

Optional (each has a default): metric name, condition type (`absence` | `threshold`),
window, threshold value, notification channels, project.

## Workflow

### Step 1 — Verify the signal and detect the log shape

Run a read against Cloud Logging to confirm the log exists and see its structure:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging read '<candidate filter>' \
  --project=firebase-auratech16 --limit=2 --freshness=1d --format=json
```

From the returned entry, determine:

- **Payload field:** is the message in `textPayload` (string) or `jsonPayload.message`?
- **Log source:** `resource.type`. `k8s_container` for GKE services; `cloud_function` for
  Firebase Functions (e.g. the FirebaseAuth `authUserClaim` sign-in trigger), which also
  carry a `logName` such as `projects/firebase-auratech16/logs/firebase-auth`.
- **Resource labels:** for GKE, `resource.labels.cluster_name`, `namespace_name`, and the
  disambiguator — `container_name` (loaders/print services) or, for structured services,
  `jsonPayload.serviceContext.service`. For a Cloud Function, `resource.labels.function_name`
  is the disambiguator, alongside the `logName`.
- **Match style:** if the line has variable content (e.g. `Published 13260 keys …`), match
  a stable substring with the `:` (has) operator, not `=` (exact).

**If no entry is returned → STOP.** There is nothing for a metric to count. Report it and
ask whether the log is actually emitted, and at a level that isn't suppressed (many Node
services default to `LOGGER_LEVEL=warn`, so `info` lines never reach Logging — the signal
may need to be logged at `warn`/`error` or an always-emitted audit level, or via `print`
to stdout). If appropriate, suggest adding the log line first (see the `Trained '…'` log
that was added to the activation loaders for exactly this reason) before creating the alert.

### Step 2 — Propose the metric + alert spec, then confirm

Assemble and show the user, before creating anything (this writes to prod monitoring):

- **Metric name** — PascalCase event name, **no service prefix**, never snake_case:
  `CouldNotValidate`, `ModelLoaded`, `UserClaimUnresolved`.
- **Policy displayName** — `<Event in words> Check on <Service> Policy`:
  `Service Could Not Validate Email Check on Email Service Policy`,
  `User Claim Unresolved Check on Firebase Auth Policy`.
- **`userLabels.context`** — the existing area value (`security`, `redis`) when the alert
  belongs to one; omitted otherwise.
- **Filter** — built from the Step 1 shape (see [`references/gcloud.md`](references/gcloud.md)).
- **Condition** — absence (default) or threshold; window; threshold value if applicable.
- **`alertStrategy`** — `autoClose 604800s` + `notificationPrompts ["OPENED"]`.
- **Notification channels** — default the three Aura channels (below).

**Run the naming check** in [`references/naming.md`](references/naming.md) against these
rules and against the live names (`logging metrics list`, `monitoring policies list`,
compared case-insensitively) before showing the spec. A name that breaks a rule is fixed
now — a metric can't be renamed later without a create-repoint-delete cycle.

Get an explicit go-ahead.

### Step 3 — Create the log-based metric

Fill [`assets/metric-config.json`](assets/metric-config.json) and create it:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging metrics create <NAME> \
  --project=firebase-auratech16 \
  --config-from-file=/tmp/<name>-metric.json
```

The metric is a counter: `metricKind DELTA`, `valueType INT64`, `unit "1"`. For GKE, keep
the `Service` label extractor (from `container_name` or `jsonPayload.serviceContext.service`)
to mirror the neural metrics. For any source, add a label extractor for each payload field a
condition should split on (e.g. `"status": "EXTRACT(jsonPayload.status)"`), declared in
`metricDescriptor.labels` too.

### Step 4 — Create the alert policy

**Idempotency — check before creating.** `gcloud monitoring policies create` always makes a
NEW policy, and Cloud Monitoring allows duplicate `displayName`s. On a re-run (e.g. a partial
failure where the metric was created but the policy step is retried) a second create opens a
duplicate policy that double-notifies on the same signal. Check first:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py monitoring policies list --project=firebase-auratech16 \
  --filter='displayName="<exact displayName>"' --format="value(name)"
# empty -> safe to create; non-empty -> a policy already exists (reuse/update it, don't re-create)
```

Then fill the matching template and create it:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py monitoring policies create \
  --project=firebase-auratech16 \
  --policy-from-file=/tmp/<name>-policy.json
```

- **Absence:** [`assets/policy-absence.json`](assets/policy-absence.json) —
  `conditionAbsent`, `perSeriesAligner ALIGN_SUM`, `crossSeriesReducer REDUCE_SUM`,
  `alignmentPeriod` + `duration` = the window (default `43200s` / 12h), `trigger.count 1`.
- **Threshold:** [`assets/policy-threshold.json`](assets/policy-threshold.json) —
  `conditionThreshold`, `comparison COMPARISON_GT`, `thresholdValue`, `perSeriesAligner ALIGN_DELTA`
  with `alignmentPeriod` = the window (default `300s` / 5m) so each aligned point is the **count**
  in that window (`thresholdValue` is compared as a count), `duration 0s`. Do **not** use
  `ALIGN_RATE` here — on a DELTA counter it's a per-second rate, so `thresholdValue: N` would mean
  ~N/second, not N per window.

Both use `alertStrategy` `autoClose 604800s` + `notificationPrompts ["OPENED"]`,
`combiner OR`, `enabled true`, and the three notification channels. Before creating, set:

- the condition `filter`'s `resource.type` to the type the metric's time series land on —
  the templates ship with `k8s_container`; a Cloud Function metric needs
  `resource.type="cloud_function"`, and a metric built from `http_load_balancer` logs needs
  `resource.type="l7_lb_rule"` (its series are written there, not on `http_load_balancer`).
  A wrong type makes the condition match nothing, with no error;
- `displayName` per the naming rule, and `userLabels.context` when it applies.

A just-created metric can take up to ~10 minutes to register; until then the create fails
with `404 Cannot find metric(s)`. Wait and retry — the filter is not at fault.

### Step 5 — Verify and explain forward-only behavior

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py logging metrics describe <NAME> --project=firebase-auratech16
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py monitoring policies list --project=firebase-auratech16 --filter='displayName ~ "<name>"'
```

Then tell the user the timing so they aren't surprised:

- **Log-based metrics are forward-only** — they only count entries ingested *after*
  creation; there's no backfill.
- The metric **won't appear in Metrics Explorer** (and has no chart data) until its **first
  data point** — i.e. the next matching log.
- An **absence** alert becomes fully effective once that first data point exists; until then
  it can't distinguish "never had data" from "stopped." For a signal that fires every N
  hours, a run within the window populates it and the alert stays quiet.

If the source is a CronJob and the user wants immediate confirmation, offer to trigger a
manual run (`kubectl -n aura-ai create job <name>-manual --from=cronjob/<name>`) so the
metric gets its first data point.

For a rare signal (a warning that may not fire for days), offer an **end-to-end test**: a
synthetic `test:true` entry written to the same `logName` + resource, with no real user
data, proves filter → metric → policy → channel now. Wait a few minutes after creating the
policy before writing it, and confirm the notification by reading the channel's history
by invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md) `history`
(bounded by `--oldest`), not Slack search.
Procedure and timing: [`references/testing-and-renaming.md`](references/testing-and-renaming.md).

## Encoded defaults (Aura prod)

| Thing | Value |
|---|---|
| Project | `firebase-auratech16` |
| Cluster | `aura-prod-cluster-a` |
| Namespace | `aura-ai` |
| Metric shape | `metricKind DELTA`, `valueType INT64`, `unit "1"` |
| Absence aggregation | `ALIGN_SUM` / `REDUCE_SUM`, `alignmentPeriod`+`duration` `43200s` (12h) |
| Threshold aggregation | `ALIGN_DELTA`, `alignmentPeriod` `300s` (count per 5m window), `duration` `0s` |
| `combiner` | `OR` |
| `alertStrategy` | `autoClose 604800s`, `notificationPrompts ["OPENED"]` |
| Notification channels | `11467163257391956365`, `9276031353074528112`, `3690673794603392095` |

Discover channels if these ever change:
`python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py beta monitoring channels list --project=firebase-auratech16`.

## Gotchas

- **`textPayload` vs `jsonPayload.message`.** Always confirmed in Step 1 — it decides the
  filter field. Never assume.
- **Empty objects are rejected on create.** `gcloud monitoring policies create` fails with
  `INVALID_ARGUMENT: … documentation.{content,subject,links}` if the policy JSON contains
  an empty `documentation: {}` (exported policies include it). Omit empty
  `documentation` / `userLabels` / `alertStrategy` objects — the templates already do.
- **RTDB scraping alerts are not log-based, and they already exist.** The Realtime Database emits no per-request logs, so its alerts are metric-threshold policies on the built-in `firebasedatabase.googleapis.com/` metrics, outside this skill. Several are live, all named `RTDB … Check on Firebase Policy` and posting to #alerts-security ([ENGBACK-1434](https://linear.app/aura-health/issue/ENGBACK-1434)). The current list, thresholds, backtest and label values are on `Tools/GCP` → "RTDB scraping and enumeration detection", including why there is no plain egress alert. Check that list (`gcloud monitoring policies list --filter='displayName ~ "^RTDB "'`) and change an existing policy rather than adding a duplicate.
- **Forward-only.** Metrics don't backfill; a just-created metric has no history.
- **Metrics Explorer visibility.** A metric type is absent from the picker until its first
  data point.
- **Unique names.** Metric names are unique per project — check `metrics list` first.
- **Metric names can't be renamed.** Create the new metric with the same filter and labels,
  wait for it to register, repoint every policy condition, then delete the old one —
  [`references/testing-and-renaming.md`](references/testing-and-renaming.md).
- **Substring vs exact.** Use `textPayload:"…"` (has) for variable lines; `container_name`
  is a reliable disambiguator between similarly-named services
  (`activation-model-loader` vs `activation-category-model-loader`).

## References

- [`references/gcloud.md`](references/gcloud.md) — the exact commands + filter recipes.
- [`references/examples.md`](references/examples.md) — worked examples (neural, activation,
  FirebaseAuth Cloud Function), covering both payload shapes and both log sources.
- [`references/naming.md`](references/naming.md) — metric, policy and `userLabels` naming
  rules, and the Step 2 naming check.
- [`references/testing-and-renaming.md`](references/testing-and-renaming.md) — synthetic-entry
  end-to-end test, timing, and renaming a metric.
- [`assets/`](assets/) — `metric-config.json`, `policy-absence.json`,
  `policy-threshold.json`.
