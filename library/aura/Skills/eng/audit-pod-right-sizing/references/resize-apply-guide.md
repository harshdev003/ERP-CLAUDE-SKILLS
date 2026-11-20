# Revert-safe resize guide

The audit skill is **read-only** — it never resizes anything. When a human has reviewed the
report and decided to act on a candidate, this is how to apply the change *without reverting a
release*. Source: AI Knowledge → Tools/Kubernetes, and the manual pass in
[PROD-3717](https://linear.app/aura-health/issue/PROD-3717).

## Why "revert-safe" is the whole point

Aura's prod backend is **GitOps + Spinnaker**. Images are built by CircleCI and auto-deployed
by Spinnaker's "Quick deployment" pubsub — so the **image tag running in the cluster is set by
Spinnaker, not by the manifest in git** (the committed manifest often carries a placeholder or
stale tag). That creates one specific footgun:

> If you `kubectl edit` / `kubectl apply` a full Deployment manifest to change resources, and
> that manifest's `image:` differs from what Spinnaker last deployed, the apply **rolls the
> workload back to the manifest's image** — silently reverting a live release while you thought
> you were only changing memory.

Two ways to avoid it, in order of preference:

## Path A — `kubectl set resources` (recommended, surgical)

Patches **only** the container resources subresource. Image, env, args, everything else is left
exactly as Spinnaker set it. Nothing to pin, nothing to revert.

```bash
kubectl config current-context   # confirm you are on the TARGET cluster

# requests only (what most right-sizing changes are):
kubectl set resources deployment/<name> -n <namespace> \
  --requests=cpu=<cpu>,memory=<mem>

# requests + limits together:
kubectl set resources deployment/<name> -n <namespace> \
  --requests=cpu=<cpu>,memory=<mem> --limits=cpu=<cpu>,memory=<mem>

# a single container in a multi-container pod:
kubectl set resources deployment/<name> -n <namespace> \
  --containers=<container> --requests=memory=<mem>
```

This triggers a normal rolling update (new ReplicaSet). It is fully declarative and idempotent —
re-running the same command is a no-op.

## Path B — edit the manifest (only if you must), image pinned to current-live

If a change genuinely has to go through the committed manifest (e.g. you want it in git under
`k8s/apps/<key>/<service>/<env>/`), **pin the image to what's live first** so the apply can't
revert the release:

```bash
# capture the tag Spinnaker currently has running:
kubectl get deployment/<name> -n <namespace> \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
# → put THAT exact image into the manifest before editing resources, then:
kubectl apply -f k8s/apps/<key>/<service>/<env>/<file>.yaml
```

Note this puts a concrete tag in git that Spinnaker will move past on the next release — Path A
avoids that entirely, which is why it's preferred for pure resize changes.

## Rollout discipline (both paths)

1. **Confirm context.** `kubectl config current-context` — or switch via the AuraInfra Makefile:
   `make ENVIRONMENT=dev switch` / `make ENVIRONMENT=prod switch`.
2. **Dev before prod.** Never both in one unconfirmed pass.
3. **Canary high-replica / critical services.** Change it, watch one rollout, confirm healthy +
   in-pod usage sane, then proceed. `kubectl rollout status deployment/<name> -n <namespace>`.
4. **Memory: leave headroom over peak, not average.** OOMKills are worse than a little waste.
   For services with cold-start / model-load / batch spikes, size above the report's suggestion.
5. **Verify after — twice, and know which reading you are looking at.** Re-run
   `kubectl top pods -n <namespace>` and confirm the new requests aren't being exceeded; check
   no `OOMKilled` / evictions:
   `kubectl get events -n <namespace> --field-selector reason=OOMKilling`.

   A reading taken minutes after a rollout is a **fresh-pod** reading, and it is wrong in
   opposite directions for the two resources: CPU is inflated by model loads / JIT warm-up /
   cache fills (one workload idling at 76m peaked at **1269m** while warming), while memory
   reads *low* and climbs with uptime. So a clean immediate check is not evidence the new
   memory request holds. Take a second reading days later — and if the audit flagged the
   workload ⚠️ **not characterised** (uniform replica ages) or reported a growth signal, treat
   the immediate check as provisional and diary the follow-up before closing the change out.

## Rollback

```bash
kubectl rollout undo deployment/<name> -n <namespace>          # previous revision
kubectl rollout undo deployment/<name> -n <namespace> --to-revision=<n>
```

## LimitRange-defaulted workloads (values marked † in the report)

A `†` on a request/limit in the audit means that value was **not** declared on the workload — it
was injected by the namespace's `type: Container` LimitRange (`defaultRequest` / `default`), which
the scheduler reserves exactly as if the workload had set it. Two consequences when acting on such
a candidate:

- **A reclaim is a per-workload override, not a trim.** `kubectl set resources deployment/<name>
  --requests=…` writes an *explicit* request on that one workload, which takes precedence over the
  LimitRange default — surgical and revert-safe (Path A above). Lowering the LimitRange itself
  changes the default for **every** workload in the namespace that relies on it — a much wider
  blast radius; only do that as its own reviewed change with the whole namespace in view.
- **A `†` limit is the real burst ceiling.** When a workload flagged near-OOM inherits its limit
  from the LimitRange, raising headroom means either setting a higher explicit limit on the
  workload or raising the LimitRange `default` — the same per-workload-vs-namespace-wide choice.
- **Mind the LimitRange `min` / `max`.** An explicit request below the namespace `min` (or a limit
  above `max`) is rejected at admission. If a `kubectl set resources` is refused, check
  `kubectl describe limitrange -n <namespace>`.

## Infra namespaces

`kube-system`, `gke-managed-*`, `sealed-secrets`, monitoring, etc. are control-plane / cluster
agents. The audit flags them **advisory only**. Do not aggressively cut them — a starved
control-plane component degrades the whole cluster, and many are managed by GKE and will be
reset. Treat any change there as its own reviewed task.
