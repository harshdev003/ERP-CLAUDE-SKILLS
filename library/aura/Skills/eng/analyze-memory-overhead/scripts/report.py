#!/usr/bin/env python3
"""
Per-conversation overhead of the Aurie memory pipeline.

Migrated off Braintrust 2026-06-05 (PROD-2465, umbrella PROD-2456). Reads
OpenAI stored chat-completions from the `Memory [Production]` and
`Conversation [Production]` projects instead of Braintrust BTQL. Access
pattern: AI Knowledge `Tools/OpenAI` -> "Reading stored completions".

Joins the two projects on the user-facing identifiers stamped into OpenAI
request metadata by IntelligenceService:

  - metadata.conversationId  -- per-conversation rollup (primary join key)
  - metadata.dialogueId      -- per-turn / per-invocation rollup

Conversation is filtered to metadata.name = 'coach-prompter' (server-side) so
sibling prompters (quick-processing-prompter, insight-card-prompter) don't
pollute the turn count. Memory is filtered to metadata.name = 'memory-prompter'.

COUNTING MODEL -- completions, not spans
----------------------------------------
Braintrust modelled a turn/invocation as a `task` span wrapping one-or-more
`llm` child spans. OpenAI stored completions have no span hierarchy -- each
completion IS exactly one LLM call. Equivalent rollup, per conversationId:

  - llm_calls    = number of stored completions
  - turns        = number of DISTINCT metadata.dialogueId values
                   (one user turn = one dialogueId; tool round-trips within a
                   turn reuse the same dialogueId across completions)
  - invocations  = number of DISTINCT metadata.dialogueId values (memory side;
                   each memory firing = one dialogueId, reused across its loop)
  - tool_calls   = llm_calls - turns/invocations (floor 0)

Verified 2026-06-05: multi-tool turns/firings carry >1 completion per
dialogueId, so (completions - distinct dialogueIds) is the tool-call count.

DATA SOURCES -- IS LLM ledger first, stored completions as fallback
-------------------------------------------------------------------
IntelligenceService persists ONE ROW PER OpenAI CALL (both Chat Completions
and Responses APIs) in the llmCall table, read here from
BigQuery llm.llm_call (docs:
https://app.notion.com/p/aurahealth/LLM-Calls-3a675e23af208127b217e53af47524e8).
For any requested window [start, end):

  - slice_before = [start, min(end, LEDGER_EPOCH))  -> ALWAYS the stored
    chat-completions walk (the only source for pre-epoch data).
  - slice_after  = [max(start, LEDGER_EPOCH), end)  -> the LEDGER is PRIMARY
    (server-side prompterName + project filters, 1000 rows/page, no
    retention limit). If the ledger request fails, the script warns on
    stderr and falls back to the stored-completions walk for that slice --
    NOTE the fallback is chat-API-only and cannot see Responses-API traffic.

The two slices are merged and deduped defensively on the OpenAI object id
(ledger `openaiId` == stored completion `id`). Ledger rows carry
conversationId / dialogueId / prompterName as top-level columns, so they
normalise into the same aggregation shape as stored completions.

PERFORMANCE NOTE
----------------
OpenAI's /v1/chat/completions list endpoint has NO server-side aggregation and
NO time filter -- only metadata filters + newest-first pagination at 100/page.
This walks every coach-prompter and memory-prompter completion in the window
and aggregates client-side. Slower than the old BTQL GROUP BY: minutes for MTD,
~10min+ for 30d. Use a narrow window (7d) for headline ratios -- distributions
stabilise fast. (The ledger path used for post-epoch slices is much faster:
server-side filters at 1000 rows/page.)

CROSS-PROJECT ID MISMATCH (read me)
-----------------------------------
The Memory pipeline historically stamped an INTERNAL conversationId/dialogueId
rather than the user-facing IDs, producing near-zero cross-project intersection
(ENGBACK-1034). The same metadata is written to OpenAI, so the same caveat
applies -- the script prints a LOUD WARNING when the paired/total ratio is low.
(2026-06-05 spot-check: some conversationIds now match across projects, so the
backend propagation may have improved; the banner reports the measured rate.)

DO NOT USE metadata.transaction AS A JOIN OR GROUPING KEY (ANYWHERE). It is a
thread/session-level id and can span multiple unrelated conversations. Always
join/group on metadata.conversationId (per-turn by metadata.dialogueId).

Usage:
    python report.py                                              # MTD (default)
    python report.py --days 7
    python report.py --since 2026-04-01T00:00:00Z --until 2026-05-01T00:00:00Z
    python report.py --days 30 --json out.json --csv out.csv
    python report.py --days 7 --top 20
    python report.py --days 7 --per-dialogue per_turn.csv

Env (PROD-3572):
    OPENAI_MEMORY_API_KEY        -- required. Project-scoped service-account key
                                    for the `Memory [Production]` completions walk.
    OPENAI_CONVERSATION_API_KEY  -- required. Project-scoped service-account key
                                    for the `Conversation [Production]` walk.

    OpenAI enforces the `model.request` scope on GET /v1/chat/completions, which
    Admin keys (OPENAI_ADMIN_API_KEY, sk-admin-...) cannot hold by design — so a
    project-scoped key is now required per walked project. Create each at
    https://platform.openai.com/api-keys (Owned by: Service account, scoped to the
    matching project) and set both in ~/.claude/settings.json env.

    GCP_KEY                      -- Service account for the IS LLM-call ledger
                                    (BigQuery llm.llm_call).
                                    Primary source for post-epoch slices. If
                                    absent or the request fails, the script
                                    warns on stderr and falls back to the
                                    stored-completions walk (no crash).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
import pathlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

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

# OpenAI project IDs (see AI Knowledge `Tools/OpenAI` -> project inventory).
MEMORY_PROJECT_ID = "proj_f0PpUSyYnJNifUZj44FrjUhr"         # Memory [Production]
CONVERSATION_PROJECT_ID = "proj_esFyd4eq2VIAjKUn69X1ZFfk"   # Conversation [Production]

# Display names -> used to derive the per-project completions key (PROD-3572).
PROJECT_NAMES = {
    MEMORY_PROJECT_ID: "Memory [Production]",
    CONVERSATION_PROJECT_ID: "Conversation [Production]",
}

CONVERSATION_PROMPTER = "coach-prompter"
MEMORY_PROMPTER = "memory-prompter"

# Below this paired/total ratio the script emits a loud cross-project warning.
LOW_INTERSECTION_WARN_RATIO = 0.50

PAGE_SIZE = 100        # OpenAI list cap (passing >100 silently returns empty)
MAX_PAGES = 100_000    # runaway-walk backstop
MAX_RETRIES = 5

# IS per-call LLM ledger (primary attribution source for post-epoch slices).
LEDGER_EPOCH_UNIX = 1784777100  # 2026-07-23T03:25:00Z — the ledger went live in Release 0.285.0. There are NO ledger rows before this moment.
LEDGER_EPOCH_LABEL = "2026-07-23T03:25Z"


class LedgerUnavailable(Exception):
    """Raised when the IS LLM-call ledger can't be used — callers fall back
    to the stored-chat-completions walk for the affected slice."""


def _unix_to_iso(unix: int) -> str:
    return (
        datetime.fromtimestamp(unix, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _ledger_project_key(project_name: str) -> str:
    """OpenAI display name -> IS ledger project key.
    'Memory [Production]' -> 'memory' (strip the env suffix, lowercase the
    first letter)."""
    base = re.sub(r"\s*\[.*?\]\s*", "", project_name).strip()
    return base[:1].lower() + base[1:]


def iter_llm_calls(start_unix, end_unix, *, label=None, project_key=None, prompter_name=None, provider=None):
    """Read the IS per-call LLM ledger (BigQuery `llm.llm_call`) for [start, end). Newest-first.

    Raises LedgerUnavailable (BigQuery error, missing GCP_KEY) so callers can fall back to the
    stored-completions walk.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    params = {"from": _unix_to_iso(start_unix), "to": _unix_to_iso(end_unix)}
    if prompter_name:
        params["prompterName"] = prompter_name
    if project_key:
        params["project"] = project_key
    if provider:
        params["provider"] = provider
    try:
        out = llm_ledger.rows(params)
    except llm_ledger.LedgerError as exc:
        raise LedgerUnavailable(str(exc)) from exc
    if label:
        print(f"  [{label}] ledger: {len(out)} rows", file=sys.stderr)
    return out


