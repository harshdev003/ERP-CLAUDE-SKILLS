# monitor-recommendation-health — check schemas & rationale

What `scripts/check.py` relies on for each of the three checks. All three are **read-only**.

## Prod topology (fixed defaults)

| Thing | Value | Source |
|---|---|---|
| Cluster | `aura-prod-cluster-a` | AuraInfra |
| Project | `firebase-auratech16` | derived from `GCP_KEY["project_id"]` |
| ContentService | namespace `aura-services`, container `content-service` | `k8s/apps/services/content-service/deployment.yaml` |
| neural-model service | namespace `aura-ai`, service `neural-model`, port `3001`, selector `app=neural-model` | `k8s/apps/ai/neural-model/service.yaml` |
| neural-model-trainer | namespace `aura-ai`, container `neural-model-trainer` (cronjob) | `k8s/apps/ai/neural-model/cronjob.yaml` |
| model artifact / NFS | `/mnt/nfs/neural-model/2.1/neural_model.h5` (NFS mounted at `/mnt/nfs`, `TRAINER_VERSION=2.1`) | deployment env |

## Check 1 — per-model /predict failure rates

**Emitter:** `ContentService/src/recommenders/reel/commands/httpCommand.js`:

```js
if (response && response.status >= 400) {
  logger.warn(`Model call failed (status ${response.status})`, {
    ...meta,            // { component: 'httpCommand', method: 'doExecute', modelName }
    error: response.error,
  });
  return undefined;     // don't cache the failure (PROD-3707)
}
```

- ContentService is a Node service → **Format A**: `meta` fields land as top-level `jsonPayload` keys. So `jsonPayload.modelName` is a flat field and `jsonPayload.message` is exactly `Model call failed (status 500)` (or 502/503/504).
- Severity is **WARNING** (`logger.warn`) — do NOT restrict the query to `severity>=ERROR`.
- `modelName` values seen in prod: `neural-model`, `user-preferences-model`, `program-model`, `serie-model`, `activated-new-model`, …
- **Version note:** `modelName` was added to this log in **2.294.2**. **2.294.1** lines have no `modelName`; the check counts them in a separate "NO modelName" bucket and surfaces the `content-service` versions seen (from `jsonPayload.serviceContext.version`) so a mixed-version window is transparent.

**Filter:**

```
resource.type="k8s_container"
AND resource.labels.cluster_name="aura-prod-cluster-a"
AND resource.labels.namespace_name="aura-services"
AND resource.labels.container_name="content-service"
AND jsonPayload.message="Model call failed (status 500)"
AND timestamp>="<cutoff>"
```

Threshold: per-model failure **count** ≥ `REC_HEALTH_FAILURE_THRESHOLD` (default 10) → FLAG.

## Check 2 — live neural-model collapse/health

**Endpoint:** `4U-engine/models/neural-model/server.py` → `POST /predict`, body `{"user": {"id": "<uid>", "habitPreferences": {…optional}}}`, optional `x-request-id` header. **Response:** a JSON array `[{"score": <float>, "id": "<track_id>"}, …]` — the *entire* track catalog scored and sorted descending (`predict.py::Predictor.predict`). Unknown user ids fall back to `FALLBACK_USER`.

That response is exactly the per-user score distribution the collapse check needs. For each user:

- **distinct** = `len({round(score, 4) …})` — mirrors the trainer's `np.unique(np.round(preds, 4)).size`.
- **std** = population std of the scores — mirrors the trainer's `preds.std()`.

Aggregate across users with the **median**, and compare:

| Metric | Healthy (live v2.1) | Collapse | Threshold (default) | Env |
|---|---|---|---|---|
| median distinct scores/user | ~13k–41k (probe run: ~9.7k) | ~1 | `> 1000` | `NEURAL_MIN_DISTINCT` |
| median per-user std | ~0.6 (probe run: ~0.27) | ~0 | `> 0.05` | `NEURAL_MIN_STD` |

These mirror the trainer's `QUALITY_MIN_DISTINCT` / `QUALITY_MIN_STD`. There is orders-of-magnitude of margin between healthy and collapse, so the exact sample of users barely matters for the *global* collapse signal.

