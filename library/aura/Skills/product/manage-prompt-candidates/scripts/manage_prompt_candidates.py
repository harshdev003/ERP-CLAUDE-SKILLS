#!/usr/bin/env python3
"""Read and update prompt-change candidates in the Aurie Conversations Ideas database.

The status, queue and decision writer for the Conversations loop — the companion
to ``record-prompt-candidates``, which creates rows but never moves them.

Every write goes through ``_transition``, which appends one line to Quick status
note saying who moved it, when and why. A status change with no note is the thing
that makes a backlog unreadable three months later, so the note is required on
every terminal transition rather than merely encouraged.

Self-contained per AINative principle 1: ``notion_rest.py`` is a per-skill copy of
the canonical at ``plugins/shared/skills/retrieve-knowledge/scripts/``.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import notion_rest  # noqa: E402

AURIE_CONVERSATIONS_DS = "3e375e23-af20-81d5-b99a-000b1b861edc"

# The lifecycle, in order. Used to render the funnel and to sanity-check a move.
LIFECYCLE = [
    "proposed", "shared", "approved", "drafted", "evaluated", "in_review",
    "merged", "released", "experiment_running",
]
TERMINAL = ["won", "lost", "inconclusive", "rolled_back", "abandoned"]
PARKED = ["rejected", "deferred", "held"]
ALL_STATUSES = LIFECYCLE + PARKED + TERMINAL

VALID_REJECTION_REASONS = {
    "already tried", "registered hard stop", "low impact", "off-strategy",
    "model not prompt", "timing", "duplicate", "other",
}
VALID_VERDICTS = {"won", "lost", "inconclusive", "rolled back"}

# Statuses whose meaning is "a decision was recorded". Moving OUT of one of these
# is allowed but always prints a warning: it is usually a mistake, and when it is
# not, the note needs to explain what changed.
DECIDED = set(PARKED) | set(TERMINAL)

# What `funnel` reports as the queue. Split by who is blocking, because a row at
# `shared` is not idle -- it is waiting on a human at Gate 1 -- and reporting it
# as loop work would have the loop re-post it.
LOOP_ACTIONS = (
    ("proposed", "share to the channel for Gate 1"),
    ("approved", "draft the change"),
    ("drafted", "run the gates"),
    ("evaluated", "post Gate 2"),
    ("released", "measure on the weekly scorecard"),
)
WAITING_ON = (
    ("shared", "a person, at Gate 1"),
    ("in_review", "review and merge"),
    ("merged", "the release"),
    ("experiment_running", "the experiment verdict"),
)


# ---- reading --------------------------------------------------------------

def _rows(ds_id: str) -> list[dict[str, Any]]:
    return list(notion_rest.notion.query_data_source(ds_id))


def candidate_id_str(row: dict[str, Any]) -> str:
    uid = (row.get("properties") or {}).get("Candidate ID", {}).get("unique_id") or {}
    prefix, number = uid.get("prefix"), uid.get("number")
    return f"{prefix}-{number}" if prefix and number is not None else "(no id)"


def _id_number(row: dict[str, Any]) -> int:
    uid = (row.get("properties") or {}).get("Candidate ID", {}).get("unique_id") or {}
    return uid.get("number") or 0


def resolve(ds_id: str, ref: str) -> dict[str, Any]:
    """Find one row by Candidate ID (CONV-9, conv-9, or bare 9) or title substring.

    Resolution is by ID first and deliberately so: the ID is immutable, where a
    title can be edited out from under a script or a Slack thread.
    """
    rows = _rows(ds_id)
    wanted = ref.strip().lower().removeprefix("conv-")
    if wanted.isdigit():
        for row in rows:
            if _id_number(row) == int(wanted):
                return row
        raise SystemExit(f"no candidate with id CONV-{wanted}")

    matches = [r for r in rows
               if ref.lower() in notion_rest.title_text(r, "Title").lower()]
    if not matches:
        raise SystemExit(f"no candidate matching {ref!r}")
    if len(matches) > 1:
        listing = "\n".join(f"  {candidate_id_str(r)} {notion_rest.title_text(r, 'Title')}"
                            for r in matches)
        raise SystemExit(f"{ref!r} matches {len(matches)} candidates:\n{listing}")
    return matches[0]


# ---- writing --------------------------------------------------------------

def _append_note(row: dict[str, Any], line: str) -> str:
    existing = notion_rest.rich_text_value(row, "Quick status note").strip()
    stamped = f"{date.today().isoformat()} — {line}"
    return f"{existing}\n{stamped}".strip() if existing else stamped


def _transition(ds_id: str, ref: str, *, status: str, note: str,
                extra: Optional[dict[str, Any]] = None,
                by: Optional[str] = None) -> int:
    row = resolve(ds_id, ref)
    current = notion_rest.select_value(row, "Status") or "unknown"
    cid = candidate_id_str(row)

    if current == status:
        print(f"{cid} is already {status} — nothing to do")
        return 0
    if current in DECIDED:
        print(f"WARNING: {cid} is {current}, which is a recorded decision.")
        print(f"         Moving it to {status} overwrites that. The note below is "
              f"the only record of why.")

    who = by or "the loop"
    props: dict[str, Any] = {
        "Status": notion_rest.make_select(status),
        "Quick status note": notion_rest.make_rich_text(
            _append_note(row, f"{current} → {status} by {who}. {note}".strip())),
        "Last seen": notion_rest.make_date(date.today().isoformat()),
    }
    if status in DECIDED:
        props["Decided at"] = notion_rest.make_date(date.today().isoformat())
    props.update(extra or {})

    notion_rest.notion.update_page_props(row["id"], props)
    print(f"{cid}  {current} → {status}")
    return 0


# ---- commands -------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    rows = sorted(_rows(args.data_source_id), key=_id_number)
    shown = 0
    for row in rows:
        status = notion_rest.select_value(row, "Status") or "-"
        prompter = notion_rest.select_value(row, "Prompter") or "-"
        klass = notion_rest.select_value(row, "Change class") or "-"
        if args.status and status != args.status:
            continue
        if args.prompter and prompter != args.prompter:
            continue
        if args.change_class and klass != args.change_class:
            continue
        print(f"  {candidate_id_str(row):<8} {status:<18} {klass:<14} "
              f"{prompter:<24} {notion_rest.title_text(row, 'Title')}")
        shown += 1
    if not shown:
        print("  (nothing matches)")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    row = resolve(args.data_source_id, args.ref)
    print(f"{candidate_id_str(row)}  {notion_rest.title_text(row, 'Title')}\n")
    for name in ("Status", "Prompter", "Change class", "Diagnosis", "Verdict",
                 "Rejection reason"):
        v = notion_rest.select_value(row, name)
        if v:
            print(f"  {name:<20} {v}")
    for name in ("Defect", "Evidence", "Cause", "Risk named", "Proposed diff",
                 "Gate results", "Braintrust version", "Release version",
                 "Experiment name", "Quick status note"):
        v = notion_rest.rich_text_value(row, name)
        if v:
            print(f"\n  {name}:\n    " + v.replace("\n", "\n    "))
    for name in ("Slack thread", "Linear issue", "PR", "Braintrust prompt"):
        v = (row.get("properties", {}).get(name) or {}).get("url")
        if v:
            print(f"\n  {name}: {v}")
    return 0


def cmd_funnel(args: argparse.Namespace) -> int:
    """Where the backlog stands, and what is actionable right now."""
    rows = _rows(args.data_source_id)
    counts: dict[str, int] = {}
    for row in rows:
        s = notion_rest.select_value(row, "Status") or "unknown"
        counts[s] = counts.get(s, 0) + 1

    print("lifecycle")
    for s in LIFECYCLE:
        if counts.get(s):
            print(f"  {s:<20} {counts[s]:>3}")
    print("parked")
    for s in PARKED:
        if counts.get(s):
            print(f"  {s:<20} {counts[s]:>3}")
    print("decided")
    for s in TERMINAL:
        if counts.get(s):
            print(f"  {s:<20} {counts[s]:>3}")

    # Two groups, because "actionable" and "in flight" are different questions
    # and collapsing them produces a queue that says both that work is waiting
    # and that nothing is waiting.
    def _render(label: str, pairs) -> int:
        shown = sum(counts.get(st, 0) for st, _ in pairs)
        if shown:
            print(f"\n{label}")
            for status, what in pairs:
                n = counts.get(status, 0)
                if n:
                    print(f"  {n:>3} {status:<20} {what}")
        return shown

    loop_work = _render("the loop can act on these now", LOOP_ACTIONS)
    waiting = _render("in flight, waiting on someone else", WAITING_ON)

    if not loop_work and not waiting:
        print("\nnothing in flight — run the digest")
    elif not loop_work:
        print("\nnothing for the loop to do — everything open is waiting on a person")
    return 0


def cmd_set_status(args: argparse.Namespace) -> int:
    if args.status not in ALL_STATUSES:
        raise SystemExit(f"status must be one of {ALL_STATUSES}")
    return _transition(args.data_source_id, args.ref, status=args.status,
                       note=args.note, by=args.by)


def cmd_approve(args: argparse.Namespace) -> int:
    return _transition(args.data_source_id, args.ref, status="approved",
                       note=args.note, by=args.by)


def cmd_reject(args: argparse.Namespace) -> int:
    if args.reason not in VALID_REJECTION_REASONS:
        raise SystemExit(f"reason must be one of {sorted(VALID_REJECTION_REASONS)}")
    return _transition(args.data_source_id, args.ref, status="rejected",
                       note=args.note, by=args.by,
                       extra={"Rejection reason": notion_rest.make_select(args.reason)})


def cmd_defer(args: argparse.Namespace) -> int:
    return _transition(args.data_source_id, args.ref, status="deferred",
                       note=args.note, by=args.by,
                       extra={"Rejection reason": notion_rest.make_select("timing")})


def cmd_verdict(args: argparse.Namespace) -> int:
    if args.verdict not in VALID_VERDICTS:
        raise SystemExit(f"verdict must be one of {sorted(VALID_VERDICTS)}")
    status = {"won": "won", "lost": "lost", "inconclusive": "inconclusive",
              "rolled back": "rolled_back"}[args.verdict]
    return _transition(args.data_source_id, args.ref, status=status,
                       note=args.note, by=args.by,
                       extra={"Verdict": notion_rest.make_select(args.verdict)})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-source-id", default=AURIE_CONVERSATIONS_DS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="list rows, optionally filtered")
    sp.add_argument("--status")
    sp.add_argument("--prompter")
    sp.add_argument("--change-class")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="print one candidate in full")
    sp.add_argument("ref", help="CONV-9, 9, or a title substring")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("funnel", help="counts by status, and what is actionable")
    sp.set_defaults(func=cmd_funnel)

    def _decision(name: str, fn, help_: str):
        s = sub.add_parser(name, help=help_)
        s.add_argument("ref")
        s.add_argument("--note", required=True, help="why — this is the record")
        s.add_argument("--by", help="who decided (defaults to 'the loop')")
        s.set_defaults(func=fn)
        return s

    _decision("approve", cmd_approve, "Gate 1 passed — the diagnosis is agreed")
    r = _decision("reject", cmd_reject, "declined, with a reason from the vocabulary")
    r.add_argument("--reason", required=True)
    _decision("defer", cmd_defer, "parked until something named is true")
    v = _decision("verdict", cmd_verdict, "record the result of a shipped candidate")
    v.add_argument("--verdict", required=True)
    s = _decision("set-status", cmd_set_status, "move to any status")
    s.add_argument("--status", required=True)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
