#!/usr/bin/env python3
"""Fetch the input context behind Aurie journal entries, from OpenAI.

Reads OpenAI's `Journal [Production]` project via the stored chat-completions
endpoints, plus the Responses API via the llmCall ledger. Access pattern
documented in AI Knowledge `Tools/OpenAI` -> "Reading stored completions
(BT migration access pattern)".

This is the *input* side of a journal analysis: the prompt the journal was
generated from, carrying the day's conversation excerpt. For the rendered
journal the user actually read and rated -- title, summary, insights,
affirmations -- use the sibling `fetch_journal.py`, which reads the persisted
row from the Aura API by journal id.

Pivots from a Firebase UID. Lists the Journal project with a server-side
`metadata[user]` filter (newest-first), so OpenAI returns only this user's
journals; the `since_unix` lower bound is applied client-side.

With `--near-iso <timestamp>` -- the rendered journal's `created_at` from
`fetch_journal.py` -- returns the single call generated closest to that
moment, which is how a k8bot-routed lookup resolves to one specific journal.
The generating call is written within seconds of the row, so proximity
identifies it reliably.

Used by the `data:analyze-user-journal` skill -- see SKILL.md.

Usage:
    fetch_logs.py --user-id <UID> [--window 30d] [--json out.json]
    fetch_logs.py --user-id <UID> --window last_journal
    fetch_logs.py --user-id <UID> --window last_n=5
    fetch_logs.py --user-id <UID> --near-iso 2026-08-05T02:05:40.628Z

Window shorthands:
    30d              -- last 30 days (default)
    7d               -- last 7 days
    last_journal     -- only the most recent journal completion
    last_n=<int>     -- the most recent N journals

`--window` is ignored when `--near-iso` resolves to a single call -- a
timestamp match already scopes to one journal.

Env:
    OPENAI_JOURNAL_API_KEY (or the project-specific key matching --project-id,
    see PROJECT_API_KEYS below) -- required. OpenAI enforces a `model.request`
    scope on GET /v1/chat/completions that OPENAI_ADMIN_API_KEY cannot hold
    (Admin keys can never gain it, by OpenAI design), so a project-scoped
    service-account key is required per project (PROD-3572). OPENAI_ADMIN_API_KEY
    no longer works here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

# `responses_reader` sits beside this file. Skills are self-contained here (no
# cross-skill imports), so put our own scripts dir on the path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from responses_reader import (  # noqa: E402
    JOURNAL_PROMPTERS,
    ResponsesReaderError,
    fetch_responses_conversations,
)

OPENAI_API = "https://api.openai.com/v1"

# Journal [Production] -- the OpenAI project that stores journal-prompter
# calls. See AI Knowledge `Tools/OpenAI` -> "Prompter -> app routing".
JOURNAL_PROJECT_ID = "proj_qXZ9CZnjp4mL5PVsW43JyOpj"

# Project-scoped service-account key per OpenAI project (PROD-3572). Admin keys
# (OPENAI_ADMIN_API_KEY) can no longer read stored completions -- OpenAI
# enforces a `model.request` scope they cannot hold, by OpenAI design -- so
# each project this script might target (via --project-id) needs its own key.
# Extend this map when a caller points --project-id at a project not yet
# listed; see AI Knowledge `Tools/OpenAI` -> project inventory for IDs.
PROJECT_API_KEYS: dict[str, str] = {
    JOURNAL_PROJECT_ID: "OPENAI_JOURNAL_API_KEY",
}


def _resolve_api_key(project_id: str) -> str:
    """Resolve the project-scoped API key for `project_id` (PROD-3572).

    OPENAI_ADMIN_API_KEY no longer works for stored-completions reads -- only
    a service-account key scoped to the target project does. Errors clearly
    when the project has no known env var yet, or the env var isn't set.
    """
    env_name = PROJECT_API_KEYS.get(project_id)
    if not env_name:
        raise FetchLogsError(
            f"No known project-scoped API key for project {project_id!r}. "
            "OPENAI_ADMIN_API_KEY no longer works for stored-completions reads "
            "(PROD-3572) -- create a service-account key for this project at "
            "https://platform.openai.com/api-keys, add it to PROJECT_API_KEYS "
            "in this script, and set it in ~/.claude/settings.json."
        )
    token = os.environ.get(env_name)
    if not token:
        raise FetchLogsError(
            f"{env_name} is not set. This project's stored completions require "
            "a project-scoped service-account key (OPENAI_ADMIN_API_KEY no "
            "longer works here -- PROD-3572). Create one at "
            f"https://platform.openai.com/api-keys (Create new secret key -> "
            f"Owned by: Service account -> Project matching {project_id}), add "
            f"it to ~/.claude/settings.json under env.{env_name}, and restart "
            "Claude Code."
        )
    return token

PAGE_LIMIT = 100
MAX_WALK = 100_000


class FetchLogsError(Exception):
    """Unrecoverable fetch/auth failure on the library path.

    Raised instead of sys.exit() so an in-process batch caller (e.g. the
    cancellation-enrichment loop, which calls run_user_id() once per row) can
    catch it per-UID and keep going instead of the whole sweep dying on one
    bad user. sys.exit() raises SystemExit, which an `except Exception` fallback
    does NOT catch -- that's what silently killed a sweep after its first UID on
    an OpenAI 401. main() maps this back to a CLI exit for standalone use.
    """

# How far apart the rendered journal's createdAt and its generating call may
# be before the match is rejected. The call is written within seconds of the
# row, so this is generous -- wide enough to absorb clock skew and a slow
# write, narrow enough that a neighbouring day's journal can't win.
NEAR_TOLERANCE_S = 6 * 60 * 60


# ---------------------------------------------------------------------------
# HTTP helpers (mirror analyze-user-conversations/scripts/fetch_logs.py)
# ---------------------------------------------------------------------------

def _headers(api_key: str, project_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Project": project_id,
    }


def _request_with_retry(url: str, headers: dict, params: dict | None = None) -> dict:
    for attempt in range(5):
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(min(wait, 30))
            continue
        if not r.ok:
            raise FetchLogsError(f"OpenAI {r.status_code} on {url}: {r.text[:300]}")
        return r.json()
    raise FetchLogsError(f"OpenAI {url}: rate-limited after 5 retries")


def _iso(unix_ts: int | float | None) -> str | None:
    if unix_ts is None:
        return None
    return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc).isoformat()


def _match_user(item: dict, user_id: str) -> bool:
    md = item.get("metadata") or {}
    return md.get("user") == user_id or item.get("input_user") == user_id


# ---------------------------------------------------------------------------
# Braintrust fallback — queried when OpenAI stored-completions return 0 for a
# lookup. The pre-migration logging path still runs in parallel and has been
# observed to hold records OpenAI silently dropped (confirmed 2026-06-23 for
# the memory-ingest path; same pattern applies here). Requires
# BRAINTRUST_API_KEY; degrades gracefully when absent.
# ---------------------------------------------------------------------------

_BRAINTRUST_BTQL = "https://api.braintrust.dev/btql"
_BRAINTRUST_ORG = "Aura Health 1"
_BT_JOURNAL_PROJECT = "Journal"
_BT_MAX_RETRIES = 3


def _bt_iso_to_unix(value: Any) -> int:
    """Convert a Braintrust ISO-8601 `created` string to unix seconds."""
    if not isinstance(value, str):
        return 0
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (ValueError, OverflowError):
        return 0


def _bt_resolve_project_id(token: str, project_name: str) -> str | None:
    """Resolve a Braintrust project name to its UUID. Returns None on failure."""
    try:
        r = requests.get(
            "https://api.braintrust.dev/v1/project",
            headers={"Authorization": f"Bearer {token}"},
            params={"project_name": project_name, "org_name": _BRAINTRUST_ORG},
            timeout=30,
        )
        if not r.ok:
            return None
        objs = r.json().get("objects", [])
        return objs[0]["id"] if objs else None
    except Exception:
        return None


def _bt_run_query(token: str, query: str) -> list[dict]:
    """Run one BTQL query against Braintrust, with retry on 429/5xx."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in range(_BT_MAX_RETRIES):
        try:
            r = requests.post(
                _BRAINTRUST_BTQL, headers=headers, json={"query": query}, timeout=60,
            )
        except requests.RequestException:
            time.sleep(2)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code != 200:
            return []
        try:
            return r.json().get("data", []) or []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _bt_build_journal_record(row: dict) -> dict:
    """Build a journal record from a BT span.

    Mirrors build_journal_record() but without the fetch_messages() HTTP call —
    BT spans carry their input messages inline in `input.messages`.
    """
    md = row.get("metadata") or {}
    inp = row.get("input")
    messages: list[dict] = (
        inp.get("messages") if isinstance(inp, dict) else
        inp if isinstance(inp, list) else
        []
    ) or []
    input_parts = _input_texts(messages)
    input_text = "\n\n".join(input_parts)

    out = row.get("output")
    asst_content: Any = None
    if isinstance(out, dict):
        asst_content = out.get("content") if "content" in out else out
    elif isinstance(out, str):
        asst_content = out
    output_fields = extract_output_fields(asst_content)

    return {
        "completion_id": row.get("id") or row.get("span_id") or "",
        "created_at": row.get("created"),
        "model": md.get("model"),
        "input": input_text,
        "output": output_fields,
        "metadata": md,
        "source": "braintrust_fallback",
    }


