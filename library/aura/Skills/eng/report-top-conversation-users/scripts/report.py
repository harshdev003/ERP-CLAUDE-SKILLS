#!/usr/bin/env python3
"""
Combined top-N user ranking across OpenAI + ElevenLabs for the Aura org.

Defaults to yesterday Pacific. For OpenAI, walks completions in the PT window
spanning two UTC bucket spans (yesterday UTC + today UTC) and allocates $ from
each bucket proportionally by token weight — so the report's window matches
ElevenLabs' natural PT alignment without phantom "voice-only" users at the seam.

Output sections (actuals only):
  1. Headline — combined $ for the window (no projected averages, no derived figures)
  2. Top N users (default 10) — UID + state + OAI $/calls + ELabs $/min + Total + Cumul%
  3. Overlap stats — both / OpenAI-only / ElevenLabs-only
  4. Provider mix — $ split

Methodology: PT-share proportional allocation against Costs API authoritative
$ on the OpenAI side (walks full UTC bucket per project, attributes
`bucket_$ × user_pt_tokens / full_day_tokens` so we only charge users for the
PT-share of each bucket). ElevenLabs side: `duration_secs × per-minute rate`
on each call detail — no credits → minutes derivation. Subscription state on
the top-N via Aura PaymentService with `includeDeleted=true`. See
references/allocation.md.

Usage:
    python report.py                                   # yesterday PT (default)
    python report.py --window today-pt                 # partial day
    python report.py --window last-7d                  # rolling 7d UTC
    python report.py --since 2026-05-15 --until 2026-05-17    # custom PT range
    python report.py --top 25
    python report.py --no-state                        # skip state annotation
    python report.py --post-to-slack
    python report.py --json out.json

Attribution sources (OpenAI side): the token-weight walk that distributes each
bucket's authoritative Costs-API $ now reads the IntelligenceService per-call
LLM ledger (BigQuery llm.llm_call) FIRST for any slice of the window at or after
the ledger epoch (2026-07-23T03:25Z, IS 0.285.0). Pre-epoch slices always use
the OpenAI stored-chat-completions walk (the only source for that period). If
the ledger request fails, the affected slice falls back to the completions
walk with a stderr warning — note that fallback is chat-API-only and cannot
see Responses-API traffic. Dollar methodology is unchanged: the Costs API
stays the $ authority; the ledger only replaces the token-weight attribution.

Env:
    OPENAI_ADMIN_API_KEY      (Admin API key, required — /v1/organization/* only:
                               costs/usage/projects. PROD-3572: OpenAI no longer
                               lets Admin keys hold model.request, so the admin key
                               CANNOT walk /v1/chat/completions.)
    OPENAI_<PROJECT>_API_KEY  (project-scoped service-account key, one per production
                               project, required for the stored-completions walk —
                               "Memory" → OPENAI_MEMORY_API_KEY, etc. Any project
                               whose key is unset is skipped with a stderr warning.
                               PROD-3572. Only exercised for pre-epoch slices or
                               when the ledger is unavailable.)
    ELEVENLABS_ADMIN_API_KEY  (preferred) or ELEVENLABS_API_KEY (fallback; required)
    GCP_KEY                   (the LLM-ledger attribution read, BigQuery llm.llm_call;
                               missing → completions fallback, not a crash)
    AURA_API_TOKEN            (state annotation; missing → no state column)
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
ELEVENLABS_API = "https://api.elevenlabs.io/v1"
AURA_API = "https://services.aurahealth.io"
AURA_SUBSCRIPTIONS_PATH = "/subscriptions/list/{user_id}"


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
# The OpenAI Costs API buckets by UTC day and each day's full dollars are
# allocated against a full-day token denominator. Splitting the epoch-straddling
# UTC day across the completions (pre-epoch) and ledger (post-epoch) slices would
# let each allocate that same day's dollars against a partial-day denominator and
# overstate the day. Snap the failover boundary up to the next UTC midnight so
# every UTC cost bucket is owned by exactly one source; the epoch day's
# post-epoch traffic is attributed via the completions walk (chat-API-only for
# that single day — an acceptable, already-annotated fallback limitation).
_ledger_epoch_dt = datetime.fromtimestamp(LEDGER_EPOCH_UNIX, tz=timezone.utc)
_ledger_epoch_midnight = _ledger_epoch_dt.replace(hour=0, minute=0, second=0, microsecond=0)
LEDGER_BUCKET_EPOCH_UNIX = int(
    (_ledger_epoch_midnight if _ledger_epoch_midnight == _ledger_epoch_dt
     else _ledger_epoch_midnight + timedelta(days=1)).timestamp()
)
LEDGER_EPOCH_ISO = "2026-07-23T03:25Z"

PT = timezone(timedelta(hours=-7))

# Production OpenAI projects (hardcoded — same as sibling skills)
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

# ElevenLabs ConvAI rates from Tools/ElevenLabs (Business plan)
COST_PER_MIN_IN_PLAN = 0.09
COST_PER_MIN_BURST = 0.19

# OpenAI tagging rollout — calls before this date have no aiPremium*/premium* fields
TAGGING_ROLLOUT_UNIX = int(datetime(2026, 5, 12, tzinfo=timezone.utc).timestamp())

DEFAULT_TOP_N = 10
ELABS_RETENTION_DAYS = 10
UID_TRUNCATE_LEN = 8


def _project_completions_api_key_env(project_name: str) -> str:
    """PROD-3572: "Memory [Production]" -> "OPENAI_MEMORY_API_KEY"."""
    base = re.sub(r"\[.*?\]", "", project_name).strip()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").upper()
    return f"OPENAI_{base}_API_KEY"


def _resolve_completions_api_key(project_name: str) -> str | None:
    """Resolve the project-scoped key for a stored-completions walk (PROD-3572).
    Returns None + stderr warning when unset, so the caller skips that project
    rather than hard-failing. OPENAI_ADMIN_API_KEY no longer works for
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
# IS LLM-call ledger (primary attribution source for post-epoch slices)
# ---------------------------------------------------------------------------

