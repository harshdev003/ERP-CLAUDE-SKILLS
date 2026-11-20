#!/usr/bin/env python3
"""Cluster-wide pod right-sizing auditor.

Compares each workload's declared CPU/memory *requests & limits* (from the spec)
against *actual* usage (`kubectl top`) and produces a ranked right-sizing report.

Two directions are flagged, PER RESOURCE — cpu and memory are classified
independently, because the same container is routinely wrong in both directions
at once (a model server pinning CPU it never touches while its weights creep
past the memory request):
  * OVER-PROVISIONED   — request >> peak actual (reclaimable headroom).
  * UNDER-PROVISIONED  — no request set, running above request, or near limit
    (OOM / eviction / noisy-neighbor risk).
A single container-level verdict has to pick one of the two, and whichever it
picks deletes the other resource from the report and from the roll-up totals —
silently, since the roll-up still looks complete.

Replica ages are reported alongside the peaks. `kubectl top` measures RSS at
whatever age the replicas happen to be, and a Deployment rolls its replicas
together, so a tight spread across replicas means "equally old" and not
necessarily "stable". Where every replica shares an age the reading is marked
NOT CHARACTERISED; where ages differ, the oldest-vs-youngest delta is reported
as a growth signal.

Effective requests/limits are LimitRange-aware: a container that omits a request
or limit inherits its namespace's `type: Container` LimitRange default
(`defaultRequest` / `default`) exactly as the admission controller would — plus
the standard request-defaults-to-limit copy. So a workload covered by a
LimitRange is scheduled with real requests and must NOT be flagged "no requests
set"; classification and reclaim math run against these effective values, and
every value sourced from a LimitRange is noted in the finding.

Read-only. This script never mutates the cluster — it only runs `kubectl get`
and `kubectl top`. Any resize is a separate, reviewed step; see
references/resize-apply-guide.md.

Precedent + method: PROD-3717 (the aura-ai model-pod pass), PROD-3722 (this skill),
AI Knowledge -> Tools/Kubernetes.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

# --- Namespaces we treat conservatively (control-plane / cluster agents). ------
# Flagged for observation but kept OUT of the aggressive reclaim roll-up and
# labelled advisory — cutting these is a control-plane risk, not a quick win.
INFRA_NS_EXACT = {
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "sealed-secrets",
    "monitoring",
    "istio-system",
    "gmp-system",
    "gke-gmp-system",
    "gatekeeper-system",
}
INFRA_NS_PREFIXES = ("gke-managed-", "gke-gmp", "gke-mcs")

MiB = 1024 * 1024
GiB = 1024 * 1024 * 1024


def is_infra_ns(ns: str) -> bool:
    if ns in INFRA_NS_EXACT:
        return True
    return any(ns.startswith(p) for p in INFRA_NS_PREFIXES)


# --- Kubernetes quantity parsing ----------------------------------------------
# CPU is normalised to millicores (m). Memory is normalised to bytes.

_CPU_SUFFIX = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9}
_MEM_BIN = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}
_MEM_DEC = {"k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18}


def parse_cpu_millicores(q: str | None) -> float | None:
    """'250m' -> 250, '1' -> 1000, '500000000n' -> 500. None/''/'<unknown>' -> None."""
    if not q or q in ("<unknown>", "<none>"):
        return None
    q = q.strip()
    m = re.fullmatch(r"([0-9.eE+-]+)([a-zA-Zµ]*)", q)
    if not m:
        return None
    val = float(m.group(1))
    suf = m.group(2)
    if suf in ("", None):
        return val * 1000.0  # cores -> millicores
    if suf in ("u", "µ"):
        return val * 1e-6 * 1000.0
    if suf in _CPU_SUFFIX:
        return val * _CPU_SUFFIX[suf] * 1000.0
    return None


def parse_mem_bytes(q: str | None) -> float | None:
    """'2000Mi' -> bytes, '1Gi' -> bytes, '512M' -> bytes, '1e9' -> bytes."""
    if not q or q in ("<unknown>", "<none>"):
        return None
    q = q.strip()
    m = re.fullmatch(r"([0-9.eE+-]+)([a-zA-Z]*)", q)
    if not m:
        return None
    val = float(m.group(1))
    suf = m.group(2)
    if suf in ("", None):
        return val  # bytes
    if suf in _MEM_BIN:
        return val * _MEM_BIN[suf]
    if suf in _MEM_DEC:
        return val * _MEM_DEC[suf]
    return None


def fmt_cpu(millicores: float | None) -> str:
    """Always millicores. A bare '3' for 3000m sits in tables next to '125m' and
    reads as 3 millicores — a 1000x misread of the exact figure this report exists
    to get right. Totals use fmt_vcpu instead, which names its unit."""
    if millicores is None:
        return "—"
    return f"{int(round(millicores))}m"


def fmt_vcpu(millicores: float | None) -> str:
    """Cluster-scale CPU, in the unit people reason about capacity in."""
    if millicores is None:
        return "—"
    return f"{millicores / 1000:.1f} vCPU ({int(round(millicores))}m)"


def fmt_mem(nbytes: float | None) -> str:
    if nbytes is None:
        return "—"
    if nbytes >= GiB and (nbytes / GiB) >= 1:
        g = nbytes / GiB
        return f"{g:.1f}Gi" if g % 1 else f"{int(g)}Gi"
    mi = nbytes / MiB
    return f"{int(round(mi))}Mi"


# --- kubectl plumbing ----------------------------------------------------------

def kubectl(args: list[str], context: str | None) -> str:
    cmd = ["kubectl"]
    if context:
        cmd += ["--context", context]
    cmd += args
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed:\n{res.stderr.strip()}")
    return res.stdout


def kubectl_json(args: list[str], context: str | None) -> dict:
    return json.loads(kubectl(args + ["-o", "json"], context))


# --- Data model ----------------------------------------------------------------

@dataclass
class ContainerSpec:
    """Requests/limits *explicitly* declared on the container in the workload spec."""
    req_cpu_m: float | None = None
    req_mem_b: float | None = None
    lim_cpu_m: float | None = None
    lim_mem_b: float | None = None


@dataclass
class LimitRangeDefaults:
    """Per-namespace `type: Container` LimitRange defaults injected at admission.

    `defaultRequest` fills a missing request; `default` fills a missing limit.
    """
    def_req_cpu_m: float | None = None
    def_req_mem_b: float | None = None
    def_lim_cpu_m: float | None = None
    def_lim_mem_b: float | None = None

    @property
    def any(self) -> bool:
        return any(v is not None for v in (
            self.def_req_cpu_m, self.def_req_mem_b, self.def_lim_cpu_m, self.def_lim_mem_b,
        ))


# Provenance of an effective value.
#   "spec"       — declared on the container itself
#   "limitrange" — injected by the namespace LimitRange default
#   "limit"      — a request copied from the container's own explicit limit
#                  (Kubernetes' request-defaults-to-limit behaviour)
#   None         — genuinely unset
@dataclass
class Effective:
    """Requests/limits the scheduler actually reserves, after LimitRange + copy rules."""
    req_cpu_m: float | None = None
    req_cpu_src: str | None = None
    req_mem_b: float | None = None
    req_mem_src: str | None = None
    lim_cpu_m: float | None = None
    lim_cpu_src: str | None = None
    lim_mem_b: float | None = None
    lim_mem_src: str | None = None


def _resolve_one(
    er: float | None, el: float | None, dr: float | None, dl: float | None,
) -> tuple[float | None, str | None, float | None, str | None]:
    """Resolve one resource (cpu OR mem) to (eff_req, req_src, eff_lim, lim_src).

    Mirrors how Kubernetes ends up scheduling a request/limit. Limit:

      * explicit → LimitRange `default` → unset

    Request precedence (verified against the Kubernetes resource docs, not obvious):

      1. explicit request                         → "spec"
      2. the container's OWN explicit limit, copied → "limit"
      3. LimitRange `defaultRequest`              → "limitrange"
      4. LimitRange `default` limit, copied        → "limitrange"

    Two subtleties drive this order:

      * The request-defaults-to-limit copy is core Kubernetes, NOT gated on a
        LimitRange — "if you specify a limit … but do not specify any request, and
        no admission-time mechanism has applied a default request … then Kubernetes
        copies the limit … and uses it as the requested value." So a limit-only
        container reserves request == limit even with no LimitRange, and must not
        count as "no requests set".
      * That copy of the container's OWN limit BEATS a LimitRange `defaultRequest`
        (step 2 before step 3): the docs show a container that sets only a limit
        gets "its memory request … set to match its memory limit. Notice that the
        container was not assigned the default memory request value." The
        `defaultRequest` (and the copy from a LimitRange `default` limit) only apply
        when the container declares neither its own request nor its own limit.
    """
    if el is not None:
        eff_lim, lim_src = el, "spec"
    elif dl is not None:
        eff_lim, lim_src = dl, "limitrange"
    else:
        eff_lim, lim_src = None, None

    if er is not None:
        eff_req, req_src = er, "spec"
    elif el is not None:
        eff_req, req_src = el, "limit"          # own-limit copy — beats defaultRequest
    elif dr is not None:
        eff_req, req_src = dr, "limitrange"     # LimitRange defaultRequest
    elif dl is not None:
        eff_req, req_src = dl, "limitrange"     # copy from LimitRange default limit
    else:
        eff_req, req_src = None, None

    return eff_req, req_src, eff_lim, lim_src


def resolve_effective(spec: ContainerSpec, lr: LimitRangeDefaults | None) -> Effective:
    lr = lr or LimitRangeDefaults()
    rq_cpu, rq_cpu_s, lm_cpu, lm_cpu_s = _resolve_one(
        spec.req_cpu_m, spec.lim_cpu_m, lr.def_req_cpu_m, lr.def_lim_cpu_m)
    rq_mem, rq_mem_s, lm_mem, lm_mem_s = _resolve_one(
        spec.req_mem_b, spec.lim_mem_b, lr.def_req_mem_b, lr.def_lim_mem_b)
    return Effective(
        req_cpu_m=rq_cpu, req_cpu_src=rq_cpu_s, req_mem_b=rq_mem, req_mem_src=rq_mem_s,
        lim_cpu_m=lm_cpu, lim_cpu_src=lm_cpu_s, lim_mem_b=lm_mem, lim_mem_src=lm_mem_s,
    )


@dataclass
class PodPeak:
    """Peak reading for ONE replica — kept per-pod so replicas can be compared.

    An aggregate peak across replicas cannot answer the question that matters
    for a long-lived process: is this value steady state, or is it where the
    oldest pod has drifted to? Comparing the oldest replica against the youngest
    is the one comparison that separates the two.
    """
    peak_cpu_m: float = 0.0
    peak_mem_b: float = 0.0


@dataclass
class Usage:
    peak_cpu_m: float = 0.0
    peak_mem_b: float = 0.0
    samples: int = 0
    pods: set[str] = field(default_factory=set)
    per_pod: dict[str, PodPeak] = field(default_factory=dict)


@dataclass
class Workload:
    ns: str
    kind: str
    name: str
    replicas: int
    containers: dict[str, ContainerSpec] = field(default_factory=dict)

    @property
    def infra(self) -> bool:
        return is_infra_ns(self.ns)


def collect_workloads(context: str | None) -> dict[tuple, Workload]:
    """Deployments, StatefulSets, DaemonSets across all namespaces."""
    out: dict[tuple, Workload] = {}
    data = kubectl_json(["get", "deploy,statefulset,daemonset", "-A"], context)
    for it in data.get("items", []):
        kind = it["kind"]
        ns = it["metadata"]["namespace"]
        name = it["metadata"]["name"]
        if kind == "DaemonSet":
            replicas = it.get("status", {}).get("desiredNumberScheduled", 0) or 0
        else:
            replicas = it.get("spec", {}).get("replicas", 1)
            if replicas is None:
                replicas = 1
        wl = Workload(ns=ns, kind=kind, name=name, replicas=int(replicas))
        for c in it["spec"]["template"]["spec"].get("containers", []):
            r = c.get("resources", {}) or {}
            req = r.get("requests", {}) or {}
            lim = r.get("limits", {}) or {}
            wl.containers[c["name"]] = ContainerSpec(
                req_cpu_m=parse_cpu_millicores(req.get("cpu")),
                req_mem_b=parse_mem_bytes(req.get("memory")),
                lim_cpu_m=parse_cpu_millicores(lim.get("cpu")),
                lim_mem_b=parse_mem_bytes(lim.get("memory")),
            )
        out[(ns, kind, name)] = wl
    return out


def collect_limitranges(
    context: str | None,
) -> tuple[dict[str, LimitRangeDefaults], str | None]:
    """Per-namespace `type: Container` LimitRange defaults (defaultRequest + default).

    Returns (defaults_by_ns, error). Degrades gracefully: if LimitRanges can't be
    read (RBAC, older cluster) it warns and returns an EMPTY map plus a non-None
    error string, so callers can distinguish a genuinely LimitRange-free cluster
    ({}, None) from a failed read ({}, "<err>"). That distinction matters — on a
    failed read the audit silently reverts to bare-spec classification and the
    false "no requests set" flags this whole change removes, so the report must say
    it's degraded rather than claim no defaults exist.

    Multiple LimitRanges / limit entries in one namespace are merged first-non-null
    wins in name order — matching the admission controller, which stops filling a
    resource once any range has provided a default for it.
    """
    out: dict[str, LimitRangeDefaults] = {}
    try:
        data = kubectl_json(["get", "limitrange", "-A"], context)
    except RuntimeError as e:
        msg = (str(e).splitlines() or ["kubectl get limitrange failed"])[0]
        print(f"WARNING: could not read LimitRanges ({msg}); proceeding on explicit "
              "specs only — effective-request math is degraded and 'no requests set' "
              "flags may be false", file=sys.stderr)
        return out, msg
    items = sorted(
        data.get("items", []),
        key=lambda it: (it["metadata"]["namespace"], it["metadata"].get("name", "")),
    )
    for it in items:
        ns = it["metadata"]["namespace"]
        d = out.setdefault(ns, LimitRangeDefaults())
        for lim in it.get("spec", {}).get("limits", []) or []:
            if lim.get("type") != "Container":
                continue
            dr = lim.get("defaultRequest", {}) or {}
            dl = lim.get("default", {}) or {}
            if d.def_req_cpu_m is None:
                d.def_req_cpu_m = parse_cpu_millicores(dr.get("cpu"))
            if d.def_req_mem_b is None:
                d.def_req_mem_b = parse_mem_bytes(dr.get("memory"))
            if d.def_lim_cpu_m is None:
                d.def_lim_cpu_m = parse_cpu_millicores(dl.get("cpu"))
            if d.def_lim_mem_b is None:
                d.def_lim_mem_b = parse_mem_bytes(dl.get("memory"))
    return out, None


def _parse_k8s_time(ts: str | None) -> datetime | None:
    """RFC3339 timestamp from the API ('2026-09-02T17:38:23Z') -> aware datetime."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_pod_index(context: str | None) -> tuple[dict[tuple, tuple], dict[tuple, datetime]]:
    """Map (ns, podname) -> (ns, kind, workload-name) via ownerReferences, plus start times.

    ReplicaSet owners are resolved to their Deployment by stripping the
    trailing pod-template-hash segment (RS name = '<deploy>-<hash>').

    The second return value is (ns, podname) -> pod start time. Age is not a
    curiosity here: a Deployment rolls all its replicas together, so replicas
    normally share an age and a tight spread of readings across them says
    "equally old", not "stable". Without the timestamps the report cannot tell
    those apart. `status.startTime` is when the kubelet accepted the pod;
    `metadata.creationTimestamp` is the fallback for a pod not yet started.
    """
    idx: dict[tuple, tuple] = {}
    started: dict[tuple, datetime] = {}
    data = kubectl_json(["get", "pods", "-A"], context)
    for p in data.get("items", []):
        ns = p["metadata"]["namespace"]
        pod = p["metadata"]["name"]
        t = (_parse_k8s_time(p.get("status", {}).get("startTime"))
             or _parse_k8s_time(p["metadata"].get("creationTimestamp")))
        if t is not None:
            started[(ns, pod)] = t
        for o in p["metadata"].get("ownerReferences", []):
            kind = o.get("kind")
            oname = o.get("name", "")
            if kind == "ReplicaSet":
                dep = oname.rsplit("-", 1)[0]  # strip pod-template-hash
                idx[(ns, pod)] = (ns, "Deployment", dep)
            elif kind == "StatefulSet":
                idx[(ns, pod)] = (ns, "StatefulSet", oname)
            elif kind == "DaemonSet":
                idx[(ns, pod)] = (ns, "DaemonSet", oname)
            # Jobs / Nodes / bare pods are not right-sizing targets here.
            break
    return idx, started


