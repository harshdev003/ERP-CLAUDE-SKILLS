#!/usr/bin/env python3
"""Classify `Learnings/Conversations` rows into synthesisable analyses vs template stubs.

The Top 10 synthesis clusters *across* rows, so it needs rows that actually carry
an analysis. Most of the database does not: rows recorded by the conversation
harvest sweep have every canonical heading but a one-line templated body under
each ("User explored their feelings with Aurie." / "None notable." / "None."),
so a heading-presence check passes them straight into the clustering pool with
nothing to cluster on.

Four gates decide the verdict, and a row must clear all four:

  G1 sections    all five canonical headings present
  G2 hypotheses  `## Hypotheses to validate` present with real content
  G3 boilerplate fewer than N narrative sections match the boilerplate vocabulary
  G4 substance   total narrative text clears a character floor

They are deliberately redundant. Any one of them separates today's corpus
cleanly on its own, and the JSON reports each gate's own count so a future
divergence is visible instead of silently resolved by whichever gate runs first
— a new sweep template that starts emitting a stub `## Hypotheses to validate`
would trip G3/G4 while G2 waves it through.

Usage
-----
    python3 classify_rows.py --summary
    python3 classify_rows.py --out rows.json
    python3 classify_rows.py --rating-filter rated --summary
    python3 classify_rows.py --dump-vocabulary   # refresh the phrase list below

Stdlib + `notion_rest` only. Needs `NOTION_TOKEN`.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_rest import (  # noqa: E402
    MCPFallback,
    notion,
    rich_text_value,
    select_value,
    title_text,
)

DATA_SOURCE_ID = "2895c7d1-a921-49ac-ad16-91b3ac70951f"

HEADLINE = "## Headline"
WHAT_WORKED = "## What worked"
WHAT_DIDNT = "## What didn't"
HYPOTHESES = "## Hypotheses to validate"
FOLLOW_UP = "## Recommended follow-up"

CANONICAL_SECTIONS = (HEADLINE, WHAT_WORKED, WHAT_DIDNT, HYPOTHESES, FOLLOW_UP)
# The four the boilerplate vocabulary describes. `## Hypotheses to validate` is
# excluded because the sweep template never emits it at all — G2 owns that one.
NARRATIVE_SECTIONS = (HEADLINE, WHAT_WORKED, WHAT_DIDNT, FOLLOW_UP)

# --- boilerplate vocabulary -------------------------------------------------
# Observed values of the templated sections, normalised per `_normalise`.
# Refresh against the live corpus with `--dump-vocabulary`; the substance floor
# (G4) is what catches a phrase that isn't listed yet, so this list going stale
# degrades precision, not correctness.

EMPTY_PHRASES = frozenset({
    "",
    "none",
    "none.",
    "none notable",
    "none notable.",
    "nothing notable",
    "nothing notable.",
    "n/a",
    "-",
})

BOILERPLATE_PHRASES = frozenset({
    "session provided meaningful insight and emotional support.",
    "discovered that allowing oneself to be supported can ease physical tension.",
    "found that survival itself is an accomplishment worthy of gentleness.",
    "realized that safety and pressure-free acceptance matter more than forcing connection.",
    "offered clear, empathetic guidance tailored to the person's immediate need.",
    "used embodied grounding techniques; user felt connected and calm.",
    "aurie validated feelings and offered grounded perspective without dismissing concerns.",
    "strong validation and empathetic mirroring of user's emotional state.",
    "helped user recognize internal clarity; shifted focus to personal agency.",
    "provided relevant guidance or reframe that user appreciated.",
    "may have lacked the specific insight or validation the user needed.",
    "session touched topic but lacked direction or clear breakthrough.",
    "response felt slightly off-target or unclear.",
    "conversation did not meet user's stated needs or expectations.",
    "teach somatic grounding techniques for managing anticipatory anxiety.",
    "audit prompt clarity; ensure aurie directly addresses stated concerns.",
})

BOILERPLATE_PATTERNS = (
    # "User explored grief with Aurie." — vertical slotted into a fixed sentence.
    re.compile(r"^user explored .{0,40} with aurie\.$"),
    # "User's sleep concerns were not adequately addressed."
    re.compile(r"^user'?s .{0,40} concerns were not adequately addressed\.$"),
    # "Aurie: hearing a voice reply to me" — the rater's own words pasted under
    # `## What worked`. Genuine signal, but a quote is not an analysis, and
    # quote-level signal is what `Signals/Conversation Patterns` is for.
    re.compile(r"^aurie: "),
)

# --- gate defaults ----------------------------------------------------------
# On the 2026-08-10 corpus the two populations do not overlap anywhere near
# these values: a real analysis carries >=1173 narrative chars, a sweep stub at
# most 275, and the shortest real `## Hypotheses to validate` runs 195. The
# floors sit in the gap with room on both sides rather than hugging either edge.
DEFAULT_MIN_BODY_CHARS = 400
DEFAULT_MIN_HYPOTHESIS_CHARS = 60
DEFAULT_MAX_BOILERPLATE_SECTIONS = 2

VERDICT_SYNTHESISABLE = "synthesisable"
VERDICT_TEMPLATE_STUB = "template_stub"
VERDICT_MISSING_SECTIONS = "missing_sections"


def _normalise(text: str) -> str:
    """Casefold, strip list markers, collapse whitespace — for phrase matching."""
    text = text.strip()
    text = re.sub(r"^[-*•]\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def is_boilerplate(text: str) -> bool:
    """True when a section's text is templated, empty, or a raw rater quote."""
    norm = _normalise(text)
    if norm in EMPTY_PHRASES or norm in BOILERPLATE_PHRASES:
        return True
    return any(p.match(norm) for p in BOILERPLATE_PATTERNS)


