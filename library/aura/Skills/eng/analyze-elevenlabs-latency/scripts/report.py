#!/usr/bin/env python3
"""
ElevenLabs Conversational AI latency report.

Walks `/v1/convai/conversations` for the requested window, fetches per-conversation
detail (so we can read the turn-level metric blocks), and computes P50/P95/P99/Max
client-side for the three latency fields ElevenLabs exposes:

  * convai_llm_service_ttf_sentence       (agent turns) -- "how long until the
                                          LLM finished generating the first
                                          sentence?" Strong proxy for "how fast
                                          does the agent start sounding like
                                          it's thinking?"
  * convai_tts_service_ttfb               (agent turns) -- "how long until the
                                          TTS service started returning audio
                                          bytes?" Combined with the LLM TTFS,
                                          this is the user-perceived
                                          response delay.
  * convai_asr_trailing_service_latency   (user turns)  -- "how long after the
                                          user stopped speaking did ASR
                                          finalise the transcript?" Affects
                                          how "snappy" turn-taking feels.

Default window is **yesterday (full UTC day)** so numbers are stable day-over-day
and always land inside the 10-day retention window. The breakdown is by platform
(`dynamic_variables.platform`, today mostly `mobile`; `web` shows up once that
client ships) so the parent `eng:analyze-latency` skill can do a per-platform
view that's not available from the Aura LLM-latency side alone.

Usage:
    report.py                                                  # yesterday (UTC)
    report.py --window today
    report.py --window last-7d
    report.py --since 2026-05-10 --until 2026-05-13
    report.py --by source                                      # SDK granularity
    report.py --json /tmp/elevenlabs_latency.json

Env:
    ELEVENLABS_ADMIN_API_KEY   preferred -- workspace-scoped
    ELEVENLABS_API_KEY         fallback  -- typically per-agent, may under-count
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip3 install requests")


ELEVENLABS_API = "https://api.elevenlabs.io/v1"
LIST_PATH = "/convai/conversations"
DETAIL_PATH = "/convai/conversations/{conversation_id}"
PAGE_SIZE = 100  # max ElevenLabs allows on the list endpoint

RETENTION_DAYS = 10  # ElevenLabs workspace Privacy panel setting (see Tools/ElevenLabs)

# Latency metric keys we surface, grouped by turn role.
AGENT_METRICS = ("convai_llm_service_ttf_sentence", "convai_tts_service_ttfb")
USER_METRICS = ("convai_asr_trailing_service_latency",)
ALL_METRICS = AGENT_METRICS + USER_METRICS

WINDOW_CHOICES = ("yesterday", "today", "last-7d", "last-10d")


def resolve_window(args: argparse.Namespace) -> tuple[int, int, str]:
    now = datetime.now(tz=timezone.utc)
    if args.since or args.until:
        if not (args.since and args.until):
            sys.exit("--since and --until must be provided together")
        start_dt = _parse_date(args.since)
        end_dt = _parse_date(args.until)
        label = f"{args.since} -> {args.until}"
    elif args.window == "today":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        label = f"today ({start_dt.strftime('%b %-d')} UTC, in progress)"
    elif args.window == "last-7d":
        end_dt = now
        start_dt = now - timedelta(days=7)
        label = "last 7 days"
    elif args.window == "last-10d":
        end_dt = now
        start_dt = now - timedelta(days=10)
        label = "last 10 days (full retention window)"
    else:  # "yesterday" -- default
        end_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = end_dt - timedelta(days=1)
        label = f"yesterday ({start_dt.strftime('%b %-d')} UTC)"

    if start_dt >= end_dt:
        sys.exit(f"Empty window: {start_dt.isoformat()} >= {end_dt.isoformat()}")

    retention_cutoff = now - timedelta(days=RETENTION_DAYS)
    if start_dt < retention_cutoff:
        print(
            f"# WARN window start {start_dt.isoformat()} is older than "
            f"the {RETENTION_DAYS}-day retention cutoff "
            f"({retention_cutoff.isoformat()}); detail fetches for older "
            "conversations may 404 and be skipped.",
            file=sys.stderr,
        )
    return int(start_dt.timestamp()), int(end_dt.timestamp()), label


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit(f"Bad date {s!r} -- expected YYYY-MM-DD (UTC)")


def _request(path: str, params: dict, api_key: str, *, ok_404: bool = False) -> dict | None:
    """GET with retry on 429. Returns None on 404 if `ok_404`."""
    url = f"{ELEVENLABS_API}{path}"
    headers = {"xi-api-key": api_key}
    for attempt in range(5):
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(wait, 30))
            continue
        if r.status_code == 404 and ok_404:
            return None
        if not r.ok:
            sys.exit(
                f"ElevenLabs {r.status_code} on {path}: {r.text[:300]}\n"
                "Hint: the API key needs scopes Workspace Analytics + "
                "ElevenAgents Read. See Tools/ElevenLabs in AI Knowledge."
            )
        return r.json()
    sys.exit(f"ElevenLabs {path}: rate-limited after 5 retries")


def list_conversations(start_unix: int, end_unix: int, api_key: str) -> Iterable[dict]:
    cursor: str | None = None
    while True:
        params: dict = {
            "page_size": PAGE_SIZE,
            "call_start_after_unix": start_unix,
            "call_start_before_unix": end_unix,
        }
        if cursor:
            params["cursor"] = cursor
        page = _request(LIST_PATH, params, api_key) or {}
        items = page.get("conversations") or []
        if not items:
            return
        for c in items:
            ts = c.get("start_time_unix_secs") or 0
            if start_unix <= ts <= end_unix:
                yield c
        if not page.get("has_more") or not page.get("next_cursor"):
            return
        cursor = page["next_cursor"]


def fetch_detail(conversation_id: str, api_key: str) -> dict | None:
    return _request(DETAIL_PATH.format(conversation_id=conversation_id), {}, api_key, ok_404=True)


def extract_platform(detail: dict, *, by: str) -> str:
    """Return the bucket key for the platform breakdown.

    `--by platform`: `dynamic_variables.platform` (mobile/web, set by the Aurie SDK).
    `--by source`:   `source_info.source` (swift_sdk/web_sdk/etc, more granular).
    Falls back to "(unknown)" if the field is missing.
    """
    cicd = detail.get("conversation_initiation_client_data") or {}
    if by == "source":
        si = cicd.get("source_info") or {}
        return (si.get("source") or "(unknown)").strip() or "(unknown)"
    dyn = cicd.get("dynamic_variables") or {}
    return (dyn.get("platform") or "(unknown)").strip() or "(unknown)"


def extract_turn_metrics(detail: dict) -> dict[str, list[float]]:
    """Pull every (metric_name -> [elapsed_seconds, ...]) pair from a detail blob."""
    out: dict[str, list[float]] = {k: [] for k in ALL_METRICS}
    for turn in detail.get("transcript") or []:
        ctm = (turn.get("conversation_turn_metrics") or {}).get("metrics") or {}
        for name in ALL_METRICS:
            v = ctm.get(name)
            if isinstance(v, dict) and v.get("elapsed_time") is not None:
                try:
                    out[name].append(float(v["elapsed_time"]))
                except (TypeError, ValueError):
                    pass
    return out


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile (matches numpy's default)."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = (len(s) - 1) * pct
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return s[lo]
    frac = rank - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def summarise(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50_ms": (percentile(values, 0.50) or 0) * 1000 if values else None,
        "p95_ms": (percentile(values, 0.95) or 0) * 1000 if values else None,
        "p99_ms": (percentile(values, 0.99) or 0) * 1000 if values else None,
        "max_ms": max(values) * 1000 if values else None,
    }


def fmt_ms(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 1000:
        return f"{v / 1000:.2f}s"
    return f"{v:.0f}ms"


METRIC_HEADERS = {
    "convai_llm_service_ttf_sentence": "LLM TTFS",
    "convai_tts_service_ttfb": "TTS TTFB",
    "convai_asr_trailing_service_latency": "ASR trailing",
}


def render_table(rows: list[dict], window_label: str, by: str) -> str:
    """Per-platform markdown table, sorted by conversation count desc."""
    rows = [r for r in rows if r["conversations"] > 0]
    rows.sort(key=lambda r: r["conversations"], reverse=True)

    headers = [by.capitalize(), "Calls", "Turns"]
    for m in ALL_METRICS:
        label = METRIC_HEADERS[m]
        headers += [f"{label} n", f"{label} P50", f"{label} P95", f"{label} P99", f"{label} Max"]

    aligns = ["", "---:", "---:"] + ["---:", "---:", "---:", "---:", "---:"] * len(ALL_METRICS)
    out: list[str] = []
    out.append(f"# ElevenLabs latency — {window_label}  (by {by})\n")
    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(f" {a} " if a else " --- " for a in aligns) + "|")
    for r in rows:
        cells = [r["platform"], f"{r['conversations']:,}", f"{r['turns']:,}"]
        for m in ALL_METRICS:
            s = r["metrics"][m]
            cells += [
                f"{s['n']:,}",
                fmt_ms(s["p50_ms"]),
                fmt_ms(s["p95_ms"]),
                fmt_ms(s["p99_ms"]),
                fmt_ms(s["max_ms"]),
            ]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def headline(rows: list[dict], window_label: str) -> str:
    rows = [r for r in rows if r["conversations"] > 0]
    if not rows:
        return f"ElevenLabs latency — {window_label}: no conversations in this window."
    biggest = max(rows, key=lambda r: r["conversations"])
    parts = [
        f"**ElevenLabs latency, {window_label}:**",
        f"{biggest['conversations']:,} conversations on {biggest['platform']}",
    ]
    llm = biggest["metrics"]["convai_llm_service_ttf_sentence"]
    tts = biggest["metrics"]["convai_tts_service_ttfb"]
    if llm["n"] and tts["n"]:
        parts.append(
            f"-- LLM TTFS P95 {fmt_ms(llm['p95_ms'])}, "
            f"TTS TTFB P95 {fmt_ms(tts['p95_ms'])}"
        )
    return " ".join(parts) + "."


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--window",
        choices=WINDOW_CHOICES,
        default="yesterday",
        help="Pre-set time window (UTC). Default: yesterday.",
    )
    ap.add_argument("--since", help="YYYY-MM-DD (UTC) lower bound, used with --until")
    ap.add_argument("--until", help="YYYY-MM-DD (UTC) upper bound, exclusive")
    ap.add_argument(
        "--by",
        choices=("platform", "source"),
        default="platform",
        help="Breakdown axis. `platform` (default) uses dynamic_variables.platform "
        "(mobile/web). `source` uses source_info.source (swift_sdk/web_sdk/...).",
    )
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    api_key = os.environ.get("ELEVENLABS_ADMIN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        sys.exit(
            "Missing ELEVENLABS_ADMIN_API_KEY (or fallback ELEVENLABS_API_KEY). "
            "Set up: see Tools/ElevenLabs in AI Knowledge."
        )

    start_unix, end_unix, window_label = resolve_window(args)
    print(
        f"# resolving ElevenLabs latency, window={window_label} (by {args.by})",
        file=sys.stderr,
    )

    by_bucket: dict[str, dict] = {}
    n_listed = 0
    n_skipped_404 = 0
    n_skipped_empty = 0
    t0 = time.time()

    for summary in list_conversations(start_unix, end_unix, api_key):
        n_listed += 1
        cid = summary.get("conversation_id")
        if not cid:
            continue
        detail = fetch_detail(cid, api_key)
        if detail is None:
            n_skipped_404 += 1
            continue
        bucket_key = extract_platform(detail, by=args.by)
        bucket = by_bucket.setdefault(
            bucket_key,
            {
                "platform": bucket_key,
                "conversations": 0,
                "turns": 0,
                "_values": {m: [] for m in ALL_METRICS},
            },
        )
        bucket["conversations"] += 1
        bucket["turns"] += len(detail.get("transcript") or [])

        per_metric = extract_turn_metrics(detail)
        if not any(per_metric.values()):
            n_skipped_empty += 1
            continue
        for m, vs in per_metric.items():
            bucket["_values"][m].extend(vs)

        if n_listed % 50 == 0:
            print(
                f"  [latency] fetched {n_listed} conversations, "
                f"{time.time() - t0:.0f}s elapsed",
                file=sys.stderr,
            )

    rows: list[dict] = []
    for key, b in by_bucket.items():
        metrics = {m: summarise(b["_values"][m]) for m in ALL_METRICS}
        rows.append(
            {
                "platform": key,
                "conversations": b["conversations"],
                "turns": b["turns"],
                "metrics": metrics,
            }
        )

    print(
        f"# fetched {n_listed} conversations; "
        f"{n_skipped_404} 404'd (likely past retention); "
        f"{n_skipped_empty} had no metrics; "
        f"{time.time() - t0:.0f}s total",
        file=sys.stderr,
    )

    print()
    print(headline(rows, window_label))
    print()
    print(render_table(rows, window_label, args.by))

    if args.json_out:
        payload = {
            "window_label": window_label,
            "start_unix": start_unix,
            "end_unix": end_unix,
            "by": args.by,
            "listed": n_listed,
            "skipped_404": n_skipped_404,
            "skipped_empty": n_skipped_empty,
            "rows": rows,
        }
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nWrote {args.json_out}")


if __name__ == "__main__":
    main()