def sample_usage(
    context: str | None,
    pod_index: dict[tuple, tuple],
    samples: int,
    interval: float,
) -> dict[tuple, Usage]:
    """Peak per-replica usage per (workload-key, container) across N samples.

    `kubectl top` is instantaneous, so we sample a few times and keep the MAX
    single-pod reading per container — a bursty service isn't under-sized on one
    reading, and memory headroom is taken over peak, not average.
    """
    usage: dict[tuple, Usage] = {}
    unmapped: dict[tuple, Usage] = {}
    for s in range(samples):
        if s > 0:
            time.sleep(interval)
        raw = kubectl(["top", "pods", "-A", "--containers", "--no-headers"], context)
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            ns, pod, container, cpu_s, mem_s = parts[0], parts[1], parts[2], parts[3], parts[4]
            cpu = parse_cpu_millicores(cpu_s) or 0.0
            mem = parse_mem_bytes(mem_s) or 0.0
            wl_key = pod_index.get((ns, pod))
            target = usage if wl_key else unmapped
            key = (wl_key or (ns, "Pod", pod), container)
            u = target.setdefault(key, Usage())
            u.peak_cpu_m = max(u.peak_cpu_m, cpu)
            u.peak_mem_b = max(u.peak_mem_b, mem)
            u.pods.add(pod)
            pk = u.per_pod.setdefault(pod, PodPeak())
            pk.peak_cpu_m = max(pk.peak_cpu_m, cpu)
            pk.peak_mem_b = max(pk.peak_mem_b, mem)
            u.samples += 1
    return usage


