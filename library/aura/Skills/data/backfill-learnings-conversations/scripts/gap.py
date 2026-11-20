#!/usr/bin/env python3
"""Compute the Learnings/Conversations backfill gap, deterministically.

`Signals/Conversation Patterns` holds one row per rating *event*;
`Learnings/Conversations` holds one row per conversation someone analysed. The gap
is the set of rated conversations with no analysis row.

The gap is recomputed from the live tables on every run, which is what makes the
backfill resumable with no state on disk: a row written by a previous chunk is in
`Learnings/Conversations`, so the next run does not offer it again. Nothing to
cap, prune or keep in sync between sessions — the same reasoning the analysis
cadences use for dedup.

MCP cannot join two data sources on our Notion plan, so this reads both over REST.

Stdlib + the bundled `notion_rest.py` only.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from notion_rest import (  # noqa: E402
    notion,
    date_start,
    rich_text_value,
    select_value,
    title_text,
)

PATTERNS_DS = "86ba6f79-e9dc-4ffb-9f05-2bd991b2ae8f"   # Signals/Conversation Patterns
LEARNINGS_DS = "2895c7d1-a921-49ac-ad16-91b3ac70951f"  # Learnings/Conversations

POLARITIES = ("Negative", "Neutral", "Positive")

# `Conversation ID` is "the id of whatever was rated, not always a conversation"
# (see the Signals/Conversation Patterns schema). Only these four shapes carry an
# Aurie conversation id that `data:analyze-user-conversations` can resolve —
# `Journal` carries a journal entry id and `Profile` a `rendererRating` row id,
# both UUIDs from other systems. Handing either to the conversation lookup fails
# every time, and because this skill keeps no state the same ids would return in
# every chunk forever.
CONVERSATION_SHAPES = ("Conversation", "Chat", "Quick Processing", "Coaching")


def _multi_select(page: dict, prop: str) -> list[str]:
    vals = page.get("properties", {}).get(prop, {}).get("multi_select") or []
    return [v.get("name", "") for v in vals if v.get("name")]


def recorded_conversation_ids() -> set[str]:
    """Every Conversation ID already carrying a Learnings/Conversations row."""
    out = set()
    for row in notion.query_data_source(LEARNINGS_DS, page_size=100):
        cid = rich_text_value(row, "Conversation ID").strip().lower()
        if cid:
            out.add(cid)
    return out


SEVERITY = {"Negative": 0, "Neutral": 1, "Positive": 2}


def rank(entry: dict) -> tuple:
    """Sort key for choosing which rating represents a conversation. Lower wins.

    Module-level and pure so the rule can be tested without Notion — an
    order-dependent pick yields a plausible, unreproducible count, which is the
    failure this ordering exists to prevent.
    """
    return (
        SEVERITY.get(entry.get("rating", ""), 9),        # worst polarity first
        0 if entry.get("feedback") else 1,               # then a rating with feedback
        entry.get("date") or "9999-99-99",               # then the earliest
    )


def rated_conversations() -> dict[str, dict]:
    """One entry per distinct conversation, from the rating stream.

    A conversation can be rated more than once, **and the ratings can disagree**:
    207 of 7,231 carry more than one polarity (35 Negative+Positive, 10
    Negative+Neutral, 9 all three, measured 2026-09-16). So "the negatives" has more
    than one possible answer, and picking the representative by arrival order makes
    the count depend on which row the sweep happened to read first — a number that
    looks like a fact and is not reproducible.

    The representative is therefore chosen by a stated rule, worst rating first:

    1. **Lowest polarity wins** (Negative < Neutral < Positive), so `--polarity
       Negative` means *received at least one Negative rating*. That is the set worth
       analysing — a user who rated a conversation badly at any point is signal, and
       a later thumbs-up does not retract it.
    2. Then a rating carrying **feedback text** beats one without, since the analyst
       has more to work with.
    3. Then the **earliest** rating, as the reaction closest to the conversation.

    Rule 1 is what makes the count order-independent, and it is the only one that
    changes which conversations are selected rather than merely which row describes
    them.
    """
    best: dict[str, dict] = {}
    for row in notion.query_data_source(PATTERNS_DS, page_size=100):
        cid = rich_text_value(row, "Conversation ID").strip()
        if not cid:
            continue
        key = cid.lower()
        entry = {
            "conversation_id": cid,
            "rating": select_value(row, "Rating") or "",
            "session_type": select_value(row, "Session Type") or "",
            "date": date_start(row, "Date") or "",
            "slack_url": (row.get("properties", {}).get("Slack URL", {}) or {}).get("url") or "",
            "feedback": rich_text_value(row, "Feedback").strip(),
            "uid_prefix": rich_text_value(row, "uid_prefix").strip(),
            "themes": _multi_select(row, "Themes"),
            "signal": title_text(row, "Signal"),
            "rating_id": rich_text_value(row, "Rating ID").strip(),
        }
        prev = best.get(key)
        if prev is None:
            best[key] = entry
            continue
        # Prefer an entry with feedback; then the earlier date.
        if rank(entry) < rank(prev):
            best[key] = entry
    return best


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--polarity", default="Negative",
        help="Comma-separated subset of Negative,Neutral,Positive (default: Negative). "
             "'all' selects every polarity.",
    )
    ap.add_argument("--since", help="Only conversations rated on/after this date (YYYY-MM-DD).")
    ap.add_argument("--until", help="Only conversations rated on/before this date (YYYY-MM-DD).")
    ap.add_argument("--days", type=int, help="Shorthand for --since <today minus N days>.")
    ap.add_argument(
        "--limit", type=int,
        help="Return at most N entries — the chunk size for one backfill run. "
             "Omit for the whole gap (the counts are still reported in full).",
    )
    ap.add_argument(
        "--session-type", default=",".join(CONVERSATION_SHAPES),
        help="Comma-separated session types, or 'all'. Defaults to the four shapes whose "
             "Conversation ID is an Aurie conversation id. Journal and Profile ids belong "
             "to other systems and cannot be resolved by the conversation lookup.",
    )
    ap.add_argument("--json", dest="json_out", help="Write the full JSON payload here.")
    args = ap.parse_args()

    if args.polarity.strip().lower() == "all":
        wanted = set(POLARITIES)
    else:
        wanted = {p.strip().title() for p in args.polarity.split(",") if p.strip()}
        unknown = wanted - set(POLARITIES)
        if unknown:
            sys.exit(f"unknown polarity {sorted(unknown)}; choose from {list(POLARITIES)} or 'all'")

    since = args.since
    if args.days:
        since = (date.today() - timedelta(days=args.days)).isoformat()

    if not notion.available:
        sys.exit("NOTION_TOKEN is not set — this skill cannot compute the gap without it.")

    recorded = recorded_conversation_ids()
    rated = rated_conversations()

    if args.session_type.strip().lower() == "all":
        shapes = None
    else:
        shapes = {s.strip() for s in args.session_type.split(",") if s.strip()}

    gap = []
    excluded_shape = collections.Counter()
    for key, e in rated.items():
        if key in recorded:
            continue
        if e["rating"] not in wanted:
            continue
        if shapes is not None and e["session_type"] not in shapes:
            # Counted, not silently dropped — an omitted row is indistinguishable
            # from one that was never rated.
            excluded_shape[e["session_type"]] += 1
            continue
        if since and e["date"] and e["date"][:10] < since:
            continue
        if args.until and e["date"] and e["date"][:10] > args.until:
            continue
        gap.append(e)

    gap.sort(key=lambda e: (e["date"] or "", e["conversation_id"]))

    by_polarity = collections.Counter(e["rating"] for e in gap)
    by_type = collections.Counter(e["session_type"] for e in gap)

    payload = {
        "filters": {
            "polarity": sorted(wanted), "since": since, "until": args.until,
            "limit": args.limit,
            "session_type": sorted(shapes) if shapes else "all",
        },
        "totals": {
            "distinct_rated_conversations": len(rated),
            "already_recorded": sum(1 for k in rated if k in recorded),
            "gap_all_polarities": sum(1 for k, e in rated.items() if k not in recorded),
            "gap_after_filters": len(gap),
            "returned": min(len(gap), args.limit) if args.limit else len(gap),
            "by_polarity": dict(by_polarity),
            "by_session_type": dict(by_type),
            "excluded_non_conversation": dict(excluded_shape),
        },
        "entries": gap[: args.limit] if args.limit else gap,
    }

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    t = payload["totals"]
    print(f"Distinct rated conversations : {t['distinct_rated_conversations']}")
    print(f"Already in Learnings         : {t['already_recorded']}")
    print(f"Gap (all polarities)         : {t['gap_all_polarities']}")
    print(f"Gap after filters            : {t['gap_after_filters']}  {dict(by_polarity)}")
    if excluded_shape:
        print(f"Excluded (id is not a conversation): {sum(excluded_shape.values())}  "
              f"{dict(excluded_shape)}")
    print(f"Returned this run            : {t['returned']}")
    if not args.json_out:
        print()
        print(json.dumps(payload["entries"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
