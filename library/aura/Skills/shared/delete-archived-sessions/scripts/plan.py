#!/usr/bin/env python3
"""Turn a saved `list_sessions` result into delete batches.

`mcp__ccd_session_mgmt__list_sessions` with `include_archived: true` returns a JSON
array of sessions. On a busy machine it is too large to show and the harness saves it
to a file; this script reads that file (or a bare JSON file) and decides what to delete.

  plan.py plan  --listing FILE [--before YYYY-MM-DD] [--skip-open-prs] [--out batches.json]
  plan.py batches --file batches.json [--start N] [--count 10]
  plan.py check --listing FILE

`plan` keeps archived sessions only, never a pinned or running one (the app refuses
both), and writes the IDs in batches of 25 — the most `delete_session` takes per call.
Every session it leaves out is counted by reason, so the summary adds up to the listing.
`check` reports what is still archived after a run.

While an incident data hold is marked (`AURA_LEGAL_HOLD` set to anything but empty or 0, or the
file `~/.claude/legal-hold/active` exists), `plan` and `batches` refuse and exit 3, so no
delete list exists to act on. `check` only reads, so it still runs.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys

BATCH_SIZE = 25  # delete_session's per-call maximum
HOLD_EXIT = 3


def legal_hold() -> str | None:
    """The marker that puts sessions under an incident data hold, or None."""
    env = os.environ.get("AURA_LEGAL_HOLD", "").strip()
    if env and env != "0":
        return "AURA_LEGAL_HOLD is set"
    marker = pathlib.Path.home() / ".claude" / "legal-hold" / "active"
    if marker.exists():
        return f"{marker} exists"
    return None


def load_sessions(path: str) -> list[dict]:
    text = pathlib.Path(path).read_text(encoding="utf-8")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise SystemExit(f"no JSON array found in {path}")
    sessions = json.loads(text[start:end + 1])
    if not isinstance(sessions, list):
        raise SystemExit(f"{path}: expected a JSON array of sessions")
    return sessions


def plan(sessions: list[dict], before: str | None = None, skip_open_prs: bool = False) -> dict:
    keep: list[dict] = []
    skipped: collections.Counter = collections.Counter()
    for s in sessions:
        if not s.get("isArchived"):
            skipped["not_archived"] += 1
        elif s.get("pinned"):
            skipped["pinned"] += 1
        elif s.get("isRunning"):
            skipped["running"] += 1
        elif before and (s.get("lastActivityAt") or "")[:10] >= before:
            skipped["after_cutoff"] += 1
        elif skip_open_prs and s.get("prState") == "OPEN":
            skipped["open_pr"] += 1
        else:
            keep.append(s)
    ids = [s["sessionId"] for s in keep]
    dates = [s["lastActivityAt"][:10] for s in keep if s.get("lastActivityAt")]
    return {
        "listed": len(sessions),
        "to_delete": len(keep),
        "skipped": dict(skipped),
        "date_range": [min(dates), max(dates)] if dates else None,
        "by_month": dict(sorted(collections.Counter(d[:7] for d in dates).items())),
        "open_prs": [{"title": s.get("title"), "prNumber": s.get("prNumber")}
                     for s in keep if s.get("prState") == "OPEN"],
        "batches": [ids[i:i + BATCH_SIZE] for i in range(0, len(ids), BATCH_SIZE)],
    }


def check(sessions: list[dict]) -> dict:
    left = [s for s in sessions if s.get("isArchived")]
    return {
        "listed": len(sessions),
        "archived_left": len(left),
        "sessions": [{"title": s.get("title"), "pinned": bool(s.get("pinned")),
                      "lastActivityAt": (s.get("lastActivityAt") or "")[:10]} for s in left],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="choose the sessions to delete and batch them")
    p.add_argument("--listing", required=True, help="saved list_sessions result")
    p.add_argument("--before", help="keep only sessions last active before this date (YYYY-MM-DD)")
    p.add_argument("--skip-open-prs", action="store_true", help="leave sessions whose PR is still open")
    p.add_argument("--out", help="write the batches to this JSON file")
    c = sub.add_parser("check", help="report what is still archived")
    c.add_argument("--listing", required=True, help="saved list_sessions result")
    b = sub.add_parser("batches", help="print batches from a plan --out file, one per line")
    b.add_argument("--file", required=True, help="the plan --out file")
    b.add_argument("--start", type=int, default=1, help="first batch to print (1-based)")
    b.add_argument("--count", type=int, default=10, help="how many batches to print")
    args = ap.parse_args()

    if args.cmd in ("plan", "batches"):
        held = legal_hold()
        if held:
            print(f"REFUSED: an incident data hold is in place ({held}). No session may be deleted "
                  "until whoever placed the hold lifts it.", file=sys.stderr)
            sys.exit(HOLD_EXIT)

    if args.cmd == "batches":
        batches = json.loads(pathlib.Path(args.file).read_text(encoding="utf-8"))
        for n in range(args.start, min(args.start + args.count, len(batches) + 1)):
            print(n, len(batches), json.dumps(batches[n - 1], separators=(",", ":")))
        return
    sessions = load_sessions(args.listing)
    if args.cmd == "check":
        print(json.dumps(check(sessions), indent=2))
        return
    result = plan(sessions, args.before, args.skip_open_prs)
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(result["batches"]), encoding="utf-8")
    summary = {k: v for k, v in result.items() if k != "batches"}
    summary["batch_count"] = len(result["batches"])
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