def _bt_fetch_journals_by_user(token: str, user_id: str, since_unix: int) -> list[dict]:
    """Fetch BT journal spans for a user from the Journal project via BTQL."""
    project_id = _bt_resolve_project_id(token, _BT_JOURNAL_PROJECT)
    if not project_id:
        return []
    since_iso = _iso(since_unix)
    query = (
        "select id, span_id, metadata, input, output, created "
        f"from project_logs('{project_id}') "
        f"where metadata.user = '{user_id}' "
        f"and created >= '{since_iso}' "
        "limit 200"
    )
    return _bt_run_query(token, query)


# ---------------------------------------------------------------------------
# Window parsing
# ---------------------------------------------------------------------------

def parse_window(window: str) -> dict[str, Any]:
    if window in ("30d", "7d") or (window.endswith("d") and window[:-1].isdigit()):
        days = int(window[:-1])
        since_unix = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        return {"kind": "days", "since_unix": since_unix, "days": days}
    if window == "last_journal":
        return {"kind": "last_journal"}
    if window.startswith("last_n="):
        n = int(window.split("=", 1)[1])
        if n <= 0:
            raise FetchLogsError(f"--window last_n=<int> requires a positive integer, got {n}")
        return {"kind": "last_n", "n": n}
    raise FetchLogsError(
        f"Invalid --window {window!r}. Use 30d / 7d / NNd / last_journal / last_n=<int>."
    )


