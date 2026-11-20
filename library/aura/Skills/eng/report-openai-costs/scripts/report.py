#!/usr/bin/env python3
"""
One-pane consolidated daily OpenAI cost snapshot for the Aura org.

Pulls authoritative per-project dollars from the OpenAI Costs API (same source
as `eng:analyze-openai-costs`), then splits those dollars across
(prompter, uid, tier) dimensions by token weight. The token/attribution walk
is ledger-first: for the portion of the window at or after the LLM-call
ledger epoch (2026-07-23T03:25Z, IS 0.285.0) the IntelligenceService per-call
ledger (BigQuery llm.llm_call) is the primary source, with automatic failover to
the per-project stored-chat-completions walk if the ledger is unreachable.
Pre-epoch portions always use stored chat completions (the ledger has no rows
before the epoch). Note the completions fallback is chat-API-only and cannot
see Responses-API traffic. Renders four sections:

  1. Day totals — total $ + 7-day rolling-average comparison
  2. Per-prompter table — sorted desc by cost
  3. Top 5 users across all projects — Firebase UID + AI premium state
  4. By-tier mini-summary — Premium Paid / Trial / Free / Payment Pending /
                            Never AI Premium / Non Premium

Allocation method: proportional to total tokens within each project — sidesteps
having to maintain a model-pricing table. See references/allocation.md and
`Tools/OpenAI` → "Cost computation methodology" in AI Knowledge for the
canonical rationale.

Usage:
    python report.py                                   # yesterday UTC (default)
    python report.py --window today                    # partial; flags incompleteness
    python report.py --window last-7d                  # rolling 7d
    python report.py --window last-30d                 # rolling 30d
    python report.py --since 2026-05-15 --until 2026-05-17    # custom (UTC, half-open)
    python report.py --no-top-users                    # suppress per-user ranking
    python report.py --post-to-slack                   # post summary to #eng-budget
    python report.py --json out.json                   # write structured JSON

Env:
    OPENAI_ADMIN_API_KEY  (Admin API key, sk-admin-...; required)
                          Used ONLY for the /organization/* Costs API calls.
    OPENAI_<PROJECT>_API_KEY  (project-scoped service-account keys; PROD-3572)
                          One per production project, derived from the project's
                          display name — e.g. Memory -> OPENAI_MEMORY_API_KEY,
                          Conversation -> OPENAI_CONVERSATION_API_KEY. Required for
                          the per-project GET /chat/completions stored-completions
                          walk. OPENAI_ADMIN_API_KEY can NEVER hold the model.request
                          scope OpenAI now enforces on that endpoint, so a project's
                          completions walk is skipped (dollars still reconcile via the
                          no-completions sentinel) when its key isn't set.
    GCP_KEY               (service account; required for the IS LLM-call ledger
                          at BigQuery llm.llm_call — the primary
                          post-epoch attribution source. Missing key degrades
                          to the stored-completions walk with a stderr warning.)
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
CHAT_COMPLETIONS_PATH = "/chat/completions"

COSTS_PAGE_SIZE = 31  # API caps at 31 for bucket_width=1d
COMPLETIONS_PAGE_SIZE = 100

# Production projects we care about. Skipped: Development clones, low-volume
# projects (Translation, Content, AI Creative, Claude, Front, Lovable),
# Playground, and Evaluations (separate concern).
PROD_PROJECTS = {
    "Memory":       "proj_f0PpUSyYnJNifUZj44FrjUhr",
    "Conversation": "proj_esFyd4eq2VIAjKUn69X1ZFfk",
    "Feed":         "proj_CrV8L2r3qubmWpSivrmKzGte",
    "Summary":      "proj_JCxJUnGGW5UAgHT4sg8SCPao",
    "Notification": "proj_LWc1KcDszJWEeHoEli8mGXOc",
    "Profile":      "proj_9KAKHkB00hwiipSGfPjmyiJM",
    "Journal":      "proj_qXZ9CZnjp4mL5PVsW43JyOpj",
    "Onboarding":   "proj_hnKIQ8RiS1imMjyBwr9ERR68",
}

STATE_LABELS = {
    "paid": "Premium Paid",
    "trial": "Premium Trial",
    "free": "Premium Free",
    "paymentPending": "Premium Payment Pending",
}

TIER_ORDER = [
    "Premium Paid",
    "Premium Trial",
    "Premium Free",
    "Premium Payment Pending",
    "Non Premium",
    "Never AI Premium",
    "(untagged)",
]

# Date the IntelligenceService metadata-tagging PRs (ENG-498 for aiPremium*,
# ENG-500 for premium*) shipped to production. Calls stored before this date
# carry NONE of the four subscription-state fields. See `Tools/OpenAI` →
# "Rollout date and the data gap" for full context.
TAGGING_ROLLOUT_UNIX = int(datetime(2026, 5, 12, tzinfo=timezone.utc).timestamp())

# IntelligenceService per-call LLM ledger (primary attribution source for the
# post-epoch slice of any window). One row per OpenAI call (Chat Completions
# AND Responses APIs). Docs:
# https://app.notion.com/p/aurahealth/LLM-Calls-3a675e23af208127b217e53af47524e8

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

LEDGER_EPOCH_UNIX = 1784777100  # 2026-07-23T03:25:00Z — the ledger went live in Release 0.285.0. There are NO ledger rows before this moment.
LEDGER_EPOCH_LABEL = "2026-07-23T03:25Z"

DEFAULT_TOP_N = 5
ANOMALY_THRESHOLD = 0.20  # flag day-vs-7d-avg deltas exceeding ±20%


# ---------------------------------------------------------------------------
# Time-window resolution
# ---------------------------------------------------------------------------

def resolve_window(window: str | None, since: str | None, until: str | None) -> tuple[int, int, str, bool]:
    """Return (start_ts, end_ts, label, is_partial)."""
    now = datetime.now(tz=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if since or until:
        if not (since and until):
            raise ValueError("--since and --until must be used together")
        s = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
        u = datetime.fromisoformat(until).replace(tzinfo=timezone.utc)
        if u <= s:
            raise ValueError("--until must be after --since")
        label = f"{s.strftime('%Y-%m-%d')} to {u.strftime('%Y-%m-%d')} UTC"
        return int(s.timestamp()), int(u.timestamp()), label, False

    w = (window or "yesterday").lower()
    if w == "yesterday":
        end = today_start
        start = end - timedelta(days=1)
        return int(start.timestamp()), int(end.timestamp()), f"Yesterday UTC ({start.strftime('%Y-%m-%d')})", False
    if w == "today":
        return int(today_start.timestamp()), int(now.timestamp()), f"Today UTC ({today_start.strftime('%Y-%m-%d')}, partial)", True
    if w in ("last-7d", "last7d", "7d"):
        start = now - timedelta(days=7)
        return int(start.timestamp()), int(now.timestamp()), "Last 7 days UTC (rolling)", False
    if w in ("last-30d", "last30d", "30d"):
        start = now - timedelta(days=30)
        return int(start.timestamp()), int(now.timestamp()), "Last 30 days UTC (rolling)", False

    raise ValueError(f"Unknown --window {window!r}. Use yesterday | today | last-7d | last-30d, or --since/--until.")


# ---------------------------------------------------------------------------
# OpenAI API helpers
# ---------------------------------------------------------------------------

def _get(path: str, params: dict, headers: dict, retries: int = 3) -> dict:
    """GET with retry on 429."""
    url = f"{OPENAI_API}{path}"
    for attempt in range(retries):
        r = requests.get(url, params=params, headers=headers)
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def fetch_costs_by_project(api_key: str, start: int, end: int) -> dict[str, float]:
    """Returns {project_id: total_dollars} for the window."""
    out: dict[str, float] = {}
    params = {
        "start_time": start,
        "end_time": end,
        "bucket_width": "1d",
        "limit": COSTS_PAGE_SIZE,
        "group_by[]": "project_id",
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    # Paginate over next_page if needed
    while True:
        body = _get(COSTS_PATH, params, headers)
        for bucket in body.get("data", []):
            for row in bucket.get("results", []):
                pid = row.get("project_id")
                if not pid:
                    continue
                out[pid] = out.get(pid, 0.0) + float(row["amount"]["value"])
        if not body.get("has_more"):
            break
        params["page"] = body.get("next_page")
        if not params["page"]:
            break
    return out


def _project_completions_api_key_env(project_name: str) -> str:
    """Derive the project-scoped env var name from a project's display name
    (PROD-3572): "Memory [Production]" -> "OPENAI_MEMORY_API_KEY"."""
    base = re.sub(r"\[.*?\]", "", project_name).strip()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").upper()
    return f"OPENAI_{base}_API_KEY"


def _resolve_completions_api_key(project_name: str) -> str | None:
    """Resolve the project-scoped API key for a stored-completions walk (PROD-3572).
    Returns None (with a stderr warning) when the project's key isn't set, so the
    caller can skip that project's completions walk instead of hard-failing —
    matching this script's existing per-project degradation. OPENAI_ADMIN_API_KEY
    no longer works for GET /v1/chat/completions."""
    env_name = _project_completions_api_key_env(project_name)
    token = os.environ.get(env_name)
    if not token:
        print(
            f"WARNING: {env_name} not set — skipping stored-completions walk for "
            f"'{project_name}'. OPENAI_ADMIN_API_KEY no longer works for "
            f"/chat/completions (PROD-3572); create a service-account key for "
            f"this project at https://platform.openai.com/api-keys and set "
            f"{env_name} in ~/.claude/settings.json.",
            file=sys.stderr,
        )
    return token


def fetch_completions(api_key: str, project_id: str, start: int, end: int):
    """Yield completions in [start, end) for one project. Walks backward from
    the most recent completion (the API returns newest first) and stops once
    we cross the start boundary."""
    headers = {"Authorization": f"Bearer {api_key}", "OpenAI-Project": project_id}
    after = None
    while True:
        params: dict = {"limit": COMPLETIONS_PAGE_SIZE}
        if after:
            params["after"] = after
        body = _get(CHAT_COMPLETIONS_PATH, params, headers)
        items = body.get("data", [])
        if not items:
            return
        stop = False
        for it in items:
            created = it.get("created", 0)
            if created >= end:
                continue
            if created < start:
                stop = True
                break
            yield it
        if stop or not body.get("has_more"):
            return
        after = items[-1].get("id")
        if not after:
            return
        time.sleep(0.05)  # gentle rate-limit cushion


# ---------------------------------------------------------------------------
# IS LLM-call ledger (primary post-epoch attribution source)
# ---------------------------------------------------------------------------

class LedgerUnavailable(Exception):
    """Raised when the IS LLM-call ledger can't be used for a slice, so the
    caller can fall back to the stored-completions walk."""


LEDGER_PROJECT_KEYS = frozenset({
    "conversation", "feed", "guardrail", "insights", "intent", "journal",
    "memory", "notification", "onboarding", "profile", "suggestions", "summary",
})


def _unix_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _ledger_project_key(project_name: str) -> str:
    """Map an OpenAI project display name to the IS ledger project key:
    strip any ' [Production]'/' [Development]' suffix and lowercase the first
    letter — 'Memory [Production]' -> 'memory', 'Conversation' -> 'conversation'."""
    base = re.sub(r"\s*\[.*?\]\s*$", "", project_name).strip()
    return base[:1].lower() + base[1:] if base else base


def iter_llm_calls(start_unix: int, end_unix: int, *, project_key: str | None = None):
    """Read the IntelligenceService per-call LLM ledger (BigQuery `llm.llm_call`) for [start, end).
    Newest-first. Raises LedgerUnavailable so callers can fall back to the stored-completions walk."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    params = {
        "from": datetime.fromtimestamp(start_unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": datetime.fromtimestamp(end_unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    params["provider"] = "openai"
    if project_key:
        params["project"] = project_key
    try:
        return llm_ledger.rows(params)
    except llm_ledger.LedgerError as exc:
        raise LedgerUnavailable(str(exc)) from exc


# ---------------------------------------------------------------------------
# Tier / prompter labelling
# ---------------------------------------------------------------------------

def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def ai_tier_label(metadata: dict, has_user: bool) -> str:
    """Composite AI-premium tier label. Mirrors the convention used by
    eng:analyze-openai-cost-per-subscription-state."""
    premium_raw = metadata.get("aiPremium")
    state_raw = metadata.get("aiPremiumState")
    if premium_raw is None and state_raw is None:
        return "Never AI Premium" if has_user else "(untagged)"
    if not _truthy(premium_raw):
        return "Non Premium"
    if state_raw and state_raw in STATE_LABELS:
        return STATE_LABELS[state_raw]
    return f"Premium {state_raw}" if state_raw else "Premium"


def extract_fields(item: dict) -> tuple[str, str, str, float]:
    """Return (prompter, uid, ai_tier, weight).

    `weight` is the call's token count scaled by its service-tier price
    multiplier — an allocation basis, not a token count.
    """
    metadata = item.get("metadata") or {}
    usage = item.get("usage") or {}

    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    total_tokens = ((input_tokens or 0) + (output_tokens or 0)) * service_tier_factor(
        item.get("service_tier")
    )

    prompter = metadata.get("name") or "(no-name)"
    uid = (
        metadata.get("user")
        or item.get("user")
        or item.get("input_user")
        or "(no-uid)"
    )
    has_user = uid != "(no-uid)"
    tier = ai_tier_label(metadata, has_user)
    return prompter, uid, tier, total_tokens


def extract_ledger_fields(row: dict) -> tuple[str, str, str, int]:
    """Return (prompter, uid, ai_tier, total_tokens) from a ledger row —
    same internal shape as extract_fields() so downstream allocation is
    untouched. Column mapping vs completion metadata:
    metadata.user -> userId, metadata.name -> prompterName,
    metadata.aiPremium -> aiPremium, metadata.aiPremiumState -> aiPremiumState,
    usage.prompt_tokens -> inputTokens, usage.completion_tokens -> outputTokens.

    The aiPremium boolean is its own ledger column (added 2026-07 —
    ENGBACK-1237); map it into the metadata shape so the shared
    `ai_tier_label` keeps a churned user (aiPremium=false) as "Non Premium"
    instead of collapsing into "Never AI Premium". Rows predating the column
    carry aiPremium=null and fall back to inferring a truthy flag from a
    present state."""
    input_tokens = row.get("inputTokens") or 0
    output_tokens = row.get("outputTokens") or 0
    # Scaled by the call's service tier — see service_tier_factor. Null on rows
    # written before the ENGBACK-1315 deploy, which weigh 1.0.
    total_tokens = (input_tokens + output_tokens) * service_tier_factor(
        row.get("serviceTier")
    )

    prompter = row.get("prompterName") or "(no-name)"
    uid = row.get("userId") or "(no-uid)"
    has_user = uid != "(no-uid)"

    metadata: dict = {}
    ai_premium = row.get("aiPremium")
    ai_state = row.get("aiPremiumState")
    if ai_premium is not None:
        metadata["aiPremium"] = "true" if ai_premium else "false"
    elif ai_state:
        metadata["aiPremium"] = "true"
    if ai_state:
        metadata["aiPremiumState"] = ai_state
    tier = ai_tier_label(metadata, has_user)
    return prompter, uid, tier, total_tokens


# ---------------------------------------------------------------------------
# Aggregation + allocation
# ---------------------------------------------------------------------------

def gather_project_calls(proj_name: str, pid: str, start: int, end: int):
    """Collect (prompter, uid, tier, tokens) items for one project in
    [start, end), ledger-first with stored-completions failover:

      slice_before = [start, min(end, LEDGER_EPOCH)) — always the stored
        chat-completions walk (the only source for pre-epoch data).
      slice_after  = [max(start, LEDGER_EPOCH), end) — IS LLM ledger primary;
        on LedgerUnavailable, warn and fall back to the completions walk
        (chat-API-only — it cannot see Responses-API traffic).

    Dedupes defensively on the OpenAI object id (ledger openaiId == stored
    completion 'id') in case of boundary overlap. Returns
    (items, sources, key_missing, partial_epoch_unsafe) where sources ⊆
    {'ledger', 'completions'} and key_missing flags a needed-but-unset project
    completions key."""
    items: list[tuple[str, str, str, int]] = []
    sources: set[str] = set()
    seen_ids: set[str] = set()
    completions_slices: list[tuple[int, int]] = []
    key_missing = False

    pre_end = min(end, LEDGER_EPOCH_UNIX)
    has_pre = start < pre_end
    if has_pre:
        completions_slices.append((start, pre_end))

    post_start = max(start, LEDGER_EPOCH_UNIX)
    has_post = post_start < end
    if has_post:
        ledger_key = _ledger_project_key(proj_name)
        if ledger_key not in LEDGER_PROJECT_KEYS:
            print(
                f"note: '{proj_name}' is not served by the LLM ledger; using "
                "stored chat completions for its post-epoch slice.",
                file=sys.stderr,
            )
            completions_slices.append((post_start, end))
        else:
            try:
                rows = iter_llm_calls(post_start, end, project_key=ledger_key)
            except LedgerUnavailable as exc:
                _fail_closed_on_ledger_outage(exc)
                print(
                    f"WARNING: ledger unavailable ({exc}): falling back to stored chat "
                    f"completions for {_unix_to_iso(post_start)}..{_unix_to_iso(end)} "
                    f"({proj_name}). Note: the completions fallback is chat-API-only "
                    f"and cannot see Responses-API traffic.",
                    file=sys.stderr,
                )
                completions_slices.append((post_start, end))
            else:
                sources.add("ledger")
                for row in rows:
                    oid = row.get("openaiId")
                    if oid:
                        if oid in seen_ids:
                            continue
                        seen_ids.add(oid)
                    items.append(extract_ledger_fields(row))

    if completions_slices:
        proj_key = _resolve_completions_api_key(proj_name)
        if not proj_key:
            key_missing = True
        else:
            sources.add("completions")
            for s, e in completions_slices:
                for it in fetch_completions(proj_key, pid, s, e):
                    oid = it.get("id")
                    if oid:
                        if oid in seen_ids:
                            continue
                        seen_ids.add(oid)
                    items.append(extract_fields(it))

    partial_epoch_unsafe = has_pre and has_post and key_missing
    return items, sources, key_missing, partial_epoch_unsafe


def aggregate(api_key: str, start: int, end: int, project_dollars: dict[str, float]):
    """For each project, fold per-completion tokens into per-(prompter, uid,
    tier) aggregates and allocate the project's $ proportionally. Returns:

        {
            "by_prompter": {prompter: {calls, cost}},
            "by_user":     {uid: {calls, cost, tier, projects}},
            "by_tier":     {tier: {calls, cost, users}},
            "totals":      {projects_with_data, projects_skipped_zero_completions,
                            total_calls, total_cost, attribution_sources},
        }
    """
    by_prompter: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0})
    by_user: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0, "tier": "?", "projects": set()})
    by_tier: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost": 0.0, "users": set()})

    projects_with_data = 0
    projects_skipped_zero_completions = 0
    total_calls = 0
    attribution_sources: set[str] = set()

    for proj_name, pid in PROD_PROJECTS.items():
        dollars = project_dollars.get(pid, 0.0)
        if dollars == 0:
            continue
        # Ledger-first attribution walk with stored-completions failover; the
        # completions walk needs a project-scoped key (PROD-3572) since the
        # admin key can't hold model.request. When a needed key is missing and
        # nothing else covered the window, park the billed $ under a sentinel
        # so totals still reconcile — same shape as the zero-completions
        # degradation below.
        proj_items, proj_sources, key_missing, partial_epoch_unsafe = gather_project_calls(proj_name, pid, start, end)
        if partial_epoch_unsafe:
            projects_skipped_zero_completions += 1
            sentinel = f"(partial-epoch-unattributed) {proj_name}"
            by_prompter[sentinel]["cost"] += dollars
            continue
        if not proj_items and key_missing:
            projects_skipped_zero_completions += 1
            sentinel = f"(no-project-key) {proj_name}"
            by_prompter[sentinel]["cost"] += dollars
            continue
        attribution_sources |= proj_sources
        # Aggregate tokens per dimension for this project
        proj_prompter_tok: dict[str, int] = defaultdict(int)
        proj_prompter_n: dict[str, int] = defaultdict(int)
        proj_user_tok: dict[str, int] = defaultdict(int)
        proj_user_n: dict[str, int] = defaultdict(int)
        proj_user_tier: dict[str, str] = {}
        proj_tier_tok: dict[str, int] = defaultdict(int)
        proj_tier_n: dict[str, int] = defaultdict(int)
        proj_tier_users: dict[str, set] = defaultdict(set)
        total_tokens = 0

        for prompter, uid, tier, t in proj_items:
            if t <= 0:
                continue
            proj_prompter_tok[prompter] += t
            proj_prompter_n[prompter] += 1
            proj_user_tok[uid] += t
            proj_user_n[uid] += 1
            proj_user_tier[uid] = tier  # last-seen
            proj_tier_tok[tier] += t
            proj_tier_n[tier] += 1
            proj_tier_users[tier].add(uid)
            total_tokens += t
            total_calls += 1

        if total_tokens == 0:
            # Project billed $ but no attributable calls in the window (e.g.,
            # store=false on the call, or rotated cache). Park the $ under a
            # synthetic prompter so the totals reconcile.
            projects_skipped_zero_completions += 1
            sentinel = f"(no-completions) {proj_name}"
            by_prompter[sentinel]["cost"] += dollars
            continue

        projects_with_data += 1
        # Allocate this project's $ across each dimension
        for prompter, tok in proj_prompter_tok.items():
            share = dollars * tok / total_tokens
            by_prompter[prompter]["cost"] += share
            by_prompter[prompter]["calls"] += proj_prompter_n[prompter]
        for uid, tok in proj_user_tok.items():
            share = dollars * tok / total_tokens
            by_user[uid]["cost"] += share
            by_user[uid]["calls"] += proj_user_n[uid]
            by_user[uid]["tier"] = proj_user_tier.get(uid, by_user[uid]["tier"])
            by_user[uid]["projects"].add(proj_name)
        for tier, tok in proj_tier_tok.items():
            share = dollars * tok / total_tokens
            by_tier[tier]["cost"] += share
            by_tier[tier]["calls"] += proj_tier_n[tier]
            by_tier[tier]["users"] |= proj_tier_users[tier]

    return {
        "by_prompter": dict(by_prompter),
        "by_user": dict(by_user),
        "by_tier": dict(by_tier),
        "totals": {
            "projects_with_data": projects_with_data,
            "projects_skipped_zero_completions": projects_skipped_zero_completions,
            "total_calls": total_calls,
            "total_cost": sum(p["cost"] for p in by_prompter.values()),
            "attribution_sources": sorted(attribution_sources),
        },
    }