def _ledger_items(rows):
    """Normalise ledger rows into the stored-completion item shape that
    _aggregate_walk reads. Ledger equivalents of the OpenAI metadata fields
    are top-level columns: prompterName == metadata.name (already filtered
    server-side), conversationId / dialogueId map 1:1, and `openaiId` is the
    OpenAI object id ('chatcmpl-...' or 'resp_...') used for dedupe."""
    return [
        {
            "id": row.get("openaiId"),
            "metadata": {
                "conversationId": row.get("conversationId"),
                "dialogueId": row.get("dialogueId"),
            },
        }
        for row in rows
    ]


def _slim_completions(items):
    """Strip walked completion objects down to the fields the aggregation
    reads (id + metadata) so a full window can be buffered for the boundary
    dedupe without holding every message body in memory."""
    return (
        {"id": it.get("id"), "metadata": it.get("metadata") or {}}
        for it in items
    )


def _dedupe_on_id(items):
    """Defensive dedupe on the OpenAI object id (ledger openaiId == stored
    completion id) in case of boundary overlap between slices."""
    seen = set()
    out = []
    for it in items:
        oid = it.get("id")
        if oid:
            if oid in seen:
                continue
            seen.add(oid)
        out.append(it)
    return out


def _fetch_attribution(api_key, project_id, prompter, since_unix, until_unix, label):
    """Fetch per-call rows for one prompter, ledger-first with failover.

    [since, min(until, epoch)) -> stored chat completions (only pre-epoch source).
    [max(since, epoch), until) -> IS LLM ledger (primary); on LedgerUnavailable,
    warn and fall back to the stored-completions walk for that slice.

    Returns (items, source_label) — items deduped on OpenAI object id.
    """
    end_unix = until_unix if until_unix is not None else int(time.time())
    project_name = PROJECT_NAMES[project_id]
    items = []
    pre_source = None
    post_source = None

    pre_end = min(end_unix, LEDGER_EPOCH_UNIX)
    if since_unix < pre_end:
        items.extend(_slim_completions(
            _walk(api_key, project_id, prompter, since_unix, pre_end, label)
        ))
        pre_source = "stored chat completions"

    post_start = max(since_unix, LEDGER_EPOCH_UNIX)
    if post_start < end_unix:
        try:
            rows = iter_llm_calls(
                post_start, end_unix,
                label=label,
                project_key=_ledger_project_key(project_name),
                prompter_name=prompter,
                provider="openai",
            )
            items.extend(_ledger_items(rows))
            post_source = "LLM ledger (BigQuery llm.llm_call)"
        except LedgerUnavailable as exc:
            _fail_closed_on_ledger_outage(exc)
            slice_label = f"[{_unix_to_iso(post_start)}, {_unix_to_iso(end_unix)})"
            print(
                f"WARNING: [{label}] ledger unavailable ({exc}): falling back to "
                f"stored chat completions for {slice_label}. Note: the "
                f"completions fallback is chat-API-only and cannot see "
                f"Responses-API traffic.",
                file=sys.stderr,
            )
            items.extend(_slim_completions(
                _walk(api_key, project_id, prompter, post_start, end_unix, label)
            ))
            post_source = (
                "stored chat completions (ledger unavailable fallback — "
                "chat-API-only, cannot see Responses-API traffic)"
            )

    if pre_source and post_source:
        source = (
            f"mixed (window straddles ledger epoch {LEDGER_EPOCH_LABEL}; "
            f"pre-epoch: {pre_source}, post-epoch: {post_source})"
        )
    elif post_source:
        source = post_source
    elif pre_source:
        source = (
            f"{pre_source} (window entirely before ledger epoch "
            f"{LEDGER_EPOCH_LABEL})"
        )
    else:
        source = "empty window"

    return _dedupe_on_id(items), source


