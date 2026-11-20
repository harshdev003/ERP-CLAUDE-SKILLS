---
name: update-kubernetes
description: >
  Walk the monthly "Update Kubernetes cluster" ENGBACK card for Aura's GKE clusters (dev
  aura-dev-cluster-a, prod aura-prod-cluster-a): read each cluster's control plane and node pool
  versions, channel, maintenance window and deprecation insights; plan the upgrade one minor at a
  time, dev, then build (Spinnaker), then prod; run pre-flight workload checks; hand the upgrade commands to the AuraInfra
  code owner, who runs them with Jerome's go; then verify nodes, Deployments, restarts, Jobs and CronJobs, and close
  the card. Read-only itself: it never upgrades a cluster. Trigger on "update kubernetes", "upgrade the
  GKE cluster", "run the kubernetes update card", "monthly kubernetes upgrade", "is the cluster on the
  latest GKE version", "upgrade k8s", or "check the GKE upgrade". Do NOT use to bump service image tags
  (`eng:bump-k8s-images`), to resize pods (`eng:audit-pod-right-sizing`), or for the Node.js runtime
  or node packages cards (`eng:update-nodejs`, `eng:update-node-packages`), which run before this one.
metadata:
  version: "1.3.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/GCP        # the GCP_KEY service account, projects
    - Tools/Linear     # ENGBACK card conventions
---

# update-kubernetes

## Goal

Keep Aura's three GKE clusters (dev, build and prod) on a current Kubernetes version without breaking a workload. Each month,
check the clusters, upgrade when one is due (dev, then build, then prod), verify, and close the card.

The skill **plans and verifies**. The upgrade itself is a prod infra change, so the AuraInfra code owner
runs it, with Jerome's go. Never run an upgrade command from this
skill, and never on someone else's behalf.

Cluster facts, how upgrades happen on the REGULAR channel, rollback limits, and what has bitten us:
[`references/clusters.md`](references/clusters.md). Read it once before the first step.

## When it runs

Card 12 of the monthly pass (`eng:setup-monthly-tasks`), titled `Update Kubernetes cluster` and
created from the ENGBACK template of the same name. It is blocked by the node packages card: upgrade
only once the services are redeployed on the new Node runtime and packages.

## Prerequisites

- `GCP_KEY` (the service account) able to read all three clusters (`container.clusters.get`) and list GKE
  diagnosis insights in both projects. A missing permission is a FAIL that names the credential: report the exact role,
  project and command. Never fall back to a personal login.
- `kubectl` with the `gke_<project>_us-central1-c_<cluster>` contexts in your kubeconfig, for the
  workload checks. Those run as the person's gcloud login (kubectl has no service-account path here),
  and only get and list.

## Step 1 — Plan (read-only)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-kubernetes/scripts/plan_upgrade.py --cluster all
```

For each cluster it prints the control plane and pool versions, the target and a status:

| Status | Meaning |
|---|---|
| `CURRENT` | already on the newest version its channel offers: nothing to upgrade, go to Step 4's checks only if the window upgraded it this month |
| `UPGRADE` | a newer version is offered; the commands follow |
| `BLOCKED` | a blocker below; no commands are shown |
| `UNVERIFIED` | a read failed or came back empty: report it, never treat it as current |

The target is the newest version on the cluster's channel unless you pass `--target`. Rules the
plan enforces:

- **One minor at a time.** A target more than one minor ahead is refused, and the next minor's newest
  version is proposed instead.
- **Dev, then build, then prod.** Each cluster's target may not be ahead of the control plane of the one
  before it: build waits for dev, prod waits for build. The build cluster runs Spinnaker, so its plan
  carries a note about the release freeze and the deploy check.
- **Blockers:** any GKE deprecation insight (an API the target removes is still in use), and a node pool
  not on `COS_CONTAINERD`.

On REGULAR, GKE upgrades on its own in prod's maintenance window (Wednesdays 07:00 UTC). If the plan
says `UPGRADE` and nothing is urgent, letting the window do it is fine: record that on the card and
verify after Wednesday. Pull forward only when there's a reason (a security patch, a version leaving the
channel), and say which.

## Step 2 — Pre-flight

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-kubernetes/scripts/check_cluster.py --cluster dev --phase pre
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-kubernetes/scripts/check_cluster.py --cluster prod --phase pre
```

`ALERT` lines block the upgrade until resolved: a PodDisruptionBudget already below its budget (an
unhealthy workload), a CronJob not on `batch/v1`. `NOTE` lines are for a person to judge:

- **PDBs allowing 0 disruptions,** usually one replica with `minAvailable: 1` (all of dev's services). GKE
  honours a PDB for up to an hour per node during an upgrade, then evicts anyway. Don't wait: once each
  surge node is Ready, `kubectl rollout restart` those Deployments so the new pod starts on the new node
  first, with no downtime (it cut a 15-min stall on dev).
- **A BackendConfig with no Cloud Armor policy.**
- **The sealed-secrets controller image:** check it supports the target.

Exit 2 is UNVERIFIED.

## Step 3 — Hand the upgrade to the code owner

Send the AuraInfra code owner one message per cluster, in order (**dev, build, prod**), with:

- the plan's commands, exactly as printed (control plane, then each node pool), and the target;
- the pre-flight result;
- the node-pool order from [`references/clusters.md`](references/clusters.md) → *How upgrades actually
  happen*: on dev and prod, surge `e2-pool` and `n2d-pool` one at a time (on a stockout, empty that pool
  and upgrade it empty); on build, upgrade the empty `default-pool`, then surge `n2d-pool`;
  rollout-restart any PDB-guarded Deployment as each new node comes up;
- the freeze length, from the timings table there (about 1.5 hours for all three);
- the go Jerome needs to give, as paste-ready text.

Do not send build's commands until dev has passed Step 4, nor prod's until build has. For build, the
message also says: freeze releases (no Spinnaker deploys) while its node pools roll, because spin-redis
has no volume and a restart loses in-flight executions. The commands run as the `GCP_KEY` service
account through `gcloud_sa.py`; if it lacks `container.clusters.update`, the code owner reports the
permission. It never switches to a personal login.

## Step 4 — Verify

After each cluster's upgrade (or after the window upgraded it), with the target and the time it began:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-kubernetes/scripts/check_cluster.py --cluster dev --phase post \
  --expect <target> --since <upgrade start, UTC ISO>
```

It checks that every node's kubelet is on the target, every Deployment in the app namespaces
(`aura-services` and `aura-ai`, plus `aura-data` and `aura-web` on prod, `spinnaker` on build) is available, and that no
container restarted and no Job failed since the start. CronJobs not scheduled since the start are a NOTE (a weekly job legitimately hasn't fired).
Also confirm the ingress 5xx and latency alerts and the Cloud Armor "policy detached" alert stayed
quiet in #alerts-ai. Then re-run Step 1: the cluster should be `CURRENT`.

**On build, one more check:** deploy one release to dev through Spinnaker and confirm it lands, then
lift the release freeze. Only then hand over prod.

Any `ALERT` stops the rollout: don't hand over the next cluster. Report it to the code owner with the lines (names and
counts only).

## Step 5 — Close the card

- Tick each checklist item as it completes.
- Rename the card to `Update Kubernetes to <version>`.
- If the window did the upgrade, say so in a comment with the verify result.
- Move it to Done.

## Output rules

Counts, versions and workload names only. No IPs, secrets or values: nothing this skill reads holds
them, and the commands print nothing beyond versions.
