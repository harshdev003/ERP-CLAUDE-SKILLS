#!/usr/bin/env python3
"""Read Gate 1 and Gate 2 verdicts out of the loop channel and write them onto rows.

Sweeps the thread of every candidate that is waiting on a human, classifies the
replies, and records the decision with the quote that produced it. Anything it
cannot read with confidence comes back as `needs_judgment` for a person to settle
— a wrong verdict written confidently is far more expensive than an unread one,
because it moves a row and nobody re-reads a thread that already looks decided.

The verdict vocabulary and the negation handling are ported from the Features
loop's harvester (`product:harvest-idea-decisions`), which is the reference
implementation. Skills here are self-contained by design, so this is a copy
rather than an import: when a bug is found in either, fix both in the same change.

Self-contained per AINative principle 1: `notion_rest.py`, `slack_read.py` and
`slack_post.py` are per-skill copies of their canonicals.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from typing import Any, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import notion_rest  # noqa: E402
import slack_read  # noqa: E402
import slack_post  # noqa: E402

AURIE_CONVERSATIONS_DS = "3e375e23-af20-81d5-b99a-000b1b861edc"
LOOP_CHANNEL = "C0C29JPEUQG"  # #ai-loop-aurie-conversations
SKILL_ID = "product:harvest-prompt-decisions"

# Which gate a row is sitting at. A generic "approved" means different things at
# each, and only the status can tell them apart.
GATE_1_STATUSES = {"shared"}
GATE_2_STATUSES = {"evaluated"}

VERDICTS = ("approved", "rejected", "deferred", "wrong_change", "changes_requested",
            "held", "needs_answer", "needs_judgment", "no_decision")

# --- the vocabulary --------------------------------------------------------
# Checked FIRST: "let's do it later" is a deferral, and its approval half would
# otherwise win.
DEFER_RE = re.compile(
    r"\b(later\s*[—–-]\s*until\b"
    r"|not right now|not now\b|for later\b|in the future"
    r"|park(?:ed|ing)? (?:this|it)\b"
    r"|once we\b|after we\b|depends on\b"
    r"|(?:do|draft|ship|try|run|revisit) (?:it|this|that)?\s*later\b)",
    re.I,
)

REJECT_RE = re.compile(
    r"\b(no because\b|not doing (?:this|it)\b|won'?t do (?:this|it)\b"
    r"|drop (?:this|it)\b|skip (?:this|it)\b|leave (?:it |this )?as is\b|rejected\b)",
    re.I,
)

# "right defect, wrong change" — the Conversations analogue of a redesign request.
# It is NOT a rejection: the defect was just endorsed, and reading it as one would
# retire a problem the reviewer agrees exists.
WRONG_CHANGE_RE = re.compile(
    r"(?:right defect[, ]+wrong (?:change|fix|approach)"
    r"|wrong (?:change|fix|approach)[, ]+right defect"
    r"|\b(?:agree|right|yes) (?:on|about|with) the (?:defect|problem|diagnosis)\b"
    r"[^.!?\n]{0,60}\b(?:but|not|disagree)\b[^.!?\n]{0,60}\b(?:change|fix|approach|diff)\b"
    r"|\bnot (?:sold|convinced) on the (?:change|fix|approach|diff)\b)",
    re.I,
)

CHANGES_REQUESTED_RE = re.compile(
    r"\b(ask(?:ing)? for changes\b|changes requested\b|needs? (?:another|more) (?:pass|round)\b"
    r"|send it back\b|rework (?:this|it)\b)",
    re.I,
)

APPROVE_RE = re.compile(
    r"\b(approved\b|go ahead\b|ship it\b|let'?s (?:do it|draft|ship|run|try))", re.I)

# Gate-naming approvals. Unambiguous where the generic ones are not, and a
# mismatch between the named gate and the row's status is worth stopping on: it
# is somebody in the wrong thread, or a row that is behind.
APPROVE_DIAGNOSIS_RE = re.compile(
    r"\bapprove[sd]?\s+(?:the\s+|this\s+|it\s+)?(?:diagnosis|reading|defect)\b", re.I)
APPROVE_RELEASE_RE = re.compile(
    r"\bapprove[sd]?\s+(?:this\s+|it\s+)?for\s+releas(?:e|ing)\b", re.I)

# Every approval pattern matches inside its own negation — "not approved for
# release" contains "approved for release". An approval only counts when nothing
# negates the verb immediately before it. Contractions are spelled out because a
# bare `n.t$` also fires on "want".
NEGATOR_RE = re.compile(
    r"(?:\bnot|\bnever|\bcannot|\bno longer|"
    r"\b(?:do|does|did|wo|ca|is|are|was|were|has|have|had|would|could|should|ai)n[o']?t)"
    r"\s+$",
    re.I,
)

_URL_OR_MENTION_RE = re.compile(r"<[^>]*>|https?://\S+", re.I)
_INTERROGATIVE_RE = re.compile(
    r"^\s*(?:what|why|how|when|where|who|which|is|are|do|does|did|can|could|should|would|"
    r"will|has|have|any|anyone)\b", re.I)


def _negated(text: str, match: re.Match) -> bool:
    return bool(NEGATOR_RE.search(text[:match.start()]))


def _is_question(text: str) -> bool:
    """A trailing question, ignoring '?' inside links and mentions."""
    stripped = _URL_OR_MENTION_RE.sub("", text).strip()
    return stripped.endswith("?") or bool(_INTERROGATIVE_RE.match(stripped) and "?" in stripped)


def classify(text: str, *, gate: int) -> tuple[str, str]:
    """Return (verdict, the quote that decided it)."""
    def quote(m: re.Match) -> str:
        return text[max(0, m.start() - 40):m.end() + 60].strip()

    for regex, verdict in ((DEFER_RE, "deferred"),
                           (WRONG_CHANGE_RE, "wrong_change"),
                           (CHANGES_REQUESTED_RE, "changes_requested"),
                           (REJECT_RE, "rejected")):
        m = regex.search(text)
        if m:
            return verdict, quote(m)

    for regex, named_gate in ((APPROVE_DIAGNOSIS_RE, 1), (APPROVE_RELEASE_RE, 2)):
        m = regex.search(text)
        if not m:
            continue
        if _negated(text, m):
            # "not approved for release" / "we cannot approve the diagnosis" is an
            # explicit refusal, not an absence of one. Falling through to
            # `no_decision` would leave the row waiting on a reply it already has.
            return "rejected", quote(m)
        if named_gate != gate:
            return "needs_judgment", (
                f"approval names gate {named_gate} but the row is at gate {gate}: "
                f"{quote(m)}")
        return "approved", quote(m)

    m = APPROVE_RE.search(text)
    if m:
        if _negated(text, m):
            return "rejected", quote(m)
        if gate == 2:
            # A bare "approved" cannot be attributed at Gate 2. The thread already
            # contains the Gate 1 approval that got the candidate here, and the
            # harvester reads the LAST human reply — so if nobody has posted Gate 2
            # yet, that Gate 1 "approved" is what it finds, and accepting it would
            # ship a candidate on a decision made about something else.
            #
            # Gate 2 therefore requires the explicit form. Gate 1 does not: nothing
            # earlier in the thread can be confused with it.
            return "needs_judgment", (
                "a bare approval at Gate 2 is ambiguous — the thread also holds the "
                f"Gate 1 approval. Ask for \"approve for release\": {quote(m)}")
        return "approved", quote(m)

    if _is_question(text):
        return "needs_answer", text.strip()[:160]
    return "no_decision", ""


# ---- Notion ---------------------------------------------------------------

def _rows(ds_id: str) -> list[dict[str, Any]]:
    return list(notion_rest.notion.query_data_source(ds_id))


def candidate_id_str(row: dict[str, Any]) -> str:
    uid = (row.get("properties") or {}).get("Candidate ID", {}).get("unique_id") or {}
    prefix, number = uid.get("prefix"), uid.get("number")
    return f"{prefix}-{number}" if prefix and number is not None else "(no id)"


def _url(row: dict[str, Any], name: str) -> str:
    return (row.get("properties", {}).get(name) or {}).get("url") or ""


def thread_ts_from_permalink(permalink: str) -> Optional[str]:
    """Slack permalinks carry the ts as `p1758560000123456` — 10s + 6 micro."""
    m = re.search(r"/p(\d{10})(\d{6})", permalink or "")
    return f"{m.group(1)}.{m.group(2)}" if m else None


def harvestable(ds_id: str) -> list[tuple[dict[str, Any], int]]:
    """Rows waiting on a human, paired with which gate they are at."""
    out = []
    for row in _rows(ds_id):
        status = notion_rest.select_value(row, "Status")
        if status in GATE_1_STATUSES:
            out.append((row, 1))
        elif status in GATE_2_STATUSES:
            out.append((row, 2))
    return out


# Where each verdict moves the row, per gate. `needs_answer`, `needs_judgment`
# and `no_decision` deliberately have no entry: the row holds where it is.
TRANSITIONS = {
    (1, "approved"): "approved",
    (1, "rejected"): "rejected",
    (1, "deferred"): "deferred",
    (1, "wrong_change"): "proposed",     # the defect stays live, the fix goes back
    # Gate 2 approval means APPROVED TO SHIP, not shipped. Writing `released`
    # here claimed a deploy that had not happened, and worse: `ship record` only
    # accepts a pre-release status, so after a normal harvest the ship write was
    # refused — the release version was never stored and ship's change-class guard
    # never ran, which is the guard that stops an arm-routed candidate being
    # booked as a full rollout. `released` is written by `ship record` alone,
    # because that is the only leg that knows a release actually happened.
    (2, "approved"): "in_review",
    (2, "rejected"): "held",
    (2, "deferred"): "held",
    (2, "changes_requested"): "approved",  # back to drafting
}


def apply_verdict(row: dict[str, Any], gate: int, verdict: str, quote: str,
                  who: str, *, confirm: bool) -> bool:
    cid = candidate_id_str(row)
    current = notion_rest.select_value(row, "Status")
    target = TRANSITIONS.get((gate, verdict))

    if target is None:
        print(f"  {cid}  {verdict} — holding at {current}")
        if verdict == "needs_answer":
            print("        the thread's last word is a question. Answer it before a "
                  "decision is asked for.")
        if verdict == "needs_judgment":
            print(f"        {quote}")
        return False

    print(f"  {cid}  {verdict} → {target}    “{quote[:90]}”")
    if not confirm:
        return False

    note = notion_rest.rich_text_value(row, "Quick status note").strip()
    line = (f"{date.today().isoformat()} — {current} → {target} by {who} at gate {gate}. "
            f"“{quote[:200]}”")
    props = {
        "Status": notion_rest.make_select(target),
        "Quick status note": notion_rest.make_rich_text(f"{note}\n{line}".strip() if note else line),
        "Decided at": notion_rest.make_date(date.today().isoformat()),
        "Last seen": notion_rest.make_date(date.today().isoformat()),
    }
    if verdict == "rejected":
        props["Rejection reason"] = notion_rest.make_select("other")
    if verdict == "deferred":
        props["Rejection reason"] = notion_rest.make_select("timing")
    notion_rest.notion.update_page_props(row["id"], props)
    if gate == 2 and verdict == "rejected":
        print(register_stub(row, quote, who))
    return True


def register_stub(row: dict[str, Any], quote: str, who: str) -> str:
    """A register entry, ready to paste into product:prompt-knowledge.

    A candidate dropped at Gate 2 was drafted, evaluated and read — it is the
    most expensive kind of answer this loop produces, and the reason it was
    dropped is exactly what stops the digest raising it again next month. Printed
    rather than written: the register lives in the repo and lands through a PR,
    and the wording is a person's to settle.

    Deliberately incomplete. `reopens_if` is left blank because only a human can
    say what would reopen the question, and an entry without one is a prejudice
    with a version number.
    """
    cid = candidate_id_str(row)
    title = notion_rest.title_text(row, "Title")
    prompter = notion_rest.select_value(row, "Prompter") or "?"
    diff = notion_rest.rich_text_value(row, "Proposed diff").strip()
    return "\n".join([
        "",
        f"        {cid} was dropped at Gate 2. Add it to the register so the digest",
        "        does not re-propose it — references/register.yaml in",
        "        product:prompt-knowledge:",
        "",
        "          REG-n:",
        f"            title: \"{title}\"",
        "            verdict: hard_stop      # or rewrite_required / known_fyi",
        f"            applies_to: [{prompter}]",
        "            refuses:",
        f"              - \"{(diff or title).splitlines()[0][:110]}\"",
        f"            finding: >-   # what the evidence showed. {who} said:",
        f"              {quote[:180]}",
        "            cost: >-      # what it took to find out",
        "            reopens_if: >-  # REQUIRED. Nothing reopens it is not an answer.",
        "            patterns: []  # how this gets phrased when someone proposes it",
        f"            source: \"{cid}, Gate 2, {date.today().isoformat()}\"",
        "",
    ])


# The loop's own posts carry this in their Tools/Slack footer. Matching the skill
# rather than `bot_id` alone keeps another integration's in-thread message from
# being mistaken for the loop asking a question.
LOOP_ASK_SKILLS = ("product:share-prompt-candidate",)


def is_loop_ask(message: dict[str, Any]) -> bool:
    text = message.get("text") or ""
    return bool(message.get("bot_id")) and any(s in text for s in LOOP_ASK_SKILLS)


def replies_to_the_open_question(replies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Human replies that came after the loop's most recent ask.

    A verdict answers the question that preceded it. The loop can ask twice in
    one thread — Gate 1 opens it, Gate 2 replies into it, and a further ask
    follows further evidence — so reading the last human reply regardless of
    position reapplies the previous gate's answer to the new question, before
    anyone has read the new one. A thread with no recognisable ask falls back to
    everything after the parent, which is what a pre-footer thread looks like.
    """
    asks = [i for i, r in enumerate(replies) if is_loop_ask(r)]
    start = asks[-1] + 1 if asks else 1
    return [r for r in replies[start:] if not r.get("bot_id")]