def _iso_to_unix(iso: str | None) -> int | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _project_completions_api_key_env(project_name: str) -> str:
    """PROD-3572: "Memory [Production]" -> "OPENAI_MEMORY_API_KEY"."""
    base = re.sub(r"\[.*?\]", "", project_name).strip()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").upper()
    return f"OPENAI_{base}_API_KEY"


def _resolve_completions_api_key(project_name: str) -> str:
    """Resolve the project-scoped key for a stored-completions walk (PROD-3572).
    This skill hard-fails on a missing key (it can't produce a meaningful memory-
    overhead number without both projects), so raise SystemExit with a clear
    message. OPENAI_ADMIN_API_KEY no longer works for /v1/chat/completions."""
    env_name = _project_completions_api_key_env(project_name)
    token = os.environ.get(env_name)
    if not token:
        sys.exit(
            f"{env_name} is not set. The stored-completions walk for "
            f"'{project_name}' requires a project-scoped service-account key — "
            f"OPENAI_ADMIN_API_KEY no longer works for /v1/chat/completions "
            f"(PROD-3572). Create one at https://platform.openai.com/api-keys "
            f"(Owned by: Service account, Project: {project_name}) and set "
            f"{env_name} in ~/.claude/settings.json."
        )
    return token


def _walk(api_key, project_id, prompter, since_unix, until_unix, label):
    """Yield stored completions for one prompter in [since, until), newest-first.

    Server-side filter on metadata[name]; client-side time bound (the list
    endpoint has no created_after/before). Stops once it pages past `since`.
    """
    after = None
    walked = kept = page = 0
    while page < MAX_PAGES:
        params = {"metadata[name]": prompter, "limit": PAGE_SIZE}
        if after:
            params["after"] = after
        for attempt in range(MAX_RETRIES):
            try:
                r = requests.get(
                    f"{OPENAI_API}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}",
                             "OpenAI-Project": project_id},
                    params=params, timeout=60,
                )
            except requests.RequestException:
                time.sleep(2)
                continue
            if r.status_code == 429:
                time.sleep(min(float(r.headers.get("Retry-After", "2")) + 1, 30))
                continue
            if r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            if not r.ok:
                sys.exit(f"OpenAI {r.status_code} on /chat/completions: {r.text[:500]}")
            break
        else:
            sys.exit("OpenAI: exhausted retries on /chat/completions")
        data = r.json()
        items = data.get("data", [])
        if not items:
            break
        page += 1
        stop = False
        for it in items:
            walked += 1
            created = it.get("created", 0) or 0
            if until_unix is not None and created >= until_unix:
                continue
            if created < since_unix:
                stop = True
                continue
            kept += 1
            yield it
        print(f"  [{label}] page {page}: walked {walked}, kept {kept}", file=sys.stderr)
        if stop or not data.get("has_more"):
            break
        after = data.get("last_id") or items[-1].get("id")
        if not after:
            break


