#!/usr/bin/env python3
"""Plan a GKE upgrade: read each cluster's versions, channel, window and blockers. Read-only.

  plan_upgrade.py [--cluster dev|build|prod|all] [--target 1.37.2-gke.1000] [--json]

Reads, as the GCP_KEY service account (gcloud_sa.py): the cluster (control plane version, release
channel, maintenance window), its node pools (version, image type, surge settings), the versions the
channel offers (get-server-config) and GKE's deprecation insights (removed APIs still in use). Prints one
plan per cluster and the upgrade commands for the person who runs them (the AuraInfra code owner, with Jerome's go).
Never runs an upgrade.

Rules:
  target    --target, or the newest version the cluster's channel offers. One minor at a time: a target
            more than one minor ahead is refused, with the next minor's newest version proposed instead.
  order     dev, then build (Spinnaker, the deploy path), then prod: each cluster's target may not be ahead
            of the control plane of the one before it.
  blockers  any deprecation insight, a node image other than COS_CONTAINERD.
  unread    a failed or empty read is UNVERIFIED, never "up to date".
Exit: 0 nothing to do or a plan with no blockers, 1 blockers, 2 a read failed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcloud_sa  # noqa: E402

# Live 2026-11-03: both zonal, REGULAR channel. references/clusters.md has the rest.
CLUSTERS = {
    "dev": {"project": "aura-dev", "zone": "us-central1-c", "name": "aura-dev-cluster-a"},
    # Spinnaker, which deploys every release (ENGBACK-2196): upgraded after dev and before prod.
    "build": {"project": "aura-build", "zone": "us-central1-c", "name": "aura-build-cluster"},
    "prod": {"project": "firebase-auratech16", "zone": "us-central1-c", "name": "aura-prod-cluster-a"},
}
IMAGE = "COS_CONTAINERD"
ORDER = ["dev", "build", "prod"]
BEFORE = {"build": "dev", "prod": "build"}
SA = "${CLAUDE_PLUGIN_ROOT}/skills/update-kubernetes/scripts/gcloud_sa.py"


class Unread(RuntimeError):
    pass


def version(v: str) -> tuple:
    """1.36.4-gke.2046003 -> (1, 36, 4, 2046003); anything else sorts lowest."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)(?:-gke\.(\d+))?$", v or "")
    return tuple(int(x or 0) for x in m.groups()) if m else (0, 0, 0, 0)


def minor(v: str) -> tuple:
    return version(v)[:2]


