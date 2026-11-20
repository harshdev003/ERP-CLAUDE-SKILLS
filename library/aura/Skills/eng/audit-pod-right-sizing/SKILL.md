---
name: audit-pod-right-sizing
description: >
  Audits every namespace and workload in a GKE cluster for mis-sized CPU/memory requests and
  limits, and produces a ranked right-sizing report. Enumerates all Deployments, StatefulSets
  and DaemonSets, samples actual usage from `kubectl top` a few times to catch bursts, then
  flags two directions per resource — CPU and memory are classified independently, so one
  container can be over-provisioned on CPU and under-provisioned on memory at once. Suggests a
  right-sized value with headroom over observed peak and rolls up a cluster-wide reclaimable
  total. Read-only: it recommends, never resizes. Trigger on "right-size the cluster",
  "right-size pods", "audit pod resources", "which pods are over-provisioned", "find
  over-requested memory/CPU", "how much cluster memory can we reclaim", "cluster resource
  audit", or "are any pods at risk of OOM". Do NOT use to actually apply a resize (a separate
  reviewed step), for autoscaler or HPA tuning, or for a one-off "what is pod X using right
  now" lookup.

metadata:
  version: "3.1.1"
knowledge:
  reads:
    - Guide/Index                # baseline routing aid (universal)
    - Tools/Kubernetes           # cluster context, GitOps layout, Spinnaker auto-deploy, revert-safe apply
    - Tools/GCP                  # project / cluster coordinates
---

# audit-pod-right-sizing