# --- Replica age / growth ------------------------------------------------------
# A Deployment rolls its replicas together, so they almost always share an age.
# That makes a tight spread of readings across replicas ambiguous: it can mean
# the workload is at steady state, or it can mean every replica has accumulated
# for the same number of days. `kubectl top` alone cannot tell those apart, and
# reading the second as the first sizes a request off a number that is still
# climbing. So the audit reports ages, refuses to call a uniform-age sample
# "characterised", and — where ages DO differ — reports the oldest-vs-youngest
# delta, which is the single comparison that exposes accumulation.

# Ages count as uniform when the spread is under an hour, or under this fraction
# of the oldest replica's age (a 7d/6.9d pair says nothing; a 7d/2d pair does).
AGE_UNIFORM_FRAC = 0.25
AGE_UNIFORM_MIN_SPREAD_H = 1.0
# A growth signal is only worth reporting when it is big enough to move a sizing
# decision: at least this much, and this share of the youngest replica's reading.
GROWTH_MIN_MEM_B = 64 * MiB
GROWTH_MIN_MEM_FRAC = 0.15


@dataclass
class AgeStats:
    """Replica ages and the oldest-vs-youngest reading delta for one container."""
    ages_h: list[float] = field(default_factory=list)
    oldest_pod: str | None = None
    youngest_pod: str | None = None
    growth_mem_b: float | None = None   # oldest peak − youngest peak
    growth_cpu_m: float | None = None
    age_delta_h: float | None = None    # oldest age − youngest age
    youngest_mem_b: float | None = None  # youngest replica's reading (growth denominator)

    @property
    def known(self) -> bool:
        return bool(self.ages_h)

    @property
    def min_h(self) -> float | None:
        return min(self.ages_h) if self.ages_h else None

    @property
    def max_h(self) -> float | None:
        return max(self.ages_h) if self.ages_h else None

    @property
    def spread_h(self) -> float | None:
        if not self.ages_h:
            return None
        return max(self.ages_h) - min(self.ages_h)

    @property
    def comparable(self) -> bool:
        """Two or more replicas with known ages — the minimum for any comparison."""
        return len(self.ages_h) >= 2

    @property
    def uniform(self) -> bool:
        """True when the readings cannot distinguish steady state from accumulation.

        A single replica is uniform by definition: there is nothing to compare it
        against, so its reading is equally unable to rule accumulation out.
        """
        if not self.known:
            return False
        if not self.comparable:
            return True
        spread, oldest = self.spread_h, self.max_h
        return spread <= max(AGE_UNIFORM_MIN_SPREAD_H, AGE_UNIFORM_FRAC * (oldest or 0.0))

    @property
    def mem_growth_material(self) -> bool:
        """Oldest-vs-youngest memory delta big enough to change a sizing call."""
        if self.uniform or self.growth_mem_b is None or self.growth_mem_b <= 0:
            return False
        if self.growth_mem_b < GROWTH_MIN_MEM_B:
            return False
        younger = self.youngest_mem_b or 0.0
        return younger <= 0 or self.growth_mem_b >= GROWTH_MIN_MEM_FRAC * younger

    @property
    def mem_growth_per_day_b(self) -> float | None:
        if self.growth_mem_b is None or not self.age_delta_h:
            return None
        return self.growth_mem_b / (self.age_delta_h / 24.0)


