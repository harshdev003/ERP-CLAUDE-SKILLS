#!/usr/bin/env python3
"""
Weekly Aurie cost report — LLM (OpenAI) + Voice (ElevenLabs) for a Sun–Sat week.

Two numbers per week, formatted for direct paste into the cost-tracking spreadsheet:

  - **LLM costs** — total OpenAI spend (`/v1/organization/costs`, summed across
    every project × day in the window)
  - **Voice costs** — total ElevenLabs spend pulled directly from the
    `fiat_units_spent` metric on `/v1/usage/character-stats`. The API returns
    the value in cents; we divide by 100 to get USD. This is the authoritative
    billed amount — no credits-per-minute or overage-rate assumptions.

Plus an LLM breakdown by AI premium tier — see "AI premium tier breakdown" below.
Voice stays as a single total because ElevenLabs doesn't expose per-user
subscription metadata on this path.

Other spreadsheet columns (LLM cost / active user, processing fees, Margin $/%)
are out of scope for this script and rendered as `—` so the TSV line still
preserves column positions when pasted.

Week boundary semantics:

  - Default detection of "what's the previous completed Sun–Sat week" runs in
    `America/Los_Angeles` (auto-DST: PDT in summer, PST in winter).
  - OpenAI query: UTC-aligned daily buckets — the calendar date of the
    Pacific Sunday anchors a UTC midnight → UTC midnight window. Matches
    how `/organization/costs` natively aggregates.
  - ElevenLabs query: same calendar dates, re-anchored at Pacific
    (America/Los_Angeles) midnight. The dashboard's date picker is
    timezone-aware to the viewer; pinning to Pacific (where the team is
    based) makes the script's number match what teammates see, regardless
    of where the script runs. Reconciles exactly with the dashboard's
    "Cost before overage surcharge" tile for the same Sun–Sat window.
  - `--since/--until` and `--week` accept calendar dates; OpenAI sees them
    as UTC midnights, ElevenLabs sees them as Pacific midnights.

AI premium tier breakdown:

  Each completion is classified by its `metadata.aiPremium` / `aiPremiumState`
  tags (added by ENG-498) into one of: Premium Paid / Trial / Free / Payment
  Pending / Non Premium / Never AI Premium / (untagged). The per-project
  dollar total is allocated across tiers proportionally to token weight
  (no model-price table — see references/allocation.md in the sibling skill
  `eng:analyze-openai-cost-per-subscription-state` for the math). Tagging
  shipped on 2026-05-12; windows that straddle or predate that date get a
  rollout warning banner so the (untagged) slice isn't misread.

  Attribution source (per slice of the window): from the ledger epoch
  (2026-07-23T03:25Z, IS 0.285.0) onward, the PRIMARY per-call source is the
  IntelligenceService LLM-call ledger (BigQuery llm.llm_call — one row per OpenAI
  call, Chat Completions AND Responses APIs, with aiPremiumState/premiumState
  + token counts as top-level columns). If the ledger can't be read (missing
  GCP_KEY, BigQuery error) the post-epoch slice falls
  back to the stored-chat-completions walk — with a stderr warning, because
  that fallback is chat-API-only and cannot see Responses-API traffic. The
  pre-epoch slice ALWAYS uses the stored-completions walk (the ledger has no
  rows before the epoch). Results merge with a defensive dedupe on the OpenAI
  object id. The Costs API stays the $ authority throughout — the ledger only
  replaces the token-weight/attribution walk.

There is also a `--from-bigquery` mode that sources the same numbers from the
persisted daily cost store (`aura-main.aurie_cost.aurie_per_user_daily_cost`,
maintained by `eng:store-aurie-user-daily-cost`) instead of the live APIs. Use
it to backfill older / late-run weeks — the live path can't, because ElevenLabs
only retains ~10 days of usage buckets. It also makes the voice-by-tier cut
instant. It reports by the store's 5-bucket `subscription_state`
(paid/trial/free/grace/other), NOT the 7-bucket aiPremium* taxonomy the live
path uses. Requires GCP_KEY instead of the OpenAI/ElevenLabs keys.

Usage:
    python report.py                                # previous completed Sun–Sat, Pacific
    python report.py --week 2026-05-04              # explicit Sunday (calendar date)
    python report.py --since 2026-04-27 --until 2026-05-04   # explicit range
    python report.py --no-tier-breakdown            # skip the per-tier walk (faster)
    python report.py --from-bigquery --week 2026-06-21   # backfill from the BQ store
    python report.py --json /tmp/aurie_week.json

Env:
    OPENAI_ADMIN_API_KEY      Required (live path). Admin API key (`sk-admin-…`).
                              Used for /organization/* only (costs, usage, project
                              list). PROD-3572: OpenAI enforces `model.request`
                              scope on GET /v1/chat/completions, which Admin keys
                              cannot hold — the stored-completions walk no longer
                              works with this key.
    OPENAI_<PROJECT>_API_KEY  Required (live path, per project) for the per-project
                              stored-completions walk (PROD-3572). Project-scoped
                              service-account key, one per OpenAI project, named
                              from the project display name:
                              "Memory [Production]"       → OPENAI_MEMORY_API_KEY
                              "Conversation [Production]" → OPENAI_CONVERSATION_API_KEY
                              A project whose key is unset is skipped (its per-tier
                              breakdown is omitted); the report still produces.
    GCP_KEY                   Recommended (live path, tier breakdown). Service account
                              for the IS LLM-call ledger (BigQuery llm.llm_call), the
                              primary post-epoch attribution source. Missing/invalid
                              → stderr warning + fallback to the stored-completions
                              walk (chat-API-only; can't see Responses-API traffic).
    ELEVENLABS_ADMIN_API_KEY  Required (live path). Scope: Workspace Analytics: Access.
    GCP_KEY                   Required for --from-bigquery. Service-account JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import pathlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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



# ---------------------------------------------------------------------------
# Fail closed on a post-epoch ledger outage (ENG-1054)
# ---------------------------------------------------------------------------
# A post-2026-07-23 ledger outage is not a degradable condition. The stored-
# completions fallback resolves nothing for every production project except
# Conversation, and only partially for it (the aiResponseAPI A/B serves its
# treatment arm on the Responses API) — while dollars still come from the Costs
# API. So the run would total correctly and look plausible with per-user
# attribution silently collapsed into the untagged bucket. Same shape as the
# bq.assert_fresh guard: a warning nobody reads is not enforcement.
# Canonical detail: Tools/OpenAI → "Responses API migration — reading stored data".
_ALLOW_DEGRADED = [bool(os.environ.get("AINATIVE_ALLOW_DEGRADED_ATTRIBUTION"))]


def _fail_closed_on_ledger_outage(exc: object) -> None:
    """Exit 2 on a post-epoch ledger outage unless explicitly overridden."""
    if _ALLOW_DEGRADED[0]:
        return
    print(
        f"FATAL: LLM ledger unreachable on a post-2026-07-23 window ({exc}).\n"
        "  The stored-completions fallback cannot cover it: every production project\n"
        "  except Conversation has had nothing stored since 2026-07-23, and\n"
        "  Conversation is only partially covered. Dollars would still total\n"
        "  correctly from the Costs API while attribution silently collapsed into\n"
        "  the untagged bucket — a plausible-looking report built on nothing.\n"
        "  Re-run once the ledger is reachable. To accept degraded attribution\n"
        "  deliberately: --allow-degraded-attribution, or set\n"
        "  AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1.",
        file=sys.stderr,
    )
    raise SystemExit(2)

# Reuse the sibling store skill's LiveKit-path vendor day-total helpers
# (Cartesia / Fish / Inworld / Deepgram / LiveKit) so the weekly report can additively surface
# the per-vendor voice split. Self-contained per AINative Architecture principle
# 1 — we add the store skill's scripts dir to sys.path rather than duplicating
# the vendor-total math. Those helpers carry the ESTIMATE-rate labels.
_STORE_SCRIPTS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "store-aurie-user-daily-cost", "scripts")
)
sys.path.insert(0, _STORE_SCRIPTS)
try:
    import store as _store  # noqa: E402
except Exception:  # noqa: BLE001 — LiveKit-path vendors are optional; degrade gracefully.
    _store = None

OPENAI_API = "https://api.openai.com/v1"
ELEVENLABS_API = "https://api.elevenlabs.io/v1"

# IntelligenceService per-call LLM ledger (primary post-epoch attribution
# source — one row per OpenAI call, Chat Completions AND Responses APIs).
# Docs: https://app.notion.com/p/aurahealth/LLM-Calls-3a675e23af208127b217e53af47524e8
LEDGER_EPOCH_UNIX = 1784777100  # 2026-07-23T03:25:00Z — the ledger went live in Release 0.285.0. There are NO ledger rows before this moment.
LEDGER_PROJECT_KEYS = frozenset({
    "conversation", "feed", "guardrail", "insights", "intent", "journal",
    "memory", "notification", "onboarding", "profile", "suggestions", "summary",
})

COSTS_PATH = "/organization/costs"
PROJECTS_PATH = "/organization/projects"

# Price multipliers relative to the Standard service tier. OpenAI prices Flex at
# exactly 50% of Standard and Fast (renamed from "priority" on 2026-07-30, both
# strings still accepted) at 2x, so a tier-blind token split misattributes dollars
# whenever tiers mix inside one project — which they do: a prompter that requests
# flex falls back to auto/default under capacity pressure (ENGBACK-1315).
# Canonical rate tables live in `Tools/OpenAI` -> "Per-tier list prices".
SERVICE_TIER_FACTORS = {
    "flex": 0.5,
    "batch": 0.5,
    "default": 1.0,
    "auto": 1.0,
    "scale": 1.0,
    "priority": 2.0,
    "fast": 2.0,
}
# An unset tier means the row predates the ENGBACK-1315 ledger deploy, not that the
# call was free or standard-priced — 1.0 keeps those rows allocating exactly as they
# did before, so a mixed pre/post window degrades instead of skewing.
DEFAULT_SERVICE_TIER_FACTOR = 1.0


def service_tier_factor(tier: object) -> float:
    """Price multiplier for one call's service tier, relative to Standard."""
    if not tier:
        return DEFAULT_SERVICE_TIER_FACTOR
    return SERVICE_TIER_FACTORS.get(str(tier).strip().lower(), DEFAULT_SERVICE_TIER_FACTOR)
