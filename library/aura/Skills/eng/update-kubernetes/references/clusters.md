# Aura's GKE clusters

Read live on 2026-11-03 (ENG-1741); versions as of the 1.37 upgrade (ENGBACK-2196), pools as of AuraInfra#177 (2026-11-04). `plan_upgrade.py` re-reads every value below on each
run; this page is for orientation and for what a read cannot tell you.

| | dev | build | prod |
|---|---|---|---|
| Cluster | `aura-dev-cluster-a` | `aura-build-cluster` | `aura-prod-cluster-a` |
| Project | `aura-dev` | `aura-build` | `firebase-auratech16` |
| Location | `us-central1-c` (zonal) | `us-central1-c` (zonal) | `us-central1-c` (zonal) |
| What runs there | the dev services | **Spinnaker 1.32.3** (namespace `spinnaker`), which deploys every release to dev and prod | the prod services |
| Release channel | REGULAR | REGULAR | REGULAR |
| Control plane (2026-11-04) | 1.37.1-gke.1552000 | 1.37.1-gke.1552000 | 1.37.1-gke.1552000 |
| Node pools | `e2-pool` (e2-standard-16) and `n2d-pool` (n2d-standard-16), each autoscaling 1–10, both always on | `n2d-pool` (n2d-standard-4, autoscaling 1–2) carries Spinnaker; `default-pool` (n1-standard-4, autoscaling 0–2) is the fallback and holds no node | `e2-pool` (e2-standard-16) and `n2d-pool` (n2d-standard-16), each autoscaling 1–10, both always on |
| Maintenance window | every Wednesday 07:00 UTC | every Wednesday 07:00 UTC | every Wednesday 07:00 UTC for 24 h (`FREQ=WEEKLY;BYDAY=WE`), no exclusions |