class LedgerUnavailable(Exception):
    pass


def _ledger_project_key(project_name: str) -> str:
    """OpenAI dashboard project name -> IS ledger project key.
    'Memory [Production]' -> 'memory' (strip env suffix, lowercase first letter)."""
    base = re.sub(r"\s*\[.*?\]\s*", "", project_name).strip()
    return base[:1].lower() + base[1:] if base else base


def _iso_to_unix(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _unix_to_iso(ts: int | float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _fmt_slice(start_ts: int, end_ts: int) -> str:
    fmt = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")  # noqa: E731
    return f"[{fmt(start_ts)} → {fmt(end_ts)})"


def iter_llm_calls(start_unix: int, end_unix: int, *, user_id: str | None = None,
                   prompter_name: str | None = None, project_key: str | None = None,
                   provider: str | None = None, openai_api: str | None = None) -> list[dict]:
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


# ---------------------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------------------

def resolve_window(window: str | None, since: str | None, until: str | None) -> tuple[int, int, str, bool]:
    """Return (start_ts_unix, end_ts_unix, label, is_partial). All UTC unix timestamps."""
    now = datetime.now(tz=timezone.utc)
    now_pt = now.astimezone(PT)
    today_pt = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)

    if since or until:
        if not (since and until):
            raise ValueError("--since and --until must be used together")
        s = datetime.fromisoformat(since).replace(tzinfo=PT)
        u = datetime.fromisoformat(until).replace(tzinfo=PT)
        if u <= s:
            raise ValueError("--until must be after --since")
        label = f"{s.strftime('%Y-%m-%d')} → {u.strftime('%Y-%m-%d')} PT"
        return int(s.timestamp()), int(u.timestamp()), label, False

    w = (window or "yesterday-pt").lower().replace("_", "-")
    if w in ("yesterday-pt", "yesterday"):
        end = today_pt
        start = end - timedelta(days=1)
        return int(start.timestamp()), int(end.timestamp()), f"Yesterday PT ({start.strftime('%b %-d')})", False
    if w in ("today-pt", "today"):
        return int(today_pt.timestamp()), int(now.timestamp()), f"Today PT ({today_pt.strftime('%b %-d')}, partial)", True
    if w in ("last-7d", "last7d", "7d"):
        start = now - timedelta(days=7)
        return int(start.timestamp()), int(now.timestamp()), "Last 7 days UTC (rolling)", False
    raise ValueError(f"Unknown --window {window!r}")


def utc_bucket_spans(start_ts: int, end_ts: int) -> list[tuple[int, int, int]]:
    """Split [start, end) into 1+ UTC midnight-aligned bucket spans.

    Returns [(bucket_start_ts, span_start_ts, span_end_ts), ...] where
    bucket_start_ts is the midnight-UTC start of the bucket the span falls in.
    Used for fetching Costs API totals per bucket then proportionally
    allocating to completions that fall in our PT window.
    """
    spans: list[tuple[int, int, int]] = []
    cur = start_ts
    while cur < end_ts:
        dt = datetime.fromtimestamp(cur, tz=timezone.utc)
        bucket_start = int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        bucket_end = bucket_start + 86400
        span_end = min(bucket_end, end_ts)
        spans.append((bucket_start, cur, span_end))
        cur = span_end
    return spans


# ---------------------------------------------------------------------------
# OpenAI side
# ---------------------------------------------------------------------------

def _http_get(url: str, headers: dict, params: dict | None = None, retries: int = 3) -> dict:
    for attempt in range(retries):
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def fetch_costs_for_bucket(api_key: str, bucket_start: int) -> dict[str, float]:
    """Returns {project_id: $} for a single UTC midnight-aligned day."""
    out: dict[str, float] = {}
    params = {
        "start_time": bucket_start,
        "end_time": bucket_start + 86400,
        "bucket_width": "1d",
        "limit": 31,
        "group_by[]": "project_id",
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    body = _http_get(f"{OPENAI_API}/organization/costs", headers, params)
    for b in body.get("data", []):
        for row in b.get("results", []):
            pid = row.get("project_id")
            if pid:
                out[pid] = out.get(pid, 0.0) + float(row["amount"]["value"])
    return out


def walk_completions(api_key: str, project_id: str, span_start: int, span_end: int):
    """Yield completions in [span_start, span_end). API returns newest-first."""
    headers = {"Authorization": f"Bearer {api_key}", "OpenAI-Project": project_id}
    after: str | None = None
    while True:
        params: dict = {"limit": 100}
        if after:
            params["after"] = after
        body = _http_get(f"{OPENAI_API}/chat/completions", headers, params)
        items = body.get("data", [])
        if not items:
            return
        stop = False
        for it in items:
            created = it.get("created", 0)
            if created >= span_end:
                continue
            if created < span_start:
                stop = True
                break
            yield it
        if stop or not body.get("has_more"):
            return
        after = items[-1].get("id")
        if not after:
            return
        time.sleep(0.04)


def _truthy(v) -> bool:
    if isinstance(v, bool): return v
    if isinstance(v, str): return v.strip().lower() in ("true","1","yes")
    return bool(v)


def ai_tier_label(metadata: dict) -> str:
    """Single AI premium tier label from the metadata stamped on the completion."""
    aip = metadata.get("aiPremium")
    aips = metadata.get("aiPremiumState")
    if aip is None and aips is None:
        return "Untagged"
    if not _truthy(aip):
        return "Non Premium"
    labels = {"paid":"Premium Paid","trial":"Premium Trial","free":"Premium Free","paymentPending":"Premium Payment Pending"}
    if aips and aips in labels:
        return labels[aips]
    return f"Premium {aips}" if aips else "Premium"


def ai_tier_label_from_ledger(ai_premium, ai_premium_state) -> str:
    """Tier label for a ledger row, reusing the shared `ai_tier_label` off a
    reconstructed metadata dict. The aiPremium boolean is its own ledger column
    (added 2026-07 — ENGBACK-1237), so a churned user (aiPremium=false) stays
    "Non Premium" instead of collapsing into "Untagged". Rows predating the
    column carry aiPremium=null and fall back to inferring a truthy flag from a
    present state."""
    metadata: dict = {}
    if ai_premium is not None:
        metadata["aiPremium"] = "true" if ai_premium else "false"
    elif ai_premium_state:
        metadata["aiPremium"] = "true"
    if ai_premium_state:
        metadata["aiPremiumState"] = ai_premium_state
    return ai_tier_label(metadata)


def aggregate_openai_users(api_key: str, pt_start: int, pt_end: int, sink_log,
                           seen_ids: set | None = None) -> tuple[dict[str, dict], bool]:
    """Walk completions across all production projects × all overlapping UTC
    buckets, allocate $ proportionally by PT-share of token weight, return per-UID.

    For each (UTC bucket, project) pair we walk the FULL UTC bucket to learn
    full_day_tokens, and within that walk we capture per-UID tokens that fall
    in the PT window. Allocation:

        user_$ = bucket_$ × user_pt_tokens / full_day_tokens

    Sum across PT-window users for one (bucket, project) = bucket_$ × pt_share,
    NOT bucket_$. This is the PT-attributable slice — the actuals-only answer.

    The prior version walked only the PT-span slice of each bucket and divided
    by PT-span tokens, which sums to the FULL bucket $ and overstates by ~2×
    for the default yesterday-pt window. See references/allocation.md for the
    full derivation. Fixed in ENGBACK-1087.
    """
    user_total: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "calls": 0, "ai_tier_last": None})
    spans = utc_bucket_spans(pt_start, pt_end)
    sink_log(f"  OpenAI: {len(spans)} UTC bucket span(s) for PT window")

    today_utc = int(datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    today_bucket_propagated_warning = False

    for bucket_start, span_start, span_end in spans:
        bucket_end = bucket_start + 86400
        bucket_dollars = fetch_costs_for_bucket(api_key, bucket_start)
        bucket_total = sum(bucket_dollars.values())
        # Warning: today UTC bucket may not have propagated
        if bucket_start == today_utc and bucket_total == 0:
            today_bucket_propagated_warning = True
            continue  # no $ to allocate
        if bucket_total == 0:
            continue
        sink_log(f"    bucket {datetime.fromtimestamp(bucket_start, tz=timezone.utc).strftime('%Y-%m-%d')}: ${bucket_total:.2f} total, PT span {span_start}→{span_end}")
        for proj_name, pid in PROD_PROJECTS.items():
            dollars = bucket_dollars.get(pid, 0)
            if dollars == 0:
                continue
            # PROD-3572: the completions walk needs a project-scoped key; the
            # admin key can no longer hold model.request. Skip a project whose
            # key is unset rather than hard-fail (its $ won't be attributed).
            proj_key = _resolve_completions_api_key(proj_name)
            if proj_key is None:
                continue
            # Walk the FULL UTC bucket — we need full_day_tokens as the
            # allocation denominator so that we attribute only the PT-share
            # of the bucket's $ rather than 100% of it.
            user_tok: dict[str, float] = defaultdict(float)
            user_calls: dict[str, int] = defaultdict(int)
            user_tier: dict[str, str] = {}
            full_day_tok = 0.0
            for it in walk_completions(proj_key, pid, bucket_start, bucket_end):
                usage = it.get("usage") or {}
                # Same basis for numerator and denominator — both accumulate here.
                tok = ((usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)) \
                    * service_tier_factor(it.get("service_tier"))
                if tok == 0:
                    continue
                full_day_tok += tok
                # Only attribute to user if the call falls in the PT window
                created = it.get("created", 0)
                if not (span_start <= created < span_end):
                    continue
                md = it.get("metadata") or {}
                uid = md.get("user") or it.get("user")
                if not uid:
                    continue
                # Defensive dedupe against the ledger slice on the epoch boundary
                # (ledger openaiId == stored completion id).
                oid = it.get("id")
                if seen_ids is not None and oid:
                    if oid in seen_ids:
                        continue
                    seen_ids.add(oid)
                user_tok[uid] += tok
                user_calls[uid] += 1
                user_tier[uid] = ai_tier_label(md)
            if full_day_tok == 0:
                continue
            for uid, tk in user_tok.items():
                share = dollars * tk / full_day_tok
                u = user_total[uid]
                u["cost"] += share
                u["calls"] += user_calls[uid]
                u["ai_tier_last"] = user_tier.get(uid, u["ai_tier_last"])
    return dict(user_total), today_bucket_propagated_warning


def aggregate_openai_users_ledger(api_key: str, pt_start: int, pt_end: int, sink_log,
                                  seen_ids: set | None = None) -> tuple[dict[str, dict], bool]:
    """Ledger flavour of aggregate_openai_users: same per-(UTC bucket, project)
    proportional allocation against the authoritative Costs API $, but the
    token-weight walk reads the IS per-call LLM ledger instead of stored chat
    completions. One paged ledger walk per bucket (provider=openai), grouped by
    ledger project key client-side. Field mapping: metadata.user -> userId,
    metadata.aiPremium -> aiPremium, metadata.aiPremiumState -> aiPremiumState,
    usage.prompt_tokens -> inputTokens, usage.completion_tokens -> outputTokens.

    Raises LedgerUnavailable (missing token / non-200 / timeout / bad JSON) so
    the caller can fall back to the stored-completions walk for this slice."""
    user_total: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "calls": 0, "ai_tier_last": None})
    spans = utc_bucket_spans(pt_start, pt_end)
    sink_log(f"  OpenAI (ledger): {len(spans)} UTC bucket span(s) for PT window")

    today_utc = int(datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    today_bucket_propagated_warning = False

    pid_by_ledger_key = {_ledger_project_key(name): pid for name, pid in PROD_PROJECTS.items()}

    for bucket_start, span_start, span_end in spans:
        bucket_end = bucket_start + 86400
        bucket_dollars = fetch_costs_for_bucket(api_key, bucket_start)
        bucket_total = sum(bucket_dollars.values())
        if bucket_start == today_utc and bucket_total == 0:
            today_bucket_propagated_warning = True
            continue
        if bucket_total == 0:
            continue
        sink_log(f"    bucket {datetime.fromtimestamp(bucket_start, tz=timezone.utc).strftime('%Y-%m-%d')}: ${bucket_total:.2f} total, PT span {span_start}→{span_end} (ledger walk)")
        # The ledger only has rows from LEDGER_EPOCH_UNIX onward — clamp the
        # bucket walk so the epoch-straddling bucket doesn't request dead air.
        walk_start = max(bucket_start, LEDGER_EPOCH_UNIX)
        rows = iter_llm_calls(walk_start, bucket_end, provider="openai")
        rows_by_key: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            key = row.get("project")
            if key in pid_by_ledger_key:
                rows_by_key[key].append(row)
        for ledger_key, pid in pid_by_ledger_key.items():
            dollars = bucket_dollars.get(pid, 0)
            if dollars == 0:
                continue
            user_tok: dict[str, float] = defaultdict(float)
            user_calls: dict[str, int] = defaultdict(int)
            user_tier: dict[str, str] = {}
            full_day_tok = 0.0
            for row in rows_by_key.get(ledger_key, []):
                # Both `full_day_tok` and `user_tok` accumulate from this one loop,
                # so scaling here keeps numerator and denominator on the same basis.
                tok = ((row.get("inputTokens") or 0) + (row.get("outputTokens") or 0)) \
                    * service_tier_factor(row.get("serviceTier"))
                if tok == 0:
                    continue
                full_day_tok += tok
                created = _iso_to_unix(row.get("createdAt"))
                if created is None or not (span_start <= created < span_end):
                    continue
                uid = row.get("userId")
                if not uid:
                    continue
                # Defensive dedupe against the completions slice on the epoch
                # boundary (ledger openaiId == stored completion id).
                oid = row.get("openaiId")
                if seen_ids is not None and oid:
                    if oid in seen_ids:
                        continue
                    seen_ids.add(oid)
                user_tok[uid] += tok
                user_calls[uid] += 1
                user_tier[uid] = ai_tier_label_from_ledger(
                    row.get("aiPremium"), row.get("aiPremiumState")
                )
            if full_day_tok == 0:
                continue
            for uid, tk in user_tok.items():
                share = dollars * tk / full_day_tok
                u = user_total[uid]
                u["cost"] += share
                u["calls"] += user_calls[uid]
                u["ai_tier_last"] = user_tier.get(uid, u["ai_tier_last"])
    return dict(user_total), today_bucket_propagated_warning


def aggregate_openai_users_with_failover(api_key: str, pt_start: int, pt_end: int,
                                         sink_log) -> tuple[dict[str, dict], bool, str]:
    """Split the window at LEDGER_BUCKET_EPOCH_UNIX (the epoch snapped up to the
    next UTC midnight, so no UTC cost bucket is split across sources) and pick
    the attribution source per slice:

      slice_before = [start, min(end, BUCKET_EPOCH))  — always the stored-chat-
        completions walk. Owns the epoch-straddling UTC day in full (it walks the
        whole day for its denominator); that day's post-epoch traffic is therefore
        chat-API-only — Responses-API calls on the epoch day are invisible.
      slice_after  = [max(start, BUCKET_EPOCH), end)  — the IS LLM ledger as
        PRIMARY (whole UTC days only); on LedgerUnavailable, stderr warning + fall
        back to the completions walk for this slice (chat-API-only fallback).

    Results are merged; per-user attribution dedupes defensively on the OpenAI
    object id (ledger openaiId == stored completion id) across the boundary.
    Returns (users, today_bucket_warning, attribution_source_label)."""
    merged: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "calls": 0, "ai_tier_last": None})
    seen_ids: set = set()
    warning = False
    sources: list[str] = []

    def merge(users: dict[str, dict]) -> None:
        for uid, u in users.items():
            m = merged[uid]
            m["cost"] += u["cost"]
            m["calls"] += u["calls"]
            if u.get("ai_tier_last"):
                m["ai_tier_last"] = u["ai_tier_last"]

    before_start, before_end = pt_start, min(pt_end, LEDGER_BUCKET_EPOCH_UNIX)
    after_start, after_end = max(pt_start, LEDGER_BUCKET_EPOCH_UNIX), pt_end

    if before_start < before_end:
        sink_log(f"  Attribution {_fmt_slice(before_start, before_end)}: stored chat completions (pre-ledger-epoch)")
        users, w = aggregate_openai_users(api_key, before_start, before_end, sink_log, seen_ids=seen_ids)
        merge(users)
        warning = warning or w
        sources.append("stored chat completions (pre-epoch)")

    if after_start < after_end:
        sink_log(f"  Attribution {_fmt_slice(after_start, after_end)}: LLM ledger (primary)")
        try:
            attempt_seen = set(seen_ids)
            users, w = aggregate_openai_users_ledger(api_key, after_start, after_end, sink_log,
                                                     seen_ids=attempt_seen)
            seen_ids |= attempt_seen
            merge(users)
            warning = warning or w
            sources.append("LLM ledger")
        except LedgerUnavailable as exc:
            _fail_closed_on_ledger_outage(exc)
            print(
                f"WARNING: ledger unavailable ({exc}): falling back to stored chat completions "
                f"for {_fmt_slice(after_start, after_end)}. NOTE: the completions fallback is "
                f"chat-API-only and cannot see Responses-API traffic.",
                file=sys.stderr,
            )
            users, w = aggregate_openai_users(api_key, after_start, after_end, sink_log, seen_ids=seen_ids)
            merge(users)
            warning = warning or w
            sources.append("stored chat completions (ledger unavailable fallback — chat-API-only, Responses-API traffic not visible)")

    if len(sources) > 1:
        source_label = (f"mixed (window straddles ledger epoch {LEDGER_EPOCH_ISO}): "
                        + " + ".join(sources))
    elif sources == ["LLM ledger"]:
        source_label = "LLM ledger (BigQuery llm.llm_call)"
    elif sources:
        source_label = sources[0]
    else:
        source_label = "n/a (empty window)"
    return dict(merged), warning, source_label


