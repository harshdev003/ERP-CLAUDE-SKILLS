#!/usr/bin/env python3
"""Build the per-alert trigger dataset and roll it up by reason, user and category.

Deterministic. Joins three inputs into one record per alert:

* `sweep_alerts.py` alerts — ids, the triage severity and any `Trigger reason:` line;
* `extract_turns.py` turns.json — the handoff category of the alert turn, falling back to
  the category `sweep_alerts.py` lifted from the triage reply for alerts it did not fetch;
* the model's codes file — `{alert_ts: {"reason", "subject", "asked_for", "severity",
  "in_care"}}` for alerts without a triage line. A code overrides the triage line.

The reason list is read from `references/trigger_reasons.md` (the `| \\`code\\` |` rows of its
Reasons table), so the dataset and the triage skill share one vocabulary. Alerts with no
reason are kept as `unclassified`, never dropped.

Each record also carries the prompt file that fired (`prompt`, the alert's `*Prompt:*` value,
or `base prompt` when the alert has no such line: Intelligence Service prints it only for an
experiment arm), and the rollup counts alerts and severity per prompt file, so an A/B test on
the coach prompt can be read per arm.

Outputs: --dataset (JSON list, one record per alert — local only, it holds user ids) and a
Markdown rollup on stdout. Severity is counted once per user (worst wins); a reason row
counts every user with at least one alert of that reason, so reason rows can overlap.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys

REASON_ROW = re.compile(r"^\| `([a-z-]+)` \|", re.M)
SEVERITY_ORDER = ["crisis", "non-crisis", "false-positive", "unknown", "unclassified"]
UNCLASSIFIED = "unclassified"
NO_PROMPT = "base prompt"



def load_reasons(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    section = text.split("## Reasons", 1)[1].split("\n## ", 1)[0]
    reasons = REASON_ROW.findall(section)
    if not reasons:
        raise SystemExit(f"{path}: no reason rows under ## Reasons")
    return reasons


def build(alerts: list[dict], turns: dict, codes: dict, reasons: list[str]) -> list[dict]:
    rows = []
    for a in alerts:
        code = codes.get(a["ts"]) or {}
        line = a.get("reason_line") or {}
        src = "model" if code else ("triage_line" if line else UNCLASSIFIED)
        pick = lambda k, default=None: code.get(k, line.get(k, default))
        reason = pick("reason", UNCLASSIFIED)
        rows.append({
            "ts": a["ts"], "user": a.get("user"), "conversation": a.get("conversation"),
            "category": (turns.get(a["ts"]) or {}).get("category") or a.get("category") or "unknown",
            "reason": reason,
            "known_reason": reason in reasons or reason == UNCLASSIFIED,
            "subject": pick("subject", "unclear"),
            "asked_for": pick("asked_for", "none"),
            "in_care": bool(pick("in_care", False)),
            "severity": code.get("severity") or a.get("severity") or UNCLASSIFIED,
            "prompt": a.get("prompt") or NO_PROMPT,
            "source": src,
        })
    return rows


def rollup(rows: list[dict], reasons: list[str]) -> dict:
    users = {r["user"] or f"?{r['ts']}" for r in rows}
    sev_rank = {s: i for i, s in enumerate(SEVERITY_ORDER)}
    worst: dict[str, str] = {}
    by_reason = collections.defaultdict(lambda: {"alerts": 0, "users": set(), "self": set(),
                                                 "asked_professional": set(), "crisis": 0})
    by_cat = collections.defaultdict(collections.Counter)
    by_prompt = collections.defaultdict(lambda: {"alerts": 0, "users": set(),
                                                 "severity": collections.Counter()})
    per_user_alerts = collections.Counter()
    for r in rows:
        u = r["user"] or f"?{r['ts']}"
        per_user_alerts[u] += 1
        s = r["severity"] if r["severity"] in sev_rank else UNCLASSIFIED
        if u not in worst or sev_rank[s] < sev_rank[worst[u]]:
            worst[u] = s
        b = by_reason[r["reason"]]
        b["alerts"] += 1
        b["users"].add(u)
        if r["subject"] == "self":
            b["self"].add(u)
        if r["asked_for"] == "professional-help":
            b["asked_professional"].add(u)
        if r["severity"] == "crisis":
            b["crisis"] += 1
        by_cat[r["category"]][r["reason"]] += 1
        p = by_prompt[r.get("prompt") or NO_PROMPT]
        p["alerts"] += 1
        p["users"].add(u)
        p["severity"][s] += 1
    order = reasons + [UNCLASSIFIED] + sorted(set(by_reason) - set(reasons) - {UNCLASSIFIED})
    return {
        "alerts": len(rows), "users": len(users),
        "conversations": len({r["conversation"] for r in rows}),
        "users_by_severity": dict(collections.Counter(worst.values())),
        "reasons": [{"reason": k, "alerts": v["alerts"], "users": len(v["users"]),
                     "self_users": len(v["self"]), "asked_professional_users": len(v["asked_professional"]),
                     "crisis_alerts": v["crisis"]}
                    for k in order if (v := by_reason.get(k))],
        "categories": {c: dict(cnt.most_common()) for c, cnt in sorted(by_cat.items())},
        "prompts": [{"prompt": k, "alerts": v["alerts"], "users": len(v["users"]),
                     "severity": dict(v["severity"])}
                    for k, v in sorted(by_prompt.items())],
        "repeat_users": sum(1 for n in per_user_alerts.values() if n > 1),
        "max_alerts_one_user": max(per_user_alerts.values(), default=0),
        "source": dict(collections.Counter(r["source"] for r in rows)),
        "unknown_reasons": sorted({r["reason"] for r in rows if not r["known_reason"]}),
    }


def render(r: dict) -> str:
    L = [f"**{r['alerts']} alerts · {r['users']} users · {r['conversations']} conversations**", ""]
    if r["alerts"] > r["conversations"]:
        L += [f"⚠️ {r['alerts'] - r['conversations']} alert(s) share a conversation with another "
              "alert (expected one alert per conversation).", ""]
    L += ["| Worst severity per user | Users |", "|---|---:|"]
    for s in SEVERITY_ORDER:
        if r["users_by_severity"].get(s):
            L.append(f"| {s} | {r['users_by_severity'][s]} |")
    L += ["", "| Trigger reason | Alerts | Users | About themselves | Asked for a professional | Crisis alerts |",
          "|---|---:|---:|---:|---:|---:|"]
    for x in r["reasons"]:
        L.append(f"| `{x['reason']}` | {x['alerts']} | {x['users']} | {x['self_users']} | "
                 f"{x['asked_professional_users']} | {x['crisis_alerts']} |")
    L += ["", "_A user with alerts of several reasons appears in each of those rows._", "",
          "| Handoff category | Alerts | Top reasons |", "|---|---:|---|"]
    for cat, cnt in r["categories"].items():
        top = ", ".join(f"`{k}` {v}" for k, v in list(cnt.items())[:3])
        L.append(f"| `{cat}` | {sum(cnt.values())} | {top} |")
    if any(p["prompt"] != NO_PROMPT for p in r["prompts"]):
        L += ["", "| Prompt that fired | Alerts | Users | Crisis | Non-crisis | False positive |",
              "|---|---:|---:|---:|---:|---:|"]
        for p in r["prompts"]:
            sev = p["severity"]
            L.append(f"| `{p['prompt']}` | {p['alerts']} | {p['users']} | {sev.get('crisis', 0)} | "
                     f"{sev.get('non-crisis', 0)} | {sev.get('false-positive', 0)} |")
        L += ["", "_Counts per arm, not rates: divide by each arm's assigned users before comparing._"]
    L += ["", f"Repeat users: {r['repeat_users']} (max {r['max_alerts_one_user']} alerts for one user). "
          f"Reasons from: {', '.join(f'{k} {v}' for k, v in sorted(r['source'].items()))}."]
    if r["unknown_reasons"]:
        L.append(f"⚠️ Reasons not in trigger_reasons.md: {', '.join(r['unknown_reasons'])}.")
    return "\n".join(L)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--alerts", required=True, help="sweep_alerts.py JSON")
    ap.add_argument("--turns", help="extract_turns.py turns.json (handoff categories)")
    ap.add_argument("--codes", help="Model codes JSON {alert_ts: {reason, subject, asked_for, severity, in_care}}")
    ap.add_argument("--reasons", required=True, help="references/trigger_reasons.md")
    ap.add_argument("--dataset", help="Write the per-alert dataset JSON here")
    ap.add_argument("--json", dest="json_out", help="Write the rollup numbers here")
    args = ap.parse_args()

    def load(path, default):
        if not path:
            return default
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    alerts = load(args.alerts, {})["alerts"]
    reasons = load_reasons(args.reasons)
    rows = build(alerts, load(args.turns, {}), load(args.codes, {}), reasons)
    result = rollup(rows, reasons)
    if args.dataset:
        with open(args.dataset, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
    print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
