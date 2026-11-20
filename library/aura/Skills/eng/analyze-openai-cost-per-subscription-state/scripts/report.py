#!/usr/bin/env python3
"""
OpenAI spend report broken down by subscription state for the Aura org.

Pulls authoritative per-project dollars from the OpenAI Costs API (same source
as `eng:analyze-openai-costs`), then iterates each project's stored chat
completions to allocate those dollars across subscription tiers using the
metadata.{aiPremium,aiPremiumState,premium,premiumState} tags added by ENG-498
and ENG-500.

Tier composition (per dimension):

  AI dimension (Aurie — `metadata.aiPremium` + `aiPremiumState`):
    - aiPremium == "false"                          → "Non AI Premium"
    - aiPremium == "true",  aiPremiumState == "paid"            → "AI Premium Paid"
    - aiPremium == "true",  aiPremiumState == "trial"           → "AI Premium Trial"
    - aiPremium == "true",  aiPremiumState == "free"            → "AI Premium Free"
    - aiPremium == "true",  aiPremiumState == "paymentPending"  → "AI Premium Payment Pending"
    - both fields missing AND user is present       → "Never AI Premium"
    - both fields missing AND no user               → "(untagged)"

  Content dimension (Aura — `metadata.premium` + `premiumState`):
    - premium == "false"                            → "Non Premium"
    - premium == "true",  premiumState == "paid"            → "Premium Paid"
    - premium == "true",  premiumState == "trial"           → "Premium Trial"
    - premium == "true",  premiumState == "free"            → "Premium Free"
    - premium == "true",  premiumState == "paymentPending"  → "Premium Payment Pending"
    - both fields missing                           → "(untagged)"

  The AI-dimension labels carry an "AI " prefix to disambiguate from the
  content (Aura Premium) labels — readers were defaulting to the Aura reading
  even when looking at the AI cut. Content-dimension labels stay unprefixed
  since they ARE the canonical Aura Premium labels.

  "AI Premium Free" / "Premium Free" means a user who has the subscription
  *access* but is not paying — e.g. through redemption-code grants, comps,
  gifts, or partnerships. They cost like Paid (same prompters, same model
  routing) but contribute zero revenue, so they get their own tier rather
  than being lumped into Non.

  "Never AI Premium" is the AI-dimension-only bucket for users whose `aiPremium`
  column has never been set (NULL in the IntelligenceService `"user"` table — the
  column is declared `BOOLEAN` nullable with no default, so a user who has never
  had an AI subscription event carries NULL forever). The metadata-stamping path
  skips writing the field when the value is NULL, so the completion arrives with
  no `aiPremium` / `aiPremiumState` fields at all. We distinguish this from true
  `(untagged)` traffic (pre-account / system / batch with no user row at all) by
  the presence of `metadata.user`. See `Tools/OpenAI` → "Three-state semantic on
  the booleans" in AI Knowledge for the full data model. The content dimension
  doesn't get this split today — `premium` defaults to FALSE in DB so the
  never-vs-lost distinction is flattened upstream; tracked in ENGBACK-1083.

Two independent cuts:
  - By AI premium tier (aiPremium + aiPremiumState)
  - By content premium tier (premium + premiumState)

Allocation method: proportional to total tokens within each project — sidesteps
having to maintain a model-pricing table. See references/allocation.md.

Usage:
    python report.py                                    # month-to-date (default)
    python report.py --window previous-month
    python report.py --window last-7d
    python report.py --window last-30d
    python report.py --since 2026-04-01 --until 2026-05-01
    python report.py --dimension ai                     # only AI premium cut
    python report.py --no-top-users                     # suppress per-tier ranking
    python report.py --json out.json                    # write structured JSON

Attribution sources (the $ authority is always the Costs API; only the
token-weight walk below changes):

  - Post-epoch slices (on/after 2026-07-23T03:25Z) read the IntelligenceService
    per-call LLM ledger (BigQuery llm.llm_call) as the
    PRIMARY source — one row per OpenAI call, covering both the Chat Completions
    and Responses APIs, with aiPremiumState / premiumState as top-level columns.
    If the ledger is unavailable (BigQuery error, missing
    GCP_KEY), the slice falls back to the stored-chat-completions walk
    (chat-API-only — it cannot see Responses-API traffic).
  - Pre-epoch slices always use the stored-chat-completions walk; the ledger has
    no rows before the epoch.
  - Results from both slices are merged, deduped on the OpenAI object id
    (ledger openaiId == stored completion id).

Env:
    OPENAI_ADMIN_API_KEY  (Admin API key, sk-admin-...; required)
        Used for /v1/organization/* (costs + project list) and the startup check.
    GCP_KEY  (service account; required for the ledger read)
        Missing key → ledger unavailable → stored-completions fallback.
    OPENAI_<PROJECT>_API_KEY  (project-scoped service-account keys; required per
        project for the stored-completions walk — PROD-3572)
        OpenAI now enforces the `model.request` scope on GET /v1/chat/completions,
        which Admin keys cannot hold. Each project's stored-completions walk uses a
        project-scoped key derived from the project's display name:
        "Memory [Production]" → OPENAI_MEMORY_API_KEY,
        "Conversation [Production]" → OPENAI_CONVERSATION_API_KEY, etc.
        A project whose key is unset is skipped (stderr warning), not hard-failed.
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
LEDGER_EPOCH_LABEL = "2026-07-23T03:25Z"

# Tier labels are dimension-specific so a reader can tell at a glance whether
# a row refers to Aurie's AI premium (the AI-features subscription, tracked
# by metadata.aiPremium*) or Aura's content premium (tracked by metadata.
# premium*). "Premium Paid" alone was ambiguous — readers were defaulting to
# the Aura reading even when looking at the AI cut. AI-dimension rows now
# carry an "AI " prefix; content-dimension rows keep the canonical Aura
# "Premium" labels.
AI_TIER_ORDER = [
    "AI Premium Paid",
    "AI Premium Trial",
    "AI Premium Free",
    "AI Premium Payment Pending",
    "Non AI Premium",
    "Never AI Premium",
    "(untagged)",
]

CONTENT_TIER_ORDER = [
    "Premium Paid",
    "Premium Trial",
    "Premium Free",
    "Premium Payment Pending",
    "Non Premium",
    "(untagged)",
]

AI_STATE_LABELS = {
    "paid": "AI Premium Paid",
    "trial": "AI Premium Trial",
    "free": "AI Premium Free",
    "paymentPending": "AI Premium Payment Pending",
}

CONTENT_STATE_LABELS = {
    "paid": "Premium Paid",
    "trial": "Premium Trial",
    "free": "Premium Free",
    "paymentPending": "Premium Payment Pending",
}
COVERAGE_WARN_THRESHOLD = 0.05  # warn if >5% of a project's tokens are untagged
TOP_USERS_PER_TIER = 5

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


# ---------------------------------------------------------------------------
# Project-scoped completions keys (PROD-3572)
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
# IntelligenceService per-call LLM ledger (ENGBACK-1237)
# ---------------------------------------------------------------------------

class LedgerUnavailable(Exception):
    """The IS LLM-call ledger could not serve a slice — caller falls back to
    the stored-chat-completions walk."""


LEDGER_PROJECT_KEYS = frozenset({
    "conversation", "feed", "guardrail", "insights", "intent", "journal",
    "memory", "notification", "onboarding", "profile", "suggestions", "summary",
})


def _ledger_project_key(project_name: str) -> str | None:
    """Map an OpenAI dashboard project name to the IS ledger project key.

    'Memory [Production]' → 'memory'. Non-production projects return None —
    the ledger only carries prod traffic keys, so those projects stay on the
    stored-completions walk.
    """
    match = re.match(r"^(.*?)\s*\[Production\]\s*$", project_name)
    if not match:
        return None
    base = match.group(1).strip()
    if not base:
        return None
    return base[0].lower() + base[1:]


def _iso_z(unix: float) -> str:
    return (
        datetime.fromtimestamp(unix, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def iter_llm_calls(
    start_unix: int, end_unix: int, *, project_key: str | None = None
) -> list[dict]:
    """Read the IntelligenceService per-call LLM ledger (BigQuery `llm.llm_call`) for [start, end).
    Newest-first. Raises LedgerUnavailable so callers can fall back to the stored-completions walk."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    params = {
        "from": datetime.fromtimestamp(start_unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": datetime.fromtimestamp(end_unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider": "openai",
    }
    if project_key:
        params["project"] = project_key
    try:
        return llm_ledger.rows(params)
    except llm_ledger.LedgerError as exc:
        raise LedgerUnavailable(str(exc)) from exc


