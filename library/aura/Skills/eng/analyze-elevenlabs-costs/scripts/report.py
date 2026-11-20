#!/usr/bin/env python3
"""
ElevenLabs spend report for the Aura workspace.

Pulls usage from `/v1/usage/character-stats` (metrics: `credits`,
`fiat_units_spent`), subscription from `/v1/user/subscription`, and renders a
markdown report of **actuals only** — no end-of-period projections:

  - **Credit consumption** per window (billing-period-to-date and last 7d by default),
    broken down by model / voice / api_key / etc. Direct from the API.
  - **Actual plan cost** (flat monthly fee from the subscription's `next_invoice`)
    + any live `current_overage.amount` from the subscription — both exact.
  - **Quota usage** so far (credits used / credits limit) as a fraction. No forward
    extrapolation: every number on the page reflects what has already happened.

The Usage API also returns `fiat_units_spent` — the consumption-value USD figure,
returned in CENTS. We divide by 100 (see run_window) so it reconciles within ~1%
of the ElevenLabs dashboard "Cost before overage surcharge" tile (the same value
eng:report-aurie-weekly-costs uses for its voice column). The rendered markdown
report still hides it to avoid it being read as the invoice — the actual bill is
flat plan + `current_overage`, not consumption value — but the corrected USD value
is in the `--json` payload (`cost_usd` / `total_cost_usd`) for tooling. (A prior
"$87k while owing $1,320" warning was the un-divided cents artifact: ÷100 it's
~$870, comfortably under the plan floor.)

Default `--combined` view shows BILLING-PERIOD-TO-DATE + LAST-7-DAYS side by side,
anchored to the workspace's actual subscription renewal date — not the calendar
month, which usually doesn't line up. Calendar MTD is still available via
`--window calendar-mtd` for cross-tool consistency with eng:analyze-openai-costs.

Usage:
    python report.py                                    # BPTD + last 7d (default)
    python report.py --combined                         # same as above (explicit)
    python report.py --window billing-period            # just BPTD
    python report.py --window calendar-mtd              # calendar month-to-date
    python report.py --window previous-month
    python report.py --window last-7d
    python report.py --window last-30d
    python report.py --since 2026-04-01 --until 2026-05-01
    python report.py --combined --breakdown product_type
    python report.py --combined --breakdown api_keys --json out.json

Env:
    ELEVENLABS_ADMIN_API_KEY  Preferred. Required scopes:
                                - Workspace Analytics: Access  (required)
                                - User: Read                   (required for the
                                  subscription / plan / leverage block — without
                                  it the script falls back to calendar MTD)
                                - Workspace: Read              (optional, only for
                                  --breakdown api_keys name lookup)
    ELEVENLABS_API_KEY        Fallback. Personal key — totals scoped to it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

# Pacific is the default time anchor across every ElevenLabs skill (matches
# the dashboard's viewer-TZ behaviour for the Pacific-based team and the
# report-aurie-weekly-costs voice column). See Tools/ElevenLabs § Timezone
# anchoring for the rationale.
PACIFIC = ZoneInfo("America/Los_Angeles")

ELEVENLABS_API = "https://api.elevenlabs.io/v1"
CHARACTER_STATS_PATH = "/usage/character-stats"
SUBSCRIPTION_PATH = "/user/subscription"
WORKSPACE_API_KEYS_PATH = "/workspace/api-keys"

# Window choices users can pass to --window.
WINDOW_CHOICES = [
    "billing-period",  # default — actual subscription cycle (BPTD)
    "calendar-mtd",    # cross-tool consistency with eng:analyze-openai-costs
    "previous-month",
    "last-7d",
    "last-30d",
]

BREAKDOWN_CHOICES = [
    "model",
    "voice",
    "api_keys",
    "user",
    "product_type",
    "source",
    "none",
]

# ---------------------------------------------------------------------------
# Time-window resolution
# ---------------------------------------------------------------------------


def resolve_window(
    window: str | None,
    since: str | None,
    until: str | None,
    subscription: dict | None = None,
) -> tuple[int, int, str]:
    """Return (start_unix_ms, end_unix_ms, label).

    ElevenLabs expects timestamps in milliseconds, unlike the OpenAI Costs API
    which uses seconds. We do the conversion here so the rest of the script can
    treat the values as opaque integers.

    **Time anchor.** Calendar-bounded windows (`calendar-mtd`, `previous-month`,
    `--since/--until`) and the daily-resolution buckets inside any window
    are anchored at **Pacific midnight** — same convention as
    `report-aurie-weekly-costs` and matching the ElevenLabs dashboard the
    Pacific-based team uses to compare numbers. Rolling windows
    (`last-7d`, `last-30d`) are TZ-agnostic (they walk back from `now`).
    The `billing-period` anchor follows ElevenLabs' own
    `next_character_count_reset_unix` and may differ — we don't second-guess
    it.

    `subscription` is the response from `/v1/user/subscription`. It's only
    needed to resolve `--window billing-period`; for other windows it can be
    None. If `billing-period` is requested without a subscription, we fall
    back to calendar MTD with a warning to stderr.
    """
    now_utc = datetime.now(tz=timezone.utc)
    now_pt = now_utc.astimezone(PACIFIC)

    if since or until:
        if not (since and until):
            sys.exit("--since and --until must be provided together")
        start_dt = _parse_date_pt(since)
        end_dt = _parse_date_pt(until)
        label = f"{since} → {until} (PT)"
    elif window == "billing-period":
        if subscription is None:
            print(
                "# ⚠️ Billing-period window requested but subscription unavailable "
                "(missing User: Read scope?). Falling back to calendar MTD.\n",
                file=sys.stderr,
            )
            start_dt = now_pt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_dt = now_utc
            label = f"calendar MTD ({start_dt.strftime('%B %Y')}, PT)"
        else:
            start_dt, end_dt = _resolve_billing_period(subscription, now_utc)
            label = (
                f"billing period ({start_dt.strftime('%b %-d')} → "
                f"{end_dt.strftime('%b %-d')}, renews "
                f"{datetime.fromtimestamp(subscription['next_character_count_reset_unix'], tz=timezone.utc).strftime('%b %-d')})"
            )
    elif window == "calendar-mtd":
        # 1st of current calendar month, Pacific midnight.
        start_dt = now_pt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now_utc
        label = f"calendar MTD ({start_dt.strftime('%B %Y')}, PT)"
    elif window == "previous-month":
        # Full prior Pacific calendar month.
        first_of_this = now_pt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_any_day = first_of_this - timedelta(days=1)
        start_dt = prev_month_any_day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = first_of_this
        label = f"previous month ({start_dt.strftime('%B %Y')}, PT)"
    elif window == "last-7d":
        end_dt = now_utc
        start_dt = now_utc - timedelta(days=7)
        label = "last 7 days"
    elif window == "last-30d":
        end_dt = now_utc
        start_dt = now_utc - timedelta(days=30)
        label = "last 30 days"
    else:
        sys.exit(f"Unknown window: {window!r}")

    if start_dt >= end_dt:
        sys.exit(f"Empty window: start {start_dt.isoformat()} >= end {end_dt.isoformat()}")

    return int(start_dt.timestamp() * 1000), int(end_dt.timestamp() * 1000), label


def _resolve_billing_period(subscription: dict, now: datetime) -> tuple[datetime, datetime]:
    """Derive the current billing period [start, end_capped_to_now) from a subscription.

    ElevenLabs reports `next_character_count_reset_unix` (when the period ends and
    quota refreshes). We step back one calendar month from that timestamp to get
    the period start, then cap the end at `now` so we report period-to-date —
    not the full closed period, which hasn't happened yet.

    Calendar-month subtraction handles month-end edge cases (e.g. renewal on
    March 31 → previous period started Feb 28/29).
    """
    reset_unix = subscription.get("next_character_count_reset_unix")
    if not reset_unix:
        sys.exit("Subscription missing next_character_count_reset_unix — cannot derive billing period")
    period_end_full = datetime.fromtimestamp(reset_unix, tz=timezone.utc)
    period_start = _one_calendar_month_prior(period_end_full)
    # Cap end at now so we get period-to-date, not the closed future period.
    period_end = min(period_end_full, now)
    return period_start, period_end


def _one_calendar_month_prior(dt: datetime) -> datetime:
    """Return the datetime exactly one calendar month before `dt`.

    Handles the case where the target month has fewer days than the source
    month (e.g. Mar 31 → Feb 28/29) by clamping to the last valid day.
    """
    if dt.month == 1:
        target_year, target_month = dt.year - 1, 12
    else:
        target_year, target_month = dt.year, dt.month - 1
    # Find the last valid day of the target month by going to day 1 of the
    # following month and stepping back one day — works for every month.
    if target_month == 12:
        next_month_first = datetime(target_year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month_first = datetime(target_year, target_month + 1, 1, tzinfo=timezone.utc)
    last_day_of_target = (next_month_first - timedelta(days=1)).day
    return dt.replace(year=target_year, month=target_month, day=min(dt.day, last_day_of_target))


def _parse_date_pt(s: str) -> datetime:
    """Parse `YYYY-MM-DD` as Pacific midnight (the team-default anchor).

    Returns an aware datetime so downstream `.timestamp()` math is correct
    across DST transitions. Same convention as report-aurie-weekly-costs.
    """
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=PACIFIC)
    except ValueError:
        sys.exit(f"Bad date {s!r} — expected YYYY-MM-DD (interpreted as Pacific midnight)")


# ---------------------------------------------------------------------------
# ElevenLabs API helpers
# ---------------------------------------------------------------------------


def _request(path: str, params: dict, api_key: str, *, ok_404: bool = False) -> dict | None:
    """GET with retry on 429. Returns None on 404 if ok_404 is set."""
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
            sys.exit(f"ElevenLabs {r.status_code} on {path}: {r.text[:300]}")
        return r.json()
    sys.exit(f"ElevenLabs {path}: rate-limited after 5 retries")


def fetch_stats(
    start_ms: int,
    end_ms: int,
    breakdown_type: str,
    metric: str,
    api_key: str,
) -> dict:
    """Fetch usage stats for the given window and breakdown.

    `metric` is one of `credits` (character/credit consumption) or
    `fiat_units_spent` (USD-denominated retail-equivalent — kept in JSON,
    hidden from markdown). We always pass `aggregation_interval=day` so the
    caller can sum per-bucket values and handle partial-day windows correctly.
    `include_workspace_metrics=true` widens the response to all workspace
    members when the key has admin scope; on a personal key it's silently
    ignored.
    """
    params = {
        "start_unix": start_ms,
        "end_unix": end_ms,
        "breakdown_type": breakdown_type,
        "metric": metric,
        "aggregation_interval": "day",
        "include_workspace_metrics": "true",
    }
    data = _request(CHARACTER_STATS_PATH, params, api_key)
    if not isinstance(data, dict):
        return {"time": [], "usage": {}}
    return data


def fetch_subscription(api_key: str) -> dict | None:
    """Best-effort fetch of the workspace subscription.

    Returns None (with a stderr note) when the key lacks `User: Read` scope —
    callers should fall back to calendar MTD instead of erroring out. We
    deliberately don't make this fatal because the rest of the report still
    works without it; only the plan/leverage block is skipped.
    """
    url = f"{ELEVENLABS_API}{SUBSCRIPTION_PATH}"
    headers = {"xi-api-key": api_key}
    r = requests.get(url, headers=headers, timeout=60)
    if r.status_code in (401, 403):
        print(
            "# ⚠️ /user/subscription returned "
            f"{r.status_code} — key likely lacks 'User: Read' scope. "
            "Skipping plan/leverage block.\n",
            file=sys.stderr,
        )
        return None
    if not r.ok:
        # Non-auth failures should surface — they probably indicate an API change.
        sys.exit(f"ElevenLabs {r.status_code} on {SUBSCRIPTION_PATH}: {r.text[:300]}")
    return r.json()


def fetch_workspace_api_keys(api_key: str) -> dict[str, str]:
    """Best-effort id → name map for workspace API keys.

    Returns {} if the endpoint isn't reachable with this key. Common causes:
      - 401/403: scope missing (Workspace: Read or higher). The error message
        ElevenLabs returns here is misleading ("no auth header") — what they
        actually mean is "your auth lacks permission for this endpoint".
      - 404: endpoint not provisioned on this plan / org.

    We don't fail the run — the report falls back to displaying raw key IDs.
    """
    url = f"{ELEVENLABS_API}{WORKSPACE_API_KEYS_PATH}"
    headers = {"xi-api-key": api_key}
    r = requests.get(url, headers=headers, timeout=60)
    if r.status_code in (401, 403, 404):
        print(
            f"# ⚠️ /workspace/api-keys returned {r.status_code} — key likely lacks "
            "the right workspace scope. Showing raw key IDs in the breakdown.\n",
            file=sys.stderr,
        )
        return {}
    if not r.ok:
        # Any other non-success — surface so we notice API drift.
        sys.exit(f"ElevenLabs {r.status_code} on {WORKSPACE_API_KEYS_PATH}: {r.text[:300]}")
    data = r.json()
    # Response shape: {"api-keys": [{"api_key_id": "...", "name": "..."}, ...]}
    # ElevenLabs has been inconsistent about the wrapper key — also accept "api_keys".
    items = data.get("api-keys") or data.get("api_keys") or []
    out: dict[str, str] = {}
    for item in items:
        kid = item.get("api_key_id") or item.get("id")
        name = item.get("name")
        if kid and name:
            out[kid] = name
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def sum_by_bucket(stats: dict) -> dict[str, float]:
    """Collapse the per-day arrays returned by the Usage API into totals per bucket.

    ElevenLabs returns: {"time": [t0, t1, ...], "usage": {"<bucket>": [v0, v1, ...]}}.
    We don't care about the daily resolution here — the SKILL.md reports totals
    per window. Daily data is preserved in the raw `--json` output for any caller
    that wants a run-rate chart later.
    """
    out: dict[str, float] = {}
    usage = stats.get("usage") or {}
    for bucket_name, series in usage.items():
        if not isinstance(series, list):
            continue
        total = 0.0
        for v in series:
            if v is None:
                continue
            try:
                total += float(v)
            except (TypeError, ValueError):
                continue
        out[bucket_name] = total
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_window(
    label: str,
    cost_by_bucket: dict[str, float],
    credit_by_bucket: dict[str, float],
    *,
    breakdown_type: str,
    api_key_names: dict[str, str],
    no_zero: bool,
    days_in_window: float,
) -> tuple[str, float, float]:
    """Render one window's section. Returns (markdown, total_cost, total_credits).

    The rendered markdown intentionally shows credits only — the
    `fiat_units_spent` figure is what those credits would cost at retail rates,
    NOT what we owe ElevenLabs, and surfacing it has historically been read as
    an actual bill. Real billing lives in the subscription block (plan cost +
    overage). `total_cost` is still returned for callers that need the raw
    value (JSON output, internal calculations).
    """
    total_cost = sum(cost_by_bucket.values())
    total_credits = sum(credit_by_bucket.values())

    lines: list[str] = []
    lines.append(f"## {label}")
    lines.append("")

    if total_credits == 0:
        lines.append("_No usage recorded in this window._")
        return "\n".join(lines), 0.0, 0.0

    daily_credits = total_credits / days_in_window if days_in_window > 0 else 0.0
    summary_bits = [
        f"**Credits:** {int(total_credits):,}",
        f"**~Daily:** {_fmt_credits_compact(daily_credits)}/day across {days_in_window:.1f} days",
    ]
    lines.append(" · ".join(summary_bits))

    if breakdown_type != "none":
        lines.append("")
        breakdown_label = _breakdown_header(breakdown_type)
        lines.append(f"| {breakdown_label} | Credits | Share |")
        lines.append("|---|---:|---:|")

        all_keys = set(cost_by_bucket) | set(credit_by_bucket)
        rows = sorted(
            all_keys,
            key=lambda k: credit_by_bucket.get(k, 0.0),
            reverse=True,
        )
        for key in rows:
            credits = credit_by_bucket.get(key, 0.0)
            if no_zero and credits == 0:
                continue
            label_str = _humanise_bucket(key, breakdown_type, api_key_names)
            pct = (credits / total_credits * 100) if total_credits else 0.0
            lines.append(
                f"| {label_str} | {int(credits):,} | {pct:.1f}% |"
            )

    return "\n".join(lines), total_cost, total_credits


def _breakdown_header(breakdown_type: str) -> str:
    return {
        "model": "Model",
        "voice": "Voice",
        "api_keys": "API key",
        "user": "User",
        "product_type": "Product",
        "source": "Source",
    }.get(breakdown_type, breakdown_type.title())


def _humanise_bucket(key: str, breakdown_type: str, api_key_names: dict[str, str]) -> str:
    if breakdown_type == "api_keys":
        # ElevenLabs returns the raw API key ID — replace with the human name when
        # we have it, fall back to the ID otherwise (see fetch_workspace_api_keys).
        return api_key_names.get(key, key)
    if key == "All":
        return "All usage"
    return key


def _fmt_money(amount: float) -> str:
    return f"${amount:,.2f}"


def _fmt_credits_compact(n: float) -> str:
    """Compact credits formatter: 1_234_567 → '1.23M', 12_345 → '12.3k', 999 → '999'."""
    n = float(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:,.0f}"


def _days_between(start_ms: int, end_ms: int) -> float:
    return max((end_ms - start_ms) / 1000.0 / 86400.0, 0.0)


# ---------------------------------------------------------------------------
# Window pipeline
# ---------------------------------------------------------------------------


def run_window(
    window_label: str,
    start_ms: int,
    end_ms: int,
    *,
    breakdown_type: str,
    api_key: str,
    api_key_names: dict[str, str],
    no_zero: bool,
) -> dict:
    """Fetch + aggregate + render one window. Returns a payload dict."""
    # `fiat_units_spent` is the API's name for the consumption-value USD figure
    # — kept in the payload (and JSON output) for tooling, but not rendered in
    # the markdown report; see render_window for rationale.
    cost_stats = fetch_stats(start_ms, end_ms, breakdown_type, "fiat_units_spent", api_key)
    credit_stats = fetch_stats(start_ms, end_ms, breakdown_type, "credits", api_key)

    cost_by_bucket = sum_by_bucket(cost_stats)
    credit_by_bucket = sum_by_bucket(credit_stats)

    # IMPORTANT: the ElevenLabs Usage API returns `fiat_units_spent` in CENTS.
    # Divide by 100 so every dollar field we emit (total_cost_usd, daily_cost_usd,
    # by_bucket[].cost_usd) is real USD. This matches eng:report-aurie-weekly-costs,
    # which also divides by 100 and reconciles within ~1% of the ElevenLabs
    # dashboard "Cost before overage surcharge" tile. Before this fix the payload
    # reported cents-as-dollars (100x too high). The `raw` field below still holds
    # the unmodified API response (cents) — consumers reading `raw` must /100.
    cost_by_bucket = {key: value / 100.0 for key, value in cost_by_bucket.items()}

    days = _days_between(start_ms, end_ms)
    markdown, total_cost, total_credits = render_window(
        window_label,
        cost_by_bucket,
        credit_by_bucket,
        breakdown_type=breakdown_type,
        api_key_names=api_key_names,
        no_zero=no_zero,
        days_in_window=days,
    )

    return {
        "label": window_label,
        "start_unix_ms": start_ms,
        "end_unix_ms": end_ms,
        "start_iso": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
        "end_iso": datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).isoformat(),
        "days": days,
        "total_cost_usd": total_cost,
        "total_credits": total_credits,
        "daily_cost_usd": (total_cost / days) if days > 0 else 0.0,
        "by_bucket": [
            {
                "key": key,
                "label": _humanise_bucket(key, breakdown_type, api_key_names),
                "cost_usd": cost_by_bucket.get(key, 0.0),
                "credits": credit_by_bucket.get(key, 0.0),
            }
            for key in sorted(
                set(cost_by_bucket) | set(credit_by_bucket),
                key=lambda k: (cost_by_bucket.get(k, 0.0), credit_by_bucket.get(k, 0.0)),
                reverse=True,
            )
        ],
        "raw": {"cost": cost_stats, "credits": credit_stats},
        "markdown": markdown,
    }


# ---------------------------------------------------------------------------
# Top-level rendering
# ---------------------------------------------------------------------------


def render_subscription_block(subscription: dict) -> str:
    """Render the plan / quota section that goes above the windows.

    Pure actuals: plan tier + monthly fee, renewal date, live
    `current_overage.amount` from the subscription endpoint (only non-zero
    when we're already over quota — at which point the number IS exact),
    and quota usage so far (credits used / credits limit, fractional).

    No projection — see the SKILL.md "Why actuals only" section.
    """
    lines: list[str] = ["## Subscription"]
    lines.append("")

    tier = subscription.get("tier") or "(unknown tier)"
    invoice = subscription.get("next_invoice") or {}
    plan_monthly_usd = (invoice.get("amount_due_cents") or 0) / 100.0
    reset_unix = subscription.get("next_character_count_reset_unix")
    chars_used = float(subscription.get("character_count") or 0)
    chars_limit = float(subscription.get("character_limit") or 0)
    overage = (subscription.get("current_overage") or {}).get("amount") or "0"
    try:
        overage_usd = float(overage)
    except (TypeError, ValueError):
        overage_usd = 0.0

    # Plan line — tier code from API can be a versioned SKU like
    # "scale_2024_08_10"; the actual UI name is whatever ElevenLabs has chosen
    # to show on the pricing page (it may differ). Surface the raw SKU so the
    # mapping is auditable; pair with the dollar amount which is unambiguous.
    plan_bits = [f"**Plan:** `{tier}` ({_fmt_money(plan_monthly_usd)}/mo)"]
    if reset_unix:
        renews = datetime.fromtimestamp(reset_unix, tz=timezone.utc).strftime("%b %-d, %Y")
        plan_bits.append(f"renews **{renews}**")
    if overage_usd > 0:
        plan_bits.append(f"⚠️ current overage: {_fmt_money(overage_usd)}")
    lines.append(" · ".join(plan_bits))

    # Quota line — what fraction of the period's credit allotment is consumed.
    # Just the actual used/limit ratio; no extrapolation.
    if chars_limit > 0:
        used_pct = chars_used / chars_limit * 100
        lines.append(
            f"**Quota:** {int(chars_used):,} / {int(chars_limit):,} credits "
            f"({used_pct:.1f}% used)"
        )

    return "\n".join(lines)


def render_report(
    payloads: list[dict],
    *,
    breakdown_type: str,
    subscription: dict | None = None,
) -> str:
    """Render the full report — pure actuals, no end-of-period projection."""
    lines: list[str] = []
    if len(payloads) > 1:
        lines.append("# ElevenLabs Spend — combined view")
    else:
        lines.append(f"# ElevenLabs Spend — {payloads[0]['label']}")
    lines.append("")

    # Cross-window headline whenever we have two windows in one report.
    if len(payloads) == 2:
        primary, wk = payloads[0], payloads[1]
        primary_daily_credits = (
            primary["total_credits"] / primary["days"] if primary["days"] > 0 else 0.0
        )
        wk_daily_credits = wk["total_credits"] / wk["days"] if wk["days"] > 0 else 0.0
        ratio_pct = (
            (wk_daily_credits / primary_daily_credits * 100)
            if primary_daily_credits > 0 else 0.0
        )
        direction = (
            "accelerating" if ratio_pct >= 115
            else "slowing" if ratio_pct and ratio_pct <= 85
            else "on pace"
        )
        primary_short = primary["label"].split(" (")[0]  # strip parenthetical dates
        if primary_daily_credits > 0:
            lines.append(
                f"**Headline:** {_fmt_credits_compact(primary['total_credits'])} credits "
                f"{primary_short} ({_fmt_credits_compact(primary_daily_credits)}/day) · "
                f"{_fmt_credits_compact(wk['total_credits'])} credits in last 7d "
                f"({_fmt_credits_compact(wk_daily_credits)}/day) · "
                f"weekly daily-avg is **{ratio_pct:.0f}%** of {primary_short} — _{direction}_."
            )
        else:
            lines.append(
                f"**Headline:** {_fmt_credits_compact(primary['total_credits'])} credits "
                f"{primary_short} · {_fmt_credits_compact(wk['total_credits'])} credits in last 7d."
            )
        lines.append("")

    if subscription is not None:
        lines.append(render_subscription_block(subscription))
        lines.append("")

    for payload in payloads:
        lines.append(payload["markdown"])
        lines.append("")

    if breakdown_type != "none":
        lines.append(
            f"_Breakdown: `{breakdown_type}`. "
            f"Switch via `--breakdown {{model,voice,api_keys,user,product_type,source,none}}`._"
        )
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="ElevenLabs spend report for the Aura workspace")
    ap.add_argument(
        "--window",
        choices=WINDOW_CHOICES,
        default="billing-period",
        help="Time window. Default `billing-period` = workspace's actual subscription "
             "cycle (auto-derived from /v1/user/subscription). Use `calendar-mtd` for "
             "consistency with eng:analyze-openai-costs.",
    )
    ap.add_argument(
        "--combined",
        action="store_true",
        help="Run billing-period-to-date AND last 7 days, render side by side. "
             "Default skill invocation.",
    )
    ap.add_argument("--since", help="UTC start date (YYYY-MM-DD). Use with --until.")
    ap.add_argument("--until", help="UTC end date, exclusive (YYYY-MM-DD).")
    ap.add_argument(
        "--breakdown",
        choices=BREAKDOWN_CHOICES,
        default="model",
        help="Secondary group-by axis. `none` skips the breakdown table.",
    )
    ap.add_argument(
        "--no-zero",
        action="store_true",
        help="Suppress rows with 0 credits in this window.",
    )
    ap.add_argument("--json", dest="json_out", help="Write structured JSON to this path.")
    args = ap.parse_args()

    # Prefer the admin key. Fall back to the per-agent key the conversations
    # skill uses, but warn — totals will be scoped to that key.
    api_key = os.environ.get("ELEVENLABS_ADMIN_API_KEY")
    using_fallback = False
    if not api_key:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        using_fallback = bool(api_key)
    if not api_key:
        sys.exit(
            "Missing ELEVENLABS_ADMIN_API_KEY (preferred) or ELEVENLABS_API_KEY. "
            "Create a workspace admin key at "
            "https://elevenlabs.io/app/workspace/api-keys and add it to "
            "~/.claude/settings.json under the `env` block."
        )
    if using_fallback:
        print(
            "# ⚠️ Using ELEVENLABS_API_KEY (personal/per-agent scope). "
            "Workspace totals may under-count — set ELEVENLABS_ADMIN_API_KEY for a "
            "full picture.\n",
            file=sys.stderr,
        )

    # Pre-fetch the subscription once. Powers the plan/leverage block AND
    # `--window billing-period` resolution. Best-effort: if the key lacks
    # User: Read scope we just skip the subscription block.
    subscription = fetch_subscription(api_key)

    # Pre-fetch the workspace API key name map once. Re-used by every window.
    api_key_names = (
        fetch_workspace_api_keys(api_key) if args.breakdown == "api_keys" else {}
    )

    # `--combined` is a preset; mixing it with explicit overrides is a user error.
    # We compare against the default to detect "user passed something else".
    if args.combined and (args.since or args.until or args.window != "billing-period"):
        sys.exit("--combined cannot be combined with --window/--since/--until")

    # Decide which window(s) to run.
    windows: list[tuple[str, int, int]] = []
    if args.combined:
        # Default skill invocation: billing-period-to-date + last 7d. Falls back
        # to calendar MTD if subscription is unavailable (resolve_window logs).
        primary_window = "billing-period" if subscription else "calendar-mtd"
        p_start, p_end, p_label = resolve_window(primary_window, None, None, subscription)
        wk_start, wk_end, wk_label = resolve_window("last-7d", None, None, subscription)
        windows.append((p_label, p_start, p_end))
        windows.append((wk_label, wk_start, wk_end))
    else:
        start_ms, end_ms, label = resolve_window(args.window, args.since, args.until, subscription)
        windows.append((label, start_ms, end_ms))

    payloads = [
        run_window(
            label,
            start_ms,
            end_ms,
            breakdown_type=args.breakdown,
            api_key=api_key,
            api_key_names=api_key_names,
            no_zero=args.no_zero,
        )
        for label, start_ms, end_ms in windows
    ]

    report = render_report(
        payloads,
        breakdown_type=args.breakdown,
        subscription=subscription,
    )
    print(report)

    if args.json_out:
        out = {
            "breakdown_type": args.breakdown,
            "currency": "usd",
            "subscription": subscription,
            "windows": [
                {k: v for k, v in p.items() if k != "markdown"}
                for p in payloads
            ],
        }
        with open(args.json_out, "w") as f:
            json.dump(out, f, indent=2, default=str)


if __name__ == "__main__":
    main()
