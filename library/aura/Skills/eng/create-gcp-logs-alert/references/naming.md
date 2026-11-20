# Naming rules

The prod log metrics and alert policies in `firebase-auratech16` follow one convention.
A new one must match it, so the list stays scannable and a duplicate is easy to spot.

## Metric name — PascalCase event name, no service prefix

The name is the **event the log line records**, in PascalCase. The service is implied by
the filter, not spelled into the name.

| ✅ | ❌ |
|---|---|
| `CouldNotValidate`, `ModelLoaded`, `NeuralModelTrained` | `user_claim_unresolved` (snake_case) |
| `UserClaimUnresolved`, `RequestBodyParseFailed` | `FirebaseAuthUserClaimUnresolved` (service prefix) |
| `GKEScaleUpFailed`, `ActivationModelLoaded` | `userClaimUnresolved` (camelCase) |

Name the event, not the alert: `NeuralModelTrained` counts trainings; the absence policy
on it is what reads "Not Trained". A prefix is allowed only when it names the *thing*, not
the service that logs it (`ActivationModelLoaded` vs `ActivationCategoryModelLoaded` are
two different models; `GKE…` events come from the platform, not one service).

A few legacy metrics break the rule (`live-event-list`, `UserWeb-IncomingRequestsCount`).
Don't copy them. A wrongly named metric can't be renamed in place — see
[`testing-and-renaming.md`](testing-and-renaming.md) → "Renaming a metric".

## Policy displayName — `<Event in words> Check on <Service> Policy`

| Policy | Event in words | Service |
|---|---|---|
| `Service Could Not Validate Email Check on Email Service Policy` | Service Could Not Validate Email | Email Service |
| `Neural Model Not Trained Check on Neural Model Trainer Policy` | Neural Model Not Trained | Neural Model Trainer |
| `RTDB REST Read Volume Check on Firebase Policy` | RTDB REST Read Volume | Firebase |
| `User Claim Unresolved Check on Firebase Auth Policy` | User Claim Unresolved | Firebase Auth |
| `Node Scale-Up Failed Check on GKE Policy` | Node Scale-Up Failed | GKE |

- Title Case, words not identifiers (`Firebase Auth`, not `authUserClaim`).
- For an absence alert, state the failure (`Not Trained`), not the event.
- `<Service>` is the human name of what is watched — a service (`Email Service`), a
  platform (`GKE`, `Firebase`), or a model/job (`Activation Model Loader`).
- Condition `displayName`s are free text describing the trigger
  (`More than 5 unresolved claims in a day`, `Any ambiguous claim`).

## `userLabels.context` — the area the alert belongs to

Set `userLabels: {"context": "<area>"}` when the alert belongs to an area that already
groups policies. Use the existing value, never a new spelling:

| Area | Value | Examples |
|---|---|---|
| Security (scraping, probes, auth anomalies) | `security` | `RTDB … Check on Firebase Policy`, `User Claim Unresolved Check on Firebase Auth Policy` |
| Redis | `redis` | `Cloud Redis - …` |

List the values in use before choosing:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-gcp-logs-alert/scripts/gcloud_sa.py monitoring policies list \
  --project=firebase-auratech16 --format="value(displayName, userLabels)"
```

Omit `userLabels` entirely when no area fits — an empty `{}` is rejected on create.

## The naming check (Step 2)

Before proposing the spec, confirm all of:

- [ ] Metric name is PascalCase, an event, no service prefix, no `_` or `-`.
- [ ] Metric name is not taken: `logging metrics list --format="value(name)"`, compared
      case-insensitively (catches `UserClaimUnresolved` vs `user_claim_unresolved`).
- [ ] Policy displayName matches `<Event in words> Check on <Service> Policy`, and no
      existing policy has the same displayName or watches the same metric.
- [ ] `userLabels.context` set to an existing value when the alert belongs to that area.
- [ ] `alertStrategy` is `autoClose 604800s` + `notificationPrompts ["OPENED"]`.
- [ ] `combiner OR`, `enabled true`, the three default notification channels.