def _ledger_row_to_completion(row: dict) -> dict:
    """Normalize one ledger row into the stored-completion item shape so
    _extract_completion_fields / allocate_dollars stay untouched.

    Ledger equivalents of the OpenAI metadata fields are top-level columns:
    userId → metadata.user, prompterName → metadata.name,
    aiPremium/aiPremiumState → metadata.aiPremium/aiPremiumState,
    premium/premiumState → metadata.premium/premiumState. The boolean
    aiPremium / premium columns were added 2026-07 (ENGBACK-1237); when present
    they map straight through, so a churned user (aiPremium=false) stays
    "Non AI Premium" instead of collapsing into "Never AI Premium". Rows
    written before that column existed carry aiPremium=null; for those we infer
    a truthy flag from a present *State column, and an absent/null state falls
    into the same missing-metadata handling the skill already applies
    (Never AI Premium / (untagged)).
    """
    metadata: dict = {}
    user_id = row.get("userId")
    if user_id:
        metadata["user"] = user_id
    prompter_name = row.get("prompterName")
    if prompter_name:
        metadata["name"] = prompter_name
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

    created = 0
    created_at = row.get("createdAt")
    if created_at:
        try:
            created = int(
                datetime.fromisoformat(
                    str(created_at).replace("Z", "+00:00")
                ).timestamp()
            )
        except ValueError:
            created = 0

    return {
        "id": row.get("openaiId"),
        "created": created,
        "metadata": metadata,
        "usage": {
            "completion_tokens": int(row.get("outputTokens") or 0),
            "prompt_tokens": int(row.get("inputTokens") or 0),
            "prompt_tokens_details": {
                "cached_tokens": int(row.get("cachedTokens") or 0),
            },
        },
        # Null on rows written before the ENGBACK-1315 deploy — see service_tier_factor.
        "service_tier": row.get("serviceTier"),
    }


