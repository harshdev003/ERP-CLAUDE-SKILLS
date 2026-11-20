#!/usr/bin/env python3
"""Window-wide reads of the IntelligenceService `llmCall` ledger.

Why this exists
---------------
`report-aurie-conversations` needs every conversational call in a time window,
across every user. Neither existing reader can answer that:

* OpenAI's stored **chat-completions** list cannot see Responses-API traffic at
  all — `GET /v1/responses` rejects API keys ("must be made with a session
  key"), so stored responses are readable but not *enumerable*. Aurie runs on
  Responses (migrated 2026-08-07), so that list returns a small remnant of the
  traffic and reports no error about the rest.
* `responses_reader.py` (the sibling skills' module) solves the same blindness,
  but pivots on **one user** (`?userId=`) or **one conversation**
  (`?conversationId=`). A report over a whole window has neither.

The ledger answers it directly: BigQuery `aura-main.llm.llm_call` (the IntelligenceService
`llmCall` table, read through the bundled `llm_ledger.py`) takes `from`/`to` bounds with no
user filter, records **both** APIs (`openaiAPI` is `chatCompletions` or `responses`), and
carries every field this report aggregates — `userId`, `conversationId`, `prompterName`,
`project`, `createdAt`, `dialogueId`. So no OpenAI call is needed at all: the report counts
calls and threads, it does not render transcripts. Each read is one query over the
half-open window `[from, to)`.

**`ledger_stats` is a separate aggregate query** (`GROUP BY openaiAPI`), an independent
oracle for how many calls the row read *should* have returned. That lets a caller report
coverage as a share rather than as a boolean "we read both surfaces" — the ENG-1114 lesson,
and the thing that would have made this report's collapse self-evident.

The ledger began recording on 2026-07-23. A window reaching back before that is
not partially covered, it is silently empty for the uncovered days, so
`coverage_start` exists to let the caller refuse rather than under-report.

Every read goes through one `getter` object with `rows(params)` and `stats(params)`
(default: `llm_ledger` itself), so tests stub that one object.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)

# The filters this report may use, all of them `llm_ledger.FILTERS`. `from`/`to` are set by this module, so they are not
# caller-supplied filters.
#
# **An unrecognised filter must never be dropped**: that turns a misspelling into a wrong
# answer rather than an error. Measured on
# 2026-08-04→08-11:
#
#     prompterName=coach-prompter  ->  44,549 / 61,562   (correct)
#     prompter=coach-prompter      ->  45,356 / 191,591  (the whole window)
#     projekt=conversation         ->  45,356 / 191,591  (the whole window)
#
# `prompter` is a plausible-looking wrong name for `prompterName`, and a typo is
# just as bad — both come back as the unfiltered total, which is a number that
# looks entirely reasonable. Downstream that reads as a coverage shortfall
# against an inflated denominator, or sends `coverage_start` to bisect the epoch
# of all traffic instead of the filtered slice.
#
# So filters are checked against this list and an unknown one raises. A
# docstring cannot catch this, because nothing about the failure is visible.
SUPPORTED_FILTERS = frozenset({
    "conversationId",
    "openaiAPI",
    "openaiId",
    "project",
    "prompterName",
    "provider",
    "serviceTier",
    "transaction",
    "userId",
})


class LedgerError(Exception):
    """Unrecoverable auth/transport problem reading the ledger."""


def _check_filters(filters: dict) -> dict:
    """Reject any filter this report does not support, before any read."""
    unknown = sorted(set(filters) - SUPPORTED_FILTERS)
    if unknown:
        raise LedgerError(
            f"unsupported ledger filter(s): {', '.join(unknown)}. Dropping it would "
            "return the UNFILTERED total, a plausible wrong number rather than an "
            f"error. Supported: {', '.join(sorted(SUPPORTED_FILTERS))}."
        )
    return {k: v for k, v in filters.items() if v is not None}


def _iso(unix_ts: int | float) -> str:
    """Unix seconds -> ISO-8601 with milliseconds and `Z`."""
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _params(start: int | float, end: int | float, filters: dict) -> dict:
    """A half-open `[start, end)` window: `llm_ledger`'s `to` is exclusive, like `--until`
    and the OpenAI Costs API's `end_time`, so activity and cost cover the same calls."""
    return {"from": _iso(start), "to": _iso(end), **_check_filters(filters)}


def _call(fn, params: dict):
    try:
        return fn(params)
    except llm_ledger.LedgerError as exc:
        raise LedgerError(str(exc)) from exc


# ---------------------------------------------------------------------------
# The server-side oracle
# ---------------------------------------------------------------------------

def ledger_stats(start: int, end: int, getter: Any = llm_ledger, **filters: Any) -> dict[str, dict]:
    """Per-`openaiAPI` aggregates for the window: `{api_name: {calls, inputTokens, outputTokens}}`.

    This is deliberately a *separate* query from the row read rather than a
    derived total: it is the independent expectation the read is checked
    against, so a truncated read shows up as a coverage shortfall instead of
    being reported as the answer.
    """
    return _call(getter.stats, _params(start, end, filters))