def attribution_source_line(sources: list[str] | set[str], window_start: int, window_end: int) -> str:
    """Human-readable annotation of where the token-weight attribution came
    from. Dollars always come from the Costs API; this line covers only the
    attribution walk."""
    sources = set(sources)
    if sources == {"ledger"}:
        return "Attribution source: LLM ledger (BigQuery `llm.llm_call`, chat + Responses APIs)."
    if "ledger" in sources and "completions" in sources:
        if window_start < LEDGER_EPOCH_UNIX <= window_end:
            return (
                f"Attribution source: mixed (window straddles ledger epoch {LEDGER_EPOCH_LABEL}) — "
                "LLM ledger post-epoch, stored chat completions pre-epoch."
            )
        return "Attribution source: mixed — LLM ledger where available, stored chat completions as fallback."
    if window_end <= LEDGER_EPOCH_UNIX:
        return f"Attribution source: stored chat completions (window predates ledger epoch {LEDGER_EPOCH_LABEL})."
    return "Attribution source: stored chat completions (ledger unavailable — chat-API-only, Responses-API traffic not visible)."


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def fmt_money(v: float) -> str:
    return f"${v:.2f}"


def render(agg: dict, label: str, is_partial: bool, baseline_avg: float | None, top_n: int, show_top_users: bool, window_start: int, window_end: int) -> str:
    lines: list[str] = []

    total_cost = agg["totals"]["total_cost"]
    total_calls = agg["totals"]["total_calls"]

    # Header
    header = f"# OpenAI cost snapshot — {label}"
    if is_partial:
        header += " ⚠️ partial day"
    lines.append(header)
    lines.append("")

    headline = f"**Total: {fmt_money(total_cost)}** across {total_calls:,} calls"
    if baseline_avg is not None and baseline_avg > 0:
        delta = (total_cost - baseline_avg) / baseline_avg
        sign = "+" if delta >= 0 else ""
        flag = ""
        if abs(delta) >= ANOMALY_THRESHOLD:
            flag = " ⚠️" if delta > 0 else " ✅"
        headline += f" — {sign}{delta*100:.0f}% vs 7d avg ({fmt_money(baseline_avg)}){flag}"
    lines.append(headline)
    lines.append("")

    # Pre-tagging-rollout warning
    if window_start < TAGGING_ROLLOUT_UNIX:
        lines.append("> ⚠️ Window predates or straddles the 2026-05-12 metadata tagging rollout. The by-tier breakdown will show inflated `(untagged)` rows for the pre-rollout portion — not a tagging regression, just calls made before the tagging code existed.")
        lines.append("")

    # 1. Per-prompter table
    by_prompter = agg["by_prompter"]
    lines.append("## Per prompter")
    lines.append("")
    lines.append("| Prompter | Calls | Cost | $/call | % |")
    lines.append("|---|---:|---:|---:|---:|")
    sorted_prompters = sorted(by_prompter.items(), key=lambda kv: -kv[1]["cost"])
    for prompter, p in sorted_prompters:
        if p["cost"] <= 0:
            continue
        cpc = p["cost"] / p["calls"] if p["calls"] else 0
        pct = (p["cost"] / total_cost * 100) if total_cost else 0
        lines.append(f"| `{prompter}` | {p['calls']:,} | {fmt_money(p['cost'])} | ${cpc:.4f} | {pct:.1f}% |")
    lines.append("")

    # 2. Top users
    if show_top_users:
        ranked = sorted(agg["by_user"].items(), key=lambda kv: -kv[1]["cost"])
        ranked = [(u, d) for u, d in ranked if d["cost"] > 0]
        user_total = sum(d["cost"] for _, d in ranked)
        lines.append(f"## Top {min(top_n, len(ranked))} users (across all projects)")
        lines.append("")
        lines.append("| Rank | UID | State | Calls | Cost | % | Cumul% |")
        lines.append("|---:|---|---|---:|---:|---:|---:|")
        cumul = 0.0
        for i, (uid, d) in enumerate(ranked[:top_n], start=1):
            pct = (d["cost"] / user_total * 100) if user_total else 0
            cumul += pct
            short = uid[:8] + "…" + uid[-4:] if len(uid) > 14 else uid
            lines.append(f"| {i} | `{short}` | {d['tier']} | {d['calls']:,} | {fmt_money(d['cost'])} | {pct:.1f}% | {cumul:.1f}% |")
        lines.append("")
        # One-line followup stat
        if len(ranked) >= top_n:
            top_n_share = sum(d["cost"] for _, d in ranked[:top_n]) / user_total * 100 if user_total else 0
            lines.append(f"Top {top_n} = {top_n_share:.0f}% of user-attributable spend. {len(ranked)} distinct end-users.")
            lines.append("")

    # 3. By-tier mini
    by_tier = agg["by_tier"]
    lines.append("## By tier")
    lines.append("")
    lines.append("| Tier | Users | Calls | Cost | % |")
    lines.append("|---|---:|---:|---:|---:|")
    tier_total = sum(t["cost"] for t in by_tier.values())
    for tier in TIER_ORDER:
        if tier not in by_tier:
            continue
        t = by_tier[tier]
        if t["cost"] <= 0:
            continue
        pct = (t["cost"] / tier_total * 100) if tier_total else 0
        lines.append(f"| {tier} | {len(t['users']):,} | {t['calls']:,} | {fmt_money(t['cost'])} | {pct:.1f}% |")
    # Any tier not in TIER_ORDER (unexpected raw state)
    for tier, t in by_tier.items():
        if tier in TIER_ORDER or t["cost"] <= 0:
            continue
        pct = (t["cost"] / tier_total * 100) if tier_total else 0
        lines.append(f"| {tier} | {len(t['users']):,} | {t['calls']:,} | {fmt_money(t['cost'])} | {pct:.1f}% |")
    lines.append("")

    # Footer with caveats
    skipped = agg["totals"]["projects_skipped_zero_completions"]
    if skipped > 0:
        lines.append(f"_Note: {skipped} project(s) had billed $ but zero attributable calls (ledger or stored completions) in the window — their dollars are parked under `(no-completions) <Project>` / `(no-project-key) <Project>` sentinel rows in the per-prompter table so totals reconcile._")
        lines.append("")
    lines.append(f"_{attribution_source_line(agg['totals'].get('attribution_sources', []), window_start, window_end)}_")
    lines.append("")
    lines.append("_Allocation: per-project $ from `/v1/organization/costs` split proportionally by token weight. Matches OpenAI's authoritative bill by construction. See `Tools/OpenAI` → Cost computation methodology._")
    return "\n".join(lines)