def _dedupe_on_id(items: list[dict]) -> list[dict]:
    """Defensive dedupe on the OpenAI object id (ledger openaiId == stored
    completion id) in case of boundary overlap between slices."""
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        oid = item.get("id")
        if oid:
            if oid in seen:
                continue
            seen.add(oid)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Time-window resolution (same shape as eng:analyze-openai-costs)
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
# OpenAI API helpers
# ---------------------------------------------------------------------------

def _request(path: str, params: dict, api_key: str, project_id: str | None = None) -> dict:
    """GET with retry on 429. Optional OpenAI-Project header for project-scoped calls."""
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
            sys.exit(f"OpenAI {r.status_code} on {path}: {r.text[:300]}")
        return r.json()
    sys.exit(f"OpenAI {path}: rate-limited after 5 retries")


def fetch_costs(start_unix: int, end_unix: int, api_key: str) -> list[dict]:
    """Return all cost buckets in [start, end), grouped by project_id."""
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


def iter_completions(
    start_unix: int, end_unix: int, project_id: str, api_key: str
) -> list[dict]:
    """List stored completions for one project within [start, end).

    The chat-completions list endpoint orders newest-first and exposes no time
    filter, so we walk pages until we drop below start_unix.
    """
    out: list[dict] = []
    after: str | None = None
    pages_walked = 0

    while True:
        params: dict = {"limit": COMPLETIONS_PAGE_SIZE}
        if after:
            params["after"] = after
        data = _request(CHAT_COMPLETIONS_PATH, params, api_key, project_id=project_id)
        items = data.get("data", [])
        if not items:
            break
        pages_walked += 1

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
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_by_project_dollars(buckets: list[dict]) -> tuple[dict[str, float], str]:
    """Return ({project_id: total_usd}, currency_label)."""
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


