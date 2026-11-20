#!/usr/bin/env python3
"""
Report OpenAI inference cost for ONE specific user.

Point-lookup counterpart to the rank-N `eng:analyze-openai-cost-per-user`
skill. When you only need one user's cost, the ranking pipeline (which
iterates every stored completion across all projects) is wasteful — this
skill finishes in ~9 min vs ~30 min by filtering at the source
(measured 8.8 min, 30-day window, 2026-08-16 — scales with the
`[Production]` project count; see references/algorithm.md).

Algorithm:
  1. Resolve window.
  2. For each Aurie production project (auto-discovered by `[Production]`
     name suffix on `/v1/organization/projects`):
       a. Pull project's total $ via Costs API (`group_by=project_id`).
       b. Pull project's total tokens via Usage API
          (`/v1/organization/usage/completions`, grouped by service tier). It
          covers Chat Completions and Responses API calls, so the denominator
          never walks the IntelligenceService ledger.
       c. Pull user's tokens, split at the ledger epoch (2026-07-23T03:25Z):
          - Pre-epoch slice: paginate `/v1/chat/completions` with
            `metadata[user]=<UID>` (OpenAI-Project: <project_id>) — the only
            source for pre-epoch data.
          - Post-epoch slice: read the IntelligenceService per-call LLM
            ledger (BigQuery llm.llm_call) as PRIMARY: ONE userId-filtered walk for
            the whole window (BigQuery, clustered by userId), split by project
            locally and cached for the run.
            If the ledger is unavailable, warn and fall back to the
            stored-completions walk for that slice (chat-API-only; cannot
            see Responses-API traffic).
  3. For windows that straddle or precede the 2026-05-12 `metadata.user`
     rollout, extrapolate the pre-rollout portion per project: take the
     user's share over the post-rollout sub-window and scale by the
     project's `full_window_$ / post_rollout_window_$` ratio. This
     assumes the user's activity proportions held across the rollout
     boundary — flag the assumption in the report's Caveats. The same
     extrapolation applies uniformly to every project including
     Conversation (no special-case Braintrust fallback as of PROD-2466 /
     2026-06-05; the OpenAI side is now the sole source of truth — see
     AI Knowledge `Tools/OpenAI`).
  4. Compute per-project user cost = `(user_tokens / project_tokens) ×
     project_$`. Sum across projects.

Env (PROD-3572, ENGBACK-1237):
  OPENAI_ADMIN_API_KEY  Admin API key (sk-admin-...). Required — used ONLY for
                        the `/v1/organization/*` calls (projects list, per-project
                        cost + token totals via the Costs/Usage API).
  GCP_KEY               Service account for the IS LLM-call ledger
                        (BigQuery llm.llm_call). Used for post-epoch per-user
                        attribution. When unset or the ledger errors, the script
                        warns and falls back to the stored-completions walk (no
                        crash).
  OPENAI_<PROJECT>_API_KEY
                        Project-scoped service-account key, one per production
                        project, derived from the project display name
                        ("Memory [Production]" -> OPENAI_MEMORY_API_KEY,
                        "Conversation [Production]" -> OPENAI_CONVERSATION_API_KEY).
                        Used for the per-user `/v1/chat/completions` stored-
                        completions walk. OpenAI now enforces `model.request`
                        scope on that endpoint, which Admin keys cannot hold, so
                        the admin key no longer works there. When a project's key
                        is unset, that project's per-user walk is skipped (its
                        user cost counts as 0) rather than hard-failing.

Usage:
  python3 report.py <UID>
  python3 report.py <UID> --window previous-month
  python3 report.py <UID> --window last-7d
  python3 report.py <UID> --from 2026-04-11 --to 2026-05-27
  python3 report.py <UID> --no-by-project          # total only
  python3 report.py <UID> --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import pathlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# Allow `from resolve_window import resolve` regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve_window import resolve  # noqa: E402


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
ROLLOUT_TS = int(datetime(2026, 5, 12, tzinfo=timezone.utc).timestamp())
COSTS_PAGE_LIMIT = 31  # OpenAI cap for bucket_width=1d
COMPLETIONS_PAGE_LIMIT = 100


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


# ---------------------------------------------------------------------------
# Tier weighting is per-source, because only some sources carry a tier
# ---------------------------------------------------------------------------
# Dollars are split by token weight, and OpenAI charges different rates per
# service tier, so the weight has to account for the tier. The complication here
# is that the numerator and denominator are assembled from several sources and
# only some of them record one:
#
#   ledger rows                     -> `serviceTier` per call        -> weightable
#   stored completions              -> `service_tier` per call       -> weightable
#   /organization/usage/completions -> `service_tier` per group      -> weightable when grouped
#
# The denominator is always the usage endpoint. Its post-epoch slice on IS projects is
# grouped by tier and weighted, matching the ledger numerator; pre-epoch and non-IS
# slices are taken at face value, matching the stored-completions numerator.
#
# The rule both accumulators below follow: **weight the ledger contribution, and
# count every non-ledger contribution at face value on BOTH sides.** That keeps each
# slice on one basis across numerator and denominator, which is what the ratio
# requires. Weighting a numerator whose denominator cannot be weighted would
# under-attribute flex users by the size of the discount.
#
# Where a project is entirely usage-API-derived the weights equal the raw counts, so
# the allocation there is a plain token split.
LEDGER_EPOCH_UNIX = 1784777100  # 2026-07-23T03:25:00Z — ledger live (IS 0.285.0); no rows before
LEDGER_EPOCH_LABEL = "2026-07-23T03:25Z"
# IS feature project keys served by the ledger. OpenAI `[Production]` projects
# outside this set (Content, Search, Support, …) are not IS-served, so their
# attribution stays on the stored-completions walk for the whole window.
LEDGER_PROJECT_KEYS = frozenset({
    "conversation", "feed", "guardrail", "insights", "intent", "journal",
    "memory", "notification", "onboarding", "profile", "suggestions", "summary",
})


class LedgerUnavailable(Exception):
    pass


def _unix_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _slice_label(start_unix: int, end_unix: int) -> str:
    return f"[{_unix_iso(start_unix)} → {_unix_iso(end_unix)})"


def _ledger_project_key(project_name: str) -> str:
    """'Memory [Production]' -> 'memory' (strip env suffix, lowercase first letter)."""
    base = re.sub(r"\[.*?\]", "", project_name).strip()
    return (base[:1].lower() + base[1:]) if base else base


def iter_llm_calls(start_unix: int, end_unix: int, *, user_id: str | None = None,
                   prompter_name: str | None = None, project_key: str | None = None,
                   openai_api: str | None = None) -> list[dict]:
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
    try:
        return llm_ledger.rows(params)
    except llm_ledger.LedgerError as exc:
        raise LedgerUnavailable(str(exc)) from exc


def _project_completions_api_key_env(project_name: str) -> str:
    """PROD-3572: "Memory [Production]" -> "OPENAI_MEMORY_API_KEY"."""
    base = re.sub(r"\[.*?\]", "", project_name).strip()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").upper()
    return f"OPENAI_{base}_API_KEY"


def _resolve_completions_api_key(project_name: str) -> str | None:
    """Resolve the project-scoped key for a stored-completions walk (PROD-3572).
    Returns None + stderr warning when unset so the caller can skip that project
    rather than hard-fail. OPENAI_ADMIN_API_KEY no longer works for
    /v1/chat/completions."""
    env_name = _project_completions_api_key_env(project_name)
    token = os.environ.get(env_name)
    if not token:
        print(
            f"WARNING: {env_name} not set — skipping the per-user completions walk "
            f"for '{project_name}'. OPENAI_ADMIN_API_KEY no longer works for "
            f"/v1/chat/completions (PROD-3572); set {env_name} in "
            f"~/.claude/settings.json.",
            file=sys.stderr,
        )
    return token


def _request(method: str, url: str, key: str, *, params=None, json_body=None,
             project_id: str | None = None, timeout: int = 60) -> dict:
    headers = {"Authorization": f"Bearer {key}"}
    if project_id:
        headers["OpenAI-Project"] = project_id
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(5):
        r = requests.request(method, url, headers=headers, params=params,
                             json=json_body, timeout=timeout)
        if r.status_code == 429 or 500 <= r.status_code < 600:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def fetch_paginated_openai(path: str, key: str, base_params: list[tuple]) -> list[dict]:
    """Paginate a usage/costs endpoint via the `page` cursor."""
    out: list[dict] = []
    cursor: str | None = None
    while True:
        params = list(base_params)
        if cursor:
            params.append(("page", cursor))
        d = _request("GET", f"{OPENAI_API}{path}", key, params=params)
        out.extend(d.get("data", []))
        if not d.get("has_more"):
            break
        cursor = d.get("next_page")
        if not cursor:
            break
    return out


def fetch_production_projects(key: str) -> dict[str, dict]:
    """Auto-discover Aurie production projects (name suffix `[Production]`)."""
    projects: dict[str, dict] = {}
    after: str | None = None
    while True:
        params = {"limit": 100, "include_archived": "false"}
        if after:
            params["after"] = after
        data = _request("GET", f"{OPENAI_API}/organization/projects", key, params=params)
        for p in data.get("data", []):
            name = p.get("name") or p["id"]
            if name.endswith(" [Production]"):
                short = name.removesuffix(" [Production]")
                projects[p["id"]] = {"name": name, "short": short, "status": p.get("status", "unknown")}
        if not data.get("has_more"):
            break
        last = data["data"][-1] if data.get("data") else None
        if not last:
            break
        after = last["id"]
    return projects


def project_cost_usd(project_id: str, start_unix: int, end_unix: int, key: str) -> float:
    """Return Costs API dollars for a project over a window."""
    cost = 0.0
    for b in fetch_paginated_openai(
        "/organization/costs", key,
        [("start_time", start_unix), ("end_time", end_unix), ("bucket_width", "1d"),
         ("project_ids[]", project_id), ("limit", COSTS_PAGE_LIMIT)],
    ):
        for r in b.get("results", []):
            try:
                cost += float(r.get("amount", {}).get("value", 0))
            except (TypeError, ValueError):
                pass
    return cost


_USER_ROWS: dict[tuple, list[dict] | LedgerUnavailable] = {}


def user_ledger_rows(uid: str, start_unix: int, end_unix: int) -> list[dict]:
    """Every ledger row for one user over [start, end), all projects, fetched once per run.

    One userId-filtered walk replaces a walk per project; callers split by `project`.
    A failure is cached too, so twelve projects don't retry the same refused walk.
    """
    key = (uid, start_unix, end_unix)
    if key not in _USER_ROWS:
        try:
            _USER_ROWS[key] = iter_llm_calls(start_unix, end_unix, user_id=uid)
        except LedgerUnavailable as exc:
            _USER_ROWS[key] = exc
    hit = _USER_ROWS[key]
    if isinstance(hit, LedgerUnavailable):
        raise hit
    return hit


def _tier(raw: object) -> str:
    """`/usage/completions` spells tiers `flex-tier`; the ledger says `flex`."""
    tier = str(raw or "").strip().lower()
    return tier[: -len("-tier")] if tier.endswith("-tier") else tier


def project_usage_tokens_weighted(
    project_id: str, start_unix: int, end_unix: int, key: str
) -> tuple[int, int, float]:
    """Usage API (input, output, tier-weighted total) for a project over a window.

    The completions usage endpoint covers Chat Completions AND Responses API calls
    (measured 2026-10-27: within 0.3% of the ledger's input tokens on every IS
    project), and groups by service tier, so the denominator needs no ledger walk.
    """
    inp = out_ = 0
    weight = 0.0
    for b in fetch_paginated_openai(
        "/organization/usage/completions", key,
        [("start_time", start_unix), ("end_time", end_unix), ("bucket_width", "1d"),
         ("project_ids[]", project_id), ("group_by[]", "service_tier"), ("limit", COSTS_PAGE_LIMIT)],
    ):
        for r in b.get("results", []):
            i, o = r.get("input_tokens") or 0, r.get("output_tokens") or 0
            inp += i
            out_ += o
            weight += (i + o) * service_tier_factor(_tier(r.get("service_tier")))
    return inp, out_, weight


def project_chat_usage_tokens(
    project_id: str, start_unix: int, end_unix: int, key: str
) -> tuple[int, int]:
    """Return chat-completions Usage API tokens for a project over a window."""
    inp = out_ = 0
    for b in fetch_paginated_openai(
        "/organization/usage/completions", key,
        [("start_time", start_unix), ("end_time", end_unix), ("bucket_width", "1d"),
         ("project_ids[]", project_id), ("limit", COSTS_PAGE_LIMIT)],
    ):
        for r in b.get("results", []):
            inp += r.get("input_tokens") or 0
            out_ += r.get("output_tokens") or 0
    return inp, out_


def project_totals(project_id: str, start_unix: int, end_unix: int, key: str) -> tuple[float, int, int]:
    """Return chat-only (cost_usd, input_tokens, output_tokens) for a project."""
    cost = project_cost_usd(project_id, start_unix, end_unix, key)
    inp, out_ = project_chat_usage_tokens(project_id, start_unix, end_unix, key)
    return cost, inp, out_


def project_totals_with_ledger_failover(
    project_id: str, project_name: str, start_unix: int, end_unix: int, key: str
) -> tuple[float, int, int, float, set[str], bool]:
    """Return project dollars, token denominator, and its tier-weighted twin.

    Costs API remains authoritative for dollars. Token totals mirror the user
    numerator source split: pre-epoch and non-ledger projects use the chat Usage
    API; ledger-covered post-epoch slices use the LLM ledger so Responses API
    calls are included in both numerator and denominator.

    `weight` is the same total with the ledger contribution scaled by each call's
    service tier and the Usage-API contribution added at face value — the endpoint
    exposes no tier dimension. See the ENG-1107 note at the top of this file.
    """
    cost = project_cost_usd(project_id, start_unix, end_unix, key)
    inp = out_ = 0
    weight = 0.0
    sources: set[str] = set()
    ledger_fell_back = False

    pre_end = min(end_unix, LEDGER_EPOCH_UNIX)
    if start_unix < pre_end:
        pre_in, pre_out = project_chat_usage_tokens(project_id, start_unix, pre_end, key)
        inp += pre_in
        out_ += pre_out
        weight += pre_in + pre_out
        sources.add("completions")

    post_start = max(start_unix, LEDGER_EPOCH_UNIX)
    if post_start < end_unix:
        ledger_key = _ledger_project_key(project_name)
        if ledger_key in LEDGER_PROJECT_KEYS:
            # Tier-weighted from the Usage API: the same tokens the ledger holds, with no
            # IntelligenceService call.
            post_in, post_out, post_weight = project_usage_tokens_weighted(
                project_id, post_start, end_unix, key)
            inp += post_in
            out_ += post_out
            weight += post_weight
            sources.add("ledger")
        else:
            post_in, post_out = project_chat_usage_tokens(project_id, post_start, end_unix, key)
            inp += post_in
            out_ += post_out
            weight += post_in + post_out
            sources.add("completions")

    return cost, inp, out_, weight, sources, ledger_fell_back


def user_tokens_in_project(project_id: str, uid: str, start_unix: int, end_unix: int, key: str,
                           *, exclude_ids: set[str] | None = None) -> tuple[int, int, int]:
    """Return (prompt_tokens, completion_tokens, completion_count) for the user.

    `exclude_ids` skips completions already counted from the IS LLM ledger
    (ledger `openaiId` == stored completion `id`) to dedupe boundary overlap.
    """
    prompt = completion = count = 0
    after: str | None = None
    while True:
        params = {"metadata[user]": uid, "limit": COMPLETIONS_PAGE_LIMIT}
        if after:
            params["after"] = after
        data = _request("GET", f"{OPENAI_API}/chat/completions", key,
                        params=params, project_id=project_id)
        items = data.get("data", [])
        if not items:
            break
        for item in items:
            created = item.get("created", 0) or 0
            if created >= end_unix or created < start_unix:
                continue
            if exclude_ids and item.get("id") in exclude_ids:
                continue
            usage = item.get("usage") or {}
            prompt += usage.get("prompt_tokens") or 0
            completion += usage.get("completion_tokens") or 0
            count += 1
        if not data.get("has_more"):
            break
        last_id = data.get("last_id") or items[-1].get("id")
        if not last_id:
            break
        after = last_id
    return prompt, completion, count


def user_tokens_with_ledger_failover(
    project_id: str,
    project_name: str,
    uid: str,
    start_unix: int,
    end_unix: int,
    *,
    force_post_epoch_completions: bool = False,
) -> tuple[int, int, int, float, set[str], bool]:
    """Per-user token attribution for one project over [start, end).

    The window splits at the ledger epoch:
      - slice_before = [start, min(end, epoch)) — always the stored-completions
        walk; the ledger has no rows before the epoch.
      - slice_after = [max(start, epoch), end) — IS LLM ledger primary (sees
        Chat Completions AND Responses API calls); on ledger failure, warn and
        fall back to the stored-completions walk for that slice.

    Merged results dedupe defensively on the OpenAI object id (ledger
    `openaiId` == stored completion `id`).

    Returns (prompt_tokens, completion_tokens, call_count, weight, sources,
    ledger_fell_back) — sources is a subset of {"ledger", "completions"}.

    `weight` mirrors the denominator's construction: the ledger contribution is
    scaled by each call's service tier, the completions-walk contribution is added
    at face value because its denominator counterpart (the Usage API) carries no
    tier. See the ENG-1107 note at the top of this file.
    """
    prompt = completion = count = 0
    weight = 0.0
    sources: set[str] = set()
    ledger_ids: set[str] = set()
    ledger_fell_back = False

    completions_ranges: list[list[int]] = []
    boundary = min(end_unix, LEDGER_EPOCH_UNIX)
    if start_unix < boundary:
        completions_ranges.append([start_unix, boundary])

    after_start = max(start_unix, LEDGER_EPOCH_UNIX)
    if after_start < end_unix:
        ledger_key = _ledger_project_key(project_name)
        if force_post_epoch_completions:
            completions_ranges.append([after_start, end_unix])
        elif ledger_key in LEDGER_PROJECT_KEYS:
            try:
                rows = [row for row in user_ledger_rows(uid, after_start, end_unix)
                        if row.get("project") == ledger_key]
            except LedgerUnavailable as exc:
                _fail_closed_on_ledger_outage(exc)
                ledger_fell_back = True
                print(
                    f"WARNING: ledger unavailable ({exc}): falling back to stored chat "
                    f"completions for {_slice_label(after_start, end_unix)} in "
                    f"'{project_name}'. The completions fallback is chat-API-only and "
                    "cannot see Responses-API traffic.",
                    file=sys.stderr,
                )
                completions_ranges.append([after_start, end_unix])
            else:
                for row in rows:
                    if row.get("provider") not in (None, "openai"):
                        continue
                    openai_id = row.get("openaiId")
                    if openai_id:
                        if openai_id in ledger_ids:
                            continue
                        ledger_ids.add(openai_id)
                    row_in = row.get("inputTokens") or 0
                    row_out = row.get("outputTokens") or 0
                    prompt += row_in
                    completion += row_out
                    weight += (row_in + row_out) * service_tier_factor(row.get("serviceTier"))
                    count += 1
                sources.add("ledger")
        else:
            # Non-IS project — no ledger coverage; stored completions throughout.
            completions_ranges.append([after_start, end_unix])

    # Merge contiguous ranges so the (unfiltered-by-time) completions walk
    # pages each project's stored completions once, not twice.
    merged: list[list[int]] = []
    for rng in sorted(completions_ranges):
        if merged and rng[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], rng[1])
        else:
            merged.append(rng)

    if merged:
        completions_key = _resolve_completions_api_key(project_name)
        if completions_key:
            for range_start, range_end in merged:
                walk_prompt, walk_completion, walk_count = user_tokens_in_project(
                    project_id, uid, range_start, range_end, completions_key,
                    exclude_ids=ledger_ids)
                prompt += walk_prompt
                completion += walk_completion
                # Face value: the matching denominator slice is the Usage API,
                # which has no tier to weight by.
                weight += walk_prompt + walk_completion
                count += walk_count
            sources.add("completions")

    return prompt, completion, count, weight, sources, ledger_fell_back


def _fmt_money(x: float) -> str:
    if x == 0:
        return "—"
    return f"${x:,.2f}" if abs(x) >= 0.01 else f"${x:.4f}"


def _fmt_pct(numer: float, denom: float) -> str:
    if not denom:
        return "—"
    return f"{numer/denom*100:.3f}%"


def render_markdown(report: dict, show_by_project: bool) -> str:
    lines: list[str] = []
    uid_short = report["user_id"][:8] + "…"
    label = report["window"]["label"]
    days_in = report["window"]["days_in"]
    total = report["totals"]["cost_usd"]
    per_day = total / max(days_in, 1)

    lines.append(f"## OpenAI cost — `{uid_short}`  ({_fmt_money(total)} over {days_in} day{'s' if days_in != 1 else ''}, {_fmt_money(per_day)}/day)")
    lines.append("")
    if show_by_project:
        lines.append("| Project | Project $ | User share | User $ |")
        lines.append("|---|---:|---:|---:|")
        for p in report["per_project"]:
            if p["user_cost"] <= 0 and p["user_tokens"] == 0:
                continue
            note = " ¹" if p.get("extrapolated") else ""
            lines.append(
                f"| {p['name']}{note} | {_fmt_money(p['project_cost'])} | "
                f"{_fmt_pct(p.get('user_weight', p['user_tokens']), p.get('project_weight', p['project_tokens']))} | "
                f"{_fmt_money(p['user_cost'])} |"
            )
        lines.append(f"| **Total** | | | **{_fmt_money(total)}** |")
        lines.append("")

    if report.get("notes"):
        lines.append("**Caveats:**")
        for n in report["notes"]:
            lines.append(f"- {n}")
        lines.append("")
    if show_by_project:
        any_extrap = any(p.get("extrapolated") for p in report["per_project"])
        if any_extrap:
            lines.append("¹ Pre-2026-05-12 user share extrapolated by scaling the post-rollout share by `full_window_$ / post_rollout_window_$`.")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("user_id", help="Firebase UID (28 alphanumeric chars typical)")
    p.add_argument("--window", help="Preset: mtd | prev-month | last-7d | last-30d | wtd | today")
    p.add_argument("--from", dest="since", help="Custom start (YYYY-MM-DD, UTC)")
    p.add_argument("--to", dest="until", help="Custom end (YYYY-MM-DD, UTC, exclusive)")
    p.add_argument("--by-project", dest="by_project", action="store_true", default=True,
                   help="Show per-project breakdown table (default on)")
    p.add_argument("--no-by-project", dest="by_project", action="store_false",
                   help="Suppress per-project breakdown; show total only")
    p.add_argument("--json", dest="json_out", help="Write structured report JSON to this path")
    p.add_argument("--allow-degraded-attribution", action="store_true",
                   help="Accept degraded per-user attribution when the LLM ledger is "
                        "unreachable on a post-2026-07-23 window (default: exit 2). "
                        "Env: AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1")
    args = p.parse_args()
    _ALLOW_DEGRADED[0] = _ALLOW_DEGRADED[0] or bool(
        getattr(args, "allow_degraded_attribution", False))

    key = os.environ.get("OPENAI_ADMIN_API_KEY")
    if not key:
        sys.exit("OPENAI_ADMIN_API_KEY not set — see CLAUDE.md → 'Token inventory' → eng:analyze-openai-costs.")

    win = resolve(args.window, args.since, args.until)
    start_iso = win["from_iso"]
    end_iso = win["to_iso"]
    start_unix = int(datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    end_unix = int(datetime.strptime(end_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())

    notes: list[str] = []
    rollout_in_window = start_unix < ROLLOUT_TS
    if rollout_in_window:
        notes.append(
            "Window touches pre-2026-05-12. OpenAI's `metadata.user` tagging shipped that day, "
            "so the pre-rollout portion lacks per-user attribution. Per-project user share is "
            "extrapolated from the post-rollout sub-window by `full_window_$ / post_rollout_window_$` "
            "(applied uniformly to every project including Conversation). Assumes the user's activity "
            "proportion held across the rollout boundary — solid for steady-state users, weaker for "
            "first-week users whose ramp spans the boundary."
        )

    print(f"# Resolving window: {win['label']} ({win['from']} → {win['to']}, {win['days_in']}d)", file=sys.stderr)
    print(f"# Fetching production projects…", file=sys.stderr)
    projects = fetch_production_projects(key)
    print(f"# Found {len(projects)} `[Production]` projects.", file=sys.stderr)

    per_project: list[dict] = []
    total_cost = 0.0
    all_sources: set[str] = set()
    ledger_fallback_projects: list[str] = []
    for pid, meta in sorted(projects.items(), key=lambda kv: kv[1]["short"]):
        short = meta["short"]
        print(f"# {short}: fetching project totals + user tokens…", file=sys.stderr)
        (
            proj_cost_full,
            proj_in_full,
            proj_out_full,
            proj_weight_full,
            proj_total_sources,
            project_ledger_fell_back,
        ) = project_totals_with_ledger_failover(pid, meta["name"], start_unix, end_unix, key)
        # ENGBACK-1237: post-epoch attribution reads the IS LLM ledger first,
        # with failover to the stored-completions walk (which also remains the
        # sole source for the pre-epoch slice and non-IS projects).
        user_in_post, user_out_post, n_post, user_weight_post, proj_sources, fell_back = \
            user_tokens_with_ledger_failover(
                pid,
                meta["name"],
                args.user_id,
                start_unix,
                end_unix,
                force_post_epoch_completions=project_ledger_fell_back,
            )
        denominator_chat_only = project_ledger_fell_back
        if fell_back and "ledger" in proj_total_sources:
            print(
                f"WARNING: {short}: user-filtered ledger attribution fell back; "
                "using chat-only project token totals so numerator and "
                "denominator stay source-consistent.",
                file=sys.stderr,
            )
            proj_cost_full, proj_in_full, proj_out_full = project_totals(
                pid, start_unix, end_unix, key
            )
            # Usage-API only — no tier dimension, so the weight is the raw total.
            proj_weight_full = float(proj_in_full + proj_out_full)
            denominator_chat_only = True
        all_sources |= proj_sources
        if fell_back or project_ledger_fell_back:
            ledger_fallback_projects.append(short)
        user_tokens_post = user_in_post + user_out_post
        proj_tokens_full = proj_in_full + proj_out_full

        extrapolated = False
        # Raw tokens drive the displayed columns; the weights drive the dollars.
        user_tokens_effective = user_tokens_post
        user_weight_effective = user_weight_post

        # Pre-rollout extrapolation. Applies uniformly to every project including
        # Conversation as of PROD-2466 / 2026-06-05 — the Braintrust fallback that
        # previously special-cased Conversation has been removed; OpenAI is the
        # sole source of truth.
        if rollout_in_window and user_tokens_post > 0:
            if denominator_chat_only:
                proj_cost_post, proj_in_post, proj_out_post = project_totals(
                    pid, ROLLOUT_TS, end_unix, key
                )
                proj_weight_post = float(proj_in_post + proj_out_post)
            else:
                (
                    proj_cost_post,
                    proj_in_post,
                    proj_out_post,
                    proj_weight_post,
                    _,
                    _,
                ) = project_totals_with_ledger_failover(
                    pid, meta["name"], ROLLOUT_TS, end_unix, key
                )
            proj_tokens_post = proj_in_post + proj_out_post
            if proj_cost_post and proj_weight_post:
                # share_post * (full_$ / post_$) = effective share over full window
                factor = proj_cost_full / proj_cost_post
                # Compute effective user_cost directly and back-derive effective user_tokens for the share column
                user_cost_post = (user_weight_post / proj_weight_post) * proj_cost_post
                user_cost_full = user_cost_post * factor
                # Set effective tokens/weight so the per-project row math reads consistently
                if proj_cost_full:
                    if proj_tokens_full:
                        user_tokens_effective = int(user_cost_full / proj_cost_full * proj_tokens_full)
                    if proj_weight_full:
                        user_weight_effective = user_cost_full / proj_cost_full * proj_weight_full
                extrapolated = True

        if proj_weight_full and proj_cost_full:
            user_cost = (user_weight_effective / proj_weight_full) * proj_cost_full
        else:
            user_cost = 0.0
        total_cost += user_cost

        per_project.append({
            "id": pid,
            "name": short,
            "project_cost": proj_cost_full,
            "project_tokens": proj_tokens_full,
            "user_tokens": user_tokens_effective,
            # The rendered "User share" is a share of DOLLARS, so it has to come
            # from the same weights that produced user_cost — a raw-token share
            # disagrees with it on any mixed-tier project and the row stops
            # reconciling (share x project $ != user $). Raw counts stay above for
            # JSON consumers that want token volumes.
            "user_weight": round(user_weight_effective, 4),
            "project_weight": round(proj_weight_full, 4),
            "user_tokens_post_rollout": user_tokens_post,
            "user_completions_post_rollout": n_post,
            "user_cost": round(user_cost, 4),
            # Always False post-PROD-2466. Retained for backward compatibility with
            # `eng:report-user-costs` JSON consumers; safe to remove in a future PR.
            "braintrust_supplemented": False,
            "extrapolated": extrapolated,
        })

    # Sort per-project rows by user_cost desc for the rendered table
    per_project.sort(key=lambda p: -p["user_cost"])

    if all_sources == {"ledger"}:
        attribution_source = "LLM ledger BigQuery llm.llm_call — sees Chat Completions + Responses API calls)"
    elif all_sources == {"completions"}:
        if end_unix <= LEDGER_EPOCH_UNIX:
            attribution_source = f"stored chat completions (window entirely before ledger epoch {LEDGER_EPOCH_LABEL})"
        elif ledger_fallback_projects:
            attribution_source = (
                "stored chat completions (ledger unavailable — chat-API-only fallback; "
                "Responses-API traffic not visible)"
            )
        else:
            attribution_source = "stored chat completions (no ledger-covered project traffic in window)"
    elif all_sources == {"ledger", "completions"}:
        reasons = []
        if start_unix < LEDGER_EPOCH_UNIX < end_unix:
            reasons.append(f"window straddles ledger epoch {LEDGER_EPOCH_LABEL}")
        if ledger_fallback_projects:
            reasons.append(
                "ledger unavailable for: " + ", ".join(sorted(ledger_fallback_projects))
                + " (chat-API-only fallback; Responses-API traffic not visible)"
            )
        if not reasons:
            reasons.append("non-IS projects use stored chat completions")
        attribution_source = "mixed (" + "; ".join(reasons) + ")"
    else:
        attribution_source = "none (no per-user attribution data fetched)"
    notes.append(f"Attribution source: {attribution_source}.")

    report = {
        "user_id": args.user_id,
        "window": win,
        "totals": {
            "cost_usd": round(total_cost, 4),
            "per_day_usd": round(total_cost / max(win["days_in"], 1), 4),
        },
        "per_project": per_project,
        "notes": notes,
        "attribution_source": attribution_source,
    }
    print(render_markdown(report, show_by_project=args.by_project))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2))
        print(f"# Wrote structured report to {args.json_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