def _block_text(block: dict) -> str:
    kind = block.get("type") or ""
    runs = (block.get(kind) or {}).get("rich_text") or []
    return "".join(r.get("plain_text", "") for r in runs)


def parse_sections(blocks: list[dict]) -> dict[str, str]:
    """Split a page's blocks into `{"## Heading": "body text"}`.

    Headings are keyed by their rendered markdown form so the canonical section
    names above can be compared directly. Content before the first heading is
    dropped, as is `# Sources` (level 1) — it is provenance, not analysis.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for block in blocks:
        kind = block.get("type") or ""
        text = _block_text(block)
        heading = re.fullmatch(r"heading_([123])", kind)
        if heading:
            current = "#" * int(heading.group(1)) + " " + text.strip()
            sections.setdefault(current, [])
            continue
        if current is None or not text.strip():
            continue
        if kind in ("bulleted_list_item", "numbered_list_item"):
            sections[current].append("- " + text.strip())
        else:
            sections[current].append(text.strip())
    return {k: "\n".join(v) for k, v in sections.items()}


def classify(
    sections: dict[str, str],
    *,
    min_body_chars: int = DEFAULT_MIN_BODY_CHARS,
    min_hypothesis_chars: int = DEFAULT_MIN_HYPOTHESIS_CHARS,
    max_boilerplate_sections: int = DEFAULT_MAX_BOILERPLATE_SECTIONS,
) -> dict:
    """Run the four gates over one row's parsed sections.

    Returns the verdict plus every gate's own outcome, so a caller can report
    where the gates disagree instead of only which one fired first.
    """
    missing = [s for s in CANONICAL_SECTIONS if s not in sections]

    hypotheses = sections.get(HYPOTHESES, "")
    boilerplate_hits = [
        s for s in NARRATIVE_SECTIONS
        if s in sections and is_boilerplate(sections[s])
    ]
    narrative_chars = sum(len(sections.get(s, "")) for s in CANONICAL_SECTIONS)

    gates = {
        "sections": not missing,
        "hypotheses": len(hypotheses.strip()) >= min_hypothesis_chars,
        "boilerplate": len(boilerplate_hits) < max_boilerplate_sections,
        "substance": narrative_chars >= min_body_chars,
    }

    reasons = []
    if missing:
        reasons.append("missing sections: " + ", ".join(missing))
    if not gates["hypotheses"]:
        reasons.append(
            f"`{HYPOTHESES}` has {len(hypotheses.strip())} chars "
            f"(< {min_hypothesis_chars})"
        )
    if not gates["boilerplate"]:
        reasons.append(
            "boilerplate sections: " + ", ".join(f"`{s}`" for s in boilerplate_hits)
        )
    if not gates["substance"]:
        reasons.append(f"{narrative_chars} narrative chars (< {min_body_chars})")

    if all(gates.values()):
        verdict = VERDICT_SYNTHESISABLE
    elif missing and not any(
        (not gates[g]) for g in ("hypotheses", "boilerplate", "substance")
    ):
        # Structurally incomplete but what is there reads like a real analysis —
        # worth surfacing separately so a genuinely half-written row can be
        # fixed rather than lumped in with the sweep template.
        verdict = VERDICT_MISSING_SECTIONS
    else:
        verdict = VERDICT_TEMPLATE_STUB

    return {
        "verdict": verdict,
        "gates": gates,
        "reasons": reasons,
        "narrative_chars": narrative_chars,
        "boilerplate_sections": boilerplate_hits,
    }


def _row_summary(row: dict) -> dict:
    props = row.get("properties") or {}
    # Every field the SKILL.md's Step 2 clustering list names, so the agent never
    # has to re-fetch a row. `User ID` is deliberately absent — the full Firebase
    # UID has no clustering use and the synthesis writes to a shared page.
    return {
        "id": row["id"],
        "url": row.get("url"),
        "title": title_text(row),
        "conversation_id": rich_text_value(row, "Conversation ID"),
        "rating": select_value(row, "Rating"),
        "source_type": select_value(row, "Source Type"),
        "conversation_type": select_value(row, "Conversation Type"),
        "prompt_version": rich_text_value(row, "Prompt Version"),
        "vertical": select_value(row, "Vertical"),
        "themes": [
            o["name"] for o in ((props.get("Themes") or {}).get("multi_select") or [])
        ],
    }


def fetch_rows(*, workers: int = 4, limit: int | None = None) -> list[dict]:
    """Enumerate the data source and attach each row's parsed body sections."""
    if not notion.available:
        raise MCPFallback(
            "NOTION_TOKEN is not set. This filter needs full-database "
            "enumeration, which `notion-search` (25-result cap, no cursor) "
            "cannot do — see the SKILL.md Notion-access note."
        )
    rows = list(notion.query_data_source(DATA_SOURCE_ID))
    if limit:
        rows = rows[:limit]

    def one(row: dict) -> dict:
        out = _row_summary(row)
        out["sections"] = parse_sections(notion.get_block_children(row["id"]))
        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, rows))


