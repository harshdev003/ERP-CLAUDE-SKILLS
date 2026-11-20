#!/usr/bin/env python3
"""Route a Gate-2-approved candidate to users, and record what happened.

Routes by change class: `safety` and `correctness` ship to everyone, `behavioural`
and `model/config` ship as a production experiment arm. The class was declared at
Gate 1, so by the time a candidate reaches here the route is already decided and
this leg does not re-open it.

It composes the release path rather than owning it — `eng:update-intelligence-prompt`
opens the PR and syncs the Braintrust Production prompt, `eng:release-version` cuts
the release. What this owns is the routing, the bookkeeping, and the rollback.

Self-contained per AINative principle 1: `notion_rest.py` is a per-skill copy.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import notion_rest  # noqa: E402

AURIE_CONVERSATIONS_DS = "3e375e23-af20-81d5-b99a-000b1b861edc"

# `evaluated` is a candidate whose evidence is in but whose Gate 2 verdict has not
# been harvested yet; `in_review` is one the harvester has recorded as approved.
# Both are pre-release, and `ship` is the only leg that writes `released`.
SHIPPABLE = {"evaluated", "in_review"}
# Only something that actually reached users can be rolled back.
ROLLBACKABLE = {"released", "experiment_running"}
FULL_ROLLOUT = {"safety", "correctness"}
ARM_ROUTED = {"behavioural", "model/config"}

# Production cannot vary a prompt body per arm yet: no prompter declares
# `experiments`, and the wiring that would let one is unbuilt. Until it lands, an
# arm-routed candidate cannot ship at all — and saying so is better than shipping
# it to everyone, which would silently convert a measured test into an unmeasured
# release of exactly the change class that most needs measuring.
ARM_BLOCKER = "ENGBACK-1376 — per-arm prompt bodies are not wired in production"


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


def _append_note(row: dict[str, Any], line: str) -> dict[str, Any]:
    note = notion_rest.rich_text_value(row, "Quick status note").strip()
    stamped = f"{date.today().isoformat()} — {line}"
    return notion_rest.make_rich_text(f"{note}\n{stamped}".strip() if note else stamped)


def cmd_route(args: argparse.Namespace) -> int:
    """What this candidate's class means it should do next."""
    row = resolve(args.data_source_id, args.ref)
    cid = candidate_id_str(row)
    klass = notion_rest.select_value(row, "Change class") or ""
    status = notion_rest.select_value(row, "Status")
    version = notion_rest.rich_text_value(row, "Braintrust version")

    print(f"{cid}  {notion_rest.title_text(row, 'Title')}")
    print(f"  status  {status}")
    print(f"  class   {klass or 'UNCLASSIFIED'}")
    print(f"  version {version or '(none)'}")

    if status not in SHIPPABLE:
        print(f"\nNot shippable: {status} is neither `evaluated` nor `in_review`. "
              f"Gate 2 comes first.")
        return 1

    if klass in FULL_ROLLOUT:
        print("\nroute: TO EVERYONE")
        print("  There is nothing to learn from withholding a guardrail fix, and a")
        print("  crisis path cannot be A/B tested.")
        print("\n  1. eng:update-intelligence-prompt — Mode A, opens the PR from the")
        print("     Braintrust version above and updates config.js")
        print("  2. a human reviews and merges")
        print("  3. eng:release-version --level patch   (a prompt-only IS release is a")
        print("     patch; the tool infers minor from a feat() commit)")
        print("  4. after deploy, the Braintrust [Production] prompt is synced — leaving")
        print("     it unsynced makes the registry disagree with production")
        print("  5. record it here:  ship record <ref> --version <x.y.z> --confirm")
        return 0

    if klass in ARM_ROUTED:
        print("\nroute: AS AN EXPERIMENT ARM")
        print(f"\n  BLOCKED — {ARM_BLOCKER}")
        print("  Shipping it to everyone instead would convert a measured test into an")
        print("  unmeasured release of exactly the class that most needs measuring.")
        print("\n  When the wiring lands: register the experiment with ONE named primary")
        print("  metric and written stopping rules BEFORE the arms are enabled, and")
        print("  reconcile the lettering — the current Aurie convention is that arm B is")
        print("  the control.")
        return 1

    print("\nNo change class, so no route. Set one — it was supposed to be declared at Gate 1.")
    return 1