def compute_age_stats(u: Usage | None, started: dict[tuple, datetime], ns: str,
                      now: datetime) -> AgeStats:
    """Ages of the replicas actually sampled, plus the oldest-vs-youngest deltas."""
    st = AgeStats()
    if not u or not u.per_pod:
        return st
    aged: list[tuple[float, str]] = []
    for pod in u.per_pod:
        t = started.get((ns, pod))
        if t is None:
            continue
        aged.append(((now - t).total_seconds() / 3600.0, pod))
    if not aged:
        return st
    aged.sort()
    st.ages_h = [a for a, _ in aged]
    st.youngest_pod = aged[0][1]
    st.oldest_pod = aged[-1][1]
    if len(aged) >= 2:
        young, old = u.per_pod[st.youngest_pod], u.per_pod[st.oldest_pod]
        st.growth_mem_b = old.peak_mem_b - young.peak_mem_b
        st.growth_cpu_m = old.peak_cpu_m - young.peak_cpu_m
        st.youngest_mem_b = young.peak_mem_b
        st.age_delta_h = aged[-1][0] - aged[0][0]
    return st


def fmt_age(hours: float | None) -> str:
    if hours is None:
        return "—"
    if hours < 1:
        return f"{int(round(hours * 60))}m"
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.1f}d"


def fmt_age_range(st: AgeStats) -> str:
    if not st.known:
        return "—"
    lo, hi = fmt_age(st.min_h), fmt_age(st.max_h)
    return lo if lo == hi else f"{lo}–{hi}"


# --- Right-sizing logic --------------------------------------------------------

def round_up(value: float, step: float) -> float:
    return math.ceil(value / step) * step


def suggest_mem(peak_b: float, headroom: float, floor_mi: float) -> float:
    step = 64 * MiB if peak_b < 1 * GiB else 128 * MiB
    return max(floor_mi * MiB, round_up(peak_b * headroom, step))


def suggest_cpu(peak_m: float, headroom: float, floor_m: float) -> float:
    return max(floor_m, round_up(peak_m * headroom, 25))


@dataclass
class Finding:
    ns: str
    kind: str
    name: str
    container: str
    replicas: int
    infra: bool
    # effective specs (explicit, else LimitRange default, else request-from-limit) —
    # this is what the scheduler reserves and what all classification/reclaim math uses
    req_cpu_m: float | None
    req_mem_b: float | None
    lim_cpu_m: float | None
    lim_mem_b: float | None
    # provenance of each effective value: "spec" | "limitrange" | "limit" | None
    req_cpu_src: str | None
    req_mem_src: str | None
    lim_cpu_src: str | None
    lim_mem_src: str | None
    # raw values declared on the container itself (None where inherited)
    spec_req_cpu_m: float | None
    spec_req_mem_b: float | None
    spec_lim_cpu_m: float | None
    spec_lim_mem_b: float | None
    # actuals (peak per replica)
    peak_cpu_m: float | None
    peak_mem_b: float | None
    pods_seen: int
    # verdict — PER RESOURCE, not per container. A container can be genuinely
    # over-provisioned on CPU and genuinely at risk on memory at the same time;
    # a single container-level verdict has to pick one, and whichever it picks
    # deletes the other from the report and from the roll-up totals.
    cpu_direction: str = "ok"        # over | under | ok | no-data
    mem_direction: str = "ok"
    cpu_reasons: list[str] = field(default_factory=list)
    mem_reasons: list[str] = field(default_factory=list)
    sugg_cpu_m: float | None = None
    sugg_mem_b: float | None = None
    reclaim_cpu_m: float = 0.0       # (req - sugg) * replicas
    reclaim_mem_b: float = 0.0
    age: AgeStats = field(default_factory=AgeStats)

    @property
    def direction(self) -> str:
        """Container-level summary of the two per-resource verdicts.

        Kept for readers that want one word, but it is DERIVED — never the thing
        classification is done on. `mixed` is new: over on one resource, risky on
        the other. A caller switching on this field must handle it, or fall back
        to the per-resource verdicts, which are the authoritative ones.
        """
        d = {self.cpu_direction, self.mem_direction}
        if "over" in d and "under" in d:
            return "mixed"
        if "under" in d:
            return "under"
        if "over" in d:
            return "over"
        if d == {"no-data"}:
            return "no-data"
        return "ok"

    @property
    def reasons(self) -> list[str]:
        return self.mem_reasons + self.cpu_reasons

    @property
    def over_resources(self) -> list[str]:
        return [r for r, d in (("mem", self.mem_direction), ("cpu", self.cpu_direction))
                if d == "over"]

    @property
    def under_resources(self) -> list[str]:
        return [r for r, d in (("mem", self.mem_direction), ("cpu", self.cpu_direction))
                if d == "under"]


def _lr_suffix(src: str | None) -> str:
    """Reason annotation when an effective value was injected by a LimitRange."""
    return " [LimitRange default]" if src == "limitrange" else ""


