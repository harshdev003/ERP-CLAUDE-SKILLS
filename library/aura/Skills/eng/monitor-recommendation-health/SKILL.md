---
name: monitor-recommendation-health
description: >
  Read-only health check for Aura's recommendation system on the prod GKE cluster. Runs three
  independent checks and prints a PASS/FLAG/COLLAPSE digest: per-model /predict failure rates,
  grouping ContentService's model-call-failed logs by model name and flagging any model over a
  threshold; a live neural-model collapse check that replays /predict against the live pod for
  a few users and measures the score spread, so a model that has silently collapsed to a
  constant score is caught; and the latest trainer quality-gate result, read from the most
  recent audit line. Read-only — it queries logs, replays predictions and reads a file, never
  writing to the cluster. Trigger on "check recommendation health", "is the recommender
  healthy", "are recs broken", "check /predict failures", "is the neural model collapsed",
  "did the model collapse", "check the model quality gate", "per-model predict failure rate",
  or "recommendation system health check".

metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/GCP     # SA name, project, GCP_KEY env-var, IAM, log schema (shared with investigate-gcp-logs)
    - Tools/Kubernetes  # kubectl port-forward to the live /predict pod (check 2, neural-collapse)
---

# monitor-recommendation-health

A standing, read-only health check for Aura's recommendation system. It complements the trainer's *publish-time* quality gate (4U-engine #150 / `train_2.1.py`) with an *any-time* check an engineer or an alert can run against prod. Three independent checks; run one or all.

## Goal

Aura's neural recommender **silently collapsed to a near-constant score for every user for years** because scheduled training auto-published with no quality check (the motivation for PROD-3714). This skill makes that class of failure observable on demand:

- **Are `/predict` calls failing, and for which model?** (ContentService logs)
- **Is the *live* model actually ranking, or has it collapsed?** (replay `/predict`)
- **Did the most recent training run pass the quality gate?** (trainer logs)

## Inputs

Nothing required — sensible prod defaults are baked in. Optional narrowing:

- `--check {failures,neural,gate,all}` — which check to run (default `all`).
- `--failures-since 1h` / `--gate-since 7d` — time windows.
- `--users <uid1,uid2,…>` — real Firebase UIDs for the neural check (recommended; see below).
- `--scores-file <path>` — offline neural check (no cluster access needed).
- `--json` — machine-readable output for an alert/cron.

## Workflow

Run the CLI (one check, or `all`):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-recommendation-health/scripts/check.py --check all
```

Each check prints a fixed-shape digest ending in a `VERDICT:` line, and the run ends with a one-line `SUMMARY:`. **Exit code** encodes the worst verdict for alert wiring: `0` all clear, `1` a FLAG / NO_DATA / check-error, `2` a COLLAPSE / gate FAIL.

### Check 1 — per-model /predict failure rates (GCP logs)

Queries `content-service` (namespace `aura-services`) for `jsonPayload.message="Model call failed (status 500)"` over the window, groups by **`jsonPayload.modelName`**, and flags any model at or above `--failure-threshold` (default 10, env `REC_HEALTH_FAILURE_THRESHOLD`).

- As of **ContentService 2.294.2** these WARN logs carry `jsonPayload.modelName`; **2.294.1** logs don't — those are counted separately as "NO modelName" so a mixed-version window is never silently mis-attributed.
- `--status` (default 500) picks the 5xx code in the message; other 5xx (502/503/504) are sibling messages — re-run with `--status 503` to see them.

### Check 2 — live neural-model collapse/health (replay `/predict`)

Reaches the live `neural-model` service (namespace `aura-ai`, port 3001) via `kubectl port-forward`, POSTs `/predict` (`{"user":{"id":"<uid>"}}`) for a few users, and measures the score spread returned over the whole track catalog:

- **median distinct scores/user > `--min-distinct`** (default 1000, env `NEURAL_MIN_DISTINCT`)
- **median per-user std > `--min-std`** (default 0.05, env `NEURAL_MIN_STD`)

These mirror the trainer's gate thresholds. Reference points: a **healthy** ranker scores ~13k–41k distinct values/user with std ~0.6; a **collapse** is ~1 distinct with std ~0 — orders of magnitude of margin.

**About sample users:** pass real Firebase UIDs via `--users` for the strongest signal. If omitted, the check probes with unknown ids, which the model maps to its `FALLBACK_USER` — this still measures the ranking spread and **catches a global collapse** (the historical failure mode), but real UIDs additionally catch per-user degeneracies.

**Offline / no-cluster path:** `--scores-file <path>` where the file is JSON `{uid: [scores]}` or `{uid: [{"score":…,"id":…}]}`. Use this when you don't have kubectl to the prod cluster — e.g. after `kubectl cp`-ing `/mnt/nfs/neural-model/2.1/neural_model.h5` out and running `predict.py` locally, dump each user's scores to a file and feed it here. Pure stdlib; runs anywhere.

### Check 3 — latest trainer quality-gate result (GCP logs)

Queries `neural-model-trainer` (namespace `aura-ai`) for the audit line `jsonPayload.message:"Model quality gate"`, takes the **most recent** one, reads its structured `jsonPayload.modelQuality` object (`medianDistinctScores` / `medianUserScoreStd` / `minEmbeddingStd` / `sampledUsers` / `catalogTracks`), and reports the metrics + PASS/FAIL against the same thresholds.

Because the trainer **refuses to publish on a FAIL** (it exits non-zero and leaves the previous artifact in place), a FAIL here usually means the last run did *not* ship — cross-check what's actually live with the neural check.

## PII guardrail

**The recommendation logs dump full user profiles inline** — the neural-model service logs "Returning recommendations" with the user's gender/age-group/persona/locale/topic preferences, and ContentService request params carry profile data. This skill enforces the same guardrail as `eng:investigate-gcp-logs`: it **never echoes a raw `jsonPayload.message` or any profile field**. Only the specific extracted signals surface — model names + counts (check 1), aggregate score statistics (check 2 — never the raw score arrays or track ids), and the numeric `modelQuality` object (check 3). The `/predict` *response* the neural check reads contains only `{score, id}` pairs, no profile.

## Setup (one-time, per user)

**GCP auth (checks 1 & 3):** identical to `eng:investigate-gcp-logs` — the shared `scripts/gcp.py` resolves `GCP_KEY` (inline SA JSON in `~/.claude/settings.json`) → `GOOGLE_APPLICATION_CREDENTIALS` → ADC. The SA needs `roles/logging.viewer` on `firebase-auratech16`. Full walkthrough: [`../investigate-gcp-logs/references/auth-setup.md`](../investigate-gcp-logs/references/auth-setup.md).

**Cluster access (check 2, live mode):** `kubectl` on `PATH` with a context for `aura-prod-cluster-a` (`kubectl config current-context`, or `make ENVIRONMENT=prod switch` in AuraInfra). Pass `--context <name>` to pin one. No kubectl? Use `--scores-file` instead.

**Python deps:** `python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/monitor-recommendation-health/scripts/requirements.txt` (just `google-cloud-logging`; the neural check is stdlib-only). Use `python3 -m pip`, not bare `pip`.

## Output

Three digests (one per check run) plus a `SUMMARY:` line. `--json` emits `{"results": [ … ]}` with one object per check (`verdict` is `OK`/`FLAG` | `HEALTHY`/`COLLAPSED` | `PASS`/`FAIL`/`NO_DATA` | `ERROR`).

## Edge cases

- **No failures in the window** → check 1 prints "No per-model failures ✅", verdict OK.
- **Only pre-2.294.2 logs** → all failures land in the "NO modelName" bucket; the digest says so — broaden nothing, it's a version artifact.
- **Gate line not found** → verdict `NO_DATA` (trainer hasn't run in the window, or the gated trainer 4U-engine #150 hasn't shipped to prod yet). Broaden `--gate-since` (e.g. 90d) to confirm.
- **kubectl missing / wrong context / pod not ready** → the neural check reports the specific failure with the `--scores-file` fallback, and (under `--check all`) the other checks still run.
- **Auth fails** → checks 1 & 3 print the specific remediation from `gcp.py` (no `GCP_KEY`, key parse error, expired ADC) and return exit 3.
- **All probe users identical** → expected when `--users` is omitted (all map to `FALLBACK_USER`); pass real UIDs to differentiate.
- **Re-running** → safe; every check is read-only, no idempotency concern.

## How this maps to PROD-3714

PROD-3714 is the recommendation-system quality-gate work. **Option #3** is the standing, out-of-band health check (this skill) that sits alongside the in-band publish-time gate the trainer got in 4U-engine #150 (`train_2.1.py`): the trainer blocks a *collapsed model from publishing*; this skill lets anyone verify, at any time, that the *live* model still ranks, that `/predict` isn't failing per-model, and that the last training run passed the gate.

## References

- [`references/checks.md`](references/checks.md) — the exact log schemas, field paths, thresholds, `/predict` request/response shape, and the PII rationale each check relies on.
- Canonical auth ladder + Cloud Logging wrapper: [`../investigate-gcp-logs/scripts/gcp.py`](../investigate-gcp-logs/scripts/gcp.py) (this skill ships a byte-identical copy per the repo's self-containment rule).