# ---------------------------------------------------------------------------
# Completion walking + content extraction
# ---------------------------------------------------------------------------

def walk_completions(
    api_key: str,
    project_id: str,
    since_unix: int,
    match: Any,
    early_stop_pages_after_first_match: int | None = None,
    metadata_filter: dict | None = None,
) -> list[dict]:
    """Walk a project's stored completions newest-first.

    Mirrors analyze-user-conversations/scripts/fetch_logs.py:walk_completions.

    `metadata_filter` (e.g. `{"metadata[user]": uid}`) is sent as query params so
    OpenAI filters **server-side** -- pages then carry only this user's
    journal completions instead of every journal in the project, which collapses
    the walk to a page or two. Verified 2026-06-05 (AI Knowledge `Tools/OpenAI`
    -> "Metadata field reference"). The `match` callback still runs as a
    defensive client-side refinement (covers the `input_user` fallback). There is
    no native time filter, so `since_unix` is still applied client-side.
    """
    out: list[dict] = []
    walked = 0
    pages_after_first_match = 0
    saw_match = False
    after: str | None = None
    while True:
        params = {"limit": PAGE_LIMIT}
        if metadata_filter:
            params.update(metadata_filter)
        if after:
            params["after"] = after
        data = _request_with_retry(
            f"{OPENAI_API}/chat/completions",
            _headers(api_key, project_id),
            params,
        )
        items = data.get("data", [])
        if not items:
            break

        page_matched = False
        stop = False
        for it in items:
            walked += 1
            if it.get("created", 0) < since_unix:
                stop = True
                continue
            if match(it):
                out.append(it)
                page_matched = True

        if saw_match and not page_matched:
            pages_after_first_match += 1
        if page_matched:
            saw_match = True

        if early_stop_pages_after_first_match is not None and saw_match and pages_after_first_match >= early_stop_pages_after_first_match:
            break

        if stop or walked >= MAX_WALK or not data.get("has_more"):
            break
        last_id = data.get("last_id") or items[-1].get("id")
        if not last_id:
            break
        after = last_id

    return out


