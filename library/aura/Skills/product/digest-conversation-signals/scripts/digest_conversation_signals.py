#!/usr/bin/env python3
"""Rank conversation defects from the signal databases into candidate JSON.

The Conversations loop's ideation leg, and the one with no Features-loop analogue:
the Features loop walks the app with a sim test, this one reads what users
actually said.

It ranks and it does NOT diagnose. Output rows carry a defect, its evidence and
its sources, with `diagnosis: "not yet diagnosed"` — which `share-prompt-candidate`
then refuses to post. That refusal is the point: a complaint is not a diagnosis,
and the loop's most expensive mistake would be laundering the loudest thing users
say straight into a proposed change.

Reads three Notion databases the analysis cadences already populate, plus the
loop's own database so an answered question is not re-raised.

Self-contained per AINative principle 1: `notion_rest.py` is a per-skill copy.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import notion_rest  # noqa: E402
import register  # noqa: E402

# Signal sources the analysis cadences already populate. These are DATA SOURCE
# ids, not database ids: under Notion API 2025-09-03 a query addresses the data
# source, and passing the database id 404s. Resolve a new one with
# GET /v1/databases/<id> and read `data_sources[].id`.
CONVERSATIONS_DS = "2895c7d1-a921-49ac-ad16-91b3ac70951f"
CANCELLATIONS_DS = "edce288d-5d89-4b4d-8083-e40d0ed7c1ee"

# Conversation Patterns (data source 86ba6f79-e9dc-4ffb-9f05-2bd991b2ae8f) is NOT
# read, and that is deliberate. Its themes are topical -- relationship_attachment,
# parenting_family, grief_loss -- describing what users brought, not what Aurie
# did. Ranking them would propose "fix parenting". It is a coverage corpus, useful
# for asking whether a change reaches the subjects people actually raise; it is not
# a defect source, and wiring it in as one would make the ranking worse.
# The loop's own database, read to avoid re-raising an answered question.
AURIE_CONVERSATIONS_DS = "3e375e23-af20-81d5-b99a-000b1b861edc"

# A candidate is either still moving, or the loop has said something about it.
# OPEN is the enumerated list; SETTLED is deliberately "everything else" rather
# than a second enumeration, because a status missing from BOTH lists silently
# becomes an uncovered theme and the digest re-raises a question already answered.
# `won`, `inconclusive`, `deferred` and `held` were each missing exactly that way.
OPEN_STATUSES = {"proposed", "shared", "approved", "drafted", "evaluated",
                 "in_review", "merged", "released", "experiment_running"}
# Of the settled ones, these mean the question was TESTED — worth naming in the
# output, where parked ones (deferred, held) read differently to a reader.
ANSWERED_STATUSES = {"rejected", "lost", "abandoned", "rolled_back", "won",
                     "inconclusive"}

# Themes that name a user's SUBJECT rather than a defect in Aurie's behaviour.
# Ranking on these would propose "fix grief", which is not a prompt change.
# The test for membership: does the theme name something the USER brought, or
# something Aurie did? `abandonment` reads like a defect and is not one — 27 of its
# 30 rows are the relationships vertical, with titles like "how long do I keep
# reaching out" and "I wasn't good enough", and it co-occurs with the real defect
# tags on those same rows. A subject tag sitting beside defect tags is the shape to
# watch for; when in doubt, read the rows rather than the name.
TOPIC_THEMES = {
    "anxiety", "grief", "self_worth", "loneliness", "affair", "self_blame",
    "somatic_anchoring", "breakup-processing", "depression", "stress", "sleep",
    "relationships", "parenting", "work", "health", "trauma", "purpose", "anger",
    "abandonment",
}

# Themes the loop has already reasoned about, with the settled reading. The theme
# is real and the obvious response to it is wrong, so the digest carries the
# reading alongside the rank rather than letting the volume speak for itself.
#
# Built from the register rather than held here, so the loop's most load-bearing
# knowledge has one home. references/register.yaml is a byte-identical mirror of
# the copy in product:prompt-knowledge; add or retire an entry there and copy it
# here in the same change.
HARD_STOPS = register.hard_stops_by_theme()
KNOWN_READINGS = register.notes_by_theme()

# Which prompter a defect most likely belongs to, by the conversation type the
# rows carry. A guess, printed as one -- the human confirms it.
TYPE_TO_PROMPTER = {
    "Coaching": "coach",
    "Chat": "coach",          # chat runs on the coach prompter in production
    "Quick Processing": "quickProcessing",
    "Other": "coach",
}


def _rows(db_id: str) -> list[dict[str, Any]]:
    return list(notion_rest.notion.query_data_source(db_id))


def _select(row: dict[str, Any], name: str) -> str:
    return notion_rest.select_value(row, name) or ""


def _multi(row: dict[str, Any], name: str) -> list[str]:
    prop = (row.get("properties") or {}).get(name) or {}
    return [o.get("name", "") for o in (prop.get("multi_select") or [])]


def _date(row: dict[str, Any], name: str) -> str:
    prop = (row.get("properties") or {}).get(name) or {}
    value = (prop.get("date") or {}).get("start") or ""
    return value[:10] or row.get("created_time", "")[:10]


def _within(day: str, since: str) -> bool:
    return bool(day) and day >= since


# ---- ranking --------------------------------------------------------------

class Defect:
    def __init__(self, theme: str) -> None:
        self.theme = theme
        self.total = 0
        self.negative = 0
        self.in_window = 0
        self.types: Counter = Counter()
        self.verticals: Counter = Counter()
        self.examples: list[str] = []

    @property
    def negative_share(self) -> float:
        return self.negative / self.total if self.total else 0.0

    @property
    def score(self) -> float:
        """Rank on negative volume inside the window, weighted by how reliably
        the theme is negative. A theme that is 100% negative on 10 rows outranks
        one that is 50% negative on 14 — the first is a defect, the second is a
        topic people feel two ways about."""
        return self.in_window * (0.5 + self.negative_share)

    @property
    def prompter(self) -> str:
        if not self.types:
            return "coach"
        return TYPE_TO_PROMPTER.get(self.types.most_common(1)[0][0], "coach")


def collect(since: str) -> tuple[dict[str, Defect], int, int]:
    """Returns (defects, rows_in_window, themed_rows_in_window)."""
    defects: dict[str, Defect] = defaultdict(lambda: Defect(""))
    in_window = themed = 0

    for row in _rows(CONVERSATIONS_DS):
        day = _date(row, "Conversation Date")
        if not _within(day, since):
            continue
        in_window += 1
        themes = [t for t in _multi(row, "Themes") if t not in TOPIC_THEMES]
        if _multi(row, "Themes"):
            themed += 1
        rating = _select(row, "Rating")
        for theme in themes:
            d = defects[theme]
            d.theme = theme
            d.total += 1
            d.in_window += 1
            if rating == "Negative":
                d.negative += 1
            d.types[_select(row, "Conversation Type")] += 1
            d.verticals[_select(row, "Vertical")] += 1
            # Verbatims come from NEGATIVE rows only. A theme is tagged on rows of
            # every polarity, so taking the first four would quote praise as
            # evidence for a complaint — and the emitted candidate carries those
            # quotes into the Gate 1 post, where a reviewer reads them as the case.
            if rating == "Negative":
                feedback = notion_rest.rich_text_value(row, "Steering Feedback").strip()
                if feedback and len(d.examples) < 4:
                    d.examples.append(feedback[:180])
    return defects, in_window, themed


def register(ds_id: str) -> tuple[list[tuple[str, str, str, str]], list[tuple[str, str, str, str]]]:
    """(answered, open) rows in the loop database, as (id, title, note, haystack).

    `haystack` is every free-text field concatenated. Coverage is decided on that
    rather than on the title, because a human writes a title in their own words
    ("close the practical-help gap") while the machine theme it answers
    (`coping_tools_gap`) appears in the Defect where the digest put it.
    """
    answered, open_rows = [], []
    for row in _rows(ds_id):
        status = _select(row, "Status")
        uid = (row.get("properties") or {}).get("Candidate ID", {}).get("unique_id") or {}
        cid = f"{uid.get('prefix')}-{uid.get('number')}"
        title = notion_rest.title_text(row, "Title")
        note = (_select(row, "Rejection reason")
                or notion_rest.rich_text_value(row, "Quick status note")[:120])
        haystack = " ".join([title] + [
            notion_rest.rich_text_value(row, f)
            for f in ("Defect", "Evidence", "Cause", "Quick status note")]).lower()
        if status in OPEN_STATUSES:
            open_rows.append((cid, title, status, haystack))
        else:
            # Everything not open is settled: answered, parked, or a status added
            # to the lifecycle after this was written. Treating an unknown status
            # as settled is the safe default — it suppresses a candidate rather
            # than duplicating one.
            label = note if status in ANSWERED_STATUSES else f"{status} — {note}"
            answered.append((cid, title, label, haystack))
    return answered, open_rows


def _covered_by(theme: str, rows: list[tuple[str, str, str, str]]) -> list[tuple[str, str, str, str]]:
    """Rows that already name this exact theme somewhere in their text.

    A literal match on the theme token, not a vocabulary overlap. Overlap was
    tried and failed in both directions: it missed the row that answers
    `coping_tools_gap` because its title says "practical-help gap", and it matched
    `first_conversation` against an unrelated row on the word "conversation",
    which appears in nearly every title in this database.
    """
    needle = theme.lower()
    return [r for r in rows if needle in r[3]]


def cancellation_context(since: str) -> tuple[Counter, int]:
    reasons: Counter = Counter()
    total = 0
    for row in _rows(CANCELLATIONS_DS):
        if not _within(_date(row, "Cancellation Date"), since):
            continue
        total += 1
        reasons[_select(row, "Reason") or "unspecified"] += 1
    return reasons, total


# ---- output ---------------------------------------------------------------

def to_candidate(d: Defect, since: str) -> dict[str, Any]:
    verticals = ", ".join(f"{k} {v}" for k, v in d.verticals.most_common(3) if k)
    evidence = (f"Conversations database, theme {d.theme}: {d.in_window} rows since {since}, "
                f"{d.negative} of them negative ({d.negative_share:.0%}). "
                f"Verticals: {verticals or 'unrecorded'}.")
    if d.examples:
        evidence += " Verbatims: " + " | ".join(f'"{e}"' for e in d.examples)
    return {
        "title": f"{d.prompter}: address {d.theme.replace('_', ' ')}",
        "prompter": d.prompter,
        "defect": (f"Theme {d.theme} is tagged on {d.in_window} rated conversations since "
                   f"{since}, {d.negative_share:.0%} of them negative."),
        "evidence": evidence,
        "diagnosis": "not yet diagnosed",
        "statusNote": (f"Raised by the digest on {date.today().isoformat()}. Ranked "
                       f"{d.in_window} rows, {d.negative_share:.0%} negative. Needs a "
                       f"diagnosis, a cause and a named risk before it can be shared."),
    }


def cmd_rank(args: argparse.Namespace) -> int:
    since = (date.today() - timedelta(days=args.days)).isoformat()
    defects, in_window, themed = collect(since)
    answered, open_rows = register(args.data_source_id)

    ranked = sorted((d for d in defects.values() if d.in_window >= args.min_rows),
                    key=lambda d: d.score, reverse=True)

    print(f"window: {since} → today ({args.days}d)")
    print(f"rated conversations in window: {in_window}, of which theme-tagged: {themed}"
          f" ({themed / in_window:.0%})" if in_window else "no rows in window")
    if in_window and themed < in_window * 0.6:
        print("\n  ! Theme tagging is incomplete in this window, so this is a RANKING of")
        print("    what was tagged, not a trend. Do not read movement into it.")

    print(f"\n{'theme':<28}{'rows':>5}{'neg':>5}{'neg%':>6}   coverage")
    for d in ranked[:args.top]:
        blocked = _covered_by(d.theme, answered)
        covered = _covered_by(d.theme, open_rows)
        flag = ""
        if blocked:
            flag = "ANSWERED — " + ", ".join(f"{c} ({n})" for c, _, n, _h in blocked[:2])
        elif covered:
            flag = "open — " + ", ".join(f"{c} [{st}]" for c, _, st, _h in covered[:2])
        if not flag and d.theme in HARD_STOPS:
            flag = "HARD STOP"
        elif not flag and d.theme in KNOWN_READINGS:
            flag = "known"
        print(f"{d.theme:<28}{d.in_window:>5}{d.negative:>5}{d.negative_share:>5.0%}   {flag}")

    stopped = [d.theme for d in ranked[:args.top] if d.theme in HARD_STOPS]
    for theme in stopped:
        print(f"\n  HARD STOP · {theme}\n    {HARD_STOPS[theme]}")
    for theme in [d.theme for d in ranked[:args.top] if d.theme in KNOWN_READINGS]:
        print(f"\n  KNOWN · {theme}\n    {KNOWN_READINGS[theme]}")

    reasons, cancels = cancellation_context(since)
    if cancels:
        print(f"\ncancellations in window: {cancels}")
        for reason, n in reasons.most_common(4):
            print(f"  {n:>4}  {reason}")
        print("  (context, not a ranking input — the complaint is bidirectional and a")
        print("   reason code cannot say which direction a given user meant)")
    return 0


def cmd_emit(args: argparse.Namespace) -> int:
    """Candidate JSON for record-prompt-candidates, answered themes excluded."""
    since = (date.today() - timedelta(days=args.days)).isoformat()
    defects, _, _ = collect(since)
    answered, open_rows = register(args.data_source_id)

    out = []
    for d in sorted(defects.values(), key=lambda d: d.score, reverse=True):
        if d.in_window < args.min_rows:
            continue
        if _covered_by(d.theme, answered) or _covered_by(d.theme, open_rows):
            continue
        if d.theme in HARD_STOPS:
            # Emitted as a candidate it would be recorded, shared, and refused at
            # Gate 1 with a reason the register already holds. Keep it out of the
            # JSON and let `rank` surface it with its reading instead.
            continue
        out.append(to_candidate(d, since))
        if len(out) >= args.top:
            break
    print(json.dumps(out, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-source-id", default=AURIE_CONVERSATIONS_DS)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--min-rows", type=int, default=3,
                   help="ignore themes below this many rows in the window")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("rank", help="the readable digest, with coverage flags")
    sp.set_defaults(func=cmd_rank)

    sp = sub.add_parser("emit", help="candidate JSON for record-prompt-candidates")
    sp.set_defaults(func=cmd_emit)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
