#!/usr/bin/env python3
"""Post a prompt-change candidate to the loop channel, at either gate.

Gate 1 opens the thread and asks whether this is the right change. Gate 2 replies
into that same thread with the evidence and asks whether to ship it, and by which
route. One thread per candidate either way, so the decision and the discussion
that produced it stay in one place.

The Gate 1 post's job is to let a reviewer approve a DIAGNOSIS. It is not a
summary of the defect — it is the defect, the reading of it, the proposed change,
and what that change expects to break.

The Gate 2 post's job is to let a reviewer approve a RELEASE, which is a
different question answered from different material: the scorecard against
recorded baselines, what the candidate said it put at risk and whether that
moved, and — stated outright — the surfaces the class required that the recorded
evidence does not mention. Without that last part a five-row smoke run and a full
run produce documents that look alike.

Neither post is assembled by hand and neither moves the row to a decided state:
`harvest-prompt-decisions` reads the reply and writes the verdict.

What the class requires is read from `references/release_contract.yaml`, a
byte-identical mirror of the copy `eng:qa-conversation` gates on.

Self-contained per AINative principle 1: `notion_rest.py`, `slack_post.py` and
`release_contract.py` are per-skill copies of their canonicals.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import notion_rest  # noqa: E402
import release_contract  # noqa: E402
import slack_post  # noqa: E402

AURIE_CONVERSATIONS_DS = "3e375e23-af20-81d5-b99a-000b1b861edc"
LOOP_CHANNEL = "C0C29JPEUQG"  # #ai-loop-aurie-conversations
SKILL_ID = "product:share-prompt-candidate"

# What a reviewer needs in order to answer Gate 1. Each maps to a Notion property.
REQUIRED = (
    ("Defect", "what is going wrong"),
    ("Diagnosis", "local or structural"),
    ("Risk named", "what this change expects to destabilise"),
    ("Change class", "which gate and which ship route"),
)

# Only these can be shared. `shared` is excluded on purpose: re-posting mints a
# second thread and splits the discussion, and the row already carries the first.
SHAREABLE = {"proposed"}

# Only an `evaluated` candidate has evidence to read. A `drafted` one would post a
# Gate 2 question nobody can answer, which is the Gate 1 failure mode repeated one
# stage later.
GATE_2_STATUSES = {"evaluated"}

# Written into the status note when the evidence post lands, and read back to
# stop a second one. Gate 1 is protected by its own status transition; Gate 2
# leaves the row at `evaluated` on purpose, so this note is the only record that
# the ask already happened.
GATE_2_MARKER = "posted for Gate 2"

# What a reviewer needs in order to answer Gate 2.
GATE_2_REQUIRED = (
    ("Gate results", "the scorecard, per surface"),
    ("Proposed diff", "what actually changed"),
    ("Risk named", "what this expected to destabilise"),
)

# The surfaces a scorecard can report on, and the words that count as reporting
# one. Matching text is coarse on purpose: this cannot prove a surface RAN, only
# that the recorded evidence mentions it, and the post says exactly that.
SURFACE_WORDS = {
    "Safety Suite": ("safety suite", "safety-suite", "524", "516"),
    "transitions": ("transitions",),
    "frustration": ("frustration",),
    "warmth": ("warmth", "aurie-voice"),
    "hard_conversations": ("hard conversation", "hard_conversations", "hard-conversations"),
    "repetition": ("repetition",),
    "multi-turn drives": ("drive", "multi-turn", "multi_turn"),
    "the source cases": ("source case", "source-case", "source_cases"),
}
LOCKED_DATASETS = ("transitions", "frustration", "warmth", "hard_conversations",
                   "repetition")


def class_route(klass: str) -> str:
    """One line naming the evidence and the ship route, from the contract."""
    req = release_contract.requirements(klass or "")
    if not req:
        return "route undeclared — set a change class, it picks the evidence"
    parts = [p for p in (req["safety_suite"], req["locked_suite"], req["drives"]) if p]
    ships = ("ships to everyone" if req["ships"] == "everyone"
             else "ships as an experiment arm")
    return f"{' · '.join(parts)} · {ships}"


def _rows(ds_id: str) -> list[dict[str, Any]]:
    return list(notion_rest.notion.query_data_source(ds_id))


def candidate_id_str(row: dict[str, Any]) -> str:
    uid = (row.get("properties") or {}).get("Candidate ID", {}).get("unique_id") or {}
    prefix, number = uid.get("prefix"), uid.get("number")
    return f"{prefix}-{number}" if prefix and number is not None else "(no id)"


def _id_number(row: dict[str, Any]) -> int:
    uid = (row.get("properties") or {}).get("Candidate ID", {}).get("unique_id") or {}
    return uid.get("number") or 0


def _url(row: dict[str, Any], name: str) -> str:
    return (row.get("properties", {}).get(name) or {}).get("url") or ""


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


# ---- readiness ------------------------------------------------------------

def missing_fields(row: dict[str, Any]) -> list[str]:
    """What this candidate still needs before a reviewer can answer Gate 1."""
    gaps = []
    for name, why in REQUIRED:
        value = (notion_rest.select_value(row, name)
                 or notion_rest.rich_text_value(row, name))
        if not value:
            gaps.append(f"{name} — {why}")
    if notion_rest.select_value(row, "Diagnosis") == "not yet diagnosed":
        gaps.append("Diagnosis — still 'not yet diagnosed'; Gate 1 approves a reading, "
                    "so there has to be one")
    return gaps


# ---- the post -------------------------------------------------------------

def render(row: dict[str, Any]) -> str:
    """The Gate 1 message. Ordered by what a reviewer decides on, not by schema."""
    cid = candidate_id_str(row)
    title = notion_rest.title_text(row, "Title")
    klass = notion_rest.select_value(row, "Change class") or "unclassified"
    prompter = notion_rest.select_value(row, "Prompter") or "?"
    diagnosis = notion_rest.select_value(row, "Diagnosis") or "?"

    out = [f"*{cid} · {title}*",
           f"`{prompter}` · *{klass}* — {class_route(klass)}",
           ""]

    def section(label: str, prop: str) -> None:
        value = notion_rest.rich_text_value(row, prop)
        if value:
            out.append(f"*{label}*\n{value}\n")

    section("The defect", "Defect")
    out.append(f"*Diagnosis* — {diagnosis}")
    cause = notion_rest.rich_text_value(row, "Cause")
    if cause:
        out.append(cause)
    out.append("")
    section("What this puts at risk", "Risk named")
    section("Proposed change", "Proposed diff")
    section("Evidence", "Evidence")

    out.append("*Gate 1 — is this the right change?*")
    out.append("You are approving a _diagnosis_, not a wording. Reply with one of:")
    out.append("• `I approve the diagnosis`")
    out.append("• `right defect, wrong change` — the defect stays, the fix goes back")
    out.append("• `no because …` — the reason is what stops this being re-raised")
    out.append("• `later — until X`")
    return "\n".join(out)


def already_posted_gate_2(row: dict[str, Any]) -> bool:
    return GATE_2_MARKER in notion_rest.rich_text_value(row, "Quick status note")


def gate_2_gaps(row: dict[str, Any]) -> list[str]:
    """What this candidate still needs before a reviewer can answer Gate 2."""
    gaps = []
    for name, why in GATE_2_REQUIRED:
        if not notion_rest.rich_text_value(row, name):
            gaps.append(f"{name} — {why}")
    if not notion_rest.select_value(row, "Change class"):
        gaps.append("Change class — it picks the evidence and the ship route")
    return gaps


def required_surfaces(klass: str) -> list[str]:
    """Which surfaces this class owes evidence for, per the release contract."""
    req = release_contract.requirements(klass or "")
    if not req:
        return []
    surfaces = []
    if req["safety_suite"]:
        surfaces.append("Safety Suite")
    scope = req["locked_suite_scope"]
    if scope == "all":
        surfaces.extend(LOCKED_DATASETS)
    elif scope != "none":
        # A targeted scope names its own rows, so which datasets ran is the
        # scorecard's to say. Requiring at least one keeps a class that owes a
        # locked-suite read from reporting none at all.
        surfaces.append("a locked dataset")
    if req["drives"]:
        surfaces.append("multi-turn drives")
    if req.get("source_cases"):
        surfaces.append("the source cases")
    return surfaces


def unreported(row: dict[str, Any], klass: str) -> list[str]:
    """Required surfaces the recorded evidence does not mention.

    Deliberately not a claim that a surface did not run — the scorecard is prose
    and this is a keyword read. It is a claim that the evidence on the row does
    not say, which is the thing a reviewer cannot otherwise see: a five-row smoke
    run and a full run produce documents that look alike.
    """
    results = notion_rest.rich_text_value(row, "Gate results").lower()
    missing = []
    for surface in required_surfaces(klass):
        if surface == "a locked dataset":
            if not any(w in results for d in LOCKED_DATASETS for w in SURFACE_WORDS[d]):
                missing.append("the locked datasets it targeted")
            continue
        if not any(word in results for word in SURFACE_WORDS[surface]):
            missing.append(surface)
    return missing


def render_gate_2(row: dict[str, Any]) -> str:
    """The Gate 2 message. Gates first, quality second, and never one number."""
    cid = candidate_id_str(row)
    title = notion_rest.title_text(row, "Title")
    klass = notion_rest.select_value(row, "Change class") or "unclassified"
    prompter = notion_rest.select_value(row, "Prompter") or "?"
    version = notion_rest.rich_text_value(row, "Braintrust version")

    out = [f"*{cid} · {title}* — Gate 2",
           f"`{prompter}` · *{klass}* — {class_route(klass)}"]
    if version:
        out.append(f"Braintrust version `{version}`")
    out.append("")

    def section(label: str, prop: str) -> None:
        value = notion_rest.rich_text_value(row, prop)
        if value:
            out.append(f"*{label}*\n{value}\n")

    section("What changed", "Proposed diff")

    out.append("*What this put at risk*")
    risk = notion_rest.rich_text_value(row, "Risk named")
    out.append(risk or "_nothing named — the candidate did not say_")
    out.append("_Read the evidence below against this, not against the defect._\n")

    section("Evidence", "Gate results")

    gaps = unreported(row, klass)
    if gaps:
        out.append("*Not measured*")
        out.append("The recorded evidence does not mention "
                   f"{', '.join(gaps)}. Say it ran, or mark it "
                   "`N/A — precondition absent`, before answering.")
        out.append("")

    out.append("*How to read it*")
    out.append("• A gate failure blocks the release. It does not rank this candidate "
               "below another — report the gates apart from the quality read.")
    out.append("• Dataset replay and multi-turn are different denominators. They are "
               "reported separately and are not converged into one number.")
    out.append("• The rubric is a read, not a gate, until its instruments are "
               "repaired. A rubric row contradicting a passed Safety Suite does not "
               "block.")
    out.append("")

    out.append("*Gate 2 — ship it, and how?*")
    out.append("Reply with one of:")
    out.append("• `I approve for release` — routes by the class above")
    out.append("• `ask for changes` — back to draft; name the dimension that failed")
    out.append("• `no because …` — the candidate is held, and the reason goes to the "
               "register so the digest does not re-raise it")
    return "\n".join(out)


def share(ds_id: str, ref: str, *, confirm: bool, channel: str) -> int:
    row = resolve(ds_id, ref)
    cid = candidate_id_str(row)
    status = notion_rest.select_value(row, "Status")

    if status not in SHAREABLE:
        print(f"{cid} is {status}, not {'/'.join(sorted(SHAREABLE))} — not sharing.")
        if status == "shared":
            existing = _url(row, "Slack thread")
            print(f"  It already has a thread: {existing or '(none recorded)'}")
        return 1

    gaps = missing_fields(row)
    if gaps:
        print(f"{cid} is not ready for Gate 1. Missing:")
        for g in gaps:
            print(f"  - {g}")
        print("\nA thread a reviewer cannot answer goes quiet, and the row then sits at")
        print("`shared` looking like it is waiting on a person. Fill these in first.")
        return 1

    text = render(row)
    print(text)
    if not confirm:
        print("\n--- not posting. Re-run with --confirm to post to the channel.")
        return 0

    resp = slack_post.post_message(channel, text, footer_skill=SKILL_ID)

    # `ts`, not `ok`, decides whether a message landed. A long post is chunked, and
    # a chunked post can return a parent `ts` with `ok` false — calling that a clean
    # rejection and retrying mints a SECOND top-level thread while the first stays
    # unrecorded, which is the failure this whole block exists to prevent.
    ts = resp.get("ts")
    if not ts:
        print(f"\nSlack rejected the post: {resp.get('error') or resp}")
        print(f"{cid} left at {status}. Nothing reached the channel and nothing was")
        print("written to Notion, so this is safe to retry.")
        return 1
    if not resp.get("ok"):
        print(f"  warning: Slack reported {resp.get('error')!r} but a message landed "
              f"(ts {ts}). Recording it — do NOT retry, that would open a second thread.")

    # Everything below MUST reach the Notion write. The message is already in the
    # channel, so any early exit here strands the candidate at `proposed` with a
    # live thread nobody has recorded, and the retry opens another one.
    permalink = ""
    try:
        link_resp = slack_post.get_permalink(channel, ts)
        if link_resp.get("ok"):
            permalink = link_resp.get("permalink", "")
    except BaseException as exc:  # noqa: BLE001 — the helper can sys.exit on transport errors
        print(f"  warning: permalink lookup failed ({exc!r}).")
    if not permalink:
        print("  Recording a permalink-shaped URL built from the ts so harvest can "
              "still find the thread.")

    props = {
        "Status": notion_rest.make_select("shared"),
        "Last seen": notion_rest.make_date(date.today().isoformat()),
    }
    if permalink:
        props["Slack thread"] = {"url": permalink}
    else:
        # harvest recovers a ts from the permalink shape, so synthesise the same
        # shape rather than leaving the row with no way back to its thread.
        props["Slack thread"] = {
            "url": f"https://slack.com/archives/{channel}/p{ts.replace('.', '')}"}
    note = notion_rest.rich_text_value(row, "Quick status note").strip()
    line = f"{date.today().isoformat()} — proposed → shared by the loop. Posted for Gate 1."
    props["Quick status note"] = notion_rest.make_rich_text(
        f"{note}\n{line}".strip() if note else line)
    notion_rest.notion.update_page_props(row["id"], props)

    print(f"\nposted  {cid} → {permalink or channel}")
    return 0


def thread_ts_from_permalink(permalink: str) -> Optional[str]:
    """Slack permalinks carry the ts as `p1758560000123456` — 10s + 6 micro.

    Mirrors the reader in `harvest-prompt-decisions`; the two must agree, since
    one writes the link this parses.
    """
    m = __import__("re").search(r"/p(\d{10})(\d{6})", permalink or "")
    return f"{m.group(1)}.{m.group(2)}" if m else None


def gate_2(ds_id: str, ref: str, *, confirm: bool, channel: str,
           again: bool = False) -> int:
    row = resolve(ds_id, ref)
    cid = candidate_id_str(row)
    status = notion_rest.select_value(row, "Status")

    if already_posted_gate_2(row) and not again:
        print(f"{cid} has already been posted for Gate 2 — not posting again.")
        print("  The row stays at `evaluated` while the question is open, so a "
              "re-run would ask the same thing twice in the same thread and "
              "split the answer.")
        print("  If further evidence was attached since, `--again` posts an "
              "updated ask.")
        return 1

    if status not in GATE_2_STATUSES:
        print(f"{cid} is {status}, not {'/'.join(sorted(GATE_2_STATUSES))} — not posting.")
        print("  Gate 2 reads evidence. A candidate reaches `evaluated` when "
              "`evaluate-prompt-candidate record` attaches some.")
        return 1

    gaps = gate_2_gaps(row)
    if gaps:
        print(f"{cid} is not ready for Gate 2. Missing:")
        for g in gaps:
            print(f"  - {g}")
        return 1

    permalink = _url(row, "Slack thread")
    thread_ts = thread_ts_from_permalink(permalink)
    if not thread_ts:
        print(f"{cid} has no usable Slack thread ({permalink or 'none recorded'}).")
        print("  Gate 2 replies into the thread Gate 1 opened, so the decision and "
              "the discussion that produced it stay together. Posting a new "
              "top-level message would split them.")
        return 1

    text = render_gate_2(row)
    print(text)
    if not confirm:
        print("\n--- not posting. Re-run with --confirm to reply in the thread.")
        return 0

    resp = slack_post.post_message(channel, text, thread_ts=thread_ts,
                                   footer_skill=SKILL_ID)
    ts = resp.get("ts")
    if not ts:
        print(f"\nSlack rejected the post: {resp.get('error') or resp}")
        print(f"{cid} left at {status}. Nothing reached the channel, so this is "
              "safe to retry.")
        return 1
    if not resp.get("ok"):
        print(f"  warning: Slack reported {resp.get('error')!r} but a message landed "
              f"(ts {ts}). Do NOT retry — that would post the evidence twice.")

    # The status does NOT move here. `evaluated` is what harvest scans for at
    # Gate 2, and moving the row would take it out of that scan while the
    # question is still open.
    note = notion_rest.rich_text_value(row, "Quick status note").strip()
    line = (f"{date.today().isoformat()} — {GATE_2_MARKER} by the loop. "
            "Waiting on a ship decision.")
    notion_rest.notion.update_page_props(row["id"], {
        "Quick status note": notion_rest.make_rich_text(
            f"{note}\n{line}".strip() if note else line),
        "Last seen": notion_rest.make_date(date.today().isoformat()),
    })

    print(f"\nreplied  {cid} → {permalink}")
    return 0


def cmd_gate_2(args: argparse.Namespace) -> int:
    return gate_2(args.data_source_id, args.ref, confirm=args.confirm,
                  channel=args.channel, again=args.again)


def cmd_gate_2_ready(args: argparse.Namespace) -> int:
    """Which evaluated candidates could go to Gate 2, and what the rest need."""
    rows = [r for r in _rows(args.data_source_id)
            if notion_rest.select_value(r, "Status") in GATE_2_STATUSES]
    if not rows:
        print("nothing at `evaluated` — run the evals and record them")
        return 0
    for row in sorted(rows, key=_id_number):
        gaps = gate_2_gaps(row)
        klass = notion_rest.select_value(row, "Change class") or ""
        if not gaps and not thread_ts_from_permalink(_url(row, "Slack thread")):
            gaps = ["Slack thread — Gate 2 replies into the Gate 1 thread"]
        if already_posted_gate_2(row):
            mark = "asked"
        else:
            mark = "ready" if not gaps else "blocked"
        print(f"  {candidate_id_str(row):<8} {mark:<8} "
              f"{notion_rest.title_text(row, 'Title')}")
        for g in gaps:
            print(f"           - {g}")
        for surface in unreported(row, klass):
            print(f"           ! not measured: {surface}")
    return 0


def cmd_share(args: argparse.Namespace) -> int:
    return share(args.data_source_id, args.ref, confirm=args.confirm, channel=args.channel)


def cmd_ready(args: argparse.Namespace) -> int:
    """Which proposed candidates could be shared, and what the rest still need."""
    rows = [r for r in _rows(args.data_source_id)
            if notion_rest.select_value(r, "Status") in SHAREABLE]
    if not rows:
        print("nothing at `proposed` — run the digest")
        return 0
    for row in sorted(rows, key=_id_number):
        gaps = missing_fields(row)
        mark = "ready" if not gaps else "blocked"
        print(f"  {candidate_id_str(row):<8} {mark:<8} {notion_rest.title_text(row, 'Title')}")
        for g in gaps:
            print(f"           - {g}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-source-id", default=AURIE_CONVERSATIONS_DS)
    p.add_argument("--channel", default=LOOP_CHANNEL)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ready", help="which proposed candidates can be shared")
    sp.set_defaults(func=cmd_ready)

    sp = sub.add_parser("share", help="post one candidate for Gate 1")
    sp.add_argument("ref", help="CONV-9, 9, or a title substring")
    sp.add_argument("--confirm", action="store_true",
                    help="required to actually post — otherwise this is a dry run")
    sp.set_defaults(func=cmd_share)

    sp = sub.add_parser("gate2-ready",
                        help="which evaluated candidates can go to Gate 2")
    sp.set_defaults(func=cmd_gate_2_ready)

    sp = sub.add_parser("gate2",
                        help="reply in the thread with the evidence, for Gate 2")
    sp.add_argument("ref", help="CONV-9, 9, or a title substring")
    sp.add_argument("--confirm", action="store_true",
                    help="required to actually post — otherwise this is a dry run")
    sp.add_argument("--again", action="store_true",
                    help="post a second ask, for when further evidence landed "
                         "after the first")
    sp.set_defaults(func=cmd_gate_2)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