def slack_summary(agg: dict, label: str, baseline_avg: float | None) -> str:
    """Condensed version for #eng-budget — totals + per-prompter + by-tier
    only. No per-user (PII-adjacent)."""
    total_cost = agg["totals"]["total_cost"]
    total_calls = agg["totals"]["total_calls"]
    parts: list[str] = []
    parts.append(f"*OpenAI cost — {label}*")
    line = f"Total *{fmt_money(total_cost)}* across {total_calls:,} calls"
    if baseline_avg is not None and baseline_avg > 0:
        delta = (total_cost - baseline_avg) / baseline_avg
        sign = "+" if delta >= 0 else ""
        line += f" ({sign}{delta*100:.0f}% vs 7d avg {fmt_money(baseline_avg)})"
    parts.append(line)
    parts.append("")
    parts.append("Top prompters:")
    by_prompter = sorted(agg["by_prompter"].items(), key=lambda kv: -kv[1]["cost"])
    for prompter, p in by_prompter[:5]:
        if p["cost"] <= 0:
            continue
        parts.append(f"  • `{prompter}` — {fmt_money(p['cost'])} ({p['calls']:,} calls)")
    parts.append("")
    parts.append("By tier:")
    tier_total = sum(t["cost"] for t in agg["by_tier"].values())
    for tier in TIER_ORDER:
        if tier not in agg["by_tier"]:
            continue
        t = agg["by_tier"][tier]
        if t["cost"] <= 0:
            continue
        pct = (t["cost"] / tier_total * 100) if tier_total else 0
        parts.append(f"  • {tier}: {fmt_money(t['cost'])} ({pct:.0f}%, {len(t['users']):,} users)")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Baseline (7-day rolling average for comparison)
