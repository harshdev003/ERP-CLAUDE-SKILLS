#!/usr/bin/env python3
"""
Aura LLM latency report — per-prompter, from /ai/dialogue/metrics.

Single POST to the IntelligenceService dialogue-metrics endpoint, which
aggregates per-turn latency server-side (Postgres `dialogue.metrics` JSONB)
and returns one row per group with P50/P95/P99/Max already computed in
milliseconds. No client-side percentile math, no pagination — the endpoint
does the aggregation. Replaces the old Braintrust BTQL implementation
(ENG-677 / umbrella PROD-2456); the per-turn latency data the old script
read out of Braintrust now lives in `dialogue.metrics`, surfaced by the
endpoint shipped in ENG-676.

Three timing fields surfaced (all milliseconds, server-side):

  * ttft  — time to first token. "How long until the user sees the first
    character streaming?" (≈ the old Braintrust TTFT.)
  * ttlt  — time to last token (full LLM response). "How long until the
    full reply finished generating?" (the stand-in for the old
    Braintrust end-to-end / E2E root-span latency.)
  * api.duration — handler-level duration end to end. New; useful as the
    outermost view of how long a turn took inside IntelligenceService.

Primary grouping is by `prompterName` (coach-prompter, memory-prompter, …)
— finer than the old per-Braintrust-project grouping. A secondary
`groupBy: model` table is fetched too (cheap, same call shape) when more
than one model is in play.

Usage:
    report.py                                       # yesterday (UTC)
    report.py --window today
    report.py --window last-7d
    report.py --since 2026-05-10T00:00:00Z --until 2026-05-13T00:00:00Z
    report.py --json /tmp/aura_latency.json
    report.py --base-url https://services.dev.aurahealth.io  # dev override

Env:
    AURA_API_TOKEN  (required — privileged IntelligenceService bearer token)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import pathlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


PROD_BASE = "https://services.aurahealth.io"
METRICS_PATH = "/ai/dialogue/metrics"

# Metric keys as the endpoint returns them; mapped to clean JSON keys below.
METRICS = ["ttft", "ttlt", "api.duration"]
PERCENTILES = [50, 95, 99]

# endpoint metric key -> JSON-payload key (dots are awkward downstream)
METRIC_JSON_KEY = {
    "ttft": "ttft",
    "ttlt": "ttlt",
    "api.duration": "api_duration",
}


def post_metrics(
    base_url: str,
    token: str,
    since_iso: str,
    until_iso: str,
    group_by: str,
) -> list[dict[str, Any]]:
    """One POST to /ai/dialogue/metrics. Returns the list of group rows.

    Surfaces the endpoint's own errors cleanly — in particular the
    DIALOGUE_METRICS_WINDOW_TOO_LARGE 422 raised when the window exceeds
    the server-side max (90 days today).
    """
    url = base_url.rstrip("/") + METRICS_PATH
    body = {
        "since": since_iso,
        "until": until_iso,
        "groupBy": [group_by],
        "metrics": METRICS,
        "percentiles": PERCENTILES,
    }
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": user_agent(),
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        )
    except requests.RequestException as exc:
        sys.exit(f"Request to {url} failed: {exc}")

    if r.status_code == 422:
        # Server-rejected request — most commonly the 90-day window cap.
        err = _parse_error(r)
        key = err.get("key")
        if key == "DIALOGUE_METRICS_WINDOW_TOO_LARGE":
            max_days = err.get("maxWindowDays", 90)
            sys.exit(
                f"Window too large: the endpoint allows at most {max_days} days "
                f"(DIALOGUE_METRICS_WINDOW_TOO_LARGE). Narrow --since/--until and retry."
            )
        sys.exit(f"Aura metrics 422: {err.get('message') or r.text[:500]}")

    if r.status_code in (401, 403):
        sys.exit(
            f"Aura metrics {r.status_code}: auth rejected. Confirm AURA_API_TOKEN is "
            f"a current privileged IntelligenceService token in ~/.claude/settings.json."
        )

    if not r.ok:
        err = _parse_error(r)
        sys.exit(f"Aura metrics {r.status_code}: {err.get('message') or r.text[:500]}")

    data = r.json()
    if not isinstance(data, list):
        sys.exit(f"Unexpected response shape (expected a JSON array): {str(data)[:500]}")
    return data


def _parse_error(r: requests.Response) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        return {}
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"]
    if isinstance(body, dict):
        return body
    return {}


def normalise_bucket(bucket: dict[str, Any] | None) -> dict[str, Any]:
    """Map the endpoint's {p50,p95,p99,max} to {p50_ms,p95_ms,p99_ms,max_ms}.

    Values are already milliseconds — no scaling. A missing/None bucket
    (e.g. ttft on a non-streaming group) yields all-None.
    """
    b = bucket or {}
    return {
        "p50_ms": b.get("p50"),
        "p95_ms": b.get("p95"),
        "p99_ms": b.get("p99"),
        "max_ms": b.get("max"),
    }


def normalise_rows(raw: list[dict[str, Any]], group_key: str) -> list[dict[str, Any]]:
    """Turn raw endpoint rows into clean payload rows.

    `group_key` is the endpoint's group field ("prompterName" or "model").
    Each output row carries the group value, n, and the three normalised
    metric buckets keyed by METRIC_JSON_KEY.
    """
    rows: list[dict[str, Any]] = []
    for r in raw:
        row: dict[str, Any] = {
            group_key: r.get(group_key),
            "n": r.get("n", 0),
        }
        for ep_key, json_key in METRIC_JSON_KEY.items():
            row[json_key] = normalise_bucket(r.get(ep_key))
        rows.append(row)
    return rows


def resolve_window(args: argparse.Namespace) -> tuple[str, str, str]:
    """Return (since_iso, until_iso, human_label). Half-open [since, until)."""
    if args.since:
        since = args.since
        until = args.until or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        return since, until, f"{since} -> {until}"

    now = datetime.now(timezone.utc).replace(microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0)

    if args.window == "yesterday":
        until = today_start
        since = until - timedelta(days=1)
        return since.isoformat(), until.isoformat(), "yesterday (UTC)"
    if args.window == "today":
        return today_start.isoformat(), now.isoformat(), "today (UTC, partial)"
    if args.window == "last-7d":
        return (now - timedelta(days=7)).isoformat(), now.isoformat(), "last 7 days"
    sys.exit(f"Unknown --window: {args.window!r}")


def fmt_ms(v: float | None) -> str:
    """ms under 1000 -> 'NNNms'; else 'N.NNs'. (Carried over from the BT version.)"""
    if v is None:
        return "—"
    if v >= 1000:
        return f"{v / 1000:.2f}s"
    return f"{v:.0f}ms"


def fmt_group(value: Any) -> str:
    """Render a group label; the endpoint emits null for un-prompted handler calls."""
    return str(value) if value not in (None, "") else "(none)"


def _ttlt_p95(row: dict[str, Any]) -> float:
    return row["ttlt"].get("p95_ms") or 0


def render_prompter_table(rows: list[dict[str, Any]], window_label: str) -> str:
    """Per-prompter markdown table, sorted by TTLT P95 desc (slowest on top)."""
    rows = sorted(rows, key=_ttlt_p95, reverse=True)
    out: list[str] = []
    out.append(f"# Aura latency — {window_label}\n")
    out.append(
        "| Prompter | n | TTFT P50 | TTFT P95 | TTFT P99 | "
        "TTLT P50 | TTLT P95 | TTLT P99 | API dur P95 |"
    )
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        ttft = r["ttft"]
        ttlt = r["ttlt"]
        api = r["api_duration"]
        out.append(
            f"| {fmt_group(r['prompterName'])} | {r['n']:,} | "
            f"{fmt_ms(ttft['p50_ms'])} | {fmt_ms(ttft['p95_ms'])} | {fmt_ms(ttft['p99_ms'])} | "
            f"{fmt_ms(ttlt['p50_ms'])} | {fmt_ms(ttlt['p95_ms'])} | {fmt_ms(ttlt['p99_ms'])} | "
            f"{fmt_ms(api['p95_ms'])} |"
        )
    return "\n".join(out)


def render_model_table(rows: list[dict[str, Any]]) -> str:
    """Secondary per-model table — only worth showing when >1 model is present."""
    rows = sorted(rows, key=_ttlt_p95, reverse=True)
    out: list[str] = []
    out.append("## By model\n")
    out.append("| Model | n | TTFT P95 | TTLT P95 | API dur P95 |")
    out.append("|---|---:|---:|---:|---:|")
    for r in rows:
        out.append(
            f"| {fmt_group(r['model'])} | {r['n']:,} | "
            f"{fmt_ms(r['ttft']['p95_ms'])} | {fmt_ms(r['ttlt']['p95_ms'])} | "
            f"{fmt_ms(r['api_duration']['p95_ms'])} |"
        )
    return "\n".join(out)


def headline(rows: list[dict[str, Any]], window_label: str) -> str:
    """One-line headline: slowest prompter by TTLT P95 + slowest by TTFT P95."""
    scored = [r for r in rows if r["n"] > 0]
    if not scored:
        return f"Aura latency — {window_label}: no turns recorded in this window."

    ttlt_cands = [r for r in scored if (r["ttlt"].get("p95_ms") or 0) > 0]
    parts = [f"**Aura latency, {window_label}:**"]
    if ttlt_cands:
        worst_ttlt = max(ttlt_cands, key=lambda r: r["ttlt"]["p95_ms"])
        parts.append(
            f"{fmt_group(worst_ttlt['prompterName'])} is the slowest prompter — "
            f"TTLT P95 {fmt_ms(worst_ttlt['ttlt']['p95_ms'])}"
        )

    ttft_cands = [r for r in scored if (r["ttft"].get("p95_ms") or 0) > 0]
    if ttft_cands:
        worst_ttft = max(ttft_cands, key=lambda r: r["ttft"]["p95_ms"])
        parts.append(
            f"and {fmt_group(worst_ttft['prompterName'])} has the slowest TTFT P95 "
            f"({fmt_ms(worst_ttft['ttft']['p95_ms'])})"
        )

    if len(parts) == 1:  # had turns but no ttft/ttlt percentiles (handler-only)
        parts.append("no streaming-token latencies recorded (handler-only traffic)")
    return " ".join(parts) + "."


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--window",
        choices=("yesterday", "today", "last-7d"),
        default="yesterday",
        help="Pre-set time window (UTC). Default: yesterday.",
    )
    ap.add_argument("--since", help="ISO timestamp lower bound (overrides --window)")
    ap.add_argument("--until", help="ISO timestamp upper bound (exclusive, used with --since)")
    ap.add_argument(
        "--base-url",
        default=PROD_BASE,
        help=f"IntelligenceService base URL. Default: {PROD_BASE}. "
        f"Dev override: https://services.dev.aurahealth.io",
    )
    ap.add_argument("--json", dest="json_out", help="Write structured JSON to this path")
    args = ap.parse_args()

    token = os.environ.get("AURA_API_TOKEN")
    if not token:
        sys.exit(
            "AURA_API_TOKEN not set. Add it to the `env` block of ~/.claude/settings.json "
            "(privileged IntelligenceService token — ask #ai-eng), then restart Claude Code."
        )

    since, until, window_label = resolve_window(args)

    print(
        f"# Fetching Aura latency, window={window_label}, base={args.base_url}",
        file=sys.stderr,
    )

    prompter_raw = post_metrics(args.base_url, token, since, until, "prompterName")
    prompter_rows = normalise_rows(prompter_raw, "prompterName")

    # Secondary per-model view — cheap (one extra POST), only render when useful.
    model_raw = post_metrics(args.base_url, token, since, until, "model")
    model_rows = normalise_rows(model_raw, "model")
    distinct_models = {r["model"] for r in model_rows if r["model"] is not None}

    print()
    print(headline(prompter_rows, window_label))
    print()
    print(render_prompter_table(prompter_rows, window_label))
    if len(distinct_models) > 1:
        print()
        print(render_model_table(model_rows))

    if args.json_out:
        payload = {
            "window_label": window_label,
            "since": since,
            "until": until,
            "prompters": prompter_rows,
        }
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nWrote {args.json_out}")


if __name__ == "__main__":
    main()