# ---------------------------------------------------------------------------
# ElevenLabs side
# ---------------------------------------------------------------------------

def aggregate_elevenlabs_users(api_key: str, pt_start: int, pt_end: int, sink_log) -> dict[str, dict]:
    """Walk ConvAI conversations in [pt_start, pt_end), fetch each detail,
    aggregate per-UID minutes + $."""
    headers = {"xi-api-key": api_key}
    sink_log(f"  ElevenLabs: walking ConvAI conversations in window...")
    conv_ids: list[str] = []
    after: str | None = None
    while True:
        params = {"page_size": 100,
                  "call_start_after_unix": pt_start,
                  "call_start_before_unix": pt_end}
        if after:
            params["cursor"] = after
        body = _http_get(f"{ELEVENLABS_API}/convai/conversations", headers, params)
        convs = body.get("conversations", [])
        for c in convs:
            cid = c.get("conversation_id")
            if cid: conv_ids.append(cid)
        if not body.get("has_more"):
            break
        after = body.get("next_cursor")
        if not after:
            break
    sink_log(f"    found {len(conv_ids)} conversations; fetching details...")
    user: dict[str, dict] = defaultdict(lambda: {"calls": 0, "minutes": 0.0, "cost": 0.0})
    for cid in conv_ids:
        try:
            detail = _http_get(f"{ELEVENLABS_API}/convai/conversations/{cid}", headers)
        except requests.HTTPError:
            continue
        md = detail.get("metadata") or {}
        dur = float(md.get("call_duration_secs") or 0)
        if dur <= 0:
            continue
        is_burst = bool((md.get("charging") or {}).get("is_burst"))
        dv = (detail.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {}
        uid = dv.get("userId") or dv.get("user_id")
        if not uid:
            continue
        minutes = dur / 60.0
        rate = COST_PER_MIN_BURST if is_burst else COST_PER_MIN_IN_PLAN
        cost = minutes * rate
        user[uid]["calls"] += 1
        user[uid]["minutes"] += minutes
        user[uid]["cost"] += cost
        time.sleep(0.02)
    return dict(user)


# ---------------------------------------------------------------------------
# Aura subscription state annotation
# ---------------------------------------------------------------------------

def fetch_subscription_rows(user_id: str, aura_token: str) -> list[dict] | None:
    """POST /subscriptions/list/:userId with includeDeleted=true so refunds /
    revocations surface alongside active / cancelled / expired rows."""
    headers = {"Authorization": f"Bearer {aura_token}", "User-Agent": user_agent(), "Content-Type": "application/json"}
    try:
        r = requests.post(f"{AURA_API}{AURA_SUBSCRIPTIONS_PATH.format(user_id=user_id)}",
                          headers=headers, json={"includeDeleted": True}, timeout=30)
    except requests.RequestException:
        return None
    if not r.ok:
        return None
    rows = r.json()
    return rows if isinstance(rows, list) else None


def _parse_dt(v: str | None) -> datetime | None:
    if not v: return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _is_active(row: dict, now: datetime) -> bool:
    if row.get("deletedAt"): return False
    if row.get("lifetime"): return True
    exp = _parse_dt(row.get("expirationAt"))
    return bool(exp and exp > now)


# Window for "deleted ≈ expired" heuristic. Tuned for the observed natural-
# expiration flow (seconds-scale gap as the system auto-revokes after trial
# end). 5 minutes is generous against clock skew and cron-batch delays.
# Verified 2026-05-17 against a real user (`KKBjkNBB…`): gap was 27 seconds.
DELETION_NEAR_EXPIRY_SECONDS = 300


def _classify_deletion(row: dict) -> str | None:
    """Disambiguate the cause of a `deletedAt` row.

    Returns one of:
      - "trial_expired" — natural end of trial after autoRenew was disabled.
        Signal: `trial=true`, `autoRenew=false`, `deletedAt` within 5 min of
        `expirationAt`. Standard end-of-trial cleanup flow.
      - "refunded" — `deletedAt` materially before `expirationAt`. Implies
        refund (Apple/Google/Aura processed before the period would have ended).
      - "revoked" — everything else (admin/CS action, payment-failure cleanup).
      - `None` if the row isn't actually deleted.

    See ENG-540 for the cohort-analysis rationale.
    """
    deleted = _parse_dt(row.get("deletedAt"))
    if not deleted:
        return None
    expires = _parse_dt(row.get("expirationAt"))
    trial = bool(row.get("trial"))
    auto_renew_off = row.get("autoRenew") is False
    if trial and auto_renew_off and expires and abs((deleted - expires).total_seconds()) < DELETION_NEAR_EXPIRY_SECONDS:
        return "trial_expired"
    if expires and (expires - deleted).total_seconds() > DELETION_NEAR_EXPIRY_SECONDS:
        return "refunded"
    return "revoked"


def derive_state(rows: list[dict]) -> str:
    """Single short state label; prefers AI dimension over content.

    The deletion-state branch uses `_classify_deletion` to disambiguate
    natural trial-expiration from refund from revoke — see ENG-540.
    """
    if not rows:
        return "Never premium"
    now = datetime.now(tz=timezone.utc)
    ai_rows = [r for r in rows if r.get("type") == "ai"]
    content_rows = [r for r in rows if r.get("type") == "content"]
    if ai_rows:
        actives = [r for r in ai_rows if _is_active(r, now)]
        if actives:
            row = max(actives, key=lambda r: _parse_dt(r.get("purchaseAt")) or datetime.min.replace(tzinfo=timezone.utc))
            if row.get("lifetime"): return "AI Lifetime"
            if row.get("trial"):
                return "AI Trial (cancelled)" if row.get("autoRenew") is False else "AI Trial"
            return "AI Cancelled (active)" if row.get("autoRenew") is False else "AI Paid"
        # No active AI rows but history exists — classify the most recent deletion
        deleted_ai = [r for r in ai_rows if r.get("deletedAt")]
        if deleted_ai:
            most_recent = max(deleted_ai, key=lambda r: _parse_dt(r.get("deletedAt")) or datetime.min.replace(tzinfo=timezone.utc))
            cause = _classify_deletion(most_recent)
            if cause == "trial_expired": return "AI Trial Expired"
            if cause == "refunded": return "AI Refunded"
            return "AI Revoked"
        return "AI Expired"
    if content_rows:
        if any(_is_active(r, now) for r in content_rows):
            return "Content-only (Aura)"
        return "Content Expired (Aura)"
    return "Never premium"


def derive_timeline(rows: list[dict]) -> list[dict]:
    """Per-user chronological timeline. Surfaced in --json only.

    Per-row `status` uses `_classify_deletion` to disambiguate the three
    causes of a `deletedAt` row (trial-expired / refunded / revoked) — see ENG-540.
    """
    if not rows:
        return []
    now = datetime.now(tz=timezone.utc)
    entries: list[dict] = []
    for r in rows:
        exp = _parse_dt(r.get("expirationAt"))
        deleted = bool(r.get("deletedAt"))
        if deleted:
            cause = _classify_deletion(r)
            status = {
                "trial_expired": "trial-expired",
                "refunded": "refunded",
                "revoked": "revoked",
            }.get(cause, "revoked")
        elif r.get("lifetime"): status = "active"
        elif exp and exp > now: status = "cancelled-active" if r.get("autoRenew") is False else "active"
        elif exp and exp <= now: status = "expired"
        else: status = "active"
        entries.append({
            "type": r.get("type"), "provider": r.get("provider"),
            "trial": bool(r.get("trial")), "lifetime": bool(r.get("lifetime")),
            "created_at": r.get("createdAt"), "purchase_at": r.get("purchaseAt"),
            "converted_at": r.get("convertedAt"), "expiration_at": r.get("expirationAt"),
            "deleted_at": r.get("deletedAt"), "auto_renew": r.get("autoRenew"),
            "status": status,
        })
    entries.sort(key=lambda e: _parse_dt(e.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return entries


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def truncate_uid(uid: str) -> str:
    if len(uid) <= UID_TRUNCATE_LEN:
        return uid
    return uid[:UID_TRUNCATE_LEN] + "…"


def fmt_money(v: float) -> str:
    return f"${v:.2f}"


def render(top: list[dict], oai_total: float, elabs_total: float, label: str,
           is_partial: bool, oai_total_users: int,
           elabs_total_users: int, both_users: int, oai_only: int, elabs_only: int,
           today_bucket_warning: bool, no_state: bool, window_start: int,
           attribution_source: str) -> str:
    lines: list[str] = []
    combined_total = oai_total + elabs_total

    header = f"# Top combined conversation users — {label}"
    if is_partial:
        header += " ⚠️ partial"
    lines.append(header)
    lines.append("")

    # Actuals only — no projected averages, no derived figures.
    lines.append(f"**{fmt_money(combined_total)} combined**")
    lines.append(f"({fmt_money(oai_total)} OpenAI + {fmt_money(elabs_total)} ElevenLabs)")
    lines.append("")

    if today_bucket_warning:
        lines.append("> ⚠️ Today's OpenAI Costs API bucket has not propagated yet — evening PT usage may be undercounted on the OpenAI side. Re-run after ~10am UTC for clean numbers.")
        lines.append("")
    if window_start < TAGGING_ROLLOUT_UNIX:
        lines.append("> ⚠️ Window predates or straddles the 2026-05-12 metadata tagging rollout — pre-rollout OpenAI calls have no aiPremium/aiPremiumState tags. State annotation will be sparse.")
        lines.append("")

    # Top N
    lines.append(f"## Top {len(top)}")
    lines.append("")
    state_col = "" if no_state else " State |"
    lines.append(f"| Rank | UID |{state_col} OAI $ | OAI calls | ELabs $ | ELabs min | Total $ | Cumul% |")
    lines.append(f"|---:|---|{'---|' if not no_state else ''}---:|---:|---:|---:|---:|---:|")
    cumul = 0.0
    user_total_cost = sum(u["total"] for u in top)
    for i, u in enumerate(top, start=1):
        pct = (u["total"] / user_total_cost * 100) if user_total_cost else 0
        cumul += pct
        short = truncate_uid(u["uid"])
        state_cell = f" {u.get('state') or '?'} |" if not no_state else ""
        lines.append(f"| {i} | `{short}` |{state_cell} {fmt_money(u['oai_cost'])} | {u['oai_calls']:,} | {fmt_money(u['elabs_cost'])} | {u['elabs_minutes']:.1f} | **{fmt_money(u['total'])}** | {cumul:.1f}% |")
    lines.append("")

    # Overlap
    elabs_only_flag = "" if elabs_only == 0 else f" ({elabs_only} ⚠️ — check window alignment or OpenAI tagging gap)"
    lines.append("## Overlap")
    lines.append("")
    lines.append(f"- {both_users:,} users on both providers")
    lines.append(f"- {oai_only:,} OpenAI-only (text chatters)")
    lines.append(f"- {elabs_only:,} ElevenLabs-only{elabs_only_flag}")
    lines.append("")

    # Provider mix
    lines.append("## Provider mix")
    lines.append("")
    if combined_total > 0:
        lines.append(f"- OpenAI: {fmt_money(oai_total)} ({oai_total/combined_total*100:.0f}%)")
        lines.append(f"- ElevenLabs: {fmt_money(elabs_total)} ({elabs_total/combined_total*100:.0f}%)")
    lines.append("")
    lines.append("_Allocation (actuals only). OpenAI side: per-project $ from `/v1/organization/costs` × `user_pt_tokens / full_day_tokens` (walks full UTC bucket per project so attribution scales to the PT-share of each bucket — no projected averages, no extrapolation). ElevenLabs side: `duration_secs × $0.09/min in-plan, $0.19/min burst` from per-conversation detail. See `references/allocation.md`._")
    lines.append("")
    lines.append(f"_Attribution source (OpenAI token weights): {attribution_source}._")
    return "\n".join(lines)


def slack_summary(top: list[dict], oai_total: float, elabs_total: float, label: str,
                  top_n_show: int = 5) -> str:
    """Condensed Slack payload — UID-truncated, top-N only, no PII. Actuals only."""
    combined = oai_total + elabs_total
    parts = [f"*Top conversation users — {label}*"]
    parts.append(f"*{fmt_money(combined)} combined* ({fmt_money(oai_total)} OpenAI + {fmt_money(elabs_total)} ElevenLabs)")
    parts.append("")
    parts.append(f"Top {min(top_n_show, len(top))}:")
    for i, u in enumerate(top[:top_n_show], start=1):
        short = truncate_uid(u["uid"])
        state = u.get("state") or "?"
        parts.append(f"  {i}. `{short}` ({state}) — {fmt_money(u['total'])} ({fmt_money(u['oai_cost'])} OAI + {fmt_money(u['elabs_cost'])} ELabs)")
    return "\n".join(parts)


def emit_slack_payload(summary: str) -> None:
    print("\n---SLACK-PAYLOAD-BEGIN---", file=sys.stderr)
    print(summary, file=sys.stderr)
    print("---SLACK-PAYLOAD-END---", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Combined top conversation users across OpenAI + ElevenLabs.")
    p.add_argument("--window", default="yesterday-pt", help="yesterday-pt | today-pt | last-7d")
    p.add_argument("--since", help="PT start date YYYY-MM-DD (use with --until)")
    p.add_argument("--until", help="PT end date YYYY-MM-DD (use with --since)")
    p.add_argument("--top", type=int, default=DEFAULT_TOP_N, help=f"Top-N (default {DEFAULT_TOP_N})")
    p.add_argument("--no-state", action="store_true", help="Skip subscription-state annotation (faster)")
    p.add_argument("--post-to-slack", action="store_true", help="Emit Slack payload for the harness")
    p.add_argument("--json", help="Write structured JSON to this path")
    p.add_argument("--allow-degraded-attribution", action="store_true",
                   help="Accept degraded per-user attribution when the LLM ledger is "
                        "unreachable on a post-2026-07-23 window (default: exit 2). "
                        "Env: AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1")
    args = p.parse_args()
    _ALLOW_DEGRADED[0] = _ALLOW_DEGRADED[0] or bool(
        getattr(args, "allow_degraded_attribution", False))

    api_oai = os.environ.get("OPENAI_ADMIN_API_KEY")
    api_elabs = os.environ.get("ELEVENLABS_ADMIN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    aura_token = os.environ.get("AURA_API_TOKEN")
    if not api_oai:
        print("ERROR: OPENAI_ADMIN_API_KEY required", file=sys.stderr); return 2
    if not api_elabs:
        print("ERROR: ELEVENLABS_ADMIN_API_KEY (or ELEVENLABS_API_KEY) required", file=sys.stderr); return 2
    if not aura_token and not args.no_state:
        print("WARN: AURA_API_TOKEN missing — top-N will show without subscription-state column", file=sys.stderr)
        args.no_state = True

    try:
        pt_start, pt_end, label, is_partial = resolve_window(args.window, args.since, args.until)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2

    window_days = (pt_end - pt_start) / 86400
    if window_days > ELABS_RETENTION_DAYS:
        print(f"WARN: window spans {window_days:.1f} days; ElevenLabs ConvAI details retained only {ELABS_RETENTION_DAYS}d — older conversations will 404", file=sys.stderr)

    print(f"Window: {label} ({pt_start} → {pt_end} unix)", file=sys.stderr)

    def log(msg): print(msg, file=sys.stderr)

    # OpenAI side — LLM ledger primary for post-epoch slices, stored chat
    # completions for pre-epoch slices and as the ledger fallback.
    oai_users, today_bucket_warning, attribution_source = aggregate_openai_users_with_failover(
        api_oai, pt_start, pt_end, log)
    oai_total = sum(u["cost"] for u in oai_users.values())
    log(f"  OpenAI: {len(oai_users)} users, {fmt_money(oai_total)} total")
    log(f"  Attribution source: {attribution_source}")

    # ElevenLabs side
    elabs_users = aggregate_elevenlabs_users(api_elabs, pt_start, pt_end, log)
    elabs_total = sum(u["cost"] for u in elabs_users.values())
    log(f"  ElevenLabs: {len(elabs_users)} users, {fmt_money(elabs_total)} total")

    # Merge
    all_uids = set(oai_users.keys()) | set(elabs_users.keys())
    merged: dict[str, dict] = {}
    for uid in all_uids:
        o = oai_users.get(uid, {"cost": 0.0, "calls": 0, "ai_tier_last": None})
        e = elabs_users.get(uid, {"calls": 0, "minutes": 0.0, "cost": 0.0})
        merged[uid] = {
            "uid": uid,
            "oai_cost": o["cost"], "oai_calls": o["calls"], "ai_tier_last": o.get("ai_tier_last"),
            "elabs_cost": e["cost"], "elabs_calls": e["calls"], "elabs_minutes": e["minutes"],
            "total": o["cost"] + e["cost"],
        }

    # Rank
    ranked = sorted(merged.values(), key=lambda u: -u["total"])[: args.top]

    # Annotate top-N with subscription state
    state_timelines: dict[str, list[dict]] = {}
    if aura_token and not args.no_state:
        log(f"  Annotating top {len(ranked)} with subscription state...")
        for u in ranked:
            rows = fetch_subscription_rows(u["uid"], aura_token)
            if rows is not None:
                u["state"] = derive_state(rows)
                state_timelines[u["uid"]] = derive_timeline(rows)
            else:
                u["state"] = "?"

    # Overlap stats
    both = sum(1 for u in merged.values() if u["oai_cost"] > 0 and u["elabs_cost"] > 0)
    oai_only = sum(1 for u in merged.values() if u["oai_cost"] > 0 and u["elabs_cost"] == 0)
    elabs_only = sum(1 for u in merged.values() if u["elabs_cost"] > 0 and u["oai_cost"] == 0)

    # Render — actuals only, no 7d-avg comparison (ENGBACK-1087)
    report = render(ranked, oai_total, elabs_total, label, is_partial,
                    len(oai_users), len(elabs_users), both, oai_only, elabs_only,
                    today_bucket_warning, args.no_state, pt_start, attribution_source)
    print(report)

    if args.post_to_slack:
        emit_slack_payload(slack_summary(ranked, oai_total, elabs_total, label))

    if args.json:
        json_safe = {
            "window": {"start": pt_start, "end": pt_end, "label": label, "is_partial": is_partial,
                       "today_bucket_propagated_warning": today_bucket_warning,
                       "attribution_source": attribution_source},
            "totals": {
                "openai_cost": oai_total, "elabs_cost": elabs_total, "combined_cost": oai_total + elabs_total,
                "openai_users": len(oai_users), "elabs_users": len(elabs_users),
                "users_both": both, "openai_only": oai_only, "elabs_only": elabs_only,
            },
            "top": [
                {**u, "subscription_timeline": state_timelines.get(u["uid"], [])}
                for u in ranked
            ],
        }
        with open(args.json, "w") as f:
            json.dump(json_safe, f, indent=2)
        log(f"\nWrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
