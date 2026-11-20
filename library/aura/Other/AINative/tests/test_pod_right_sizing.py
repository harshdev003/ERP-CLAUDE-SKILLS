"""Pins the two defects ENG-1228 removed from `eng:audit-pod-right-sizing`.

Both were *silent*: each produced a clean-looking report whose numbers were
materially wrong, with nothing raised and no empty section to notice. That is
exactly the shape a happy-path run cannot catch — the 2026-09-02 prod run only
caught them because GCP's own recommender independently flagged the same
workload and the figures didn't reconcile.

  1. Classification was per CONTAINER and mutually exclusive, so a container
     tripping any risk on one resource never had the other resource analysed.
     The single largest CPU reclaim in the cluster was therefore absent from the
     CPU roll-up — the omitted item was bigger than the entire reported total.
  2. Pod age was never read, so a uniform reading across uniformly-aged replicas
     was written up as steady state when it was accumulation.

The scenario below is the real one (user-preferences-model, aura-ai, 8 replicas).
Analysis is pure — no kubectl, no network.
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = (pathlib.Path(__file__).resolve().parents[1]
              / "plugins/eng/skills/audit-pod-right-sizing/scripts")
sys.path.insert(0, str(SCRIPT_DIR))

import audit_pod_resources as A  # noqa: E402

GiB = A.GiB
MiB = A.MiB
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

DEFAULTS = dict(
    over_ratio=4.0, cpu_headroom=1.5, mem_headroom=2.0,
    cpu_floor_m=50, mem_floor_mi=128, near_limit_frac=0.9,
    min_reclaim_mem_mi=128, min_reclaim_cpu_m=100,
)


def workload(ns="aura-ai", name="user-preferences-model", replicas=8,
             req_cpu="3000m", req_mem="2900Mi", lim_cpu=None, lim_mem=None,
             container="app", kind="Deployment"):
    wl = A.Workload(ns=ns, kind=kind, name=name, replicas=replicas)
    wl.containers[container] = A.ContainerSpec(
        req_cpu_m=A.parse_cpu_millicores(req_cpu),
        req_mem_b=A.parse_mem_bytes(req_mem),
        lim_cpu_m=A.parse_cpu_millicores(lim_cpu),
        lim_mem_b=A.parse_mem_bytes(lim_mem),
    )
    return {(ns, kind, name): wl}


def usage(pods, ns="aura-ai", name="user-preferences-model", container="app",
          kind="Deployment"):
    """pods: {podname: (cpu_millicores, mem_bytes)} -> (usage map, started map)."""
    u = A.Usage()
    started = {}
    for i, (pod, (cpu, mem)) in enumerate(pods.items()):
        u.peak_cpu_m = max(u.peak_cpu_m, cpu)
        u.peak_mem_b = max(u.peak_mem_b, mem)
        u.pods.add(pod)
        u.per_pod[pod] = A.PodPeak(peak_cpu_m=cpu, peak_mem_b=mem)
        u.samples += 1
    return {((ns, kind, name), container): u}, started


def aged(started, ages_h, ns="aura-ai"):
    for pod, h in ages_h.items():
        started[(ns, pod)] = NOW - timedelta(hours=h)
    return started


def run(wls, usg, started, **over):
    params = dict(DEFAULTS)
    params.update(over)
    return A.analyze(wls, usg, {}, started, now=NOW, **params)


class PerResourceClassification(unittest.TestCase):
    """Defect 1 — a container risky on one resource must still be analysed on the other."""

    def _real_case(self):
        # The 2026-09-02 workload: 3000m CPU request against a 76m peak (39x over)
        # AND a 3.8Gi memory peak against a 2900Mi request (running above it).
        wls = workload()
        pods = {f"upm-{i}": (76.0, 3.8 * GiB) for i in range(8)}
        usg, started = usage(pods)
        started = aged(started, {p: 168.0 for p in pods})  # all 7d — rolled together
        return run(wls, usg, started)[0]

    def test_cpu_over_and_mem_under_coexist(self):
        f = self._real_case()
        self.assertEqual(f.cpu_direction, "over")
        self.assertEqual(f.mem_direction, "under")
        self.assertEqual(f.over_resources, ["cpu"])
        self.assertEqual(f.under_resources, ["mem"])

    def test_cpu_reclaim_is_counted_despite_memory_risk(self):
        """The regression itself: this reclaim used to be zero."""
        f = self._real_case()
        # suggest = ceil(76 * 1.5 / 25) * 25 = 125m; (3000 - 125) * 8 replicas
        self.assertEqual(f.sugg_cpu_m, 125)
        self.assertEqual(f.reclaim_cpu_m, (3000 - 125) * 8)
        self.assertGreater(f.reclaim_cpu_m, 22_000)  # ~23 vCPU, per ENG-1228

    def test_memory_risk_is_still_reported(self):
        f = self._real_case()
        self.assertTrue(any("mem peak above request" in r for r in f.mem_reasons))
        self.assertIsNone(f.sugg_mem_b)
        self.assertEqual(f.reclaim_mem_b, 0.0)

    def test_container_appears_in_both_tables(self):
        f = self._real_case()
        md = A.render_markdown([f], "ctx", 3, DEFAULTS, {})
        over_body = md.split("## Over-provisioned")[1].split("## Under-provisioned")[0]
        risky_body = md.split("## Under-provisioned / risky")[1].split("## Replica age")[0]
        self.assertIn("user-preferences-model", over_body)
        self.assertIn("user-preferences-model", risky_body)

    def test_rows_name_the_resource_they_concern(self):
        """A row in both tables reads as contradictory unless each names its resource."""
        f = self._real_case()
        md = A.render_markdown([f], "ctx", 3, DEFAULTS, {})
        self.assertIn("| Over on |", md)
        self.assertIn("| Resource |", md)
        self.assertIn("Over on one resource, risky on the other", md)

    def test_rollup_cpu_total_includes_it(self):
        """The acceptance criterion: the roll-up must not omit the biggest item."""
        f = self._real_case()
        md = A.render_markdown([f], "ctx", 3, DEFAULTS, {})
        rollup = md.split("## Cluster roll-up")[1].split("## Over-provisioned")[0]
        self.assertIn("Reclaimable CPU:** **23.0 vCPU (23000m)**", rollup)

    def test_inverse_also_works(self):
        """Over on memory, risky on CPU — the mirror image the ticket asked to audit."""
        wls = workload(req_cpu="200m", req_mem="12000Mi", lim_cpu="220m", replicas=3)
        pods = {f"p-{i}": (215.0, 300 * MiB) for i in range(3)}
        usg, started = usage(pods)
        started = aged(started, {"p-0": 200.0, "p-1": 20.0, "p-2": 4.0})
        f = run(wls, usg, started)[0]
        self.assertEqual(f.mem_direction, "over")
        self.assertEqual(f.cpu_direction, "under")
        self.assertGreater(f.reclaim_mem_b, 0)

    def test_no_requests_is_judged_per_resource(self):
        """A container with memory reserved but no CPU request is still at risk."""
        wls = workload(req_cpu=None, req_mem="1Gi", replicas=1)
        usg, started = usage({"p-0": (50.0, 200 * MiB)})
        started = aged(started, {"p-0": 10.0})
        f = run(wls, usg, started)[0]
        self.assertEqual(f.cpu_direction, "under")
        self.assertTrue(any("no cpu request set" in r for r in f.cpu_reasons))
        self.assertNotEqual(f.mem_direction, "under")

    def test_direction_summary_reports_mixed(self):
        self.assertEqual(self._real_case().direction, "mixed")

    def test_healthy_container_stays_ok(self):
        wls = workload(req_cpu="200m", req_mem="512Mi", replicas=2)
        usg, started = usage({"p-0": (150.0, 400 * MiB), "p-1": (140.0, 380 * MiB)})
        started = aged(started, {"p-0": 100.0, "p-1": 10.0})
        f = run(wls, usg, started)[0]
        self.assertEqual(f.direction, "ok")
        self.assertEqual(f.reclaim_cpu_m, 0.0)
        self.assertEqual(f.reclaim_mem_b, 0.0)


class ReplicaAgeAwareness(unittest.TestCase):
    """Defect 2 — a uniform reading from uniformly-aged replicas is not steady state."""

    def test_uniform_ages_are_flagged_not_characterised(self):
        wls = workload(req_cpu="3000m", req_mem="8Gi")
        pods = {f"upm-{i}": (76.0, (3.5 + i * 0.05) * GiB) for i in range(8)}
        usg, started = usage(pods)
        started = aged(started, {p: 168.0 + (i * 0.2) for i, p in enumerate(pods)})
        f = run(wls, usg, started)[0]
        self.assertTrue(f.age.known)
        self.assertTrue(f.age.uniform)
        md = A.render_markdown([f], "ctx", 3, DEFAULTS, {})
        self.assertIn("uniform replica ages", md)
        self.assertIn("not characterised", md.lower())

    def test_single_replica_cannot_be_characterised(self):
        wls = workload(req_cpu="3000m", req_mem="8Gi", replicas=1)
        usg, started = usage({"p-0": (76.0, 3.5 * GiB)})
        started = aged(started, {"p-0": 168.0})
        f = run(wls, usg, started)[0]
        self.assertFalse(f.age.comparable)
        self.assertTrue(f.age.uniform)

    def test_spread_ages_are_not_flagged(self):
        wls = workload(req_cpu="3000m", req_mem="8Gi", replicas=3)
        usg, started = usage({"a": (76.0, 3.5 * GiB), "b": (70.0, 2.0 * GiB),
                              "c": (72.0, 1.0 * GiB)})
        started = aged(started, {"a": 168.0, "b": 40.0, "c": 6.0})
        f = run(wls, usg, started)[0]
        self.assertFalse(f.age.uniform)

    def test_oldest_vs_youngest_delta_is_the_growth_signal(self):
        """The one comparison that would have caught the 2.8Gi/week climb."""
        wls = workload(req_cpu="3000m", req_mem="8Gi", replicas=2)
        usg, started = usage({"old": (76.0, 3.8 * GiB), "new": (1269.0, 1.0 * GiB)})
        started = aged(started, {"old": 168.0, "new": 0.0})
        f = run(wls, usg, started)[0]
        self.assertAlmostEqual(f.age.growth_mem_b, 2.8 * GiB, delta=1 * MiB)
        self.assertTrue(f.age.mem_growth_material)
        # 2.8Gi over 7 days
        self.assertAlmostEqual(f.age.mem_growth_per_day_b, 0.4 * GiB, delta=2 * MiB)
        md = A.render_markdown([f], "ctx", 3, DEFAULTS, {})
        self.assertIn("Memory growth signal", md)

    def test_ages_appear_in_the_tables(self):
        wls = workload(req_cpu="3000m", req_mem="8Gi")
        pods = {f"upm-{i}": (76.0, 3.5 * GiB) for i in range(8)}
        usg, started = usage(pods)
        started = aged(started, {p: 168.0 for p in pods})
        md = A.render_markdown(run(wls, usg, started), "ctx", 3, DEFAULTS, {})
        self.assertIn("| Replica age |", md)
        self.assertIn("7.0d", md)

    def test_cpu_is_never_rendered_as_a_bare_core_count(self):
        """A bare '3' for 3000m reads as 3 millicores in a table of '125m' values."""
        self.assertEqual(A.fmt_cpu(3000), "3000m")
        self.assertEqual(A.fmt_cpu(125), "125m")
        self.assertIn("vCPU", A.fmt_vcpu(23000))

    def test_missing_start_times_degrade_rather_than_crash(self):
        wls = workload(req_cpu="3000m", req_mem="8Gi", replicas=2)
        usg, _ = usage({"a": (76.0, 3.5 * GiB), "b": (70.0, 3.4 * GiB)})
        f = run(wls, usg, {})[0]
        self.assertFalse(f.age.known)
        self.assertFalse(f.age.uniform)   # unknown is not a claim of uniformity
        A.render_markdown([f], "ctx", 3, DEFAULTS, {})  # must not raise

    def test_sizing_caveat_covers_cold_start_for_both_resources(self):
        md = A.render_markdown([], "ctx", 3, DEFAULTS, {})
        caveat = md.split("## Applying")[1]
        self.assertIn("cold start", caveat.lower())
        self.assertIn("order of magnitude", caveat.lower())
        self.assertIn("accumulat", caveat.lower())


class JsonPayload(unittest.TestCase):
    """`--json` must expose the per-resource verdicts, or a caller inherits the blind spot."""

    def _payload(self):
        wls = workload()
        pods = {f"upm-{i}": (76.0, 3.8 * GiB) for i in range(8)}
        usg, started = usage(pods)
        started = aged(started, {p: 168.0 for p in pods})
        return json.loads(json.dumps(A.findings_to_dicts(run(wls, usg, started))))[0]

    def test_per_resource_verdicts_are_present(self):
        d = self._payload()
        self.assertEqual(d["cpu_direction"], "over")
        self.assertEqual(d["mem_direction"], "under")
        self.assertEqual(d["over_resources"], ["cpu"])
        self.assertEqual(d["under_resources"], ["mem"])
        self.assertEqual(d["direction"], "mixed")
        self.assertEqual(d["reclaim_cpu_m"], (3000 - 125) * 8)

    def test_age_fields_are_present(self):
        d = self._payload()
        self.assertEqual(len(d["replica_ages_hours"]), 8)
        self.assertTrue(d["age_uniform"])
        self.assertTrue(d["age_comparable"])
        self.assertAlmostEqual(d["replica_age_max_hours"], 168.0)

    def test_reason_lists_are_split_by_resource(self):
        d = self._payload()
        self.assertTrue(any("mem peak above request" in r for r in d["mem_reasons"]))
        self.assertTrue(any("cpu request" in r for r in d["cpu_reasons"]))
        self.assertEqual(d["reasons"], d["mem_reasons"] + d["cpu_reasons"])


class TimestampParsing(unittest.TestCase):
    def test_rfc3339_forms(self):
        self.assertEqual(A._parse_k8s_time("2026-09-02T17:38:23Z"),
                         datetime(2026, 9, 2, 17, 38, 23, tzinfo=timezone.utc))
        self.assertIsNotNone(A._parse_k8s_time("2026-09-02T17:38:23.904Z"))
        self.assertIsNone(A._parse_k8s_time(None))
        self.assertIsNone(A._parse_k8s_time("not-a-time"))


if __name__ == "__main__":
    unittest.main()
