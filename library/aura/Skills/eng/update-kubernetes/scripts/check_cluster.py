#!/usr/bin/env python3
"""Workload checks around a GKE upgrade, read-only through kubectl. Counts and names, never values.

  check_cluster.py --cluster dev|build|prod --phase pre
  check_cluster.py --cluster dev|build|prod --phase post --expect 1.37.2-gke.1000 --since 2026-12-02T07:00:00Z

kubectl runs with the gke_<project>_<zone>_<cluster> context from the runner's kubeconfig (the person's
gcloud login; kubectl has no service-account path here). It only gets and lists.

  pre   PodDisruptionBudgets already below budget (ALERT) or allowing 0 disruptions (a NOTE: GKE waits up to
        an hour per node, then evicts), CronJobs not on batch/v1,
        BackendConfigs without a Cloud Armor policy, the sealed-secrets controller's image.
  post  every node's kubelet on --expect; every Deployment in the app namespaces available; pods restarted
        since --since; Jobs failed since --since; CronJobs not scheduled since --since (a note: a weekly
        job legitimately hasn't fired).
Exit: 0 OK, 1 something to look at, 2 a read failed or came back empty (UNVERIFIED, never OK).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

from plan_upgrade import CLUSTERS, version

# The app namespaces with Deployments on each cluster (live 2026-11-03: dev runs none in aura-web or aura-data).
APP_NAMESPACES = {"dev": ("aura-services", "aura-ai"), "build": ("spinnaker",),
                  "prod": ("aura-services", "aura-ai", "aura-data", "aura-web")}


class Unread(RuntimeError):
    pass


def context(name: str) -> str:
    c = CLUSTERS[name]
    return f"gke_{c['project']}_{c['zone']}_{c['name']}"


def kubectl(ctx: str, args: list[str], run=subprocess.run) -> list[dict]:
    out = run(["kubectl", "--context", ctx, "get", *args, "-o", "json"], capture_output=True, text=True,
              encoding="utf-8", errors="replace")
    if out.returncode:
        raise Unread(f"kubectl get {' '.join(args)} failed: {(out.stderr or '').strip()[-300:]}")
    try:
        return json.loads(out.stdout).get("items", [])
    except ValueError:
        raise Unread(f"kubectl get {' '.join(args)} returned no JSON")


def ts(v: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


def ref(o: dict) -> str:
    m = o.get("metadata", {})
    return f"{m.get('namespace', '-')}/{m.get('name', '?')}"


def pre(get) -> list[tuple[str, str]]:
    out = []
    pdbs = get(["pdb", "-A"])
    single = []
    for p in pdbs:
        st = p.get("status", {})
        if not st.get("expectedPods") or st.get("disruptionsAllowed", 0):
            continue
        if st.get("currentHealthy", 0) < st.get("desiredHealthy", 0):
            out.append(("ALERT", f"PDB {ref(p)}: {st.get('currentHealthy', 0)}/{st.get('desiredHealthy')} healthy, "
                                 "already below its budget: fix the workload before draining"))
        else:
            single.append(ref(p))
    if single:
        # GKE honours a PDB for up to an hour per node during an upgrade, then evicts anyway.
        out.append(("NOTE", f"{len(single)} PDB(s) allow 0 disruptions (usually one replica with minAvailable 1): "
                            f"each node drain waits up to an hour, then evicts; once the surge node is Ready, "
                            f"`kubectl rollout restart` each so its new pod starts on the new node first: "
                            f"{', '.join(single[:8])}" + (" …" if len(single) > 8 else "")))
    for c in get(["cronjobs", "-A"]):
        if c.get("apiVersion") != "batch/v1":
            out.append(("ALERT", f"CronJob {ref(c)} is {c.get('apiVersion')}, not batch/v1"))
    try:
        bcs = get(["backendconfigs", "-A"])
    except Unread as e:
        out.append(("NOTE", f"BackendConfigs not read ({e}): check the Cloud Armor attachment by hand"))
        bcs = []
    for b in bcs:
        if not ((b.get("spec") or {}).get("securityPolicy") or {}).get("name"):
            out.append(("NOTE", f"BackendConfig {ref(b)} has no Cloud Armor policy"))
    sealed = [d for d in get(["deployments", "-A"]) if "sealed-secrets" in d.get("metadata", {}).get("name", "")]
    for d in sealed:
        imgs = [c.get("image", "?") for c in d.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])]
        out.append(("NOTE", f"sealed-secrets controller {ref(d)}: {', '.join(imgs)}: check it supports the target"))
    if not sealed:
        out.append(("NOTE", "no sealed-secrets controller Deployment found"))
    out.append(("OK", f"{len(pdbs)} PDB(s) read, {len(bcs)} BackendConfig(s) read"))
    return out


def post(get, expect: str, since: datetime, namespaces=APP_NAMESPACES["prod"]) -> list[tuple[str, str]]:
    out = []
    nodes = get(["nodes"])
    if not nodes:
        raise Unread("no nodes in the read")
    want = expect.split("-")[0]
    off = {}
    for n in nodes:
        k = n.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", "?").lstrip("v").split("-")[0]
        if version(k)[:3] != version(want)[:3]:
            off[k] = off.get(k, 0) + 1
    out += [("ALERT", f"{c} node(s) still on kubelet {k}, expected {want}") for k, c in sorted(off.items())]
    if not off:
        out.append(("OK", f"all {len(nodes)} node(s) on kubelet {want}"))
    for ns in namespaces:
        deps = get(["deployments", "-n", ns])
        if not deps:
            raise Unread(f"no Deployments read in {ns}")
        bad = [d for d in deps if (d.get("status", {}).get("availableReplicas") or 0)
               < (d.get("spec", {}).get("replicas") or 0)]
        out += [("ALERT", f"Deployment {ref(d)}: {d.get('status', {}).get('availableReplicas') or 0}/"
                          f"{d.get('spec', {}).get('replicas')} available") for d in bad]
        if not bad:
            out.append(("OK", f"{ns}: {len(deps)} Deployment(s) available"))
        restarted = 0
        for p in get(["pods", "-n", ns]):
            for cs in p.get("status", {}).get("containerStatuses", []):
                t = ts(((cs.get("lastState") or {}).get("terminated") or {}).get("finishedAt"))
                if t and t >= since:
                    restarted += 1
        if restarted:
            out.append(("ALERT", f"{ns}: {restarted} container restart(s) since the upgrade began"))
    # The Job's Failed condition, not status.failed: that counts failed pods, and a pod evicted by the drain
    # and retried leaves a Job that succeeded with failed >= 1.
    failed = [j for j in get(["jobs", "-A"]) if any(
        c.get("type") == "Failed" and c.get("status") == "True" and (ts(c.get("lastTransitionTime")) or since) >= since
        for c in j.get("status", {}).get("conditions", []))]
    out += [("ALERT", f"Job {ref(j)} failed since the upgrade began") for j in failed]
    crons = [c for c in get(["cronjobs", "-A"]) if not c.get("spec", {}).get("suspend")]
    quiet = [c for c in crons if (ts(c.get("status", {}).get("lastScheduleTime")) or since) < since]
    if quiet:
        out.append(("NOTE", f"{len(quiet)} of {len(crons)} CronJob(s) not scheduled since the upgrade began "
                            f"(fine for a weekly or monthly job): {', '.join(ref(c) for c in quiet[:10])}"))
    return out


def main(argv=None, run=subprocess.run) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--cluster", choices=sorted(CLUSTERS), required=True)
    ap.add_argument("--phase", choices=["pre", "post"], required=True)
    ap.add_argument("--expect", help="post: the version every node should be on")
    ap.add_argument("--since", help="post: when the upgrade began (UTC ISO)")
    a = ap.parse_args(argv)
    if a.phase == "post" and not (a.expect and ts(a.since)):
        ap.error("--phase post needs --expect and --since")
    ctx = context(a.cluster)
    try:
        res = pre(lambda x: kubectl(ctx, x, run)) if a.phase == "pre" else \
            post(lambda x: kubectl(ctx, x, run), a.expect, ts(a.since).astimezone(timezone.utc),
                 APP_NAMESPACES[a.cluster])
    except Unread as e:
        print(f"UNVERIFIED {ctx}: {e}")
        return 2
    for level in ("ALERT", "NOTE", "OK"):
        for lv, text in res:
            if lv == level:
                print(f"{lv:5} {text}")
    return 1 if any(lv == "ALERT" for lv, _ in res) else 0


if __name__ == "__main__":
    sys.exit(main())