def _aggregate_walk(items, cid_keys, count_label):
    """Aggregate a stream of completions into per-cid and per-did rows.

    Returns (per_cid_rows, per_did_rows) in the shapes the downstream roll_up /
    write_per_dialogue_csv expect:
      per_cid: { cid, llm_calls, <count_label> }
      per_did: { did, cid, llm_calls, <count_label> }
    """
    cid_completions = defaultdict(int)
    cid_dids = defaultdict(set)
    did_completions = defaultdict(int)
    did_cid = {}
    for it in items:
        m = it.get("metadata") or {}
        cid = next((m[k] for k in cid_keys if m.get(k)), None)
        did = m.get("dialogueId")
        if cid:
            cid_completions[cid] += 1
            if did:
                cid_dids[cid].add(did)
        if did:
            did_completions[did] += 1
            if cid and did not in did_cid:
                did_cid[did] = cid
    per_cid = [
        {"cid": c, "llm_calls": cid_completions[c], count_label: len(cid_dids[c])}
        for c in cid_completions
    ]
    per_did = [
        {"did": d, "cid": did_cid.get(d), "llm_calls": did_completions[d], count_label: 1}
        for d in did_completions
    ]
    return per_cid, per_did


def fetch_conversation(api_key, since_unix, until_unix):
    """Return (per_cid, per_did, source) for coach-prompter, ledger-first."""
    items, source = _fetch_attribution(
        api_key, CONVERSATION_PROJECT_ID, CONVERSATION_PROMPTER,
        since_unix, until_unix, "Conversation",
    )
    per_cid, per_did = _aggregate_walk(
        items, ("conversationId", "conversation_id"), "turns"
    )
    return per_cid, per_did, source


def fetch_memory(api_key, since_unix, until_unix):
    """Return (per_cid, per_did, source) for memory-prompter, ledger-first."""
    items, source = _fetch_attribution(
        api_key, MEMORY_PROJECT_ID, MEMORY_PROMPTER,
        since_unix, until_unix, "Memory",
    )
    per_cid, per_did = _aggregate_walk(items, ("conversationId",), "invocations")
    return per_cid, per_did, source


