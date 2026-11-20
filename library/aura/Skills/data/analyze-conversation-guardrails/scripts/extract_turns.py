#!/usr/bin/env python3
"""Fetch each alert's conversation and extract the turn that triggered it.

Deterministic, read-only. For every alert in `sweep_alerts.py`'s output that still needs a
classification (no `Trigger reason:` line on its triage reply, unless --all), runs the bundled `fetch_conversation.py` (10 in parallel, one cached JSON
per alert ts under --cache-dir, so a re-run fetches only what is missing), then emits one
compact record per alert: the user message right before the guardrail reply that fired the
alert, the user message before that, and the handoff category of the alert turn.

Output: `turns.txt` (one line per alert, numbered, for the model to classify in batches)
and `turns.json` (the same records, keyed by alert ts). Neither leaves this machine: they
hold conversation text and are working files, never posted.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
FETCH = os.path.join(HERE, "fetch_conversation.py")


def fetch(alert: dict, cache_dir: str, no_gcp: bool) -> str:
    """Fetch one conversation into the cache. A failure leaves no file, so the alert is
    recorded as not analysable rather than aborting the batch."""
    path = os.path.join(cache_dir, f"{alert['ts']}.json")
    if os.path.exists(path) and os.path.getsize(path):
        return path
    cmd = [sys.executable, FETCH, "--conversation-id", alert["conversation"],
           "--json", path]
    if alert.get("user"):
        cmd += ["--user-id", alert["user"]]
    if alert.get("transaction"):
        cmd += ["--transaction", alert["transaction"]]
    if no_gcp:
        cmd.append("--no-gcp")
    try:
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"fetch failed for alert {alert['ts']}: {type(exc).__name__}", file=sys.stderr)
    return path


def extract(alert: dict, path: str, user_chars: int, prev_chars: int) -> dict:
    rec = {"ts": alert["ts"], "user": alert.get("user"), "category": None,
           "trigger": None, "previous": None, "turns": 0, "analysable": False}
    if not (os.path.exists(path) and os.path.getsize(path)):
        return rec
    with open(path, encoding="utf-8") as fh:
        report = json.load(fh)
    msgs = (report.get("messages") or []) + (report.get("ledger_only_messages") or [])
    rec["turns"] = len(msgs)
    tx = (report.get("alert_transaction") or "").upper()
    rec["category"] = next((h.get("category") for h in report.get("handoffs") or []
                            if tx and (h.get("transaction") or "").upper() == tx), None)
    idx = next((i for i, m in enumerate(msgs) if m.get("is_alert_turn")), None)
    if idx is None:
        idx = next((i for i, m in enumerate(msgs) if m.get("guardrail_handled")), None)
    if idx is None:
        return rec
    rec["category"] = rec["category"] or msgs[idx].get("handoff_category")
    # trigger_reasons.md: when the alert's user message answers an earlier guardrail
    # reply ("But I'm not gonna do that"), judge the message that started the run.
    while True:
        u = next((i for i in range(idx - 1, -1, -1) if msgs[i].get("role") == "user"), None)
        g = next((i for i in range(u - 1, -1, -1) if msgs[i].get("role") == "assistant"), None) \
            if u is not None else None
        if g is None or not msgs[g].get("guardrail_handled"):
            break
        # Never walk past the last user message with text: an incomplete transcript
        # can open on a handler reply, and the follow-up is better than no trigger.
        if not any(m.get("role") == "user" and (m.get("text") or "").strip() for m in msgs[:g]):
            break
        idx = g
    users = [m for m in msgs[:idx] if m.get("role") == "user" and (m.get("text") or "").strip()]
    flat = lambda m, n: " ".join((m.get("text") or "").split())[:n]
    if users:
        rec["trigger"] = flat(users[-1], user_chars)
        rec["analysable"] = True
    if len(users) > 1:
        rec["previous"] = flat(users[-2], prev_chars)
    return rec


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--alerts", required=True, help="sweep_alerts.py JSON")
    ap.add_argument("--cache-dir", required=True, help="Per-alert conversation JSON cache")
    ap.add_argument("--out-dir", required=True, help="Where turns.txt / turns.json go")
    ap.add_argument("--all", action="store_true",
                    help="Also fetch alerts whose triage reply already carries a Trigger reason line")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--no-gcp", action="store_true", help="Skip the GCP handoff-category lookup")
    ap.add_argument("--trigger-chars", type=int, default=320)
    ap.add_argument("--previous-chars", type=int, default=140)
    args = ap.parse_args()

    with open(args.alerts, encoding="utf-8") as fh:
        alerts = json.load(fh)["alerts"]
    todo = [a for a in alerts if a.get("conversation") and (args.all or not a.get("reason_line"))]
    os.makedirs(args.cache_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        paths = list(pool.map(lambda a: fetch(a, args.cache_dir, args.no_gcp), todo))

    records = [extract(a, p, args.trigger_chars, args.previous_chars) for a, p in zip(todo, paths)]
    with open(os.path.join(args.out_dir, "turns.json"), "w", encoding="utf-8") as fh:
        json.dump({r["ts"]: r for r in records}, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out_dir, "turns.txt"), "w", encoding="utf-8") as fh:
        for i, r in enumerate(records):
            fh.write(f"#{i} ts={r['ts']} [{r['category']}] PREV: {r['previous'] or ''} || "
                     f"TRIG: {r['trigger'] or '-'}\n")
    print(json.dumps({"alerts": len(alerts), "to_classify": len(todo),
                      "analysable": sum(r["analysable"] for r in records),
                      "not_analysable": sum(not r["analysable"] for r in records)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