def build_report(rows: list[dict], args: argparse.Namespace) -> dict:
    total = len(rows)
    dropped_by_rating = 0
    verdicts: list[dict] = []

    # Classify first, then apply the optional rating filter to what survived.
    # The other order attributes an unrated stub to the rating filter, so the
    # stub count — the whole reason this filter exists — would silently shrink
    # whenever `--rating-filter rated` is passed.
    classified = [
        (
            row,
            classify(
                row["sections"],
                min_body_chars=args.min_body_chars,
                min_hypothesis_chars=args.min_hypothesis_chars,
                max_boilerplate_sections=args.max_boilerplate_sections,
            ),
        )
        for row in rows
    ]

    # Each gate's own verdict, over **every** classified row. This is a statement
    # about the corpus, not about the selected slice, so the rating filter must
    # not shrink it — the four numbers are what reveal that the corpus shape
    # moved, and that diagnostic must not get weaker just because a filter is on.
    per_gate = {
        gate: sum(1 for _, r in classified if r["gates"][gate])
        for gate in ("sections", "hypotheses", "boilerplate", "substance")
    }

    for row, result in classified:
        if (
            result["verdict"] == VERDICT_SYNTHESISABLE
            and args.rating_filter == "rated"
            and (row.get("rating") or "N/A") == "N/A"
        ):
            dropped_by_rating += 1
            continue
        entry = {k: v for k, v in row.items() if k != "sections"}
        entry.update(result)
        if result["verdict"] == VERDICT_SYNTHESISABLE:
            # Bodies only for the rows the synthesis will actually read; the
            # stubs' bodies are the thing being excluded.
            entry["sections"] = row["sections"]
        verdicts.append(entry)

    counts = collections.Counter(v["verdict"] for v in verdicts)
    n = counts[VERDICT_SYNTHESISABLE]
    stubs = counts[VERDICT_TEMPLATE_STUB]

    # The byline has to reconcile: every row in `total` lands in exactly one
    # bucket. Naming only the stubs would leave the rating-dropped and
    # structurally-incomplete rows unaccounted for, which is the same
    # under-stated-denominator problem the byline exists to fix.
    excluded = []
    if stubs:
        excluded.append(
            f"{stubs} are sweep-recorded rows whose bodies are templated "
            "one-liners with no synthesisable content"
        )
    if counts[VERDICT_MISSING_SECTIONS]:
        excluded.append(
            f"{counts[VERDICT_MISSING_SECTIONS]} are missing canonical sections"
        )
    if dropped_by_rating:
        excluded.append(
            f"{dropped_by_rating} carry an analysis but are unrated (`Rating: N/A`)"
        )

    byline = f"from {n} of {total} rows below"
    if excluded:
        byline += (
            " (rows carrying a real five-section analysis; of the rest, "
            + ", ".join(excluded)
            + ")"
        )

    return {
        "generated": datetime.date.today().isoformat(),
        "data_source": DATA_SOURCE_ID,
        "total": total,
        "synthesisable": n,
        "template_stubs": stubs,
        "missing_sections": counts[VERDICT_MISSING_SECTIONS],
        "dropped_by_rating_filter": dropped_by_rating,
        "thresholds": {
            "min_body_chars": args.min_body_chars,
            "min_hypothesis_chars": args.min_hypothesis_chars,
            "max_boilerplate_sections": args.max_boilerplate_sections,
        },
        "rows_passing_each_gate": per_gate,
        "byline": byline,
        "rows": verdicts,
    }


