#!/usr/bin/env python3
"""Write a shipped candidate's verdict back, and keep the loop's funnel.

What closes the loop. A verdict lands on the row, and the reason it landed is what
the digest reads next week to avoid re-raising a question the loop has answered.

It reads a result; it never forms one. Whoever owns experiments decides when one
is over and what it said — this leg records that decision and refuses to invent it.

Self-contained per AINative principle 1: `notion_rest.py` is a per-skill copy.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from typing import Any, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import notion_rest  # noqa: E402

AURIE_CONVERSATIONS_DS = "3e375e23-af20-81d5-b99a-000b1b861edc"

# A verdict can only be written on a candidate that actually reached users.
VERDICTABLE = {"released", "experiment_running"}
VALID_VERDICTS = {"won", "lost", "inconclusive", "rolled back"}
VERDICT_TO_STATUS = {"won": "won", "lost": "lost",
                     "inconclusive": "inconclusive", "rolled back": "rolled_back"}

FUNNEL_ORDER = ["proposed", "shared", "approved", "drafted", "evaluated",
                "in_review", "merged", "released", "experiment_running"]
TERMINAL = ["won", "lost", "inconclusive", "rolled_back", "abandoned"]
PARKED = ["rejected", "deferred", "held"]


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
    rows = _rows(ds_id)
    wanted = ref.strip().lower().removeprefix("conv-")
    if wanted.isdigit():
        for row in rows:
            if _id_number(row) == int(wanted):
                return row
        raise SystemExit(f"no candidate with id CONV-{wanted}")
    matches = [r for r in rows if ref.lower() in notion_rest.title_text(r, "Title").lower()]
    if len(matches) != 1:
        raise SystemExit(f"{ref!r} matches {len(matches)} candidates — use the CONV id")
    return matches[0]


def cmd_verdict(args: argparse.Namespace) -> int:
    if args.verdict not in VALID_VERDICTS:
        raise SystemExit(f"verdict must be one of {sorted(VALID_VERDICTS)}")
    row = resolve(args.data_source_id, args.ref)
    cid = candidate_id_str(row)
    status = notion_rest.select_value(row, "Status")

    if status not in VERDICTABLE:
        print(f"{cid} is {status}. A verdict can only be written on a candidate that")
        print("reached users — anything else would record a result nobody observed.")
        return 1

    target = VERDICT_TO_STATUS[args.verdict]
    print(f"{cid}  {notion_rest.title_text(row, 'Title')}")
    print(f"  {status} → {target}   ({args.verdict})")
    print(f"  learning: {args.learning}")
    if not args.confirm:
        print("\n--- not writing. Re-run with --confirm.")
        return 0

    note = notion_rest.rich_text_value(row, "Quick status note").strip()
    line = (f"{date.today().isoformat()} — {status} → {target} by "
            f"{args.by or 'the loop'}. {args.learning}")
    notion_rest.notion.update_page_props(row["id"], {
        "Status": notion_rest.make_select(target),
        "Verdict": notion_rest.make_select(args.verdict),
        "Decided at": notion_rest.make_date(date.today().isoformat()),
        "Last seen": notion_rest.make_date(date.today().isoformat()),
        "Quick status note": notion_rest.make_rich_text(f"{note}\n{line}".strip() if note else line),
    })
    print(f"\nrecorded  {cid} → {target}")
    if args.verdict in ("lost", "rolled back"):
        print("\nA lost or withdrawn candidate is ANSWERED, which is the opposite of")
        print("abandoned. It stays in the register so the next candidate on this defect")
        print("sees that this was tried, and what it cost.")
    return 0


def cmd_funnel(args: argparse.Namespace) -> int:
    rows = _rows(args.data_source_id)
    counts: Counter = Counter(notion_rest.select_value(r, "Status") or "unknown" for r in rows)
    by_class: Counter = Counter(notion_rest.select_value(r, "Change class") or "unclassified"
                                for r in rows)

    print(f"{len(rows)} candidates\n")
    for label, group in (("in flight", FUNNEL_ORDER), ("parked", PARKED), ("decided", TERMINAL)):
        shown = [(s, counts[s]) for s in group if counts.get(s)]
        if shown:
            print(label)
            for s, n in shown:
                print(f"  {s:<20} {n:>3}")
    print("\nby change class")
    for klass, n in by_class.most_common():
        print(f"  {klass:<20} {n:>3}")

    # `abandoned` is in TERMINAL because it ends a candidate, but it is not a
    # verdict and those candidates typically never shipped. Counting it as reached
    # and decided makes one abandonment look like a closed loop.
    verdicts = [s for s in TERMINAL if s != "abandoned"]
    decided = sum(counts[s] for s in verdicts)
    in_flight = counts.get("released", 0) + counts.get("experiment_running", 0)
    reached = decided + in_flight
    if reached:
        won = counts.get("won", 0)
        print(f"\n{reached} reached users, {decided} have a verdict, {won} won")
    else:
        print("\nnothing has reached users yet — the loop has not closed once")
    if counts.get("abandoned"):
        print(f"{counts['abandoned']} abandoned — dropped without an answer, "
              f"which is not a verdict")
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    """Shipped candidates still waiting on a verdict."""
    rows = [r for r in _rows(args.data_source_id)
            if notion_rest.select_value(r, "Status") in VERDICTABLE]
    if not rows:
        print("nothing waiting on a verdict")
        return 0
    for row in sorted(rows, key=_id_number):
        status = notion_rest.select_value(row, "Status")
        version = notion_rest.rich_text_value(row, "Release version") or "?"
        print(f"  {candidate_id_str(row):<8} {status:<20} {version:<10} "
              f"{notion_rest.title_text(row, 'Title')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-source-id", default=AURIE_CONVERSATIONS_DS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("funnel", help="the whole backlog, by stage and by class")
    sp.set_defaults(func=cmd_funnel)

    sp = sub.add_parser("pending", help="shipped candidates with no verdict yet")
    sp.set_defaults(func=cmd_pending)

    sp = sub.add_parser("verdict", help="write a result back onto a shipped candidate")
    sp.add_argument("ref")
    sp.add_argument("--verdict", required=True,
                    help="won | lost | inconclusive | 'rolled back'")
    sp.add_argument("--learning", required=True,
                    help="the one line the next candidate on this defect needs")
    sp.add_argument("--by")
    sp.add_argument("--confirm", action="store_true")
    sp.set_defaults(func=cmd_verdict)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
