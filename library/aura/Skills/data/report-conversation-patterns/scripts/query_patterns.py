#!/usr/bin/env python3
"""Query the Signals/Conversation Patterns table over a Date window.

Primary sampling frame for data:report-conversation-patterns after the
PROD-3515 Signals migration: harvest-conversation-patterns writes one
write-once row per rating to Signals/Conversation Patterns, and this script
reads those rows back over a Date range for the weekly/monthly report — no
Slack re-fetch needed.

The grain is the rating, not the thread (ENG-1115), so `counts` are rating
counts and two rows can share a `source_id`. `sample.py` collapses those to one
entry per thread for the qualitative pass; the counts deliberately do not.

Resolves the table's Notion data source in this order:
  1. --data-source <id>            (explicit override)
  2. $CONVERSATION_PATTERNS_DS     (env override, optional)
  3. CONVERSATION_PATTERNS_DS      (module constant — the live Signals table)
Emits JSON on stdout (or --json <path>) shaped like the legacy fetch_ratings
output so the report's synthesis + sampling steps are reused:

  {
    "source": "signals_table",
    "window": "7d",
    "from_date": "2026-07-06",
    "to_date": "2026-07-13",
    "counts": {"total": N, "positive": P, "neutral": U, "negative": G,
               "by_type": {...}, "by_theme": {...},
               "distinct_threads": M, "ratings_beyond_first_per_thread": N-M},
    "entries": [ {rating_ts, conversation_id, date, polarity, shape, themes,
                  uid_prefix, feedback, prompt_version, slack_url, signal} ]
  }

If the data source can't be resolved OR the window has zero rows, exits 3 so
the caller can fall back to the Slack path (fetch_ratings.py). Stdlib only
apart from the bundled notion_rest helper (self-contained per Architecture
principle 1).
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(
    0, f"{os.environ.get('CLAUDE_PLUGIN_ROOT', '.')}/skills/report-conversation-patterns/scripts"
)
from notion_rest import (  # noqa: E402
    notion,
    date_start,
    rich_text_value,
    select_value,
    title_text,
)

# Live Signals/Conversation Patterns data source (created under PROD-3515).
# Hardcoded like the other DS-backed skills; $CONVERSATION_PATTERNS_DS or
# --data-source can override for a different table.
CONVERSATION_PATTERNS_DS = "86ba6f79-e9dc-4ffb-9f05-2bd991b2ae8f"

NO_FALLBACK_EXIT = 3

# Signals schema `Session Type` select label → the short `shape` key that
# fetch_ratings.py (the Slack fallback frame) emits, so both frames feed
# sample.py the same stratification vocabulary.
SESSION_TYPE_TO_SHAPE = {
    "Quick Processing": "qp",
    "Coaching": "coaching",
    "Conversation": "conversation",
    "Chat": "chat",
    "Journal": "journal",
    "Profile": "profile",
}

# Shapes whose rated object is a conversation with a fetchable transcript. Journal
# and profile rate something else (a journal entry, the generated profile), so they
# carry no conversation id in either frame.
TRANSCRIPT_SHAPES = {"qp", "coaching", "conversation", "chat"}


def resolve_window(window: str, today: dt.date) -> tuple[dt.date, dt.date]:
    """Return (from_date, to_date) inclusive for a window token."""
    w = window.strip().lower()
    if w in ("month", "1m", "30d"):
        days = 30
    elif w.endswith("d") and w[:-1].isdigit():
        days = int(w[:-1])
    elif w in ("week", "1w", "7d"):
        days = 7
    else:
        raise ValueError(f"unrecognised window: {window!r} (use 7d / 14d / 30d / week / month)")
    return today - dt.timedelta(days=days - 1), today


def _multi_select_names(prop: dict) -> list[str]:
    return [o.get("name", "") for o in (prop or {}).get("multi_select", []) if o.get("name")]


def _url_value(prop: dict) -> str:
    return (prop or {}).get("url") or ""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", default="7d", help="7d / 14d / 30d / week / month")
    ap.add_argument("--data-source",
                    default=(os.environ.get("CONVERSATION_PATTERNS_DS") or CONVERSATION_PATTERNS_DS),
                    help="Signals/Conversation Patterns data source id "
                         "(defaults to the live table; $CONVERSATION_PATTERNS_DS overrides)")
    ap.add_argument("--today", default="", help="override today (YYYY-MM-DD), for testing")
    ap.add_argument("--json", dest="json_out", default="", help="write JSON here instead of stdout")
    args = ap.parse_args()

    if not notion.available:
        print("notion_rest unavailable (no NOTION_TOKEN) — caller should use the Slack fallback",
              file=sys.stderr)
        sys.exit(NO_FALLBACK_EXIT)
    if not args.data_source:
        print("no data source for Signals/Conversation Patterns "
              "(pass --data-source or set $CONVERSATION_PATTERNS_DS once the table exists) — "
              "caller should use the Slack fallback", file=sys.stderr)
        sys.exit(NO_FALLBACK_EXIT)

    # Anchor "today" to UTC: the table's `Date` values derive from UTC Slack
    # timestamps, so a local-timezone today() could include/exclude boundary
    # rows near midnight UTC depending on where the skill runs.
    today = (dt.date.fromisoformat(args.today) if args.today
             else dt.datetime.now(dt.timezone.utc).date())
    from_date, to_date = resolve_window(args.window, today)

    date_filter = {
        "and": [
            {"property": "Date", "date": {"on_or_after": from_date.isoformat()}},
            {"property": "Date", "date": {"on_or_before": to_date.isoformat()}},
        ]
    }
    try:
        rows = list(notion.query_data_source(args.data_source, filter=date_filter, page_size=100))
    except Exception as e:  # noqa: BLE001
        print(f"query failed ({e}) — caller should use the Slack fallback", file=sys.stderr)
        sys.exit(NO_FALLBACK_EXIT)

    entries = []
    counts = {"total": 0, "positive": 0, "neutral": 0, "negative": 0, "by_type": {}, "by_theme": {}}
    for r in rows:
        # notion_rest accessors take (row, prop_name); the local blob helpers
        # take the property blob from r["properties"].
        props = r.get("properties", {})
        polarity = (select_value(r, "Rating") or "").lower()
        session_type = select_value(r, "Session Type") or ""
        # Normalise to fetch_ratings' short `shape` key so sample.py stratifies
        # both frames identically (falls back to a lowercased label if unknown).
        shape = SESSION_TYPE_TO_SHAPE.get(session_type, session_type.lower())
        themes = _multi_select_names(props.get("Themes"))
        uid_prefix = rich_text_value(r, "uid_prefix")
        # `Conversation ID` holds the id of whatever was rated — a conversation id
        # on the conversation shapes, a journal entry id or rendererRating id
        # otherwise. Only the conversation shapes have a fetchable transcript, and
        # sample.py ranks on `conversation_id` truthiness, so exposing a journal or
        # rating id there would rank those rows as transcript-fetchable and send a
        # non-conversation UUID to fetch_logs.py. fetch_ratings.py (the fallback
        # frame) emits None for those shapes — match it, and keep the raw id under
        # `source_id` for provenance.
        source_id = rich_text_value(r, "Conversation ID")
        entries.append({
            # The table's `Rating ID` dedup key — one Slack ts per rating event.
            # Carried through so a sampled entry names the rating it came from, not just
            # its thread. Named `rating_ts` because `fetch_ratings.py` — the other frame
            # feeding the same sampler — already uses `rating_id` for the profile
            # `rendererRating` row id. Do not rename this to `rating_id`.
            "rating_ts": rich_text_value(r, "Rating ID"),
            "conversation_id": source_id if shape in TRANSCRIPT_SHAPES else None,
            "source_id": source_id,
            "date": date_start(r, "Date"),
            "polarity": polarity,
            "shape": shape,
            # Profile rows only — the rated card, or `whole-profile`. None elsewhere.
            "renderer": select_value(r, "Renderer") or None,
            "themes": themes,
            # sample.py dedups per user on `uid` (first 8 chars) then re-emits
            # only `uid_prefix`; the table already stores just the prefix
            # (PII rule), so `uid` == `uid_prefix` here and dedup still works.
            "uid": uid_prefix,
            "uid_prefix": uid_prefix,
            "feedback": rich_text_value(r, "Feedback"),
            "prompt_version": rich_text_value(r, "Prompt Version"),
            "slack_url": _url_value(props.get("Slack URL")),
            "signal": title_text(r, "Signal"),
        })
        counts["total"] += 1
        if polarity in ("positive", "neutral", "negative"):
            counts[polarity] += 1
        if shape:
            counts["by_type"][shape] = counts["by_type"].get(shape, 0) + 1
        for t in themes:
            counts["by_theme"][t] = counts["by_theme"].get(t, 0) + 1

    # `total` counts ratings. A thread rated N times contributes N to it, which is the
    # honest volume number — but it is not a thread count, and reading it as one
    # overstates reach. Report both so the distinction is available to the narrative.
    threads = {e["source_id"] for e in entries if e.get("source_id")}
    counts["distinct_threads"] = len(threads)
    counts["ratings_beyond_first_per_thread"] = (
        sum(1 for e in entries if e.get("source_id")) - len(threads)
    )

    if counts["total"] == 0:
        print(f"0 rows in window {from_date}..{to_date} — caller should use the Slack fallback",
              file=sys.stderr)
        sys.exit(NO_FALLBACK_EXIT)

    output = {
        "source": "signals_table",
        "window": args.window,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "counts": counts,
        "entries": entries,
    }
    out_str = json.dumps(output, ensure_ascii=False, indent=2)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            f.write(out_str)
        print(f"[query_patterns] wrote {args.json_out} ({counts['total']} rows)", file=sys.stderr)
    else:
        print(out_str)


if __name__ == "__main__":
    main()