def cmd_sweep(args: argparse.Namespace) -> int:
    rows = harvestable(args.data_source_id)
    if not rows:
        print("no candidate is waiting on a human — nothing to harvest")
        return 0

    written = 0
    for row, gate in rows:
        cid = candidate_id_str(row)
        permalink = _url(row, "Slack thread")
        ts = thread_ts_from_permalink(permalink)
        if not ts:
            print(f"  {cid}  no thread recorded — share it first")
            continue

        replies = slack_read.replies(args.channel, ts)
        human = replies_to_the_open_question(replies)
        if not human:
            print(f"  {cid}  no replies to the open question — waiting at gate {gate}")
            continue

        # The LAST human reply wins: a thread can change its mind, and the most
        # recent word is the one the team stands behind.
        last = human[-1]
        verdict, quote = classify(last.get("text", ""), gate=gate)
        who = last.get("user", "a reviewer")
        if apply_verdict(row, gate, verdict, quote, who, confirm=args.confirm):
            written += 1

    if not args.confirm:
        print("\n--- not writing. Re-run with --confirm to record these verdicts.")
    else:
        print(f"\n{written} verdict(s) recorded")
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    """Classify a line of text without touching Slack or Notion — for calibration."""
    verdict, quote = classify(args.text, gate=args.gate)
    print(f"{verdict}  “{quote}”")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-source-id", default=AURIE_CONVERSATIONS_DS)
    p.add_argument("--channel", default=LOOP_CHANNEL)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sweep", help="read every waiting thread and record verdicts")
    sp.add_argument("--confirm", action="store_true",
                    help="required to actually write — otherwise this is a dry run")
    sp.set_defaults(func=cmd_sweep)

    sp = sub.add_parser("classify", help="classify one line of text, offline")
    sp.add_argument("text")
    sp.add_argument("--gate", type=int, default=1, choices=(1, 2))
    sp.set_defaults(func=cmd_classify)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