def print_summary(report: dict) -> None:
    print(f"Learnings/Conversations — {report['generated']}")
    print(f"  total rows                 {report['total']}")
    print(f"  synthesisable              {report['synthesisable']}")
    print(f"  template stubs             {report['template_stubs']}")
    print(f"  missing sections           {report['missing_sections']}")
    if report["dropped_by_rating_filter"]:
        print(f"  dropped (rating filter)    {report['dropped_by_rating_filter']}")
    print("\n  rows passing each gate (independently):")
    for gate, count in report["rows_passing_each_gate"].items():
        print(f"    {gate:<12} {count}")
    print(f"\n  byline: {report['byline']}")

    keep = [r for r in report["rows"] if r["verdict"] == VERDICT_SYNTHESISABLE]
    if keep:
        print(f"\n  synthesisable rows ({len(keep)}):")
        for r in sorted(keep, key=lambda r: r["title"]):
            themes = ",".join(r["themes"]) or "—"
            print(
                f"    [{r['rating'] or 'N/A':<8}] {r['title'][:58]:<58} "
                f"{r['narrative_chars']:>5}c  {themes[:40]}"
            )


def dump_vocabulary(rows: list[dict]) -> None:
    """Print observed section values among the stubs, so the phrase list can be refreshed."""
    observed: dict[str, collections.Counter] = {
        s: collections.Counter() for s in NARRATIVE_SECTIONS
    }
    for row in rows:
        result = classify(row["sections"])
        if result["verdict"] == VERDICT_SYNTHESISABLE:
            continue
        for section in NARRATIVE_SECTIONS:
            text = row["sections"].get(section)
            if text and "\n" not in text:
                observed[section][text.strip()] += 1
    for section in NARRATIVE_SECTIONS:
        print(f"\n### {section}  ({len(observed[section])} distinct single-line values)")
        for text, count in observed[section].most_common():
            flag = " " if is_boilerplate(text) else "*"
            print(f"{flag}{count:5d} | {text}")
    print("\n(* = not matched by the current vocabulary — candidate to add)")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--rating-filter",
        choices=["all", "rated"],
        default="all",
        help="`rated` drops rows whose Rating is N/A (default: all)",
    )
    ap.add_argument("--min-body-chars", type=int, default=DEFAULT_MIN_BODY_CHARS)
    ap.add_argument(
        "--min-hypothesis-chars", type=int, default=DEFAULT_MIN_HYPOTHESIS_CHARS
    )
    ap.add_argument(
        "--max-boilerplate-sections",
        type=int,
        default=DEFAULT_MAX_BOILERPLATE_SECTIONS,
    )
    ap.add_argument("--limit", type=int, help="only classify the first N rows (debug)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", help="write the JSON report to this path")
    ap.add_argument("--summary", action="store_true", help="human-readable summary")
    ap.add_argument(
        "--dump-vocabulary",
        action="store_true",
        help="print the stub rows' section values, flagging unmatched ones",
    )
    args = ap.parse_args()

    try:
        rows = fetch_rows(workers=args.workers, limit=args.limit)
    except MCPFallback as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dump_vocabulary:
        dump_vocabulary(rows)
        return 0

    report = build_report(rows, args)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        print(f"wrote {args.out}", file=sys.stderr)
    if args.summary or args.out:
        print_summary(report)
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))

    if report["synthesisable"] == 0:
        print(
            "\nerror: no row survived the filter — nothing to synthesise.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