CHAT_COMPLETIONS_PATH = "/chat/completions"
CHARACTER_STATS_PATH = "/usage/character-stats"

COSTS_PAGE_SIZE = 180  # /organization/costs max per page
COMPLETIONS_PAGE_SIZE = 100
RETENTION_DAYS = 10    # ElevenLabs usage-bucket retention floor

# Default boundary timezone for "this week" vs "last week" detection.
# Auto-handles DST (PDT in summer, PST in winter). The actual API queries
# still hit UTC-aligned daily buckets — Pacific only affects which Sunday
# the default path picks when running near a midnight boundary.
PACIFIC = ZoneInfo("America/Los_Angeles")

# Subscription tier mapping — kept in sync with
# eng:analyze-openai-cost-per-subscription-state. Per AINative architecture
# principle #1 (skills are self-contained), this is a copy, not an import.
# Tier labels are AI-prefixed to disambiguate from Aura's "Premium" (content
# premium) — `aiPremium*` metadata refers to the AI-features subscription
# (Aurie), while `premium*` metadata refers to the content subscription. Plain
# "Premium Paid" was ambiguous; this skill only renders the AI dimension, so
# every Premium-* label here is AI premium by definition.
TIER_ORDER = [
    "AI Premium Paid",
    "AI Premium Trial",
    "AI Premium Free",
    "AI Premium Payment Pending",
    "Non AI Premium",
    "Never AI Premium",
    "(untagged)",
]

STATE_LABELS = {
    "paid": "AI Premium Paid",
    "trial": "AI Premium Trial",
    "free": "AI Premium Free",
    "paymentPending": "AI Premium Payment Pending",
}

# Date the IntelligenceService metadata-tagging PR (ENG-498 for aiPremium*)
# shipped to production. Calls stored before this date carry no aiPremium /
# aiPremiumState fields. Windows straddling this boundary get a banner so
# readers don't misread the inflated (untagged) slice as a tagging regression.
TAGGING_ROLLOUT_UNIX = int(
    datetime(2026, 5, 12, tzinfo=timezone.utc).timestamp()
)

# ---- BigQuery backfill mode (--from-bigquery) ----
# The daily cost store (eng:store-aurie-user-daily-cost) writes gapless per-user
# daily cost to this table. Reading it sidesteps the ElevenLabs 10-day retention
# wall (so older / late-run weeks stay accurate) and makes the voice tier
# breakdown instant (no 15–20 min /convai/conversations walk). This path uses the
# store's 5-bucket `subscription_state` — a DIFFERENT taxonomy from the 7-bucket
# aiPremium* tiers above; the two are not interchangeable.
BQ_TABLE = "aura-main.aurie_cost.aurie_per_user_daily_cost"
# Above this share of voice on the unattributed `Other` row, the paid/non-paid split
# warns: a healthy week runs ~0-5% (Intelligence Service TTS and rounding remainders).
OTHER_VOICE_WARN_SHARE = 0.05
BQ_COVERAGE_START = date(2026, 6, 1)  # store has no per-user rows before this
BQ_STATE_ORDER = ["paid", "trial", "free", "grace", "other"]
BQ_STATE_LABELS = {
    "paid": "Paid (AI Premium)",
    "trial": "Trial",
    "free": "Free",
    "grace": "Grace / payment pending",
    "other": "Other (unattributed / reconciling)",
}


# ---------------------------------------------------------------------------
# Time-window resolution
# ---------------------------------------------------------------------------


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit(f"Bad date {s!r} — expected YYYY-MM-DD")


def _parse_date_only(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"Bad date {s!r} — expected YYYY-MM-DD")


def previous_completed_sunday_pacific(now_pacific: datetime) -> date:
    """Calendar date of the Sunday starting the most recently *completed*
    Sun–Sat week, evaluated in `America/Los_Angeles`.

    Python's `weekday()` returns Monday=0 … Sunday=6. The current (in-progress)
    week starts on the most recent Sunday at or before today; the previous
    *completed* week starts 7 days before that.
    """
    today = now_pacific.date()
    days_since_sunday = (now_pacific.weekday() + 1) % 7  # 0 if today is Sunday
    start_of_current_week = today - timedelta(days=days_since_sunday)
    return start_of_current_week - timedelta(days=7)