# ---------------------------------------------------------------------------

def fetch_baseline_avg(api_key: str, window_start: int, window_end: int) -> float | None:
    """Average daily total $ across the 7 calendar days ending the day BEFORE
    window_start. Returns None if we can't establish a baseline (e.g., window
    is rolling and overlaps the baseline range)."""
    window_size_days = max(1, (window_end - window_start) // 86400)
    if window_size_days != 1:
        # For non-1-day windows the baseline comparison is less meaningful;
        # skip rather than mislead.
        return None
    baseline_end = window_start
    baseline_start = baseline_end - 7 * 86400
    params = {
        "start_time": baseline_start,
        "end_time": baseline_end,
        "bucket_width": "1d",
        "limit": COSTS_PAGE_SIZE,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        body = _get(COSTS_PATH, params, headers)
    except requests.HTTPError:
        return None
    total = 0.0
    n_days = 0
    for bucket in body.get("data", []):
        day_total = sum(float(row["amount"]["value"]) for row in bucket.get("results", []))
        total += day_total
        n_days += 1
    return (total / n_days) if n_days else None


# ---------------------------------------------------------------------------
# Slack posting (uses MCP via the harness; the script just emits the payload)
# ---------------------------------------------------------------------------

def emit_slack_payload(summary: str) -> None:
    """The script itself doesn't post to Slack — the calling agent does, via
    the Slack MCP. We just write the payload to stderr so the agent can pick
    it up after `python report.py --post-to-slack` returns."""
    print("\n---SLACK-PAYLOAD-BEGIN---", file=sys.stderr)
    print(summary, file=sys.stderr)
    print("---SLACK-PAYLOAD-END---", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Daily OpenAI cost snapshot for the Aura org.")
    parser.add_argument("--window", default="yesterday", help="yesterday | today | last-7d | last-30d")
    parser.add_argument("--since", help="UTC start date (YYYY-MM-DD), half-open with --until")
    parser.add_argument("--until", help="UTC end date (YYYY-MM-DD), half-open with --since")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help=f"Top-N users (default {DEFAULT_TOP_N})")
    parser.add_argument("--no-top-users", action="store_true", help="Suppress the top-users section")
    parser.add_argument("--post-to-slack", action="store_true", help="Emit a Slack payload for the harness to post")
    parser.add_argument("--json", help="Write structured JSON to this path")
    parser.add_argument("--allow-degraded-attribution", action="store_true",
                   help="Accept degraded per-user attribution when the LLM ledger is "
                        "unreachable on a post-2026-07-23 window (default: exit 2). "
                        "Env: AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1")
    args = parser.parse_args()
    _ALLOW_DEGRADED[0] = _ALLOW_DEGRADED[0] or bool(
        getattr(args, "allow_degraded_attribution", False))

    api_key = os.environ.get("OPENAI_ADMIN_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_ADMIN_API_KEY not set", file=sys.stderr)
        return 2

    try:
        start, end, label, is_partial = resolve_window(args.window, args.since, args.until)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Resolving window: {label} ({start} → {end} UTC)", file=sys.stderr)

    # Step 1: authoritative $ per project for the window
    project_dollars = fetch_costs_by_project(api_key, start, end)
    if not project_dollars:
        # Could be a today-bucket lag situation
        if is_partial:
            print("ERROR: today's Costs API bucket has not propagated yet — try again in 1–4 hours, or use --window yesterday.", file=sys.stderr)
            return 1
        print("ERROR: no cost data returned for the window.", file=sys.stderr)
        return 1

    # Step 2: enumerate completions and allocate
    agg = aggregate(api_key, start, end, project_dollars)

    # Step 3: baseline (7-day rolling average) for the header
    baseline_avg = fetch_baseline_avg(api_key, start, end)

    # Step 4: render
    report_md = render(agg, label, is_partial, baseline_avg, args.top, not args.no_top_users, start, end)
    print(report_md)

    # Optional: Slack payload
    if args.post_to_slack:
        slack = slack_summary(agg, label, baseline_avg)
        emit_slack_payload(slack)

    # Optional: JSON output
    if args.json:
        # Stringify sets for JSON serialisation
        json_safe = {
            "window": {"start": start, "end": end, "label": label, "is_partial": is_partial},
            "totals": agg["totals"],
            "attribution_source": attribution_source_line(agg["totals"].get("attribution_sources", []), start, end),
            "baseline_7d_avg": baseline_avg,
            "by_prompter": agg["by_prompter"],
            "by_user": {
                uid: {
                    "calls": d["calls"],
                    "cost": d["cost"],
                    "tier": d["tier"],
                    "projects": sorted(d["projects"]),
                }
                for uid, d in agg["by_user"].items()
            },
            "by_tier": {
                tier: {
                    "calls": d["calls"],
                    "cost": d["cost"],
                    "users": len(d["users"]),
                }
                for tier, d in agg["by_tier"].items()
            },
        }
        with open(args.json, "w") as f:
            json.dump(json_safe, f, indent=2)
        print(f"\nWrote {args.json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