def roll_up(
    conv_rows: list[dict[str, Any]], mem_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Outer-join conversation and memory rows by `cid`.

    Returns (per_cid_rows, orphan_stats). Memory rows whose `cid` doesn't
    appear in the Conversation set are bucketed into `orphans` — this is
    the cross-project mismatch canary.
    """
    per_cid: dict[str, dict[str, Any]] = {}

    for r in conv_rows:
        cid = r.get("cid")
        if not cid:
            continue
        bucket = per_cid.setdefault(cid, _empty_bucket(cid))
        bucket["conversation_llm_calls"] += int(r.get("llm_calls") or 0)
        bucket["conversation_turns"] += int(r.get("turns") or 0)

    orphan_cids = 0
    orphan_llm = 0
    orphan_inv = 0
    for r in mem_rows:
        cid = r.get("cid")
        if not cid:
            continue
        llm = int(r.get("llm_calls") or 0)
        inv = int(r.get("invocations") or 0)
        if cid not in per_cid:
            # Memory cid not seen on the Conversation side. Either the
            # cross-project IDs don't propagate (the bug this report
            # surfaces) or the originating conversation fell outside the
            # window.
            orphan_cids += 1
            orphan_llm += llm
            orphan_inv += inv
        bucket = per_cid.setdefault(cid, _empty_bucket(cid))
        bucket["memory_llm_calls"] += llm
        bucket["memory_invocations"] += inv

    rows: list[dict[str, Any]] = []
    for bucket in per_cid.values():
        bucket["conversation_tool_calls"] = max(
            bucket["conversation_llm_calls"] - bucket["conversation_turns"], 0
        )
        bucket["memory_tool_calls"] = max(
            bucket["memory_llm_calls"] - bucket["memory_invocations"], 0
        )
        cl = bucket["conversation_llm_calls"]
        ct = bucket["conversation_turns"]
        c_tool = bucket["conversation_tool_calls"]
        bucket["memory_llm_per_conv_llm"] = (
            bucket["memory_llm_calls"] / cl if cl else None
        )
        bucket["memory_tool_per_conv_tool"] = (
            bucket["memory_tool_calls"] / c_tool if c_tool else None
        )
        bucket["memory_per_5_turns"] = (
            5 * bucket["memory_invocations"] / ct if ct else None
        )
        rows.append(bucket)

    return rows, {
        "orphan_memory_cids": orphan_cids,
        "orphan_memory_llm_calls": orphan_llm,
        "orphan_memory_invocations": orphan_inv,
    }


def _empty_bucket(cid: str) -> dict[str, Any]:
    return {
        "conversation_id": cid,
        "conversation_turns": 0,
        "conversation_llm_calls": 0,
        "memory_invocations": 0,
        "memory_llm_calls": 0,
    }


def aggregate(rows: list[dict[str, Any]], orphans: dict[str, int]) -> dict[str, Any]:
    paired = [
        r for r in rows
        if r["conversation_llm_calls"] > 0 and r["memory_llm_calls"] > 0
    ]
    only_memory = sum(1 for r in rows if r["conversation_llm_calls"] == 0)
    only_conv = sum(1 for r in rows if r["memory_llm_calls"] == 0)

    def pct(xs: list[float], p: float) -> float | None:
        if not xs:
            return None
        xs = sorted(xs)
        k = max(0, min(len(xs) - 1, int(round((len(xs) - 1) * p))))
        return xs[k]

    def dist(xs: list[float]) -> dict[str, Any]:
        if not xs:
            return {"n": 0, "min": None, "mean": None, "median": None,
                    "p90": None, "p99": None, "max": None, "stdev": None}
        return {
            "n": len(xs),
            "min": min(xs),
            "mean": statistics.mean(xs),
            "median": statistics.median(xs),
            "p90": pct(xs, 0.90),
            "p99": pct(xs, 0.99),
            "max": max(xs),
            "stdev": statistics.stdev(xs) if len(xs) >= 2 else None,
        }

    ratios_llm = [r["memory_llm_per_conv_llm"] for r in paired if r["memory_llm_per_conv_llm"] is not None]
    ratios_tool = [r["memory_tool_per_conv_tool"] for r in paired if r["memory_tool_per_conv_tool"] is not None]
    per_5 = [r["memory_per_5_turns"] for r in paired if r["memory_per_5_turns"] is not None]

    totals = {
        "memory_llm_calls": sum(r["memory_llm_calls"] for r in rows),
        "memory_invocations": sum(r["memory_invocations"] for r in rows),
        "memory_tool_calls": sum(r["memory_tool_calls"] for r in rows),
        "conversation_llm_calls": sum(r["conversation_llm_calls"] for r in rows),
        "conversation_turns": sum(r["conversation_turns"] for r in rows),
        "conversation_tool_calls": sum(r["conversation_tool_calls"] for r in rows),
    }

    def safe_div(a: float, b: float) -> float | None:
        return a / b if b else None

    per_unit = {
        "memory_llm_per_invocation": safe_div(totals["memory_llm_calls"], totals["memory_invocations"]),
        "memory_tool_per_invocation": safe_div(totals["memory_tool_calls"], totals["memory_invocations"]),
        "conversation_llm_per_turn": safe_div(totals["conversation_llm_calls"], totals["conversation_turns"]),
        "conversation_tool_per_turn": safe_div(totals["conversation_tool_calls"], totals["conversation_turns"]),
    }

    return {
        "conversations_total": len(rows),
        "conversations_with_both_sides": len(paired),
        "conversations_memory_only": only_memory,
        "conversations_conversation_only": only_conv,
        "orphans": orphans,
        "totals": totals,
        "per_unit": per_unit,
        "ratio_memory_llm_per_conv_llm": dist(ratios_llm),
        "ratio_memory_tool_per_conv_tool": dist(ratios_tool),
        "memory_per_5_turns": dist(per_5),
    }


def print_cross_project_warning(agg: dict[str, Any]) -> None:
    """Print a loud banner when the cross-project conversationId join
    produces near-zero intersection — the canary for the backend ID
    propagation gap this skill is designed to surface."""
    total = agg["conversations_total"]
    paired = agg["conversations_with_both_sides"]
    ratio = (paired / total) if total else 0.0
    if total == 0 or ratio >= LOW_INTERSECTION_WARN_RATIO:
        return
    line = "!" * 80
    print()
    print(line)
    print("WARNING: cross-project conversationId join produced low intersection.")
    print(f"  paired conversations / total = {paired:,} / {total:,} = {ratio:.1%}")
    print()
    print("This means the Memory project's `metadata.conversationId` does not match")
    print("the Conversation project's `metadata.conversationId` for most conversations.")
    print("The memory-prompter stamps an INTERNAL conversationId from its own agentic")
    print("loop. Until the Aura backend propagates the user-facing conversationId into")
    print("the memory pipeline, the per-conversation ratios below are unreliable —")
    print("most memory invocations show up as orphans rather than attributed to a chat.")
    print()
    print("Track the backend propagation fix on Linear (ENGBACK-1034). Do NOT fall")
    print("back to `metadata.transaction` as the join key — that field is a thread/")
    print("session id and is shared across multiple unrelated conversations even")
    print("within one project. Grouping by it produces wrong aggregates. Fix the IDs")
    print("at the source.")
    print(line)
    print()


def print_summary(window_label: str, agg: dict[str, Any], sources: dict[str, str]) -> None:
    print(f"\nWindow: {window_label}")
    distinct = set(sources.values())
    if len(distinct) == 1:
        print(f"Attribution source: {next(iter(distinct))}")
    else:
        print("Attribution source:")
        for walk_label, source in sources.items():
            print(f"  {walk_label}: {source}")
    print("-" * 80)
    print(f"Conversations seen:           {agg['conversations_total']:>10,}")
    print(f"  with both memory + conv:    {agg['conversations_with_both_sides']:>10,}")
    print(f"  memory-only (no conv span): {agg['conversations_memory_only']:>10,}")
    print(f"  conv-only (no memory span): {agg['conversations_conversation_only']:>10,}")
    o = agg["orphans"]
    if o["orphan_memory_cids"]:
        print(
            f"  orphan memory cids (no conv match): {o['orphan_memory_cids']:>6,}"
            f"  ({o['orphan_memory_llm_calls']:,} LLM, "
            f"{o['orphan_memory_invocations']:,} invocations)"
        )
    t = agg["totals"]
    print()
    print(f"{'Totals':<28}{'Conversation':>14}{'Memory':>14}{'mem/conv':>10}")
    cl, ml = t["conversation_llm_calls"], t["memory_llm_calls"]
    c_tool, m_tool = t["conversation_tool_calls"], t["memory_tool_calls"]
    print(f"{'LLM calls':<28}{cl:>14,}{ml:>14,}{(ml / cl if cl else 0):>9.2f}x")
    print(f"{'Tool calls':<28}{c_tool:>14,}{m_tool:>14,}{(m_tool / c_tool if c_tool else 0):>9.2f}x")
    print(f"{'Turns / invocations':<28}{t['conversation_turns']:>14,}{t['memory_invocations']:>14,}")
    print()

    pu = agg["per_unit"]
    print("Per-unit averages (totals-based)")
    print(
        f"  Conv:    {pu['conversation_llm_per_turn'] or 0:.2f} LLM/turn"
        f"   {pu['conversation_tool_per_turn'] or 0:.2f} tool/turn"
    )
    print(
        f"  Memory:  {pu['memory_llm_per_invocation'] or 0:.2f} LLM/invocation"
        f"   {pu['memory_tool_per_invocation'] or 0:.2f} tool/invocation"
    )
    print()

    def print_dist(label: str, d: dict[str, Any], fmt: str = ".2f", suffix: str = "") -> None:
        if not d["n"]:
            print(f"  {label}: no paired conversations")
            return
        stdev_s = f"  stdev={d['stdev']:{fmt}}{suffix}" if d["stdev"] is not None else "  stdev=n/a"
        print(
            f"  {label} (n={d['n']:,}): "
            f"min={d['min']:{fmt}}{suffix}  "
            f"mean={d['mean']:{fmt}}{suffix}  "
            f"median={d['median']:{fmt}}{suffix}  "
            f"p90={d['p90']:{fmt}}{suffix}  "
            f"p99={d['p99']:{fmt}}{suffix}  "
            f"max={d['max']:{fmt}}{suffix}"
            f"{stdev_s}"
        )

    print("Per-conversation distributions")
    print_dist("memory_llm  / conv_llm   ", agg["ratio_memory_llm_per_conv_llm"], suffix="x")
    print_dist("memory_tool / conv_tool  ", agg["ratio_memory_tool_per_conv_tool"], suffix="x")
    print_dist("memory_invocations / 5 turns", agg["memory_per_5_turns"])

    print_cross_project_warning(agg)


def print_top(rows: list[dict[str, Any]], top: int, sort_key: str) -> None:
    sortable = [r for r in rows if r.get(sort_key) is not None]
    sortable.sort(key=lambda r: r[sort_key], reverse=True)
    print(f"\nTop {min(top, len(sortable))} by {sort_key}:")
    print("-" * 100)
    print(
        f"{'conversation_id':<38}  {'conv_turns':>10} {'conv_llm':>8} "
        f"{'mem_inv':>7} {'mem_llm':>7} {'mem/conv_llm':>13} {'mem/5turns':>11}"
    )
    for r in sortable[:top]:
        ratio = r.get("memory_llm_per_conv_llm")
        per5 = r.get("memory_per_5_turns")
        print(
            f"{r['conversation_id']:<38}  "
            f"{r['conversation_turns']:>10,} {r['conversation_llm_calls']:>8,} "
            f"{r['memory_invocations']:>7,} {r['memory_llm_calls']:>7,} "
            f"{(f'{ratio:.2f}x' if ratio is not None else '-'):>13} "
            f"{(f'{per5:.2f}' if per5 is not None else '-'):>11}"
        )


def write_csv(rows: list[dict[str, Any]], path: str) -> None:
    fields = [
        "conversation_id",
        "conversation_turns",
        "conversation_llm_calls",
        "conversation_tool_calls",
        "memory_invocations",
        "memory_llm_calls",
        "memory_tool_calls",
        "memory_llm_per_conv_llm",
        "memory_tool_per_conv_tool",
        "memory_per_5_turns",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def write_per_dialogue_csv(
    conv_rows: list[dict[str, Any]],
    mem_rows: list[dict[str, Any]],
    path: str,
) -> None:
    """Per-dialogueId join. Same cross-project caveat as the per-cid view:
    memory's dialogueId is currently internal to the agentic loop and only
    propagates from the originating turn in a small fraction of cases."""
    per_did: dict[str, dict[str, Any]] = {}
    for r in conv_rows:
        did = r.get("did")
        if not did:
            continue
        b = per_did.setdefault(did, {
            "dialogue_id": did,
            "conversation_id": r.get("cid"),
            "conversation_turns": 0,
            "conversation_llm_calls": 0,
            "memory_invocations": 0,
            "memory_llm_calls": 0,
        })
        b["conversation_turns"] += int(r.get("turns") or 0)
        b["conversation_llm_calls"] += int(r.get("llm_calls") or 0)
    for r in mem_rows:
        did = r.get("did")
        if not did:
            continue
        b = per_did.setdefault(did, {
            "dialogue_id": did,
            "conversation_id": r.get("cid"),
            "conversation_turns": 0,
            "conversation_llm_calls": 0,
            "memory_invocations": 0,
            "memory_llm_calls": 0,
        })
        b["memory_invocations"] += int(r.get("invocations") or 0)
        b["memory_llm_calls"] += int(r.get("llm_calls") or 0)

    fields = [
        "dialogue_id",
        "conversation_id",
        "conversation_turns",
        "conversation_llm_calls",
        "memory_invocations",
        "memory_llm_calls",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for did, b in sorted(per_did.items()):
            w.writerow({k: b.get(k) for k in fields})


def month_to_date_since() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--days", type=int, help="Last N days. Mutually exclusive with --since/--mtd.")
    g.add_argument("--mtd", action="store_true", help="Month-to-date (default if no window is set).")
    ap.add_argument("--since", help="ISO timestamp lower bound. Pair with optional --until.")
    ap.add_argument("--until", help="ISO timestamp upper bound (exclusive).")
    ap.add_argument("--top", type=int, default=10, help="Print top N rows by ratio (default 10).")
    ap.add_argument(
        "--sort",
        default="memory_llm_per_conv_llm",
        choices=(
            "memory_llm_per_conv_llm",
            "memory_tool_per_conv_tool",
            "memory_llm_calls",
            "memory_tool_calls",
            "memory_per_5_turns",
            "memory_invocations",
        ),
    )
    ap.add_argument("--json", dest="json_out", help="Write the per-cid + aggregates JSON here.")
    ap.add_argument("--csv", dest="csv_out", help="Write the per-cid CSV here.")
    ap.add_argument(
        "--per-dialogue",
        dest="per_dialogue_csv",
        help="Also write a per-dialogueId CSV here. Same cross-project caveat as per-cid.",
    )
    ap.add_argument("--allow-degraded-attribution", action="store_true",
                   help="Accept degraded per-user attribution when the LLM ledger is "
                        "unreachable on a post-2026-07-23 window (default: exit 2). "
                        "Env: AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1")
    args = ap.parse_args()
    _ALLOW_DEGRADED[0] = _ALLOW_DEGRADED[0] or bool(
        getattr(args, "allow_degraded_attribution", False))

    # PROD-3572: /v1/chat/completions needs a project-scoped key per project;
    # OPENAI_ADMIN_API_KEY can't hold model.request scope. Resolve at each walk's
    # call site (both required — the overhead ratio needs both projects).
    conv_api_key = _resolve_completions_api_key(PROJECT_NAMES[CONVERSATION_PROJECT_ID])
    mem_api_key = _resolve_completions_api_key(PROJECT_NAMES[MEMORY_PROJECT_ID])

    if args.since:
        since = args.since
        window_label = f"{since} -> {args.until or 'now'}"
    elif args.days:
        since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
        window_label = f"last {args.days} day(s) (since {since})"
    else:
        since = month_to_date_since()
        window_label = f"month-to-date (since {since})"

    since_unix = _iso_to_unix(since)
    until_unix = _iso_to_unix(args.until)

    print(
        "Attribution walk: IS LLM ledger first for the post-epoch slice "
        f"(>= {LEDGER_EPOCH_LABEL}); stored chat completions for the pre-epoch "
        "slice and as fallback (the completions walk has no server-side "
        "aggregation — it pages every coach-prompter + memory-prompter "
        "completion and counts client-side; minutes for MTD, longer for 30d).",
        file=sys.stderr,
    )
    print(f"Conversation [Production], metadata.name={CONVERSATION_PROMPTER}...", file=sys.stderr)
    conv_rows, conv_did_rows, conv_source = fetch_conversation(conv_api_key, since_unix, until_unix)
    print(f"  -> {len(conv_rows):,} conversation cids", file=sys.stderr)

    print(f"Memory [Production], metadata.name={MEMORY_PROMPTER}...", file=sys.stderr)
    mem_rows, mem_did_rows, mem_source = fetch_memory(mem_api_key, since_unix, until_unix)
    print(f"  -> {len(mem_rows):,} memory cids", file=sys.stderr)

    sources = {"Conversation": conv_source, "Memory": mem_source}
    per_cid, orphans = roll_up(conv_rows, mem_rows)
    agg = aggregate(per_cid, orphans)
    print_summary(window_label, agg, sources)

    # NOTE: the Braintrust-era known-incident regression scan (errored Memory
    # spans matching named signatures) was dropped in the OpenAI migration
    # (PROD-2465). OpenAI only stores SUCCESSFUL completions, so errored spans
    # have no stored-completions equivalent — that monitoring is BT-native and
    # belongs in a separate BT-only skill if revived. See PROD-2456.

    print_top(per_cid, args.top, args.sort)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(
                {
                    "window": {"since": since, "until": args.until, "label": window_label},
                    "attribution_sources": sources,
                    "aggregates": agg,
                    "rows": per_cid,
                },
                f,
                indent=2,
                default=str,
            )
        print(f"\nWrote {args.json_out}")

    if args.csv_out:
        write_csv(per_cid, args.csv_out)
        print(f"Wrote {args.csv_out}")

    if args.per_dialogue_csv:
        # Reuse the per-dialogueId rows already collected during the main walk —
        # no second pass needed (the walk is the expensive part).
        print(
            f"  per-dialogue: {len(conv_did_rows):,} conversation dids, "
            f"{len(mem_did_rows):,} memory dids",
            file=sys.stderr,
        )
        write_per_dialogue_csv(conv_did_rows, mem_did_rows, args.per_dialogue_csv)
        print(f"Wrote {args.per_dialogue_csv}")


if __name__ == "__main__":
    main()