def _truthy(value) -> bool:
    """OpenAI metadata values are always strings — coerce them to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _tier_label(metadata: dict, dimension: str, has_user: bool = False) -> str:
    """Build the composite tier label for one completion.

    dimension == "ai":      reads aiPremium / aiPremiumState
    dimension == "content": reads premium / premiumState

    `has_user` should be True when the completion carries a `metadata.user` (or
    top-level `user`/`input_user`) — used to split the "both fields missing"
    case on the AI dimension. With a user, the user row exists but `aiPremium`
    is NULL in the DB (declared `BOOLEAN` nullable, no default) — that's the
    "Never AI Premium" bucket. Without a user, it's true `(untagged)` pre-
    account / system / batch traffic. The content dimension keeps a single
    `(untagged)` bucket because `premium` defaults to FALSE in DB, so a missing
    field there implies pre-account traffic with no user row.
    """
    if dimension == "ai":
        premium_raw = metadata.get("aiPremium")
        state_raw = metadata.get("aiPremiumState")
        state_labels = AI_STATE_LABELS
        non_premium_label = "Non AI Premium"
        unknown_state_label = "AI Premium"
    else:
        premium_raw = metadata.get("premium")
        state_raw = metadata.get("premiumState")
        state_labels = CONTENT_STATE_LABELS
        non_premium_label = "Non Premium"
        unknown_state_label = "Premium"

    if premium_raw is None and state_raw is None:
        if dimension == "ai" and has_user:
            return "Never AI Premium"
        return "(untagged)"

    if not _truthy(premium_raw):
        return non_premium_label

    if state_raw and state_raw in state_labels:
        return state_labels[state_raw]
    # Premium with an unknown state — preserve the raw value so it surfaces.
    return f"{unknown_state_label} {state_raw}" if state_raw else unknown_state_label


def _extract_completion_fields(item: dict) -> tuple[str, str, str, int, float]:
    """Pull (ai_tier, content_tier, user_id, total_tokens, weight) from one completion.

    `total_tokens` stays raw so tagging-coverage stats keep counting tokens; `weight`
    is the same count scaled by the call's service-tier price multiplier and is the
    only correct basis for splitting dollars.
    """
    metadata = item.get("metadata") or {}
    usage = item.get("usage") or {}

    # Handle both Chat Completions (prompt/completion_tokens) and
    # Responses-API-style (input/output_tokens) shapes defensively.
    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    total_tokens = (input_tokens or 0) + (output_tokens or 0)

    # Aura tags Firebase UID under metadata.user (see ENG-498). Fall back to
    # the top-level OpenAI user / input_user fields just in case.
    user_id = (
        metadata.get("user")
        or item.get("user")
        or item.get("input_user")
        or "(unknown)"
    )
    has_user = user_id != "(unknown)"

    ai_tier = _tier_label(metadata, "ai", has_user=has_user)
    content_tier = _tier_label(metadata, "content", has_user=has_user)

    weight = total_tokens * service_tier_factor(item.get("service_tier"))
    return ai_tier, content_tier, user_id, total_tokens, weight


def allocate_dollars(
    per_project_dollars: dict[str, float],
    per_project_completions: dict[str, list[dict]],
) -> dict:
    """Proportionally allocate each project's $ across tiers by token weight.

    Returns a dict with both AI- and content-premium cuts:
      {
        "by_ai":              {tier: $},
        "by_content":         {tier: $},
        "users_by_ai":        {tier: set(uid)},
        "users_by_content":   {tier: set(uid)},
        "user_dollars_ai":    {tier: {uid: $}},
        "user_dollars_content": {tier: {uid: $}},
        "coverage":           {project_id: {total_tokens, ai_untagged_pct,
                                            content_untagged_pct,
                                            completion_count}},
      }
    """
    by_ai: dict[str, float] = defaultdict(float)
    by_content: dict[str, float] = defaultdict(float)
    users_by_ai: dict[str, set] = defaultdict(set)
    users_by_content: dict[str, set] = defaultdict(set)
    user_dollars_ai: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    user_dollars_content: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    coverage: dict[str, dict] = {}

    for pid, dollars in per_project_dollars.items():
        if dollars <= 0 or pid == "(unattributed)":
            continue
        completions = per_project_completions.get(pid) or []
        if not completions:
            # No tagged data for this project — entire $ falls into (untagged).
            by_ai["(untagged)"] += dollars
            by_content["(untagged)"] += dollars
            coverage[pid] = {
                "total_tokens": 0,
                "ai_untagged_pct": 1.0,
                "content_untagged_pct": 1.0,
                "completion_count": 0,
            }
            continue

        # *_weight drives the dollar split; *_tokens stay raw for coverage stats.
        ai_weight: dict[str, float] = defaultdict(float)
        content_weight: dict[str, float] = defaultdict(float)
        user_ai_weight: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        user_content_weight: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        ai_untagged_tokens = 0
        content_untagged_tokens = 0
        total_tokens = 0
        total_weight = 0.0

        for item in completions:
            ai_tier, content_tier, user_id, t, w = _extract_completion_fields(item)
            if t <= 0:
                continue
            total_tokens += t
            total_weight += w
            ai_weight[ai_tier] += w
            content_weight[content_tier] += w
            user_ai_weight[ai_tier][user_id] += w
            user_content_weight[content_tier][user_id] += w
            if ai_tier == "(untagged)":
                ai_untagged_tokens += t
            if content_tier == "(untagged)":
                content_untagged_tokens += t

        if total_tokens == 0:
            by_ai["(untagged)"] += dollars
            by_content["(untagged)"] += dollars
            coverage[pid] = {
                "total_tokens": 0,
                "ai_untagged_pct": 1.0,
                "content_untagged_pct": 1.0,
                "completion_count": len(completions),
            }
            continue

        for tier, w in ai_weight.items():
            tier_share = dollars * w / total_weight
            by_ai[tier] += tier_share
            for uid, uw in user_ai_weight[tier].items():
                user_dollars_ai[tier][uid] += dollars * uw / total_weight
                if uid != "(unknown)":
                    users_by_ai[tier].add(uid)

        for tier, w in content_weight.items():
            tier_share = dollars * w / total_weight
            by_content[tier] += tier_share
            for uid, uw in user_content_weight[tier].items():
                user_dollars_content[tier][uid] += dollars * uw / total_weight
                if uid != "(unknown)":
                    users_by_content[tier].add(uid)

        coverage[pid] = {
            "total_tokens": total_tokens,
            "ai_untagged_pct": ai_untagged_tokens / total_tokens,
            "content_untagged_pct": content_untagged_tokens / total_tokens,
            "completion_count": len(completions),
        }

    return {
        "by_ai": dict(by_ai),
        "by_content": dict(by_content),
        "users_by_ai": {t: set(u) for t, u in users_by_ai.items()},
        "users_by_content": {t: set(u) for t, u in users_by_content.items()},
        "user_dollars_ai": {t: dict(d) for t, d in user_dollars_ai.items()},
        "user_dollars_content": {t: dict(d) for t, d in user_dollars_content.items()},
        "coverage": coverage,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt_money(amount: float, currency: str) -> str:
    symbol = "$" if currency.lower() == "usd" else f"{currency.upper()} "
    return f"{symbol}{amount:,.2f}"


def _tier_sort_key_for(tier_order: list[str]):
    """Build a sort key bound to a dimension's tier order."""
    def _key(tier: str) -> tuple[int, str]:
        try:
            return (tier_order.index(tier), tier)
        except ValueError:
            return (len(tier_order), tier)
    return _key


