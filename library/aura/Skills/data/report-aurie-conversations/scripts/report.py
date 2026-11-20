#!/usr/bin/env python3
"""data:report-aurie-conversations — weekly (or any-period) Aurie conversation analytics.

Produces, for a resolved time window:
  1. Active conversation users by type (coach / quick-processing / chat / companion / …)
  2. Conversations in the period (distinct coach `conversationId`)
  3. Voice conversations (voice `conversationId` threads) + voice users + voice %
  4. Conversation duration distribution (mean / p25 / p50 / p75 / p95 / max) + turns
  5. Voice vs text-only duration + turns distributions, same conversationId basis

A "conversation" = one `conversationId` (a persistent thread; can span days).
Duration = max(created) − min(created) across that thread's coach turns.
A "voice conversation" = a conversationId thread that had an ElevenLabs voice
session (matched via the Aura conversationId dynamic variable). The activity and
ElevenLabs windows are aligned to the SAME unix range (a window offset otherwise
biases the voice %).

Where the activity numbers come from
------------------------------------
The `llmCall` ledger (BigQuery `aura-main.llm.llm_call`), **not** OpenAI's stored-completions
list. `GET /v1/chat/completions` cannot see Responses-API traffic — stored
responses are readable by id but not enumerable — and Aurie runs on Responses
(migrated 2026-08-07). On 2026-08-04→08-11 that list held 45,356 of 108,899
conversation-project calls (42%), showed `insight-card` as 0 against a true
1,482, and omitted journal entirely, with no error to indicate any of it.

The ledger records **both** APIs and every field this report aggregates, so one
uniform walk covers every surface — and, being bounded server-side by
`from`/`to`, it is fast: ~1–3 min for a 7-day window.
`ledger_reader.coverage` checks the walk against the ledger's own `/stats`
aggregate every run, so a partial read reports itself as a share instead of
passing as the answer.

The ledger began recording on 2026-07-23. Windows reaching back further are
refused rather than under-reported — see --allow-partial.

Auth:
  GCP_KEY — REQUIRED. Reads the ledger from BigQuery; the ledger is the only
    index for Responses-API traffic, so without it there are no activity numbers.
  OPENAI_ADMIN_API_KEY  — required for the /v1/organization/* cost + project
    lookups. Still works there; the model.request scope OpenAI now enforces
    only blocks stored-completions reads (PROD-3572).
  ELEVENLABS_ADMIN_API_KEY (or ELEVENLABS_API_KEY) — optional; without it the
    voice sections are skipped with a note (graceful degradation).

Runtime: the ledger walk is ~1–3 min on a 7-day window; the ElevenLabs detail
walk adds ~8–10 min. Use --no-voice to skip the voice walk.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("This skill requires the 'requests' library. Install with: pip3 install requests")

# `ledger_reader` sits beside this file. Skills are self-contained (Architecture
# principle 1), so the import is by plain name off this script's own directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ledger_reader import (  # noqa: E402
    LedgerError,
    coverage,
    coverage_start,
    ledger_stats,
    row_unix,
    walk_ledger,
)

OPENAI_API = "https://api.openai.com/v1"
ELEVEN_API = "https://api.elevenlabs.io/v1"

# ElevenLabs Conversational AI rates (API actuals — Tools/ElevenLabs).
ELEVEN_IN_PLAN_PER_MIN = 0.09
ELEVEN_BURST_PER_MIN = 0.19

# Conversation [Production] OpenAI project — the *billing* identity, still used
# for the Costs API split below. Activity now comes from the ledger, which keys
# the same project as `project=conversation`.
CONV_PROJECT = os.environ.get("AURA_CONVERSATION_PROJECT_ID", "proj_esFyd4eq2VIAjKUn69X1ZFfk")

# Ledger `project` keys this report aggregates activity for. `conversation`
# carries the conversational prompters; `journal` is a separate OpenAI project
# and so a separate ledger project, which is exactly why journal was invisible
# to a report that only knew about one.
LEDGER_PROJECTS = ("conversation", "journal")

CONVERSATION_TYPES = [
    "coach-prompter",
    "quick-processing-prompter",
    "quick-processing-sentinel-prompter",
    "chat-prompter",
    "companion-prompter",
]
SECONDARY_TYPES = [
    "insight-card-prompter",
    "quick-processing-done-prompter",
    "guardrails-handler-prompter",
]
# Own OpenAI project, single-shot (not a conversation turn) — reported as its
# own row rather than folded into the conversational types.
JOURNAL_TYPES = ["journal-prompter"]

# The prompter whose threads define item 2 ("conversations in period"), their
# durations and their turn counts. Coach-only by design: widening it would move
# the headline conversation count for a reason unrelated to coverage, so any
# week-over-week comparison spanning the change becomes uninterpretable.
THREAD_TYPE = "coach-prompter"

KNOWN_TYPES = CONVERSATION_TYPES + SECONDARY_TYPES + JOURNAL_TYPES

# Per-secondary-type table tag describing what each prompter is:
# - guardrails-handler-prompter: a live in-conversation reply substituting for a
#   coach turn (safety/crisis deflection via handoffToGuardrails).
# - insight-card-prompter: a card generated when the client ends a conversation
#   (createInsightCard=true on POST /conversations/:id/end), shown on the app's
#   done screen. It stamps its own conversationId, distinct from the coach
#   conversation it summarizes — hence excluded from the item-2 conversation count.
# - quick-processing-done-prompter: the quick-processing equivalent of the above
#   (createQuickProcessingDone=true on the same endpoint), writing the done screen
#   to conversation.quickProcessingDone. Unlike insight-card-prompter it carries
#   the conversation's own conversationId, because setQuickProcessingDone forwards
#   it to the prompter — so its calls join to the conversation they close. That is
#   metadata only: the prompter runs useResponseConversation=false, so no
#   server-side Responses conversation is bound.
SECONDARY_TAGS = {
    "insight-card-prompter": "  (end-of-conversation card, own conversationId)",
    "quick-processing-done-prompter": "  (quick-processing done screen, conversation's own conversationId)",
    "guardrails-handler-prompter": "  (secondary — in-conversation reply)",
}

# Side-effect OpenAI projects triggered by conversations (each `<name> [Production]`).
# These bill separately from the Conversation project but are part of true
# conversational cost-to-serve. Matched by name in fetch_llm_costs.
SIDE_EFFECT_PROJECTS = ["Memory", "Summary", "Profile", "Journal", "Notification", "Feed", "Insights"]

WINDOWS = ("last-7d", "last-30d", "month-to-date", "previous-month")

# ElevenLabs deletes conversation detail (transcript, audio, and the
# `dynamic_variables` we read the Aura conversationId from) after the workspace
# retention period. Voice attribution is therefore only reliable for windows
# inside it — older conversations' detail 404s and the voice match silently
# undercounts. Configured retention is 10 days (Tools/ElevenLabs; was 5 → 10 on
# 2026-05-14); treat ~the last week as the reliable voice window. Override with
# ELEVENLABS_RETENTION_DAYS if the workspace setting changes.
ELEVENLABS_RETENTION_DAYS = int(os.environ.get("ELEVENLABS_RETENTION_DAYS", "10"))


# ---------------------------------------------------------------------------
# Window resolution (UTC; OpenAI + ElevenLabs share the same unix range)
# ---------------------------------------------------------------------------

def resolve_window(window, since, until):
    now = datetime.now(tz=timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if since or until:
        if not (since and until):
            sys.exit("--since and --until must be provided together (YYYY-MM-DD, UTC)")
        s = _parse(since)
        e = _parse(until)  # exclusive
        label = f"{since} → {until}"
    elif window == "last-30d":
        e, s, label = now, now - timedelta(days=30), "last 30 days"
    elif window == "month-to-date":
        s = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        e, label = now, f"month to date ({s.strftime('%B %Y')})"
    elif window == "previous-month":
        first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev = first - timedelta(days=1)
        s = prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        e, label = first, f"previous month ({s.strftime('%B %Y')})"
    else:  # last-7d (default)
        e, s, label = now, now - timedelta(days=7), "last 7 days"
    if s >= e:
        sys.exit(f"Empty window: {s.isoformat()} >= {e.isoformat()}")
    # Clamp the end to now. A window ending in the future is still filling, so
    # the ledger population would grow *during* the run: the row walk and the
    # /stats snapshot would sample different instants and the coverage check
    # would report a phantom shortfall on every live window. Pinning the bound
    # makes the population fixed, so the check means what it says. It also
    # removes nothing real — there is no data after now to report on.
    if e > now:
        e = now
        label = f"{label} (through {now:%Y-%m-%d %H:%M} UTC)"
    return int(s.timestamp()), int(e.timestamp()), label


def _parse(d):
    try:
        return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit(f"Bad date {d!r} — expected YYYY-MM-DD (UTC)")


# ---------------------------------------------------------------------------
# OpenAI: walk the Conversation project completions
# ---------------------------------------------------------------------------

def oa_request(path, params, key, project_id=None):
    headers = {"Authorization": f"Bearer {key}"}
    if project_id:
        headers["OpenAI-Project"] = project_id
    for attempt in range(6):
        try:
            r = requests.get(f"{OPENAI_API}{path}", headers=headers, params=params, timeout=60)
        except (requests.ConnectionError, requests.Timeout) as e:
            # Long walks (tens of thousands of completions) will hit transient
            # drops — back off and retry rather than crash the whole run.
            time.sleep(min(2 ** attempt, 30))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(float(r.headers.get("Retry-After", 2 ** attempt)), 30))
            continue
        if not r.ok:
            sys.exit(f"OpenAI {r.status_code} on {path}: {r.text[:300]}")
        return r.json()
    sys.exit(f"OpenAI {path}: failed after 6 retries (rate-limit / 5xx / network)")


def _fetch_project_names(key):
    """{project_id: name} for all org projects (incl. archived)."""
    names = {}
    after = None
    while True:
        params = {"limit": 100, "include_archived": "true"}
        if after:
            params["after"] = after
        data = oa_request("/organization/projects", params, key)
        rows = data.get("data", [])
        for p in rows:
            names[p["id"]] = p.get("name") or p["id"]
        if not data.get("has_more") or not rows:
            break
        after = rows[-1]["id"]
    return names


def fetch_llm_costs(start, end, key):
    """OpenAI $ for the window, split into the Conversation project and the
    conversation-triggered side-effect pipelines.

    Authoritative source (OpenAI Costs API, group_by project_id). The
    Conversation [Production] project is the LLM cost of the conversations
    themselves (coach / quick-processing / insight-card / guardrails / companion
    / chat). The side-effect pipelines — Memory, Summary, Profile, Journal,
    Notification, Feed, Insights — each bill to their OWN [Production] project
    and are triggered BY conversations, so they're part of true conversational
    cost-to-serve. Returns both so the report can show a clean "direct" number
    and a "full cost-to-serve" number.

    Returns {"conversation": float, "side_effects": {name: $}, "side_effects_total": float}.
    """
    proj = defaultdict(float)
    page = None
    while True:
        params = {"start_time": start, "end_time": end, "bucket_width": "1d",
                  "group_by": ["project_id"], "limit": 180}
        if page:
            params["page"] = page
        data = oa_request("/organization/costs", params, key)
        for bucket in data.get("data", []):
            for res in bucket.get("results", []):
                pid = res.get("project_id") or "(none)"
                proj[pid] += float((res.get("amount") or {}).get("value", 0.0) or 0.0)
        page = data.get("next_page")
        if not page:
            break
    names = _fetch_project_names(key)
    conversation = proj.get(CONV_PROJECT, 0.0)
    side = {}
    for pid, amt in proj.items():
        nm = names.get(pid, pid)
        for base in SIDE_EFFECT_PROJECTS:
            if nm == f"{base} [Production]":   # [Production] only — skip [Development]
                side[base] = side.get(base, 0.0) + amt
    return {"conversation": conversation, "side_effects": side,
            "side_effects_total": sum(side.values())}


def aggregate_ledger(rows):
    """Fold ledger rows into the per-prompter aggregates the report renders.

    Returns (users, convs, calls, span, turns, api_calls) where `span` is
    [min_created, max_created] per coach conversationId and `api_calls` is
    {prompter: {openaiAPI: n}} for the per-surface breakdown.

    **Dedup is per-metric, and getting it backwards is silent in both
    directions.** One thread can span both surfaces — the `aiResponseAPI` A/B
    served its treatment arm on Responses while the control stayed on Chat
    Completions — and the two carry different object ids under one
    `conversationId`. So:

    * users / conversations are **sets**, keyed on userId / conversationId. A
      thread that used both surfaces collapses to one, as it must.
    * calls, turns and duration spans count **every row**. Each row is a real
      call; collapsing them across surfaces would undercount turns and shorten
      `max(created) − min(created)`.

    Both totals stay plausible either way, which is why this is spelled out
    rather than left to the shape of the code.
    """
    users = defaultdict(set)
    convs = defaultdict(set)
    calls = defaultdict(int)
    api_calls = defaultdict(lambda: defaultdict(int))
    span = {}
    turns = defaultdict(int)

    for row in rows:
        name = row.get("prompterName")
        if not name:
            continue
        created = row_unix(row)
        calls[name] += 1
        api_calls[name][row.get("openaiAPI") or "unknown"] += 1
        uid = row.get("userId")
        if uid:
            users[name].add(uid)
        raw_cid = row.get("conversationId")
        if not raw_cid:
            continue
        # Normalise ONCE, here, so every downstream consumer shares one key
        # space. Aurie's voice call sites write the UUID upper-case while others
        # write it lower-case, so the same thread arrives under both casings.
        #
        # Folding it in some places and not others is worse than not folding at
        # all: §1's per-prompter `convs` counted raw casings while §2's `span`
        # folded them, so the same report disagreed with itself about coach
        # volume (4,266 vs 4,008) with nothing to indicate which was right.
        # The voice set is normalised the same way where it is built.
        cid = raw_cid.upper()
        convs[name].add(cid)
        if name != THREAD_TYPE or not created:
            continue
        s = span.get(cid)
        if s is None:
            span[cid] = [created, created]
        else:
            if created < s[0]:
                s[0] = created
            if created > s[1]:
                s[1] = created
        turns[cid] += 1
    return users, convs, calls, span, turns, api_calls


def report_types(calls):
    """Ordered prompter rows for the per-type table.

    Known types first (stable ordering, and a known-but-idle type still shows as
    a zero rather than vanishing), then **any other prompter the ledger
    returned**. That tail is the point: journal was invisible for weeks because
    the type list was hardcoded and nothing reported what it had skipped. A new
    prompter now appears on its own.
    """
    extra = sorted(set(calls) - set(KNOWN_TYPES))
    return KNOWN_TYPES + extra


# ---------------------------------------------------------------------------
# ElevenLabs: voice conversationId threads (optional)
# ---------------------------------------------------------------------------

def el_request(path, params, key, ok_404=False):
    headers = {"xi-api-key": key}
    for attempt in range(6):
        try:
            r = requests.get(f"{ELEVEN_API}{path}", headers=headers, params=params, timeout=60)
        except (requests.ConnectionError, requests.Timeout) as e:
            time.sleep(min(2 ** attempt, 30))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(float(r.headers.get("Retry-After", 2 ** attempt)), 30))
            continue
        if r.status_code == 404 and ok_404:
            return None
        if not r.ok:
            sys.exit(f"ElevenLabs {r.status_code} on {path}: {r.text[:200]}")
        return r.json()
    sys.exit(f"ElevenLabs {path}: failed after 6 retries (rate-limit / 5xx / network)")


def voice_conversation_ids(start, end, key):
    """Walk ElevenLabs conversations for the window.

    Returns a dict:
      aura_cids   — set of Aura conversationIds that had a voice session
      listed      — ElevenLabs sessions seen in window
      voice_users — distinct voice user ids
      minutes     — total voice minutes (in-plan + burst)
      cost        — voice $ (in-plan min × $0.09 + burst min × $0.19, API actuals)
      burst_min   — burst minutes (flagged separately; usually 0)
    """
    aura_cids = set()
    voice_users = set()
    listed = 0
    in_plan_secs = 0.0
    burst_secs = 0.0
    cursor = None
    while True:
        params = {"page_size": 100, "call_start_after_unix": start, "call_start_before_unix": end}
        if cursor:
            params["cursor"] = cursor
        page = el_request("/convai/conversations", params, key) or {}
        items = page.get("conversations") or []
        for c in items:
            ts = c.get("start_time_unix_secs") or 0
            if not (start <= ts <= end):
                continue
            listed += 1
            detail = el_request(f"/convai/conversations/{c['conversation_id']}", {}, key, ok_404=True)
            if not detail:
                continue
            md = detail.get("metadata") or {}
            secs = md.get("call_duration_secs") or c.get("call_duration_secs") or 0
            is_burst = bool(md.get("is_burst") or c.get("is_burst"))
            if is_burst:
                burst_secs += secs
            else:
                in_plan_secs += secs
            dyn = (detail.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {}
            aura = dyn.get("conversationId")
            uid = detail.get("user_id") or dyn.get("userId")
            if aura:
                # Upper-cased to match the conversation-thread keys, which are
                # normalised the same way. Aurie writes the UUID upper-case on
                # the voice paths and lower-case elsewhere, so an exact match
                # drops every lower-case thread from the voice set — silently,
                # as a lower voice %.
                aura_cids.add(aura.upper())
            if uid:
                voice_users.add(uid)
            if listed % 200 == 0:
                sys.stderr.write(f"\r  …{listed} voice conversations fetched")
                sys.stderr.flush()
        if not page.get("has_more") or not page.get("next_cursor"):
            break
        cursor = page["next_cursor"]
    sys.stderr.write("\n")
    cost = (in_plan_secs / 60.0) * ELEVEN_IN_PLAN_PER_MIN + (burst_secs / 60.0) * ELEVEN_BURST_PER_MIN
    return {
        "aura_cids": aura_cids, "listed": listed, "voice_users": voice_users,
        "minutes": (in_plan_secs + burst_secs) / 60.0, "cost": cost,
        "burst_min": burst_secs / 60.0,
    }


# ---------------------------------------------------------------------------
# Stats + render
# ---------------------------------------------------------------------------

def pct(arr, p):
    if not arr:
        return 0.0
    s = sorted(arr)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return float(s[lo]) if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def dist(vals):
    if not vals:
        return None
    return {
        "n": len(vals), "mean": sum(vals) / len(vals),
        "p25": pct(vals, 25), "p50": pct(vals, 50), "p75": pct(vals, 75),
        "p95": pct(vals, 95), "max": max(vals),
    }


def _short(uid):
    return f"{uid[:8]}…" if uid and len(uid) > 8 else (uid or "?")


def render(label, users, convs, calls, span, turns, voice_cids, voice_listed, voice_users, no_voice,
           voice_retention_warning=None, llm_cost=None, voice_cost=None, voice_minutes=None,
           voice_burst_min=None, days=None, side_effects=None, side_effects_total=None,
           api_calls=None, cov=None, coverage_note=None):
    out = []
    out.append("=" * 64)
    out.append(f"AURIE CONVERSATION REPORT — {label}")
    out.append("=" * 64)

    # 0. surface coverage — first, because every number below is scoped by it.
    if cov:
        out.append("\n## 0. Surface coverage (llmCall ledger vs its own /stats)")
        for api in sorted(cov.get("by_api", {})):
            b = cov["by_api"][api]
            share = b.get("share")
            pretty = f"{share*100:.1f}%" if share is not None else "n/a"
            out.append(f"   {api:<18}{b['walked']:>9,} of {b['expected']:>9,} calls   {pretty}")
        total_share = cov.get("share")
        out.append(f"   {'TOTAL':<18}{cov.get('walked',0):>9,} of {cov.get('expected',0):>9,} calls   "
                   f"{f'{total_share*100:.1f}%' if total_share is not None else 'n/a'}")
        if not cov.get("complete"):
            missing = cov.get("expected", 0) - cov.get("walked", 0)
            # State the deficit in calls, not just the share: a 0.01% shortfall
            # rounds to "100.0%" and would otherwise read as a flag contradicting
            # its own number.
            out.append(f"   ⚠️  INCOMPLETE — {missing:,} call(s) short of the ledger's own "
                       "total. Counts below are a floor, not the answer.")
    if coverage_note:
        out.append(f"   ⚠️  {coverage_note}")

    # 1. active users by type
    out.append("\n## 1. Active conversation users by type")
    out.append(f"{'type':<26}{'users':>8}{'conversations':>15}{'calls':>9}")
    api_calls = api_calls or {}
    for n in report_types(calls):
        if n in CONVERSATION_TYPES:
            tag = ""
        elif n in JOURNAL_TYPES:
            tag = "  (own project, single-shot — not a conversation)"
        elif n in SECONDARY_TYPES:
            tag = SECONDARY_TAGS.get(n, "  (secondary)")
        else:
            tag = "  (discovered — not a known type)"
        out.append(f"{n.replace('-prompter',''):<26}{len(users.get(n,())):>8}"
                   f"{len(convs.get(n,())):>15}{calls.get(n,0):>9}{tag}")
        # Per-surface split, so a type that is entirely on one API is visible
        # rather than inferred. This is the line whose absence let the report
        # print `insight-card 0` while 1,482 Responses calls existed.
        by_api = api_calls.get(n) or {}
        if by_api:
            out.append(f"{'':<26}{'':>8}{'':>15}{'':>9}    "
                       + " · ".join(f"{a}: {c:,}" for a, c in sorted(by_api.items())))

    total_convs = len(span)
    out.append(f"\n## 2. Conversations in period: {total_convs:,}  (distinct coach conversationId)")

    # durations
    dur = {c: (span[c][1] - span[c][0]) / 60.0 for c in span}  # minutes
    all_cids = set(span)

    # 3 + voice
    if no_voice or voice_cids is None:
        out.append("\n## 3. Voice conversations: (skipped — no ElevenLabs key; pass a key or drop --no-voice)")
        voice_set = set()
    else:
        voice_set = all_cids & voice_cids
        vpct = 100 * len(voice_set) / total_convs if total_convs else 0
        out.append(f"\n## 3. Voice conversations: {len(voice_set):,} = {vpct:.0f}% of conversations "
                   f"({100-vpct:.0f}% text-only)")
        # The denominator is *observed* conversations, so it moves with coverage
        # — and it is coach threads ONLY, so the text-only surfaces (chat,
        # journal, insight-card) never enter it. That makes the direction of a
        # coverage change genuinely unintuitive here: closing a gap that looked
        # purely textual moved this share UP, 43% -> 45%, because what was
        # missing was coach's own traffic, voice and text alike. Re-run and
        # compare rather than reasoning about which way it should go.
        out.append("   _Share of OBSERVED conversations. The denominator moves with surface "
                   "coverage (§0); it is not a share of all Aurie usage._")
        if voice_retention_warning:
            out.append(f"   ⚠️  {voice_retention_warning}")
        out.append(f"   distinct voice users: {len(voice_users):,}   "
                   f"(ElevenLabs sessions listed: {voice_listed:,}; "
                   f"{voice_listed/len(voice_set):.1f} calls/voice-conversation)" if voice_set else
                   f"   distinct voice users: {len(voice_users):,}")

    def block(title, cids):
        d = dist([dur[c] for c in cids])
        t = dist([turns[c] for c in cids])
        if not d:
            out.append(f"\n{title}: (none)")
            return
        out.append(f"\n{title} (n={d['n']:,})")
        out.append(f"   duration min: mean {d['mean']:.1f}  p25 {d['p25']:.1f}  p50 {d['p50']:.1f}  "
                   f"p75 {d['p75']:.1f}  p95 {d['p95']:.1f}  max {d['max']:.0f}")
        out.append(f"   turns:        mean {t['mean']:.1f}  p25 {t['p25']:.0f}  p50 {t['p50']:.0f}  "
                   f"p95 {t['p95']:.0f}  max {t['max']:.0f}")

    out.append("\n## 4. Conversation duration — ALL")
    block("ALL conversations", all_cids)

    if voice_set:
        out.append("\n## 5. Voice vs text-only (same conversationId basis)")
        block("VOICE conversations", voice_set)
        block("TEXT-only conversations", all_cids - voice_set)

    # 6. cost
    if llm_cost is not None:
        out.append("\n## 6. Cost (window)")
        v = voice_cost or 0.0
        se = side_effects_total or 0.0
        direct = llm_cost + v
        full = direct + se
        out.append(f"   Conversation LLM (OpenAI):   ${llm_cost:,.2f}")
        if voice_cost is not None:
            burst_note = f" (incl. {voice_burst_min:.0f} burst min)" if voice_burst_min else ""
            out.append(f"   Voice (ElevenLabs):          ${v:,.2f}   "
                       f"[{voice_minutes:,.0f} min{burst_note}]")
        else:
            out.append("   Voice (ElevenLabs):          (skipped — no key / --no-voice)")
        out.append(f"   Direct conversation cost:    ${direct:,.2f}")
        if side_effects:
            bd = " · ".join(f"{k} ${vv:,.0f}" for k, vv in sorted(side_effects.items(), key=lambda x: -x[1]))
            out.append(f"   Side-effects (triggered):    ${se:,.2f}   [{bd}]")
            out.append(f"   FULL cost-to-serve:          ${full:,.2f}")
        denom = full if side_effects else direct
        if days:
            out.append(f"   per day:                     ${denom / days:,.2f}")
        if total_convs:
            out.append(f"   per conversation:            ${denom / total_convs:,.3f}")
        total_turns = sum(turns.values())
        if total_turns:
            out.append(f"   per turn:                    ${denom / total_turns:,.4f}")
        if voice_set:
            out.append(f"   per voice conversation:      ${v / len(voice_set):,.3f}  (voice $ only)")
        if side_effects:
            out.append("   _per day / per conversation / per turn use FULL cost-to-serve. Side-effects:")
            out.append("    Memory / Summary / Profile / Insights scale ~per conversation; Journal /")
            out.append("    Notification / Feed are activity-gated crons, folded in as a lump (approximate")
            out.append("    per-conversation)._")
        else:
            out.append("   _Side-effect pipelines (memory/summary/profile/journal/notification/feed/insights) not")
            out.append("    fetched. See eng:analyze-openai-costs for full org cost-to-serve._")

    return "\n".join(out)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", choices=WINDOWS, default="last-7d")
    ap.add_argument("--since", help="UTC start YYYY-MM-DD (pairs with --until)")
    ap.add_argument("--until", help="UTC end YYYY-MM-DD, exclusive (pairs with --since)")
    ap.add_argument("--no-voice", action="store_true", help="Skip the ElevenLabs voice walk")
    ap.add_argument("--json", dest="json_out", help="Write structured JSON to this path")
    ap.add_argument(
        "--allow-partial", action="store_true",
        help="Proceed even when the window starts before the ledger's recorded "
             "history. The uncovered days contribute nothing, so counts are a "
             "floor — only for deliberately partial reads.",
    )
    args = ap.parse_args()

    oa_key = os.environ.get("OPENAI_ADMIN_API_KEY")
    if not oa_key:
        sys.exit("OPENAI_ADMIN_API_KEY is not set (sk-admin-…). See CLAUDE.md → token inventory.")
    el_key = os.environ.get("ELEVENLABS_ADMIN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")

    start, end, label = resolve_window(args.window, args.since, args.until)
    sys.stderr.write(f"window: {label} [{start},{end})\n")

    days = max(1.0, (end - start) / 86400.0)

    sys.stderr.write("Fetching LLM cost — Conversation + side-effect projects (OpenAI Costs API)…\n")
    costs = fetch_llm_costs(start, end, oa_key)
    llm_cost = costs["conversation"]
    side_effects = costs["side_effects"]
    side_effects_total = costs["side_effects_total"]

    # Activity: one ledger walk per reported project, covering BOTH OpenAI APIs.
    # Fail closed on an unreachable ledger — a caught exception here would hand
    # back zero conversations, which is indistinguishable from a quiet week.
    coverage_note = None
    try:
        first = coverage_start(start, end)
        if first is None:
            sys.exit(
                f"The llmCall ledger has no rows at all for {label}. Either the "
                "window predates the ledger (it began recording 2026-07-23) or "
                "the ledger is down. Refusing to report zero conversations as a "
                "finding."
            )
        if first > start:
            gap = (first - start) / 86400.0
            coverage_note = (
                f"Window starts {gap:.1f} day(s) before the ledger's recorded "
                f"history (first rows {datetime.fromtimestamp(first, tz=timezone.utc):%Y-%m-%d}). "
                "Those days contribute nothing; every count is a FLOOR."
            )
            if not args.allow_partial:
                sys.exit(
                    f"{coverage_note}\n\nRefusing to report a partial window as a "
                    "result. Re-run with a start on or after "
                    f"{datetime.fromtimestamp(first, tz=timezone.utc):%Y-%m-%d}, or pass "
                    "--allow-partial to accept floor counts."
                )
            sys.stderr.write(f"⚠️  {coverage_note}\n")

        rows = []
        stats = {}
        for project in LEDGER_PROJECTS:
            sys.stderr.write(f"Walking llmCall ledger — project={project} (both OpenAI APIs)…\n")
            got = walk_ledger(
                start, end, project=project,
                progress=lambda n, p=project: sys.stderr.write(f"\r  …{n:,} {p} rows"),
            )
            sys.stderr.write("\n")
            rows.extend(got)
            for api, agg in ledger_stats(start, end, project=project).items():
                cur = stats.setdefault(api, {"calls": 0})
                cur["calls"] += int(agg.get("calls") or 0)
    except LedgerError as exc:
        sys.exit(
            f"Could not read the llmCall ledger: {exc}\n\n"
            "The ledger is the only index for Responses-API traffic, which now "
            "carries ~all Aurie conversation volume. Without it this report "
            "cannot produce activity numbers — and chat-completions-only counts "
            "would understate reality by roughly half. Fix the BigQuery read "
            "(GCP_KEY) and re-run."
        )

    users, convs, calls, span, turns, api_calls = aggregate_ledger(rows)
    cov = coverage(rows, stats)
    if not cov["complete"]:
        sys.stderr.write(
            f"⚠️  walked {cov['walked']:,} of {cov['expected']:,} ledger calls "
            f"({(cov['share'] or 0)*100:.1f}%) — counts below are a floor.\n"
        )

    voice_cids, voice_listed, voice_users = None, 0, set()
    voice_cost, voice_minutes, voice_burst_min = None, None, None
    voice_retention_warning = None
    if not args.no_voice:
        if el_key:
            retention_floor = int(time.time()) - ELEVENLABS_RETENTION_DAYS * 86400
            if start < retention_floor:
                voice_retention_warning = (
                    f"Window starts before ElevenLabs' ~{ELEVENLABS_RETENTION_DAYS}-day retention — "
                    "voice attribution is INCOMPLETE (older conversation detail is deleted, so the "
                    "Aura conversationId can't be read). Voice % is undercounted. Voice analysis is "
                    "only reliable for ~the last week."
                )
                sys.stderr.write(f"⚠️  {voice_retention_warning}\n")
            sys.stderr.write("Walking ElevenLabs conversations for voice conversationIds…\n")
            v = voice_conversation_ids(start, end, el_key)
            voice_cids, voice_listed, voice_users = v["aura_cids"], v["listed"], v["voice_users"]
            voice_cost, voice_minutes, voice_burst_min = v["cost"], v["minutes"], v["burst_min"]
        else:
            sys.stderr.write("No ElevenLabs key — skipping voice sections.\n")

    report = render(label, users, convs, calls, span, turns,
                    voice_cids, voice_listed, voice_users, args.no_voice,
                    voice_retention_warning=voice_retention_warning,
                    llm_cost=llm_cost, voice_cost=voice_cost, voice_minutes=voice_minutes,
                    voice_burst_min=voice_burst_min, days=days,
                    side_effects=side_effects, side_effects_total=side_effects_total,
                    api_calls=api_calls, cov=cov, coverage_note=coverage_note)
    print(report)

    if args.json_out:
        dur = {c: (span[c][1] - span[c][0]) / 60.0 for c in span}
        all_cids = set(span)
        voice_set = (all_cids & voice_cids) if voice_cids else set()
        total_turns = sum(turns.values())
        direct_cost = llm_cost + (voice_cost or 0.0)
        full_cost = direct_cost + side_effects_total
        payload = {
            "window": {"label": label, "start": start, "end": end, "days": days},
            # Coverage first and unconditional: a consumer must be able to see
            # how much of the traffic this run actually read before trusting a
            # single count. Reported as shares, never as a "both surfaces" flag.
            "api_breakdown": cov,
            "responses_source": {
                "index": "llmCall ledger (BigQuery llm.llm_call)",
                "projects": list(LEDGER_PROJECTS),
                "apis": sorted(cov.get("by_api", {})),
                "note": (
                    "Activity is indexed from the Aura llmCall ledger, which "
                    "records both the chatCompletions and responses APIs. "
                    "OpenAI's stored-completions list cannot enumerate "
                    "Responses-API traffic, so it is no longer used here."
                ),
                "partial_window": coverage_note,
            },
            "by_type": {n: {"users": len(users.get(n, ())), "conversations": len(convs.get(n, ())),
                            "calls": calls.get(n, 0), "by_api": dict(api_calls.get(n, {}))}
                        for n in report_types(calls)},
            "conversations": len(all_cids),
            "voice_conversations": len(voice_set) if voice_cids is not None else None,
            "voice_users": len(voice_users) if voice_cids is not None else None,
            "duration_all": dist([dur[c] for c in all_cids]),
            "duration_voice": dist([dur[c] for c in voice_set]) if voice_set else None,
            "duration_text": dist([dur[c] for c in (all_cids - voice_set)]) if voice_cids is not None else None,
            "turns_all": dist([turns[c] for c in all_cids]),
            "cost": {
                "conversation_llm": round(llm_cost, 2),
                "voice": round(voice_cost, 2) if voice_cost is not None else None,
                "voice_minutes": round(voice_minutes, 1) if voice_minutes is not None else None,
                "side_effects": {k: round(v, 2) for k, v in side_effects.items()},
                "side_effects_total": round(side_effects_total, 2),
                "direct_conversation_cost": round(direct_cost, 2),
                "full_cost_to_serve": round(full_cost, 2),
                "per_day": round(full_cost / days, 2),
                "per_conversation": round(full_cost / len(all_cids), 4) if all_cids else None,
                "per_turn": round(full_cost / total_turns, 4) if total_turns else None,
                "per_voice_conversation": round((voice_cost or 0.0) / len(voice_set), 4) if voice_set else None,
                "note": ("per_day / per_conversation / per_turn use full_cost_to_serve "
                         "(Conversation + Memory/Summary/Profile/Journal/Notification/Feed/Insights + voice). "
                         "Crons (journal/notification/feed) are activity-gated — per-conversation is approximate."),
            },
        }
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)
        sys.stderr.write(f"JSON written: {args.json_out}\n")


if __name__ == "__main__":
    main()