def stats_calls(stats: dict[str, dict]) -> int:
    """Total calls across every API in a `ledger_stats` result."""
    return sum(int(v.get("calls") or 0) for v in stats.values())


# ---------------------------------------------------------------------------
# Coverage — is the window inside the ledger's recorded history at all?
# ---------------------------------------------------------------------------

def coverage_start(start: int, end: int, getter: Any = llm_ledger, **filters: Any) -> int | None:
    """First instant in `[start, end)` with any ledger rows, to the hour.

    Returns `start` when the window is covered from its first day, a later
    timestamp when the ledger only begins partway in, and `None` when the whole
    window is empty.

    Why probe rather than hardcode the epoch: the ledger's first row is a
    deploy artifact (2026-07-23 today), and a hardcoded date silently becomes
    wrong if the table is ever backfilled or rotated. A genuinely zero-traffic
    first day reads as uncovered, which makes the caller refuse rather than
    under-report — the safe direction here.
    """
    day = 86400
    if stats_calls(ledger_stats(start, min(start + day, end), getter, **filters)) > 0:
        return start
    if stats_calls(ledger_stats(start, end, getter, **filters)) == 0:
        return None
    # Invariant: `[start, lo)` has no rows and the first covered instant lies in
    # `[lo, hi)`, so **`hi` is the answer**: an upper bound the caller can re-run
    # from and be covered by construction.
    resolution = 3600
    lo, hi = start, end
    while hi - lo > resolution:
        mid = lo + (hi - lo) // 2
        if stats_calls(ledger_stats(lo, mid, getter, **filters)) > 0:
            hi = mid
        else:
            lo = mid
    return hi


# ---------------------------------------------------------------------------
# The row read
# ---------------------------------------------------------------------------

def walk_ledger(
    start: int,
    end: int,
    getter: Any = llm_ledger,
    progress: Any = None,
    **filters: Any,
) -> list[dict]:
    """Every ledger row in `[start, end)` matching `filters`, in one query."""
    rows = _call(getter.rows, _params(start, end, filters))
    if progress:
        progress(len(rows))
    return rows


def row_unix(row: dict) -> int:
    """A row's `createdAt` as unix seconds (0 when unparseable)."""
    raw = row.get("createdAt") or ""
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError):
        return 0


# ---------------------------------------------------------------------------
# Coverage reporting — a share, never a boolean
# ---------------------------------------------------------------------------

def coverage(rows: list[dict], stats: dict[str, dict]) -> dict[str, Any]:
    """Compare what the walk returned against what `/stats` says exists.

    Emits per-API `walked` / `expected` / `share`, so a consumer can tell a
    trivial gap from a real one. A boolean "both surfaces were read" repeats
    exactly the mistake that let this report halve without complaint.
    """
    walked: dict[str, int] = {}
    for row in rows:
        api = row.get("openaiAPI") or "unknown"
        walked[api] = walked.get(api, 0) + 1
    apis = sorted(set(walked) | set(stats))
    by_api = {}
    for api in apis:
        exp = int((stats.get(api) or {}).get("calls") or 0)
        got = walked.get(api, 0)
        by_api[api] = {
            "walked": got,
            "expected": exp,
            "share": round(got / exp, 4) if exp else (1.0 if got == 0 else None),
        }
    total_exp = sum(v["expected"] for v in by_api.values())
    total_got = sum(v["walked"] for v in by_api.values())
    return {
        "by_api": by_api,
        "walked": total_got,
        "expected": total_exp,
        "share": round(total_got / total_exp, 4) if total_exp else None,
        "complete": total_exp > 0 and total_got >= total_exp,
    }


def main() -> None:  # pragma: no cover - probe entrypoint
    # UTF-8 stdout, per the repo-wide rule: Windows defaults to cp1252 and
    # raises on the `…`/`→` this module prints. Guarded because `reconfigure`
    # only exists on a real TextIOWrapper — a replaced stdout would otherwise
    # make the probe die before printing anything.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Probe: window-wide ledger reads.")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--project", default="conversation")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    end = int(datetime.now(timezone.utc).timestamp())
    start = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp())
    st = ledger_stats(start, end, project=args.project)
    rows = walk_ledger(
        start, end, project=args.project,
        progress=lambda n: sys.stderr.write(f"\r  {n:,} rows"),
    )
    sys.stderr.write("\n")
    cov = coverage(rows, st)
    if args.json:
        print(json.dumps({"coverage": cov, "stats": st}, indent=2, default=str))
        return
    print(f"rows walked : {len(rows):,}")
    print(f"coverage    : {json.dumps(cov['by_api'], indent=2)}")


if __name__ == "__main__":  # pragma: no cover
    main()