def analyze(
    workloads: dict[tuple, Workload],
    usage: dict[tuple, Usage],
    limitranges: dict[str, LimitRangeDefaults],
    pod_started: dict[tuple, datetime],
    over_ratio: float,
    cpu_headroom: float,
    mem_headroom: float,
    cpu_floor_m: float,
    mem_floor_mi: float,
    near_limit_frac: float,
    min_reclaim_mem_mi: float,
    min_reclaim_cpu_m: float,
    now: datetime | None = None,
) -> list[Finding]:
    """Classify every container's CPU and memory INDEPENDENTLY.

    The two resources fail in different directions for the same container all the
    time — a model server that pins 3 vCPU it never touches while its weights
    creep past the memory request is the ordinary case, not a corner one. Judging
    the container as a whole forces a single verdict, and the resource that loses
    is dropped from the report entirely: its reclaim never reaches the roll-up and
    its risk never reaches the risky table. Nothing errors; the totals just come
    out confidently short.
    """
    now = now or datetime.now(timezone.utc)
    findings: list[Finding] = []
    for (ns, kind, name), wl in workloads.items():
        lr = limitranges.get(ns)
        for cname, spec in wl.containers.items():
            eff = resolve_effective(spec, lr)
            u = usage.get(((ns, kind, name), cname))
            peak_cpu = u.peak_cpu_m if u else None
            peak_mem = u.peak_mem_b if u else None
            pods_seen = len(u.pods) if u else 0
            f = Finding(
                ns=ns, kind=kind, name=name, container=cname, replicas=wl.replicas,
                infra=wl.infra,
                req_cpu_m=eff.req_cpu_m, req_mem_b=eff.req_mem_b,
                lim_cpu_m=eff.lim_cpu_m, lim_mem_b=eff.lim_mem_b,
                req_cpu_src=eff.req_cpu_src, req_mem_src=eff.req_mem_src,
                lim_cpu_src=eff.lim_cpu_src, lim_mem_src=eff.lim_mem_src,
                spec_req_cpu_m=spec.req_cpu_m, spec_req_mem_b=spec.req_mem_b,
                spec_lim_cpu_m=spec.lim_cpu_m, spec_lim_mem_b=spec.lim_mem_b,
                peak_cpu_m=peak_cpu, peak_mem_b=peak_mem, pods_seen=pods_seen,
                age=compute_age_stats(u, pod_started, ns, now),
            )

            # --- risk direction, per resource — measured against EFFECTIVE values ---
            # "no request set" is judged per resource too: a container scheduled with
            # a memory request but no CPU reservation is a real noisy-neighbor risk,
            # and the old container-level test (BOTH unset) never saw it. In the prod
            # cluster that is the common shape — aura-ai / aura-data carry a mem-only
            # LimitRange — so expect this to surface rows the previous version hid.
            if eff.req_mem_b is None:
                f.mem_direction = "under"
                f.mem_reasons.append("no memory request set (eviction risk)")
            if eff.req_cpu_m is None:
                f.cpu_direction = "under"
                f.cpu_reasons.append("no cpu request set (noisy-neighbor risk)")

            if peak_mem is not None and eff.lim_mem_b and peak_mem >= near_limit_frac * eff.lim_mem_b:
                f.mem_direction = "under"
                f.mem_reasons.append(
                    f"mem peak ≥{int(near_limit_frac*100)}% of limit (OOM risk)"
                    + _lr_suffix(eff.lim_mem_src))
            if peak_cpu is not None and eff.lim_cpu_m and peak_cpu >= near_limit_frac * eff.lim_cpu_m:
                f.cpu_direction = "under"
                f.cpu_reasons.append(
                    f"cpu peak ≥{int(near_limit_frac*100)}% of limit (throttling)"
                    + _lr_suffix(eff.lim_cpu_src))
            if peak_mem is not None and eff.req_mem_b and peak_mem > eff.req_mem_b:
                f.mem_direction = "under"
                f.mem_reasons.append("mem peak above request" + _lr_suffix(eff.req_mem_src))
            if peak_cpu is not None and eff.req_cpu_m and peak_cpu > eff.req_cpu_m:
                # cpu over-request is soft (compressible) — it throttles, it doesn't evict
                f.cpu_direction = "under"
                f.cpu_reasons.append("cpu peak above request (soft)" + _lr_suffix(eff.req_cpu_src))

            # --- over-provisioned, per resource ---
            # Each resource is analysed on its own: memory being risky no longer
            # suppresses the CPU reclaim (or the reverse). A resource already flagged
            # risky is skipped — request/peak cannot exceed the ratio once peak is at
            # or above the request, so this only guards the near-limit case.
            mem_ratio = (eff.req_mem_b / peak_mem
                         if eff.req_mem_b and peak_mem else None)
            cpu_ratio = (eff.req_cpu_m / peak_cpu
                         if eff.req_cpu_m and peak_cpu else None)
            mem_flag = (f.mem_direction != "under" and mem_ratio is not None
                        and mem_ratio > over_ratio)
            cpu_flag = (f.cpu_direction != "under" and cpu_ratio is not None
                        and cpu_ratio > over_ratio)

            # Candidate suggestions, computed regardless of which resource tripped the
            # ratio. A resource under the ratio still gets trimmed alongside one that
            # tripped it (unchanged from before) — dropping those would quietly shrink
            # the roll-up, which is the same failure this change exists to remove.
            sm = sc = None
            if f.mem_direction != "under" and eff.req_mem_b and peak_mem is not None:
                cand = suggest_mem(peak_mem, mem_headroom, mem_floor_mi)
                if cand < eff.req_mem_b and (eff.req_mem_b - cand) >= min_reclaim_mem_mi * MiB:
                    sm = cand
            if f.cpu_direction != "under" and eff.req_cpu_m and peak_cpu is not None:
                cand = suggest_cpu(peak_cpu, cpu_headroom, cpu_floor_m)
                if cand < eff.req_cpu_m and (eff.req_cpu_m - cand) >= min_reclaim_cpu_m:
                    sc = cand

            if (mem_flag or cpu_flag) and (sm is not None or sc is not None):
                if sm is not None:
                    f.sugg_mem_b = sm
                    f.reclaim_mem_b = (eff.req_mem_b - sm) * wl.replicas
                    f.mem_direction = "over"
                    f.mem_reasons.append(
                        f"mem request {mem_ratio:.0f}× peak" if mem_flag else
                        f"mem trimmed alongside ({mem_ratio:.1f}× peak — under the "
                        f"{over_ratio:g}× flag)")
                if sc is not None:
                    f.sugg_cpu_m = sc
                    f.reclaim_cpu_m = (eff.req_cpu_m - sc) * wl.replicas
                    f.cpu_direction = "over"
                    f.cpu_reasons.append(
                        f"cpu request {cpu_ratio:.0f}× peak" if cpu_flag else
                        f"cpu trimmed alongside ({cpu_ratio:.1f}× peak — under the "
                        f"{over_ratio:g}× flag)")
                # reclaiming a LimitRange-defaulted request means overriding it with an
                # explicit request (or lowering the LimitRange), not a plain trim.
                if sm is not None and f.req_mem_src == "limitrange":
                    f.mem_reasons.append(
                        "effective request from LimitRange default — reclaim by setting "
                        "an explicit request or adjusting the namespace LimitRange")
                if sc is not None and f.req_cpu_src == "limitrange":
                    f.cpu_reasons.append(
                        "effective request from LimitRange default — reclaim by setting "
                        "an explicit request or adjusting the namespace LimitRange")

            if f.mem_direction == "ok" and peak_mem is None:
                f.mem_direction = "no-data"
            if f.cpu_direction == "ok" and peak_cpu is None:
                f.cpu_direction = "no-data"
            if f.direction == "no-data":
                f.mem_reasons.append("no running pods / no metrics")

            findings.append(f)
    return findings


# --- Rendering -----------------------------------------------------------------

