#!/usr/bin/env python3
"""
OpenAI token usage report for the Aura organization.

Pulls from the OpenAI Usage API (`/v1/organization/usage/completions`) for token
counts (input, cached input, input audio, output, output audio, plus any new
*_tokens field OpenAI ships in the future) and emits two separate tables: one
broken down by model, one broken down by project.

Sibling to `eng:analyze-openai-costs` / `eng:report-openai-costs` which report
dollars. This one reports tokens — no dollar allocation, no per-model rate table.

Usage:
    python report.py                                     # yesterday UTC (default)
    python report.py --window today
    python report.py --window last-7d
    python report.py --window last-30d
    python report.py --since 2026-05-17 --until 2026-05-18
    python report.py --window yesterday --json out.json
    python report.py --window yesterday --no-projects   # model table only
    python report.py --window yesterday --no-models     # project table only

Env:
    OPENAI_ADMIN_API_KEY  (Admin API key, sk-admin-...; required)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

OPENAI_API = "https://api.openai.com/v1"
PROJECTS_PATH = "/organization/projects"
USAGE_COMPLETIONS_PATH = "/organization/usage/completions"

USAGE_PAGE_SIZE = 31  # /organization/usage/* caps at 31 for bucket_width=1d

# Primary token fields rendered by default — the API's top-level totals. Each
# row in the rendered table sums Input + Output to ~Total (cached input is a
# subset of input, not additional).
PRIMARY_TOKEN_FIELDS = (
    "input_tokens",
    "input_cached_tokens",
    "input_audio_tokens",
    "output_tokens",
    "output_audio_tokens",
)

# Sub-breakdown fields OpenAI also returns (input split into text/image/audio
# and uncached/cached, output split into text/image/audio). These double-count
# vs the primary fields, so they're hidden by default and surfaced only with
# `--all-fields` for the rare deep dive.
DETAIL_TOKEN_FIELDS = (
    "input_text_tokens",
    "input_image_tokens",
    "input_uncached_tokens",
    "input_cached_text_tokens",
    "input_cached_image_tokens",
    "input_cached_audio_tokens",
    "output_text_tokens",
    "output_image_tokens",
)

# Render order for known fields, primary first then detail. Any *_tokens field
# the API ships in the future that isn't in either tuple gets appended after
# these, sorted, so new dimensions (e.g. reasoning_tokens when OpenAI promotes
# them to the org endpoint) show up automatically without a code change.
KNOWN_TOKEN_FIELDS = PRIMARY_TOKEN_FIELDS + DETAIL_TOKEN_FIELDS

PRETTY_TOKEN_LABEL = {
    "input_tokens": "Input",
    "input_cached_tokens": "Cached input",
    "input_audio_tokens": "Input audio",
    "output_tokens": "Output",
    "output_audio_tokens": "Output audio",
    "input_text_tokens": "Input text",
    "input_image_tokens": "Input image",
    "input_uncached_tokens": "Input uncached",
    "input_cached_text_tokens": "Cached input text",
    "input_cached_image_tokens": "Cached input image",
    "input_cached_audio_tokens": "Cached input audio",
    "output_text_tokens": "Output text",
    "output_image_tokens": "Output image",
    "output_reasoning_tokens": "Reasoning",  # forward-compat naming
    "reasoning_tokens": "Reasoning",
}


# ---------------------------------------------------------------------------
# Time-window resolution
# ---------------------------------------------------------------------------

def resolve_window(window: str | None, since: str | None, until: str | None) -> tuple[int, int, str]:
    """Return (start_unix, end_unix, label)."""
    now = datetime.now(tz=timezone.utc)

    if since or until:
        if not (since and until):
            sys.exit("--since and --until must be provided together")
        start_dt = _parse_date(since)
        end_dt = _parse_date(until)
        label = f"{since} → {until} (UTC)"
    elif window == "today":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        label = f"today UTC ({start_dt.strftime('%Y-%m-%d')}, partial day)"
    elif window == "last-7d":
        end_dt = now
        start_dt = now - timedelta(days=7)
        label = "last 7 days"
    elif window == "last-30d":
        end_dt = now
        start_dt = now - timedelta(days=30)
        label = "last 30 days"
    elif window == "month-to-date":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        label = f"month to date ({start_dt.strftime('%B %Y')})"
    else:  # yesterday UTC (default)
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = today_midnight - timedelta(days=1)
        end_dt = today_midnight
        label = f"yesterday UTC ({start_dt.strftime('%Y-%m-%d')})"

    if start_dt >= end_dt:
        sys.exit(f"Empty window: start {start_dt.isoformat()} >= end {end_dt.isoformat()}")

    return int(start_dt.timestamp()), int(end_dt.timestamp()), label


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit(f"Bad date {s!r} — expected YYYY-MM-DD (UTC)")


# ---------------------------------------------------------------------------
# OpenAI API helpers
# ---------------------------------------------------------------------------

def _request(path: str, params: dict, api_key: str) -> dict:
    """GET with retry on 429 + 5xx."""
    url = f"{OPENAI_API}{path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    last_err = ""
    for attempt in range(6):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=60)
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(min(2 ** attempt, 30))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(wait, 30))
            last_err = f"{r.status_code}: {r.text[:200]}"
            continue
        if not r.ok:
            sys.exit(f"OpenAI {r.status_code} on {path}: {r.text[:300]}")
        return r.json()
    sys.exit(f"OpenAI {path}: gave up after retries — last error: {last_err}")


def fetch_projects(api_key: str) -> dict[str, dict]:
    """Map project_id → {name, status}."""
    projects: dict[str, dict] = {}
    after: str | None = None
    while True:
        params: dict = {"limit": 100, "include_archived": "true"}
        if after:
            params["after"] = after
        data = _request(PROJECTS_PATH, params, api_key)
        for p in data.get("data", []):
            projects[p["id"]] = {
                "name": p.get("name") or p["id"],
                "status": p.get("status", "unknown"),
            }
        if not data.get("has_more"):
            break
        last = data["data"][-1] if data.get("data") else None
        if not last:
            break
        after = last["id"]
    return projects


def fetch_usage(
    group_by: str,
    start_unix: int,
    end_unix: int,
    api_key: str,
    all_fields: bool = False,
) -> tuple[dict[str, dict[str, int]], dict[str, int], list[str]]:
    """Sum token counts across the window, grouped by `group_by`.

    Returns:
      totals          {bucket_key: {token_field: count}}
      num_requests    {bucket_key: int}             # num_model_requests sum
      token_fields    [token_field, ...]            # render order

    Field selection:
      - Always captures every `*_tokens` field the API returned (so JSON output
        carries the full set even when the table renders a subset).
      - `token_fields` returned to the caller is filtered to PRIMARY_TOKEN_FIELDS
        by default — the table stays scannable. With `all_fields=True` the
        list includes DETAIL fields plus any new fields OpenAI ships in the
        future (sorted, appended last) so new dimensions appear automatically.
    """
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    num_requests: dict[str, int] = defaultdict(int)
    seen_fields: set[str] = set()
    page: str | None = None
    while True:
        params: dict = {
            "start_time": start_unix,
            "end_time": end_unix,
            "bucket_width": "1d",
            "group_by": [group_by],
            "limit": USAGE_PAGE_SIZE,
        }
        if page:
            params["page"] = page
        data = _request(USAGE_COMPLETIONS_PATH, params, api_key)
        for bucket in data.get("data", []):
            for row in bucket.get("results", []):
                key = row.get(group_by) or "(none)"
                for fld, val in row.items():
                    if fld.endswith("_tokens") and isinstance(val, (int, float)):
                        totals[key][fld] += int(val or 0)
                        seen_fields.add(fld)
                num_requests[key] += int(row.get("num_model_requests", 0) or 0)
        page = data.get("next_page")
        if not page:
            break

    if all_fields:
        ordered: list[str] = [f for f in KNOWN_TOKEN_FIELDS if f in seen_fields]
        extras = sorted(f for f in seen_fields if f not in KNOWN_TOKEN_FIELDS)
        ordered.extend(extras)
    else:
        ordered = [f for f in PRIMARY_TOKEN_FIELDS if f in seen_fields]

    # Materialise the defaultdicts so JSON serialisation is plain dicts.
    flat = {k: dict(v) for k, v in totals.items()}
    return flat, dict(num_requests), ordered


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _label(field: str) -> str:
    return PRETTY_TOKEN_LABEL.get(field, field.replace("_tokens", "").replace("_", " ").title())


def _row_total(row: dict[str, int]) -> int:
    """Total tokens for a row.

    Sums `input_tokens` + `output_tokens` only — those are the API's two
    top-level totals. Cached input is a subset of input (already counted), and
    `input_text_tokens` / `input_image_tokens` / etc. are sub-decompositions of
    input/output (double-counting if summed). This keeps the Total column
    reconciled across `--all-fields` on and off.
    """
    return int(row.get("input_tokens", 0) or 0) + int(row.get("output_tokens", 0) or 0)


def render_table(
    title: str,
    totals: dict[str, dict[str, int]],
    num_requests: dict[str, int],
    token_fields: list[str],
    key_label: str,
    name_lookup: dict[str, str] | None = None,
) -> str:
    """One table — rows ranked by total tokens desc."""
    lines: list[str] = []
    lines.append(f"## {title}")
    lines.append("")

    if not totals:
        lines.append("_No usage recorded in this window._")
        return "\n".join(lines)

    headers = [key_label] + [_label(f) for f in token_fields] + ["Total tokens", "Requests"]
    aligns = ["---"] + ["---:"] * (len(token_fields) + 2)
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(aligns) + "|")

    ranked = sorted(totals.items(), key=lambda kv: _row_total(kv[1]), reverse=True)

    # Column totals for the footer row — anchors the table to the org-wide sum
    # so the reader can sanity-check that all groups add up.
    col_totals: dict[str, int] = defaultdict(int)
    total_requests = 0

    for key, row in ranked:
        display = (name_lookup or {}).get(key, key)
        per_field = [int(row.get(f, 0) or 0) for f in token_fields]
        # Row total = input + output only (see _row_total). Don't sum
        # `per_field` — that would double-count cached input (subset of input)
        # and any DETAIL columns (sub-decompositions of input/output).
        row_total = _row_total(row)
        req = int(num_requests.get(key, 0) or 0)
        for f, v in zip(token_fields, per_field):
            col_totals[f] += v
        col_totals["__row_total__"] += row_total
        total_requests += req
        cells = [display] + [f"{v:,}" for v in per_field] + [f"{row_total:,}", f"{req:,}"]
        lines.append("| " + " | ".join(cells) + " |")

    # Footer (org-wide total). Marked in bold so it's visually distinct.
    footer_cells = ["**Total**"]
    footer_cells += [f"**{col_totals[f]:,}**" for f in token_fields]
    footer_cells += [
        f"**{col_totals['__row_total__']:,}**",
        f"**{total_requests:,}**",
    ]
    lines.append("| " + " | ".join(footer_cells) + " |")

    return "\n".join(lines)


def render_report(
    label: str,
    by_model: dict[str, dict[str, int]],
    by_model_requests: dict[str, int],
    by_model_fields: list[str],
    by_project: dict[str, dict[str, int]],
    by_project_requests: dict[str, int],
    by_project_fields: list[str],
    projects: dict[str, dict],
    show_models: bool,
    show_projects: bool,
) -> str:
    lines: list[str] = []
    lines.append(f"# OpenAI Token Usage — {label}")
    lines.append("")

    # Headline: total tokens + total requests org-wide, picked from whichever
    # breakdown is present. Both should reconcile.
    primary = by_model if show_models and by_model else by_project
    total_tokens = sum(_row_total(row) for row in primary.values())
    total_requests = sum(
        int(v or 0)
        for v in (by_model_requests if show_models and by_model else by_project_requests).values()
    )
    if total_tokens == 0:
        lines.append("_No token usage recorded in this window._")
        return "\n".join(lines)

    lines.append(
        f"**Total:** {total_tokens:,} tokens across {total_requests:,} model request(s)"
    )
    lines.append("")
    lines.append(
        "_Source: `/v1/organization/usage/completions` (chat completions + responses)._ "
        "_Numbers may lag the OpenAI dashboard by up to ~24h on the most recent bucket._"
    )
    lines.append("")

    if show_models:
        lines.append(
            render_table(
                "By model",
                by_model,
                by_model_requests,
                by_model_fields,
                key_label="Model",
            )
        )
        lines.append("")
    if show_projects:
        name_lookup = {pid: meta.get("name", pid) for pid, meta in projects.items()}
        lines.append(
            render_table(
                "By project",
                by_project,
                by_project_requests,
                by_project_fields,
                key_label="Project",
                name_lookup=name_lookup,
            )
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def _slack_summary(
    label: str,
    by_model: dict[str, dict[str, int]],
    by_model_fields: list[str],
    by_project: dict[str, dict[str, int]],
    projects: dict[str, dict],
    top_n: int = 5,
) -> str:
    """Condensed text for `--post-to-slack`. Top N rows per table."""
    lines: list[str] = []
    total_tokens = sum(_row_total(row) for row in (by_model or by_project).values())
    lines.append(f"*OpenAI Token Usage — {label}*")
    lines.append(f"Total: {total_tokens:,} tokens")
    lines.append("")
    if by_model:
        lines.append(f"*Top {top_n} models*")
        ranked = sorted(by_model.items(), key=lambda kv: _row_total(kv[1]), reverse=True)[:top_n]
        for m, row in ranked:
            lines.append(f"• `{m}` — {_row_total(row):,} tokens")
        lines.append("")
    if by_project:
        lines.append(f"*Top {top_n} projects*")
        name_lookup = {pid: meta.get("name", pid) for pid, meta in projects.items()}
        ranked = sorted(by_project.items(), key=lambda kv: _row_total(kv[1]), reverse=True)[:top_n]
        for pid, row in ranked:
            lines.append(f"• {name_lookup.get(pid, pid)} — {_row_total(row):,} tokens")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Force UTF-8 stdout — arrows and bullets in the rendered Markdown otherwise
    # blow up on Windows consoles defaulting to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="OpenAI token usage report for the Aura org")
    ap.add_argument(
        "--window",
        choices=["yesterday", "today", "last-7d", "last-30d", "month-to-date"],
        default="yesterday",
    )
    ap.add_argument("--since", help="UTC start date (YYYY-MM-DD). Use with --until.")
    ap.add_argument("--until", help="UTC end date, exclusive (YYYY-MM-DD).")
    ap.add_argument(
        "--no-models",
        action="store_true",
        help="Skip the by-model breakdown.",
    )
    ap.add_argument(
        "--no-projects",
        action="store_true",
        help="Skip the by-project breakdown.",
    )
    ap.add_argument(
        "--all-fields",
        action="store_true",
        help="Surface every `*_tokens` field the API returned, including the "
             "sub-decompositions of input/output (text, image, audio, "
             "uncached, cached_text, cached_image, cached_audio). Default is "
             "the 5 primary fields only — table stays scannable.",
    )
    ap.add_argument(
        "--post-to-slack",
        action="store_true",
        help="After rendering, post a condensed top-N summary to "
             "#eng-budget (C05PC18APLH) via the Slack MCP. Prints the "
             "message to stdout when the MCP isn't wired into this process; "
             "the calling skill is responsible for the actual send.",
    )
    ap.add_argument(
        "--top",
        type=int,
        default=5,
        help="Top N rows in the Slack summary (default 5).",
    )
    ap.add_argument("--json", dest="json_out", help="Write structured JSON to this path.")
    args = ap.parse_args()

    if args.no_models and args.no_projects:
        sys.exit("Nothing to render: both --no-models and --no-projects given.")

    api_key = os.environ.get("OPENAI_ADMIN_API_KEY")
    if not api_key:
        sys.exit(
            "Missing OPENAI_ADMIN_API_KEY. Create an Admin API key at "
            "https://platform.openai.com/settings/organization/admin-keys "
            "and add it to ~/.claude/settings.json under the `env` block."
        )

    start_unix, end_unix, label = resolve_window(args.window, args.since, args.until)

    by_model: dict[str, dict[str, int]] = {}
    by_model_requests: dict[str, int] = {}
    by_model_fields: list[str] = []
    by_project: dict[str, dict[str, int]] = {}
    by_project_requests: dict[str, int] = {}
    by_project_fields: list[str] = []

    if not args.no_models:
        by_model, by_model_requests, by_model_fields = fetch_usage(
            "model", start_unix, end_unix, api_key, all_fields=args.all_fields,
        )

    projects: dict[str, dict] = {}
    if not args.no_projects:
        by_project, by_project_requests, by_project_fields = fetch_usage(
            "project_id", start_unix, end_unix, api_key, all_fields=args.all_fields,
        )
        projects = fetch_projects(api_key)

    report = render_report(
        label,
        by_model, by_model_requests, by_model_fields,
        by_project, by_project_requests, by_project_fields,
        projects,
        show_models=not args.no_models,
        show_projects=not args.no_projects,
    )
    print(report)

    if args.post_to_slack:
        # The Slack MCP runs in the agent harness, not here. Print the prepared
        # message so the calling skill can route it via slack_send_message.
        print()
        print("---")
        print("Slack summary (route to #eng-budget / C05PC18APLH):")
        print()
        print(_slack_summary(label, by_model, by_model_fields, by_project, projects, top_n=args.top))

    if args.json_out:
        payload = {
            "window": {
                "label": label,
                "start_unix": start_unix,
                "end_unix": end_unix,
                "start_iso": datetime.fromtimestamp(start_unix, tz=timezone.utc).isoformat(),
                "end_iso": datetime.fromtimestamp(end_unix, tz=timezone.utc).isoformat(),
            },
            "by_model": {
                "token_fields": by_model_fields,
                "rows": [
                    {
                        "model": m,
                        "tokens": dict(row),
                        "total_tokens": _row_total(row),
                        "num_requests": by_model_requests.get(m, 0),
                    }
                    for m, row in sorted(
                        by_model.items(),
                        key=lambda kv: _row_total(kv[1]),
                        reverse=True,
                    )
                ],
            } if not args.no_models else None,
            "by_project": {
                "token_fields": by_project_fields,
                "rows": [
                    {
                        "project_id": pid,
                        "project_name": (projects.get(pid) or {}).get("name", pid),
                        "status": (projects.get(pid) or {}).get("status", "unknown"),
                        "tokens": dict(row),
                        "total_tokens": _row_total(row),
                        "num_requests": by_project_requests.get(pid, 0),
                    }
                    for pid, row in sorted(
                        by_project.items(),
                        key=lambda kv: _row_total(kv[1]),
                        reverse=True,
                    )
                ],
            } if not args.no_projects else None,
        }
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)


if __name__ == "__main__":
    main()