def _render_cut(
    title: str,
    by_tier: dict[str, float],
    users_by_tier: dict[str, set],
    user_dollars: dict[str, dict[str, float]],
    currency: str,
    show_top_users: bool,
    tier_order: list[str],
) -> list[str]:
    lines: list[str] = []
    lines.append(f"## {title}")
    lines.append("")
    total = sum(by_tier.values())
    if total <= 0:
        lines.append("_No spend recorded in this window._")
        return lines

    header = "| Tier | Spend | Share | Active users | $/user |"
    sep = "|---|---:|---:|---:|---:|"
    if show_top_users:
        header = header[:-1] + " Top user |"
        sep = sep[:-1] + "---:|"
    lines.append(header)
    lines.append(sep)

    tiers = sorted(by_tier.keys(), key=_tier_sort_key_for(tier_order))
    for tier in tiers:
        amount = by_tier[tier]
        pct = amount / total * 100 if total else 0
        active = len(users_by_tier.get(tier) or set())
        per_user = (amount / active) if active else 0
        per_user_cell = _fmt_money(per_user, currency) if active else "—"
        row = (
            f"| {tier} | {_fmt_money(amount, currency)} | {pct:.1f}% | "
            f"{active if active else '—'} | {per_user_cell} |"
        )
        if show_top_users:
            user_dollar_map = user_dollars.get(tier) or {}
            tagged = {u: v for u, v in user_dollar_map.items() if u != "(unknown)"}
            if tagged:
                top_uid, top_amt = max(tagged.items(), key=lambda kv: kv[1])
                row += f" {_fmt_money(top_amt, currency)} ({_short_uid(top_uid)}) |"
            else:
                row += " — |"
        lines.append(row)

    if show_top_users:
        any_top = False
        body_lines: list[str] = []
        for tier in tiers:
            if tier == "(untagged)":
                continue
            user_dollar_map = user_dollars.get(tier) or {}
            tagged = {u: v for u, v in user_dollar_map.items() if u != "(unknown)"}
            if not tagged:
                continue
            top = sorted(tagged.items(), key=lambda kv: kv[1], reverse=True)[:TOP_USERS_PER_TIER]
            any_top = True
            body_lines.append("")
            body_lines.append(f"**Top spenders — {tier}**")
            body_lines.append("")
            body_lines.append("| User | Spend |")
            body_lines.append("|---|---:|")
            for uid, amt in top:
                body_lines.append(f"| `{uid}` | {_fmt_money(amt, currency)} |")
        if any_top:
            lines.extend(body_lines)

    return lines


def _short_uid(uid: str) -> str:
    if len(uid) <= 14:
        return uid
    return f"{uid[:8]}…{uid[-4:]}"


