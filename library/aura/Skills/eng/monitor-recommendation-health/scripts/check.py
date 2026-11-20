#!/usr/bin/env python3
"""
monitor-recommendation-health — a READ-ONLY health check for Aura's recommendation system.

Three independent checks (run one, or --check all for the lot):

  failures  Per-model /predict failure rate. Groups ContentService's
            "Model call failed (status <5xx>)" WARN logs by jsonPayload.modelName
            and flags any model over a threshold. (GCP Cloud Logging)

  neural    Live neural-model collapse/health check. Replays /predict against the
            live pod (kubectl port-forward) for a few users and measures the score
            spread — a healthy ranker scores ~13k distinct values/user with
            std ~0.6; a COLLAPSED model scores ~1 distinct with std ~0. Also runs
            offline against a --scores-file. (kubectl + stdlib HTTP; no GCP)

  gate      Latest trainer quality-gate result. Reads the most recent
            neural-model-trainer "Model quality gate: {...}" audit line
            (jsonPayload.modelQuality) and reports the metrics + PASS/FAIL.
            (GCP Cloud Logging)

Read-only: queries logs, replays predictions, reads a file. Never writes anything
to the cluster, the logs, or NFS.

PII guardrail: the recommendation logs dump full user profiles inline
(neural-model "Returning recommendations", ContentService request params). This
tool NEVER echoes a raw jsonPayload.message or any profile field — only the
specific extracted signals: model names + counts, aggregate score statistics, and
the numeric modelQuality object. See references/checks.md.

Maps to PROD-3714 (recommendation-system quality gate, option #3 — the standing
health check that complements the trainer's publish-time gate in 4U-engine #150).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

# Skill-local imports — these scripts live alongside this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gcp import AuthError, get_client, list_entries  # noqa: E402

# --- Fixed prod topology (see AuraInfra k8s/apps + the 4U-engine model repos) ---
DEFAULT_CLUSTER = "aura-prod-cluster-a"
CONTENT_NAMESPACE = "aura-services"
CONTENT_CONTAINER = "content-service"
AI_NAMESPACE = "aura-ai"
TRAINER_CONTAINER = "neural-model-trainer"
NEURAL_SERVICE = "neural-model"
NEURAL_PORT = 3001

# --- Defaults (all env-overridable so an alert/cron can pin them) ---
DEFAULT_FAILURES_SINCE = os.environ.get("REC_HEALTH_FAILURES_SINCE", "1h")
DEFAULT_FAILURE_STATUS = os.environ.get("REC_HEALTH_FAILURE_STATUS", "500")
# Per-model failure count over the window that trips a flag.
FAILURE_THRESHOLD = int(os.environ.get("REC_HEALTH_FAILURE_THRESHOLD", "10"))
DEFAULT_GATE_SINCE = os.environ.get("REC_HEALTH_GATE_SINCE", "7d")

# Collapse thresholds — mirror the trainer's publish-time gate (train_2.1.py:
# QUALITY_MIN_DISTINCT=1000, QUALITY_MIN_STD=0.05). Healthy ~13k-41k distinct /
# std ~0.6; collapse ~1 / ~0 — orders of magnitude of margin.
NEURAL_MIN_DISTINCT = float(os.environ.get("NEURAL_MIN_DISTINCT", "1000"))
NEURAL_MIN_STD = float(os.environ.get("NEURAL_MIN_STD", "0.05"))
# Same knobs the gate uses for the log-side PASS/FAIL recompute.
QUALITY_MIN_DISTINCT = float(os.environ.get("QUALITY_MIN_DISTINCT", "1000"))
QUALITY_MIN_STD = float(os.environ.get("QUALITY_MIN_STD", "0.05"))
QUALITY_MIN_EMB_STD = float(os.environ.get("QUALITY_MIN_EMB_STD", "1e-6"))

MAX_ENTRIES = 5000
_SINCE_RE = re.compile(r"^(\d+)([smhd])$")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def parse_since(since: str) -> timedelta:
    m = _SINCE_RE.match(since)
    if not m:
        raise ValueError(f"--since must be like 30m, 4h, 7d (got {since!r})")
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(seconds={"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * n)


def time_filter(window: timedelta) -> str:
    cutoff = datetime.now(timezone.utc) - window
    return f'timestamp>="{cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")}"'


def _short_ts(ts: str) -> str:
    if not ts:
        return "?"
    return ts.replace("T", " ")[:19] + "Z"


def score_stats(scores: list[float]) -> tuple[int, float]:
    """(distinct-rounded-scores, population-std) — mirrors the trainer's math:
    np.unique(np.round(preds, 4)).size and preds.std()."""
    if not scores:
        return 0, 0.0
    distinct = len({round(float(s), 4) for s in scores})
    std = statistics.pstdev([float(s) for s in scores]) if len(scores) > 1 else 0.0
    return distinct, std


# ---------------------------------------------------------------------------
# Check 1 — per-model /predict failure rates (GCP)
# ---------------------------------------------------------------------------


def check_failures(auth, args) -> dict:
    window = parse_since(args.failures_since)
    # ContentService logs `Model call failed (status <code>)` at WARN with a flat
    # jsonPayload.modelName (as of 2.294.2). Older 2.294.1 lines lack modelName.
    message = f"Model call failed (status {args.status})"
    filter_str = " AND ".join([
        'resource.type="k8s_container"',
        f'resource.labels.cluster_name="{args.cluster}"',
        f'resource.labels.namespace_name="{CONTENT_NAMESPACE}"',
        f'resource.labels.container_name="{CONTENT_CONTAINER}"',
        f'jsonPayload.message="{message}"',
        time_filter(window),
    ])
    print(f"# [failures] query: {filter_str}", file=sys.stderr)

    per_model: dict[str, int] = {}
    no_model = 0
    versions: set[str] = set()
    total = 0
    for entry in list_entries(auth, filter_str, max_entries=MAX_ENTRIES):
        total += 1
        jp = entry.get("jsonPayload") or {}
        model = jp.get("modelName")
        svc = (jp.get("serviceContext") or {})
        if svc.get("version"):
            versions.add(svc["version"])
        if model:
            per_model[model] = per_model.get(model, 0) + 1
        else:
            no_model += 1

    flagged = {m: c for m, c in per_model.items() if c >= args.failure_threshold}
    result = {
        "check": "failures",
        "window": args.failures_since,
        "status_code": args.status,
        "threshold": args.failure_threshold,
        "total_failures": total,
        "per_model": dict(sorted(per_model.items(), key=lambda kv: -kv[1])),
        "without_modelName": no_model,
        "content_service_versions": sorted(versions),
        "flagged_models": dict(sorted(flagged.items(), key=lambda kv: -kv[1])),
        "verdict": "FLAG" if flagged else "OK",
    }
    return result


def render_failures(r: dict) -> str:
    lines = [
        f"CHECK 1 — /predict failure rates  |  content-service ≥ WARN  |  "
        f"status {r['status_code']}  |  last {r['window']}",
    ]
    if r["content_service_versions"]:
        lines.append(f"  content-service versions seen: {', '.join(r['content_service_versions'])}")
    lines.append(f"  {r['total_failures']} failure log(s), threshold = {r['threshold']}/model")
    lines.append("")
    if r["per_model"]:
        lines.append(f"  {'model':<26}  {'failures':>8}   flag")
        lines.append(f"  {'-' * 26}  {'-' * 8}   ----")
        for model, count in r["per_model"].items():
            flag = "  ⚠ OVER THRESHOLD" if count >= r["threshold"] else ""
            lines.append(f"  {model:<26}  {count:>8}{flag}")
    else:
        lines.append("  No per-model failures in the window. ✅")
    if r["without_modelName"]:
        lines.append("")
        lines.append(
            f"  {r['without_modelName']} failure(s) had NO modelName — these are "
            f"pre-2.294.2 (2.294.1) logs; modelName was added in 2.294.2, so they "
            f"can't be attributed to a model."
        )
    lines.append("")
    if r["verdict"] == "FLAG":
        names = ", ".join(f"{m} ({c})" for m, c in r["flagged_models"].items())
        lines.append(f"  VERDICT: ⚠ FLAG — over-threshold: {names}")
    else:
        lines.append("  VERDICT: ✅ OK — no model over the failure threshold")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Check 2 — live neural-model collapse/health (kubectl replay OR --scores-file)
# ---------------------------------------------------------------------------


def _replay_predict(users: list[str], args) -> dict[str, list[float]]:
    """kubectl port-forward svc/neural-model, then POST /predict per user.

    Returns {userId: [pred_score, ...]} over the whole track catalog.
    Read-only: /predict is a pure inference call; no state is mutated.
    """
    if not shutil.which("kubectl"):
        raise RuntimeError(
            "kubectl not found on PATH. Point kubectl at the prod cluster "
            f"({args.cluster}, namespace {AI_NAMESPACE}) and retry, or run the "
            "check offline with --scores-file (see references/checks.md)."
        )
    local_port = args.local_port
    cmd = ["kubectl", "-n", args.namespace, "port-forward",
           f"svc/{args.service}", f"{local_port}:{args.port}"]
    if args.context:
        cmd = ["kubectl", "--context", args.context, "-n", args.namespace,
               "port-forward", f"svc/{args.service}", f"{local_port}:{args.port}"]
    print(f"# [neural] {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    scores: dict[str, list[float]] = {}
    try:
        # Wait for the forward to come up (kubectl prints "Forwarding from ...").
        deadline = time.time() + args.connect_timeout
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"kubectl port-forward exited early:\n{out.strip()}")
            line = proc.stdout.readline() if proc.stdout else ""
            if "Forwarding from" in line:
                ready = True
                break
        if not ready:
            raise RuntimeError(
                f"kubectl port-forward did not become ready within "
                f"{args.connect_timeout}s. Check cluster context / pod readiness."
            )
        url = f"http://127.0.0.1:{local_port}/predict"
        for uid in users:
            body = json.dumps({"user": {"id": uid}}).encode("utf-8")
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=args.predict_timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"/predict returned HTTP {e.code} for a probe user") from e
            # Response is [{"score": <float>, "id": <track_id>}, ...] over the catalog.
            scores[uid] = [float(rec["score"]) for rec in payload if "score" in rec]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return scores


def check_neural(args) -> dict:
    # Offline path — operator supplies {uid: [scores]} or {uid: [{score,id}]}.
    if args.scores_file:
        with open(args.scores_file) as f:
            raw = json.load(f)
        scores: dict[str, list[float]] = {}
        for uid, arr in raw.items():
            if arr and isinstance(arr[0], dict):
                scores[uid] = [float(x["score"]) for x in arr if "score" in x]
            else:
                scores[uid] = [float(x) for x in arr]
        source = f"scores-file:{os.path.basename(args.scores_file)}"
    else:
        users = [u.strip() for u in args.users.split(",") if u.strip()] if args.users else []
        if not users:
            # No real UIDs given → probe distinct ids. The model falls back to its
            # FALLBACK_USER for unknown ids, so this still measures the ranking
            # spread and catches a GLOBAL collapse (the historical failure mode).
            # Real UIDs additionally catch per-user degeneracies — pass --users.
            users = [f"rec-health-probe-{i}" for i in range(1, args.sample_users + 1)]
        scores = _replay_predict(users, args)
        source = f"live:{args.service}@{args.namespace}"

    per_user = []
    for uid, sc in scores.items():
        distinct, std = score_stats(sc)
        per_user.append({"user": uid, "scored": len(sc), "distinct": distinct, "std": round(std, 6)})

    distinct_counts = [u["distinct"] for u in per_user]
    stds = [u["std"] for u in per_user]
    median_distinct = statistics.median(distinct_counts) if distinct_counts else 0
    median_std = statistics.median(stds) if stds else 0.0

    collapsed = (
        not per_user
        or median_distinct < args.min_distinct
        or median_std < args.min_std
    )
    result = {
        "check": "neural",
        "source": source,
        "min_distinct": args.min_distinct,
        "min_std": args.min_std,
        "users_scored": len(per_user),
        "median_distinct_scores": median_distinct,
        "median_user_score_std": round(median_std, 6),
        # PII guardrail: per-user aggregates only — never the raw score arrays.
        "per_user": per_user,
        "verdict": "COLLAPSED" if collapsed else "HEALTHY",
    }
    return result


def render_neural(r: dict) -> str:
    lines = [
        f"CHECK 2 — neural-model collapse/health  |  source: {r['source']}",
        f"  thresholds: median distinct/user > {r['min_distinct']:g}, "
        f"per-user std > {r['min_std']:g}  (healthy ~13k / ~0.6; collapse ~1 / ~0)",
        "",
    ]
    if r["per_user"]:
        lines.append(f"  {'user':<28}  {'distinct':>8}  {'std':>10}")
        lines.append(f"  {'-' * 28}  {'-' * 8}  {'-' * 10}")
        for u in r["per_user"]:
            lines.append(f"  {u['user'][:28]:<28}  {u['distinct']:>8}  {u['std']:>10.4f}")
        lines.append("")
    lines.append(
        f"  median distinct/user = {r['median_distinct_scores']:g}   "
        f"median per-user std = {r['median_user_score_std']:g}"
    )
    lines.append("")
    if r["verdict"] == "HEALTHY":
        lines.append("  VERDICT: ✅ HEALTHY — model ranks (varied scores)")
    else:
        lines.append(
            "  VERDICT: 🚨 COLLAPSED — scores are near-constant. The live model is "
            "returning ~the same score for every track. Do NOT trust its recs; "
            "investigate the last publish (see the gate check)."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Check 3 — latest trainer quality-gate result (GCP)
# ---------------------------------------------------------------------------


def check_gate(auth, args) -> dict:
    window = parse_since(args.gate_since)
    # The trainer emits one audit line per run: "Model quality gate: {...}" with a
    # structured jsonPayload.modelQuality object (train_2.1.py / 4U-engine #150).
    filter_str = " AND ".join([
        'resource.type="k8s_container"',
        f'resource.labels.cluster_name="{args.cluster}"',
        f'resource.labels.namespace_name="{AI_NAMESPACE}"',
        f'resource.labels.container_name="{TRAINER_CONTAINER}"',
        'jsonPayload.message:"Model quality gate"',
        time_filter(window),
    ])
    print(f"# [gate] query: {filter_str}", file=sys.stderr)

    latest = None
    for entry in list_entries(auth, filter_str, max_entries=200, order_by="timestamp desc"):
        jp = entry.get("jsonPayload") or {}
        if jp.get("modelQuality"):
            latest = entry
            break  # ordered desc → first hit is the most recent

    if latest is None:
        return {
            "check": "gate",
            "window": args.gate_since,
            "found": False,
            "verdict": "NO_DATA",
        }

    jp = latest.get("jsonPayload") or {}
    mq = jp.get("modelQuality") or {}
    svc = jp.get("serviceContext") or {}
    md = mq.get("medianDistinctScores")
    ms = mq.get("medianUserScoreStd")
    es = mq.get("minEmbeddingStd")
    passed = (
        md is not None and ms is not None and es is not None
        and md >= args.min_distinct and ms >= args.min_std and es >= QUALITY_MIN_EMB_STD
    )
    return {
        "check": "gate",
        "window": args.gate_since,
        "found": True,
        "timestamp": latest.get("timestamp"),
        "trainer_version": svc.get("trainer_version") or svc.get("version"),
        "modelQuality": mq,
        "min_distinct": args.min_distinct,
        "min_std": args.min_std,
        "min_emb_std": QUALITY_MIN_EMB_STD,
        "verdict": "PASS" if passed else "FAIL",
    }


def render_gate(r: dict) -> str:
    lines = [f"CHECK 3 — latest trainer quality gate  |  neural-model-trainer  |  last {r['window']}"]
    if not r["found"]:
        lines.append("")
        lines.append(
            f"  No \"Model quality gate\" line in the last {r['window']}. Either the "
            f"trainer hasn't run in that window, or the gated trainer (4U-engine "
            f"#150, train_2.1.py) hasn't shipped to prod yet. Broaden with a longer "
            f"--gate-since (e.g. 90d)."
        )
        lines.append("  VERDICT: ⚠ NO DATA")
        return "\n".join(lines)
    mq = r["modelQuality"]
    lines.append(f"  most recent run: {_short_ts(r['timestamp'])}  (trainer {r.get('trainer_version') or '?'})")
    lines.append(
        f"  minimums: distinct ≥ {r['min_distinct']:g}, std ≥ {r['min_std']:g}, "
        f"embStd ≥ {r['min_emb_std']:g}"
    )
    lines.append("")
    lines.append(f"  {'metric':<22}  {'value':>16}")
    lines.append(f"  {'-' * 22}  {'-' * 16}")
    for k in ("medianDistinctScores", "medianUserScoreStd", "minEmbeddingStd",
              "sampledUsers", "catalogTracks"):
        if k in mq:
            v = mq[k]
            lines.append(f"  {k:<22}  {v:>16}")
    lines.append("")
    if r["verdict"] == "PASS":
        lines.append("  VERDICT: ✅ PASS — the last published model cleared the gate")
    else:
        lines.append(
            "  VERDICT: 🚨 FAIL — the most recent gate metrics are below the "
            "collapse thresholds. Note the trainer refuses to publish on a FAIL "
            "(it exits non-zero, leaving the previous artifact), so a FAIL here "
            "usually means the last run did NOT ship — confirm what's live via the "
            "neural check."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="monitor-recommendation-health",
        description="Read-only health check for Aura's recommendation system.",
    )
    p.add_argument("--check", choices=["failures", "neural", "gate", "all"],
                   default="all", help="Which check(s) to run (default: all).")
    p.add_argument("--project", help="Override the project derived from the SA key.")
    p.add_argument("--cluster", default=DEFAULT_CLUSTER,
                   help=f"GKE cluster (default {DEFAULT_CLUSTER}).")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    # Check 1 — failures
    p.add_argument("--failures-since", default=DEFAULT_FAILURES_SINCE,
                   help=f"Failure-rate window (default {DEFAULT_FAILURES_SINCE}).")
    p.add_argument("--status", default=DEFAULT_FAILURE_STATUS,
                   help=f"5xx status in the failure message (default {DEFAULT_FAILURE_STATUS}).")
    p.add_argument("--failure-threshold", type=int, default=FAILURE_THRESHOLD,
                   help=f"Per-model failure count that flags (default {FAILURE_THRESHOLD}).")

    # Check 2 — neural
    p.add_argument("--users", help="Comma-separated Firebase UIDs to score (recommended).")
    p.add_argument("--sample-users", type=int, default=5,
                   help="If --users omitted, number of probe users (fallback-user spread).")
    p.add_argument("--scores-file",
                   help="Offline mode: JSON {uid: [scores]} or {uid: [{score,id}]}; skips kubectl.")
    p.add_argument("--namespace", default=AI_NAMESPACE, help=f"neural-model namespace (default {AI_NAMESPACE}).")
    p.add_argument("--service", default=NEURAL_SERVICE, help=f"neural-model service (default {NEURAL_SERVICE}).")
    p.add_argument("--port", type=int, default=NEURAL_PORT, help=f"neural-model port (default {NEURAL_PORT}).")
    p.add_argument("--local-port", type=int, default=13001, help="Local port-forward port (default 13001).")
    p.add_argument("--context", help="kubectl context to use for the port-forward.")
    p.add_argument("--connect-timeout", type=int, default=20, help="Seconds to wait for port-forward.")
    p.add_argument("--predict-timeout", type=int, default=30, help="Per-/predict timeout (seconds).")
    p.add_argument("--min-distinct", type=float, default=NEURAL_MIN_DISTINCT,
                   help=f"Min median distinct scores/user (default {NEURAL_MIN_DISTINCT:g}).")
    p.add_argument("--min-std", type=float, default=NEURAL_MIN_STD,
                   help=f"Min median per-user std (default {NEURAL_MIN_STD:g}).")

    # Check 3 — gate
    p.add_argument("--gate-since", default=DEFAULT_GATE_SINCE,
                   help=f"How far back to look for the latest gate line (default {DEFAULT_GATE_SINCE}).")

    return p.parse_args(argv)


def _needs_gcp(check: str) -> bool:
    return check in ("failures", "gate", "all")


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)

    auth = None
    if _needs_gcp(args.check):
        try:
            auth = get_client(project_override=args.project)
            print(f"# auth: source={auth.source} project={auth.project} principal={auth.principal}",
                  file=sys.stderr)
        except AuthError as e:
            print(f"AUTH ERROR: {e}", file=sys.stderr)
            return 3

    results: list[dict] = []
    worst = 0  # exit code: 0 OK, 1 warn/flag, 2 collapse/fail

    def run(check: str):
        nonlocal worst
        try:
            if check == "failures":
                r = check_failures(auth, args)
                worst = max(worst, 1 if r["verdict"] == "FLAG" else 0)
            elif check == "neural":
                r = check_neural(args)
                worst = max(worst, 2 if r["verdict"] == "COLLAPSED" else 0)
            elif check == "gate":
                r = check_gate(auth, args)
                worst = max(worst, 2 if r["verdict"] == "FAIL" else (1 if r["verdict"] == "NO_DATA" else 0))
        except Exception as e:  # a check that can't run shouldn't sink the rest
            r = {"check": check, "verdict": "ERROR", "error": f"{type(e).__name__}: {e}"}
            worst = max(worst, 1)
        results.append(r)

    checks = ["failures", "neural", "gate"] if args.check == "all" else [args.check]
    for c in checks:
        run(c)

    if args.json:
        print(json.dumps({"results": results}, indent=2))
        return worst

    renderers = {"failures": render_failures, "neural": render_neural, "gate": render_gate}
    blocks = []
    for r in results:
        if r.get("verdict") == "ERROR":
            blocks.append(f"CHECK — {r['check']}\n  ⚠ could not run: {r['error']}")
        else:
            blocks.append(renderers[r["check"]](r))
    print("\n\n".join(blocks))
    print("")
    print(_summary_line(results))
    return worst


def _summary_line(results: list[dict]) -> str:
    icons = {"OK": "✅", "HEALTHY": "✅", "PASS": "✅",
             "FLAG": "⚠", "NO_DATA": "⚠", "ERROR": "⚠",
             "COLLAPSED": "🚨", "FAIL": "🚨"}
    bits = [f"{icons.get(r.get('verdict'), '?')} {r['check']}={r.get('verdict')}" for r in results]
    return "SUMMARY: " + "  ".join(bits)


if __name__ == "__main__":
    sys.exit(main())