Every pool is COS_CONTAINERD with SURGE (maxSurge 1, maxUnavailable 0). On dev and prod, two machine
families are always on, so a stockout of one can't block scale-up or an upgrade surge: the autoscaler
grows the other. The AuraInfra Makefile targets that rebuild them are `create-e2-node-pool`,
`create-n2d-node-pool`, `create-build-n2d-node-pool` and `create-build-node-pool` (AuraInfra#176,
AuraInfra#177). Every pool is created with `--num-nodes 0` and then resized to 1, because a create that
asks for nodes hangs, and locks the cluster, while the zone is stocked out.

These are all the clusters (ENGBACK-2196 found the build cluster missing here). The LiveKit voice agent
runs on LiveKit Cloud, and `aura-main` has none.

**Upgrade order: dev, then build, then prod.** Build is upgraded after dev and before prod because a
broken Spinnaker stops every release, including a prod rollback.
- **Release freeze while its node pools roll.** Spinnaker's pods are drained and rescheduled, so no
  deploy starts until every pool is done.
- **Check after its upgrade that Spinnaker still deploys:** deploy one release to dev through Spinnaker
  and confirm it reaches dev, before the prod upgrade.

Reading the build cluster's GKE deprecation insights needs the `GCP_KEY` service account to have
Recommender read access on `aura-build`. Without it, the build plan is UNVERIFIED, and prod is blocked
behind it. On `aura-build` the service account also needs `iam.serviceAccountUser` on the project's
default compute service account to create a node pool. It has no in-cluster write or log read there, so
pods are moved by resizing a pool, not by `kubectl drain`.

## How upgrades actually happen

On REGULAR, GKE upgrades the control plane and then the node pools by itself, inside the maintenance
window. So the monthly card is mostly **check and verify**: confirm the clusters are on a current
version, and pull an upgrade forward (by hand, dev, then build, then prod) only when one is due and shouldn't wait for
Wednesday, for example a security patch or a version leaving the channel.

A manual upgrade is the control plane first (`--master`), then each node pool, with the default surge
(not blue-green). Dev first, verified, then build (with a release freeze and a Spinnaker deploy check),
then prod.

**Dev and prod: each pool with the surge, one pool at a time** (about 3.5 min a node on prod). The other
pool stays up and absorbs the drained pods. If a surge fails with
`ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS`, that machine family is stocked out: empty that pool onto the
other one and upgrade it empty (the recipe below), then set its autoscaling back to min 1 and resize it
to 1 (the autoscaler doesn't fill an empty pool up to its minimum on its own).

**Build: `default-pool` empty, then `n2d-pool` with the surge.** `default-pool` holds no node, so its
upgrade takes under a minute. `n2d-pool`'s surge restarts Spinnaker's pods, hence the release freeze.

**Emptying a pool and upgrading it empty.** Drain its nodes onto the other pool (`kubectl cordon` +
`drain` where the person has in-cluster write; on build the resize does the drain), then set
autoscaling to min 0 and resize to 0, then upgrade the empty pool:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-kubernetes/scripts/gcloud_sa.py container clusters update <cluster> --node-pool <pool> --enable-autoscaling --min-nodes 0 --max-nodes <max> --zone us-central1-c --project <project> --quiet
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-kubernetes/scripts/gcloud_sa.py container clusters resize <cluster> --node-pool <pool> --num-nodes 0 --zone us-central1-c --project <project> --quiet
python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-kubernetes/scripts/gcloud_sa.py container clusters upgrade <cluster> --node-pool <pool> --cluster-version <target> --zone us-central1-c --project <project> --quiet
```

**A 0-disruption PDB stalls the drain** for up to an hour per node. Dev now runs 2 replicas
(ENGBACK-2247), so pre-flight should report none. If it reports some anyway: once each surge node is
Ready, run `kubectl -n aura-services rollout restart deployment/<name>` for each one on the old node.
The new pod starts on the new node before the old one stops, so there is no downtime, and the drain
carries on.

### How long it takes

The 1.36.4 → 1.37.1 upgrade on 2026-11-03 (ENGBACK-2196), for sizing the release freeze:

| Step | dev | build | prod |
|---|---|---|---|
| Control plane | 9 min | 9 min | 9 min |
| Fallback pool: drain, min 0, resize 0, upgrade empty | about 5 min | about 4 min (with the Spinnaker move) | 3.5 min |
| Main pool, surge | about 18 min, including a 15-min PDB stall | none (new pool created on the target, about 1.5 min) | 17 min for 5 nodes, no stall (prod runs ≥2 replicas) |
| Post-checks | 1 min | — | 4 min (model replicas warm) |
| **Total** | **about 33 min** | **about 20 min** | **about 34 min** |

The freeze for all three ran 23:00 → 00:32 UTC, about 1.5 hours. Prod's load balancer returned 4 × 5xx
out of 68,668 requests during its upgrade, and one transient GKE ingress sync alert fired (a GCP API 503
during NEG churn, not certs). Expect both; neither needs action.

**Rollback:** the control plane cannot be downgraded. Node pools can be downgraded within the version
skew, or recreated at the old version. The real safety is the dev, build, prod order and the Wednesday
window.

## What has bitten us

From the 1.37 upgrade (ENGBACK-2196):

- **Surge stockout on n2-standard-16:** the dev `standard-pool` surge failed with
  `ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS` in `us-central1-c`. The same stockout at an earlier
  upgrade left a stale 1.36.2 node in both dev and prod while the pool reported 1.36.4. That's why dev
  and prod now keep two machine families always on (`e2-pool` and `n2d-pool`, AuraInfra#177), and the
  n2 pool is gone.
- **PDB stall:** dev's single-replica Deployments with `minAvailable: 1` held the old n2d node for
  15 minutes (GKE waits up to an hour per node). `rollout restart` unblocked it (above); the dev PDB fix
  is ENGBACK-2247.
- **spin-redis has no volume:** restarting it drops Spinnaker's in-flight executions and recent
  execution history. Pipeline definitions are safe in GCS. So move Spinnaker only under a release freeze.
- **`gcloud container node-pools create`** needs `--no-shielded-secure-boot` alongside
  `--shielded-integrity-monitoring`.

The rest came from what runs on the clusters, not Kubernetes:

- **Base-image bumps moving pinned packages:** an Alpine base-image bump moved CoachService's pinned apk
  `ffmpeg` (PROD-2270).
- **Stale image tags in AuraInfra:** prod deploys by digest, so an old tag left in a manifest is a rollback
  trap.
- **Never `kubectl apply -R` an AuraInfra service directory:** it sweeps in the `dev/` and `prod/` sealed
  secrets.
- **Cloud Armor attaches through BackendConfig annotations:** after any ingress change, check the
  policies are still attached (the "policy detached" alert).
