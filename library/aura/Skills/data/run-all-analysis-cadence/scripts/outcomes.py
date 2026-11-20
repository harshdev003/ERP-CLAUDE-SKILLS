#!/usr/bin/env python3
"""Turn an unattended cadence run's per-step outcomes into the follow-ups it must raise.

The run appends one JSON object per line as each step it was due to run ends (a group, or a
harvest/healthcheck leg):

    {"step": "conversations", "status": "ok"}
    {"step": "cost", "status": "gate", "reason": "ledger plan waits for a person", "rerun": "data:run-cost-harvest-cadence"}
    {"step": "knowledge", "status": "failed", "reason": "NOTION_TOKEN is not set"}

A JSON list of the same objects is read too. A missing or empty file means nothing was
recorded: every due step counts as `stopped`.

Statuses: `ok` and `out-of-period` raise nothing. `failed`, `blocked` and `stopped` (the run
ended before the step ran: an expired wait, an early end) raise `blocked`. `gate` (a write
that waits for a person) raises `decision`. A step the run was due to run but has no entry for
is `stopped`: a missing entry never reads as a success.

Prints a JSON list, one object per follow-up, holding the fields to pass to
shared:raise-followup. The key is stable per cadence and step, so a step that stops every run
stays one post with replies. Exit 0 when nothing needs raising, 1 when something does, 2 on a
bad input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

RAISE = {"failed": "blocked", "blocked": "blocked", "stopped": "blocked", "gate": "decision"}
QUIET = {"ok", "out-of-period"}
# What shared:raise-followup refuses outside a link, so a reason never gets its post refused:
# a full id (a uid, a sha, a page id: 20+ letters and digits mixed, or a dashed UUID), an
# email, an IP address or a token.
_UUID = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I)
_FULL_ID = re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{20,}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9A-Fa-f]{1,4}:){3,}[0-9A-Fa-f:]*\b")
_TOKEN = re.compile(r"\b(?:xox[abposr]-[\w-]+|sk-[\w-]{20,}|gh[pousr]_\w{20,}|github_pat_\w+|ntn_\w{20,}"
                    r"|lin_api_\w+|AIza[\w-]{30,}|Bearer\s+\S{16,})")


def _clean(text: str) -> str:
    """One line, with anything raise-followup would refuse cut or replaced."""
    first = (str(text or "").strip().splitlines() or [""])[0]
    first = _TOKEN.sub("[token]", first)
    first = _EMAIL.sub("[email]", first)
    first = _IP.sub("[ip]", first)
    first = _UUID.sub(lambda m: m.group(0)[:8] + "…", first)
    first = _FULL_ID.sub(lambda m: m.group(0)[:8] + "…", first)
    return first[:300]


def followups(cadence: str, session: str, outcomes: list[dict], due: list[str]) -> list[dict]:
    seen = {o.get("step") for o in outcomes}
    outcomes = list(outcomes) + [{"step": s, "status": "stopped",
                                  "reason": "the run ended before this step reported an outcome"}
                                 for s in due if s not in seen]
    out = []
    for o in outcomes:
        step, status = str(o.get("step") or "").strip(), str(o.get("status") or "").strip()
        if not step:
            raise ValueError(f"an outcome has no step: {o}")
        if status in QUIET:
            continue
        if status not in RAISE:
            raise ValueError(f"unknown status {status!r} for step {step}")
        kind = RAISE[status]
        reason = _clean(o.get("reason")) or "no reason recorded"
        rerun = o.get("rerun") or ""
        verb = {"gate": "is waiting at a write gate", "stopped": "did not run"}.get(status, f"{status}")
        out.append({
            "kind": kind,
            "title": f"{cadence}: {step} {verb}",
            "what": f"The unattended {cadence} run: step {step} {verb}. Reason: {reason}",
            "why": "This step's data stays stale until it runs; nothing else reports it.",
            "ask": ("Decide on the waiting write, then re-run the step." if kind == "decision"
                    else "Fix the cause, then re-run the step.") + (f" Re-run with {rerun}." if rerun else ""),
            "key": f"{cadence}:{step}:stop",
            "session": session,
        })
    return out


def read_outcomes(path: str) -> list:
    """JSON lines (the append format) or one JSON list; missing or empty means none recorded."""
    try:
        raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return []
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        outcomes = json.loads(raw)
    else:
        outcomes = []
        for n, line in enumerate(raw.splitlines(), 1):
            if line.strip():
                try:
                    outcomes.append(json.loads(line))
                except ValueError as e:
                    raise ValueError(f"line {n} is not JSON: {e}") from None
    if not all(isinstance(o, dict) for o in outcomes):
        raise ValueError("every outcome must be a JSON object")
    return outcomes


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cadence", required=True, help="the cadence skill name, e.g. run-all-harvest-cadence")
    ap.add_argument("--session", required=True, help="the routine or session raising them")
    ap.add_argument("--in", dest="path", required=True, help="the outcomes JSON file ('-' for stdin)")
    ap.add_argument("--due", default="", help="comma-separated steps the run was due to run")
    args = ap.parse_args(argv)
    try:
        outcomes = read_outcomes(args.path)
        due = [s.strip() for s in args.due.split(",") if s.strip()]
        result = followups(args.cadence, args.session, outcomes, due)
    except (OSError, ValueError) as e:
        print(f"outcomes: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result else 0


if __name__ == "__main__":
    sys.exit(main())