def _utc_midnight(d: date) -> datetime:
    """Build a UTC-anchored datetime at 00:00 of the given calendar date."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def resolve_window(
    week: str | None, since: str | None, until: str | None,
    skip_retention: bool = False,
) -> tuple[datetime, datetime, str, bool]:
    """Return (start_utc, end_utc, label, is_explicit_range).

    Default boundary detection uses `America/Los_Angeles` (Pacific) so that
    "this week vs last week" lines up with the user's local sense of time.
    The actual query window is always UTC-aligned: the calendar date of the
    Pacific Sunday is used as the start of a UTC midnight → UTC midnight
    bucket. This matches how the underlying APIs aggregate (daily UTC
    buckets) and avoids partial-bucket edge cases.

    `is_explicit_range` is True when `--since/--until` were both given;
    that branch skips the retention safety check so callers can backfill
    older rows knowingly.
    """
    now_utc = datetime.now(tz=timezone.utc)
    now_pacific = now_utc.astimezone(PACIFIC)

    if since or until:
        if not (since and until):
            sys.exit("--since and --until must be provided together")
        start = _parse_date(since)
        end = _parse_date(until)
        is_explicit = True
    elif week:
        start_date = _parse_date_only(week)
        if start_date.weekday() != 6:  # 6 = Sunday
            sys.exit(
                f"--week must be a Sunday — got {week} "
                f"({start_date.strftime('%A')})."
            )
        start = _utc_midnight(start_date)
        end = start + timedelta(days=7)
        is_explicit = False
    else:
        start_date = previous_completed_sunday_pacific(now_pacific)
        start = _utc_midnight(start_date)
        end = start + timedelta(days=7)
        is_explicit = False

    # Hard validations (apply to every branch)
    if start >= end:
        sys.exit(f"Empty window: start {start.date()} >= end {end.date()}")
    if end > now_utc:
        sys.exit(
            f"Window ends in the future ({end.date()} > today {now_utc.date()} UTC). "
            "Wait for the week to complete before running."
        )

    # Retention check (default + --week only; --since/--until bypasses it).
    # Measured in UTC since the underlying API retention is UTC-based.
    # --from-bigquery also bypasses it: the BigQuery store persists full history,
    # so the ElevenLabs 10-day floor doesn't apply.
    if not is_explicit and not skip_retention:
        today_midnight_utc = now_utc.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        age_days = (today_midnight_utc - start).days
        if age_days > RETENTION_DAYS:
            sys.exit(
                f"Window starts {age_days} days ago — beyond the "
                f"{RETENTION_DAYS}-day data retention window. ElevenLabs may "
                "have dropped usage buckets in this range. Re-run sooner, or "
                "pass --since/--until to override and accept any gap."
            )

    label = f"{_fmt_md(start)} – {_fmt_md(end - timedelta(days=1))}"
    return start, end, label, is_explicit


def _fmt_md(d: datetime) -> str:
    """Format month/day without leading zeros, cross-platform.

    POSIX `%-m`/`%-d` and Windows `%#m`/`%#d` are mutually exclusive — neither
    works on the other platform. Doing it manually sidesteps both.
    """
    return f"{d.month}/{d.day}"


# ---------------------------------------------------------------------------
# HTTP helper (shared)
# ---------------------------------------------------------------------------


class ProjectWalkSkip(Exception):
    """Raised by `_get` (skip_on_error=True) so a single project's stored-
    completions failure (e.g. a [Development] project resolving to its
    [Production] twin's OPENAI_<PROJECT>_API_KEY, PROD-3572, and getting a 401
    mismatched_project) skips just that project instead of aborting the whole
    report."""


def _get(url: str, headers: dict, params: dict, *, label: str, skip_on_error: bool = False) -> dict:
    """GET with retry on 429.

    `skip_on_error=True` raises `ProjectWalkSkip` instead of calling `sys.exit`
    on a 401/403 — used by the per-project completions walk so one project's
    auth failure doesn't kill the run.
    """
    for attempt in range(5):
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(wait, 30))
            continue
        if not r.ok:
            msg = f"{label}: HTTP {r.status_code} — {r.text[:300]}"
            if skip_on_error and r.status_code in (401, 403):
                raise ProjectWalkSkip(msg)
            sys.exit(msg)
        return r.json()
    # Retry exhaustion on 429 is a systemic rate-limit problem, not a single
    # project's auth failure — always hard-fail here so it can't silently
    # fold real $ into `(untagged)` (skip_on_error is for 401/403 only).
    sys.exit(f"{label}: rate-limited after 5 retries")


# ---------------------------------------------------------------------------
# Project-scoped stored-completions key resolution (PROD-3572)
# ---------------------------------------------------------------------------


def _project_completions_api_key_env(project_name: str) -> str:
    """PROD-3572: "Memory [Production]" -> "OPENAI_MEMORY_API_KEY"."""
    base = re.sub(r"\[.*?\]", "", project_name).strip()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").upper()
    return f"OPENAI_{base}_API_KEY"


def _resolve_completions_api_key(project_name: str) -> str | None:
    """Resolve the project-scoped key for a stored-completions walk (PROD-3572).
    Returns None + stderr warning when unset so the caller skips that project
    rather than hard-fail. OPENAI_ADMIN_API_KEY no longer works for
    /v1/chat/completions."""
    env_name = _project_completions_api_key_env(project_name)
    token = os.environ.get(env_name)
    if not token:
        print(
            f"WARNING: {env_name} not set — skipping stored-completions walk for "
            f"'{project_name}'. OPENAI_ADMIN_API_KEY no longer works for "
            f"/v1/chat/completions (PROD-3572); set {env_name} in "
            f"~/.claude/settings.json.",
            file=sys.stderr,
        )
    return token


# ---------------------------------------------------------------------------
# IntelligenceService LLM-call ledger (post-epoch attribution primary)
# ---------------------------------------------------------------------------


class LedgerUnavailable(Exception):
    """Raised when the IS LLM-call ledger can't be read (missing token,
    non-200, timeout, parse error) — the caller falls back to the
    stored-chat-completions walk for the affected slice."""


def _iso_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def iter_llm_calls(
    start_unix: int, end_unix: int, *, provider: str | None = None
) -> list[dict]:
    """Read the IntelligenceService per-call LLM ledger (BigQuery `llm.llm_call`) for [start, end).
    Newest-first. Raises LedgerUnavailable so callers can fall back to the stored-completions walk."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    params = {
        "from": datetime.fromtimestamp(start_unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": datetime.fromtimestamp(end_unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if provider:
        params["provider"] = provider
    try:
        return llm_ledger.rows(params)
    except llm_ledger.LedgerError as exc:
        raise LedgerUnavailable(str(exc)) from exc


def _ledger_project_key(project_name: str) -> str | None:
    """'Memory [Production]' → 'memory' (the IS feature key the ledger's
    `project` column uses). Returns None for non-production projects — the
    ledger attributes prod traffic only; other projects keep the
    stored-completions walk for the whole window."""
    m = re.match(r"^(.*?)\s*\[Production\]$", project_name.strip())
    if not m:
        return None
    base = m.group(1).strip()
    if not base:
        return None
    return base[0].lower() + base[1:]


def _ledger_row_to_completion(row: dict) -> dict:
    """Normalize one ledger row into the stored-completion shape that
    `allocate_by_ai_tier` / `_extract_completion_fields` already consume, so
    the downstream allocation math is untouched. Column mapping:
    userId → metadata.user, prompterName → metadata.name,
    aiPremium/aiPremiumState → metadata.*, premium/premiumState → metadata.*,
    inputTokens → prompt_tokens, outputTokens → completion_tokens,
    cachedTokens → prompt_tokens_details.cached_tokens, openaiId → id (dedupe
    key against stored completions at the epoch boundary).

    The aiPremium / premium booleans are their own ledger columns (added
    2026-07 — ENGBACK-1237); when present they map straight through so the
    shared `_ai_tier_label` classifier keeps a churned user (aiPremium=false)
    as "Non AI Premium" rather than collapsing to "Never AI Premium". Rows
    written before that column existed carry aiPremium=null; for those we infer
    a truthy flag from a present *State column, matching the old behaviour."""
    user_id = row.get("userId")
    metadata: dict = {}
    if user_id:
        metadata["user"] = user_id
    if row.get("prompterName"):
        metadata["name"] = row["prompterName"]
    ai_premium = row.get("aiPremium")
    ai_state = row.get("aiPremiumState")
    if ai_premium is not None:
        metadata["aiPremium"] = "true" if ai_premium else "false"
    elif ai_state:
        metadata["aiPremium"] = "true"
    if ai_state:
        metadata["aiPremiumState"] = ai_state
    content_premium = row.get("premium")
    content_state = row.get("premiumState")
    if content_premium is not None:
        metadata["premium"] = "true" if content_premium else "false"
    elif content_state:
        metadata["premium"] = "true"
    if content_state:
        metadata["premiumState"] = content_state
    usage = {
        "prompt_tokens": int(row.get("inputTokens") or 0),
        "completion_tokens": int(row.get("outputTokens") or 0),
        "prompt_tokens_details": {
            "cached_tokens": int(row.get("cachedTokens") or 0),
        },
    }
    return {
        "id": row.get("openaiId"),
        "metadata": metadata,
        "usage": usage,
        # Null on rows written before the ENGBACK-1315 deploy — see service_tier_factor.
        "service_tier": row.get("serviceTier"),
    }


# ---------------------------------------------------------------------------
# OpenAI Costs API
# ---------------------------------------------------------------------------


def fetch_openai_by_project(
    start: datetime, end: datetime, api_key: str
) -> tuple[float, dict[str, float]]:
    """Return (total_usd, {project_id: usd}) summed across the window."""
    headers = {"Authorization": f"Bearer {api_key}"}
    per_project: dict[str, float] = defaultdict(float)
    page: str | None = None
    while True:
        params: dict = {
            "start_time": int(start.timestamp()),
            "end_time": int(end.timestamp()),
            "bucket_width": "1d",
            "group_by": ["project_id"],
            "limit": COSTS_PAGE_SIZE,
        }
        if page:
            params["page"] = page
        data = _get(
            f"{OPENAI_API}{COSTS_PATH}",
            headers,
            params,
            label="OpenAI /organization/costs",
        )
        for bucket in data.get("data", []):
            for result in bucket.get("results", []):
                amount = result.get("amount") or {}
                try:
                    value = float(amount.get("value", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                pid = result.get("project_id") or "(unattributed)"
                per_project[pid] += value
        page = data.get("next_page")
        if not page:
            break
    total = sum(per_project.values())
    return total, dict(per_project)


def fetch_projects(api_key: str) -> dict[str, str]:
    """Map project_id → display name (for coverage reporting)."""
    projects: dict[str, str] = {}
    after: str | None = None
    headers = {"Authorization": f"Bearer {api_key}"}
    while True:
        params: dict = {"limit": 100, "include_archived": "true"}
        if after:
            params["after"] = after
        data = _get(
            f"{OPENAI_API}{PROJECTS_PATH}",
            headers,
            params,
            label="OpenAI /organization/projects",
        )
        items = data.get("data", [])
        for p in items:
            projects[p["id"]] = p.get("name") or p["id"]
        if not data.get("has_more") or not items:
            break
        after = items[-1]["id"]
    return projects


def iter_completions(
    start: datetime, end: datetime, project_id: str, api_key: str
) -> list[dict]:
    """List stored completions for one project within [start, end).

    The chat-completions list endpoint orders newest-first and exposes no
    server-side time filter, so we walk pages until we drop below start_unix.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Project": project_id,
    }
    start_unix = int(start.timestamp())
    end_unix = int(end.timestamp())

    out: list[dict] = []
    after: str | None = None

    while True:
        params: dict = {"limit": COMPLETIONS_PAGE_SIZE}
        if after:
            params["after"] = after
        data = _get(
            f"{OPENAI_API}{CHAT_COMPLETIONS_PATH}",
            headers,
            params,
            label=f"OpenAI /chat/completions (project {project_id})",
            skip_on_error=True,
        )
        items = data.get("data", [])
        if not items:
            break

        stop_walking = False
        for item in items:
            created = item.get("created", 0) or 0
            if created >= end_unix:
                continue
            if created < start_unix:
                stop_walking = True
                continue
            out.append(item)

        if stop_walking:
            break
        if not data.get("has_more"):
            break
        last_id = data.get("last_id") or (items[-1].get("id") if items else None)
        if not last_id:
            break
        after = last_id

    return out


# ---------------------------------------------------------------------------
# Tier classification + dollar allocation (AI premium dimension)
#
# Copied from `eng:analyze-openai-cost-per-subscription-state` per AINative
# architecture principle #1 (skills are self-contained). Only the AI premium
# cut is reproduced here — the content premium dimension stays in the sibling
# skill, which is the right place for per-state drill-downs.
# ---------------------------------------------------------------------------


def _truthy(value) -> bool:
    """OpenAI metadata values are always strings — coerce them to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _ai_tier_label(metadata: dict, has_user: bool) -> str:
    """Build the AI premium tier label for one completion.

    `has_user` separates the "both fields missing" case: with a user, the
    user row exists but `aiPremium` is NULL (declared `BOOLEAN` nullable, no
    default) — "Never AI Premium". Without a user, it's true `(untagged)`
    pre-account / system / batch traffic.
    """
    premium_raw = metadata.get("aiPremium")
    state_raw = metadata.get("aiPremiumState")

    if premium_raw is None and state_raw is None:
        return "Never AI Premium" if has_user else "(untagged)"

    if not _truthy(premium_raw):
        return "Non AI Premium"

    if state_raw and state_raw in STATE_LABELS:
        return STATE_LABELS[state_raw]
    return f"AI Premium {state_raw}" if state_raw else "AI Premium"


def _extract_completion_fields(item: dict) -> tuple[str, str, int]:
    """Pull (ai_tier, user_id, weight) from one completion.

    `weight` is the call's token count scaled by its service-tier price multiplier —
    an allocation basis, not a token count.
    """
    metadata = item.get("metadata") or {}
    usage = item.get("usage") or {}

    # Handle both Chat Completions and Responses-API shapes defensively.
    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    total_tokens = (input_tokens or 0) + (output_tokens or 0)

    user_id = (
        metadata.get("user")
        or item.get("user")
        or item.get("input_user")
        or "(unknown)"
    )
    has_user = user_id != "(unknown)"

    # Ledger rows and stored completions both classify through the same
    # `_ai_tier_label` off metadata.aiPremium/aiPremiumState — the ledger
    # normalizer maps its boolean columns into that metadata shape. `_ai_tier`
    # is honoured if a caller still sets it, but nothing does now.
    ai_tier = item.get("_ai_tier") or _ai_tier_label(metadata, has_user=has_user)
    # Scaled by the call's service-tier price multiplier: this is an allocation
    # basis, not a token count — never render it as one.
    weight = total_tokens * service_tier_factor(item.get("service_tier"))
    return ai_tier, user_id, weight


def allocate_by_ai_tier(
    per_project_dollars: dict[str, float],
    per_project_completions: dict[str, list[dict]],
) -> tuple[dict[str, float], dict[str, set]]:
    """Allocate each project's $ across AI premium tiers by token weight.

    Returns (by_tier_dollars, users_by_tier).
    Projects with no completions or zero tokens contribute their full $ to
    the `(untagged)` bucket.
    """
    by_tier: dict[str, float] = defaultdict(float)
    users_by_tier: dict[str, set] = defaultdict(set)

    for pid, dollars in per_project_dollars.items():
        if dollars <= 0 or pid == "(unattributed)":
            continue
        completions = per_project_completions.get(pid) or []
        if not completions:
            by_tier["(untagged)"] += dollars
            continue

        tier_tokens: dict[str, float] = defaultdict(float)
        tier_users: dict[str, set] = defaultdict(set)
        total_tokens = 0.0

        for item in completions:
            ai_tier, user_id, t = _extract_completion_fields(item)
            if t <= 0:
                continue
            total_tokens += t
            tier_tokens[ai_tier] += t
            if user_id != "(unknown)":
                tier_users[ai_tier].add(user_id)

        if total_tokens == 0:
            by_tier["(untagged)"] += dollars
            continue

        for tier, tok in tier_tokens.items():
            by_tier[tier] += dollars * tok / total_tokens
            users_by_tier[tier].update(tier_users[tier])

    return dict(by_tier), {t: set(u) for t, u in users_by_tier.items()}


def rollout_window_warning(start: datetime, end: datetime) -> str | None:
    """Return a markdown banner when the window straddles or predates the
    2026-05-12 aiPremium*/premium* tagging rollout."""
    start_unix = int(start.timestamp())
    end_unix = int(end.timestamp())
    if start_unix >= TAGGING_ROLLOUT_UNIX:
        return None
    if end_unix <= TAGGING_ROLLOUT_UNIX:
        return (
            "> :warning: **Pre-rollout window — tier breakdown is meaningless.** "
            "The `aiPremium*` metadata tagging "
            "([ENG-498](https://linear.app/aura-health/issue/ENG-498)) shipped "
            "on 2026-05-12; this window predates that, so all LLM spend lands "
            "in `(untagged)`."
        )
    return (
        "> :warning: **Mixed pre/post-rollout window.** "
        "The `aiPremium*` metadata tagging "
        "([ENG-498](https://linear.app/aura-health/issue/ENG-498)) shipped on "
        "2026-05-12. Calls before that date carry no tier metadata and surface "
        "as `(untagged)` regardless of actual subscription state — the "
        "`(untagged)` share is overstated until the window is entirely "
        "post-rollout."
    )


# ---------------------------------------------------------------------------
# ElevenLabs Usage API
# ---------------------------------------------------------------------------


def _to_pacific_window_unix_ms(start: datetime, end: datetime) -> tuple[int, int]:
    """Re-anchor a UTC-midnight window at Pacific (America/Los_Angeles) midnight.

    The window's calendar dates are preserved; only the wall-clock offset
    changes. The ElevenLabs dashboard's date picker interprets the chosen
    range in the viewer's local timezone — pinning the script to Pacific
    (where the team is based) makes the numbers match what teammates see
    in the dashboard, regardless of where the script happens to run.
    """
    start_pacific = datetime(start.year, start.month, start.day, tzinfo=PACIFIC)
    end_pacific = datetime(end.year, end.month, end.day, tzinfo=PACIFIC)
    return int(start_pacific.timestamp() * 1000), int(end_pacific.timestamp() * 1000)


def fetch_elevenlabs_cost_usd(start: datetime, end: datetime, api_key: str) -> float:
    """Sum billed ElevenLabs spend in [start, end). Returns USD.

    Uses the `fiat_units_spent` metric, which the API returns in cents — we
    divide by 100 to get dollars. The window's calendar dates are re-anchored
    at Pacific (America/Los_Angeles) midnight to match the ElevenLabs
    dashboard as viewed by the team.

    The character-stats endpoint restricts per-bucket values to activity
    that falls inside `[start_unix, end_unix]` — even when the window
    doesn't align to UTC midnight. So we just sum every returned value.
    Cross-checked against `analyze-elevenlabs-cost-per-user`'s walk of
    `/v1/convai/conversations` for the same Pacific window: agrees within
    the ~0.1% gap accounted for by conversations without a `userId`.

    (An earlier version of this script dropped the trailing UTC bucket
    on the assumption that the API returned full-bucket values per overlap.
    That assumption was wrong — the trailing-day exclusion was discarding
    legitimate in-window activity, ~$45–80/week.)
    """
    start_ms, end_ms = _to_pacific_window_unix_ms(start, end)
    headers = {"xi-api-key": api_key}
    params = {
        "start_unix": start_ms,
        "end_unix": end_ms,
        # Any breakdown works since we just sum — `model` is cheap and stable.
        "breakdown_type": "model",
        "metric": "fiat_units_spent",
        "aggregation_interval": "day",
        "include_workspace_metrics": "true",
    }
    data = _get(
        f"{ELEVENLABS_API}{CHARACTER_STATS_PATH}",
        headers,
        params,
        label="ElevenLabs /usage/character-stats",
    )
    usage = data.get("usage") or {}
    total_cents = 0.0
    for series in usage.values():
        if not isinstance(series, list):
            continue
        for v in series:
            if v is None:
                continue
            try:
                total_cents += float(v)
            except (TypeError, ValueError):
                continue
    return total_cents / 100.0


def fetch_elevenlabs_split_usd(start: datetime, end: datetime,
                               api_key: str) -> tuple[float, float, float]:
    """Return (agent_usd, intelligence_usd, livekit_tts_usd) — the week's ElevenLabs
    spend split by CALLER (ENG-982), Pacific-anchored to match
    `fetch_elevenlabs_cost_usd`.

    Three callers: the ConvAI agent bundle (runs inside the ElevenLabs platform, so
    it has no caller API key — the has_api_key=False slice), the Intelligence
    Service raw-TTS path that mobile calls, and the LiveKit agent's TTS. The last
    two are both raw TTS and are told apart by the CALLING KEY via all_api_keys —
    `product_type` cannot separate them, which is what previously booked
    Intelligence Service spend as LiveKit.

    Degrades rather than failing: on any error, or when all_api_keys is
    unavailable (it needs the workspace Admin role, which a Service Account key
    cannot hold), the API-called slice folds into `intelligence` — true today,
    since LiveKit bills $0. A total failure returns (week_total, 0, 0), the
    pre-split all-agent world.
    """
    start_ms, end_ms = _to_pacific_window_unix_ms(start, end)
    base = {
        "start_unix": start_ms,
        "end_unix": end_ms,
        "metric": "fiat_units_spent",
        "aggregation_interval": "day",
        "include_workspace_metrics": "true",
    }

    def _usage(breakdown):
        try:
            data = _get(f"{ELEVENLABS_API}{CHARACTER_STATS_PATH}", {"xi-api-key": api_key},
                        {**base, "breakdown_type": breakdown},
                        label=f"ElevenLabs /usage/character-stats ({breakdown})")
        except Exception:  # noqa: BLE001 — never let the split break the report.
            return None
        return (data.get("usage") or {})

    def _cents(series):
        return sum(float(v) for v in series if v is not None) if isinstance(series, list) else 0.0

    # 1) ConvAI (no caller key) vs API-called.
    by_haskey = _usage("has_api_key")
    if by_haskey is None:
        return fetch_elevenlabs_cost_usd(start, end, api_key), 0.0, 0.0
    agent_cents = api_cents = 0.0
    for raw_key, series in by_haskey.items():
        if str(raw_key).strip().lower() in ("false", "0", "none"):
            agent_cents += _cents(series)
        else:
            api_cents += _cents(series)

    # 2) Split the API slice per calling key. LiveKit is tested FIRST: its key is
    # named "LiveKit TTS [Prod]", which normalises to a string CONTAINING the
    # Intelligence Service token "tts[prod]".
    by_key = _usage("all_api_keys")
    if by_key is None:
        return agent_cents / 100.0, api_cents / 100.0, 0.0
    intel_cents = lk_cents = 0.0
    for raw_key, series in by_key.items():
        cents = _cents(series)
        norm = str(raw_key).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
        if "livekit" in norm:
            lk_cents += cents
        else:
            intel_cents += cents  # IS keys, and unknown keys → dominant caller
    # Absorb any disagreement between the two breakdowns so the split still sums
    # to the week total.
    intel_cents += api_cents - (intel_cents + lk_cents)
    return agent_cents / 100.0, max(intel_cents, 0.0) / 100.0, max(lk_cents, 0.0) / 100.0


# ---------------------------------------------------------------------------
# LiveKit-path vendors (Cartesia / Fish / Inworld / Deepgram / LiveKit) — weekly
# workspace totals
# ---------------------------------------------------------------------------

# Non-ElevenLabs LiveKit-path vendor keys, in table order. ElevenLabs-as-LiveKit-TTS
# is reported under ElevenLabs, never in this sum.
LIVEKIT_PATH_VENDORS = ("cartesia", "fish", "inworld", "deepgram", "livekit")


def _stored_livekit_path_days(d0, d_last) -> tuple[dict, str]:
    """({day: {cartesia, fish, inworld, deepgram, livekit}}, note) as the store BOOKED them.

    store.py books LiveKit against the month's prior minutes (overage past the plan
    allotment) and Deepgram including LiveKit Inference STT; the per-day vendor
    recompute sees neither. Same columns as --from-bigquery. A day absent from the
    result is one the store does not hold.
    """
    sql = f"""
        SELECT CAST(date AS STRING) AS d,
          SUM(COALESCE(voice_cartesia, 0)) AS cartesia,
          SUM(COALESCE(voice_fish, 0)) AS fish,
          SUM(COALESCE(voice_inworld, 0)) AS inworld,
          SUM(COALESCE(voice_deepgram, 0)) AS deepgram,
          SUM(COALESCE(voice_livekit, 0) + COALESCE(voice_livekit_platform, 0)) AS livekit
        FROM `{BQ_TABLE}`
        WHERE date BETWEEN DATE(@d0) AND DATE(@d_last)
        GROUP BY d
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bq  # noqa: E402  (bundled copy — canonical at eng:query-bigquery)
        rows = bq.query(sql, params={"d0": d0.isoformat(), "d_last": d_last.isoformat()},
                        max_bytes=2 * 1024 ** 3)
    except Exception as e:  # noqa: BLE001 — BQUnavailable / BQError → vendor recompute
        return {}, f"livekit_store_unavailable: {type(e).__name__} — vendor recompute for every day"
    return ({r["d"]: {k: float(r.get(k) or 0.0) for k in LIVEKIT_PATH_VENDORS}
             for r in rows}, "")


def fetch_livekit_path_week(start: datetime, end: datetime, rates: dict) -> tuple[dict, list[str]]:
    """Sum the LiveKit-path vendors over [start, end).

    Returns ({cartesia, fish, inworld, deepgram, livekit}, notes). Every day the daily cost store
    holds is read from it (the dollars it booked). Only an unstored day falls back to
    the vendor daily-workspace totals, DERIVED from PRICING-SHEET ESTIMATE rates —
    which miss the LiveKit overage and LiveKit Inference STT, and say so in a note.
    If the store helpers couldn't be imported, unstored days return zeros with a note
    (the report still renders LLM + ElevenLabs actuals).
    """
    totals = {k: 0.0 for k in LIVEKIT_PATH_VENDORS}
    notes: set[str] = set()
    d = start.date()
    last = (end - timedelta(days=1)).date()
    stored, store_note = _stored_livekit_path_days(d, last)
    if store_note:
        notes.add(store_note)
    n_days = (last - d).days + 1
    unstored = [(d + timedelta(days=i)).isoformat() for i in range(n_days)
                if (d + timedelta(days=i)).isoformat() not in stored]
    for day in stored.values():
        for k in totals:
            totals[k] += day[k]
    if stored:
        notes.add(f"livekit_path_source: {len(stored)} of {n_days} day(s) read from the "
                  "BigQuery cost store (booked $: LiveKit overage and LiveKit Inference STT included)")
    if not unstored:
        return totals, sorted(notes)
    if _store is None:
        notes.add("livekit_path_pending: store helpers unavailable — LiveKit-path voice not reported "
                  f"for {len(unstored)} unstored day(s)")
        return totals, sorted(notes)
    notes.add(f"livekit_path_unstored: {len(unstored)} day(s) ({', '.join(unstored)}) recomputed "
              "from the vendor APIs — omits the month-to-date LiveKit overage and LiveKit "
              "Inference STT, so the LiveKit path reads low for those days")
    for ds in unstored:
        cartesia, ct_note = _store.cartesia_day_total(ds, rates["cartesia"])
        deepgram, dg_note = _store.deepgram_day_total(ds, rates["deepgram"])
        _by_user, _min, livekit, lk_note, lk_meta = _store.livekit_costs(ds, rates["livekit"])
        # Fish has no usage API and Inworld falls back to them: both read the agent
        # logs livekit_costs just parsed.
        fish, fh_note = _store.fish_day_total(lk_meta, rates["fish"])
        inworld, iw_note = _store.inworld_day_total(ds, lk_meta, rates["inworld"])
        totals["cartesia"] += cartesia
        totals["fish"] += fish
        totals["inworld"] += inworld
        totals["deepgram"] += deepgram
        totals["livekit"] += livekit
        for n in (ct_note, fh_note, iw_note, dg_note, lk_note):
            if n:
                notes.add(n)
    return totals, sorted(notes)


# ---------------------------------------------------------------------------
# BigQuery backfill source (--from-bigquery)
# ---------------------------------------------------------------------------


def fetch_bigquery_week(start: datetime, end: datetime) -> dict:
    """Aggregate the per-user daily cost store over [start, end) (UTC dates).

    Returns a dict with top-line $ (llm/voice/total), the per-vendor voice split
    (ElevenLabs actual; the LiveKit-path vendors' $ was rate-derived when the
    store was written), and per-subscription-tier LLM+voice+active-user rows,
    plus any coverage notes. Requires GCP_KEY. Raises BQUnavailable / BQError
    from the bundled bq helper on failure — the caller turns that into a clean
    exit.
    """
    # Bundled copy — canonical at eng:query-bigquery/scripts/bq.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import bq  # noqa: E402

    d0 = start.date()
    d_last = (end - timedelta(days=1)).date()
    sql = f"""
        SELECT
          COALESCE(subscription_state, 'other') AS state,
          SUM(llm_cost)             AS llm,
          SUM(voice_cost)           AS voice,
          SUM(total_cost)           AS total,
          SUM(voice_11labs_agent)   AS el_agent,
          -- ENG-982: Intelligence Service raw TTS. Omitting it drops that slice
          -- from the vendor table and breaks the tie-out to SUM(voice_cost).
          SUM(voice_11labs_intelligence) AS el_intelligence,
          SUM(voice_11labs_livekit) AS el_livekit,
          SUM(voice_cartesia)       AS cartesia,
          SUM(voice_fish)           AS fish,
          SUM(voice_inworld)        AS inworld,
          SUM(voice_deepgram)       AS deepgram,
          -- PROD-5998: overage + subscription accrual (voice_livekit alone is
          -- overage-only and reads $0 inside the plan allotment).
          SUM(COALESCE(voice_livekit, 0)
              + COALESCE(voice_livekit_platform, 0)) AS livekit,
          COUNT(DISTINCT IF(active = 1, user_id, NULL)) AS active_users
        FROM `{BQ_TABLE}`
        WHERE date BETWEEN DATE(@d0) AND DATE(@d_last)
        GROUP BY state
    """
    rows = bq.query(
        sql,
        params={"d0": d0.isoformat(), "d_last": d_last.isoformat()},
        max_bytes=2 * 1024 ** 3,
    )

    def f(r: dict, k: str) -> float:
        return float(r.get(k) or 0.0)

    by_state = {r["state"]: r for r in rows}
    llm_total = sum(f(r, "llm") for r in rows)
    voice_total = sum(f(r, "voice") for r in rows)
    total = sum(f(r, "total") for r in rows)
    vendor = {
        "el_agent": sum(f(r, "el_agent") for r in rows),
        # ENG-982: the renderer reads `el_intelligence`; leaving the key out of
        # this dict renders $0.00 for that vendor and breaks the vendor table's
        # tie-out to SUM(voice_cost) by the Intelligence Service slice.
        "el_intelligence": sum(f(r, "el_intelligence") for r in rows),
        "el_livekit": sum(f(r, "el_livekit") for r in rows),
        "cartesia": sum(f(r, "cartesia") for r in rows),
        "fish": sum(f(r, "fish") for r in rows),
        "inworld": sum(f(r, "inworld") for r in rows),
        "deepgram": sum(f(r, "deepgram") for r in rows),
        "livekit": sum(f(r, "livekit") for r in rows),
    }

    tiers: list[dict] = []
    ordered = BQ_STATE_ORDER + [s for s in by_state if s not in BQ_STATE_ORDER]
    seen: set[str] = set()
    for state in ordered:
        if state in seen or state not in by_state:
            continue
        seen.add(state)
        r = by_state[state]
        tiers.append({
            "state": state,
            "label": BQ_STATE_LABELS.get(state, state),
            "llm": f(r, "llm"),
            "voice": f(r, "voice"),
            "active_users": int(r.get("active_users") or 0),
        })

    # Which days the store actually holds. The leading-edge check below catches a
    # window that starts before the store exists; this catches the commoner case —
    # the store simply has not run yet for the last day(s) of a just-closed week.
    # Without it those days are summed as zero and the week reads low with no
    # signal at all, which is the exact failure the live path refuses to make.
    observed = bq.query(
        f"SELECT COUNT(DISTINCT date) AS days, MIN(date) AS mn, MAX(date) AS mx "
        f"FROM `{BQ_TABLE}` WHERE date BETWEEN DATE(@d0) AND DATE(@d_last)",
        params={"d0": d0.isoformat(), "d_last": d_last.isoformat()},
    )
    cov = observed[0] if observed else {}
    expected_days = (d_last - d0).days + 1
    present_days = int(cov.get("days") or 0)

    notes: list[str] = []
    if not rows:
        notes.append(
            "no rows in window — the daily cost store may not have run for these "
            "dates yet (run eng:store-aurie-user-daily-cost first)"
        )
    if d0 < BQ_COVERAGE_START:
        notes.append(
            f"partial: the BigQuery cost store begins {BQ_COVERAGE_START.isoformat()}; "
            "earlier days are missing, so totals for this window are understated"
        )
    if rows and present_days < expected_days:
        mx = cov.get("mx")
        missing = expected_days - present_days
        tail = (
            f" — the store ends {mx}, the window ends {d_last}"
            if mx and mx < d_last else ""
        )
        notes.append(
            f"PARTIAL WEEK: {present_days} of {expected_days} days present{tail}. "
            f"{missing} day(s) contribute $0, so every figure below is understated. "
            "Run eng:store-aurie-user-daily-cost for the missing dates, or take the "
            "top-line row from the live path instead."
        )
    src = bq.credential_source()
    if src:
        notes.append(f"BigQuery auth: {src}")

    return {
        "llm_usd": llm_total,
        "voice_usd": voice_total,
        "total_usd": total,
        "vendor": vendor,
        "tiers": tiers,
        "notes": notes,
        "coverage": {
            "days_present": present_days,
            "days_expected": expected_days,
            "first_date": str(cov.get("mn") or ""),
            "last_date": str(cov.get("mx") or ""),
            "complete": present_days >= expected_days,
            "credential_source": bq.credential_source(),
        },
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def fmt_usd(v: float) -> str:
    return f"${v:,.2f}"


def _tier_sort_key(tier: str) -> tuple[int, str]:
    try:
        return (TIER_ORDER.index(tier), tier)
    except ValueError:
        return (len(TIER_ORDER), tier)


def render_markdown(
    label: str,
    start: datetime,
    end: datetime,
    llm_usd: float,
    voice_usd: float,
) -> str:
    sat = end - timedelta(days=1)

    lines: list[str] = []
    lines.append(
        f"**Week of {_fmt_md(start)} (Sun) → {_fmt_md(sat)} (Sat)** "
        f"— Pacific calendar week, UTC-aligned buckets"
    )
    lines.append("")
    lines.append(
        "| Week of | LLM costs | LLM cost per active user | Voice costs | "
        "Processing costs | Margin ($) | Margin (%) |"
    )
    lines.append("|---|---:|---|---:|---|---|---|")
    lines.append(
        f"| {label} | **{fmt_usd(llm_usd)}** | — | "
        f"**{fmt_usd(voice_usd)}** | — | — | — |"
    )
    lines.append("")
    lines.append(
        "**TSV (paste into the sheet, starting at the `Week of` column):**"
    )
    lines.append("")
    lines.append("```")
    # Six target columns after `Week of`: LLM, LLM/user, Voice, Processing,
    # Margin $, Margin %. We fill LLM (1) and Voice (3); columns 2/4/5/6 are
    # blank so positions still line up if pasted into the live sheet.
    lines.append(
        f"{label}\t{fmt_usd(llm_usd)}\t\t{fmt_usd(voice_usd)}\t\t\t"
    )
    lines.append("```")
    return "\n".join(lines)


def render_voice_vendor_breakdown(
    elevenlabs_usd: float,
    vendor_totals: dict,
    rates: dict,
    notes: list[str],
    elevenlabs_split: tuple[float, float, float] | None = None,
) -> str:
    """Additive per-vendor voice split for the week (ElevenLabs vs LiveKit path).

    ElevenLabs is the actual billed value; Cartesia / Fish / Deepgram / LiveKit are
    DERIVED from PRICING-SHEET ESTIMATE rates (clearly labelled). When
    `elevenlabs_split` (agent_usd, intelligence_usd, livekit_tts_usd) is provided,
    ElevenLabs is shown split by its three CALLERS (ENG-982) — the ConvAI agent
    bundle, the Intelligence Service raw-TTS path mobile calls, and the LiveKit
    agent's TTS — since all three bill the same vendor."""
    ct = vendor_totals.get("cartesia", 0.0)
    fh = vendor_totals.get("fish", 0.0)
    iw = vendor_totals.get("inworld", 0.0)
    dg = vendor_totals.get("deepgram", 0.0)
    lk = vendor_totals.get("livekit", 0.0)
    livekit_path = ct + fh + iw + dg + lk
    voice_total = elevenlabs_usd + livekit_path

    lines: list[str] = []
    lines.append("")
    lines.append("**Voice breakdown by vendor** (ElevenLabs vs LiveKit path)")
    lines.append("")
    lines.append("| Vendor | Spend | Basis |")
    lines.append("|---|---:|---|")
    if elevenlabs_split is not None:
        el_agent, el_intel, el_tts = elevenlabs_split
        lines.append(f"| ElevenLabs — ConvAI agent | {fmt_usd(el_agent)} | actual (fiat, has_api_key=False) |")
        lines.append(f"| ElevenLabs — Intelligence Service TTS | {fmt_usd(el_intel)} | actual (fiat, by calling key) |")
        lines.append(f"| ElevenLabs — LiveKit TTS | {fmt_usd(el_tts)} | actual (fiat, by calling key) |")
    else:
        lines.append(f"| ElevenLabs (ConvAI) | {fmt_usd(elevenlabs_usd)} | actual (fiat_units_spent) |")
    lines.append(f"| Cartesia (TTS) | {fmt_usd(ct)} | derived, ESTIMATE ${rates.get('cartesia', 0):.2f}/M credits |")
    # Fish / Inworld default to the store's rates, so they are None when `store` failed to
    # import — every day is then read from the store and no rate was applied.
    fh_rate, iw_rate = rates.get("fish"), rates.get("inworld")
    fh_basis = f"derived, ESTIMATE ${fh_rate:.2f}/M bytes" if fh_rate is not None else "as stored"
    iw_basis = f"usage API characters × ${iw_rate:.2f}/M" if iw_rate is not None else "as stored"
    lines.append(f"| Fish Audio (TTS) | {fmt_usd(fh)} | {fh_basis} |")
    lines.append(f"| Inworld (TTS) | {fmt_usd(iw)} | {iw_basis} |")
    lines.append(f"| Deepgram (STT) | {fmt_usd(dg)} | derived, ESTIMATE ${rates.get('deepgram', 0):.3f}/hr |")
    lines.append(f"| LiveKit (transport) | {fmt_usd(lk)} | derived, ESTIMATE ${rates.get('livekit', 0):.3f}/min |")
    lines.append(f"| **LiveKit path (Cartesia+Fish+Inworld+Deepgram+LiveKit)** | **{fmt_usd(livekit_path)}** | derived |")
    lines.append(f"| **Total voice** | **{fmt_usd(voice_total)}** | |")
    lines.append("")
    lines.append(
        "_LiveKit-path vendor $ is DERIVED from PRICING-SHEET ESTIMATE rates — "
        "replace with contract rates via `--cartesia-rate-per-million` / "
        "`--fish-rate-per-million` / `--inworld-rate-per-million` / "
        "`--deepgram-rate-per-hour` / `--livekit-rate-per-min`. ElevenLabs $ is "
        "the actual billed value._"
    )
    for n in notes:
        lines.append(f"_note: {n}_")
    return "\n".join(lines)


def render_ai_tier_breakdown(
    by_tier: dict[str, float],
    users_by_tier: dict[str, set],
    llm_total: float,
) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append("**LLM breakdown by AI premium tier**")
    lines.append("")
    lines.append("| Tier | Spend | Share | Active users | $/user |")
    lines.append("|---|---:|---:|---:|---:|")

    for tier in sorted(by_tier.keys(), key=_tier_sort_key):
        amount = by_tier[tier]
        if amount <= 0:
            continue
        pct = amount / llm_total * 100 if llm_total else 0
        active = len(users_by_tier.get(tier) or set())
        per_user_cell = fmt_usd(amount / active) if active else "—"
        active_cell = f"{active:,}" if active else "—"
        lines.append(
            f"| {tier} | {fmt_usd(amount)} | {pct:.1f}% | "
            f"{active_cell} | {per_user_cell} |"
        )
    return "\n".join(lines)


def render_bq_vendor_breakdown(vendor: dict) -> str:
    """Per-vendor voice split from the daily cost store (all values as stored)."""
    el_agent = vendor.get("el_agent", 0.0)
    el_intel = vendor.get("el_intelligence", 0.0) or 0.0
    el_livekit = vendor.get("el_livekit", 0.0)
    ct = vendor.get("cartesia", 0.0)
    fh = vendor.get("fish", 0.0)
    iw = vendor.get("inworld", 0.0)
    dg = vendor.get("deepgram", 0.0)
    lk = vendor.get("livekit", 0.0)
    total = el_agent + el_intel + el_livekit + ct + fh + iw + dg + lk
    lines = [
        "",
        "**Voice breakdown by vendor** (from the daily cost store)",
        "",
        "| Vendor | Spend | Basis |",
        "|---|---:|---|",
        f"| ElevenLabs — ConvAI agent | {fmt_usd(el_agent)} | actual billed |",
        f"| ElevenLabs — Intelligence Service TTS | {fmt_usd(el_intel)} | actual billed |",
        f"| ElevenLabs — LiveKit TTS | {fmt_usd(el_livekit)} | actual billed |",
        f"| Cartesia (TTS) | {fmt_usd(ct)} | usage actual, $ derived from rate |",
        f"| Fish Audio (TTS) | {fmt_usd(fh)} | agent-logged usage, $ derived from rate |",
        f"| Inworld (TTS) | {fmt_usd(iw)} | usage actual, $ derived from rate |",
        f"| Deepgram (STT) | {fmt_usd(dg)} | usage actual, $ derived from rate |",
        f"| LiveKit (transport) | {fmt_usd(lk)} | usage actual, $ derived from rate |",
        f"| **Total voice** | **{fmt_usd(total)}** | ≈ voice cost above |",
    ]
    return "\n".join(lines)


def _bq_tier_rows(tiers: list[dict], key: str, total: float) -> list[str]:
    """Render one row per tier, then a Total row that must equal the parts.

    Only an exactly-zero tier is skipped. A NEGATIVE one is rendered: the store's
    `other` bucket is a reconciling remainder that legitimately swings either way,
    and dropping it on sign made the table silently not add up — voice `other` of
    -$15.88 for the week of 9/20 left a table of $2,232.22 under a $2,216.34 total,
    while the LLM table beside it (positive `other`) summed correctly. The Total row
    exists so any future mismatch is visible in the output instead of being left for
    a reader to catch by hand.
    """
    out: list[str] = []
    for t in tiers:
        amount = t[key]
        if not amount:
            continue
        pct = amount / total * 100 if total else 0
        active = t["active_users"]
        if active:
            per_user = fmt_usd(amount / active)
            active_cell = f"{active:,}"
        else:
            per_user = "—"
            active_cell = "—"
        out.append(
            f"| {t['label']} | {fmt_usd(amount)} | {pct:.1f}% | "
            f"{active_cell} | {per_user} |"
        )
    parts = sum(t[key] for t in tiers)
    out.append(f"| **Total** | **{fmt_usd(parts)}** | | | |")
    # Belt and braces: if the parts ever stop matching the total the caller
    # computed, say so in the table rather than letting the two drift quietly.
    if total and abs(parts - total) >= 0.01:
        out.append(
            f"| _mismatch vs reported total_ | _{fmt_usd(total)}_ | | | |"
        )
    return out


def render_bq_tier_breakdown(
    tiers: list[dict], llm_total: float, voice_total: float
) -> str:
    """LLM + voice breakdown by the store's 5-bucket subscription_state.

    Explicitly NOT the 7-bucket aiPremium* taxonomy of the live-API path —
    labelled so a reader doesn't compare the two as if they were the same cut.
    """
    lines = [
        "",
        "**LLM breakdown by subscription tier** "
        "(BigQuery — 5-bucket `subscription_state`)",
        "",
        "| Tier | Spend | Share | Active users | $/user |",
        "|---|---:|---:|---:|---:|",
    ]
    lines += _bq_tier_rows(tiers, "llm", llm_total)
    lines += [
        "",
        "**Voice breakdown by subscription tier** "
        "(BigQuery — 5-bucket `subscription_state`)",
        "",
        "| Tier | Voice spend | Share | Active users | $/user |",
        "|---|---:|---:|---:|---:|",
    ]
    lines += _bq_tier_rows(tiers, "voice", voice_total)
    return "\n".join(lines)


def render_paid_split(tiers: list[dict], llm_usd: float, voice_usd: float) -> str:
    """The deliverable: spend, and how many DOLLARS of it are paid vs non-paid.

    Dollars lead and percentages are dropped. The question this skill exists to
    answer is how much money sits on each side; a share invites the reader to
    reconstruct the dollars, which is where a mismatched basis slips through
    unnoticed. Every column totals explicitly so the table proves it reconciles.

    `paid` is the only paid bucket. `grace` (failing renewal) counts as non-paid
    but is named in the composition line, since it is neither a free user nor
    collected revenue and a reader may want it on the other side.
    """
    by = {t['state']: t for t in tiers}

    def g(state: str, key: str) -> float:
        t = by.get(state)
        return float(t[key]) if t else 0.0

    rows = []
    for key, name, total in (("llm", "LLM", llm_usd), ("voice", "Voice", voice_usd)):
        paid = g("paid", key)
        rows.append((name, paid, total - paid, total))

    paid_all = sum(r[1] for r in rows)
    non_paid_all = sum(r[2] for r in rows)
    total_all = sum(r[3] for r in rows)

    out = ["", "**Spend — paid vs non-paid**", "",
           "| | Paid | Non-paid | Total |", "|---|---:|---:|---:|"]
    for name, paid, non_paid, total in rows:
        out.append(f"| {name} | {fmt_usd(paid)} | {fmt_usd(non_paid)} | {fmt_usd(total)} |")
    out.append(f"| **Total** | **{fmt_usd(paid_all)}** | **{fmt_usd(non_paid_all)}** | "
               f"**{fmt_usd(total_all)}** |")

    parts = []
    for state in ("free", "trial", "grace", "other"):
        amt = g(state, "llm") + g(state, "voice")
        if amt:
            parts.append(f"{BQ_STATE_LABELS.get(state, state)} {fmt_usd(amt)}")
    if parts:
        joined = " + ".join(parts)
        out += ["", f"_Non-paid = {joined}._"]
    # `Other` is spend nobody owns, so it lands in non-paid by default. Normally that is
    # pennies; when a whole cost stream is booked there (the LiveKit pool overage was,
    # 2026-10-15 to 10-25: 12% of voice) the paid share reads low with nothing saying so.
    other_voice = g("other", "voice")
    if voice_usd > 0 and other_voice / voice_usd > OTHER_VOICE_WARN_SHARE:
        attributed = voice_usd - other_voice
        paid_of_attributed = g("paid", "voice") / attributed if attributed > 0 else 0.0
        out += ["", f"_WARNING: `Other` holds {other_voice / voice_usd:.1%} of voice "
                    f"({fmt_usd(other_voice)}) and is counted as non-paid. Paid is "
                    f"{paid_of_attributed:.1%} of the voice that IS attributed — check which "
                    f"vendor column landed on `Other` before quoting a paid share._"]
    return "\n".join(out)


def run_bigquery(args, start: datetime, end: datetime, label: str) -> None:
    """--from-bigquery path: read the daily cost store instead of the live APIs.

    Credentials are resolved inside the bq helper — GCP_KEY first, then
    Application Default Credentials. Deliberately no env precheck here: this
    used to exit on an unset GCP_KEY alone, which told operators BigQuery was
    unavailable on machines with a perfectly good `gcloud` login and pushed
    them onto a ~20-minute vendor-API walk. Let the helper try both routes and
    report only if both fail.
    """

    print(
        f"# Reading weekly cost from BigQuery {BQ_TABLE} for {label} "
        f"({start.date()} → {end.date()}, UTC dates)…",
        file=sys.stderr,
    )
    try:
        data = fetch_bigquery_week(start, end)
    except Exception as e:  # noqa: BLE001 — BQUnavailable / BQError → clean exit
        sys.exit(f"BigQuery read failed: {e}")

    llm_usd = data["llm_usd"]
    voice_usd = data["voice_usd"]

    md = render_markdown(label, start, end, llm_usd, voice_usd)
    # The store voice figure is all-vendor, NOT the billed ElevenLabs cell the
    # spreadsheet column is built from. render_markdown labels its TSV "paste into
    # the sheet", which is true of the live path and a trap here, so relabel it.
    md = md.replace(
        "**TSV (paste into the sheet, starting at the `Week of` column):**",
        "**TSV — store basis. Do NOT paste as the billed row; take that from the "
        "live path (voice here is all-vendor, not ElevenLabs-billed):**",
    )
    md += (
        f"\n\n> _Store basis (`{BQ_TABLE}`): every voice vendor, per user. LLM ties "
        "to the OpenAI org total to the penny; voice runs ~1–2.5% under ElevenLabs' "
        "billed figure. `--detail` adds the per-vendor and 5-bucket tables._"
    )
    if not args.no_tier_breakdown:
        md += "\n" + render_paid_split(data["tiers"], llm_usd, voice_usd)
    # The per-vendor and 5-bucket tables are diagnostics, not the deliverable:
    # they answer 'why did this move', a different question from 'what did it
    # cost and how much of that was paid'. Behind --detail so the default output
    # stays short enough to actually read.
    if args.detail:
        if not args.no_voice_vendor_breakdown:
            md += "\n" + render_bq_vendor_breakdown(data["vendor"])
        if not args.no_tier_breakdown:
            md += "\n" + render_bq_tier_breakdown(data["tiers"], llm_usd, voice_usd)
    for n in data["notes"]:
        md += f"\n\n_note: {n}_"
    print(md)

    if args.json_out:
        payload = {
            "source": "bigquery",
            "table": BQ_TABLE,
            "week_of": label,
            "start_iso": start.isoformat(),
            "end_iso": end.isoformat(),
            "llm_cost_usd": round(llm_usd, 4),
            "voice_cost_usd": round(voice_usd, 4),
            "total_cost_usd": round(data["total_usd"], 4),
            "voice_by_vendor": {k: round(v, 4) for k, v in data["vendor"].items()},
            "by_subscription_state": [
                {
                    "state": t["state"],
                    "label": t["label"],
                    "llm_usd": round(t["llm"], 4),
                    "voice_usd": round(t["voice"], 4),
                    "active_users": t["active_users"],
                }
                for t in data["tiers"]
            ],
            "notes": data["notes"],
        }
        with open(args.json_out, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"# JSON written to {args.json_out}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # Force UTF-8 stdout so the "—", arrows, and bullets in the rendered
    # Markdown survive Windows consoles that default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description=(
            "Weekly Aurie cost report (LLM + Voice) for a Sun–Sat week. "
            "LLM cost from OpenAI /v1/organization/costs; voice cost from "
            "ElevenLabs /v1/usage/character-stats fiat_units_spent metric "
            "(authoritative billed amount, no rate assumptions). "
            "By default also renders an LLM-spend breakdown by AI premium "
            "tier (--no-tier-breakdown to skip). "
            "Default boundary detection uses America/Los_Angeles (PDT/PST); "
            "the query window is always UTC-aligned daily buckets."
        )
    )
    ap.add_argument(
        "--week",
        help=(
            "Sunday of the week to report (YYYY-MM-DD, calendar date). "
            "Defaults to the most recent completed Sunday in Pacific time."
        ),
    )
    ap.add_argument(
        "--since",
        help="Start date (YYYY-MM-DD), inclusive, queried at UTC midnight. "
        "Use with --until. Bypasses the retention guard.",
    )
    ap.add_argument(
        "--until",
        help="End date (YYYY-MM-DD), exclusive, queried at UTC midnight. "
        "Use with --since.",
    )
    ap.add_argument(
        "--no-tier-breakdown",
        action="store_true",
        help=(
            "Skip the per-tier LLM breakdown. Faster (no per-project "
            "chat-completions walk) — use when you only need the TSV row."
        ),
    )
    ap.add_argument(
        "--cartesia-rate-per-million", type=float,
        default=(getattr(_store, "DEFAULT_CARTESIA_RATE_PER_MILLION", 38.00)),
        help="ESTIMATE — USD per 1M Cartesia credits (default 38.00, Scale overage rate).",
    )
    ap.add_argument(
        "--fish-rate-per-million", type=float,
        default=(getattr(_store, "DEFAULT_FISH_RATE_PER_MILLION", None)),
        help="ESTIMATE — USD per 1M Fish Audio UTF-8 bytes (default: the store skill's rate).",
    )
    ap.add_argument(
        "--inworld-rate-per-million", type=float,
        default=(getattr(_store, "DEFAULT_INWORLD_RATE_PER_MILLION", None)),
        help="USD per 1M Inworld TTS characters (default: the store skill's rate).",
    )
    ap.add_argument(
        "--deepgram-rate-per-hour", type=float,
        default=(getattr(_store, "DEFAULT_DEEPGRAM_RATE_PER_HOUR", 0.348)),
        help="USD per Deepgram audio-hour (default 0.348, Nova-3 Multilingual streaming PAYG).",
    )
    ap.add_argument(
        "--livekit-rate-per-min", type=float,
        default=(getattr(_store, "DEFAULT_LIVEKIT_RATE_PER_MIN", 0.01)),
        help="ESTIMATE — USD per LiveKit session-minute (default 0.01, LiveKit Cloud Scale).",
    )
    ap.add_argument(
        "--no-voice-vendor-breakdown", action="store_true",
        help="Skip the additive per-vendor voice split (ElevenLabs vs LiveKit path).",
    )
    ap.add_argument(
        "--from-bigquery", action="store_true", dest="from_bigquery",
        help=(
            "Source the report from the BigQuery daily cost store "
            "(aura-main.aurie_cost.aurie_per_user_daily_cost) instead of the live "
            "OpenAI/ElevenLabs APIs. Requires GCP_KEY (not the OpenAI/ElevenLabs "
            "keys); bypasses the ElevenLabs 10-day retention guard so older / "
            "late-run weeks work, and makes the voice tier breakdown instant. Tier "
            "tables use the store's 5-bucket subscription_state, not the 7-bucket "
            "AI-premium taxonomy."
        ),
    )
    ap.add_argument(
        "--detail", action="store_true",
        help=(
            "Add the per-vendor voice table and the 5-bucket subscription_state "
            "tables to the --from-bigquery output. Diagnostics for explaining a "
            "move; the default answers what it cost and how much of that was paid."
        ),
    )
    ap.add_argument(
        "--json",
        dest="json_out",
        help="Also write structured JSON to this PATH.",
    )
    ap.add_argument("--allow-degraded-attribution", action="store_true",
                   help="Accept degraded per-user attribution when the LLM ledger is "
                        "unreachable on a post-2026-07-23 window (default: exit 2). "
                        "Env: AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1")
    args = ap.parse_args()
    _ALLOW_DEGRADED[0] = _ALLOW_DEGRADED[0] or bool(
        getattr(args, "allow_degraded_attribution", False))

    # --from-bigquery is a self-contained path: it reads the persisted daily cost
    # store, so it needs neither the OpenAI/ElevenLabs keys nor the retention
    # guard. Dispatch before the live-API key check.
    if args.from_bigquery:
        start, end, label, _is_explicit = resolve_window(
            args.week, args.since, args.until, skip_retention=True
        )
        run_bigquery(args, start, end, label)
        return

    openai_key = os.environ.get("OPENAI_ADMIN_API_KEY")
    elevenlabs_key = os.environ.get("ELEVENLABS_ADMIN_API_KEY")
    missing = []
    if not openai_key:
        missing.append("OPENAI_ADMIN_API_KEY")
    if not elevenlabs_key:
        missing.append("ELEVENLABS_ADMIN_API_KEY")
    if missing:
        sys.exit(
            f"Missing required env var(s): {', '.join(missing)}. "
            "Add them to ~/.claude/settings.json → env and restart Claude Code."
        )

    start, end, label, _is_explicit = resolve_window(
        args.week, args.since, args.until
    )

    print(
        f"# Fetching LLM + Voice costs for {label} "
        f"({start.date()} → {end.date()}, UTC half-open)…",
        file=sys.stderr,
    )

    llm_usd, per_project_dollars = fetch_openai_by_project(start, end, openai_key)
    voice_usd = fetch_elevenlabs_cost_usd(start, end, elevenlabs_key)
    # ElevenLabs role split (ConvAI agent vs LiveKit-path TTS) — additive; sums to
    # voice_usd. ElevenLabs serves both roles, so the single figure is decomposed.
    el_agent_usd, el_intel_usd, el_tts_usd = fetch_elevenlabs_split_usd(
        start, end, elevenlabs_key)

    # LiveKit-path vendor totals for the week (Cartesia / Fish / Inworld / Deepgram / LiveKit),
    # DERIVED from ESTIMATE rates. Additive — the ElevenLabs figure above is
    # unchanged; this surfaces the per-vendor split.
    vendor_rates = {"cartesia": args.cartesia_rate_per_million,
                    "fish": args.fish_rate_per_million,
                    "inworld": args.inworld_rate_per_million,
                    "deepgram": args.deepgram_rate_per_hour,
                    "livekit": args.livekit_rate_per_min}
    vendor_totals: dict = {k: 0.0 for k in LIVEKIT_PATH_VENDORS}
    vendor_notes: list[str] = []
    if not args.no_voice_vendor_breakdown:
        print("# Fetching LiveKit-path vendor totals (Cartesia/Fish/Inworld/Deepgram/LiveKit)…",
              file=sys.stderr)
        vendor_totals, vendor_notes = fetch_livekit_path_week(start, end, vendor_rates)

    rollout_warning = None
    by_tier: dict[str, float] = {}
    users_by_tier: dict[str, set] = {}
    attribution_source: str | None = None

    if not args.no_tier_breakdown:
        rollout_warning = rollout_window_warning(start, end)
        print(
            f"# Allocating LLM spend across AI premium tiers "
            f"({len([p for p, d in per_project_dollars.items() if d > 0])} "
            f"project(s) with spend)…",
            file=sys.stderr,
        )

        # Ledger-first attribution: split the window at the ledger epoch.
        #   [start, pre_end)   — stored-completions walk (only source pre-epoch)
        #   [post_start, end)  — IS LLM-call ledger (primary), with failover to
        #                        the stored-completions walk on any read failure.
        ledger_epoch = datetime.fromtimestamp(LEDGER_EPOCH_UNIX, tz=timezone.utc)
        pre_end = min(end, ledger_epoch)
        post_start = max(start, ledger_epoch)
        has_pre = start < pre_end
        has_post = post_start < end

        ledger_by_project_key: dict[str, list[dict]] | None = None
        if has_post:
            try:
                ledger_rows = iter_llm_calls(
                    int(post_start.timestamp()),
                    int(end.timestamp()),
                    provider="openai",
                )
                ledger_by_project_key = defaultdict(list)
                for row in ledger_rows:
                    key = row.get("project") or "(unknown)"
                    ledger_by_project_key[key].append(row)
                print(
                    f"# Ledger: {len(ledger_rows)} call(s) for "
                    f"{post_start.isoformat()} → {end.isoformat()}",
                    file=sys.stderr,
                )
            except LedgerUnavailable as exc:
                _fail_closed_on_ledger_outage(exc)
                print(
                    f"WARNING: ledger unavailable ({exc}): falling back to "
                    f"stored chat completions for {post_start.isoformat()} → "
                    f"{end.isoformat()}. NOTE: the completions fallback is "
                    "chat-API-only and cannot see Responses-API traffic.",
                    file=sys.stderr,
                )
                ledger_by_project_key = None

        if not has_post:
            attribution_source = (
                "stored chat completions "
                "(window predates ledger epoch 2026-07-23T03:25Z)"
            )
        elif ledger_by_project_key is None:
            attribution_source = (
                "stored chat completions (ledger unavailable — chat-API-only "
                "fallback; Responses-API traffic not visible)"
            )
        elif has_pre:
            attribution_source = (
                "mixed (window straddles ledger epoch 2026-07-23T03:25Z: "
                "pre-epoch from stored chat completions, post-epoch from the "
                "LLM ledger)"
            )
        else:
            attribution_source = "LLM ledger (BigQuery llm.llm_call, per-call rows)"

        # PROD-3572: the stored-completions walk needs a project-scoped key
        # (Admin key can't hold `model.request`). Resolve names via the admin
        # key, then per project resolve OPENAI_<PROJECT>_API_KEY.
        project_names = fetch_projects(openai_key)
        per_project_completions: dict[str, list[dict]] = {}
        for pid, dollars in per_project_dollars.items():
            if dollars <= 0 or pid == "(unattributed)":
                continue
            project_name = project_names.get(pid, pid)
            ledger_key = None
            if ledger_by_project_key is not None:
                candidate_key = _ledger_project_key(project_name)
                if candidate_key in LEDGER_PROJECT_KEYS:
                    ledger_key = candidate_key

            merged: list[dict] = []
            seen_ids: set[str] = set()
            partial_epoch_unsafe = False

            if ledger_key is not None:
                for row in ledger_by_project_key.get(ledger_key, []):
                    item = _ledger_row_to_completion(row)
                    oid = item.get("id")
                    if oid:
                        if oid in seen_ids:
                            continue
                        seen_ids.add(oid)
                    merged.append(item)
                # Ledger covers [post_start, end); walk the pre-epoch slice only.
                walk_end = pre_end
            else:
                # Ledger failed, or non-production project (not in the ledger)
                # → the stored-completions walk covers the whole window.
                walk_end = end

            if start < walk_end:
                completions_key = _resolve_completions_api_key(project_name)
                if completions_key:
                    try:
                        walked = iter_completions(
                            start, walk_end, pid, completions_key
                        )
                    except SystemExit:
                        raise
                    except Exception as e:  # noqa: BLE001
                        print(
                            f"⚠️  Failed to fetch completions for project "
                            f"{pid}: {e}",
                            file=sys.stderr,
                        )
                        walked = []
                        partial_epoch_unsafe = True
                    # Defensive dedupe on the OpenAI object id — ledger
                    # openaiId == stored completion id at boundary overlap.
                    for item in walked:
                        oid = item.get("id")
                        if oid:
                            if oid in seen_ids:
                                continue
                            seen_ids.add(oid)
                        merged.append(item)
                else:
                    partial_epoch_unsafe = True

            if has_pre and has_post and partial_epoch_unsafe:
                print(
                    f"WARNING: incomplete epoch-straddling attribution for "
                    f"{project_name} ({pid}); folding project dollars into "
                    "the untagged tier instead of allocating the full Costs-API "
                    "total across a partial slice.",
                    file=sys.stderr,
                )
                per_project_completions[pid] = []
            else:
                per_project_completions[pid] = merged
        by_tier, users_by_tier = allocate_by_ai_tier(
            per_project_dollars, per_project_completions
        )

    md = render_markdown(label, start, end, llm_usd, voice_usd)
    if not args.no_voice_vendor_breakdown:
        md += "\n" + render_voice_vendor_breakdown(
            voice_usd, vendor_totals, vendor_rates, vendor_notes,
            elevenlabs_split=(el_agent_usd, el_intel_usd, el_tts_usd),
        )
    if not args.no_tier_breakdown:
        md += "\n" + render_ai_tier_breakdown(by_tier, users_by_tier, llm_usd)
        if attribution_source:
            md += f"\n\n_Attribution source: {attribution_source}._"
        if rollout_warning:
            md = rollout_warning + "\n\n" + md
    print(md)

    if args.json_out:
        payload: dict = {
            "week_of": label,
            "start_iso": start.isoformat(),
            "end_iso": end.isoformat(),
            "llm_cost_usd": round(llm_usd, 4),
            # voice_cost_usd stays the ElevenLabs figure for backward-compat with
            # existing consumers; the multi-vendor split is under voice_by_vendor.
            "voice_cost_usd": round(voice_usd, 4),
        }
        if not args.no_voice_vendor_breakdown:
            lk_path = sum(vendor_totals[k] for k in LIVEKIT_PATH_VENDORS)
            payload["voice_by_vendor"] = {
                "elevenlabs_usd": round(voice_usd, 4),
                "elevenlabs_agent_usd": round(el_agent_usd, 4),
                "elevenlabs_intelligence_usd": round(el_intel_usd, 4),
                "elevenlabs_livekit_usd": round(el_tts_usd, 4),
                "cartesia_usd": round(vendor_totals["cartesia"], 4),
                "fish_usd": round(vendor_totals["fish"], 4),
                "inworld_usd": round(vendor_totals["inworld"], 4),
                "deepgram_usd": round(vendor_totals["deepgram"], 4),
                "livekit_usd": round(vendor_totals["livekit"], 4),
                "livekit_path_usd": round(lk_path, 4),
                "total_voice_usd": round(voice_usd + lk_path, 4),
                "rates_estimate": vendor_rates,
                "notes": vendor_notes,
            }
        if not args.no_tier_breakdown:
            payload["attribution_source"] = attribution_source
            payload["by_ai_premium_tier"] = [
                {
                    "tier": tier,
                    "amount": round(by_tier[tier], 4),
                    "active_users": len(users_by_tier.get(tier) or set()),
                }
                for tier in sorted(by_tier.keys(), key=_tier_sort_key)
                if by_tier[tier] > 0
            ]
            payload["rollout"] = {
                "tagging_started_iso": datetime.fromtimestamp(
                    TAGGING_ROLLOUT_UNIX, tz=timezone.utc
                ).isoformat(),
                "window_status": (
                    "post"
                    if int(start.timestamp()) >= TAGGING_ROLLOUT_UNIX
                    else "pre"
                    if int(end.timestamp()) <= TAGGING_ROLLOUT_UNIX
                    else "mixed"
                ),
                "warning": rollout_warning,
            }
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"# JSON written to {args.json_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
