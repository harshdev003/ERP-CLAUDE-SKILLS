"""Aggregate per-row results into the comparison readout. Pure: no network.

Input is the `results.json` run_comparison.py writes: one entry per prompt
version, each with its scored rows. Errored rows are kept and counted, never
silently dropped from the denominator's story — they are excluded from the
rates and reported as `errors` so a short run can't pass for a clean one.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

NO_TRIGGER = "no_trigger"


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def tool_call_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    either = [r for r in rows if not r.get("error") and r.get("skip")]
    ok = [r for r in rows if not r.get("error") and not r.get("skip")]
    tp = [r for r in ok if r["expect_trigger"] and r["actual_trigger"]]
    fn = [r for r in ok if r["expect_trigger"] and not r["actual_trigger"]]
    fp = [r for r in ok if not r["expect_trigger"] and r["actual_trigger"]]
    tn = [r for r in ok if not r["expect_trigger"] and not r["actual_trigger"]]
    arg_scored = [r for r in ok if r.get("arg_score") is not None]
    confusion: Counter[tuple[str, str]] = Counter()
    for r in ok:
        exp = (r.get("expected_arg") or "trigger") if r["expect_trigger"] else NO_TRIGGER
        act = (r.get("actual_arg") or "trigger") if r["actual_trigger"] else NO_TRIGGER
        confusion[(exp, str(act))] += 1
    return {
        "rows": len(rows),
        "errors": sum(1 for r in rows if r.get("error")),
        "either": len(either),
        "either_triggered": sum(1 for r in either if r.get("actual_trigger")),
        "tp": len(tp), "fn": len(fn), "fp": len(fp), "tn": len(tn),
        "recall": _rate(len(tp), len(tp) + len(fn)),
        "precision": _rate(len(tp), len(tp) + len(fp)),
        "arg_accuracy": _rate(sum(1 for r in arg_scored if r["arg_score"] == 1.0), len(arg_scored)),
        "exact": _rate(sum(1 for r in ok if r["scores"].get("exact") == 1.0), len(ok)),
        "missed": [r["row_id"] for r in fn],
        # Trials per row that should trigger, so a miss can be read as a rate.
        "trigger_trials": dict(Counter(r["row_id"] for r in ok if r["expect_trigger"])),
        "false_alarms": [r["row_id"] for r in fp],
        "confusion": {f"{e}|{a}": n for (e, a), n in sorted(confusion.items())},
    }


def judge_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if not r.get("error") and r.get("judge_score") is not None]
    return {
        "rows": len(rows),
        "errors": sum(1 for r in rows if r.get("error")),
        "unscored": sum(1 for r in rows if not r.get("error") and r.get("judge_score") is None),
        "mean": _rate(sum(r["judge_score"] for r in scored), len(scored)) if scored else None,
        "fails": [r["row_id"] for r in scored if r["judge_score"] < 0.5],
    }


def _outcome(row: dict[str, Any], scorer: str) -> Any:
    if row.get("error"):
        return "error"
    if scorer == "tool-call":
        return (row["actual_trigger"], row.get("actual_arg"))
    return row.get("judge_score")


def _majority(outcomes: list[Any]) -> Any:
    counts = Counter(json.dumps(o, default=str) for o in outcomes)
    top = max(counts.values())
    # A tie has no majority; it reads as its own outcome rather than picking a side.
    winners = sorted(k for k, n in counts.items() if n == top)
    return json.loads(winners[0]) if len(winners) == 1 else "tie"


def disagreements(versions: list[dict[str, Any]], scorer: str) -> list[dict[str, Any]]:
    """Rows where versions' majority outcome differs. With one trial, the outcome itself."""
    by_row: dict[str, dict[str, Any]] = {}
    for v in versions:
        for r in v["rows"]:
            entry = by_row.setdefault(r["row_id"], {"row_id": r["row_id"], "case_id": r.get("case_id"),
                                                    "expected": r.get("label"), "trials": {}})
            entry["trials"].setdefault(v["label"], []).append(_outcome(r, scorer))
    out = []
    for entry in by_row.values():
        entry["outcomes"] = {lbl: _majority(o) for lbl, o in entry["trials"].items()}
        if len({json.dumps(o, default=str) for o in entry["outcomes"].values()}) > 1:
            out.append(entry)
    return out


def group_metrics(rows: list[dict[str, Any]], scorer: str) -> dict[str, dict[str, Any]]:
    """Per-group score: mean judge score, or the exact-match rate for tool-call. Rows without a group are left out."""
    groups: dict[str, list[float]] = {}
    for r in rows:
        if not r.get("group") or r.get("error") or r.get("skip"):
            continue
        if scorer == "tool-call":
            val = r.get("scores", {}).get("exact")
        else:
            val = r.get("judge_score")
        if val is not None:
            groups.setdefault(r["group"], []).append(float(val))
    return {g: {"n": len(v), "score": sum(v) / len(v)} for g, v in sorted(groups.items())}


def summarize(results: dict[str, Any]) -> dict[str, Any]:
    scorer = results["scorer"]
    metric_fn = tool_call_metrics if scorer == "tool-call" else judge_metrics
    return {
        "scorer": scorer,
        "versions": [{"label": v["label"], "experiment_url": v.get("experiment_url"),
                      **metric_fn(v["rows"]), "groups": group_metrics(v["rows"], scorer)} for v in results["versions"]],
        "disagreements": disagreements(results["versions"], scorer),
    }