def cmd_record(args: argparse.Namespace) -> int:
    """Record that a candidate reached users."""
    row = resolve(args.data_source_id, args.ref)
    cid = candidate_id_str(row)
    status = notion_rest.select_value(row, "Status")
    if status not in SHIPPABLE:
        print(f"{cid} is {status}, so it is not waiting on a release to record.")
        return 1

    # The same guard `route` applies, applied on the WRITE path too. Without it an
    # arm-routed candidate can be booked as shipped to everyone — which is exactly
    # the unmeasured full rollout `route` exists to refuse, arrived at by a
    # different command.
    klass = notion_rest.select_value(row, "Change class") or ""
    if klass in ARM_ROUTED:
        print(f"{cid} is {klass}, which ships as an experiment arm, not to everyone.")
        print(f"  BLOCKED — {ARM_BLOCKER}")
        print("  Recording it as released would book an unmeasured full rollout of the")
        print("  class that most needs measuring. Use `route` to see the path.")
        return 1
    if klass not in FULL_ROLLOUT:
        print(f"{cid} has no change class, so there is no route to record against.")
        return 1

    props = {
        "Status": notion_rest.make_select("released"),
        "Release version": notion_rest.make_rich_text(args.version),
        "Last seen": notion_rest.make_date(date.today().isoformat()),
        # The note takes the row's ACTUAL status, not a hardcoded one. After a
        # Gate 2 harvest the row sits at `in_review`, so a fixed "evaluated →
        # released" would record a transition that never happened — and the note
        # is what the digest and the diagnosis leg read back as the record of what
        # shipped.
        "Quick status note": _append_note(
            row, f"{status} → released by {args.by or 'the loop'} in {args.version}."),
    }
    if args.pr:
        props["PR"] = {"url": args.pr}

    print(f"{cid}  → released in {args.version}")
    if not args.confirm:
        print("--- not writing. Re-run with --confirm.")
        return 0
    notion_rest.notion.update_page_props(row["id"], props)
    print("recorded")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    """Record a withdrawal, and say what a full rollback means."""
    row = resolve(args.data_source_id, args.ref)
    cid = candidate_id_str(row)
    status = notion_rest.select_value(row, "Status")

    # A rollback is a RESULT, and the digest reads `rolled_back` as answered. Writing
    # one on a candidate that never reached users would retire a defect nobody ever
    # tried -- the same reason `learn` only accepts a verdict on a shipped row.
    if status not in ROLLBACKABLE:
        print(f"{cid} is {status}, so there is nothing to roll back.")
        print("  A rollback records that something reached users and was withdrawn.")
        print("  On an untried candidate it would retire the defect instead — the")
        print("  digest treats `rolled_back` as answered and stops raising it.")
        print(f"  To drop it without shipping, reject or defer it instead.")
        return 1

    print(f"{cid}  {notion_rest.title_text(row, 'Title')}")
    print("\nA rollback reverts BOTH halves:")
    print("  · the IntelligenceService commit, and")
    print("  · the Braintrust [Production] prompt")
    print("  Reverting only the code leaves the registry claiming production runs")
    print("  something it does not, which is worse than the regression.")
    print(f"\nreason: {args.reason}")

    if not args.confirm:
        print("\n--- not writing. Re-run with --confirm.")
        return 0
    notion_rest.notion.update_page_props(row["id"], {
        "Status": notion_rest.make_select("rolled_back"),
        "Verdict": notion_rest.make_select("rolled back"),
        "Decided at": notion_rest.make_date(date.today().isoformat()),
        "Last seen": notion_rest.make_date(date.today().isoformat()),
        "Quick status note": _append_note(
            row, f"rolled back by {args.by or 'the loop'}. {args.reason}"),
    })
    print(f"\nrecorded  {cid} → rolled_back")
    print("This is a result, not a failure to record: the next candidate on this")
    print("defect has to see that this was tried and withdrawn.")
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    rows = [r for r in _rows(args.data_source_id)
            if notion_rest.select_value(r, "Status") in SHIPPABLE]
    if not rows:
        print("nothing at `evaluated` — run the evals and pass Gate 2 first")
        return 0
    for row in sorted(rows, key=_id_number):
        klass = notion_rest.select_value(row, "Change class") or "unclassified"
        route = ("to everyone" if klass in FULL_ROLLOUT else
                 f"arm — BLOCKED ({ARM_BLOCKER.split(' — ')[0]})" if klass in ARM_ROUTED
                 else "no route")
        print(f"  {candidate_id_str(row):<8} {klass:<14} {route:<34} "
              f"{notion_rest.title_text(row, 'Title')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-source-id", default=AURIE_CONVERSATIONS_DS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ready", help="candidates past Gate 2, with their route")
    sp.set_defaults(func=cmd_ready)

    sp = sub.add_parser("route", help="what this candidate's class means it should do")
    sp.add_argument("ref")
    sp.set_defaults(func=cmd_route)

    sp = sub.add_parser("record", help="record that it reached users")
    sp.add_argument("ref")
    sp.add_argument("--version", required=True, help="the IS release it went out in")
    sp.add_argument("--pr")
    sp.add_argument("--by")
    sp.add_argument("--confirm", action="store_true")
    sp.set_defaults(func=cmd_record)

    sp = sub.add_parser("rollback", help="record a withdrawal")
    sp.add_argument("ref")
    sp.add_argument("--reason", required=True)
    sp.add_argument("--by")
    sp.add_argument("--confirm", action="store_true")
    sp.set_defaults(func=cmd_rollback)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