def read(args: list[str], run=None) -> object:
    run = run or gcloud_sa.run
    out = run([*args, "--format=json"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode:
        raise Unread(f"gcloud {' '.join(args[:3])} failed as {gcloud_sa.credential()}: "
                     f"{(out.stderr or '').strip()[-300:]}")
    try:
        return json.loads(out.stdout or "null")
    except ValueError:
        raise Unread(f"gcloud {' '.join(args[:3])} returned no JSON")


def state(c: dict, run=None) -> dict:
    loc = ["--zone", c["zone"], "--project", c["project"]]
    cl = read(["container", "clusters", "describe", c["name"], *loc], run) or {}
    pools = read(["container", "node-pools", "list", "--cluster", c["name"], *loc], run) or []
    cfg = read(["container", "get-server-config", *loc], run) or {}
    insights = read(["recommender", "insights", "list", "--insight-type=google.container.DiagnosisInsight",
                     f"--location={c['zone']}", f"--project={c['project']}", "--filter=insightSubtype:DEPRECATION"],
                    run) or []
    master = cl.get("currentMasterVersion", "")
    if not master or not pools:
        raise Unread(f"{c['name']}: no control plane version or no node pools in the read")
    channel = (cl.get("releaseChannel") or {}).get("channel", "UNSPECIFIED")
    offered = next((ch for ch in cfg.get("channels", []) if ch.get("channel") == channel), {})
    window = ((cl.get("maintenancePolicy") or {}).get("window") or {}).get("recurringWindow") or {}
    return {
        "cluster": c["name"], "project": c["project"], "zone": c["zone"], "master": master, "channel": channel,
        "valid": offered.get("validVersions", []), "default": offered.get("defaultVersion", ""),
        "window": {"recurrence": window.get("recurrence", ""), "start": (window.get("window") or {}).get("startTime", "")},
        "pools": [{"name": p.get("name"), "version": p.get("version", ""),
                   "image": (p.get("config") or {}).get("imageType", ""),
                   "strategy": (p.get("upgradeSettings") or {}).get("strategy", "SURGE")} for p in pools],
        "deprecations": sorted({i.get("insightSubtype", "?") for i in insights}),
    }


def plan(s: dict, target: str | None = None, before_master: str | None = None, before: str = "dev") -> dict:
    blockers, notes = [], []
    valid = sorted(s["valid"], key=version, reverse=True)
    if not valid and not target:
        raise Unread(f"{s['cluster']}: the {s['channel']} channel offered no versions in the read")
    want = target or valid[0]
    if minor(want)[1] - minor(s["master"])[1] > 1 or minor(want)[0] != minor(s["master"])[0]:
        nxt = (minor(s["master"])[0], minor(s["master"])[1] + 1)
        step = next((v for v in valid if minor(v) == nxt), "")
        blockers.append(f"{want} is more than one minor ahead of {s['master']}: GKE upgrades one minor at a time"
                        + (f"; next step is {step}" if step else ""))
        want = step or want
    if target and valid and target not in valid:
        blockers.append(f"{target} is not offered on the {s['channel']} channel")
    if before_master is not None and version(want) > version(before_master):
        blockers.append(f"{before} first: {before}'s control plane is {before_master}, below {want}")
    if s["deprecations"]:
        blockers.append(f"{len(s['deprecations'])} deprecation insight subtype(s) open: {', '.join(s['deprecations'])}")
    for p in s["pools"]:
        if p["image"] != IMAGE:
            blockers.append(f"node pool {p['name']} runs {p['image'] or 'an unknown image'}, not {IMAGE}")
    up_master = version(want) > version(s["master"])
    pools = [p for p in s["pools"] if version(want) > version(p["version"])]
    if s["window"]["recurrence"]:
        start = s["window"]["start"][11:16] + "Z" if len(s["window"]["start"]) >= 16 else "?"
        notes.append(f"auto-upgrades on {s['channel']} in the window {s['window']['recurrence']} at {start}: "
                     f"an upgrade not pulled forward lands there")
    loc = f"--zone {s['zone']} --project {s['project']} --quiet"
    cmds = []
    if up_master:
        cmds.append(f"python3 {SA} container clusters upgrade {s['cluster']} --master --cluster-version {want} {loc}")
    cmds += [f"python3 {SA} container clusters upgrade {s['cluster']} --node-pool {p['name']} --cluster-version {want} {loc}"
             for p in pools]
    return {**s, "target": want, "blockers": blockers, "notes": notes, "commands": cmds,
            "status": "blocked" if blockers else ("upgrade" if cmds else "current")}


def render(p: dict) -> str:
    lines = [f"{p['cluster']} ({p['project']}, {p['zone']}, {p['channel']}): control plane {p['master']}, "
             f"target {p['target']} -> {p['status'].upper()}"]
    lines += [f"  pool {x['name']}: {x['version']} {x['image']} {x['strategy']}" for x in p["pools"]]
    lines += [f"  BLOCKER {b}" for b in p["blockers"]] + [f"  note {n}" for n in p["notes"]]
    if p["commands"] and not p["blockers"]:
        lines.append("  commands (run by the code owner, with Jerome's go; control plane first):")
        lines += [f"    {c}" for c in p["commands"]]
    return "\n".join(lines)


def main(argv=None, run=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--cluster", choices=[*ORDER, "all"], default="all")
    ap.add_argument("--target")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    names = ORDER if a.cluster == "all" else [a.cluster]
    out, code, masters = [], 0, {}
    # Each cluster is planned against the one before it, so every earlier cluster is read first.
    for n in ORDER[:max(ORDER.index(x) for x in names) + 1]:
        try:
            s = state(CLUSTERS[n], run)
        except Unread as e:
            out.append({"cluster": CLUSTERS[n]["name"], "status": "unverified", "error": str(e)})
            code = 2
            continue
        masters[n] = s["master"]
        if n not in names:
            continue
        prev = BEFORE.get(n)
        p = plan(s, a.target, masters.get(prev), prev or "dev")
        if prev and prev not in masters:
            p["blockers"].append(f"{prev} first: {prev}'s control plane could not be read")
            p["status"] = "blocked"
        if n == "build":
            p["notes"].append("release freeze while its node pools roll: no Spinnaker deploys until they're done; "
                              "after, deploy one release to dev through Spinnaker to check the deploy path")
        out.append(p)
        if p["blockers"]:
            code = max(code, 1)
    if a.json:
        print(json.dumps(out, indent=1))
    else:
        for p in out:
            print(f"{p['cluster']}: UNVERIFIED: {p['error']}" if p["status"] == "unverified" else render(p))
    return code


if __name__ == "__main__":
    sys.exit(main())
