#!/usr/bin/env python3
"""
OpenAI spend report ranked by individual Aura user (Firebase UID).

Pulls authoritative per-project dollars from the OpenAI Costs API (same source
as `eng:analyze-openai-costs`), then iterates each project's stored chat
completions and proportionally allocates the dollars across users by token
weight. Ranks users globally, with subscription tier as an annotation on each
top row, plus a distribution-stats block (mean / median / p90 / p95 / p99 /
max / stddev) so the reader can frame the top spenders against the long tail.

Each user's tier (AI premium + content premium) is the **last-seen** tier
across their completions in the window — current state, not a per-call
average. AI-dimension tier labels carry an "AI " prefix
(`AI Premium Paid`, `Non AI Premium`, …); content-dimension labels stay
unprefixed (`Premium Paid`, `Non Premium`, …) since those are the
canonical Aura labels. `--tier` infers the dimension from the label's
prefix so the same flag works for both cuts.

Usage:
    python report.py                                      # month-to-date, top 5
    python report.py --window previous-month
    python report.py --window last-7d
    python report.py --window last-30d
    python report.py --since 2026-04-01 --until 2026-05-01
    python report.py --top 25                             # show top 25 instead of default 5
    python report.py --tier "AI Premium Free"             # filter on AI dimension (Aurie)
    python report.py --tier "Premium Free"                # filter on content dimension (Aura)
    python report.py --no-stats                           # suppress distribution block
    python report.py --no-headline                        # suppress headline-outliers paragraph
    python report.py --json out.json                      # structured JSON

Attribution sources (dollars always come from the Costs API; only the
token-weight/attribution walk varies):

  - Post-epoch slice (on/after 2026-07-23T03:25Z): the IntelligenceService
    per-call LLM ledger (BigQuery llm.llm_call) is
    PRIMARY — one row per OpenAI call, covering both Chat Completions and
    Responses APIs. On ledger failure the script warns on stderr and falls
    back to the stored-chat-completions walk (chat-API-only).
  - Pre-epoch slice: always the stored-chat-completions walk — the ledger has
    no rows before the epoch.

Env:
    OPENAI_ADMIN_API_KEY  (Admin API key, sk-admin-...; required)
        Used only for /v1/organization/* (costs + projects list). Still valid.
    GCP_KEY  (service account)
        Required for the ledger read (BigQuery llm.llm_call). When unset, the
        post-epoch slice falls back to the stored-completions walk with a
        stderr warning.
    OPENAI_<PROJECT>_API_KEY  (project-scoped service-account keys; PROD-3572)
        Required for the per-project stored-completions walk
        (GET /v1/chat/completions). OpenAI enforces the `model.request` scope
        there, which Admin keys cannot hold — so the walk uses a project-scoped
        key resolved from the project display name, e.g.
        "Memory [Production]" -> OPENAI_MEMORY_API_KEY,
        "Conversation [Production]" -> OPENAI_CONVERSATION_API_KEY.
        A project whose key is unset is skipped (its dollars fold into the
        untagged/unattributed bucket) rather than hard-failing the report.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
import pathlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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

OPENAI_API = "https://api.openai.com/v1"
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

COSTS_PAGE_SIZE = 180
COMPLETIONS_PAGE_SIZE = 100

LEDGER_EPOCH_UNIX = 1784777100  # 2026-07-23T03:25:00Z — the ledger went live in Release 0.285.0. There are NO ledger rows before this moment.
# 2026-07-24T00:00:00Z — end of the ledger ROLL-OUT RAMP. The ledger existed from
# the epoch above but was only partially populated for the rest of that first day:
# measured over the same post-epoch clock window, it held 701 distinct users on
# 07-23 vs 2,549 on 07-24, uniformly ~3x thin across every project (feed 59 vs
# 1,221; notification 562 vs 2,045). Trusting it alone for that slice dropped ~1,600
# users, and because per-user cost is an allocation of the authoritative day total
# by token share, the day's whole $260 was re-spread over the survivors — inflating
# per-user cost ~2.7x. Inside this window we therefore union the ledger WITH stored
# completions, which still hold that traffic (the Responses-API migration, 0.287.x,
# shipped after the ledger release, so 07-23 was still overwhelmingly Chat
# Completions). Deduping on `id` makes the union safe — see collect_project_items.
# Scoped to the ramp so the steady-state path keeps its single source and doesn't
# pay for a second walk per project per day. [ENG-991]
LEDGER_RAMP_END_UNIX = 1784851200

DEFAULT_TOP_N = 5

# Date the IntelligenceService metadata-tagging PRs (ENG-498 for aiPremium*,
# ENG-500 for premium*) shipped to production. Calls stored before this date
# carry NONE of the four subscription-state fields — they pre-date the code
# that writes them. The cost-cohort skills warn at runtime when a requested
# window predates or straddles this boundary so readers don't misread the
# inflated (untagged) slice as a tagging regression. See `Tools/OpenAI` →
# "Rollout date and the data gap" for the full caveat.
TAGGING_ROLLOUT_UNIX = int(
    datetime(2026, 5, 12, tzinfo=timezone.utc).timestamp()
)

# Tier labels are dimension-specific so a reader (or a `--tier` filter) can
# tell which subscription the row refers to: Aurie's AI premium
# (metadata.aiPremium*) carries an "AI " prefix; Aura's content premium
# (metadata.premium*) keeps the canonical unprefixed labels. Kept in sync
# with `eng:analyze-openai-cost-per-subscription-state` and
# `eng:report-aurie-weekly-costs` per AINative architecture principle #1
# (skills are self-contained; copies are kept consistent by hand).
AI_TIER_LABELS = {
    "paid": "AI Premium Paid",
    "trial": "AI Premium Trial",
    "free": "AI Premium Free",
    "paymentPending": "AI Premium Payment Pending",
}

CONTENT_TIER_LABELS = {
    "paid": "Premium Paid",
    "trial": "Premium Trial",
    "free": "Premium Free",
    "paymentPending": "Premium Payment Pending",
}

# Accept both AI- and content-dimension labels for `--tier`. The caller (most
# often `eng:analyze-openai-cost-per-subscription-state` chaining a drill-down)
# passes the exact label rendered in the per-state table, so this set is the
# union of both dimensions' tier labels plus the shared sentinels.
VALID_TIER_FILTERS = {
    # AI dimension (Aurie)
    "AI Premium Paid",
    "AI Premium Trial",
    "AI Premium Free",
    "AI Premium Payment Pending",
    "Non AI Premium",
    "Never AI Premium",
    # Content dimension (Aura)
    "Premium Paid",
    "Premium Trial",
    "Premium Free",
    "Premium Payment Pending",
    "Non Premium",
    # Shared
    "(untagged)",
}


# ---------------------------------------------------------------------------
# Time-window resolution (identical to sibling skills)
# ---------------------------------------------------------------------------

def resolve_window(window: str | None, since: str | None, until: str | None) -> tuple[int, int, str]:
    now = datetime.now(tz=timezone.utc)

    if since or until:
        if not (since and until):
            sys.exit("--since and --until must be provided together")
        start_dt = _parse_date(since)
        end_dt = _parse_date(until)
        label = f"{since} → {until}"
    elif window == "previous-month":
        first_of_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_any_day = first_of_this - timedelta(days=1)
        start_dt = prev_month_any_day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = first_of_this
        label = f"previous month ({start_dt.strftime('%B %Y')})"
    elif window == "last-7d":
        end_dt = now
        start_dt = now - timedelta(days=7)
        label = "last 7 days"
    elif window == "last-30d":
        end_dt = now
        start_dt = now - timedelta(days=30)
        label = "last 30 days"
    else:
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        label = f"month to date ({start_dt.strftime('%B %Y')})"

    if start_dt >= end_dt:
        sys.exit(f"Empty window: start {start_dt.isoformat()} >= end {end_dt.isoformat()}")

    return int(start_dt.timestamp()), int(end_dt.timestamp()), label


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit(f"Bad date {s!r} — expected YYYY-MM-DD (UTC)")


# ---------------------------------------------------------------------------
# Project-scoped API key resolution for the stored-completions walk (PROD-3572)
# ---------------------------------------------------------------------------

def _project_base_name(project_name: str) -> str:
    """"Evaluations [Production]" -> "Evaluations" (env qualifier stripped)."""
    return re.sub(r"\[.*?\]", "", project_name).strip()


# Projects whose spend is internal tooling, not cost-to-serve. Matched on the
# qualifier-stripped base name, so both env twins are covered.
#
# ENG-1044: `Evaluations` is the prompt engineer's workspace. Its completions
# carry whatever UID the eval fixture used, so attributing them would book
# prompt-engineering spend onto a real user's Aurie cost — bursty, too: 14 of 31
# days, up to $16.64 in one day. The dollars still land in the org day total
# (which must reconcile to the real OpenAI bill) via the untagged bucket; they
# just never reach a user. Same treatment as a `[Development]` project below.
INTERNAL_PROJECT_BASE_NAMES = frozenset({"Evaluations"})


def _project_completions_api_key_env(project_name: str) -> str:
    """PROD-3572: "Memory [Production]" -> "OPENAI_MEMORY_API_KEY"."""
    base = re.sub(r"[^A-Za-z0-9]+", "_", _project_base_name(project_name))
    return f"OPENAI_{base.strip('_').upper()}_API_KEY"


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
# IntelligenceService LLM-call ledger (post-epoch primary attribution source)
# ---------------------------------------------------------------------------


class LedgerUnavailable(Exception):
    """The IS LLM-call ledger couldn't be read (missing token, non-200,
    timeout, JSON parse error). Callers catch this and fall back to the
    stored-chat-completions walk for the affected slice."""


LEDGER_PROJECT_KEYS = frozenset({
    "conversation", "feed", "guardrail", "insights", "intent", "journal",
    "memory", "notification", "onboarding", "profile", "suggestions", "summary",
})


def _unix_to_iso(unix: int) -> str:
    return (
        datetime.fromtimestamp(unix, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _ledger_project_key(project_name: str) -> str | None:
    """OpenAI dashboard project name -> IS ledger project key.

    "Memory [Production]" -> "memory", "Conversation [Production]" ->
    "conversation". Returns None for non-Production projects (the ledger only
    records production traffic under the feature key; a [Development] twin
    would collide with its Production sibling's key)."""
    if re.search(r"\[(?!Production\])[^\]]*\]\s*$", project_name):
        return None
    base = re.sub(r"\s*\[Production\]\s*$", "", project_name).strip()
    if not base:
        return None
    return base[0].lower() + base[1:]


def iter_llm_calls(
    start_unix: int,
    end_unix: int,
    *,
    user_id: str | None = None,
    prompter_name: str | None = None,
    project_key: str | None = None,
    openai_api: str | None = None,
    provider: str | None = None,
) -> list[dict]:
    """Read the IntelligenceService per-call LLM ledger (BigQuery `llm.llm_call`) for [start, end).
    Newest-first. Raises LedgerUnavailable so callers can fall back to the stored-completions walk."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    params = {
        "from": datetime.fromtimestamp(start_unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": datetime.fromtimestamp(end_unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if user_id:
        params["userId"] = user_id
    if prompter_name:
        params["prompterName"] = prompter_name
    if project_key:
        params["project"] = project_key
    if openai_api:
        params["openaiAPI"] = openai_api
    if provider:
        params["provider"] = provider
    try:
        return llm_ledger.rows(params)
    except llm_ledger.LedgerError as exc:
        raise LedgerUnavailable(str(exc)) from exc


def _ledger_row_to_item(row: dict) -> dict:
    """Normalise a ledger row into the stored-chat-completion item shape so
    `_extract_user_fields` / `aggregate_per_user` stay source-agnostic.

    Field mapping: userId -> metadata.user, prompterName -> metadata.name,
    aiPremiumState -> metadata.aiPremiumState, premiumState ->
    metadata.premiumState, inputTokens/outputTokens/cachedTokens -> the
    completions usage keys, openaiId -> id (dedupe key against stored
    completion `id` at the epoch boundary).

    The boolean metadata.aiPremium / metadata.premium are their own ledger
    columns (added 2026-07 — ENGBACK-1237). When present they map straight
    through, so a churned user (aiPremium=false) stays "Non AI Premium" and is
    not collapsed into "Never AI Premium". Rows written before that column
    existed carry aiPremium=null; for those we fall back to inferring a truthy
    boolean from a present *State column, and an absent/null state falls
    through to the same untagged/absent handling as missing completion
    metadata (Never AI Premium with a UID, "(untagged)" without)."""
    created = 0
    created_at = row.get("createdAt")
    if created_at:
        try:
            created = int(
                datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
            )
        except ValueError:
            created = 0

    metadata: dict = {}
    if row.get("userId"):
        metadata["user"] = row["userId"]
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
        "input_tokens": row.get("inputTokens") or 0,
        "output_tokens": row.get("outputTokens") or 0,
        "prompt_tokens_details": {"cached_tokens": row.get("cachedTokens") or 0},
    }

    return {
        "id": row.get("openaiId") or "",
        "created": created,
        "metadata": metadata,
        "usage": usage,
        # Null on rows written before the ENGBACK-1315 deploy — see service_tier_factor.
        "service_tier": row.get("serviceTier"),
    }


# ---------------------------------------------------------------------------
# OpenAI API helpers (same shape as sibling skills)
# ---------------------------------------------------------------------------


class ProjectWalkSkip(Exception):
    """A project-scoped stored-completions walk couldn't authenticate.

    Most common cause (PROD-3582): a `[Development]` project shares its
    `[Production]` twin's env var — the PROD-3572 key derivation strips the
    `[..]` qualifier, so both map to e.g. `OPENAI_CONVERSATION_API_KEY`, which
    holds the Production key. Pairing it with the Development project id yields
    a 401 `mismatched_project`. The caller catches this and skips that
    project's per-user allocation; its dollars still fold into the untagged
    bucket, so the org day total still reconciles.
    """


def _request(path: str, params: dict, api_key: str, project_id: str | None = None,
             skip_on_project_error: bool = False) -> dict:
    url = f"{OPENAI_API}{path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    if project_id:
        headers["OpenAI-Project"] = project_id
    for attempt in range(5):
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(wait, 30))
            continue
        if not r.ok:
            # For a project-scoped completions walk, an auth/permission error
            # (401 mismatched_project, 403) is a skip-this-project signal, not a
            # fatal error — degrade like a missing key rather than aborting the
            # whole run. Fatal only for the admin-key paths (costs / projects).
            if skip_on_project_error and r.status_code in (401, 403):
                raise ProjectWalkSkip(f"OpenAI {r.status_code} on {path}: {r.text[:200]}")
            sys.exit(f"OpenAI {r.status_code} on {path}: {r.text[:300]}")
        return r.json()
    sys.exit(f"OpenAI {path}: rate-limited after 5 retries")


def fetch_costs(start_unix: int, end_unix: int, api_key: str) -> list[dict]:
    buckets: list[dict] = []
    page: str | None = None
    while True:
        params = {
            "start_time": start_unix,
            "end_time": end_unix,
            "bucket_width": "1d",
            "group_by": ["project_id"],
            "limit": COSTS_PAGE_SIZE,
        }
        if page:
            params["page"] = page
        data = _request(COSTS_PATH, params, api_key)
        buckets.extend(data.get("data", []))
        page = data.get("next_page")
        if not page:
            break
    return buckets


def fetch_projects(api_key: str) -> dict[str, dict]:
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


def iter_completions(start_unix: int, end_unix: int, project_id: str, api_key: str) -> list[dict]:
    """List stored completions for one project within [start, end).

    Newest-first endpoint with no time filter — walk pages and stop when we
    drop below start_unix. Same shape as the sibling skills.
    """
    out: list[dict] = []
    after: str | None = None

    while True:
        params: dict = {"limit": COMPLETIONS_PAGE_SIZE}
        if after:
            params["after"] = after
        data = _request(CHAT_COMPLETIONS_PATH, params, api_key, project_id=project_id,
                        skip_on_project_error=True)
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
# Tier resolution
# ---------------------------------------------------------------------------

def _truthy(value) -> bool:
    """OpenAI metadata values are always strings — coerce to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _tier_label(metadata: dict, dimension: str, has_user: bool = False) -> str:
    """Composite tier label from (premium-bool, premium-state).

    dimension == "ai"      → aiPremium / aiPremiumState
    dimension == "content" → premium / premiumState

    `has_user` should be True when the completion carries a user identifier
    (metadata.user / item.user / item.input_user). On the AI dimension, this
    splits the "both fields missing" case: with a user → "Never AI Premium"
    (the user row exists but aiPremium is NULL in the DB — the column is
    declared `BOOLEAN` nullable, no default); without a user → "(untagged)"
    pre-account / system / batch traffic. The content dimension keeps a single
    `(untagged)` bucket because `premium` defaults to FALSE in DB, so a missing
    content field implies pre-account traffic with no user row. See `Tools/OpenAI`
    → "Three-state semantic on the booleans" in AI Knowledge, and ENGBACK-1083
    for the upstream schema fix.
    """
    if dimension == "ai":
        premium_raw = metadata.get("aiPremium")
        state_raw = metadata.get("aiPremiumState")
        tier_labels = AI_TIER_LABELS
        non_premium_label = "Non AI Premium"
        unknown_state_label = "AI Premium"
    else:
        premium_raw = metadata.get("premium")
        state_raw = metadata.get("premiumState")
        tier_labels = CONTENT_TIER_LABELS
        non_premium_label = "Non Premium"
        unknown_state_label = "Premium"

    if premium_raw is None and state_raw is None:
        if dimension == "ai" and has_user:
            return "Never AI Premium"
        return "(untagged)"
    if not _truthy(premium_raw):
        return non_premium_label
    if state_raw and state_raw in tier_labels:
        return tier_labels[state_raw]
    return f"{unknown_state_label} {state_raw}" if state_raw else unknown_state_label


def _extract_user_fields(item: dict) -> tuple[str, str, str, float, int]:
    """Return (uid, ai_tier, content_tier, weight, created).

    `weight` is the call's token count scaled by its service-tier price multiplier.
    It is an allocation basis, not a token count — never render it as one.
    """
    metadata = item.get("metadata") or {}
    usage = item.get("usage") or {}

    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    weight = ((input_tokens or 0) + (output_tokens or 0)) * service_tier_factor(
        item.get("service_tier")
    )

    uid = (
        metadata.get("user")
        or item.get("user")
        or item.get("input_user")
        or "(untagged)"
    )
    has_user = uid != "(untagged)"
    ai_tier = _tier_label(metadata, "ai", has_user=has_user)
    content_tier = _tier_label(metadata, "content", has_user=has_user)
    created = item.get("created", 0) or 0

    return uid, ai_tier, content_tier, weight, created


# ---------------------------------------------------------------------------
# Aggregation: per-user dollars, last-seen tier, top project
# ---------------------------------------------------------------------------

def aggregate_per_user(
    per_project_dollars: dict[str, float],
    per_project_completions: dict[str, list[dict]],
    projects: dict[str, dict],
) -> tuple[dict[str, dict], float]:
    """Allocate each project's $ across its users by token weight.

    Returns (per_user, untagged_amount):

      per_user[uid] = {
        "amount":           float,                     # total $ across all projects
        "project_amounts":  {project_name: float},     # $ per project for this user
        "completion_count": int,
        "ai_tier":          str,                       # last-seen
        "ai_tier_created":  int,                       # ts of that last-seen
        "content_tier":     str,
        "content_tier_created": int,
      }
      untagged_amount = $ that landed on the "(untagged)" sentinel (no UID)
    """
    per_user: dict[str, dict] = {}
    untagged_amount = 0.0

    for pid, dollars in per_project_dollars.items():
        if dollars <= 0 or pid == "(unattributed)":
            continue
        completions = per_project_completions.get(pid) or []
        if not completions:
            # No completions to allocate against — the project's $ has no
            # UID to attach to, so it all falls into the untagged bucket.
            untagged_amount += dollars
            continue

        # First pass: token weights per UID, last-seen tier per UID per dim.
        tokens_per_uid: dict[str, float] = defaultdict(float)
        completion_count_per_uid: dict[str, int] = defaultdict(int)
        ai_tier_latest: dict[str, tuple[str, int]] = {}
        content_tier_latest: dict[str, tuple[str, int]] = {}

        for item in completions:
            uid, ai_tier, content_tier, t, created = _extract_user_fields(item)
            if t <= 0:
                continue
            tokens_per_uid[uid] += t
            completion_count_per_uid[uid] += 1
            cur_ai = ai_tier_latest.get(uid)
            if cur_ai is None or created > cur_ai[1]:
                ai_tier_latest[uid] = (ai_tier, created)
            cur_content = content_tier_latest.get(uid)
            if cur_content is None or created > cur_content[1]:
                content_tier_latest[uid] = (content_tier, created)

        total_tokens = sum(tokens_per_uid.values())
        if total_tokens == 0:
            untagged_amount += dollars
            continue

        project_name = (projects.get(pid) or {}).get("name", pid)

        for uid, t in tokens_per_uid.items():
            share = dollars * t / total_tokens
            if uid == "(untagged)":
                untagged_amount += share
                continue
            entry = per_user.setdefault(
                uid,
                {
                    "amount": 0.0,
                    "project_amounts": defaultdict(float),
                    "completion_count": 0,
                    "ai_tier": "(untagged)",
                    "ai_tier_created": 0,
                    "content_tier": "(untagged)",
                    "content_tier_created": 0,
                },
            )
            entry["amount"] += share
            entry["project_amounts"][project_name] += share
            entry["completion_count"] += completion_count_per_uid[uid]
            ai_label, ai_ts = ai_tier_latest[uid]
            if ai_ts > entry["ai_tier_created"]:
                entry["ai_tier"] = ai_label
                entry["ai_tier_created"] = ai_ts
            content_label, content_ts = content_tier_latest[uid]
            if content_ts > entry["content_tier_created"]:
                entry["content_tier"] = content_label
                entry["content_tier_created"] = content_ts

    # Normalise project_amounts to a regular dict for serialisation.
    for entry in per_user.values():
        entry["project_amounts"] = dict(entry["project_amounts"])
        top = max(entry["project_amounts"].items(), key=lambda kv: kv[1], default=("—", 0.0))
        entry["top_project"] = top[0]
        entry["top_project_amount"] = top[1]

    return per_user, untagged_amount


# ---------------------------------------------------------------------------
# Rollout-window warning
# ---------------------------------------------------------------------------

def rollout_window_warning(start_unix: int, end_unix: int) -> str | None:
    """Return a markdown banner when the requested window predates or straddles
    the 2026-05-12 aiPremium*/premium* tagging rollout. None if the window is
    entirely post-rollout.

    The point of the banner: pre-rollout calls carry no subscription metadata
    by construction, so they show up as `(untagged)` regardless of the user's
    actual subscription state. Without this warning, readers misread the
    inflated `(untagged)` slice as a tagging regression rather than as the
    expected effect of the rollout window.
    """
    if start_unix >= TAGGING_ROLLOUT_UNIX:
        return None
    if end_unix <= TAGGING_ROLLOUT_UNIX:
        return (
            "> :warning: **Pre-rollout window — tier breakdown is meaningless.** "
            "The `aiPremium*` / `premium*` metadata tagging "
            "([ENG-498](https://linear.app/aura-health/issue/ENG-498) / "
            "[ENG-500](https://linear.app/aura-health/issue/ENG-500)) shipped on "
            "2026-05-12. The requested window predates that — every call lacks "
            "subscription-state metadata by construction, not by bug. All spend "
            "will fall into `(untagged)`. Pick a window on or after 2026-05-12 "
            "for a useful tier cut."
        )
    return (
        "> :warning: **Mixed pre/post-rollout window.** "
        "The `aiPremium*` / `premium*` metadata tagging "
        "([ENG-498](https://linear.app/aura-health/issue/ENG-498) / "
        "[ENG-500](https://linear.app/aura-health/issue/ENG-500)) shipped on "
        "2026-05-12. Calls before that date carry no metadata and surface as "
        "`(untagged)` regardless of subscription state. The `(untagged)` rows "
        "in this report are a mix of pre-rollout traffic and the (small) real "
        "untagged baseline — treat their share as overstated until the window "
        "is entirely post-rollout."
    )


# ---------------------------------------------------------------------------
# Distribution stats
# ---------------------------------------------------------------------------

def compute_stats(amounts: list[float]) -> dict | None:
    """Return summary stats over a list of per-user dollar amounts.

    Population is whatever the caller passes — apply --tier filter upstream.
    Returns None when there are fewer than 2 users (stats undefined / trivial).
    """
    n = len(amounts)
    if n == 0:
        return None
    if n == 1:
        # With one user every percentile equals that value; stddev is undefined.
        v = amounts[0]
        return {
            "count": 1,
            "mean": v,
            "median": v,
            "p90": v,
            "p95": v,
            "p99": v,
            "max": v,
            "stddev": 0.0,
        }

    mean = statistics.fmean(amounts)
    stddev = statistics.stdev(amounts)
    # statistics.quantiles divides into n equal-probability buckets and returns
    # the n-1 cut points. With n=100 we get the 1st…99th percentiles. We use
    # the `inclusive` method because we have the entire population of users in
    # the window, not a sample — this also guarantees p99 ≤ max, which avoids
    # confusing "p95 above max" rendering for small populations.
    centiles = statistics.quantiles(amounts, n=100, method="inclusive")
    median = statistics.median(amounts)
    return {
        "count": n,
        "mean": mean,
        "median": median,
        "p90": centiles[89],   # 90th percentile
        "p95": centiles[94],
        "p99": centiles[98],
        "max": max(amounts),
        "stddev": stddev,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt_money(amount: float) -> str:
    return f"${amount:,.2f}"


def _short_uid(uid: str) -> str:
    if len(uid) <= 14:
        return uid
    return f"{uid[:8]}…{uid[-4:]}"


def render(
    label: str,
    per_user: dict[str, dict],
    untagged_amount: float,
    total_org_dollars: float,
    tier_filter: str | None,
    top_n: int,
    show_stats: bool,
    show_headline: bool,
    attribution_note: str | None = None,
) -> str:
    lines: list[str] = []
    title = f"# OpenAI Spend by User — {label}"
    if tier_filter:
        title += f"  (tier: {tier_filter})"
    lines.append(title)
    lines.append("")
    if attribution_note:
        lines.append(attribution_note)
        lines.append("")

    # Filter by tier. The label's dimension is inferred from its prefix:
    # AI-prefixed labels (or "Never AI Premium") filter on `ai_tier`; all
    # other Premium-/Non-Premium labels filter on `content_tier`; "(untagged)"
    # matches users whose ai_tier OR content_tier is untagged (both dimensions
    # can produce that sentinel for the same user).
    if tier_filter:
        tier_field = _tier_filter_field(tier_filter)
        if tier_field == "any":
            candidates = {
                uid: e
                for uid, e in per_user.items()
                if e["ai_tier"] == tier_filter or e["content_tier"] == tier_filter
            }
        else:
            candidates = {
                uid: e for uid, e in per_user.items() if e[tier_field] == tier_filter
            }
    else:
        candidates = per_user

    tagged_total = sum(e["amount"] for e in candidates.values())
    user_count = len(candidates)

    if tier_filter:
        lines.append(
            f"**Tagged spend in tier:** {_fmt_money(tagged_total)} across "
            f"{user_count:,} unique users"
        )
    else:
        lines.append(
            f"**Total tagged spend:** {_fmt_money(tagged_total)} across "
            f"{user_count:,} unique users"
        )
    untagged_share = (untagged_amount / total_org_dollars * 100) if total_org_dollars else 0
    lines.append(
        f"**Untagged spend:** {_fmt_money(untagged_amount)} "
        f"({untagged_share:.1f}% of org total — pre-account / system / batch, no UID)"
    )
    lines.append("")

    if user_count == 0:
        lines.append("_No users matched the filter in this window._")
        return "\n".join(lines).rstrip() + "\n"

    sorted_amounts = sorted((e["amount"] for e in candidates.values()), reverse=True)
    stats = compute_stats(sorted_amounts) if show_stats else None
    median_val = stats["median"] if stats else None

    if stats:
        lines.append("## Distribution (tagged users only)")
        lines.append("")
        lines.append("| Stat   | $/user |")
        lines.append("|--------|-------:|")
        lines.append(f"| Mean   | {_fmt_money(stats['mean'])} |")
        lines.append(f"| Median | {_fmt_money(stats['median'])} |")
        lines.append(f"| p90    | {_fmt_money(stats['p90'])} |")
        lines.append(f"| p95    | {_fmt_money(stats['p95'])} |")
        lines.append(f"| p99    | {_fmt_money(stats['p99'])} |")
        lines.append(f"| Max    | {_fmt_money(stats['max'])} |")
        lines.append(f"| Stddev | {_fmt_money(stats['stddev'])} |")
        lines.append("")
        top_user_amount = sorted_amounts[0]
        top_share = (
            top_user_amount / total_org_dollars * 100 if total_org_dollars else 0
        )
        if median_val and median_val > 0:
            multiplier = top_user_amount / median_val
            lines.append(
                f"**Top user accounts for** {top_share:.1f}% of org spend "
                f"({_fmt_money(top_user_amount)}) — ~{multiplier:.0f}× the median."
            )
        else:
            lines.append(
                f"**Top user accounts for** {top_share:.1f}% of org spend "
                f"({_fmt_money(top_user_amount)})."
            )
        lines.append("")

    # Top-N ranked table
    ranked = sorted(
        candidates.items(), key=lambda kv: kv[1]["amount"], reverse=True
    )[:top_n]
    actual_n = len(ranked)
    section_title = (
        f"## Top {actual_n} spenders" + (f" — {tier_filter}" if tier_filter else "")
    )
    lines.append(section_title)
    lines.append("")
    lines.append(
        "| Rank | User (Firebase UID)   | Spend  | Share | Tier (AI / content) | Top project | Calls |"
    )
    lines.append("|-----:|-----------------------|-------:|------:|---------------------|-------------|------:|")
    for rank, (uid, entry) in enumerate(ranked, 1):
        share = entry["amount"] / total_org_dollars * 100 if total_org_dollars else 0
        tier_cell = f"{entry['ai_tier']} / {entry['content_tier']}"
        lines.append(
            f"| {rank} | `{_short_uid(uid)}` | {_fmt_money(entry['amount'])} | "
            f"{share:.1f}% | {tier_cell} | {entry.get('top_project', '—')} | "
            f"{entry['completion_count']:,} |"
        )

    if show_headline and ranked:
        outlier_lines = _build_headline_outliers(ranked, median_val, total_org_dollars)
        if outlier_lines:
            lines.append("")
            lines.append("## Headline outliers")
            lines.append("")
            lines.extend(outlier_lines)

    return "\n".join(lines).rstrip() + "\n"


def _tier_filter_field(tier_filter: str) -> str:
    """Pick which user-record field to match a --tier filter against.

    AI-dimension labels (`AI Premium *`, `Non AI Premium`, `Never AI Premium`)
    filter on `ai_tier`. Content-dimension labels (`Premium *`, `Non Premium`)
    filter on `content_tier`. `(untagged)` is shared between both dimensions
    so it matches `any` (either field).
    """
    if tier_filter == "(untagged)":
        return "any"
    if tier_filter.startswith("AI ") or tier_filter == "Never AI Premium":
        return "ai_tier"
    return "content_tier"


def _build_headline_outliers(
    ranked: list[tuple[str, dict]],
    median_val: float | None,
    total_org_dollars: float,
) -> list[str]:
    out: list[str] = []
    if not ranked:
        return out

    # Premium Free / Premium Payment Pending / Non / untagged in the top set.
    # These match against the AI dimension (ai_tier), so labels carry the
    # canonical AI-prefixed form.
    pf = [(u, e) for u, e in ranked if e["ai_tier"] == "AI Premium Free"]
    pp = [(u, e) for u, e in ranked if e["ai_tier"] == "AI Premium Payment Pending"]
    np_ = [(u, e) for u, e in ranked if e["ai_tier"] == "Non AI Premium"]
    ut = [(u, e) for u, e in ranked if e["ai_tier"] == "(untagged)"]

    if pf:
        pf_total = sum(e["amount"] for _, e in pf)
        verb = "users is" if len(pf) == 1 else "users are"
        out.append(
            f"- **{len(pf)} of the top {len(ranked)} {verb}** AI Premium Free "
            f"(comped / redemption-code access, paying $0 for AI) — {_fmt_money(pf_total)} combined."
        )
    if pp:
        pp_total = sum(e["amount"] for _, e in pp)
        verb = "users is" if len(pp) == 1 else "users are"
        out.append(
            f"- **{len(pp)} of the top {len(ranked)} {verb}** AI Premium Payment Pending "
            f"(dunning / failed-payment retry on AI subscription) — {_fmt_money(pp_total)} combined."
        )
    if np_:
        np_total = sum(e["amount"] for _, e in np_)
        verb = "users is" if len(np_) == 1 else "users are"
        out.append(
            f"- **{len(np_)} of the top {len(ranked)} {verb}** Non AI Premium "
            f"(no AI subscription) — {_fmt_money(np_total)} combined; "
            f"high cost without an AI subscription is a model-routing / abuse-pattern signal."
        )
    if ut:
        verb = "users is" if len(ut) == 1 else "users are"
        out.append(
            f"- **{len(ut)} of the top {len(ranked)} {verb}** (untagged) — "
            "likely a code path that missed `metadata.aiPremium` / `metadata.premium`."
        )

    # Top user vs median framing
    if median_val and median_val > 0:
        top_uid, top_entry = ranked[0]
        mult = top_entry["amount"] / median_val
        if mult >= 100:
            out.append(
                f"- Top user is ~{mult:.0f}× the median — distribution is heavily "
                "right-skewed (typical for SaaS, but the very top is worth a manual look)."
            )

    # Single-project dominance
    for uid, e in ranked:
        if e["amount"] <= 0 or not e.get("project_amounts"):
            continue
        top_share = e.get("top_project_amount", 0) / e["amount"]
        if top_share >= 0.95:
            out.append(
                f"- `{_short_uid(uid)}` spends {top_share * 100:.0f}% of their "
                f"cost in **{e['top_project']}** alone — single-flow user, easy to "
                "investigate."
            )
            break  # one is enough; don't pile on

    return out


# ---------------------------------------------------------------------------
# Cost aggregation (re-used from sibling skills)
# ---------------------------------------------------------------------------

def aggregate_by_project_dollars(buckets: list[dict]) -> tuple[dict[str, float], str]:
    totals: dict[str, float] = {}
    currency = "usd"
    for bucket in buckets:
        for row in bucket.get("results", []):
            amount = row.get("amount") or {}
            value = float(amount.get("value", 0) or 0)
            cur = amount.get("currency") or "usd"
            if cur != currency and value > 0:
                currency = cur
            pid = row.get("project_id") or "(unattributed)"
            totals[pid] = totals.get(pid, 0.0) + value
    return totals, currency


# ---------------------------------------------------------------------------
# Per-project attribution collection: ledger-first with epoch split + failover
# ---------------------------------------------------------------------------

def fetch_completions_slice(
    start_unix: int, end_unix: int, pid: str, pname: str
) -> tuple[list[dict], bool]:
    """Stored-chat-completions walk for one project over one time slice.

    The only source for the pre-epoch slice, and the fallback when the ledger
    is unavailable for the post-epoch slice. Returns [] (dollars fold into the
    untagged bucket) when the project-scoped key is unset or the walk hits a
    project auth error — same degradation as before the ledger existed."""
    # PROD-3572: the stored-completions walk needs a project-scoped key —
    # OPENAI_ADMIN_API_KEY can't hold the model.request scope.
    completions_key = _resolve_completions_api_key(pname)
    if not completions_key:
        return [], True
    try:
        return iter_completions(start_unix, end_unix, pid, completions_key), False
    except ProjectWalkSkip as e:
        # Project-scoped auth/permission error (e.g. a mismatched key that
        # isn't caught by the [Development] guard) — skip this walk rather
        # than abort the whole run. Its dollars fold into untagged.
        print(
            f"⚠️  Skipping {pname} ({pid}) stored-completions walk: {e} "
            f"— dollars fold into the untagged bucket (PROD-3582).",
            file=sys.stderr,
        )
        return [], True
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(
            f"⚠️  Failed to fetch completions for {pname} ({pid}): {e}",
            file=sys.stderr,
        )
        return [], True


def collect_project_items(
    start_unix: int,
    end_unix: int,
    pid: str,
    pname: str,
    sources_used: set[str],
) -> tuple[list[dict], bool]:
    """Gather attribution items for one project over [start, end).

    Splits the window at the ledger epoch: the pre-epoch slice always uses the
    stored-chat-completions walk (the ledger has no rows before the epoch);
    the post-epoch slice uses the IS LLM-call ledger as primary, falling back
    to the completions walk (with a stderr warning) when the ledger is
    unavailable. Results are merged and deduped defensively on the OpenAI
    object id (ledger openaiId == stored completion id) in case of boundary
    overlap."""
    items: list[dict] = []

    before_start, before_end = start_unix, min(end_unix, LEDGER_EPOCH_UNIX)
    after_start, after_end = max(start_unix, LEDGER_EPOCH_UNIX), end_unix
    has_before = before_start < before_end
    has_after = after_start < after_end
    partial_epoch_unsafe = False

    if has_before:
        before_items, before_failed = fetch_completions_slice(before_start, before_end, pid, pname)
        partial_epoch_unsafe = partial_epoch_unsafe or before_failed
        items.extend(before_items)
        sources_used.add("completions")

    if has_after:
        slice_label = f"{_unix_to_iso(after_start)} → {_unix_to_iso(after_end)}"
        project_key = _ledger_project_key(pname)
        if not project_key or project_key not in LEDGER_PROJECT_KEYS:
            print(
                f"note: '{pname}' is not served by the LLM ledger; using stored "
                f"chat completions for {slice_label}.",
                file=sys.stderr,
            )
            after_items, after_failed = fetch_completions_slice(
                after_start, after_end, pid, pname
            )
            partial_epoch_unsafe = partial_epoch_unsafe or after_failed
            items.extend(after_items)
            sources_used.add("completions-non-ledger")
        else:
            try:
                rows = iter_llm_calls(
                    after_start, after_end, project_key=project_key, provider="openai"
                )
                items.extend(_ledger_row_to_item(row) for row in rows)
                sources_used.add("ledger")
                # Ledger roll-out ramp: the ledger is known-incomplete here, so it is
                # a supplement to stored completions rather than a replacement. Union
                # both and let the id-dedupe below collapse the overlap — a Chat
                # Completions call recorded in both shares one id, and a Responses-API
                # call carries a response id the completions walk never returns, so
                # neither can double-count. Outside the ramp this is skipped entirely.
                if after_start < LEDGER_RAMP_END_UNIX:
                    ramp_end = min(after_end, LEDGER_RAMP_END_UNIX)
                    ramp_items, ramp_failed = fetch_completions_slice(
                        after_start, ramp_end, pid, pname
                    )
                    # Deliberately NOT folded into partial_epoch_unsafe: the caller
                    # discards a project's items entirely when that flag is set, so a
                    # failed supplementary walk would throw away the ledger rows we do
                    # have and leave this project WORSE than before the union existed.
                    # The union is strictly additive — on failure we keep the ledger
                    # data (status-quo behaviour) and mark the slice as partial.
                    items.extend(ramp_items)
                    sources_used.add("ledger-ramp-union")
                    if ramp_failed:
                        sources_used.add("ledger-ramp-union-partial")
                        print(
                            f"WARNING: ledger roll-out ramp union INCOMPLETE for {pname} "
                            f"({_unix_to_iso(after_start)} → {_unix_to_iso(ramp_end)}): the "
                            f"stored-completions walk failed, so this project keeps "
                            f"ledger-only attribution for a window where the ledger is "
                            f"known-incomplete. Its per-user cost is likely overstated.",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"note: {_unix_to_iso(after_start)} → {_unix_to_iso(ramp_end)} "
                            f"falls in the ledger roll-out ramp; unioning {len(ramp_items)} "
                            f"stored completion(s) with {len(rows)} ledger row(s) for "
                            f"{pname} (dedupe on id).",
                            file=sys.stderr,
                        )
            except LedgerUnavailable as e:
                _fail_closed_on_ledger_outage(e)
                print(
                    f"WARNING: ledger unavailable ({e}): falling back to stored "
                    f"chat completions for {pname} {slice_label}. Note: the "
                    f"completions fallback is chat-API-only and cannot see "
                    f"Responses-API traffic.",
                    file=sys.stderr,
                )
                after_items, after_failed = fetch_completions_slice(after_start, after_end, pid, pname)
                partial_epoch_unsafe = partial_epoch_unsafe or after_failed
                items.extend(after_items)
                sources_used.add("completions-fallback")

    seen_ids: set[str] = set()
    deduped: list[dict] = []
    for item in items:
        item_id = item.get("id") or ""
        if item_id and item_id in seen_ids:
            continue
        if item_id:
            seen_ids.add(item_id)
        deduped.append(item)
    return deduped, has_before and has_after and partial_epoch_unsafe


def _primary_attribution_class(project_sources: set[str]) -> str:
    """Collapse one project's source markers into a single attribution class.

    Precedence is deliberately worst-first: a project whose ledger read RAISED
    (`completions-fallback`) is classed as degraded even though it may also have
    contributed `ledger` rows for part of the window, because materiality has to
    err toward flagging. `completions-non-ledger` outranks `ledger` for the same
    reason. Used to attribute dollars to a class in `dollars_by_attribution`."""
    if "completions-fallback" in project_sources:
        return "completions-fallback"
    if "completions-non-ledger" in project_sources:
        return "completions-non-ledger"
    if "ledger" in project_sources:
        return "ledger"
    if "completions" in project_sources:
        return "completions-pre-epoch"
    return "none"


def summarize_attribution_materiality(
    dollars_by_attribution: dict[str, float],
) -> dict[str, object]:
    """Dollar shares per attribution class — the materiality signal (ENG-1114).

    `sources` alone cannot tell a consumer whether the non-ledger slice is one
    cent or two hundred dollars, so the only test available to a caller was "is
    this string present", which flags a $0.01 project exactly as loudly as a real
    outage. That is not a hypothetical: on 2026-08-09 `completions-non-ledger`
    was triggered by `Front` at $0.01 plus four projects at $0.00 while every
    project carrying money went through the ledger ($350.11 of $350.12), and the
    day was withheld from the cost store as though the attribution were broken.

    Emitted into the `--json` `attribution` block so downstream writers — above
    all `eng:store-aurie-user-daily-cost`, which PERSISTS the result — can gate
    on the degraded *share* instead of a substring."""
    attributed = sum(v for k, v in dollars_by_attribution.items() if k != "none")
    def share(key: str) -> float:
        amount = dollars_by_attribution.get(key, 0.0)
        return (100.0 * amount / attributed) if attributed > 0 else 0.0
    return {
        "attributed_dollars": round(attributed, 6),
        "dollars": {k: round(v, 6) for k, v in sorted(dollars_by_attribution.items())},
        "shares_pct": {
            k: round(share(k), 4)
            for k in sorted(dollars_by_attribution)
            if k != "none"
        },
        # The one number a persisting consumer should gate on: spend whose
        # per-user split came from a stored-completions FALLBACK (a ledger read
        # that failed), which post-epoch cannot see Responses-API traffic.
        # Deliberately excludes `completions-non-ledger` — that is by design,
        # not an outage.
        "degraded_share_pct": round(share("completions-fallback"), 4),
        "non_ledger_share_pct": round(share("completions-non-ledger"), 4),
    }


def summarize_attribution_coverage(
    per_user: dict[str, dict],
    walked_project_dollars: dict[str, float],
    skipped_dollars: float = 0.0,
) -> dict[str, object]:
    """How much of the walkable spend actually REACHED a user (ENG-1120).

    Distinct from `materiality`, which reports the attribution *source*. Coverage
    reports the attribution *outcome*, and the two fail independently: a walk can
    use exactly the right source for the window and still resolve nothing, in
    which case there is no `completions-fallback` marker to notice and no degraded
    *share* either — the walk simply returned no completions, every dollar fell
    into the untagged bucket, and the day still reconciled to the authoritative
    bill because those dollars land on the store's `Other` row.

    That is what happened on 2026-06-24 and 2026-06-25: 0 users carried any LLM
    cost, $144.88 and $151.00 sat entirely on `Other`, voice attribution was
    intact, and nothing errored. The dollars were right and the distribution was
    empty — which is precisely what every per-user reader consumes.

    Denominator is the spend in projects this run actually WALKED. Projects
    skipped by design (`[Development]` twins, `INTERNAL_PROJECT_BASE_NAMES`) are
    excluded from the ratio and reported separately as `skipped_dollars`: their
    dollars are real org spend but are not any user's cost-to-serve, so counting
    them as uncovered would make a healthy day look broken.

    `coverage_pct` is 100.0 when there is nothing to attribute (no walkable
    spend), so a genuinely zero-LLM day reads as complete rather than as a
    collapse — consumers should still skip the check when `eligible_dollars` is 0.
    """
    eligible = sum(v for v in walked_project_dollars.values() if v > 0)
    reached = sum(float(e.get("amount") or 0.0) for e in per_user.values())
    # Allocation is by token share of the same dollars, so `reached` can only
    # drift above `eligible` by float error; clamp so the share never reads >100%.
    reached = min(reached, eligible)
    coverage = (100.0 * reached / eligible) if eligible > 0 else 100.0
    return {
        "eligible_dollars": round(eligible, 6),
        "reached_dollars": round(reached, 6),
        "unreached_dollars": round(eligible - reached, 6),
        # The one number a persisting consumer should gate on: the share of
        # walkable spend that landed on a real UID.
        "coverage_pct": round(coverage, 4),
        "users_with_cost": sum(
            1 for e in per_user.values() if float(e.get("amount") or 0.0) > 0
        ),
        # Excluded from the ratio by design — context, not a coverage failure.
        "skipped_dollars": round(skipped_dollars, 6),
    }


def build_coverage_clause(coverage: dict[str, object] | None) -> str:
    """Coverage sentence for the rendered note.

    Appended by `main` on EVERY branch rather than inside `build_attribution_note`,
    because that function returns early for a wholly pre-epoch window — which is
    exactly where this failure was found (June 2026). A clause wired into the
    post-epoch branches only would have been invisible for the affected days."""
    if not coverage:
        return ""
    eligible = float(coverage.get("eligible_dollars") or 0.0)
    if eligible <= 0:
        return ""
    pct = float(coverage.get("coverage_pct") or 0.0)
    unreached = float(coverage.get("unreached_dollars") or 0.0)
    users = coverage.get("users_with_cost")
    clause = (
        f"; **per-user coverage {pct:.2f}%** "
        f"({users} users carry cost; ${unreached:,.2f} of ${eligible:,.2f} "
        f"reached no user)"
    )
    if pct < 90.0:
        clause += (
            " — ATTRIBUTION COLLAPSE: the dollars still reconcile to the bill but "
            "the per-user distribution is not usable"
        )
    return clause


def build_attribution_note(
    start_unix: int,
    end_unix: int,
    sources_used: set[str],
    materiality: dict[str, object] | None = None,
) -> str:
    """One-line data-source annotation for the rendered report."""
    epoch_label = "ledger epoch 2026-07-23T03:25Z"
    # ENG-1114: this text asserts an OUTAGE, so it may only be emitted for
    # `completions-fallback`. Attaching it to `completions-non-ledger` — a
    # project the ledger never serves, by design — claims a failure that did not
    # happen; see the mixed-branch note below.
    fallback_note = (
        "ledger unavailable — stored-completions fallback is chat-API-only "
        "and cannot see Responses-API traffic"
    )
    non_ledger_note = "projects the ledger does not serve, by design — not an outage"

    def _materiality_clause() -> str:
        if not materiality:
            return ""
        bits = []
        deg = float(materiality.get("degraded_share_pct") or 0.0)
        non = float(materiality.get("non_ledger_share_pct") or 0.0)
        dollars = materiality.get("dollars") or {}
        if deg > 0:
            bits.append(
                f"fell back for ${float(dollars.get('completions-fallback', 0.0)):,.2f} "
                f"({deg:.2f}%) of attributed spend"
            )
        if non > 0:
            bits.append(
                f"non-ledger projects account for "
                f"${float(dollars.get('completions-non-ledger', 0.0)):,.2f} ({non:.2f}%)"
            )
        return ("; " + "; ".join(bits)) if bits else ""
    if end_unix <= LEDGER_EPOCH_UNIX:
        return (
            f"**Attribution source:** stored chat completions "
            f"(window predates {epoch_label})"
        )
    ramp_note = (
        "; the slice inside the ledger roll-out ramp (to 2026-07-24T00:00Z, where "
        "the ledger is only partially populated) unions the ledger with stored chat "
        "completions, deduped on id"
    )
    if "ledger-ramp-union-partial" in sources_used:
        ramp_note += (
            " — INCOMPLETE for at least one project (its completions walk failed, "
            "leaving ledger-only attribution that likely overstates per-user cost)"
        )
    if start_unix >= LEDGER_EPOCH_UNIX:
        fell_back = "completions-fallback" in sources_used
        non_ledger = "completions-non-ledger" in sources_used
        # Build the base note, then append the ramp caveat once on the way out —
        # appending per-branch drops it on every path but the pure-ledger one, and
        # the mixed path is the common case for a ramp-window run (any non-ledger
        # project puts the run there).
        #
        # ENG-1114: the two completions conditions are NOT interchangeable and are
        # reported separately. `completions-fallback` means a ledger read raised —
        # an outage, and the condition ENG-1054 fails closed on.
        # `completions-non-ledger` means the project is not in
        # LEDGER_PROJECT_KEYS, which is a permanent property of that project, not
        # a failure. Collapsing them made a run whose only non-ledger spend was
        # $0.01 announce "ledger unavailable", and a good day was withheld from
        # the cost store on the strength of that sentence. The straddling branch
        # below always distinguished them; this branch did not.
        if not fell_back and not non_ledger:
            note = "**Attribution source:** LLM ledger (BigQuery llm.llm_call)"
        elif "ledger" in sources_used:
            causes = []
            if fell_back:
                causes.append(fallback_note)
            if non_ledger:
                causes.append(non_ledger_note)
            note = (
                f"**Attribution source:** mixed — LLM ledger for some "
                f"projects, stored chat completions for others "
                f"({'; '.join(causes)})"
            )
        elif fell_back:
            causes = [fallback_note] + ([non_ledger_note] if non_ledger else [])
            note = (
                f"**Attribution source:** stored chat completions "
                f"({'; '.join(causes)})"
            )
        else:
            note = (
                f"**Attribution source:** stored chat completions "
                f"({non_ledger_note})"
            )
        note += _materiality_clause()
        if "ledger-ramp-union" in sources_used:
            note += ramp_note
        return note
    note = (
        f"**Attribution source:** mixed (window straddles {epoch_label}) — "
        f"stored chat completions before the epoch, LLM ledger after"
    )
    if "completions-fallback" in sources_used:
        note += f"; some post-epoch projects fell back ({fallback_note})"
    if "completions-non-ledger" in sources_used:
        note += (
            f"; some post-epoch projects used stored chat completions "
            f"({non_ledger_note})"
        )
    note += _materiality_clause()
    if "ledger-ramp-union" in sources_used:
        note += ramp_note
    return note


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Ensure stdout can emit the Unicode arrows used in the rendered Markdown
    # report on Windows consoles that default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="OpenAI spend report ranked by user")
    ap.add_argument(
        "--window",
        choices=["month-to-date", "previous-month", "last-7d", "last-30d"],
        default="month-to-date",
    )
    ap.add_argument("--since", help="UTC start date (YYYY-MM-DD). Use with --until.")
    ap.add_argument("--until", help="UTC end date, exclusive (YYYY-MM-DD).")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP_N, help=f"Top-N cutoff (default {DEFAULT_TOP_N}).")
    ap.add_argument(
        "--tier",
        help=(
            "Filter ranking + stats to one tier. Dimension inferred from the "
            "label: AI-prefixed labels filter on the AI dimension (Aurie) — "
            '"AI Premium Paid" / "AI Premium Trial" / "AI Premium Free" / '
            '"AI Premium Payment Pending" / "Non AI Premium" / '
            '"Never AI Premium". Unprefixed labels filter on the content '
            'dimension (Aura) — "Premium Paid" / "Premium Trial" / '
            '"Premium Free" / "Premium Payment Pending" / "Non Premium". '
            '"(untagged)" matches users whose AI or content tier is untagged.'
        ),
    )
    ap.add_argument("--no-stats", action="store_true", help="Suppress the distribution block.")
    ap.add_argument("--no-headline", action="store_true", help="Suppress the headline-outliers paragraph.")
    ap.add_argument("--json", dest="json_out", help="Write structured JSON to this path.")
    ap.add_argument("--allow-degraded-attribution", action="store_true",
                   help="Accept degraded per-user attribution when the LLM ledger is "
                        "unreachable on a post-2026-07-23 window (default: exit 2). "
                        "Env: AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1")
    args = ap.parse_args()
    _ALLOW_DEGRADED[0] = _ALLOW_DEGRADED[0] or bool(
        getattr(args, "allow_degraded_attribution", False))

    if args.tier and args.tier not in VALID_TIER_FILTERS:
        sys.exit(
            f"--tier must be one of: {sorted(VALID_TIER_FILTERS)}. Got {args.tier!r}."
        )
    if args.top <= 0:
        sys.exit("--top must be a positive integer")

    api_key = os.environ.get("OPENAI_ADMIN_API_KEY")
    if not api_key:
        sys.exit(
            "Missing OPENAI_ADMIN_API_KEY. Create an Admin API key at "
            "https://platform.openai.com/settings/organization/admin-keys "
            "and add it to ~/.claude/settings.json under the `env` block."
        )

    start_unix, end_unix, label = resolve_window(args.window, args.since, args.until)

    cost_buckets = fetch_costs(start_unix, end_unix, api_key)
    per_project_dollars, _currency = aggregate_by_project_dollars(cost_buckets)
    projects = fetch_projects(api_key)

    per_project_completions: dict[str, list[dict]] = {}
    attribution_sources: set[str] = set()
    # ENG-1114: dollars per attribution class, so the note and the `--json` block
    # can report HOW MUCH spend each source covered rather than only that the
    # source appeared somewhere. Skipped (dev/internal) projects are excluded —
    # their dollars are never attributed to a user at all.
    dollars_by_attribution: dict[str, float] = {}
    # ENG-1120: the coverage denominator is the spend this run actually walked.
    # Tracked separately from `dollars_by_attribution` because that map is keyed
    # by attribution CLASS — it cannot answer "which projects were eligible".
    walked_project_dollars: dict[str, float] = {}
    skipped_project_dollars = 0.0
    for pid, dollars in per_project_dollars.items():
        if dollars <= 0 or pid == "(unattributed)":
            continue
        pname = (projects.get(pid) or {}).get("name", pid)
        # PROD-3582: a `[Development]` project shares its `[Production]` twin's
        # project-scoped key env var (PROD-3572 strips the `[..]` qualifier), so
        # walking it would 401 `mismatched_project`; its ledger key would also
        # collide with the Production twin's. We only attribute Production
        # cost — skip Dev projects; their dollars still fold into the untagged
        # bucket, so the org day total reconciles.
        # ENG-1044: internal-tooling projects (see INTERNAL_PROJECT_BASE_NAMES)
        # are skipped for the same reason — their dollars are real org spend but
        # are not any user's cost-to-serve.
        if (pname.endswith("[Development]")
                or _project_base_name(pname) in INTERNAL_PROJECT_BASE_NAMES):
            per_project_completions[pid] = []
            skipped_project_dollars += dollars
            continue
        walked_project_dollars[pid] = dollars
        project_sources: set[str] = set()
        items, partial_epoch_unsafe = collect_project_items(
            start_unix, end_unix, pid, pname, project_sources
        )
        attribution_sources |= project_sources
        cls = _primary_attribution_class(project_sources)
        dollars_by_attribution[cls] = dollars_by_attribution.get(cls, 0.0) + dollars
        per_project_completions[pid] = [] if partial_epoch_unsafe else items

    per_user, untagged_amount = aggregate_per_user(
        per_project_dollars, per_project_completions, projects
    )

    total_org_dollars = sum(
        v for k, v in per_project_dollars.items() if k != "(unattributed)"
    )

    attribution_materiality = summarize_attribution_materiality(dollars_by_attribution)
    attribution_coverage = summarize_attribution_coverage(
        per_user, walked_project_dollars, skipped_project_dollars
    )
    attribution_note = build_attribution_note(
        start_unix, end_unix, attribution_sources, attribution_materiality
    ) + build_coverage_clause(attribution_coverage)

    report = render(
        label,
        per_user,
        untagged_amount,
        total_org_dollars,
        args.tier,
        args.top,
        not args.no_stats,
        not args.no_headline,
        attribution_note=attribution_note,
    )
    rollout_warning = rollout_window_warning(start_unix, end_unix)
    if rollout_warning:
        # Echo to stderr so the warning surfaces in logs / pipes even when
        # stdout is being captured to a file.
        print(rollout_warning, file=sys.stderr)
        # And prepend it to the rendered report so it lands above the headline
        # in chat / Slack readouts.
        report = rollout_warning + "\n\n" + report
    print(report)

    if args.json_out:
        if args.tier:
            candidates = {u: e for u, e in per_user.items() if e["ai_tier"] == args.tier}
        else:
            candidates = per_user
        sorted_amounts = sorted((e["amount"] for e in candidates.values()), reverse=True)
        stats = compute_stats(sorted_amounts)

        # Full ranked list — decoupled from the markdown `--top` cutoff so deeper
        # ranks (top 20 / 50 / etc.) can be extracted from the JSON without
        # re-running the script (the per-completion iteration takes ~30 min on
        # month-to-date and is the entire cost; rank cutoff is render-time only).
        all_ranked = sorted(
            candidates.items(), key=lambda kv: kv[1]["amount"], reverse=True
        )
        ranked = all_ranked[: args.top]

        def _user_row(uid: str, e: dict) -> dict:
            return {
                "uid": uid,
                "amount": e["amount"],
                "share": (e["amount"] / total_org_dollars * 100) if total_org_dollars else 0,
                "ai_tier": e["ai_tier"],
                "content_tier": e["content_tier"],
                "top_project": e.get("top_project"),
                "completion_count": e["completion_count"],
            }

        # Window vs rollout status — gives JSON consumers a programmatic
        # signal that `(untagged)` rows may be pre-rollout traffic rather
        # than a tagging gap. Mirrors the chat-rendered banner.
        if start_unix >= TAGGING_ROLLOUT_UNIX:
            rollout_status = "post"
        elif end_unix <= TAGGING_ROLLOUT_UNIX:
            rollout_status = "pre"
        else:
            rollout_status = "mixed"

        payload = {
            "window": {
                "label": label,
                "start_unix": start_unix,
                "end_unix": end_unix,
                "start_iso": datetime.fromtimestamp(start_unix, tz=timezone.utc).isoformat(),
                "end_iso": datetime.fromtimestamp(end_unix, tz=timezone.utc).isoformat(),
            },
            "rollout": {
                "tagging_started_unix": TAGGING_ROLLOUT_UNIX,
                "tagging_started_iso": datetime.fromtimestamp(
                    TAGGING_ROLLOUT_UNIX, tz=timezone.utc
                ).isoformat(),
                "window_status": rollout_status,
                "warning": rollout_warning,
            },
            "attribution": {
                "sources": sorted(attribution_sources),
                "note": attribution_note,
                # ENG-1114: gate on `materiality.degraded_share_pct`, never on the
                # presence of a string in `sources`.
                "materiality": attribution_materiality,
                # ENG-1120: attribution OUTCOME, not source. `materiality` cannot
                # see a walk that used the right source and resolved nothing.
                "coverage": attribution_coverage,
                "ledger_epoch_unix": LEDGER_EPOCH_UNIX,
                "ledger_epoch_iso": datetime.fromtimestamp(
                    LEDGER_EPOCH_UNIX, tz=timezone.utc
                ).isoformat(),
            },
            "total": total_org_dollars,
            "tagged_amount": sum(e["amount"] for e in candidates.values()),
            "untagged_amount": untagged_amount,
            "tier_filter": args.tier,
            "stats": stats,
            # `top_users` stays capped at --top for backward compat with any
            # caller that grabs the rendered headline. `users_all` carries the
            # full ranked list (same row shape) so deeper ranks are extractable.
            "top_users": [_user_row(uid, e) for uid, e in ranked],
            "users_all": [_user_row(uid, e) for uid, e in all_ranked],
        }
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)


if __name__ == "__main__":
    main()
