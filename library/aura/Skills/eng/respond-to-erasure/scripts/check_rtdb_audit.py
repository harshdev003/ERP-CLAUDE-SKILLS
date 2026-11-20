#!/usr/bin/env python3
"""Confirm the RTDB side of an account delete from FirebaseDenormalizer's audit line.

FirebaseDenormalizer (2.6.0+) writes one always-kept line per deleted user when its
`userDeleted` trigger finishes: "Deleted secondary nodes for user '{uid}'", with
`removed` and `failed` node counts. The trigger only fires once `/users/{uid}` is gone,
so the line also covers that node.

Prints one JSON object, never the uid or any payload beyond the counts and node names:
  {"status": "done" | "failed" | "unverified", "removed": n, "failed": n, "failed_nodes": [...],
   "timestamp": ..., "reason": ...}
- done: the line exists and failed == 0
- failed: the line exists and failed > 0. `failed_nodes` names them, read from the run's error
  lines ("Error removing reference", "... in batches", "Partially removed reference"), which share
  the summary line's `transaction`; one line per failed node. It is null when those lines could
  not be read, and may be shorter than `failed` when some are missing.
- unverified: no line yet, more than one, or the log could not be read

Auth: GCP_KEY (logging read on firebase-auratech16), AuraClaude user agent via gcp.py.

Usage:
  python3 check_rtdb_audit.py --uid UID --since 2026-10-24T06:05:00Z
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys

PROJECT = "firebase-auratech16"
LOG_NAME = f"projects/{PROJECT}/logs/firebase-denormalizer"
MESSAGE = "Deleted secondary nodes for user"
# removeReference logs exactly one of these per failed node (userDeleted.js).
FAILURE_MESSAGES = ("Error removing reference", "Partially removed reference")


def _count(value) -> int | None:
    """jsonPayload numbers come back as floats (69.0); anything else is not a count."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value) or value < 0:
        return None
    return int(value)


def _failed_nodes(gcp, auth, transaction, lo) -> list[str] | None:
    """Node names from the run's error lines, one per failed node; None when they can't be read."""
    if not isinstance(transaction, str) or not transaction or '"' in transaction:
        return None
    flt = (
        f'logName="{LOG_NAME}" AND jsonPayload.transaction="{transaction}" '
        f'AND severity>=ERROR AND timestamp>="{lo.isoformat()}"'
    )
    try:
        entries = list(gcp.list_entries(auth, flt, max_entries=200, order_by="timestamp asc"))
    except Exception:  # noqa: BLE001  unknown, never an empty list
        return None
    refs = []
    for e in entries:
        payload = e.get("jsonPayload") or {}
        ref = payload.get("reference")
        # Only removeReference's failure lines, one per reference: a retried or repeated line must
        # not stand in for a failed node it doesn't name.
        if str(payload.get("message", "")).startswith(FAILURE_MESSAGES) and isinstance(ref, str) and ref.strip("/"):
            if ref not in refs:
                refs.append(ref)
    return [r.strip("/").split("/")[0] for r in refs]  # the node only, never the key (a uid or email)


def check(uid: str, since: str) -> dict:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gcp  # noqa: E402  (canonical helper copy, see docs/canonical-sources.md)

    # Start a minute early: the delete time is read off our clock, the line off GCP's.
    lo = dt.datetime.fromisoformat(since.replace("Z", "+00:00")) - dt.timedelta(minutes=1)
    flt = (
        f'logName="{LOG_NAME}" AND jsonPayload.userId="{uid}" '
        f'AND jsonPayload.message:"{MESSAGE}" AND timestamp>="{lo.isoformat()}"'
    )
    try:
        auth = gcp.get_client(project_override=PROJECT)
        entries = list(gcp.list_entries(auth, flt, max_entries=10, order_by="timestamp asc"))
    except Exception as e:  # noqa: BLE001  any read failure is "unverified", never "done"
        return {"status": "unverified", "reason": f"log read failed: {type(e).__name__}"}

    if not entries:
        return {"status": "unverified", "reason": "no audit line yet"}
    if len(entries) > 1:
        return {"status": "unverified", "reason": f"{len(entries)} audit lines, expected one"}

    payload = entries[0].get("jsonPayload") or {}
    removed, failed = _count(payload.get("removed")), _count(payload.get("failed"))
    out = {"removed": removed, "failed": failed, "timestamp": entries[0].get("timestamp")}
    if removed is None or failed is None:
        return {**out, "status": "unverified", "reason": "audit line has no counts"}
    if failed > 0:
        nodes = _failed_nodes(gcp, auth, payload.get("transaction"), lo)
        return {**out, "status": "failed", "failed_nodes": nodes, "reason": f"{failed} node(s) not removed"}
    return {**out, "status": "done"}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uid", required=True, help="the erased user's Firebase UID")
    p.add_argument("--since", required=True, help="ISO time the account delete was called (UTC)")
    a = p.parse_args()
    print(json.dumps(check(a.uid, a.since)))


if __name__ == "__main__":
    main()