def fetch_messages(api_key: str, project_id: str, completion_id: str) -> list[dict]:
    """Return the full message transcript for one journal completion id.

    For journal calls the messages include the system prompt and the input
    user message (the day's conversation excerpt + user info). The assistant's
    output (`title`, `insights`, `summary`, `affirmations`) is in
    `choices[0].message.content` on the parent completion and also lands here
    as the final assistant message.
    """
    msgs: list[dict] = []
    after: str | None = None
    while True:
        params = {"limit": PAGE_LIMIT}
        if after:
            params["after"] = after
        data = _request_with_retry(
            f"{OPENAI_API}/chat/completions/{completion_id}/messages",
            _headers(api_key, project_id),
            params,
        )
        items = data.get("data", [])
        msgs.extend(items)
        if not data.get("has_more") or not items:
            break
        last_id = items[-1].get("id")
        if not last_id:
            break
        after = last_id
    return msgs


def _msg_text(msg: dict) -> str | None:
    """Extract plain text from a /messages entry. See analyze-user-conversations."""
    content = msg.get("content")
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                text = p.get("text") or p.get("content")
                if text:
                    parts.append(text)
        return "\n".join(parts) if parts else None
    return None


# ---------------------------------------------------------------------------
# journal output parsing -- journal-prompter emits JSON (often markdown-fenced)
# with title / insights / summary / affirmations.
# ---------------------------------------------------------------------------

def _input_texts(messages: list[dict]) -> list[str]:
    """The text of a call's system, developer and user messages, in order.

    From Intelligence Service 1.36 the context is a developer message after the
    system message, and from 1.40 it follows a developer `---` + user `---`
    cache-delimiter pair (`utils/buildContextMessages.js`). The pair is not
    input, so it is skipped -- only where it sits, right after the system message.
    """
    start = 0
    while start < len(messages) and messages[start].get("role") == "system":
        start += 1
    skip = set()
    if (start and start + 1 < len(messages)
            and messages[start].get("role") == "developer" and _msg_text(messages[start]) == "---"
            and messages[start + 1].get("role") == "user" and _msg_text(messages[start + 1]) == "---"):
        skip = {start, start + 1}
    return [t for i, m in enumerate(messages)
            if i not in skip and m.get("role") in ("system", "developer", "user") and (t := _msg_text(m))]


def extract_output_fields(asst_content: str | None) -> dict[str, Any]:
    """Parse the journal output fields out of the assistant content string.

    Three shapes seen in the wild:
      1. Raw markdown-fenced JSON (most common).
      2. Plain JSON string.
      3. Already an object -- not typical from /messages, kept defensive.
    """
    if asst_content is None:
        return {"title": None, "insights": None, "summary": None, "affirmations": None}
    if isinstance(asst_content, dict):
        return {
            "title": asst_content.get("title"),
            "insights": asst_content.get("insights"),
            "summary": asst_content.get("summary"),
            "affirmations": asst_content.get("affirmations"),
        }
    stripped = asst_content.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        stripped = "\n".join(inner).strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return {
                "title": parsed.get("title"),
                "insights": parsed.get("insights"),
                "summary": parsed.get("summary"),
                "affirmations": parsed.get("affirmations"),
            }
    except (json.JSONDecodeError, ValueError):
        pass
    return {"title": None, "insights": None, "summary": None, "affirmations": None}


# ---------------------------------------------------------------------------
# Selecting the call behind one known journal
# ---------------------------------------------------------------------------

