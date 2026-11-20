"""eng:update-kubernetes (ENG-1741): the GKE upgrade plan and the workload checks around it.

The skill never upgrades a cluster; Backend does, with Jerome's go. So what is pinned here is what the
plan lets through: one minor at a time, dev before prod, a deprecation insight or a non-COS node image
blocks, and a failed or empty read is UNVERIFIED rather than "current" (a read that came back empty once
printed "matches live" for four apps: CLAUDE.md, "An empty or failed read is unverified"). gcloud and
kubectl are stubbed at their one call each; fixtures follow the shape of `gcloud ... --format=json` and
`kubectl get -o json`.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "plugins/eng/skills/update-kubernetes/scripts"
sys.path.insert(0, str(SCRIPTS))
import check_cluster  # noqa: E402
import plan_upgrade  # noqa: E402

V36 = "1.36.4-gke.2046003"
V36B = "1.36.6-gke.1100000"
V37 = "1.37.2-gke.1000000"
V38 = "1.38.0-gke.500000"


def gke(master=V36, pools=(("standard-pool", V36, "COS_CONTAINERD"),), valid=(V37, V36B, V36), insights=(),
        fail=None):
    """A stub for gcloud_sa.run answering the four reads plan_upgrade makes."""
    def run(args, **kw):
        if fail and fail in args:
            return subprocess.CompletedProcess(args, 1, "", "PERMISSION_DENIED: container.clusters.get")
        if args[:3] == ["container", "clusters", "describe"]:
            body = {"currentMasterVersion": master, "releaseChannel": {"channel": "REGULAR"},
                    "maintenancePolicy": {"window": {"recurringWindow": {
                        "recurrence": "FREQ=WEEKLY;BYDAY=WE", "window": {"startTime": "2026-01-07T07:00:00Z"}}}}}
        elif args[:3] == ["container", "node-pools", "list"]:
            body = [{"name": n, "version": v, "config": {"imageType": img},
                     "upgradeSettings": {"strategy": "SURGE", "maxSurge": 1}} for n, v, img in pools]
        elif args[:2] == ["container", "get-server-config"]:
            body = {"channels": [{"channel": "REGULAR", "defaultVersion": V36B, "validVersions": list(valid)},
                                 {"channel": "RAPID", "validVersions": [V38]}]}
        else:
            body = [{"insightSubtype": s} for s in insights]
        return subprocess.CompletedProcess(args, 0, json.dumps(body), "")
    return run


def plan(argv, run):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = plan_upgrade.main([*argv, "--json"], run=run)
    return code, {p["cluster"]: p for p in json.loads(out.getvalue())}


class Plan(unittest.TestCase):
    def test_newest_offered_version_on_the_channel_with_commands_master_first(self):
        code, p = plan(["--cluster", "dev"], gke())
        dev = p["aura-dev-cluster-a"]
        self.assertEqual((code, dev["status"], dev["target"]), (0, "upgrade", V37))
        self.assertIn("--master --cluster-version " + V37, dev["commands"][0])
        self.assertIn("--node-pool standard-pool", dev["commands"][1])
        self.assertTrue(all(c.startswith("python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-kubernetes/scripts/gcloud_sa.py ")
                            for c in dev["commands"]))
        self.assertIn("FREQ=WEEKLY;BYDAY=WE", dev["notes"][0])

    def test_already_current_has_no_commands(self):
        code, p = plan(["--cluster", "dev"], gke(master=V37, pools=(("standard-pool", V37, "COS_CONTAINERD"),)))
        self.assertEqual((code, p["aura-dev-cluster-a"]["status"], p["aura-dev-cluster-a"]["commands"]),
                         (0, "current", []))

    def test_a_lagging_pool_gets_its_own_command(self):
        _, p = plan(["--cluster", "dev"], gke(master=V37, pools=(("standard-pool", V36, "COS_CONTAINERD"),)))
        self.assertEqual(len(p["aura-dev-cluster-a"]["commands"]), 1)
        self.assertIn("--node-pool standard-pool", p["aura-dev-cluster-a"]["commands"][0])

    def test_one_minor_at_a_time(self):
        code, p = plan(["--cluster", "dev", "--target", V38], gke(valid=(V38, V37, V36)))
        dev = p["aura-dev-cluster-a"]
        self.assertEqual((code, dev["status"]), (1, "blocked"))
        self.assertIn("one minor at a time; next step is " + V37, dev["blockers"][0])

    def masters(self, **by_project):
        def run(args, **kw):
            proj = next(v for v in by_project if f"--project={v}" in args or v in args)
            return gke(master=by_project[proj], pools=(("p", by_project[proj], "COS_CONTAINERD"),))(args, **kw)
        return run

    def test_dev_then_build_then_prod(self):
        # ENGBACK-2196: the build cluster runs Spinnaker, the deploy path, so it sits between dev and prod.
        run = self.masters(**{"aura-dev": V36, "aura-build": V36B, "firebase-auratech16": V36B})
        code, p = plan(["--cluster", "build", "--target", V37], run)
        self.assertEqual(code, 1)
        self.assertNotIn("aura-dev-cluster-a", p)
        self.assertIn("dev first: dev's control plane is " + V36, p["aura-build-cluster"]["blockers"][0])
        self.assertIn("release freeze", " ".join(p["aura-build-cluster"]["notes"]))
        run = self.masters(**{"aura-dev": V37, "aura-build": V36B, "firebase-auratech16": V36B})
        _, p = plan(["--cluster", "prod", "--target", V37], run)
        self.assertEqual(sorted(p), ["aura-prod-cluster-a"])
        self.assertIn("build first: build's control plane is " + V36B, p["aura-prod-cluster-a"]["blockers"][0])
        run = self.masters(**{"aura-dev": V37, "aura-build": V37, "firebase-auratech16": V36B})
        _, p = plan(["--cluster", "prod", "--target", V37], run)
        self.assertEqual(p["aura-prod-cluster-a"]["blockers"], [])

    def test_blockers_hide_the_commands(self):
        code, p = plan(["--cluster", "dev"], gke(insights=("DEPRECATION_K8S_1_37_EXAMPLE",),
                                                  pools=(("n2d-pool", V36, "UBUNTU_CONTAINERD"),)))
        dev = p["aura-dev-cluster-a"]
        self.assertEqual((code, dev["status"], len(dev["blockers"])), (1, "blocked", 2))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            plan_upgrade.main(["--cluster", "dev"], run=gke(insights=("DEPRECATION_K8S_1_37_EXAMPLE",)))
        self.assertNotIn("commands", out.getvalue())

    def test_a_failed_read_is_unverified(self):
        code, p = plan(["--cluster", "all"], gke(fail="describe"))
        self.assertEqual(code, 2)
        self.assertEqual({x["status"] for x in p.values()}, {"unverified"})
        self.assertIn("PERMISSION_DENIED", p["aura-dev-cluster-a"]["error"])

    def test_an_empty_read_is_unverified(self):
        code, p = plan(["--cluster", "dev"], gke(master=""))
        self.assertEqual((code, p["aura-dev-cluster-a"]["status"]), (2, "unverified"))

    def test_prod_with_an_unread_build_is_blocked(self):
        def run(args, **kw):
            if "aura-build" in " ".join(args):
                return subprocess.CompletedProcess(args, 1, "", "IAM_PERMISSION_DENIED")
            return gke()(args, **kw)
        code, p = plan(["--cluster", "prod"], run)
        self.assertEqual(code, 2)
        self.assertEqual(p["aura-build-cluster"]["status"], "unverified")
        self.assertIn("build's control plane could not be read", p["aura-prod-cluster-a"]["blockers"][-1])


def kube(objects):
    """A stub for subprocess.run answering `kubectl get <kind> ...` from a {kind: [items]} map."""
    def run(argv, **kw):
        kind = argv[4]
        if kind not in objects:
            return subprocess.CompletedProcess(argv, 1, "", f"error: the server doesn't have a resource type {kind}")
        items = objects[kind]
        if "-n" in argv:
            ns = argv[argv.index("-n") + 1]
            items = [o for o in items if o["metadata"].get("namespace") == ns]
        return subprocess.CompletedProcess(argv, 0, json.dumps({"items": items}), "")
    return run


def obj(ns, name, **rest):
    return {"metadata": {"namespace": ns, "name": name}, **rest}


def healthy():
    deps = [obj(ns, f"svc-{i}", spec={"replicas": 2}, status={"availableReplicas": 2})
            for ns in check_cluster.APP_NAMESPACES["prod"] for i in range(2)]
    return {
        "nodes": [{"metadata": {"name": f"n{i}"}, "status": {"nodeInfo": {"kubeletVersion": "v1.37.2-gke.1000000"}}}
                  for i in range(3)],
        "deployments": deps,
        "pods": [obj("aura-services", "svc-0-abc", status={"containerStatuses": [{"lastState": {}}]})],
        "jobs": [],
        "cronjobs": [obj("aura-services", "nightly", apiVersion="batch/v1", spec={},
                         status={"lastScheduleTime": "2026-12-02T09:00:00Z"})],
    }


def check(argv, run):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = check_cluster.main(argv, run=run)
    return code, out.getvalue()


POST = ["--cluster", "dev", "--phase", "post", "--expect", V37, "--since", "2026-12-02T07:00:00Z"]


class Check(unittest.TestCase):
    def test_a_healthy_cluster_passes(self):
        code, out = check(POST, kube(healthy()))
        self.assertEqual(code, 0, out)
        self.assertIn("all 3 node(s) on kubelet 1.37.2", out)

    def test_it_reads_the_clusters_own_context(self):
        seen = []
        run = kube(healthy())
        check(POST, lambda a, **k: seen.append(a[:4]) or run(a, **k))
        self.assertEqual(seen[0], ["kubectl", "--context", "gke_aura-dev_us-central1-c_aura-dev-cluster-a", "get"])
        self.assertEqual({a[3] for a in seen}, {"get"})

    def test_problems_alert(self):
        o = healthy()
        o["nodes"][0]["status"]["nodeInfo"]["kubeletVersion"] = "v1.36.4-gke.2046003"
        o["deployments"][0]["status"]["availableReplicas"] = 1
        o["pods"][0]["status"]["containerStatuses"][0]["lastState"] = {"terminated": {"finishedAt": "2026-12-02T08:00:00Z"}}
        o["jobs"] = [obj("aura-data", "export-1", status={"failed": 2, "conditions": [
                         {"type": "Failed", "status": "True", "lastTransitionTime": "2026-12-02T08:30:00Z"}]}),
                     obj("aura-data", "export-2", status={"failed": 1, "succeeded": 1, "conditions": [
                         {"type": "Complete", "status": "True", "lastTransitionTime": "2026-12-02T08:40:00Z"}]})]
        code, out = check(POST, kube(o))
        self.assertEqual(code, 1)
        for text in ("1 node(s) still on kubelet 1.36.4", "1/2 available", "1 container restart(s)",
                     "Job aura-data/export-1 failed"):
            self.assertIn(text, out)
        self.assertNotIn("export-2", out)  # an evicted pod retried: the Job succeeded

    def test_a_cronjob_not_yet_fired_is_only_a_note(self):
        o = healthy()
        o["cronjobs"][0]["status"]["lastScheduleTime"] = "2026-11-30T09:00:00Z"
        code, out = check(POST, kube(o))
        self.assertEqual(code, 0)
        self.assertIn("NOTE  1 of 1 CronJob(s) not scheduled since the upgrade began", out)

    def test_an_empty_namespace_or_failed_read_is_unverified(self):
        o = healthy()
        o["deployments"] = [d for d in o["deployments"] if d["metadata"]["namespace"] != "aura-ai"]
        self.assertEqual(check(POST, kube(o))[0], 2)
        del o["nodes"]
        code, out = check(POST, kube(o))
        self.assertEqual(code, 2)
        self.assertIn("UNVERIFIED", out)

    def test_pre_flight(self):
        o = healthy()
        o["pdb"] = [obj("aura-services", "api", status={"expectedPods": 2, "currentHealthy": 1, "desiredHealthy": 2,
                                                        "disruptionsAllowed": 0}),
                    obj("aura-services", "one", status={"expectedPods": 1, "currentHealthy": 1, "desiredHealthy": 1,
                                                        "disruptionsAllowed": 0}),
                    obj("aura-services", "web", status={"expectedPods": 2, "disruptionsAllowed": 1})]
        o["cronjobs"].append(obj("aura-data", "old", apiVersion="batch/v1beta1", spec={}, status={}))
        o["backendconfigs"] = [obj("aura-web", "edge", spec={"securityPolicy": {"name": "aura-web-edge"}}),
                               obj("aura-web", "bare", spec={})]
        o["deployments"].append(obj("kube-system", "sealed-secrets-controller", spec={"template": {"spec": {
            "containers": [{"image": "docker.io/bitnami/sealed-secrets-controller:0.27.1"}]}}}))
        code, out = check(["--cluster", "prod", "--phase", "pre"], kube(o))
        self.assertEqual(code, 1)
        self.assertIn("ALERT PDB aura-services/api: 1/2 healthy, already below its budget", out)
        self.assertIn("NOTE  1 PDB(s) allow 0 disruptions", out)
        self.assertIn("rollout restart", out)
        self.assertIn("aura-services/one", out)
        self.assertNotIn("aura-services/web", out)
        self.assertIn("CronJob aura-data/old is batch/v1beta1", out)
        self.assertIn("BackendConfig aura-web/bare has no Cloud Armor policy", out)
        self.assertIn("sealed-secrets-controller:0.27.1", out)

    def test_the_build_cluster_checks_spinnaker(self):
        self.assertEqual(check_cluster.APP_NAMESPACES["build"], ("spinnaker",))
        self.assertEqual(check_cluster.context("build"), "gke_aura-build_us-central1-c_aura-build-cluster")

    def test_post_needs_the_target_and_start(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            check_cluster.main(["--cluster", "dev", "--phase", "post"], run=kube(healthy()))


class Wiring(unittest.TestCase):
    def test_the_skill_never_runs_an_upgrade_itself(self):
        skill = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("clusters upgrade", skill)
        calls = []
        run = gke()
        plan(["--cluster", "all"], lambda a, **k: calls.append(a) or run(a, **k))
        self.assertEqual(len(calls), 12)  # four reads for each of the three clusters
        self.assertEqual([a for a in calls if "upgrade" in a], [])


if __name__ == "__main__":
    unittest.main()