def new_missed_rows(base: dict[str, Any], cand: dict[str, Any]) -> list[str]:
    """Rows the candidate misses at a higher rate than the baseline, as `id (a/n → b/m)`.

    A rate, not a set: with trials, a row the baseline missed once and the
    candidate missed every time is a regression, and one the baseline never
    missed but the candidate missed once is still one.
    """
    bm, cm = Counter(base["missed"]), Counter(cand["missed"])
    bt, ct = base.get("trigger_trials") or {}, cand.get("trigger_trials") or {}
    out = []
    for row in sorted(cm):
        b_rate = bm[row] / bt[row] if bt.get(row) else 0.0
        c_rate = cm[row] / ct[row] if ct.get(row) else 1.0
        if c_rate > b_rate:
            out.append(f"{row} ({bm[row]}/{bt.get(row, 0)} → {cm[row]}/{ct.get(row, 0)})")
    return out


def _counted(ids: list[str]) -> str:
    """Row ids, with a ×n count when trials repeat a row."""
    if not ids:
        return "none"
    return ", ".join(f"{i} ×{n}" if n > 1 else i for i, n in Counter(ids).items())


def _fmt_outcome(o: Any) -> str:
    if isinstance(o, (list, tuple)):
        trig, arg = o
        return f"`{arg}`" if trig and arg else ("trigger" if trig else "no trigger")
    return str(o)


def render_markdown(summary: dict[str, Any], baseline: str | None = None) -> str:
    lines: list[str] = []
    versions = summary["versions"]
    if summary["scorer"] == "tool-call":
        lines += ["| Version | Recall | Precision | Arg accuracy | Exact | TP/FN/FP/TN | Either (triggered) | Errors |",
                  "|---|---|---|---|---|---|---|---|"]
        for v in versions:
            lines.append(f"| {v['label']} | {_pct(v['recall'])} | {_pct(v['precision'])} | "
                         f"{_pct(v['arg_accuracy'])} | {_pct(v['exact'])} | "
                         f"{v['tp']}/{v['fn']}/{v['fp']}/{v['tn']} | {v['either']} ({v['either_triggered']}) | {v['errors']} |")
        for v in versions:
            lines += ["", f"### {v['label']}"]
            if v.get("experiment_url"):
                lines.append(f"Experiment: {v['experiment_url']}")
            lines.append(f"- Missed triggers ({len(v['missed'])}): {_counted(v['missed'])}")
            lines.append(f"- False alarms ({len(v['false_alarms'])}): {_counted(v['false_alarms'])}")
            lines.append("- Confusion (expected → actual): " + "; ".join(
                f"{k.replace('|', ' → ')}: {n}" for k, n in v["confusion"].items()))
        if baseline:
            base = next((v for v in versions if v["label"] == baseline), None)
            if base:
                for v in versions:
                    if v is base:
                        continue
                    new_misses = new_missed_rows(base, v)
                    lines.append("")
                    lines.append(f"**New missed triggers in {v['label']} vs {baseline}: "
                                 f"{len(new_misses)}**" + (f" — {', '.join(new_misses)}" if new_misses else ""))
    else:
        lines += ["| Version | Mean judge score | Fails (<0.5) | Unscored | Errors |", "|---|---|---|---|---|"]
        for v in versions:
            lines.append(f"| {v['label']} | {_pct(v['mean'])} | {len(v['fails'])} | {v['unscored']} | {v['errors']} |")
        for v in versions:
            if v.get("experiment_url"):
                lines.append(f"\n{v['label']}: {v['experiment_url']}")
    groups = sorted({g for v in versions for g in v.get("groups") or {}})
    if groups:
        what = "exact-match rate" if summary["scorer"] == "tool-call" else "mean judge score"
        lines += ["", f"### By group ({what})", "",
                  "| Group | " + " | ".join(v["label"] for v in versions) + " |",
                  "|---|" + "---|" * len(versions)]
        for g in groups:
            cells = []
            for v in versions:
                m = (v.get("groups") or {}).get(g)
                cells.append(f"{_pct(m['score'])} (n={m['n']})" if m else "—")
            lines.append(f"| {g} | " + " | ".join(cells) + " |")
    dis = summary["disagreements"]
    lines += ["", f"### Rows where versions disagree ({len(dis)})"]
    for d in dis[:50]:
        outs = ", ".join(f"{k}: {_fmt_outcome(o)}" + (f" ({len(d['trials'][k])} trials)" if len(d['trials'][k]) > 1 else "")
                         for k, o in d["outcomes"].items())
        lines.append(f"- `{d['case_id'] or d['row_id']}` (expected `{d['expected']}`) — {outs}")
    if len(dis) > 50:
        lines.append(f"- … {len(dis) - 50} more in summary.json")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Re-render the readout from a results.json.")
    p.add_argument("results", help="results.json written by run_comparison.py")
    p.add_argument("--baseline", help="Version label to diff new misses against (default: the first).")
    args = p.parse_args()
    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    summary = summarize(results)
    baseline = args.baseline or (results["versions"][0]["label"] if results["versions"] else None)
    sys.stdout.write(render_markdown(summary, baseline))
    return 0


if __name__ == "__main__":
    sys.exit(main())