Generalizes the manual `aura-ai` model-pod pass from
[PROD-3717](https://linear.app/aura-health/issue/PROD-3717) — where `reel-model`,
`locale-model`, `activated-new-model` were requesting **12000Mi but using ~0.3Gi** and dropping
three deployments to 2000Mi reclaimed **~30Gi** of reserved memory — into a **reusable,
cluster-wide** audit ([PROD-3722](https://linear.app/aura-health/issue/PROD-3722)).

**Core contract:**

- **Deterministic gathering + analysis.** All the kubectl enumeration, quantity parsing,
  pod→workload mapping, over/under classification, and ranking happen in
  [`scripts/audit_pod_resources.py`](scripts/audit_pod_resources.py) — no LLM guessing about
  numbers. The model's job is to run it, read the report, and narrate the candidates.
- **Read-only, always.** The script only runs `kubectl get` and `kubectl top`. It never mutates
  the cluster. Any actual resize is a separate, human-reviewed step following
  [`references/resize-apply-guide.md`](references/resize-apply-guide.md).
- **LimitRange-aware.** A container that omits a request or limit inherits its namespace's
  `type: Container` LimitRange `defaultRequest` / `default` at admission (plus the standard
  request-defaults-to-limit copy), so it is *not* running unconstrained. The script fetches
  `kubectl get limitrange -A`, builds a per-namespace default map, and classifies + sizes against
  these **effective** values, not the bare spec — so a LimitRange-covered workload is never flagged
  "no requests set". Every value sourced from a LimitRange rather than the workload spec is marked
  `†` in the report (and carries a `*_source` field in `--json`): a reclaim on a `†` request means
  overriding an inherited default, and a `†` limit is the real burst ceiling. If LimitRanges can't
  be read, the report says so and marks itself degraded rather than silently reverting to bare-spec
  classification. In the prod cluster, `aura-services` / `aura-web` carry cpu+mem LimitRanges and
  `aura-ai` / `aura-data` carry a mem-only LimitRange.
- **Per-resource classification, never per container.** CPU and memory get their own verdict
  (`cpu_direction` / `mem_direction`, each `over` / `under` / `ok` / `no-data`). The two
  resources are routinely wrong in opposite directions on the same container — a model server
  pinning 3 vCPU it never touches while its weights creep past the memory request is the
  ordinary case. A single container-level verdict has to pick one, and whichever it picks
  deletes the other resource from the report *and from the roll-up totals*, silently: the
  summary still looks complete, just short. On the 2026-09-02 prod run the omitted CPU item was
  larger than the entire reported CPU total ([ENG-1228](https://linear.app/aura-health/issue/ENG-1228)).
  So a container may legitimately appear in **both** tables — each row names the resource it
  concerns, and the roll-up counts the containers appearing in both. `direction` survives in
  `--json` as a derived one-word summary and takes the value `mixed` when the resources
  disagree; per-resource fields are the authoritative ones.
- **A uniform reading from uniformly-aged replicas is not steady state.** `kubectl top` measures
  usage at whatever age the replicas happen to be, and a Deployment rolls its replicas together
  — so a tight spread across replicas means "equally old", not "stable". The script reports each
  replica's age, marks a workload **not characterised** when the ages are uniform (a single
  replica included — nothing to compare it against), and where ages *do* differ reports the
  oldest-vs-youngest delta as a growth signal. That one comparison is what would have caught a
  workload reading a flat 3.5–3.9Gi across eight 7-day-old replicas while actually growing
  ~2.8Gi per week of uptime.
- **Headroom over peak, not average — memory especially.** `kubectl top` is instantaneous, so
  the script samples a few times and keeps the **max per-replica** reading. Suggested memory is
  deliberately more generous than CPU (default 2.0× peak vs 1.5×): OOM kills are worse than a bit
  of waste. Suggestions are a *floor for discussion*, not a target.
- **Infra treated conservatively.** `kube-system`, `gke-managed-*`, `sealed-secrets`, monitoring,
  etc. are flagged advisory-only and kept out of the aggressive reclaim roll-up — don't starve
  the control plane.

## Prerequisites

- **`kubectl`** configured with access to the target cluster, and **metrics-server** available
  (`kubectl top nodes` should return data — GKE has it on by default).
- Confirm the context first: `kubectl config current-context` should be
  `gke_firebase-auratech16_us-central1-c_aura-prod-cluster-a` for the prod pass (or switch via
  the AuraInfra Makefile `make ENVIRONMENT=prod switch`). See Tools/Kubernetes.
- Python 3 (standard library only — no external deps).

## Inputs (all optional)

- `context` — kubectl context to audit. Defaults to current-context.
- `samples` / `interval` — how many `kubectl top` snapshots to take and the seconds between them
  (defaults 3 × 5s). Use more (e.g. `--samples 5`) for a first cluster-wide pass so a bursty
  service isn't judged on a single reading.
- Tuning knobs (sensible defaults; only change with reason): `--over-ratio` (flag when
  request/peak > 4×), `--cpu-headroom` / `--mem-headroom`, `--cpu-floor-m` / `--mem-floor-mi`,
  `--near-limit-frac`, `--min-reclaim-mem-mi` / `--min-reclaim-cpu-m`.

## Step 1 — Confirm the target cluster

Run `kubectl config current-context` and show it to the user. If it isn't the intended cluster,
stop and switch (`make ENVIRONMENT=<env> switch`) before auditing — you don't want to report on
dev when they meant prod, or vice-versa.

## Step 2 — Run the audit

```bash
python3 scripts/audit_pod_resources.py --samples 5
```

The script:
1. Enumerates all Deployments / StatefulSets / DaemonSets across every namespace and reads each
   container's requests + limits from the spec, then fetches `kubectl get limitrange -A` and
   resolves each container's **effective** requests/limits (spec → namespace LimitRange default →
   request-defaults-to-limit copy). All classification below runs on these effective values.
2. Maps running pods back to their owning workload (ReplicaSet → Deployment by stripping the
   pod-template-hash; StatefulSet / DaemonSet directly) and samples `kubectl top pods -A
   --containers` `--samples` times, keeping the **peak per replica** per container.
3. Classifies **each resource of** each container independently (against effective
   requests/limits) — CPU and memory each get their own verdict:
   - **over-provisioned** — request/peak > `--over-ratio`, with a suggested value = peak ×
     headroom (rounded up to a sane step, never below the floor), and reclaim = (request −
     suggested) × replicas. A resource under the ratio is still trimmed alongside one that
     tripped it (marked "trimmed alongside" in the reasons) — dropping those would quietly
     shrink the roll-up.
   - **under-provisioned / risky** — no request set for *that resource* (nothing on the spec
     *and* no LimitRange default), peak above request, or peak ≥ `--near-limit-frac` of the
     limit. Note this is per resource: a container with a memory request but no CPU
     reservation is flagged on CPU, which the previous container-level test (both unset)
     never saw. In the prod cluster `aura-ai` / `aura-data` carry a mem-only LimitRange, so
     expect this to surface rows earlier versions hid.
   - **no-data** — no running pods / no metrics (can't size; mostly GKE system workloads).

   A risky verdict on one resource no longer suppresses the other's analysis, so the same
   container can be listed as over-provisioned on CPU *and* risky on memory. That is the
   intended output, not a contradiction — do not "reconcile" it back to one verdict.
4. Reads each pod's start time and reports replica ages next to the peaks. Where every replica
   shares an age the row is marked ⚠️ **not characterised** — its suggestion is a lower bound
   until confirmed against a freshly-rolled replica. Where ages differ, the oldest-vs-youngest
   memory delta is reported as a growth signal with a per-day rate.

   Values inherited from a LimitRange (not the workload spec) are flagged `†` in the report and
   carry a `*_source` field (`spec` / `limitrange` / `limit`) in `--json`.
5. Ranks over-provisioned by reclaimable memory, then repeats the ranking **by reclaimable
   CPU** (a CPU-dominant container with no memory reclaim otherwise sorts below every container
   with any — which is how the biggest CPU item in the cluster stayed invisible), rolls up a
   cluster total (app namespaces), and lists infra namespaces advisory-only.

Add `--json` for a structured payload, or `--out report.md` to write the markdown to a file
(handy for attaching to the Linear card).

## Step 3 — Present the report

Surface the report to the user: lead with the cluster roll-up (total reclaimable memory/CPU),
then the top over-provisioned candidates — read **both** rankings, memory and CPU, since the
largest CPU reclaim is often a workload with no memory reclaim at all — then the risky /
under-provisioned list (no-request pods and anything near its limit — these are the ones that
can hurt, not just waste). Call out that suggestions are a floor for discussion and that memory
should keep extra margin for services with known startup/batch spikes.

Two things to say out loud rather than let the reader infer:

- **A workload in both tables is not a contradiction.** Name the resource each row concerns —
  "over-provisioned on CPU, running above its memory request" — otherwise it reads as a bug and
  invites someone to pick one.
- **A ⚠️ row is not characterised.** Uniform replica ages mean the reading cannot distinguish
  steady state from accumulation, so its suggestion is a floor and the workload needs a
  follow-up reading against a freshly-rolled replica before anyone cuts. If the report carries
  a growth signal, size off the **oldest** replica, not the cluster peak.

**Do not apply anything.** This skill's deliverable is the ranked candidate list for human
review — exactly like the PROD-3717 pass surfaced candidates before any change.

## Step 4 — (optional) hand off to an apply

If the user wants to act on a candidate, point them at
[`references/resize-apply-guide.md`](references/resize-apply-guide.md): use `kubectl set
resources` (surgical — leaves the Spinnaker-managed image untouched, so an apply can't revert a
release), dev before prod, canary high-replica services, and verify with `kubectl top` +
OOM-event checks afterward. Applying is out of scope for this skill.