def _parse_iso_to_unix(value: str) -> int:
    """Parse an ISO-8601 timestamp (accepting a trailing Z) to epoch seconds.

    A value carrying no offset is read as UTC. Left naive, `.timestamp()`
    would interpret it in the host's local zone -- an hours-scale shift that
    would quietly move the match to the wrong journal rather than fail.
    """
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def nearest_by_created_at(
    journals: list[dict], target_unix: int, tolerance_s: int = NEAR_TOLERANCE_S
) -> int | None:
    """Index of the journal call generated closest to `target_unix`, or None.

    The rendered journal row carries the `createdAt` of the entry the user
    read, and the generating call is written within seconds of it. Proximity
    in time therefore identifies the call. Text cannot: the k8bot fenced block
    holds the user's rating comment, not journal content.

    Returns None when the closest candidate falls outside `tolerance_s`, so a
    window holding no corresponding call reports a miss rather than the
    least-bad guess.
    """
    best_index: int | None = None
    best_delta: int | None = None
    for index, journal in enumerate(journals):
        created = journal.get("created_at")
        if not created:
            continue
        try:
            delta = abs(_parse_iso_to_unix(str(created)) - target_unix)
        except ValueError:
            continue
        if best_delta is None or delta < best_delta:
            best_index, best_delta = index, delta

    if best_delta is None or best_delta > tolerance_s:
        return None
    return best_index


