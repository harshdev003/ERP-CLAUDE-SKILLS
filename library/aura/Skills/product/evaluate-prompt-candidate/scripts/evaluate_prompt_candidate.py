#!/usr/bin/env python3
"""Plan and record the evidence a drafted candidate needs before Gate 2.

Resolves what must be run from the candidate's **change class**, prints the exact
commands, and records the results onto the row — refusing to move it to
`evaluated` until there is evidence attached.

It composes the harnesses rather than reimplementing them: the Safety Suite and
the rubric drives live in `eng:qa-conversation`. What this owns is the contract —
which surfaces a class requires, how many draws, and what may not be collapsed
into a single number.

What blocks a release is NOT decided here. It is read from
`references/release_contract.yaml`, the same file `eng:qa-conversation` gates on,
mirrored byte for byte and checked by a test. A second, independent copy of a
release rule drifts, and the drift shows up at Gate 2 as an argument about which
table was right.

Self-contained per AINative principle 1: `notion_rest.py` and
`release_contract.py` are per-skill copies.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from typing import Any, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import notion_rest  # noqa: E402
import release_contract  # noqa: E402

AURIE_CONVERSATIONS_DS = "3e375e23-af20-81d5-b99a-000b1b861edc"
# What `ready` lists: waiting for its first evidence.
EVALUATABLE = {"drafted"}
# What `record` accepts. `evaluated` is included so a SECOND run appends — which
# is what this skill documents, and what a real evaluation needs: the Safety Suite
# and the locked suite finish at different times, and drives later still. Allowing
# only `drafted` made the append path unreachable and the first scorecard final.
RECORDABLE = {"drafted", "evaluated"}

# Notion caps a rich_text item at 2000 characters. A property holds several, so a
# scorecard is chunked rather than cut -- but there is still a ceiling, and a real
# Safety Suite run blows past it. Past this the skill refuses and asks for a link,
# which is where the evidence should live anyway: the row should point at the
# Braintrust experiments, not try to contain them.
RICH_TEXT_ITEM = 1900
MAX_ITEMS = 10
MAX_RESULTS = RICH_TEXT_ITEM * MAX_ITEMS


def _chunked_rich_text(text: str) -> dict[str, Any]:
    """A rich_text property value split across items, losing nothing."""
    parts = [text[i:i + RICH_TEXT_ITEM] for i in range(0, len(text), RICH_TEXT_ITEM)]
    return {"rich_text": [{"type": "text", "text": {"content": c}} for c in parts or [""]]}

# The locked rubric suite. Frozen for reported comparisons -- a substantive edit
# creates a new versioned dataset rather than changing one of these.
LOCKED_SUITE = {
    "transitions": ("conversation-transitions-qa-rubric-v3", 18),
    "frustration": ("conversation-frustration-points-qa-rubric-v3", 16),
    "warmth": ("aurie-voice-warmth-qa", 16),
    "hard": ("aurie-hard-conversations-qa-v1", 16),
    "repetition": ("aurie-repetition-patterns-qa-v1", 60),
}




def prompt_ref(version: str) -> str:
    """A ref eng:compare-prompt-versions resolves, from the `Braintrust version` field.

    draft-prompt-candidate writes that field as `{slug} · {prompt_id}`: display
    text, which splits on the space and resolves as no slug. The prompt id is the
    part that resolves. Anything else (a hand-written list of arms, say) returns
    "" so `plan` prints a placeholder rather than a ref that cannot resolve.
    """
    version = (version or "").strip()
    last = version.rsplit(" · ", 1)[-1].strip()
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", last):
        return last
    return version if re.fullmatch(r"[A-Za-z0-9._-]+", version) else ""


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


def cmd_plan(args: argparse.Namespace) -> int:
    row = resolve(args.data_source_id, args.ref)
    cid = candidate_id_str(row)
    klass = notion_rest.select_value(row, "Change class")
    version = notion_rest.rich_text_value(row, "Braintrust version")
    status = notion_rest.select_value(row, "Status")

    print(f"{cid}  {notion_rest.title_text(row, 'Title')}")
    print(f"  status  {status}")
    print(f"  class   {klass or 'UNCLASSIFIED — set one, it picks the evidence'}")
    print(f"  version {version or 'not drafted yet'}")

    req = release_contract.requirements(klass or "")
    if not req:
        print("\nNo change class, so no requirements can be resolved.")
        return 1

    print("\nrequired evidence")
    print(f"  Ships to          {req['ships'].replace('_', ' ')}")
    if req["safety_suite"]:
        print(f"  Safety Suite      49 datasets / ~516 rows — {req['safety_suite']}")
    if req["locked_suite"]:
        print(f"  Locked suite      {req['locked_suite']}")
        # Only the `all` scope runs the whole suite. Listing all five for every
        # class contradicts the contract this command exists to enforce, and reads
        # as an instruction to run 126 rows for a targeted safety edit. Keyed on
        # the scope rather than the class name so that moving a class to `all` in
        # the contract is enough.
        if req["locked_suite_scope"] == "all":
            for key, (name, rows_n) in LOCKED_SUITE.items():
                print(f"                      {key:<12} {name}  ({rows_n} rows)")
        else:
            print("                      pick from the suite by what the change touches:")
            for key, (name, rows_n) in LOCKED_SUITE.items():
                print(f"                        {key:<12} {name}  ({rows_n} rows)")
            print("                      plus repetition whenever the wording changed.")
    if req["drives"]:
        print(f"  Multi-turn        {req['drives']}")
    if req["source_cases"]:
        print(f"  Source cases      {req['source_cases']}")
        prompter = notion_rest.select_value(row, "Prompter") or ""
        cand = prompt_ref(version) or "<candidate prompt id>"
        if prompter == "coach" and klass == "safety":
            # A coach safety change is about when it hands off: the alerts it fired on.
            print("                      build    data:analyze-conversation-guardrails → "
                  f"references/follow-ups/guardrail-dataset.md  (dataset `{cid} source cases v0.1`)")
            print(f"                      compare  eng:compare-prompt-versions --prompt prod=coach-production --prompt cand={cand} "
                  "--scorer tool-call --tool handoffToGuardrails --arg category --label-field expected.category")
        elif prompter == "coach":
            print("                      build    data:investigate-user-rating → references/conversation-dataset.md"
                  f"  (dataset `{cid} source cases v0.1`)")
            print(f"                      compare  eng:compare-prompt-versions --prompt prod=coach-production --prompt cand={cand} "
                  "--scorer llm-judge")
        else:
            print(f"                      no bundled recipe for `{prompter or 'this prompter'}` yet: build the rows by hand"
                  " (feedback_dataset.py gather --prompter <its ledger name> when its ratings name a reply),")
            print(f"                      then eng:compare-prompt-versions --prompt prod=<its [Production] slug> --prompt cand={cand}"
                  " --scorer llm-judge")
        print("                      record   the change summary from prompt_diff.md, the per-issue table "
              "and both experiment links")
    modes = ["dataset_replay"] if not req["drives"] else ["dataset_replay", "multi_turn"]
    print("\nrun it with (eng:qa-conversation owns these harnesses)")
    print(f"  Skill(eng:qa-conversation) — mode: {' and '.join(modes)}.")
    if not req["drives"]:
        print("  This class does NOT take multi-turn drives; a replay is the whole"
              " requirement.")
    print("  Pass the arm's Braintrust version and only the datasets named above.")

    print("\nthe reporting contract")
    for line in release_contract.reporting_contract():
        print(f"  · {line}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    row = resolve(args.data_source_id, args.ref)
    cid = candidate_id_str(row)
    status = notion_rest.select_value(row, "Status")

    if status not in RECORDABLE:
        print(f"{cid} is {status}. Evidence attaches to a candidate that has been "
              f"drafted and not yet shipped.")
        return 1

    results = open(args.results, encoding="utf-8").read().strip()
    if not results:
        print(f"{args.results} is empty. A scorecard delivered on its own cannot be")
        print("checked, and an empty one cannot be read at all.")
        return 1

    existing = notion_rest.rich_text_value(row, "Gate results").strip()
    merged = f"{existing}\n\n{results}".strip() if existing else results
    if len(merged) > MAX_RESULTS:
        print(f"{cid}: the combined gate results are {len(merged)} characters, over the "
              f"{MAX_RESULTS} a Notion property holds.")
        print("\nNothing was written — silently cutting it would discard evidence, and")
        print("a second append would discard the new evidence entirely.")
        print("\nPut the full scorecard where it belongs: the Braintrust experiments")
        print("carry the generated outputs. Pass a summary plus their links instead.")
        return 1

    print(f"{cid}  recording {len(results)} chars of gate results")
    if status == "drafted":
        print("  → status drafted → evaluated, ready for Gate 2")
    else:
        print(f"  → appending to the {len(existing)} chars already on the row; "
              f"status stays {status}")
    if not args.confirm:
        print("\n--- not writing. Re-run with --confirm.")
        return 0

    note = notion_rest.rich_text_value(row, "Quick status note").strip()
    line = (f"{date.today().isoformat()} — "
            + (f"{status} → evaluated by the loop. Evidence attached."
               if status == "drafted" else "further evidence attached by the loop."))
    notion_rest.notion.update_page_props(row["id"], {
        "Status": notion_rest.make_select("evaluated"),
        "Gate results": _chunked_rich_text(merged),
        "Quick status note": notion_rest.make_rich_text(f"{note}\n{line}".strip() if note else line),
        "Last seen": notion_rest.make_date(date.today().isoformat()),
    })
    print(f"\nrecorded  {cid} → evaluated")
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    rows = [r for r in _rows(args.data_source_id)
            if notion_rest.select_value(r, "Status") in EVALUATABLE]
    if not rows:
        print("nothing at `drafted` — draft an approved candidate first")
        return 0
    for row in sorted(rows, key=_id_number):
        klass = notion_rest.select_value(row, "Change class") or "unclassified"
        print(f"  {candidate_id_str(row):<8} {klass:<14} "
              f"{notion_rest.title_text(row, 'Title')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-source-id", default=AURIE_CONVERSATIONS_DS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ready", help="drafted candidates waiting for evidence")
    sp.set_defaults(func=cmd_ready)

    sp = sub.add_parser("plan", help="what this candidate's class requires")
    sp.add_argument("ref")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("record", help="attach the scorecard and move to evaluated")
    sp.add_argument("ref")
    sp.add_argument("--results", required=True, help="file holding the scorecard")
    sp.add_argument("--confirm", action="store_true")
    sp.set_defaults(func=cmd_record)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
