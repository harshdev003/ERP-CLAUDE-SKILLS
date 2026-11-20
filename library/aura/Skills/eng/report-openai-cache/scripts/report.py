#!/usr/bin/env python3
"""Daily OpenAI prompt-cache cost for one prompter, from the IntelligenceService LLM-call ledger.

Read-only. Sums BigQuery `aura-main.llm.llm_call` per UTC day (auth: GCP_KEY) and prints one
row per day plus a Total: requests, input tokens, cached share, what the input cost, what it
would have cost at a baseline cached share, the difference, and input tokens per request.

    cost = (input - cached) * uncached rate + cached * cached rate

The rates are passed in, never stored here: their home is `Tools/OpenAI` (the model's input and
cached-input prices). No prompt text is read; the ledger holds metadata only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

DAILY_SQL = """
SELECT FORMAT_DATE('%Y-%m-%d', DATE(createdAt)) AS day, COUNT(*) AS requests,
       SUM(IFNULL(inputTokens, 0)) AS inputTokens, SUM(IFNULL(cachedTokens, 0)) AS cachedTokens
FROM `aura-main.llm.llm_call`
WHERE prompterName = @prompter AND createdAt >= TIMESTAMP(@since) AND createdAt < TIMESTAMP(@until)
GROUP BY day ORDER BY day
"""
# The table is day-partitioned on createdAt; this caps a mistaken years-long window.
MAX_BYTES = 50 * 1024**3


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str) -> datetime:
    s = value.strip()
    if len(s) == 10:
        s += "T00:00:00Z"
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_daily(prompter: str, start: datetime, end: datetime, query=None) -> list[dict]:
    """Per-UTC-day totals, summed in BigQuery: a month of calls never comes back to this machine."""
    if query is None:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bq  # noqa: E402
        query = bq.query
    rows = query(DAILY_SQL, params={"prompter": prompter, "since": iso(start), "until": iso(end)},
                 max_bytes=MAX_BYTES)
    return [{k: r[k] for k in ("day", "requests", "inputTokens", "cachedTokens")} for r in rows]


def cost(input_tokens: float, cached_tokens: float, uncached_per_m: float, cached_per_m: float) -> float:
    return ((input_tokens - cached_tokens) * uncached_per_m + cached_tokens * cached_per_m) / 1e6


def table(days: list[dict], uncached_per_m: float, cached_per_m: float, baseline_pct: float) -> list[dict]:
    """One row per day plus a Total row. "At baseline" prices the same input at the baseline cached share."""
    def row(day, requests, inp, cached):
        actual = cost(inp, cached, uncached_per_m, cached_per_m)
        base = cost(inp, inp * baseline_pct / 100, uncached_per_m, cached_per_m)
        return {"day": day, "requests": requests, "inputTokens": inp,
                "cachedPct": 100 * cached / inp if inp else None, "cost": actual, "baselineCost": base,
                "difference": actual - base, "tokensPerRequest": inp / requests if requests else None}
    out = [row(d["day"], d["requests"], d["inputTokens"], d["cachedTokens"]) for d in days]
    out.append(row("Total", sum(d["requests"] for d in days), sum(d["inputTokens"] for d in days),
                   sum(d["cachedTokens"] for d in days)))
    return out


def money(v: float) -> str:
    return f"-${-v:,.2f}" if v <= -0.005 else f"${abs(v):,.2f}"


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}%"


def render(rows: list[dict], prompter: str, baseline_pct: float, uncached_per_m: float, cached_per_m: float) -> str:
    out = [
        f"# Prompt-cache cost by day — `{prompter}`",
        "",
        f"Rates: uncached ${uncached_per_m:g}/M, cached ${cached_per_m:g}/M input tokens. "
        f"Difference = input cost − cost at {baseline_pct:g}% cached (positive: it cost more than the baseline).",
        "",
        f"| Day | Requests | Input (M tokens) | Cached % | Input cost | Cost at {baseline_pct:g}% | Difference | Tokens per request |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        b = "**" if r["day"] == "Total" else ""
        cells = [r["day"], f"{r['requests']:,}", f"{r['inputTokens'] / 1e6:,.2f}", pct(r["cachedPct"]),
                 money(r["cost"]), money(r["baselineCost"]), money(r["difference"]),
                 "—" if r["tokensPerRequest"] is None else f"{r['tokensPerRequest']:,.0f}"]
        out.append("| " + " | ".join(f"{b}{c}{b}" for c in cells) + " |")
    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", required=True, help="UTC start: a date (2026-10-01) or ISO-8601 timestamp")
    ap.add_argument("--until", help="UTC end, exclusive (default now)")
    ap.add_argument("--prompter", default="coach-prompter", help="prompterName (default coach-prompter)")
    ap.add_argument("--baseline", type=float, required=True, help="cached share in %% to compare against, e.g. 88.96")
    ap.add_argument("--uncached-per-m", type=float, required=True, help="$ per 1M uncached input tokens (Tools/OpenAI)")
    ap.add_argument("--cached-per-m", type=float, required=True, help="$ per 1M cached input tokens (Tools/OpenAI)")
    ap.add_argument("--json", dest="json_out", help="also write the rows here")
    args = ap.parse_args()

    start = parse_ts(args.since)
    end = parse_ts(args.until) if args.until else datetime.now(timezone.utc)
    days = fetch_daily(args.prompter, start, end)
    if not days:
        sys.exit(f"No ledger rows for prompterName={args.prompter} in that window.")
    rows = table(days, args.uncached_per_m, args.cached_per_m, args.baseline)
    print(render(rows, args.prompter, args.baseline, args.uncached_per_m, args.cached_per_m))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2)


if __name__ == "__main__":
    main()