def render(
    label: str,
    total_window_dollars: float,
    allocation: dict,
    currency: str,
    dimension: str,
    show_top_users: bool,
    attribution_source: str,
) -> str:
    lines: list[str] = []
    lines.append(f"# OpenAI Spend by Subscription State — {label}")
    lines.append("")
    lines.append(f"**Attribution source:** {attribution_source}")
    lines.append("")

    by_ai = allocation["by_ai"]
    by_content = allocation["by_content"]

    untagged_ai = by_ai.get("(untagged)", 0.0)
    untagged_content = by_content.get("(untagged)", 0.0)
    tagged_ai = sum(v for k, v in by_ai.items() if k != "(untagged)")
    tagged_content = sum(v for k, v in by_content.items() if k != "(untagged)")

    # Headline summary — use the AI cut's tagged total as the canonical number
    # (both cuts share the same dollar pool; only the breakdown differs).
    tagged_tiers_ai = sum(1 for k, v in by_ai.items() if k != "(untagged)" and v > 0)
    untagged_pct = (untagged_ai / total_window_dollars * 100) if total_window_dollars else 0
    lines.append(
        f"**Total spend:** {_fmt_money(total_window_dollars, currency)} — "
        f"{_fmt_money(tagged_ai, currency)} tagged across {tagged_tiers_ai} tier(s), "
        f"{_fmt_money(untagged_ai, currency)} untagged ({untagged_pct:.1f}%)"
    )
    lines.append("")

    if dimension in ("ai", "both"):
        lines.extend(
            _render_cut(
                "By AI premium tier (aiPremium + aiPremiumState)",
                by_ai,
                allocation["users_by_ai"],
                allocation["user_dollars_ai"],
                currency,
                show_top_users,
                AI_TIER_ORDER,
            )
        )
        lines.append("")

    if dimension in ("content", "both"):
        lines.extend(
            _render_cut(
                "By content premium tier (premium + premiumState)",
                by_content,
                allocation["users_by_content"],
                allocation["user_dollars_content"],
                currency,
                show_top_users,
                CONTENT_TIER_ORDER,
            )
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def rollout_window_warning(start_unix: int, end_unix: int) -> str | None:
    """Return a markdown banner when the requested window predates or straddles
    the 2026-05-12 aiPremium*/premium* tagging rollout. None if entirely
    post-rollout.

    Without this banner, readers misread the inflated `(untagged)` rows as a
    tagging regression rather than the expected effect of the rollout window.
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
        "`(untagged)` regardless of subscription state. The `(untagged)` slice "
        "in this report is a mix of pre-rollout traffic and the (small) real "
        "untagged baseline — treat its share as overstated until the window "
        "is entirely post-rollout."
    )


def render_coverage_warnings(
    coverage: dict[str, dict],
    projects: dict[str, dict],
    per_project_dollars: dict[str, float],
) -> str:
    """Render a warning block when any project has >5% untagged tokens."""
    flagged: list[tuple[str, dict]] = []
    for pid, cov in coverage.items():
        if cov.get("ai_untagged_pct", 0) > COVERAGE_WARN_THRESHOLD or cov.get(
            "content_untagged_pct", 0
        ) > COVERAGE_WARN_THRESHOLD:
            flagged.append((pid, cov))

    if not flagged:
        return ""

    lines: list[str] = []
    lines.append("## ⚠️ Coverage warnings")
    lines.append("")
    lines.append(
        "Projects below have >5% of tokens missing `aiPremiumState` or `premiumState`. "
        "ENG-498 / ENG-500 may have regressed for these paths, or the traffic is "
        "pre-account / system / batch (which legitimately omits tags)."
    )
    lines.append("")
    lines.append("| Project | Completions | ai_untagged | content_untagged | Project spend |")
    lines.append("|---|---:|---:|---:|---:|")
    for pid, cov in sorted(flagged, key=lambda kv: per_project_dollars.get(kv[0], 0), reverse=True):
        name = (projects.get(pid) or {}).get("name", pid)
        lines.append(
            f"| {name} | {cov.get('completion_count', 0):,} | "
            f"{cov.get('ai_untagged_pct', 0) * 100:.1f}% | "
            f"{cov.get('content_untagged_pct', 0) * 100:.1f}% | "
            f"${per_project_dollars.get(pid, 0):,.2f} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Attribution-source assembly (ledger primary, stored completions fallback)
# ---------------------------------------------------------------------------

def _stored_completions_slice(
    pid: str, pname: str, slice_start: int, slice_end: int
) -> tuple[list[dict], bool]:
    """Existing stored-chat-completions walk, scoped to one time slice.
    Chat-API-only — it cannot see Responses-API traffic."""
    completions_key = _resolve_completions_api_key(pname)
    if completions_key is None:
        return [], True
    try:
        return iter_completions(slice_start, slice_end, pid, completions_key), False
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — surface and continue with empty list
        print(
            f"⚠️  Failed to fetch completions for {pname} ({pid}): {e}",
            file=sys.stderr,
        )
        return [], True


def fetch_attribution_items(
    per_project_dollars: dict[str, float],
    projects: dict[str, dict],
    start_unix: int,
    end_unix: int,
) -> tuple[dict[str, list[dict]], str]:
    """Build per-project attribution items for [start, end).

    Slice the window at the ledger epoch:
      - slice_before = [start, min(end, epoch)) — always the stored-chat-
        completions walk; the ledger has no rows before the epoch.
      - slice_after = [max(start, epoch), end) — LLM ledger primary; falls
        back to the stored-completions walk if the ledger is unavailable.
    Merged results are deduped on the OpenAI object id.

    Returns (per_project_items, attribution_source_line).
    """
    before_start, before_end = start_unix, min(end_unix, LEDGER_EPOCH_UNIX)
    after_start, after_end = max(start_unix, LEDGER_EPOCH_UNIX), end_unix
    has_before = before_start < before_end
    has_after = after_start < after_end

    per_project_items: dict[str, list[dict]] = {}
    sources_used: set[str] = set()
    ledger_disabled_reason: str | None = None

    for pid, dollars in per_project_dollars.items():
        if dollars <= 0 or pid == "(unattributed)":
            continue
        pname = (projects.get(pid) or {}).get("name", pid)
        items: list[dict] = []
        partial_epoch_unsafe = False

        if has_before:
            before_items, before_failed = _stored_completions_slice(pid, pname, before_start, before_end)
            partial_epoch_unsafe = partial_epoch_unsafe or before_failed
            items.extend(before_items)
            sources_used.add("completions-pre-epoch")

        if has_after:
            ledger_key = _ledger_project_key(pname)
            rows: list[dict] | None = None
            # Attempt the ledger per project — a failure on one project must not
            # disable it for the rest of the run (iter_llm_calls already retries
            # transient blips). ledger_disabled_reason records the last failure
            # for the attribution annotation only; it no longer gates projects.
            if ledger_key and ledger_key in LEDGER_PROJECT_KEYS:
                try:
                    rows = iter_llm_calls(
                        after_start, after_end, project_key=ledger_key
                    )
                except LedgerUnavailable as exc:
                    _fail_closed_on_ledger_outage(exc)
                    ledger_disabled_reason = str(exc)
                    print(
                        f"WARNING: ledger unavailable ({exc}): falling back to "
                        f"stored chat completions for "
                        f"{_iso_z(after_start)} → {_iso_z(after_end)}. "
                        "Note: the completions fallback is chat-API-only and "
                        "cannot see Responses-API traffic.",
                        file=sys.stderr,
                    )
            else:
                print(
                    f"NOTE: '{pname}' is not served by the LLM ledger; using "
                    "stored chat completions for the post-epoch "
                    "slice (chat-API-only).",
                    file=sys.stderr,
                )
            if rows is not None:
                items.extend(_ledger_row_to_completion(row) for row in rows)
                sources_used.add("ledger")
            else:
                after_items, after_failed = _stored_completions_slice(pid, pname, after_start, after_end)
                partial_epoch_unsafe = partial_epoch_unsafe or after_failed
                items.extend(after_items)
                sources_used.add("completions-fallback")

        if has_before and has_after and partial_epoch_unsafe:
            per_project_items[pid] = []
        else:
            per_project_items[pid] = _dedupe_on_id(items)

    source_line = _attribution_source_label(
        has_before, has_after, sources_used, ledger_disabled_reason
    )
    return per_project_items, source_line


def _attribution_source_label(
    has_before: bool,
    has_after: bool,
    sources_used: set[str],
    ledger_disabled_reason: str | None,
) -> str:
    """One-line data-source annotation for the rendered report."""
    if has_before and has_after:
        detail = f"window straddles ledger epoch {LEDGER_EPOCH_LABEL}"
        if "ledger" not in sources_used and ledger_disabled_reason:
            detail += (
                f"; ledger unavailable ({ledger_disabled_reason}) — post-epoch "
                "slice from stored chat completions, chat-API-only"
            )
        return f"mixed ({detail})"
    if has_after:
        if "ledger" in sources_used and "completions-fallback" not in sources_used:
            return f"LLM ledger (BigQuery llm.llm_call)"
        if "ledger" in sources_used:
            return (
                f"mixed (LLM ledger (BigQuery llm.llm_call) + stored chat completions "
                "for projects without a ledger key)"
            )
        reason = ledger_disabled_reason or "no ledger-keyed projects"
        return (
            f"stored chat completions (ledger unavailable: {reason} — "
            "chat-API-only, Responses-API traffic not visible)"
        )
    return (
        "stored chat completions "
        f"(window entirely before ledger epoch {LEDGER_EPOCH_LABEL})"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Ensure stdout can emit the Unicode arrows used in the rendered Markdown
    # report on Windows consoles that default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="OpenAI spend report by subscription state")
    ap.add_argument(
        "--window",
        choices=["month-to-date", "previous-month", "last-7d", "last-30d"],
        default="month-to-date",
    )
    ap.add_argument("--since", help="UTC start date (YYYY-MM-DD). Use with --until.")
    ap.add_argument("--until", help="UTC end date, exclusive (YYYY-MM-DD).")
    ap.add_argument(
        "--dimension",
        choices=["ai", "content", "both"],
        default="both",
        help="Which cut to render. Default 'both'.",
    )
    ap.add_argument(
        "--no-top-users",
        action="store_true",
        help="Suppress the per-tier top-users ranking (shorter output, Slack-friendly).",
    )
    ap.add_argument("--json", dest="json_out", help="Write structured JSON to this path.")
    ap.add_argument("--allow-degraded-attribution", action="store_true",
                   help="Accept degraded per-user attribution when the LLM ledger is "
                        "unreachable on a post-2026-07-23 window (default: exit 2). "
                        "Env: AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1")
    args = ap.parse_args()
    _ALLOW_DEGRADED[0] = _ALLOW_DEGRADED[0] or bool(
        getattr(args, "allow_degraded_attribution", False))

    api_key = os.environ.get("OPENAI_ADMIN_API_KEY")
    if not api_key:
        sys.exit(
            "Missing OPENAI_ADMIN_API_KEY. Create an Admin API key at "
            "https://platform.openai.com/settings/organization/admin-keys "
            "and add it to ~/.claude/settings.json under the `env` block."
        )

    start_unix, end_unix, label = resolve_window(args.window, args.since, args.until)

    cost_buckets = fetch_costs(start_unix, end_unix, api_key)
    per_project_dollars, currency = aggregate_by_project_dollars(cost_buckets)
    projects = fetch_projects(api_key)

    # Build attribution items only for projects that actually have spend.
    # Zero-cost projects don't need allocation — they contribute $0 to every
    # tier. Post-epoch slices read the IS LLM ledger first (ENGBACK-1237),
    # with failover to the stored-chat-completions walk.
    per_project_completions, attribution_source = fetch_attribution_items(
        per_project_dollars, projects, start_unix, end_unix
    )

    allocation = allocate_dollars(per_project_dollars, per_project_completions)

    total_window_dollars = sum(
        v for k, v in per_project_dollars.items() if k != "(unattributed)"
    )

    report = render(
        label,
        total_window_dollars,
        allocation,
        currency,
        args.dimension,
        not args.no_top_users,
        attribution_source,
    )
    rollout_warning = rollout_window_warning(start_unix, end_unix)
    if rollout_warning:
        # Echo to stderr so the warning surfaces in logs / pipes even when
        # stdout is being captured to a file.
        print(rollout_warning, file=sys.stderr)
        # And prepend to the rendered report so it lands above the headline
        # in chat / Slack readouts.
        report = rollout_warning + "\n\n" + report
    print(report)

    warnings = render_coverage_warnings(
        allocation["coverage"], projects, per_project_dollars
    )
    if warnings:
        print(warnings)

    if args.json_out:
        def _tier_payload(
            by_tier: dict[str, float],
            users: dict[str, set],
            user_dollars: dict[str, dict[str, float]],
            tier_order: list[str],
        ) -> list[dict]:
            out = []
            for tier in sorted(by_tier.keys(), key=_tier_sort_key_for(tier_order)):
                amount = by_tier[tier]
                tagged_users = {
                    u: v
                    for u, v in (user_dollars.get(tier) or {}).items()
                    if u != "(unknown)"
                }
                top = sorted(tagged_users.items(), key=lambda kv: kv[1], reverse=True)[
                    :TOP_USERS_PER_TIER
                ]
                out.append(
                    {
                        "tier": tier,
                        "amount": amount,
                        "active_users": len(users.get(tier) or set()),
                        "top_users": [{"uid": uid, "amount": amt} for uid, amt in top],
                    }
                )
            return out

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
                "ledger_epoch_iso": datetime.fromtimestamp(
                    LEDGER_EPOCH_UNIX, tz=timezone.utc
                ).isoformat(),
                "ledger_epoch_unix": LEDGER_EPOCH_UNIX,
                "source": attribution_source,
            },
            "currency": currency,
            "total": total_window_dollars,
            "untagged_amount": allocation["by_ai"].get("(untagged)", 0.0),
            "by_ai_premium_state": _tier_payload(
                allocation["by_ai"],
                allocation["users_by_ai"],
                allocation["user_dollars_ai"],
                AI_TIER_ORDER,
            ),
            "by_premium_state": _tier_payload(
                allocation["by_content"],
                allocation["users_by_content"],
                allocation["user_dollars_content"],
                CONTENT_TIER_ORDER,
            ),
            "coverage": allocation["coverage"],
        }
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)


if __name__ == "__main__":
    main()