**How the check reaches the pod:** `kubectl port-forward svc/neural-model <local>:3001 -n aura-ai`, then POSTs `/predict` over the forwarded port with stdlib `urllib` (no in-pod `curl` dependency, no extra pip dep). The forward is torn down in a `finally`. `/predict` is a pure inference call — nothing is mutated.

**Offline alternative (`--scores-file`):** JSON `{uid: [scores]}` or `{uid: [{"score","id"}]}`. Matches the task's "load `/mnt/nfs/neural-model/2.1/neural_model.h5` in a throwaway pod" path — score users however you like (e.g. `kubectl cp` the artifact + run `predict.py`), dump to a file, feed it here for the same verdict math with no live-cluster dependency.

## Check 3 — latest trainer quality-gate result

**Emitter:** `4U-engine/models/neural-model-trainer/train_2.1.py` (the gate, PROD-3714 / 4U-engine #150):

```py
quality = {
    'medianDistinctScores': float(np.median(distinct_counts)),
    'medianUserScoreStd':   float(np.median(user_stds)),
    'minEmbeddingStd':      float(min(emb_stds)) if emb_stds else 0.0,
    'sampledUsers':         int(len(sample_users)),
    'catalogTracks':        int(M),
}
logger.audit('Model quality gate: {}'.format(quality),
             context.meta({"component": 'train', "method": 'qualityGate',
                           "modelQuality": quality}))
if (quality['medianDistinctScores'] < QUALITY_MIN_DISTINCT
        or quality['medianUserScoreStd'] < QUALITY_MIN_STD
        or quality['minEmbeddingStd'] < QUALITY_MIN_EMB_STD):
    logger.error('QUALITY GATE FAILED — model looks COLLAPSED; NOT publishing. …')
    raise SystemExit('neural-model quality gate failed — collapsed model not published')
```

- The trainer's `utils/logger.py` emits **pure JSON** to stdout (`json.dumps({message, **meta, severity, serviceContext})`), so fluentbit parses it into `jsonPayload` — **Format A**, `jsonPayload.modelQuality` is a real nested object (not stringified-in-message). This is unlike the neural-model *service*, whose Python logging prepends `LEVEL - ` (Format B).
- `logger.audit` maps to Stackdriver severity `DEFAULT`, so **don't filter on severity** — filter on the message substring.
- One gate line per training run.

**Filter:**

```
resource.type="k8s_container"
AND resource.labels.cluster_name="aura-prod-cluster-a"
AND resource.labels.namespace_name="aura-ai"
AND resource.labels.container_name="neural-model-trainer"
AND jsonPayload.message:"Model quality gate"
AND timestamp>="<cutoff>"
```

`order_by="timestamp desc"`, take the first entry with a `modelQuality` object. PASS iff `medianDistinctScores ≥ QUALITY_MIN_DISTINCT (1000)` AND `medianUserScoreStd ≥ QUALITY_MIN_STD (0.05)` AND `minEmbeddingStd ≥ QUALITY_MIN_EMB_STD (1e-6)`.

**FAIL semantics:** the trainer exits non-zero on a FAIL and leaves the previous artifact, so a FAIL line means that run did *not* publish. `NO_DATA` (no gate line in the window) is expected until the gated trainer (4U-engine #150) has actually run in prod — as of this skill's authoring the trainer TF2 migration is mid-flight (PROD-3707), so a fresh prod query legitimately returns NO_DATA.

## PII rationale

The neural-model service logs "Returning recommendations" with the **full user profile** (gender, age group, persona, locale, topic preferences, recommendation ids), and ContentService request params carry profile data too. Same guardrail as `eng:investigate-gcp-logs`: never echo a raw `jsonPayload.message` or profile field. This skill only ever surfaces:

- check 1 — `modelName` + counts + `serviceContext.version` (no `error` body, no message beyond the fixed string);
- check 2 — per-user **aggregate** stats (distinct count, std) — never the raw score arrays or track ids; the `/predict` response has no profile anyway;
- check 3 — the numeric `modelQuality` object only.
