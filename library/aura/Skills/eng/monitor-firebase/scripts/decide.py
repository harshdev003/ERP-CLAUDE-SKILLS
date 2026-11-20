#!/usr/bin/env python3
"""Pick which alert findings to post: each alert key at most once per cooldown.

  decide.py --findings <findings.jsonl> [--cooldown-hours 6] [--record]

Prints the alert findings not posted within the cooldown, one JSON line each. With --record, marks
them as posted in the state dir (run it after the Slack post succeeds). Prints nothing when there
is nothing to post: a clean pass stays silent.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import common


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--findings", required=True)
    ap.add_argument("--cooldown-hours", type=float, default=6)
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    path = common.state_dir() / "posted.json"
    posted = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    now = time.time()
    posted = {k: t for k, t in posted.items() if now - t < 7 * 86400}
    fresh = []
    for line in open(a.findings, encoding="utf-8"):
        try:
            f = json.loads(line)
        except ValueError:
            continue
        if f.get("level") == "alert" and now - posted.get(f["key"], 0) >= a.cooldown_hours * 3600:
            if f["key"] not in {x["key"] for x in fresh}:
                fresh.append(f)
    for f in fresh:
        print(json.dumps(f))
        if a.record:
            posted[f["key"]] = now
    if a.record:
        path.write_text(json.dumps(posted), encoding="utf-8")


if __name__ == "__main__":
    main()
