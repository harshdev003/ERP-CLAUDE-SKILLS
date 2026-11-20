#!/usr/bin/env python3
"""The offline bar for Aura content recommendations.

Replays a candidate ranker against held-out history and scores it against the
incumbent. A candidate that does not beat the baseline here never reaches a
human — that is the loop's whole speed advantage.

  baselines --split-date 2026-09-15          score the four baselines, store them
  score --name cf-v1 --scores-file s.json    score a candidate against them
  runs [--limit 20]                          recent runs from the store
  show --run-id <id>                         one run in full

Read references/metrics.md for what each metric means and references/store.md
for the BigQuery schema. Validity limits are printed with every report and are
not optional reading: there is no impression log yet, so this is retrieval
evaluation over observed outcomes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Mapping, Sequence

import bq
import metrics

MIXPANEL = "`mixpanel-prod-1.mixpanel_nessie_day_partitioned_963821"
STORE_DATASET = "aura-main.recs_eval"
RUNS_TABLE = f"{STORE_DATASET}.runs"
DEFAULT_K = 10
# Recall is reported at 10 and 20, so every ranker must emit at least 20 even
# when k is smaller — otherwise recall@20 silently collapses onto recall@10.
RECALL_DEPTHS = (10, 20)
MAX_DEPTH = max(RECALL_DEPTHS)


def depth_for(k: int) -> int:
    """How many candidates every ranker must emit for a run at depth k.

    Deep enough for the deepest recall, and never shallower than k itself.
    Candidate and baselines must use the SAME value: scoring a 20-item
    candidate against a longer incumbent compares it on fewer slots than the
    bar it has to beat.
    """
    return max(int(k), MAX_DEPTH)
# Long enough to give every user some history, short enough that a coach who
# joined last year is represented. 365d matches the trainer's own window.
FIT_DAYS = 365
EVAL_DAYS = 7

COHORT_BUCKETS = (
    (2019, "2017-18"),
    (2021, "2019-20"),
    (2023, "2021-22"),
    (9999, "2023+"),
)


def cohort_for(year: int | None) -> str:
    if not year:
        return "unknown"
    for upper, label in COHORT_BUCKETS:
        if year < upper:
            return label
    return "2023+"


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

TRUTH_SQL = """
SELECT userId AS user_id, session AS track_id, COUNT(*) AS completions
FROM {mp}.meditation_done_single`
WHERE _PARTITIONTIME >= TIMESTAMP('{start}')
  AND _PARTITIONTIME <  TIMESTAMP('{end}')
  AND userId IS NOT NULL AND session IS NOT NULL
GROUP BY user_id, track_id
"""

# One pass: every track, its coach, and that coach's first upload year.
CATALOGUE_SQL = """
WITH coach_start AS (
  SELECT source, EXTRACT(YEAR FROM MIN(createdAt)) AS first_year
  FROM `aura-main.firebase.tracks`
  WHERE source IS NOT NULL AND createdAt IS NOT NULL
  GROUP BY source
)
SELECT t.id AS track_id, t.source AS coach, cs.first_year AS coach_first_year
FROM `aura-main.firebase.tracks` t
LEFT JOIN coach_start cs ON cs.source = t.source
WHERE t.id IS NOT NULL
"""

POPULAR_SQL = """
SELECT track_id, score
FROM `aura-main.firebase.popular_tracks`
ORDER BY score DESC
"""


def _window(split: date, days_before: int, days_after: int):
    return (split - timedelta(days=days_before)).isoformat(), (
        split + timedelta(days=days_after)
    ).isoformat()


def load_interactions(start: str, end: str) -> List[dict]:
    sql = TRUTH_SQL.format(mp=MIXPANEL, start=start, end=end)
    return list(bq.query(sql))


def load_catalogue() -> Dict[str, dict]:
    return {
        r["track_id"]: {
            "coach": r["coach"],
            "cohort": cohort_for(r["coach_first_year"]),
        }
        for r in bq.query(CATALOGUE_SQL)
    }


def load_popularity() -> List[str]:
    """Global popularity order — literally what `popular-model` serves."""
    return [r["track_id"] for r in bq.query(POPULAR_SQL)]


def build_eval_set(split: date):
    """Fit and eval windows, plus everything the metrics need.

    Returns (truth, repeats, fit_by_user, catalogue, popular_order).
    `truth` gains are completion counts, which is the signal available without
    joining the five feedback tables; the full utility score from
    track_scores_2.1.sql is the intended upgrade once the harness is wired to it.
    """
    fit_start, _ = _window(split, FIT_DAYS, 0)
    eval_start, eval_end = split.isoformat(), (split + timedelta(days=EVAL_DAYS)).isoformat()

    fit_rows = load_interactions(fit_start, split.isoformat())
    eval_rows = load_interactions(eval_start, eval_end)

    fit_by_user: Dict[str, List[str]] = {}
    for r in fit_rows:
        fit_by_user.setdefault(r["user_id"], []).append(r["track_id"])

    truth: Dict[str, Dict[str, float]] = {}
    for r in eval_rows:
        truth.setdefault(r["user_id"], {})[r["track_id"]] = float(r["completions"])

    # Only users with history in both windows. Cold-start users are scored
    # separately rather than silently dropped into the headline number.
    warm = {u: t for u, t in truth.items() if u in fit_by_user}
    repeats = {u: set(fit_by_user.get(u, [])) for u in warm}

    return warm, repeats, fit_by_user, load_catalogue(), load_popularity()


# --------------------------------------------------------------------------
# baselines
# --------------------------------------------------------------------------


def baseline_popular(users, popular_order, k, **_) -> Dict[str, List[str]]:
    top = popular_order[: depth_for(k)]
    return {u: list(top) for u in users}


def baseline_random(users, catalogue, k, seed=20260922, **_) -> Dict[str, List[str]]:
    import random as _r

    ids = sorted(catalogue)
    rng = _r.Random(seed)
    depth = depth_for(k)
    return {u: rng.sample(ids, min(depth, len(ids))) for u in users}


def baseline_user_repeat(users, fit_by_user, k, **_) -> Dict[str, List[str]]:
    """The trap baseline. 28.7% of pairs are repeats, so this scores well on
    plain NDCG and near zero on the new-only metric. A candidate that cannot
    beat it on ndcg@k_new is not recommending anything."""
    out = {}
    for u in users:
        seen, ordered = set(), []
        for t in reversed(fit_by_user.get(u, [])):
            if t not in seen:
                seen.add(t)
                ordered.append(t)
        out[u] = ordered[: depth_for(k)]
    return out


BASELINES = {
    "popular-30d": baseline_popular,
    "random": baseline_random,
    "user-repeat": baseline_user_repeat,
}


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def score_recs(recs, truth, repeats, catalogue, popular_order, k) -> dict:
    pop_counts = {t: 1.0 / (i + 1) for i, t in enumerate(popular_order)}
    total = sum(pop_counts.values()) or 1.0
    pop_dist = {t: v / total for t, v in pop_counts.items()}
    cohorts = {t: c["cohort"] for t, c in catalogue.items()}

    per_user_plain = metrics.per_user_ndcg(recs, truth, k)
    per_user_new = metrics.per_user_ndcg(recs, truth, k, exclude=repeats)
    exposure = metrics.exposure_counts(recs, k)
    full = [exposure.get(t, 0) for t in catalogue]

    def mean(d):
        return sum(d.values()) / len(d) if d else 0.0

    # Each metric slices the ranking to its OWN depth. `recs` therefore arrives
    # untruncated (>= MAX_DEPTH); pre-slicing it to k here is what made recall@20
    # collapse onto recall@10 at the default k.
    recalls = {}
    for depth in RECALL_DEPTHS:
        vals = [metrics.recall_at_k(recs[u], t, depth)
                for u, t in truth.items() if u in recs and t]
        recalls[f"recall_at_{depth}"] = sum(vals) / len(vals) if vals else 0.0
        recalls[f"recall_at_{depth}_truncated"] = any(
            len(recs[u]) < depth for u in recs)

    return {
        "ndcg_at_k": mean(per_user_plain),
        "ndcg_at_k_new": mean(per_user_new),
        "per_user_ndcg_new": per_user_new,
        "coverage_at_k": metrics.coverage_at_k(recs, len(catalogue), k),
        "gini_exposure": metrics.gini(full),
        "novelty": metrics.novelty(recs, pop_dist, k),
        "cohort_exposure": metrics.cohort_exposure(recs, cohorts, k),
        "scored_users": len(per_user_new),
        **recalls,
    }


def compare(candidate: dict, baseline: dict) -> dict:
    ratio, lo, hi = metrics.bootstrap_ratio_ci(
        candidate["per_user_ndcg_new"], baseline["per_user_ndcg_new"]
    )
    cov = (
        candidate["coverage_at_k"] / baseline["coverage_at_k"]
        if baseline["coverage_at_k"]
        else float("inf")
    )
    deltas = metrics.cohort_delta(candidate["cohort_exposure"], baseline["cohort_exposure"])
    v, why = metrics.verdict(
        ndcg_new_ratio=ratio, ndcg_new_ci=(lo, hi), coverage_ratio=cov, cohort_deltas=deltas
    )
    return {
        "ndcg_new_ratio": ratio,
        "ndcg_new_ci": [lo, hi],
        "coverage_ratio": cov,
        "cohort_deltas": deltas,
        "verdict": v,
        "reasons": why,
        "offline_score": metrics.offline_score_line(
            ndcg_new_ratio=ratio, coverage_ratio=cov, cohort_deltas=deltas
        ),
    }


VALIDITY = """VALIDITY LIMITS — these apply to every number above
  * No impression log. This is retrieval evaluation over OBSERVED outcomes: a
    track never shown cannot be credited, so anything surfacing the long tail is
    structurally penalised. Coverage partly compensates; it does not fix it.
    Fixed by ENGBACK-1378 — re-baseline everything once it lands.
  * Position bias is uncorrected until positiononlist is populated (ENGBACK-1379).
  * The ground truth was produced by a popularity-dominated system, so
    popular-30d is advantaged by construction. A candidate that merely MATCHES
    the baseline here may still be better in production. That is an argument for
    running the experiment, not for weakening the gate."""


def render(name: str, split: str, k: int, scored: dict, cmp: dict | None) -> str:
    lines = [
        f"CANDIDATE {name}   split {split}   k={k}   users {scored['scored_users']:,}",
        "",
        f"  ndcg@{k}          {scored['ndcg_at_k']:.4f}",
        f"  ndcg@{k}_new      {scored['ndcg_at_k_new']:.4f}   <- primary",
        f"  recall@10        {scored['recall_at_10']:.4f}",
        f"  recall@20        {scored['recall_at_20']:.4f}",
        f"  coverage@{k}      {scored['coverage_at_k']:.4f}",
        f"  gini_exposure    {scored['gini_exposure']:.4f}",
        f"  novelty          {scored['novelty']:.2f} bits",
        "  cohort exposure  "
        + ", ".join(f"{c} {s:.1%}" for c, s in sorted(scored["cohort_exposure"].items())),
    ]
    if cmp:
        lines += [
            "",
            f"  vs popular-30d   ndcg@{k}_new {cmp['ndcg_new_ratio']:.3f}x "
            f"(95% CI {cmp['ndcg_new_ci'][0]:.3f}–{cmp['ndcg_new_ci'][1]:.3f})",
            f"                   coverage {cmp['coverage_ratio']:.3f}x",
            "                   cohort "
            + ", ".join(f"{c} {d:+.1f}pp" for c, d in sorted(cmp["cohort_deltas"].items())),
            "",
            f"  VERDICT: {cmp['verdict']}",
        ] + [f"    - {r}" for r in cmp["reasons"]]
    return "\n".join(lines) + "\n\n" + VALIDITY


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------

RUNS_SCHEMA = [
    ("run_id", "STRING"), ("created_at", "TIMESTAMP"), ("candidate", "STRING"),
    ("config_hash", "STRING"), ("split_date", "DATE"), ("k", "INT64"),
    ("scored_users", "INT64"), ("metrics_json", "STRING"),
    ("comparison_json", "STRING"), ("verdict", "STRING"), ("offline_score", "STRING"),
]


def ensure_store() -> None:
    """Create the dataset and the runs table if they are absent.

    Idempotent DDL rather than a documented manual step: the first `baselines`
    run on a fresh project would otherwise fail at the INSERT, after doing all
    the query work.
    """
    bq.query(f"CREATE SCHEMA IF NOT EXISTS `{STORE_DATASET}` OPTIONS (location = 'US')")
    cols = ", ".join(f"{name} {typ}" for name, typ in RUNS_SCHEMA)
    bq.query(
        f"CREATE TABLE IF NOT EXISTS `{RUNS_TABLE}` ({cols}) "
        "PARTITION BY split_date CLUSTER BY candidate"
    )


def store_run(name, split, k, scored, cmp, config_hash) -> str:
    payload = {kk: vv for kk, vv in scored.items() if kk != "per_user_ndcg_new"}
    run_id = hashlib.sha1(f"{name}|{split}|{k}|{config_hash}".encode()).hexdigest()[:16]
    params = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate": name,
        "config_hash": config_hash,
        "split_date": str(split),
        "k": int(k),
        "scored_users": int(scored["scored_users"]),
        "metrics_json": json.dumps(payload, sort_keys=True, default=str),
        "comparison_json": json.dumps(cmp or {}, sort_keys=True, default=str),
        "verdict": (cmp or {}).get("verdict", "BASELINE"),
        "offline_score": (cmp or {}).get("offline_score", ""),
    }
    ensure_store()
    # MERGE, not INSERT: run_id is a deterministic hash of (candidate, split, k,
    # config), so re-scoring identical inputs must replace the row rather than
    # accumulate duplicates that `show --run-id` would pick between arbitrarily.
    # created_at / split_date are CAST here because the shared bq helper types
    # every non-numeric parameter as STRING.
    bq.query(
        f"""
        MERGE `{RUNS_TABLE}` T
        USING (SELECT @run_id AS run_id) S
        ON T.run_id = S.run_id
        WHEN MATCHED THEN UPDATE SET
          created_at = TIMESTAMP(@created_at), candidate = @candidate,
          config_hash = @config_hash, split_date = DATE(@split_date), k = @k,
          scored_users = @scored_users, metrics_json = @metrics_json,
          comparison_json = @comparison_json, verdict = @verdict,
          offline_score = @offline_score
        WHEN NOT MATCHED THEN INSERT (
          run_id, created_at, candidate, config_hash, split_date, k,
          scored_users, metrics_json, comparison_json, verdict, offline_score
        ) VALUES (
          @run_id, TIMESTAMP(@created_at), @candidate, @config_hash,
          DATE(@split_date), @k, @scored_users, @metrics_json,
          @comparison_json, @verdict, @offline_score
        )
        """,
        params=params,
    )
    return run_id


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def _default_split() -> date:
    return date.today() - timedelta(days=EVAL_DAYS)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # x / dots fail on cp1252 (Windows)
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    for cmd in ("baselines", "score"):
        s = sub.add_parser(cmd)
        s.add_argument("--split-date", type=date.fromisoformat, default=None)
        s.add_argument("--k", type=int, default=DEFAULT_K)
        s.add_argument("--json", action="store_true")
        s.add_argument("--no-store", action="store_true")
        if cmd == "score":
            s.add_argument("--name", required=True)
            s.add_argument("--scores-file", required=True,
                           help='JSON {user_id: [track_id, ...]} ranked best-first')

    r = sub.add_parser("runs"); r.add_argument("--limit", type=int, default=20)
    sh = sub.add_parser("show"); sh.add_argument("--run-id", required=True)

    a = p.parse_args(argv)

    if a.cmd == "runs":
        ensure_store()
        rows = list(bq.query(
            f"SELECT run_id, created_at, candidate, split_date, verdict, offline_score "
            f"FROM `{RUNS_TABLE}` ORDER BY created_at DESC LIMIT {int(a.limit)}"))
        for row in rows:
            print(f"{row['run_id']}  {str(row['created_at'])[:19]}  "
                  f"{row['candidate']:<22} {row['verdict']:<12} {row['offline_score']}")
        return 0

    if a.cmd == "show":
        ensure_store()
        rows = list(bq.query(
            f"SELECT * FROM `{RUNS_TABLE}` WHERE run_id = @run_id",
            params={"run_id": a.run_id}))
        if not rows:
            print(f"no run {a.run_id}", file=sys.stderr)
            return 1
        print(json.dumps(dict(rows[0]), indent=2, default=str))
        return 0

    split = a.split_date or _default_split()
    truth, repeats, fit_by_user, catalogue, popular_order = build_eval_set(split)
    users = sorted(truth)
    if not users:
        print(f"no users with history in both windows around {split}", file=sys.stderr)
        return 2

    scored_baselines = {}
    for bname, fn in BASELINES.items():
        recs = fn(users=users, popular_order=popular_order, catalogue=catalogue,
                  fit_by_user=fit_by_user, k=a.k)
        scored_baselines[bname] = score_recs(
            recs, truth, repeats, catalogue, popular_order, a.k)

    incumbent = scored_baselines["popular-30d"]

    if a.cmd == "baselines":
        out = {}
        for bname, sc in scored_baselines.items():
            cmp = compare(sc, incumbent) if bname != "popular-30d" else None
            if not a.no_store:
                store_run(bname, split, a.k, sc, cmp, config_hash="baseline")
            out[bname] = {kk: vv for kk, vv in sc.items() if kk != "per_user_ndcg_new"}
            print(render(bname, str(split), a.k, sc, cmp)); print()
        if a.json:
            print(json.dumps(out, indent=2, default=str))
        return 0

    with open(a.scores_file) as fh:
        raw = json.load(fh)
    # Same depth as the baselines — see depth_for(). score_recs slices per
    # metric, and a candidate that emits fewer is flagged truncated rather than
    # quietly scored as if it had filled every slot.
    recs = {u: list(v)[: depth_for(a.k)] for u, v in raw.items() if u in truth}
    if not recs:
        print("scores file shares no users with the eval set", file=sys.stderr)
        return 2

    sc = score_recs(recs, truth, repeats, catalogue, popular_order, a.k)
    cmp = compare(sc, incumbent)
    config_hash = hashlib.sha1(open(a.scores_file, "rb").read()).hexdigest()[:12]
    if not a.no_store:
        cmp["run_id"] = store_run(a.name, split, a.k, sc, cmp, config_hash)

    if a.json:
        print(json.dumps({"metrics": {kk: vv for kk, vv in sc.items()
                                      if kk != "per_user_ndcg_new"},
                          "comparison": cmp}, indent=2, default=str))
    else:
        print(render(a.name, str(split), a.k, sc, cmp))
    return 0 if cmp["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