def render_markdown(
    findings: list[Finding],
    context: str,
    samples: int,
    params: dict,
    limitranges: dict[str, LimitRangeDefaults],
    lr_error: str | None = None,
) -> str:
    over = [f for f in findings if f.over_resources and not f.infra]
    over_infra = [f for f in findings if f.over_resources and f.infra]
    under = [f for f in findings if f.under_resources]
    nodata = [f for f in findings if f.direction == "no-data"]
    # A container over on one resource and risky on the other appears in BOTH tables.
    # That is the fix working, not a contradiction — say so, or the next reader
    # "reconciles" it by collapsing back to one verdict per container.
    both = [f for f in findings if f.over_resources and f.under_resources]

    lr_ns = sorted(ns for ns, d in limitranges.items() if d.any)
    lr_used = any(
        "limitrange" in (f.req_cpu_src, f.req_mem_src, f.lim_cpu_src, f.lim_mem_src)
        for f in findings
    )

    over.sort(key=lambda f: (f.reclaim_mem_b, f.reclaim_cpu_m), reverse=True)
    over_infra.sort(key=lambda f: (f.reclaim_mem_b, f.reclaim_cpu_m), reverse=True)
    # risky: no-requests first, then near-limit
    under.sort(key=lambda f: (0 if any("request set" in r for r in f.reasons) else 1,
                              f.ns, f.name))

    total_mem = sum(f.reclaim_mem_b for f in over)
    total_cpu = sum(f.reclaim_cpu_m for f in over)
    mem_over_n = sum(1 for f in over if "mem" in f.over_resources)
    cpu_over_n = sum(1 for f in over if "cpu" in f.over_resources)
    infra_mem = sum(f.reclaim_mem_b for f in over_infra)
    infra_cpu = sum(f.reclaim_cpu_m for f in over_infra)

    # Sizing candidates whose replicas are all the same age — their readings cannot
    # be called steady state, so their suggestions are not characterised.
    sized = [f for f in findings if (f.over_resources or f.under_resources) and not f.infra]
    uniform = [f for f in sized if f.age.known and f.age.uniform]
    growers = sorted(
        (f for f in findings if f.age.mem_growth_material),
        key=lambda f: f.age.growth_mem_b or 0.0, reverse=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L: list[str] = []
    L.append("# Cluster-wide pod right-sizing report")
    L.append("")
    L.append(f"- **Cluster / context:** `{context}`")
    L.append(f"- **Generated:** {now}")
    L.append(f"- **Sampling:** {samples} × `kubectl top` (peak per replica)")
    L.append(
        f"- **Thresholds:** over-provisioned when request/peak > {params['over_ratio']}× · "
        f"headroom cpu ×{params['cpu_headroom']}, mem ×{params['mem_headroom']} · "
        f"floors {fmt_cpu(params['cpu_floor_m'])}/{int(params['mem_floor_mi'])}Mi"
    )
    L.append(
        "- **Per-resource verdicts:** CPU and memory are classified independently. A "
        "container can be over-provisioned on one and at risk on the other, and will "
        "then appear in both tables below — each row names the resource it concerns."
    )
    if lr_error:
        L.append(
            f"- **LimitRange-aware:** ⚠️ **could not read LimitRanges** (`{lr_error}`) — this "
            f"report fell back to **bare-spec** classification. Requests/limits a container "
            f"inherits from a namespace LimitRange are NOT reflected, so any 'no requests set' "
            f"flag below may be false. Re-run with access to `kubectl get limitrange -A`."
        )
    elif lr_ns:
        L.append(
            f"- **LimitRange-aware:** effective requests/limits fall back to each namespace's "
            f"`type: Container` LimitRange default when a container omits its own — "
            f"{len(lr_ns)} namespace(s) define defaults: {', '.join(f'`{n}`' for n in lr_ns)}. "
            f"Values marked † below come from a LimitRange, not the workload spec."
        )
    else:
        L.append("- **LimitRange-aware:** no `type: Container` LimitRange defaults found in this cluster.")
    L.append("")
    L.append("## Cluster roll-up (application namespaces)")
    L.append("")
    L.append(f"- **Reclaimable memory:** **{fmt_mem(total_mem)}** across {mem_over_n} containers over-provisioned on memory")
    L.append(f"- **Reclaimable CPU:** **{fmt_vcpu(total_cpu)}** (summed requests) across {cpu_over_n} containers over-provisioned on CPU")
    L.append(f"- **Under-provisioned / risky:** {len(under)} containers")
    L.append(f"- **No data (not running / no metrics):** {len(nodata)} containers")
    if both:
        names = sorted(f"`{f.name}/{f.container}`" for f in both)
        shown = ", ".join(names[:8]) + (f" (+{len(names) - 8} more)" if len(names) > 8 else "")
        L.append(
            f"- **Over on one resource, risky on the other:** {len(both)} container(s) — "
            f"{shown}. These appear in both tables by design; their reclaim IS counted in "
            f"the totals above."
        )
    if uniform:
        L.append(
            f"- ⚠️ **Not characterised — uniform replica ages:** {len(uniform)} of {len(sized)} "
            f"sized containers have replicas that are all the same age, so their readings "
            f"cannot distinguish steady state from accumulation (see *Replica age* below)."
        )
    if over_infra:
        L.append(
            f"- **Infra namespaces (advisory only, not in totals):** "
            f"~{fmt_mem(infra_mem)} mem / {fmt_vcpu(infra_cpu)} cpu potential — review conservatively"
        )
    L.append("")

    # --- Over-provisioned ---
    L.append("## Over-provisioned — ranked by reclaimable memory")
    L.append("")
    if over:
        L.append("| # | Workload | NS | Kind | Repl | Container | Over on | Mem req → suggest (peak) | CPU req → suggest (peak) | Reclaim mem | Reclaim cpu | Replica age |")
        L.append("|---|---|---|---|---:|---|---|---|---|---:|---:|---|")
        for i, f in enumerate(over, 1):
            mem_cell = _delta_cell(f.req_mem_b, f.sugg_mem_b, f.peak_mem_b, fmt_mem, f.req_mem_src)
            cpu_cell = _delta_cell(f.req_cpu_m, f.sugg_cpu_m, f.peak_cpu_m, fmt_cpu, f.req_cpu_src)
            L.append(
                f"| {i} | {f.name} | {f.ns} | {f.kind} | {f.replicas} | {f.container} | "
                f"{'+'.join(f.over_resources)} | {mem_cell} | {cpu_cell} | "
                f"{fmt_mem(f.reclaim_mem_b) if f.reclaim_mem_b else '—'} | "
                f"{fmt_cpu(f.reclaim_cpu_m) if f.reclaim_cpu_m else '—'} | "
                f"{_age_cell(f.age)} |"
            )
    else:
        L.append("_None over the threshold._")
    if lr_used:
        L.append("")
        L.append("_† request drawn from a namespace LimitRange default — reclaim it by setting an "
                 "explicit lower request on the workload (or adjusting the LimitRange), not by a "
                 "plain trim; a per-workload override is the surgical move._")
    L.append("")

    # --- Top CPU reclaims -------------------------------------------------------
    # The table above ranks by memory, so a CPU-dominant container with no memory
    # reclaim sorts below every container with any. On the run that surfaced this,
    # the single largest CPU reclaim in the cluster would have sat at the bottom.
    cpu_ranked = [f for f in over if f.reclaim_cpu_m > 0]
    cpu_ranked.sort(key=lambda f: f.reclaim_cpu_m, reverse=True)
    if cpu_ranked:
        L.append("## Over-provisioned — ranked by reclaimable CPU")
        L.append("")
        L.append("| # | Workload | NS | Repl | Container | CPU req → suggest (peak) | Reclaim cpu | Replica age |")
        L.append("|---|---|---|---:|---|---|---:|---|")
        for i, f in enumerate(cpu_ranked[:15], 1):
            cpu_cell = _delta_cell(f.req_cpu_m, f.sugg_cpu_m, f.peak_cpu_m, fmt_cpu, f.req_cpu_src)
            L.append(
                f"| {i} | {f.name} | {f.ns} | {f.replicas} | {f.container} | {cpu_cell} | "
                f"{fmt_cpu(f.reclaim_cpu_m)} | {_age_cell(f.age)} |"
            )
        L.append("")

    # --- Under-provisioned ---
    L.append("## Under-provisioned / risky")
    L.append("")
    if under:
        L.append("| Workload | NS | Kind | Container | Resource | Issue | Peak (cpu/mem) | Request (cpu/mem) | Limit (cpu/mem) | Replica age |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for f in under:
            req_cell = (f"{_lr_mark(fmt_cpu(f.req_cpu_m), f.req_cpu_src)}/"
                        f"{_lr_mark(fmt_mem(f.req_mem_b), f.req_mem_src)}")
            lim_cell = (f"{_lr_mark(fmt_cpu(f.lim_cpu_m), f.lim_cpu_src)}/"
                        f"{_lr_mark(fmt_mem(f.lim_mem_b), f.lim_mem_src)}")
            risky_reasons = ((f.mem_reasons if f.mem_direction == "under" else [])
                             + (f.cpu_reasons if f.cpu_direction == "under" else []))
            L.append(
                f"| {f.name} | {f.ns} | {f.kind} | {f.container} | "
                f"{'+'.join(f.under_resources)} | {'; '.join(risky_reasons)} | "
                f"{fmt_cpu(f.peak_cpu_m)}/{fmt_mem(f.peak_mem_b)} | "
                f"{req_cell} | {lim_cell} | {_age_cell(f.age)} |"
            )
    else:
        L.append("_None flagged._")
    if lr_used:
        L.append("")
        L.append("_† value comes from the namespace `type: Container` LimitRange default, not the "
                 "workload spec. A LimitRange-defaulted limit is the real burst ceiling — factor it "
                 "into headroom reasoning._")
    L.append("")

    # --- Replica age / growth ---------------------------------------------------
    L.append("## Replica age — is this steady state, or just equally old?")
    L.append("")
    L.append(
        "A Deployment rolls its replicas together, so they usually share an age. A tight "
        "spread of readings across same-aged replicas therefore says *equally old*, not "
        "*stable* — `kubectl top` cannot tell the two apart, and sizing a request off an "
        "accumulating value leaves margin that shrinks with uptime. Rows marked ⚠️ below "
        "are **not characterised**: treat their suggestions as a lower bound and confirm "
        "against a freshly-rolled replica before cutting."
    )
    L.append("")
    if uniform:
        L.append(f"**Uniform-age (not characterised) — {len(uniform)} of {len(sized)} sized containers:**")
        L.append("")
        L.append("| Workload | NS | Container | Replicas sampled | Replica age | Peak (cpu/mem) |")
        L.append("|---|---|---|---:|---|---|")
        for f in sorted(uniform, key=lambda f: (f.ns, f.name, f.container)):
            note = " (single replica — nothing to compare)" if not f.age.comparable else ""
            L.append(
                f"| ⚠️ {f.name} | {f.ns} | {f.container} | {f.pods_seen} | "
                f"{fmt_age_range(f.age)}{note} | "
                f"{fmt_cpu(f.peak_cpu_m)}/{fmt_mem(f.peak_mem_b)} |"
            )
        L.append("")
    else:
        L.append("_No sized container has uniformly-aged replicas._")
        L.append("")
    if growers:
        L.append("**Memory growth signal — oldest vs youngest replica:** where replica ages "
                 "*do* differ, this single comparison is what exposes accumulation.")
        L.append("")
        L.append("| Workload | NS | Container | Youngest (age → mem) | Oldest (age → mem) | Growth | ≈ per day |")
        L.append("|---|---|---|---|---|---:|---:|")
        for f in growers:
            a = f.age
            L.append(
                f"| {f.name} | {f.ns} | {f.container} | "
                f"{fmt_age(a.min_h)} → {fmt_mem(a.youngest_mem_b)} | "
                f"{fmt_age(a.max_h)} → {fmt_mem((a.youngest_mem_b or 0) + (a.growth_mem_b or 0))} | "
                f"+{fmt_mem(a.growth_mem_b)} | "
                f"+{fmt_mem(a.mem_growth_per_day_b) if a.mem_growth_per_day_b else '—'} |"
            )
        L.append("")
        L.append("_Size these off the **oldest** replica's reading plus headroom, not the "
                 "cluster-wide peak — the peak is wherever the oldest pod happens to have "
                 "reached today, not where it stops._")
        L.append("")

    # --- Infra advisory ---
    if over_infra:
        L.append("## Infra namespaces (advisory — do NOT aggressively cut)")
        L.append("")
        L.append("| Workload | NS | Kind | Container | Over on | Mem req → suggest (peak) | CPU req → suggest (peak) |")
        L.append("|---|---|---|---|---|---|---|")
        for f in over_infra:
            mem_cell = _delta_cell(f.req_mem_b, f.sugg_mem_b, f.peak_mem_b, fmt_mem, f.req_mem_src)
            cpu_cell = _delta_cell(f.req_cpu_m, f.sugg_cpu_m, f.peak_cpu_m, fmt_cpu, f.req_cpu_src)
            L.append(f"| {f.name} | {f.ns} | {f.kind} | {f.container} | "
                     f"{'+'.join(f.over_resources)} | {mem_cell} | {cpu_cell} |")
        L.append("")

    L.append("## Applying (separate, reviewed step — not done here)")
    L.append("")
    L.append("This report is read-only. To resize a candidate revert-safely, follow "
             "`references/resize-apply-guide.md` — use `kubectl set resources` so the "
             "Spinnaker-managed image is left untouched, dev before prod, memory headroom "
             "over peak. Roll high-replica services as a canary.")
    L.append("")
    L.append("> **Sizing caveat — memory.** Suggestions are `peak × headroom`, a *floor for "
             "discussion*, not a target. `kubectl top` shows RSS *at the age the replicas "
             "happen to be*, which is not the same thing as steady state: a process that "
             "accumulates (caches, arenas, fragmentation) reads higher the longer it has "
             "been up, and every replica of a Deployment is normally the same age, so the "
             "consistency across replicas does not rule it out. Where the *Replica age* "
             "section marks a workload not characterised, treat its suggestion as a lower "
             "bound. When in doubt, under-cut the cut (leave more memory).")
    L.append("")
    L.append("> **Sizing caveat — cold start.** The reverse trap applies right after a "
             "rollout: fresh pods pay model loads, JIT warm-up and cache fills, so a sample "
             "taken then captures cold-start demand, not steady state. For CPU that gap runs "
             "an order of magnitude (a workload idling at 76m peaked at 1269m while warming). "
             "Memory is the opposite — fresh pods read *low* and climb. So a post-rollout "
             "sample over-states CPU and under-states memory; neither resource is "
             "characterised by a single sample at a single pod age. CPU is compressible "
             "(it throttles, it doesn't OOM) so it is sized tighter, but a request set below "
             "cold-start demand buys a slow start on every deploy.")
    L.append("")
    return "\n".join(L)


def _age_cell(a: AgeStats) -> str:
    """Replica age, marked ⚠️ when it cannot distinguish steady state from growth."""
    if not a.known:
        return "—"
    return f"{fmt_age_range(a)}{' ⚠️' if a.uniform else ''}"


def _lr_mark(text: str, src: str | None) -> str:
    """Append a † to a rendered value that came from a LimitRange default."""
    return f"{text}†" if src == "limitrange" else text


def _delta_cell(req, sugg, peak, fmt, req_src=None) -> str:
    if req is None:
        base = "none"
    else:
        base = _lr_mark(fmt(req), req_src)
    if sugg is not None:
        base += f" → **{fmt(sugg)}**"
    return f"{base} ({fmt(peak)})"


def findings_to_dicts(findings: list[Finding]) -> list[dict]:
    out = []
    for f in findings:
        out.append({
            "namespace": f.ns, "kind": f.kind, "workload": f.name, "container": f.container,
            "replicas": f.replicas, "infra": f.infra,
            # Per-resource verdicts are the authoritative ones. `direction` is a
            # derived container-level summary kept for readers that want one word;
            # it takes the value "mixed" when the two resources disagree, so a
            # caller switching on it must not assume the old three buckets.
            "cpu_direction": f.cpu_direction, "mem_direction": f.mem_direction,
            "cpu_reasons": f.cpu_reasons, "mem_reasons": f.mem_reasons,
            "over_resources": f.over_resources, "under_resources": f.under_resources,
            "direction": f.direction,
            "reasons": f.reasons,
            # effective (spec, else LimitRange default, else request-from-limit)
            "request_cpu_m": f.req_cpu_m, "request_mem_bytes": f.req_mem_b,
            "limit_cpu_m": f.lim_cpu_m, "limit_mem_bytes": f.lim_mem_b,
            # provenance: "spec" | "limitrange" | "limit" | null
            "request_cpu_source": f.req_cpu_src, "request_mem_source": f.req_mem_src,
            "limit_cpu_source": f.lim_cpu_src, "limit_mem_source": f.lim_mem_src,
            # raw values declared on the container itself (null where inherited)
            "spec_request_cpu_m": f.spec_req_cpu_m, "spec_request_mem_bytes": f.spec_req_mem_b,
            "spec_limit_cpu_m": f.spec_lim_cpu_m, "spec_limit_mem_bytes": f.spec_lim_mem_b,
            "peak_cpu_m": f.peak_cpu_m, "peak_mem_bytes": f.peak_mem_b, "pods_seen": f.pods_seen,
            "suggest_cpu_m": f.sugg_cpu_m, "suggest_mem_bytes": f.sugg_mem_b,
            "reclaim_cpu_m": f.reclaim_cpu_m, "reclaim_mem_bytes": f.reclaim_mem_b,
            # Replica ages, and the oldest-vs-youngest delta where ages differ.
            # `age_uniform` true means the sample cannot distinguish steady state
            # from accumulation — the reading is not characterised.
            "replica_ages_hours": [round(a, 2) for a in f.age.ages_h],
            "replica_age_min_hours": f.age.min_h,
            "replica_age_max_hours": f.age.max_h,
            "replica_age_spread_hours": f.age.spread_h,
            "age_uniform": f.age.uniform if f.age.known else None,
            "age_comparable": f.age.comparable,
            "growth_mem_bytes": f.age.growth_mem_b,
            "growth_cpu_m": f.age.growth_cpu_m,
            "growth_mem_bytes_per_day": f.age.mem_growth_per_day_b,
            "growth_mem_material": f.age.mem_growth_material,
        })
    return out


def limitranges_to_dicts(limitranges: dict[str, LimitRangeDefaults]) -> dict:
    return {
        ns: {
            "default_request_cpu_m": d.def_req_cpu_m, "default_request_mem_bytes": d.def_req_mem_b,
            "default_limit_cpu_m": d.def_lim_cpu_m, "default_limit_mem_bytes": d.def_lim_mem_b,
        }
        for ns, d in sorted(limitranges.items()) if d.any
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--context", default=None, help="kubectl context (default: current-context)")
    ap.add_argument("--samples", type=int, default=3, help="number of kubectl top samples (default 3)")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between samples (default 5)")
    ap.add_argument("--over-ratio", type=float, default=4.0, help="flag over-provisioned when request/peak > this (default 4)")
    ap.add_argument("--cpu-headroom", type=float, default=1.5, help="suggested cpu = peak × this (default 1.5)")
    ap.add_argument("--mem-headroom", type=float, default=2.0,
                    help="suggested mem = peak × this (default 2.0 — memory is deliberately "
                         "more generous than cpu; OOM kills are worse than a little waste)")
    ap.add_argument("--cpu-floor-m", type=float, default=50, help="never suggest cpu below this many millicores (default 50)")
    ap.add_argument("--mem-floor-mi", type=float, default=128, help="never suggest mem below this many Mi (default 128)")
    ap.add_argument("--near-limit-frac", type=float, default=0.9, help="peak ≥ this × limit is risky (default 0.9)")
    ap.add_argument("--min-reclaim-mem-mi", type=float, default=128, help="min per-pod mem reclaim to report a candidate (default 128Mi)")
    ap.add_argument("--min-reclaim-cpu-m", type=float, default=100, help="min per-pod cpu reclaim to report a candidate (default 100m)")
    ap.add_argument("--json", action="store_true", help="emit structured JSON instead of markdown")
    ap.add_argument("--out", default=None, help="write output to this file (also prints path)")
    args = ap.parse_args()

    context = args.context
    if context is None:
        try:
            context = kubectl(["config", "current-context"], None).strip()
        except Exception:
            context = "(unknown)"

    try:
        workloads = collect_workloads(args.context)
        limitranges, lr_error = collect_limitranges(args.context)
        pod_index, pod_started = build_pod_index(args.context)
        usage = sample_usage(args.context, pod_index, args.samples, args.interval)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    params = dict(
        over_ratio=args.over_ratio, cpu_headroom=args.cpu_headroom, mem_headroom=args.mem_headroom,
        cpu_floor_m=args.cpu_floor_m, mem_floor_mi=args.mem_floor_mi,
    )
    findings = analyze(
        workloads, usage, limitranges, pod_started,
        over_ratio=args.over_ratio, cpu_headroom=args.cpu_headroom, mem_headroom=args.mem_headroom,
        cpu_floor_m=args.cpu_floor_m, mem_floor_mi=args.mem_floor_mi,
        near_limit_frac=args.near_limit_frac,
        min_reclaim_mem_mi=args.min_reclaim_mem_mi, min_reclaim_cpu_m=args.min_reclaim_cpu_m,
    )

    if args.json:
        payload = {
            "context": context,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "samples": args.samples,
            "params": params,
            "limitrange_defaults": limitranges_to_dicts(limitranges),
            "limitrange_fetch_error": lr_error,
            "findings": findings_to_dicts(findings),
        }
        output = json.dumps(payload, indent=2)
    else:
        output = render_markdown(findings, context, args.samples, params, limitranges, lr_error)

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(output)
        print(f"wrote {args.out}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