def build_journal_record(
    api_key: str,
    project_id: str,
    completion: dict,
) -> dict:
    """Build a journal record from one completion id.

    Journal calls have a distinctive shape on the stored side:
      - `/messages` returns ONE message: the system prompt, with the day's
        conversation excerpt + user PII embedded inline by IntelligenceService.
        There is no `user`-role message because journal-prompter is a
        single-shot call whose input is the system prompt itself.
      - `choices[0].message.content` carries the assistant output -- a
        markdown-fenced JSON object with title / insights / summary /
        affirmations. Fetch the completion-level message directly; don't
        re-fetch via /messages.

    Returns the parsed output fields, the system-prompt input text (so
    the synthesis can see what the journal was generated from), and metadata.
    """
    cid = completion["id"]

    # Output JSON is on the completion itself, in choices[0].message.content.
    choices = completion.get("choices") or [{}]
    asst_msg = (choices[0] or {}).get("message") or {}
    asst_text = _msg_text(asst_msg)
    output_fields = extract_output_fields(asst_text)

    # The system prompt carries the input context. For a Chat-Completions item
    # that means one /messages fetch; for a Responses item `responses_reader`
    # has already put the input on `messages` (there is no /messages endpoint
    # for a response, only /input_items, which the reader consumed).
    if completion.get("_source") == "responses":
        messages = completion.get("messages") or []
    else:
        messages = fetch_messages(api_key, project_id, cid)
    # Up to Intelligence Service 1.35 a journal call is typically one system
    # message with the context embedded inline; from 1.36 the context is a
    # developer message after it (see `_input_texts`). Keep `user`-role too in
    # case any journal variant emits one.
    input_text = "\n\n".join(_input_texts(messages))

    return {
        "completion_id": cid,
        "created_at": _iso(completion.get("created")),
        "model": completion.get("model"),
        "input": input_text,
        "output": output_fields,
        "metadata": completion.get("metadata") or {},
    }


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def run_user_id(
    api_key: str,
    project_id: str,
    user_id: str,
    window: str,
    near_unix: int | None,
) -> dict:
    window_spec = parse_window(window)

    if window_spec["kind"] == "days":
        since_unix = window_spec["since_unix"]
    else:
        since_unix = int(
            (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        )

    # Early-stop on last_journal and when homing in on one known journal: we
    # only need to walk past the matched item, not the full window.
    early_stop = (
        10
        if window_spec.get("kind") == "last_journal" or near_unix is not None
        else None
    )

    completions = walk_completions(
        api_key,
        project_id,
        since_unix,
        lambda it: _match_user(it, user_id),
        early_stop_pages_after_first_match=early_stop,
        metadata_filter={"metadata[user]": user_id},
    )

    # The `journal` prompter moved to the Responses API (ENGBACK-1235), and
    # Responses objects are NOT enumerable on OpenAI -- `GET /v1/responses`
    # rejects API keys ("must be made with a session key"). So the walk above
    # returns nothing for recent journals and the llmCall ledger has to index
    # them instead.
    #
    # This runs BEFORE the Braintrust fallback deliberately: without it the walk
    # returned 0, Braintrust silently rescued the run, and the result looked
    # healthy while actually depending on a source this repo migrated off in
    # 2026-06 (PROD-2457). Live OpenAI data should win over that crutch.
    responses_diag: dict[str, Any] = {}
    try:
        responses_result = fetch_responses_conversations(
            api_key, project_id, user_id, since_unix,
            prompters=JOURNAL_PROMPTERS,
            # Each journal is its own record, not a turn in a shared
            # conversation, and the analysis needs every call's own input.
            # Journals are 1:1 with conversationId today so the default
            # grouping would not actually drop anything -- but that is an
            # accident of how they are keyed, so state the requirement rather
            # than depend on it.
            input_items_for="all",
        )
        completions = completions + responses_result["items"]
        responses_diag = {
            "ledger_row_count": responses_result["ledger_row_count"],
            "responses_row_count": responses_result["responses_row_count"],
            "unfetchable_count": len(responses_result["unfetchable"]),
        }
        if responses_result["items"]:
            print(
                f"# Recovered {len(responses_result['items'])} journal(s) from the "
                "Responses API via the llmCall ledger.",
                file=sys.stderr,
            )
        if responses_result["unfetchable"]:
            print(
                f"# WARNING: {len(responses_result['unfetchable'])} Responses "
                "object(s) were in the ledger but could not be fetched -- the "
                "journal list is incomplete, not empty.",
                file=sys.stderr,
            )
    except ResponsesReaderError as exc:
        responses_diag = {"error": str(exc)[:300]}
        print(
            f"# WARNING: could not read Responses-API journals ({exc}). Recent "
            "journals run on the Responses API, so results may be missing them "
            "entirely.",
            file=sys.stderr,
        )

    if not completions:
        bt_token = os.environ.get("BRAINTRUST_API_KEY")
        if bt_token:
            print(
                "# OpenAI returned 0 matches; trying Braintrust fallback "
                f"(Journal project, user={user_id})…",
                file=sys.stderr,
            )
            bt_rows = _bt_fetch_journals_by_user(bt_token, user_id, since_unix)
            if len(bt_rows) >= 200:
                print(
                    "# WARNING: Braintrust fallback hit the 200-span cap — "
                    "oldest journal entries may be missing.",
                    file=sys.stderr,
                )
            if bt_rows:
                print("# Recovered from Braintrust fallback.", file=sys.stderr)
                bt_rows.sort(
                    key=lambda r: _bt_iso_to_unix(r.get("created")), reverse=True
                )
                kind = window_spec.get("kind")
                if kind == "last_journal":
                    bt_rows = bt_rows[:1]
                elif kind == "last_n":
                    bt_rows = bt_rows[: window_spec["n"]]
                journals = [_bt_build_journal_record(r) for r in bt_rows]
                # Mirror the OpenAI path's proximity match so a --near-iso
                # caller doesn't get an unfiltered list.
                if near_unix is not None:
                    index = nearest_by_created_at(journals, near_unix)
                    if index is not None:
                        return {
                            "user_id": user_id,
                            "window": window_spec,
                            "match_strategy": "user_id + createdAt proximity",
                            "journal_count": 1,
                            "journals": [journals[index]],
                            "source": "braintrust_fallback",
                        }
                    return {
                        "user_id": user_id,
                        "window": window_spec,
                        "match_strategy": "user_id only (no call near the journal timestamp)",
                        "journal_count": len(journals),
                        "journals": journals,
                        "source": "braintrust_fallback",
                        "hint": (
                            "No journal call was generated within "
                            f"{NEAR_TOLERANCE_S // 3600}h of the journal's "
                            "createdAt. Browse the returned journals manually."
                        ),
                    }
                return {
                    "user_id": user_id,
                    "window": window_spec,
                    "match_strategy": "user_id only",
                    "journal_count": len(journals),
                    "journals": journals,
                    "source": "braintrust_fallback",
                }
            print(
                "# Braintrust fallback also returned no journal spans for this user.",
                file=sys.stderr,
            )
        return {
            "user_id": user_id,
            "window": window_spec,
            "journal_count": 0,
            "journals": [],
            "hint": (
                "Zero journal completions matched. Check: (1) the UID is correct, "
                "(2) the user actually used the journal feature in the window, "
                "(3) the window isn't pre-2026-05-12 (subscription-metadata tagging "
                "started then; pre-rollout traffic doesn't carry metadata.user)."
            ),
        }

    # Sort newest-first.
    completions.sort(key=lambda c: c.get("created") or 0, reverse=True)

    # Apply window-shape trimming (last_journal / last_n / days = no trim).
    kind = window_spec.get("kind")
    if kind == "last_journal":
        candidates = completions[:1]
    elif kind == "last_n":
        candidates = completions[: window_spec["n"]]
    else:
        candidates = completions

    # Build full records for the candidates.
    journals = [build_journal_record(api_key, project_id, c) for c in candidates]

    # Home in on the call behind one known journal when the caller passed its
    # timestamp -- one call is far more useful than the whole window.
    if near_unix is not None:
        index = nearest_by_created_at(journals, near_unix)
        if index is not None:
            return {
                "user_id": user_id,
                "window": window_spec,
                "match_strategy": "user_id + createdAt proximity",
                "journal_count": 1,
                "journals": [journals[index]],
                "responses_source": responses_diag,
            }
        # Nothing landed near the journal timestamp. Return all candidates so
        # the caller can browse, plus a hint.
        return {
            "user_id": user_id,
            "window": window_spec,
            "match_strategy": "user_id only (no call near the journal timestamp)",
            "journal_count": len(journals),
            "journals": journals,
            "hint": (
                "No journal call was generated within "
                f"{NEAR_TOLERANCE_S // 3600}h of the journal's createdAt. Either "
                "the window is too narrow to contain it, or the generating call "
                "predates the OpenAI retention horizon. The rendered journal from "
                "fetch_journal.py still stands on its own -- report the missing "
                "input context rather than dropping the analysis."
            ),
            "responses_source": responses_diag,
        }

    return {
        "user_id": user_id,
        "window": window_spec,
        "match_strategy": "user_id only",
        "journal_count": len(journals),
        "journals": journals,
        "responses_source": responses_diag,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--user-id", required=True, help="Firebase UID. The lookup pivot."
    )
    ap.add_argument(
        "--near-iso",
        default=None,
        help=(
            "The rendered journal's createdAt (ISO-8601), from fetch_journal.py. "
            "When set, returns the single journal call generated closest to it "
            "instead of the whole window."
        ),
    )
    ap.add_argument(
        "--window",
        default=None,
        help=(
            "30d (default) | 7d | NNd | last_journal | last_n=<int>. "
            "Ignored when --near-iso resolves to a single call."
        ),
    )
    ap.add_argument(
        "--project-id",
        default=JOURNAL_PROJECT_ID,
        help=(
            f"OpenAI project ID (default: Journal [Production] {JOURNAL_PROJECT_ID})."
        ),
    )
    ap.add_argument("--json", dest="json_out", help="Write JSON output here.")
    args = ap.parse_args()

    # The library path raises FetchLogsError (not SystemExit) so in-process
    # batch callers can isolate a bad UID; as the CLI, map it back to a
    # non-zero exit. The arg-validation sys.exit() calls below raise SystemExit,
    # which passes straight through this FetchLogsError handler unchanged.
    try:
        api_key = _resolve_api_key(args.project_id)

        near_unix = None
        if args.near_iso:
            try:
                near_unix = _parse_iso_to_unix(args.near_iso)
            except ValueError:
                sys.exit(
                    f"--near-iso {args.near_iso!r} is not an ISO-8601 timestamp. "
                    "Pass the rendered journal's created_at verbatim from "
                    "fetch_journal.py."
                )

        report = run_user_id(
            api_key,
            args.project_id,
            args.user_id,
            args.window or "30d",
            near_unix,
        )
    except FetchLogsError as exc:
        sys.exit(str(exc))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Wrote {args.json_out}")
        print(
            f"  user={args.user_id}  window={args.window or '30d'}  "
            f"journals={report.get('journal_count', 0)}  "
            f"strategy={report.get('match_strategy', 'n/a')}"
        )
    else:
        json.dump(report, sys.stdout, indent=2, default=str)
        print()


if __name__ == "__main__":
    main()
