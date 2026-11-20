#!/usr/bin/env python3
"""
One-pane consolidated daily ElevenLabs cost snapshot for the Aura workspace.

Six sections in a single screen:

  1. Day headline — credits + actual plan cost + live overage + 7d delta
  2. Subscription / quota — plan tier, monthly cost, renewal, quota used + pace
  3. By model — credit consumption split across models
  4. By product type — Conversational AI vs TTS vs other
  5. Top 5 users (ConvAI) — minutes + $ (duration × rate) + subscription state
  6. Optional Slack payload via --post-to-slack

ElevenLabs bills on a flat-plan-plus-overage model — there's no metered daily
$ for the whole workspace. The headline therefore leads with CREDITS (what
ElevenLabs meters) and surfaces the actual plan cost + live overage. The only
$ figures derived from usage are the per-user top-5 amounts, computed as
`duration_secs × $0.09/min in-plan, $0.19/min burst` — actuals only, no
credits→minutes derivation.

Subscription-state annotation on the top 5 is fetched from Aura PaymentService
(`POST /subscriptions/list/:userId`, AURA_API_TOKEN). Graceful degrade when
that token is unset.

Usage:
    python report.py                                  # yesterday PT (default)
    python report.py --window today                   # partial day, flags incompleteness
    python report.py --window last-7d
    python report.py --since 2026-05-15 --until 2026-05-17
    python report.py --no-top-users                   # skip the slow conversation walk
    python report.py --post-to-slack                  # emit Slack payload
    python report.py --json out.json

Env:
    ELEVENLABS_ADMIN_API_KEY  Preferred. Scopes: Workspace Analytics + User
                              Read + ElevenAgents Read.
    ELEVENLABS_API_KEY        Fallback. Personal key — totals scoped to it.
    AURA_API_TOKEN            For per-user state annotation. Optional;
                              top-5 still renders without it.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import pathlib
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


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


try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip3 install requests")


ELEVENLABS_API = "https://api.elevenlabs.io/v1"
CHARACTER_STATS_PATH = "/usage/character-stats"
SUBSCRIPTION_PATH = "/user/subscription"
CONVERSATIONS_LIST_PATH = "/convai/conversations"
CONVERSATIONS_DETAIL_PATH = "/convai/conversations/{conversation_id}"

AURA_API = "https://services.aurahealth.io"
AURA_SUBSCRIPTIONS_PATH = "/subscriptions/list/{user_id}"

PACIFIC = ZoneInfo("America/Los_Angeles")

# Cost rates from Tools/ElevenLabs (Business plan).
COST_PER_MIN_IN_PLAN = 0.09
COST_PER_MIN_BURST = 0.19

# Workspace retention (ElevenLabs Privacy panel; bumped 5 → 10 days 2026-05-14).
RETENTION_DAYS = 10

# UID redaction per Compliance/Privacy.
UID_DISPLAY_PREFIX = 8

DEFAULT_TOP_N = 5
ANOMALY_THRESHOLD = 0.20


# ---------------------------------------------------------------------------
# Time-window resolution
# ---------------------------------------------------------------------------

def resolve_window(window: str | None, since: str | None, until: str | None) -> tuple[int, int, str, bool]:
    """Return (start_unix_s, end_unix_s, label, is_partial)."""
    now_utc = datetime.now(tz=timezone.utc)
    now_pt = now_utc.astimezone(PACIFIC)

    if since or until:
        if not (since and until):
            sys.exit("--since and --until must be used together")
        start_dt = _parse_date_pt(since)
        end_dt = _parse_date_pt(until)
        if end_dt <= start_dt:
            sys.exit("--until must be after --since")
        label = f"{since} → {until} (PT)"
        return int(start_dt.timestamp()), int(end_dt.timestamp()), label, False

    w = (window or "yesterday").lower()
    if w == "today":
        start_dt = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now_utc
        return int(start_dt.timestamp()), int(end_dt.timestamp()), f"Today PT ({start_dt.strftime('%b %-d')}, partial)", True
    if w in ("yesterday", ""):
        end_dt = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = end_dt - timedelta(days=1)
        return int(start_dt.timestamp()), int(end_dt.timestamp()), f"Yesterday PT ({start_dt.strftime('%b %-d')})", False
    if w in ("last-7d", "7d"):
        end_dt = now_utc
        start_dt = end_dt - timedelta(days=7)
        return int(start_dt.timestamp()), int(end_dt.timestamp()), "Last 7 days", False
    if w in ("last-24h", "24h"):
        end_dt = now_utc
        start_dt = end_dt - timedelta(hours=24)
        return int(start_dt.timestamp()), int(end_dt.timestamp()), "Last 24 hours", False

    sys.exit(f"Unknown --window {window!r}. Use yesterday | today | last-24h | last-7d, or --since/--until.")


def _parse_date_pt(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=PACIFIC)
    except ValueError:
        sys.exit(f"Bad date {s!r} — expected YYYY-MM-DD (Pacific midnight)")


# ---------------------------------------------------------------------------
# ElevenLabs API
# ---------------------------------------------------------------------------

def _elabs_get(path: str, params: dict, api_key: str, *, ok_404: bool = False) -> dict | None:
    """GET with retry on 429. Returns None on 404 if ok_404."""
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


def fetch_character_stats(start_unix: int, end_unix: int, breakdown_type: str, api_key: str) -> dict:
    """Fetch /v1/usage/character-stats for a window with a breakdown.

    Uses hourly aggregation so PT-aligned windows can be summed precisely
    without UTC-day-bucket misalignment. ElevenLabs character-stats expects
    MILLISECONDS for start/end_unix.
    """
    params = {
        "start_unix": start_unix * 1000,
        "end_unix": end_unix * 1000,
        "breakdown_type": breakdown_type,
        "metric": "credits",
        "aggregation_interval": "hour",
        "include_workspace_metrics": "true",
    }
    data = _elabs_get(CHARACTER_STATS_PATH, params, api_key) or {}
    return data if isinstance(data, dict) else {"time": [], "usage": {}}


def fetch_subscription(api_key: str) -> dict | None:
    """Best-effort fetch of the workspace subscription. None on 401/403."""
    url = f"{ELEVENLABS_API}{SUBSCRIPTION_PATH}"
    headers = {"xi-api-key": api_key}
    r = requests.get(url, headers=headers, timeout=60)
    if r.status_code in (401, 403):
        print(
            f"⚠️ /user/subscription returned {r.status_code} — key likely lacks 'User: Read' scope. Subscription block skipped.",
            file=sys.stderr,
        )
        return None
    if not r.ok:
        sys.exit(f"ElevenLabs {r.status_code} on {SUBSCRIPTION_PATH}: {r.text[:300]}")
    return r.json()


def list_conversations(start_unix: int, end_unix: int, api_key: str):
    cursor: str | None = None
    while True:
        params: dict = {
            "page_size": 100,
            "call_start_after_unix": start_unix,
            "call_start_before_unix": end_unix,
        }
        if cursor:
            params["cursor"] = cursor
        page = _elabs_get(CONVERSATIONS_LIST_PATH, params, api_key) or {}
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


def fetch_conversation_detail(conversation_id: str, api_key: str) -> dict | None:
    return _elabs_get(
        CONVERSATIONS_DETAIL_PATH.format(conversation_id=conversation_id),
        {},
        api_key,
        ok_404=True,
    )


def extract_attribution(detail: dict) -> dict | None:
    """Pull duration + user_id + is_burst from a conversation detail."""
    if not isinstance(detail, dict):
        return None
    cicd = detail.get("conversation_initiation_client_data") or {}
    dyn = cicd.get("dynamic_variables") or {}
    user_id = detail.get("user_id") or dyn.get("userId")
    if not user_id:
        return None
    md = detail.get("metadata") or {}
    charging = md.get("charging") or {}
    return {
        "user_id": user_id,
        "duration_secs": float(md.get("call_duration_secs") or 0),
        "is_burst": bool(charging.get("is_burst")),
    }


def duration_to_dollars(duration_secs: float, *, is_burst: bool) -> float:
    minutes = duration_secs / 60.0
    rate = COST_PER_MIN_BURST if is_burst else COST_PER_MIN_IN_PLAN
    return minutes * rate


# ---------------------------------------------------------------------------
# Aura PaymentService — subscription state per user
# ---------------------------------------------------------------------------

def fetch_subscription_rows(user_id: str, aura_token: str) -> list[dict] | None:
    """Fetch ALL subscription rows for a user (including soft-deleted).

    Passes `includeDeleted=true` so refunds and revocations are surfaced
    alongside active, cancelled, and expired rows. The full history is what
    `derive_state` and `derive_timeline` need for accurate annotation.
    Returns None on transport / non-200 failure.
    """
    url = f"{AURA_API}{AURA_SUBSCRIPTIONS_PATH.format(user_id=user_id)}"
    headers = {"Authorization": f"Bearer {aura_token}", "User-Agent": user_agent(), "Content-Type": "application/json"}
    try:
        r = requests.post(url, headers=headers, json={"includeDeleted": True}, timeout=30)
    except requests.RequestException:
        return None
    if not r.ok:
        return None
    rows = r.json()
    return rows if isinstance(rows, list) else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _is_active(row: dict, now: datetime) -> bool:
    """Row is currently active if lifetime OR (not deleted AND not expired)."""
    if row.get("deletedAt"):
        return False
    if row.get("lifetime"):
        return True
    exp = _parse_dt(row.get("expirationAt"))
    return bool(exp and exp > now)


# Window for "deleted ≈ expired" heuristic. Tuned for the observed natural-
# expiration flow (seconds-scale gap between expirationAt and deletedAt as the
# system auto-revokes after trial end). 5 minutes is generous against clock
# skew and cron-batch deletion delays. Verified 2026-05-17 against a real
# user (`KKBjkNBB…`): expiration → deletion gap was 27 seconds.
DELETION_NEAR_EXPIRY_SECONDS = 300


def _classify_deletion(row: dict) -> str | None:
    """Disambiguate the cause of a `deletedAt` row.

    Returns one of:
      - "trial_expired" — natural end of trial after autoRenew was disabled.
        Signal: `trial=true`, `autoRenew=false`, `deletedAt` within 5 min of
        `expirationAt`. This is the standard end-of-trial flow Apple/Google/Aura
        runs to auto-clean the row after a trial naturally ages out.
      - "refunded" — `deletedAt` materially before `expirationAt`. Implies
        Apple/Google/Aura processed a refund before the period would have ended.
        (Also catches mid-period chargebacks and refund-then-keep-using.)
      - "revoked" — everything else (admin/CS action, payment-failure cleanup,
        edge cases where `expirationAt` isn't set). Rare fallback.
      - `None` if the row isn't actually deleted.

    Discovery: top-spender drill-down on 2026-05-17 surfaced that the prior
    "any deletedAt → refunded/revoked" lumping miscategorised standard
    end-of-trial users as refunds. See ENG-540.
    """
    deleted = _parse_dt(row.get("deletedAt"))
    if not deleted:
        return None
    expires = _parse_dt(row.get("expirationAt"))
    trial = bool(row.get("trial"))
    auto_renew_off = row.get("autoRenew") is False
    # Natural trial expiration: trial + autoRenew off + deletion within window of expiration
    if trial and auto_renew_off and expires and abs((deleted - expires).total_seconds()) < DELETION_NEAR_EXPIRY_SECONDS:
        return "trial_expired"
    # Refund: deletion materially before expiration
    if expires and (expires - deleted).total_seconds() > DELETION_NEAR_EXPIRY_SECONDS:
        return "refunded"
    # Everything else: admin revoke, payment-failure cleanup, weird edge cases
    return "revoked"


def derive_state(rows: list[dict]) -> str:
    """Derive a single short state label from the rows. Prefers AI dimension.

    For the deeper per-row history (sorted by createdAt with status per row),
    use `derive_timeline`. The single-label flavour here is what the rendered
    top-5 table shows; the full timeline is surfaced in `--json` output.

    The deletion-state branch uses `_classify_deletion` to disambiguate
    natural trial-expiration from refund from revoke — see ENG-540 for the
    cohort-analysis rationale (don't conflate "trial ended naturally" with
    "user refunded the period and may still be using").
    """
    if not rows:
        return "Never premium"
    now = datetime.now(tz=timezone.utc)
    ai_rows = [r for r in rows if r.get("type") == "ai"]
    content_rows = [r for r in rows if r.get("type") == "content"]

    # AI dimension wins (report is about Aurie ConvAI usage)
    if ai_rows:
        active_ai = [r for r in ai_rows if _is_active(r, now)]
        if active_ai:
            # Prefer the most-recently-purchased active row
            row = max(active_ai, key=lambda r: _parse_dt(r.get("purchaseAt")) or datetime.min.replace(tzinfo=timezone.utc))
            if row.get("lifetime"):
                return "AI Lifetime"
            if row.get("trial"):
                if row.get("autoRenew") is False:
                    return "AI Trial (cancelled)"
                return "AI Trial"
            if row.get("autoRenew") is False:
                return "AI Cancelled (active)"
            return "AI Paid"
        # No active AI rows but history exists — classify the most recent deletion
        deleted_ai = [r for r in ai_rows if r.get("deletedAt")]
        if deleted_ai:
            # Most-recently-deleted row wins (latest signal)
            most_recent = max(deleted_ai, key=lambda r: _parse_dt(r.get("deletedAt")) or datetime.min.replace(tzinfo=timezone.utc))
            cause = _classify_deletion(most_recent)
            if cause == "trial_expired":
                return "AI Trial Expired"
            if cause == "refunded":
                return "AI Refunded"
            return "AI Revoked"
        return "AI Expired"
    # No AI rows at all — content only?
    if content_rows:
        active_content = [r for r in content_rows if _is_active(r, now)]
        if active_content:
            return "Content-only (Aura)"
        return "Content Expired (Aura)"
    return "Never premium"


def derive_timeline(rows: list[dict]) -> list[dict]:
    """Build a per-user subscription timeline from raw rows.

    Each entry summarises one subscription row's lifecycle:
      {
        "type": "ai" | "content" | ...,
        "provider": "stripe" | "aura" | ...,
        "trial": bool,
        "lifetime": bool,
        "created_at": ISO,
        "purchase_at": ISO,
        "converted_at": ISO | null,    # trial → paid conversion
        "expiration_at": ISO | null,
        "deleted_at": ISO | null,
        "auto_renew": bool | null,
        "status": one of: "active", "cancelled-active", "expired",
                  "trial-expired", "refunded", "revoked"
      }

    Sorted by `createdAt` ascending — earliest first. Suitable for
    `--json` consumers and for downstream agents (data:analyze-user-behavior
    etc.) that want the full subscription story rather than a single label.

    Per-row `status` uses `_classify_deletion` to disambiguate the three
    causes of a `deletedAt` row (trial-expired / refunded / revoked) — see
    ENG-540 for the cohort-analysis rationale.
    """
    now = datetime.now(tz=timezone.utc)
    entries: list[dict] = []
    for r in rows or []:
        exp = _parse_dt(r.get("expirationAt"))
        deleted = bool(r.get("deletedAt"))
        if deleted:
            cause = _classify_deletion(r)
            status = {
                "trial_expired": "trial-expired",
                "refunded": "refunded",
                "revoked": "revoked",
            }.get(cause, "revoked")
        elif r.get("lifetime"):
            status = "active"
        elif exp and exp > now:
            status = "cancelled-active" if r.get("autoRenew") is False else "active"
        elif exp and exp <= now:
            status = "expired"
        else:
            status = "active"  # lifetime grants with no expirationAt
        entries.append({
            "type": r.get("type"),
            "provider": r.get("provider"),
            "trial": bool(r.get("trial")),
            "lifetime": bool(r.get("lifetime")),
            "created_at": r.get("createdAt"),
            "purchase_at": r.get("purchaseAt"),
            "converted_at": r.get("convertedAt"),
            "expiration_at": r.get("expirationAt"),
            "deleted_at": r.get("deletedAt"),
            "auto_renew": r.get("autoRenew"),
            "status": status,
        })
    entries.sort(key=lambda e: _parse_dt(e.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return entries


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def sum_credits(stats: dict, *, day_start_unix: int | None = None, day_end_unix: int | None = None) -> tuple[float, dict[str, float]]:
    """Sum credit consumption across the response.

    Returns (total, per_bucket_total). When day_start/day_end are passed,
    sum only the day buckets that fall within that range — useful for the
    headline 7d-baseline math when we fetched a wider window."""
    times = stats.get("time") or []
    usage = stats.get("usage") or {}
    if not times:
        return 0.0, {}
    # Sum across all buckets
    total = 0.0
    per_bucket: dict[str, float] = defaultdict(float)
    for bucket_label, day_series in usage.items():
        bucket_total = 0.0
        for i, day_ts_ms in enumerate(times):
            day_ts = int(day_ts_ms) // 1000
            if day_start_unix is not None and day_ts < day_start_unix:
                continue
            if day_end_unix is not None and day_ts >= day_end_unix:
                continue
            val = float(day_series[i] if i < len(day_series) else 0)
            bucket_total += val
        per_bucket[bucket_label] = bucket_total
        total += bucket_total
    return total, dict(per_bucket)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def fmt_credits(c: float) -> str:
    if c >= 1_000_000:
        return f"{c/1_000_000:.2f}M"
    if c >= 1_000:
        return f"{c/1_000:.1f}K"
    return f"{c:.0f}"


def fmt_money(v: float) -> str:
    return f"${v:.2f}"


def short_uid(uid: str) -> str:
    return uid[:UID_DISPLAY_PREFIX] + "…" if len(uid) > UID_DISPLAY_PREFIX else uid


def render(
    label: str,
    is_partial: bool,
    window_credits: float,
    baseline_avg_credits: float | None,
    subscription: dict | None,
    by_model: dict[str, float],
    by_product: dict[str, float],
    top_users: list[dict],
    aura_token_set: bool,
    retention_warned: bool,
    no_top_users: bool,
) -> str:
    lines: list[str] = []

    header = f"# ElevenLabs cost snapshot — {label}"
    if is_partial:
        header += " ⚠️ partial day"
    lines.append(header)
    lines.append("")

    # Headline
    pieces: list[str] = [f"**{fmt_credits(window_credits)} credits**"]
    if subscription:
        plan_name = (subscription.get("tier") or "?").upper()
        next_inv = (subscription.get("next_invoice") or {}).get("amount_due_cents")
        monthly = f"${next_inv/100:.0f}" if next_inv is not None else "?"
        overage = float((subscription.get("current_overage") or {}).get("amount") or 0)
        pieces.append(f"Plan: {monthly}/mo ({plan_name})")
        pieces.append(f"Overage: {fmt_money(overage)}{' ⚠️' if overage > 0 else ''}")
    if baseline_avg_credits is not None and baseline_avg_credits > 0:
        delta = (window_credits - baseline_avg_credits) / baseline_avg_credits
        sign = "+" if delta >= 0 else ""
        flag = ""
        if abs(delta) >= ANOMALY_THRESHOLD:
            flag = " ⚠️" if delta > 0 else " ✅"
        pieces.append(f"{sign}{delta*100:.0f}% vs 7d avg ({fmt_credits(baseline_avg_credits)}){flag}")
    lines.append(" | ".join(pieces))
    lines.append("")

    # Subscription / quota block
    if subscription:
        used = subscription.get("character_count")
        limit = subscription.get("character_limit")
        renewal_unix = subscription.get("next_character_count_reset_unix")
        plan_start_unix = (renewal_unix - 30 * 86400) if renewal_unix else None
        now_unix = int(datetime.now(tz=timezone.utc).timestamp())
        lines.append("## Subscription / quota")
        lines.append("")
        if used is not None and limit:
            pct_used = used / limit * 100
            quota_line = f"- Quota: {fmt_credits(used)} / {fmt_credits(limit)} ({pct_used:.0f}% used)"
            if plan_start_unix and renewal_unix and renewal_unix > plan_start_unix:
                period_days = (renewal_unix - plan_start_unix) // 86400
                day_in_period = max(0, (now_unix - plan_start_unix) // 86400)
                expected_pct = day_in_period / period_days * 100 if period_days else 0
                pace_delta = pct_used - expected_pct
                pace = "on pace" if abs(pace_delta) <= 5 else ("ahead" if pace_delta > 0 else "behind")
                quota_line += f", {day_in_period}/{period_days} days into period ({pace}, expected ~{expected_pct:.0f}%)"
            lines.append(quota_line)
        if renewal_unix:
            renewal_dt = datetime.fromtimestamp(renewal_unix, tz=timezone.utc).astimezone(PACIFIC)
            lines.append(f"- Renewal: {renewal_dt.strftime('%b %-d, %Y')}")
        lines.append("")

    # By model
    if by_model:
        lines.append("## By model")
        lines.append("")
        lines.append("| Model | Credits | % |")
        lines.append("|---|---:|---:|")
        total = sum(by_model.values())
        for m, c in sorted(by_model.items(), key=lambda kv: -kv[1]):
            if c <= 0:
                continue
            pct = (c / total * 100) if total else 0
            lines.append(f"| `{m}` | {fmt_credits(c)} | {pct:.1f}% |")
        lines.append("")

    # By product type
    if by_product:
        lines.append("## By product type")
        lines.append("")
        lines.append("| Product | Credits | % |")
        lines.append("|---|---:|---:|")
        total = sum(by_product.values())
        for p, c in sorted(by_product.items(), key=lambda kv: -kv[1]):
            if c <= 0:
                continue
            pct = (c / total * 100) if total else 0
            lines.append(f"| {p} | {fmt_credits(c)} | {pct:.1f}% |")
        lines.append("")

    # Top users
    if not no_top_users:
        if top_users:
            lines.append(f"## Top {len(top_users)} users (Conversational AI)")
            lines.append("")
            lines.append("| Rank | UID | State | Minutes | Cost | Calls |")
            lines.append("|---:|---|---|---:|---:|---:|")
            for i, u in enumerate(top_users, start=1):
                state = u.get("state") or ("(no state — AURA_API_TOKEN missing)" if not aura_token_set else "(state lookup failed)")
                lines.append(
                    f"| {i} | `{short_uid(u['user_id'])}` | {state} | "
                    f"{u['minutes']:.1f} | {fmt_money(u['cost'])} | {u['calls']} |"
                )
            lines.append("")
        else:
            lines.append("## Top users (Conversational AI)")
            lines.append("")
            lines.append("_No Conversational AI traffic in this window._")
            lines.append("")
        if retention_warned:
            lines.append("> ⚠️ Window extends beyond ElevenLabs' 10-day Conversational AI retention. Some older calls were not detail-fetchable; top-5 may understate cost on the back end of the window.")
            lines.append("")
    else:
        lines.append("_Top-users section skipped (`--no-top-users`)._")
        lines.append("")

    lines.append("_Credits + plan / overage are ElevenLabs actuals. Per-user $ = `duration_secs × $0.09/min in-plan, $0.19/min burst` (actuals, no derivation). State annotation via Aura PaymentService `/subscriptions/list/:userId`._")
    return "\n".join(lines)


def slack_summary(
    label: str,
    window_credits: float,
    baseline_avg_credits: float | None,
    subscription: dict | None,
    by_model: dict[str, float],
    by_product: dict[str, float],
) -> str:
    parts: list[str] = []
    parts.append(f"*ElevenLabs cost — {label}*")
    pieces = [f"*{fmt_credits(window_credits)}* credits"]
    if subscription:
        next_inv = (subscription.get("next_invoice") or {}).get("amount_due_cents")
        monthly = f"${next_inv/100:.0f}" if next_inv is not None else "?"
        overage = float((subscription.get("current_overage") or {}).get("amount") or 0)
        pieces.append(f"plan {monthly}/mo")
        pieces.append(f"overage {fmt_money(overage)}")
    if baseline_avg_credits is not None and baseline_avg_credits > 0:
        delta = (window_credits - baseline_avg_credits) / baseline_avg_credits
        sign = "+" if delta >= 0 else ""
        pieces.append(f"{sign}{delta*100:.0f}% vs 7d avg")
    parts.append(" | ".join(pieces))
    parts.append("")
    if by_model:
        parts.append("Top models:")
        for m, c in sorted(by_model.items(), key=lambda kv: -kv[1])[:5]:
            if c <= 0:
                continue
            parts.append(f"  • `{m}` — {fmt_credits(c)}")
    if by_product:
        parts.append("By product type:")
        for p, c in sorted(by_product.items(), key=lambda kv: -kv[1]):
            if c <= 0:
                continue
            parts.append(f"  • {p}: {fmt_credits(c)}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def emit_slack_payload(summary: str) -> None:
    print("\n---SLACK-PAYLOAD-BEGIN---", file=sys.stderr)
    print(summary, file=sys.stderr)
    print("---SLACK-PAYLOAD-END---", file=sys.stderr)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Daily ElevenLabs cost snapshot for the Aura workspace.")
    parser.add_argument("--window", default="yesterday", help="yesterday | today | last-24h | last-7d")
    parser.add_argument("--since", help="YYYY-MM-DD, half-open with --until, Pacific midnight")
    parser.add_argument("--until", help="YYYY-MM-DD, half-open with --since, Pacific midnight")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help=f"Top-N users (default {DEFAULT_TOP_N})")
    parser.add_argument("--no-top-users", action="store_true", help="Skip the conversation walk (much faster, no top-5)")
    parser.add_argument("--post-to-slack", action="store_true", help="Emit a Slack payload for the harness to post")
    parser.add_argument("--json", help="Write structured JSON to this path")
    args = parser.parse_args()

    api_key = os.environ.get("ELEVENLABS_ADMIN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: ELEVENLABS_ADMIN_API_KEY (or ELEVENLABS_API_KEY) not set", file=sys.stderr)
        return 2

    aura_token = os.environ.get("AURA_API_TOKEN")
    if not aura_token and not args.no_top_users:
        print("ℹ️ AURA_API_TOKEN unset — top-5 will render without subscription-state annotation.", file=sys.stderr)

    try:
        start, end, label, is_partial = resolve_window(args.window, args.since, args.until)
    except SystemExit:
        raise

    window_days = (end - start) / 86400.0
    if window_days > RETENTION_DAYS:
        print(
            f"⚠️ Window is {window_days:.1f} days — exceeds ElevenLabs' {RETENTION_DAYS}-day ConvAI retention. "
            "Some calls older than 10 days will 404 on detail fetch and be excluded from top-5.",
            file=sys.stderr,
        )

    print(f"Resolving window: {label} ({start} → {end} unix)", file=sys.stderr)

    # Step 1: subscription state (plan, overage, quota)
    subscription = fetch_subscription(api_key)

    # Step 2: workspace credit totals for window + 7d baseline
    # Pull a 9-day character-stats window: 7 days before yesterday-end + the window itself,
    # then sum slices for "window total" and "7d avg" separately.
    baseline_start = end - 7 * 86400  # 7 full days ending at window end
    fetch_start = min(start, baseline_start)
    by_model_stats = fetch_character_stats(fetch_start, end, "model", api_key)
    by_product_stats = fetch_character_stats(fetch_start, end, "product_type", api_key)

    # Sum window credits (across model breakdown — same total as product breakdown)
    window_credits, by_model = sum_credits(by_model_stats, day_start_unix=start, day_end_unix=end)
    _, by_product = sum_credits(by_product_stats, day_start_unix=start, day_end_unix=end)

    # 7d baseline: sum the 7 days BEFORE window-start, then divide by window-days
    baseline_window_size_days = max(1.0, (end - start) / 86400.0)
    baseline_total, _ = sum_credits(by_model_stats, day_start_unix=baseline_start, day_end_unix=start)
    baseline_avg = (baseline_total / 7.0) * baseline_window_size_days if window_days <= 7 else None

    # Step 3: walk conversations for top-5 (slow part)
    top_users: list[dict] = []
    retention_warned = window_days > RETENTION_DAYS
    if not args.no_top_users:
        print("Walking /v1/convai/conversations for top-5 attribution (slow)...", file=sys.stderr)
        user_agg: dict[str, dict] = defaultdict(lambda: {"duration": 0.0, "duration_burst": 0.0, "cost": 0.0, "calls": 0})
        n_total = 0
        n_no_attr = 0
        for c in list_conversations(start, end, api_key):
            n_total += 1
            cid = c.get("conversation_id")
            if not cid:
                continue
            detail = fetch_conversation_detail(cid, api_key)
            if detail is None:
                n_no_attr += 1
                continue
            attr = extract_attribution(detail)
            if not attr:
                n_no_attr += 1
                continue
            uid = attr["user_id"]
            user_agg[uid]["duration"] += attr["duration_secs"]
            if attr["is_burst"]:
                user_agg[uid]["duration_burst"] += attr["duration_secs"]
            user_agg[uid]["cost"] += duration_to_dollars(attr["duration_secs"], is_burst=attr["is_burst"])
            user_agg[uid]["calls"] += 1
        print(f"  walked {n_total} conversations, {len(user_agg)} distinct users with userId, {n_no_attr} without attribution", file=sys.stderr)
        ranked = sorted(user_agg.items(), key=lambda kv: -kv[1]["duration"])[: args.top]
        for uid, d in ranked:
            state = None
            timeline: list[dict] = []
            if aura_token:
                rows = fetch_subscription_rows(uid, aura_token)
                if rows is not None:
                    state = derive_state(rows)
                    timeline = derive_timeline(rows)
            top_users.append({
                "user_id": uid,
                "minutes": d["duration"] / 60.0,
                "cost": d["cost"],
                "calls": d["calls"],
                "state": state,
                "subscription_timeline": timeline,  # surfaced in --json
            })

    # Render
    report_md = render(
        label, is_partial, window_credits, baseline_avg, subscription,
        by_model, by_product, top_users,
        aura_token_set=bool(aura_token),
        retention_warned=retention_warned,
        no_top_users=args.no_top_users,
    )
    print(report_md)

    if args.post_to_slack:
        summary = slack_summary(label, window_credits, baseline_avg, subscription, by_model, by_product)
        emit_slack_payload(summary)

    if args.json:
        out = {
            "window": {"start": start, "end": end, "label": label, "is_partial": is_partial},
            "total_credits": window_credits,
            "baseline_7d_avg_credits": baseline_avg,
            "subscription": subscription,
            "by_model": by_model,
            "by_product_type": by_product,
            "top_users": top_users,  # full UIDs in JSON; truncated only in rendered markdown
        }
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\nWrote {args.json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
