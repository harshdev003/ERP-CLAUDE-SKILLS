#!/usr/bin/env python3
"""
OpenAI spend report for the Aura organization, broken down by project.

Pulls from the OpenAI Costs API (`/v1/organization/costs`) for the per-project
dollar totals, and optionally from `/v1/organization/usage/completions` for a
secondary per-API-key token-usage view within each project.

Usage:
    python report.py                                    # month-to-date (default)
    python report.py --window previous-month
    python report.py --window last-7d
    python report.py --window last-30d
    python report.py --since 2026-04-01 --until 2026-05-01
    python report.py --window month-to-date --no-api-keys
    python report.py --window month-to-date --json out.json

Env:
    OPENAI_ADMIN_API_KEY  (Admin API key, sk-admin-...; required) -- used for
        /v1/organization/* (costs, usage, projects). Still works there; OpenAI's
        model.request scope only blocks stored-completions reads (PROD-3572).
    GCP_KEY  (service account; used by --by-prompter) -- the
        PRIMARY attribution source for post-epoch slices is the
        IntelligenceService per-call LLM ledger (BigQuery llm.llm_call, one row
        per OpenAI call across both Chat Completions and Responses APIs).
        Missing/failed ledger access degrades to the stored-completions walk
        below with a stderr warning -- it does not fail the run.
    OPENAI_<PROJECT>_API_KEY  (project-scoped service-account key; used by
        --by-prompter for pre-epoch slices and as the ledger fallback, one per
        project with non-zero cost in the window) -- the stored-completions
        path walks GET /v1/chat/completions per project to allocate $ across
        prompters by token weight, which OPENAI_ADMIN_API_KEY can no longer do
        (Admin keys can't hold the model.request scope, by OpenAI design). The
        env var name is derived from the project's display name: "Memory
        [Production]" -> OPENAI_MEMORY_API_KEY, "Conversation [Production]" ->
        OPENAI_CONVERSATION_API_KEY, etc. Missing a project's key degrades
        that project's $ into the "(untagged)" bucket with a stderr warning --
        it does not fail the run (see `_resolve_completions_api_key`).
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
USAGE_COMPLETIONS_PATH = "/organization/usage/completions"
CHAT_COMPLETIONS_PATH = "/chat/completions"

COSTS_PAGE_SIZE = 180  # /organization/costs accepts up to 180 buckets per page
USAGE_PAGE_SIZE = 31   # /organization/usage/* caps at 31 for bucket_width=1d
COMPLETIONS_PAGE_SIZE = 100  # /chat/completions page size when walking per-project stored completions

LEDGER_EPOCH_UNIX = 1784777100  # 2026-07-23T03:25:00Z — the ledger went live in Release 0.285.0. There are NO ledger rows before this moment.
LEDGER_EPOCH_LABEL = "2026-07-23T03:25Z"

LINE_ITEM_TOKEN_KINDS = ("input", "cached input", "output", "cache writes")

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


class OpenAIRequestError(Exception):
    """Raised when a single endpoint call fails after retries.

    Used by --by-prompter so a transient 5xx on one project can be caught and
    that project's $ falls into the untagged bucket instead of killing the run.
    The default path still calls sys.exit on failure for backwards compat.
    """


class LedgerUnavailable(Exception):
    """Raised when the IS LLM-call ledger can't serve a slice (missing token,
    non-200, timeout, JSON parse error). Callers fall back to the
    stored-completions walk for that slice."""


def _project_completions_api_key_env(project_name: str) -> str:
    """Derive the expected project-scoped env var name from a project's
    display name (PROD-3572 convention): "Memory [Production]" ->
    "OPENAI_MEMORY_API_KEY". Strips a trailing "[...]" qualifier and any
    non-alphanumeric separators.
    """
    base = re.sub(r"\[.*?\]", "", project_name).strip()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").upper()
    return f"OPENAI_{base}_API_KEY"


def _resolve_completions_api_key(project_name: str) -> str:
    """Resolve the project-scoped API key for a stored-completions walk (PROD-3572).

    OPENAI_ADMIN_API_KEY no longer works for GET /v1/chat/completions -- only a
    service-account key scoped to the target project does. Raises
    OpenAIRequestError (not sys.exit) so callers on the --by-prompter path can
    fold this project's $ into the untagged bucket and keep going, per the
    existing per-project failure handling.
    """
    env_name = _project_completions_api_key_env(project_name)
    token = os.environ.get(env_name)
    if not token:
        raise OpenAIRequestError(
            f"{env_name} not set -- stored-completions reads for "
            f"'{project_name}' require a project-scoped service-account key "
            "(OPENAI_ADMIN_API_KEY no longer works here, PROD-3572). Create "
            "one at https://platform.openai.com/api-keys (Create new secret "
            f"key -> Owned by: Service account -> Project: {project_name}) "
            f"and add it to ~/.claude/settings.json under env.{env_name}."
        )
    return token


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
        label = f"{since} → {until}"
    elif window == "previous-month":
        first_of_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_end = first_of_this
        # Step back one day to land inside previous month, then to day 1
        prev_month_any_day = first_of_this - timedelta(days=1)
        start_dt = prev_month_any_day.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = last_month_end
        label = f"previous month ({start_dt.strftime('%B %Y')})"
    elif window == "last-7d":
        end_dt = now
        start_dt = now - timedelta(days=7)
        label = "last 7 days"
    elif window == "last-30d":
        end_dt = now
        start_dt = now - timedelta(days=30)
        label = "last 30 days"
    else:  # month-to-date (default)
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

def _request(
    path: str,
    params: dict,
    api_key: str,
    project_id: str | None = None,
    raise_on_error: bool = False,
) -> dict:
    """GET with retry on 429 + 5xx.

    `project_id` sets the `OpenAI-Project` header (required by `/chat/completions`
    to scope stored completions to a single project).

    `raise_on_error=True` raises `OpenAIRequestError` instead of calling
    `sys.exit` — used by --by-prompter so per-project failures don't kill the run.
    """
    url = f"{OPENAI_API}{path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    if project_id:
        headers["OpenAI-Project"] = project_id
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
            msg = f"OpenAI {r.status_code} on {path}: {r.text[:300]}"
            if raise_on_error:
                raise OpenAIRequestError(msg)
            sys.exit(msg)
        return r.json()
    msg = f"OpenAI {path}: gave up after retries — last error: {last_err}"
    if raise_on_error:
        raise OpenAIRequestError(msg)
    sys.exit(msg)


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
    """Map project_id → {name, status} (best-effort; includes archived).

    Returns the full list so the caller can decide whether to surface
    zero-cost projects (active ones get zero-filled into the report;
    archived ones stay implicit unless the Costs API returned rows).
    """
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


def fetch_api_key_usage(
    start_unix: int, end_unix: int, project_id: str, api_key: str
) -> dict[str, dict]:
    """Return {api_key_id: {input_tokens, output_tokens, name}} for one project."""
    totals: dict[str, dict] = {}
    page: str | None = None
    while True:
        params = {
            "start_time": start_unix,
            "end_time": end_unix,
            "bucket_width": "1d",
            "group_by": ["api_key_id"],
            "project_ids": [project_id],
            "limit": USAGE_PAGE_SIZE,
        }
        if page:
            params["page"] = page
        data = _request(USAGE_COMPLETIONS_PATH, params, api_key)
        for bucket in data.get("data", []):
            for row in bucket.get("results", []):
                kid = row.get("api_key_id") or "(unattributed)"
                t = totals.setdefault(
                    kid, {"input_tokens": 0, "output_tokens": 0, "name": row.get("api_key_name")}
                )
                t["input_tokens"] += row.get("input_tokens", 0) or 0
                t["output_tokens"] += row.get("output_tokens", 0) or 0
                if row.get("api_key_name") and not t["name"]:
                    t["name"] = row["api_key_name"]
        page = data.get("next_page")
        if not page:
            break
    return totals


# ---------------------------------------------------------------------------
# Per-prompter spend allocation
# ---------------------------------------------------------------------------
# Mirrors the allocation backbone in `eng:analyze-openai-cost-per-user`, but
# groups on `metadata.name` (the prompter name written by the intelligence-
# service, e.g. `coach-prompter`, `memory-prompter`) instead of `metadata.user`.
# OpenAI doesn't expose per-call dollars, so per-project totals from /costs are
# allocated across prompters by token weight within that project.
#
# Token-weight source, per project and per slice of the window:
#   - pre-epoch slice  [start, min(end, LEDGER_EPOCH)) — OpenAI stored chat
#     completions walk (only source; the ledger has no rows before the epoch).
#   - post-epoch slice [max(start, LEDGER_EPOCH), end) — the IS per-call LLM
#     ledger (BigQuery llm.llm_call, `prompterName` == `metadata.name`) is PRIMARY:
#     it covers both Chat Completions and Responses APIs. If the ledger request
#     fails, fall back to the stored-completions walk (chat-API-only) with a
#     stderr warning.
# Slices are merged and defensively deduped on the OpenAI object id
# (ledger `openaiId` == stored completion `id`).


LEDGER_PROJECT_KEYS = frozenset({
    "conversation", "feed", "guardrail", "insights", "intent", "journal",
    "memory", "notification", "onboarding", "profile", "suggestions", "summary",
})


def _unix_to_iso(ts: int) -> str:
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _ledger_project_key(project_name: str) -> str | None:
    """Map an OpenAI dashboard project name to the IS ledger project key:
    'Memory [Production]' -> 'memory'. Returns None for non-Production
    projects — the ledger only records prod IS traffic."""
    m = re.match(r"^(.+?)\s*\[(.+?)\]\s*$", project_name or "")
    if not m or m.group(2) != "Production":
        return None
    base = m.group(1).strip()
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


def _record_from_completion(item: dict, source_slice: str) -> dict:
    """Normalize a stored chat completion into the internal call-record shape."""
    metadata = item.get("metadata") or {}
    usage = item.get("usage") or {}
    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    cached_tokens = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                     or (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    return {
        "id": item.get("id"),
        "name": metadata.get("name"),
        "tokens": (input_tokens or 0) + (output_tokens or 0),
        # Kept separate for the cost-shaped weighting (ENG-1046). `output_tokens`
        # is completion_tokens, which ALREADY includes reasoning — never add a
        # reasoning field on top of it.
        "input": input_tokens or 0,
        "output": output_tokens or 0,
        "cached": cached_tokens or 0,
        "user": metadata.get("user"),
        "conversation_id": metadata.get("conversationId"),
        # Top-level on the completion object, not inside usage/metadata.
        "service_tier": item.get("service_tier"),
        "source_slice": source_slice,
    }


def _record_from_ledger_row(row: dict, source_slice: str) -> dict:
    """Normalize an IS ledger row into the same call-record shape.

    Ledger columns replace the completion's metadata fields:
    prompterName == metadata.name, userId == metadata.user,
    conversationId == metadata.conversationId; usage.prompt_tokens ->
    inputTokens, usage.completion_tokens -> outputTokens.
    """
    tokens = (row.get("inputTokens") or 0) + (row.get("outputTokens") or 0)
    if tokens <= 0:
        tokens = row.get("totalTokens") or 0
    return {
        "id": row.get("openaiId"),
        "name": row.get("prompterName"),
        "tokens": tokens,
        # See _record_from_completion: outputTokens already includes reasoning.
        "input": row.get("inputTokens") or 0,
        "output": row.get("outputTokens") or 0,
        "cached": row.get("cachedTokens") or 0,
        "user": row.get("userId"),
        "conversation_id": row.get("conversationId"),
        # Null on rows written before the ENGBACK-1315 deploy — see service_tier_factor.
        "service_tier": row.get("serviceTier"),
        "source_slice": source_slice,
    }


def gather_project_call_records(
    project_id: str,
    project_name: str,
    start_unix: int,
    end_unix: int,
    sources_used: set[str],
) -> list[dict]:
    """Collect normalized call records for one project over [start, end),
    ledger-first for the post-epoch slice, stored completions elsewhere.

    Raises OpenAIRequestError (from the stored-completions path) so the
    caller can fold the whole project's $ into the untagged bucket.
    """
    records: list[dict] = []
    pre_end = min(end_unix, LEDGER_EPOCH_UNIX)
    post_start = max(start_unix, LEDGER_EPOCH_UNIX)

    if start_unix < pre_end:
        # A pre-epoch completions failure (missing key / API error) must not
        # abort the whole project — the post-epoch ledger slice below can still
        # attribute its traffic. Degrade to "pre-epoch unattributed" and carry on.
        try:
            completions_key = _resolve_completions_api_key(project_name)
            for item in iter_completions(start_unix, pre_end, project_id, completions_key):
                records.append(_record_from_completion(item, "pre"))
            sources_used.add("completions_pre_epoch")
        except OpenAIRequestError as e:
            print(
                f"⚠️  pre-epoch stored-completions walk failed for {project_name} "
                f"({e}): its pre-epoch tokens go unattributed, but continuing to the "
                "post-epoch ledger slice",
                file=sys.stderr,
            )
            sources_used.add("completions_pre_epoch_failed")

    if post_start < end_unix:
        ledger_key = _ledger_project_key(project_name)
        rows: list[dict] | None = None
        if ledger_key is None or ledger_key not in LEDGER_PROJECT_KEYS:
            print(
                f"note: no ledger coverage for '{project_name}': "
                "using stored chat completions for its post-epoch slice",
                file=sys.stderr,
            )
            sources_used.add("completions_non_ledger")
        else:
            try:
                rows = iter_llm_calls(
                    post_start, end_unix, project_key=ledger_key, provider="openai"
                )
                sources_used.add("ledger")
            except LedgerUnavailable as e:
                _fail_closed_on_ledger_outage(e)
                print(
                    f"⚠️  ledger unavailable ({e}): falling back to stored chat "
                    f"completions for {_unix_to_iso(post_start)} → {_unix_to_iso(end_unix)} "
                    f"({project_name}) — the fallback is chat-API-only and cannot "
                    "see Responses-API traffic",
                    file=sys.stderr,
                )
                sources_used.add("completions_fallback")
        if rows is not None:
            records.extend(_record_from_ledger_row(row, "post") for row in rows)
        else:
            # Same principle as the pre-epoch slice: a post-epoch completions
            # fallback failure must not discard the pre-epoch rows already
            # collected (the caller would otherwise fold the whole project into
            # untagged). Degrade to "post-epoch unattributed" and keep them.
            try:
                completions_key = _resolve_completions_api_key(project_name)
                for item in iter_completions(post_start, end_unix, project_id, completions_key):
                    records.append(_record_from_completion(item, "post"))
            except OpenAIRequestError as e:
                print(
                    f"⚠️  post-epoch stored-completions fallback failed for "
                    f"{project_name} ({e}): its post-epoch tokens go unattributed, "
                    "keeping the pre-epoch attribution already collected",
                    file=sys.stderr,
                )
                sources_used.add("completions_post_epoch_failed")

    # Defensive dedupe on the OpenAI object id (ledger openaiId == stored
    # completion id) in case of boundary overlap between slices.
    seen: set[str] = set()
    deduped: list[dict] = []
    for rec in records:
        rid = rec.get("id")
        if rid:
            if rid in seen:
                continue
            seen.add(rid)
        deduped.append(rec)
    return deduped


def _attribution_source_note(
    start_unix: int, end_unix: int, sources_used: set[str]
) -> str:
    """One-line annotation of where the token-weight attribution came from."""
    if end_unix <= LEDGER_EPOCH_UNIX:
        return (
            "_Attribution source: stored chat completions "
            f"(window entirely before ledger epoch {LEDGER_EPOCH_LABEL})._"
        )
    post_bits: list[str] = []
    if "ledger" in sources_used or not sources_used & {
        "completions_fallback", "completions_non_ledger",
    }:
        post_bits.append("LLM ledger (BigQuery llm.llm_call)")
    if "completions_fallback" in sources_used:
        post_bits.append(
            "stored chat completions fallback (chat-API-only — cannot see "
            "Responses-API traffic)"
        )
    if "completions_non_ledger" in sources_used:
        post_bits.append("stored chat completions (non-ledger projects)")
    post = " + ".join(post_bits)
    if start_unix >= LEDGER_EPOCH_UNIX:
        return f"_Attribution source: {post}._"
    return (
        f"_Attribution source: mixed (window straddles ledger epoch "
        f"{LEDGER_EPOCH_LABEL}) — pre-epoch slice via stored chat completions, "
        f"post-epoch slice via {post}._"
    )


def iter_completions(
    start_unix: int, end_unix: int, project_id: str, api_key: str
) -> list[dict]:
    """List stored chat completions for one project within [start, end).

    Endpoint returns newest-first with no native time filter — walk pages and
    stop once we drop below start_unix.
    """
    out: list[dict] = []
    after: str | None = None

    while True:
        params: dict = {"limit": COMPLETIONS_PAGE_SIZE}
        if after:
            params["after"] = after
        data = _request(
            CHAT_COMPLETIONS_PATH, params, api_key,
            project_id=project_id, raise_on_error=True,
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


# Relative cost weights for splitting a project's dollars across its prompters
# (ENG-1046). SHAPE, not rates: OpenAI charges far more per output token than per
# input token and discounts cached input heavily, so a flat total-token split
# under-attributes output-heavy prompters — the same blind spot that lets a
# ~10x-output renderer hide inside an "ambient" bucket (PROD-4163). Actual per-model rates live in `Tools/OpenAI` -> "Per-model rates"
# and are deliberately not duplicated here.
W_INPUT_DEFAULT = 1.0
W_CACHED_DEFAULT = 0.1
W_OUTPUT_DEFAULT = 8.0

# Flag a prompter whose average output per call is this many times the median
# across the ranked set. Heuristic: it annotates, never filters.
OUTPUT_OUTLIER_MULTIPLE = 3.0


def _cost_weight(rec: dict, weights: dict) -> float:
    """Cost-shaped token weight for one call record, scaled by its service tier."""
    tier = service_tier_factor(rec.get("service_tier"))
    out = rec.get("output") or 0
    inp = rec.get("input") or 0
    cached = min(rec.get("cached") or 0, inp)
    if not (inp or out):
        # A record carrying only the flat total (no input/output split) — use it
        # so the call still carries its share rather than vanishing.
        return float(rec.get("tokens") or 0) * tier
    return ((inp - cached) * weights["input"]
            + cached * weights["cached"]
            + out * weights["output"]) * tier


def aggregate_by_prompter(
    per_project_dollars: dict[str, float],
    projects: dict[str, dict],
    start_unix: int,
    end_unix: int,
    weights: dict | None = None,
) -> tuple[dict[str, dict], float, str]:
    """Allocate each project's $ across prompter names by COST-SHAPED token weight.

    Returns (per_prompter, untagged_amount, source_note):

      per_prompter[name] = {
        "amount":        float,                # total $ across all projects
        "tokens":        int,                  # total token weight that produced it
        "calls":         int,                  # completion count
        "users":         int,                  # distinct metadata.user values
        "conversations": int,                  # distinct metadata.conversationId values (0 for non-conversational prompters)
        "projects":      {project_name: $},    # per-project $ breakdown for this prompter
      }
      untagged_amount = $ that landed on the "(untagged)" sentinel
        (no `metadata.name`, project had no completions, or the API failed for that project)
      source_note = one-line annotation of where the attribution data came from
        (LLM ledger / stored chat completions / mixed across the ledger epoch)
    """
    if weights is None:
        weights = {"input": W_INPUT_DEFAULT, "cached": W_CACHED_DEFAULT,
                   "output": W_OUTPUT_DEFAULT}
    per_prompter: dict[str, dict] = {}
    # Track distinct user/conversation IDs lazily — flattened to counts at the end
    # so we don't carry sets around in the rendered/serialised output.
    users_per_name: dict[str, set[str]] = defaultdict(set)
    conversations_per_name: dict[str, set[str]] = defaultdict(set)
    untagged_amount = 0.0
    sources_used: set[str] = set()

    for pid, dollars in per_project_dollars.items():
        if dollars <= 0 or pid == "(unattributed)":
            continue
        pname = (projects.get(pid) or {}).get("name", pid)
        project_sources: set[str] = set()

        try:
            records = gather_project_call_records(
                pid, pname, start_unix, end_unix, project_sources,
            )
            sources_used.update(project_sources)
        except OpenAIRequestError as e:
            print(
                f"⚠️  Skipping completions for {pname} ({pid}): {e}",
                file=sys.stderr,
            )
            untagged_amount += dollars
            continue

        if not records:
            untagged_amount += dollars
            continue

        if (
            start_unix < LEDGER_EPOCH_UNIX < end_unix
            and project_sources & {
                "completions_pre_epoch_failed",
                "completions_post_epoch_failed",
            }
        ):
            # The Costs API only returns daily buckets, so it cannot safely split
            # one UTC day at the 03:25Z ledger epoch. If either side of an
            # epoch-straddling project fails, do not allocate the full project's
            # dollars across the surviving side's token weights.
            untagged_amount += dollars
            continue

        tokens_per_name: dict[str, int] = defaultdict(int)
        calls_per_name: dict[str, int] = defaultdict(int)
        # Weighted mass drives the dollar split; raw tokens stay for the Tokens
        # column, and output tokens feed the avg-out/call outlier signal.
        weight_per_name: dict[str, float] = defaultdict(float)
        output_per_name: dict[str, int] = defaultdict(int)
        for rec in records:
            total_tokens = rec["tokens"] or 0
            if total_tokens <= 0:
                continue
            name = rec["name"] or "(untagged)"
            tokens_per_name[name] += total_tokens
            calls_per_name[name] += 1
            weight_per_name[name] += _cost_weight(rec, weights)
            output_per_name[name] += rec.get("output") or 0
            uid = rec["user"]
            if uid:
                users_per_name[name].add(uid)
            # Non-conversational prompters (memory, feed, summary, …) don't set
            # conversationId — that's expected. The column will read 0 for them.
            conv_id = rec["conversation_id"]
            if conv_id:
                conversations_per_name[name].add(conv_id)

        total_tokens = sum(tokens_per_name.values())
        total_weight = sum(weight_per_name.values())
        if total_tokens == 0:
            untagged_amount += dollars
            continue
        if total_weight <= 0:
            # Every record fell back to a zero flat total — no basis to split on.
            untagged_amount += dollars
            continue

        for name, tokens in tokens_per_name.items():
            share = dollars * weight_per_name[name] / total_weight
            if name == "(untagged)":
                untagged_amount += share
                continue
            entry = per_prompter.setdefault(
                name,
                {
                    "amount": 0.0,
                    "tokens": 0,
                    "calls": 0,
                    "users": 0,
                    "conversations": 0,
                    "output_tokens": 0,
                    "projects": defaultdict(float),
                },
            )
            entry["amount"] += share
            entry["tokens"] += tokens
            entry["calls"] += calls_per_name[name]
            entry["output_tokens"] += output_per_name[name]
            entry["projects"][pname] += share

    for name, entry in per_prompter.items():
        entry["projects"] = dict(entry["projects"])
        top = max(entry["projects"].items(), key=lambda kv: kv[1], default=("—", 0.0))
        entry["top_project"] = top[0]
        entry["users"] = len(users_per_name.get(name, ()))
        entry["conversations"] = len(conversations_per_name.get(name, ()))
        entry["avg_output_per_call"] = (
            round(entry["output_tokens"] / entry["calls"], 1) if entry["calls"] else 0.0)

    # Output-heavy annotation. Median is taken across the whole ranked set rather
    # than per project, because this report is org-wide — a prompter can span
    # projects, so a per-project median would give one prompter several verdicts.
    avgs = sorted(e["avg_output_per_call"] for e in per_prompter.values() if e["calls"])
    if avgs:
        mid = len(avgs) // 2
        median = avgs[mid] if len(avgs) % 2 else (avgs[mid - 1] + avgs[mid]) / 2
        for entry in per_prompter.values():
            entry["output_multiple_of_median"] = (
                round(entry["avg_output_per_call"] / median, 2) if median else None)
            entry["output_outlier"] = bool(
                median and entry["avg_output_per_call"] >= median * OUTPUT_OUTLIER_MULTIPLE)

    source_note = _attribution_source_note(start_unix, end_unix, sources_used)
    return per_prompter, untagged_amount, source_note


def render_prompter_table(
    per_prompter: dict[str, dict],
    total_org_dollars: float,
    untagged_amount: float,
    source_note: str,
) -> str:
    lines: list[str] = []
    lines.append("## Spend by prompter (`metadata.name`)")
    lines.append("")
    tagged_total = sum(e["amount"] for e in per_prompter.values())
    lines.append(
        f"**Tagged spend:** ${tagged_total:,.2f} across "
        f"{len(per_prompter):,} distinct prompters"
    )
    untagged_share = (
        untagged_amount / total_org_dollars * 100 if total_org_dollars else 0
    )
    lines.append(
        f"**Untagged spend:** ${untagged_amount:,.2f} "
        f"({untagged_share:.1f}% of org total — no `metadata.name` on the call)"
    )
    lines.append("")
    lines.append(source_note)
    lines.append("")

    if not per_prompter:
        lines.append("_No prompter-tagged spend in this window._")
        return "\n".join(lines)

    lines.append(
        "| Rank | prompterName | Spend | Share | Tokens | Out/call | Calls | Users | Conversations | Top project |"
    )
    lines.append(
        "|-----:|--------------|------:|------:|-------:|---------:|------:|------:|--------------:|-------------|"
    )
    ranked = sorted(per_prompter.items(), key=lambda kv: kv[1]["amount"], reverse=True)
    for rank, (name, e) in enumerate(ranked, 1):
        share = (e["amount"] / total_org_dollars * 100) if total_org_dollars else 0
        # Conversations = 0 is meaningful (non-conversational prompter), render as "—" to avoid implying missing data.
        conv_cell = f"{e['conversations']:,}" if e.get("conversations") else "—"
        flag = " ⚠️" if e.get("output_outlier") else ""
        lines.append(
            f"| {rank} | `{name}`{flag} | ${e['amount']:,.2f} | {share:.1f}% | "
            f"{e['tokens']:,} | {e.get('avg_output_per_call', 0):,.0f} | "
            f"{e['calls']:,} | {e['users']:,} | {conv_cell} | "
            f"{e.get('top_project', '—')} |"
        )
    outliers = [(n, e) for n, e in ranked if e.get("output_outlier")]
    if outliers:
        lines.append("")
        lines.append("**⚠️ Output-heavy prompters.** Average output per call is a large "
                     "multiple of the median across this ranking. Output dominates OpenAI "
                     "cost, so this is where a verbose prompt shows up as money:")
        lines.append("")
        for n, e in outliers:
            lines.append(f"- **`{n}`** — {e['avg_output_per_call']:,.0f} out-tokens/call, "
                         f"**{e.get('output_multiple_of_median')}x** the median, "
                         f"${e['amount']:,.2f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-model rate calculation
# ---------------------------------------------------------------------------

def fetch_model_tokens(start_unix: int, end_unix: int, api_key: str) -> dict[str, dict[str, int]]:
    """Return {model: {input, output, cached}} aggregated across the whole org."""
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"input": 0, "output": 0, "cached": 0})
    page: str | None = None
    while True:
        params: dict = {
            "start_time": start_unix,
            "end_time": end_unix,
            "bucket_width": "1d",
            "group_by": ["model"],
            "limit": USAGE_PAGE_SIZE,
        }
        if page:
            params["page"] = page
        data = _request(USAGE_COMPLETIONS_PATH, params, api_key)
        for bucket in data.get("data", []):
            for row in bucket.get("results", []):
                m = row.get("model") or "(none)"
                totals[m]["input"] += row.get("input_tokens", 0) or 0
                totals[m]["output"] += row.get("output_tokens", 0) or 0
                totals[m]["cached"] += row.get("input_cached_tokens", 0) or 0
        page = data.get("next_page")
        if not page:
            break
    return dict(totals)


def fetch_line_item_spend(start_unix: int, end_unix: int, api_key: str) -> dict[tuple[str, str], float]:
    """Return {(model, kind): $} where kind in {input, cached input, output, cache writes, other}.

    `cache writes` is a fourth billing dimension OpenAI began emitting with the
    gpt-5.6 family; before it was matched here its dollars fell into `other` and
    silently dropped out of every per-model rate.
    """
    spend: dict[tuple[str, str], float] = defaultdict(float)
    page: str | None = None
    while True:
        params: dict = {
            "start_time": start_unix,
            "end_time": end_unix,
            "bucket_width": "1d",
            "group_by": ["line_item"],
            "limit": COSTS_PAGE_SIZE,
        }
        if page:
            params["page"] = page
        data = _request(COSTS_PATH, params, api_key)
        for bucket in data.get("data", []):
            for row in bucket.get("results", []):
                li = row.get("line_item") or ""
                amt = row.get("amount") or {}
                v = float(amt.get("value", 0) or 0)
                m = re.match(r"^(.+),\s*(input|cached input|output|cache writes)$", li)
                if m:
                    spend[(m.group(1), m.group(2))] += v
                else:
                    spend[(li, "other")] += v
        page = data.get("next_page")
        if not page:
            break
    return dict(spend)


def compute_model_rates(
    tokens: dict[str, dict[str, int]],
    spend: dict[tuple[str, str], float],
    min_spend: float = 1.0,
) -> list[dict]:
    """Back-calculate per-1M-token rates by dividing $ by tokens for each model.

    Returns one dict per model with input/cached/output rates plus the underlying
    counts that produced them. Drops:
      - models with zero token volume on completions (they bill on non-token units —
        e.g. image generation is priced per image, not per token, even though it
        shows up in the costs API)
      - models with `spend_total < min_spend` (rate is too noisy to publish)
    """
    rows: list[dict] = []
    all_models = set(tokens.keys()) | {m for (m, _) in spend.keys() if m and "(none)" not in m}
    for m in sorted(all_models):
        t = tokens.get(m, {"input": 0, "output": 0, "cached": 0})
        sd_in = spend.get((m, "input"), 0.0)
        sd_cached = spend.get((m, "cached input"), 0.0)
        sd_out = spend.get((m, "output"), 0.0)
        sd_cache_writes = spend.get((m, "cache writes"), 0.0)
        total = sd_in + sd_cached + sd_out + sd_cache_writes

        # No tokens at all → not a token-priced model (e.g. image generation
        # billed per image, even though /costs labels its line_items "input"/"output").
        if t["input"] == 0 and t["output"] == 0:
            continue
        if total < min_spend:
            continue

        in_non_cached = max(0, t["input"] - t["cached"])
        rows.append({
            "model": m,
            "input_per_1m": (sd_in / in_non_cached * 1_000_000) if in_non_cached else None,
            "cached_input_per_1m": (sd_cached / t["cached"] * 1_000_000) if t["cached"] else None,
            "output_per_1m": (sd_out / t["output"] * 1_000_000) if t["output"] else None,
            "tokens_input": t["input"],
            "tokens_input_cached": t["cached"],
            "tokens_output": t["output"],
            "spend_input": sd_in,
            "spend_cached": sd_cached,
            "spend_output": sd_out,
            # No per-1M rate: /organization/usage/completions exposes no cache-write
            # token count to divide by, so only the dollars are reportable.
            "spend_cache_writes": sd_cache_writes,
            "spend_total": total,
        })
    rows.sort(key=lambda r: -r["spend_total"])
    return rows


def detect_new_models(rate_rows: list[dict], known: list[str]) -> list[str]:
    """Return models present in the current rate set but missing from `known`."""
    known_set = {k.strip() for k in known if k and k.strip()}
    return [r["model"] for r in rate_rows if r["model"] not in known_set]


def render_rate_table(rate_rows: list[dict]) -> str:
    """Markdown table of per-model rates. Format matches the Tools/OpenAI section."""
    if not rate_rows:
        return "_No per-token rates available in this window._"
    lines = [
        "| Model | Input $/1M | Cached input $/1M | Output $/1M | Spend |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in rate_rows:
        lines.append(
            f"| `{r['model']}` "
            f"| {_fmt_rate(r['input_per_1m'])} "
            f"| {_fmt_rate(r['cached_input_per_1m'])} "
            f"| {_fmt_rate(r['output_per_1m'])} "
            f"| ${r['spend_total']:,.2f} |"
        )
    return "\n".join(lines)


def _fmt_rate(v: float | None) -> str:
    return f"${v:.4f}" if v is not None else "—"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_by_project(buckets: list[dict]) -> tuple[dict[str, float], str]:
    """Return ({project_id: total_usd}, currency_label)."""
    totals: dict[str, float] = {}
    currency = "usd"
    for bucket in buckets:
        for row in bucket.get("results", []):
            amount = row.get("amount") or {}
            value = float(amount.get("value", 0) or 0)
            cur = amount.get("currency") or "usd"
            if cur != currency and value > 0:
                currency = cur  # surface any non-USD currency in the label
            pid = row.get("project_id") or "(unattributed)"
            totals[pid] = totals.get(pid, 0.0) + value
    return totals, currency


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(
    label: str,
    by_project: dict[str, float],
    projects: dict[str, dict],
    api_key_usage: dict[str, dict[str, dict]] | None,
    currency: str,
) -> str:
    total = sum(by_project.values())
    lines: list[str] = []
    lines.append(f"# OpenAI Spend — {label}")
    lines.append("")
    if total == 0 and not by_project:
        lines.append("_No spend recorded in this window._")
        return "\n".join(lines)

    nonzero = sum(1 for v in by_project.values() if v > 0)
    zero = len(by_project) - nonzero
    summary = f"**Total:** {_fmt_money(total, currency)} across {nonzero} active project(s)"
    if zero:
        summary += f" (+ {zero} zero-cost in this window)"
    lines.append(summary)
    lines.append("")
    lines.append("| Project | Spend | Share |")
    lines.append("|---|---:|---:|")
    rows = sorted(by_project.items(), key=lambda kv: kv[1], reverse=True)
    for pid, amount in rows:
        name = (projects.get(pid) or {}).get("name", pid)
        pct = (amount / total * 100) if total else 0
        lines.append(f"| {name} | {_fmt_money(amount, currency)} | {pct:.1f}% |")

    if api_key_usage:
        lines.append("")
        lines.append("## Per-API-key usage")
        lines.append("")
        lines.append("_Token counts only — OpenAI does not expose per-key dollar attribution._")
        for pid, _ in rows:
            keys = api_key_usage.get(pid) or {}
            if not keys:
                continue
            pname = (projects.get(pid) or {}).get("name", pid)
            lines.append("")
            lines.append(f"### {pname}")
            lines.append("")
            lines.append("| API key | Input tokens | Output tokens |")
            lines.append("|---|---:|---:|")
            sorted_keys = sorted(
                keys.items(),
                key=lambda kv: (kv[1]["input_tokens"] + kv[1]["output_tokens"]),
                reverse=True,
            )
            for kid, t in sorted_keys:
                kname = t.get("name") or kid
                lines.append(f"| {kname} | {t['input_tokens']:,} | {t['output_tokens']:,} |")
    return "\n".join(lines)


def _fmt_money(amount: float, currency: str) -> str:
    symbol = "$" if currency.lower() == "usd" else f"{currency.upper()} "
    return f"{symbol}{amount:,.2f}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # Ensure stdout can emit the Unicode arrows used in the rendered Markdown
    # report on Windows consoles that default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="OpenAI spend report for the Aura org")
    ap.add_argument(
        "--window",
        choices=["month-to-date", "previous-month", "last-7d", "last-30d"],
        default="month-to-date",
    )
    ap.add_argument("--since", help="UTC start date (YYYY-MM-DD). Use with --until.")
    ap.add_argument("--until", help="UTC end date, exclusive (YYYY-MM-DD).")
    ap.add_argument(
        "--no-api-keys",
        action="store_true",
        help="Skip the per-API-key token breakdown (faster).",
    )
    ap.add_argument(
        "--no-zero",
        action="store_true",
        help="Suppress projects with $0 spend in this window (tighter table).",
    )
    ap.add_argument(
        "--by-model",
        action="store_true",
        help="Also compute & print empirical per-model token rates "
             "(back-calculated from /costs ÷ /usage/completions).",
    )
    ap.add_argument(
        "--by-prompter",
        action="store_true",
        help="Also rank spend by `metadata.name` (prompter name) — cost-shaped "
             "token weighting allocates each project's $ across the prompters "
             "that ran in it, and flags output-heavy ones. "
             "Post-epoch slices read the IS LLM-call ledger (BigQuery llm.llm_call) "
             "first, with automatic fallback to a /chat/completions walk per "
             "non-zero project; pre-epoch slices always use the walk.",
    )
    # ENG-1046: relative weights for the --by-prompter dollar split. Shape, not
    # rates — see W_*_DEFAULT above. Override to sensitivity-test an allocation;
    # all three equal is exactly a flat total-token split.
    ap.add_argument("--w-input", type=float, default=W_INPUT_DEFAULT,
                    help="--by-prompter weight for uncached input tokens (default 1.0)")
    ap.add_argument("--w-cached", type=float, default=W_CACHED_DEFAULT,
                    help="--by-prompter weight for cached input tokens (default 0.1)")
    ap.add_argument("--w-output", type=float, default=W_OUTPUT_DEFAULT,
                    help="--by-prompter weight for output tokens (default 8.0)")
    ap.add_argument(
        "--known-models",
        default="",
        help="Comma-separated list of models already documented in AI Knowledge "
             "Tools/OpenAI. Used with --by-model to flag models that need "
             "refreshing on the page. Pass after retrieving the current page.",
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
    by_project, currency = aggregate_by_project(cost_buckets)
    projects = fetch_projects(api_key)

    # Zero-fill: surface every *active* project that the Costs API didn't return
    # a row for (e.g. the org's "Default project"). Archived projects only appear
    # if the Costs API surfaced them — they don't get zero-filled.
    for pid, meta in projects.items():
        if meta.get("status") == "active" and pid not in by_project:
            by_project[pid] = 0.0

    if args.no_zero:
        by_project = {pid: amt for pid, amt in by_project.items() if amt > 0}

    api_key_usage: dict[str, dict[str, dict]] | None = None
    if not args.no_api_keys and by_project:
        api_key_usage = {}
        for pid, amount in by_project.items():
            if pid == "(unattributed)" or amount == 0:
                continue  # don't burn a usage call on zero-cost projects
            api_key_usage[pid] = fetch_api_key_usage(start_unix, end_unix, pid, api_key)

    report = render(label, by_project, projects, api_key_usage, currency)
    print(report)

    per_prompter: dict[str, dict] = {}
    prompter_untagged = 0.0
    prompter_source_note = ""
    if args.by_prompter:
        per_prompter, prompter_untagged, prompter_source_note = aggregate_by_prompter(
            by_project, projects, start_unix, end_unix,
            weights={"input": args.w_input, "cached": args.w_cached,
                     "output": args.w_output},
        )
        total_org_dollars = sum(
            v for k, v in by_project.items() if k != "(unattributed)"
        )
        print()
        print(render_prompter_table(
            per_prompter, total_org_dollars, prompter_untagged, prompter_source_note,
        ))

    rate_rows: list[dict] = []
    new_models: list[str] = []
    if args.by_model:
        tokens = fetch_model_tokens(start_unix, end_unix, api_key)
        spend = fetch_line_item_spend(start_unix, end_unix, api_key)
        rate_rows = compute_model_rates(tokens, spend)
        known = [m for m in (args.known_models or "").split(",") if m.strip()]
        new_models = detect_new_models(rate_rows, known) if known else []

        print()
        print("## Per-model rates (empirical)")
        print()
        print(
            "_Back-calculated from `/organization/costs?group_by=line_item` ÷ "
            "`/organization/usage/completions?group_by=model` over this window. "
            "Reflects Aura's actual rates including any negotiated discount._"
        )
        print()
        print(render_rate_table(rate_rows))
        if known:
            print()
            if new_models:
                print(
                    f"**Refresh needed.** New model(s) detected since last "
                    f"`Tools/OpenAI` update: `{', '.join(new_models)}`. "
                    f"Update the page with the table above."
                )
            else:
                print("All models in this window are already documented in `Tools/OpenAI`.")

    if args.json_out:
        payload = {
            "window": {
                "label": label,
                "start_unix": start_unix,
                "end_unix": end_unix,
                "start_iso": datetime.fromtimestamp(start_unix, tz=timezone.utc).isoformat(),
                "end_iso": datetime.fromtimestamp(end_unix, tz=timezone.utc).isoformat(),
            },
            "currency": currency,
            "total": sum(by_project.values()),
            "by_project": [
                {
                    "project_id": pid,
                    "project_name": (projects.get(pid) or {}).get("name", pid),
                    "status": (projects.get(pid) or {}).get("status", "unknown"),
                    "amount": amount,
                }
                for pid, amount in sorted(by_project.items(), key=lambda kv: kv[1], reverse=True)
            ],
            "api_key_usage": api_key_usage,
            "by_model_rates": rate_rows if args.by_model else None,
            "new_models": new_models if args.by_model else None,
            "by_prompter": (
                {
                    "tagged_amount": sum(e["amount"] for e in per_prompter.values()),
                    "untagged_amount": prompter_untagged,
                    "attribution_source": prompter_source_note,
                    "prompters": [
                        {
                            "name": name,
                            "amount": e["amount"],
                            "tokens": e["tokens"],
                            "calls": e["calls"],
                            "users": e["users"],
                            "conversations": e["conversations"],
                            "top_project": e.get("top_project"),
                            "projects": e["projects"],
                        }
                        for name, e in sorted(
                            per_prompter.items(),
                            key=lambda kv: kv[1]["amount"],
                            reverse=True,
                        )
                    ],
                }
                if args.by_prompter
                else None
            ),
        }
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)


if __name__ == "__main__":
    main()
