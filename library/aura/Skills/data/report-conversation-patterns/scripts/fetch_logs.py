#!/usr/bin/env python3
"""Fetch Aurie conversation transcripts from OpenAI stored completions.

Replaces the prior Braintrust BTQL implementation (PROD-2457, umbrella PROD-2456).
Reads OpenAI's `Conversation [Production]` project via the stored chat-completions
endpoints. Access pattern documented in AI Knowledge `Tools/OpenAI` ->
"Reading stored completions (BT migration access pattern)".

Two entry points, mutually exclusive:

  * `--user-id <UID>`         -- pivots from a Firebase UID. Lists the project
                                 with a server-side `metadata[user]` filter
                                 (newest-first), applies the `since_unix` window
                                 bound client-side, and groups by
                                 `metadata.conversationId`. The top-level
                                 `input_user` is kept as a defensive fallback.
  * `--conversation-id <UUID>` -- pulls one conversation directly via a
                                 server-side `metadata[conversationId]` filter.
                                 Near-instant; preferred when the caller has the ID.

Used by the `data:analyze-user-conversations` skill -- see SKILL.md.

Usage:
    fetch_logs.py --user-id <UID> [--window 60d] [--json out.json]
    fetch_logs.py --user-id <UID> --window last_conversation
    fetch_logs.py --user-id <UID> --window last_n=5
    fetch_logs.py --conversation-id <UUID> [--json out.json]

Window shorthands (only with --user-id):
    60d                     -- last 60 days (default)
    7d                      -- last 7 days
    last_conversation       -- only the most recent conversation_id
    last_n=<int>            -- the most recent N conversation_ids

`--window` is ignored (with a stderr note) when `--conversation-id` is set --
a conversationId query already scopes to one conversation.

Env:
    OPENAI_CONVERSATION_API_KEY (or the project-specific key matching
    --project-id, see PROJECT_API_KEYS below) -- required. OpenAI enforces a
    `model.request` scope on GET /v1/chat/completions that OPENAI_ADMIN_API_KEY
    cannot hold (Admin keys can never gain it, by OpenAI design), so a
    project-scoped service-account key is required per project (PROD-3572).
    OPENAI_ADMIN_API_KEY no longer works here.

Notes on what changed vs the BT version:
    - No project-id lookup step (OpenAI project IDs are constants).
    - No `metadata.userId / uid / firebase_uid` field probing -- OpenAI's
      IntelligenceService write path uses `metadata.user` consistently; the
      script also accepts the top-level `input_user` field as a fallback.
    - No `extract_rating()` -- BT's `review` span (User Rating + auto-title +
      deliberation_seconds) has no OpenAI equivalent. Rating polarity continues
      to flow from the Slack URL parse in Mode A; Mode B without a URL loses
      the rating context (the synthesis can ask the caller if needed).
    - No per-turn fan-out of /messages (would be N completions of API calls per
      conversation). Instead, /messages is fetched ONCE for the latest
      completion in each conversation. That response is the completion's INPUT,
      so it carries the system prompt + the recent user/assistant turns, but it
      is capped at the prompter's `numberOfPreviousMessages` (e.g. 20 for
      coach-prompter -> ~10 turns) and it does NOT include the final assistant
      reply (that reply is the completion's OUTPUT). We therefore:
        * attach the latest completion's OUTPUT as the trailing turn's assistant
          reply (else the last turn looks empty -- a phantom "empty terminal
          turn"), and
        * report the true length via `total_turn_count` (distinct dialogueIds
          across all completions) + `transcript_truncated`, so a long
          conversation isn't mistaken for its ~10-turn tail.
      Per-turn timestamps within a conversation are approximated to the
      conversation's `ended_at`; per-turn timing isn't available without
      per-completion fetches.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

# `responses_reader` sits beside this file. Skills are deliberately
# self-contained (no cross-skill imports anywhere in this repo, because plugins
# distribute independently), so put our own scripts dir on the path rather than
# reaching for a shared package.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from responses_reader import (  # noqa: E402
    CONVERSATION_PROMPTERS,
    ResponsesReaderError,
    _item_text,
    _output_text,
    fetch_input_items,
    fetch_response,
    fetch_responses_conversations,
    fetch_responses_for_conversation,
)

OPENAI_API = "https://api.openai.com/v1"

# Conversation [Production] -- the OpenAI project that stores coach-prompter,
# quick-processing-prompter, insight-card-prompter, starters-prompter,
# guardrails-handler-prompter, and a few other conversational prompters.
# See AI Knowledge `Tools/OpenAI` -> "Prompter -> app routing".
CONVERSATION_PROJECT_ID = "proj_esFyd4eq2VIAjKUn69X1ZFfk"

# Project-scoped service-account key per OpenAI project (PROD-3572). Admin keys
# (OPENAI_ADMIN_API_KEY) can no longer read stored completions -- OpenAI
# enforces a `model.request` scope they cannot hold, by OpenAI design -- so
# each project this script might target (via --project-id) needs its own key.
# Extend this map when a caller points --project-id at a project not yet
# listed; see AI Knowledge `Tools/OpenAI` -> project inventory for IDs.
PROJECT_API_KEYS: dict[str, str] = {
    CONVERSATION_PROJECT_ID: "OPENAI_CONVERSATION_API_KEY",
}

# Completion-list page size. OpenAI accepts up to 100.
PAGE_LIMIT = 100

# When walking by conversationId without a time bound, cap the walk so a stale
# conversationId doesn't make us walk all of recorded history. 30 days is well
# past the longest-running Aurie conversation observed.
CONVERSATION_ID_WALK_CEILING_DAYS = 30

# Maximum total completions to walk in any single invocation. Defensive cap
# for misbehaving filters -- shouldn't be hit in normal usage.
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


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers(api_key: str, project_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Project": project_id,
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


def _is_conversation_prompter(item: dict) -> bool:
    """True when a stored record is a conversation turn rather than a side effect.

    The Conversation project stores more than transcripts: `insight-card-prompter`,
    `starters-prompter` and friends run against the same project with the same
    `metadata.user`, so a user-pivot walk returns them alongside real turns. They
    carry only a system message, so each one becomes a conversation with zero
    turns -- a phantom that inflates `conversation_count` and reads as an empty
    transcript. On a 1-day sample of an active user, 12 of 33 conversations were
    insight cards.

    The prompter name lives under a different metadata key per source: OpenAI
    stored completions use `name`, while Braintrust spans and the llmCall ledger
    use `prompterName`. Both are accepted so one predicate serves every caller --
    reading only `name` would silently reject every Braintrust span.
    """
    md = item.get("metadata") or {}
    return (md.get("name") or md.get("prompterName")) in CONVERSATION_PROMPTERS


def _match_conv(item: dict, conv_id: str) -> bool:
    md = item.get("metadata") or {}
    # IntelligenceService writes `metadata.conversationId` (camelCase). Defensive:
    # also accept the snake_case form, and both case variants of the UUID.
    candidates = {conv_id, conv_id.upper(), conv_id.lower()}
    return (md.get("conversationId") in candidates
            or md.get("conversation_id") in candidates)


# ---------------------------------------------------------------------------
# Braintrust fallback — queried when OpenAI stored-completions return 0 for a
# lookup. The pre-migration logging path still runs in parallel and has been
# observed to hold records OpenAI silently dropped (confirmed 2026-06-23 for
# the memory-ingest path; same pattern applies here). Requires
# BRAINTRUST_API_KEY; degrades gracefully when absent.
# ---------------------------------------------------------------------------

_BRAINTRUST_BTQL = "https://api.braintrust.dev/btql"
_BRAINTRUST_ORG = "Aura Health 1"
_BT_CONV_PROJECT = "Conversation"
_BT_MAX_RETRIES = 3
# Row cap for the conversationId BTQL fallback. Hoisted so the truncation
# warning in run_conversation_id() stays in sync with the query limit.
_BT_CONV_ID_LIMIT = 50


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


def _bt_extract_messages(row: dict) -> list[dict]:
    """Extract input messages from a BT span's `input` field."""
    inp = row.get("input")
    if isinstance(inp, dict):
        return inp.get("messages") or []
    if isinstance(inp, list):
        return inp
    return []


def _bt_transcript_row(bt_rows: list[dict]) -> dict:
    """Pick the span whose `input.messages` actually carries the transcript.

    Not always the chronologically last one. The Conversation project stores
    side-effect and bookkeeping spans in the same conversation -- observed
    trailing a real transcript: two `quick-processing-done-prompter` spans and
    one with no `prompterName` at all, each with `input.messages == []`. Reading
    the transcript off `bt_rows[-1]` therefore renders ZERO turns while the
    20-message span sits one row earlier.

    That failure is silent and points the caller at the wrong conclusion: the
    conversation is found, `completion_count` and `total_turn_count` are right,
    and only `turns` is empty -- which `data:run-feedback-analysis-cadence`
    Step 4c reads as a terminal "no transcript" on any entry older than an hour,
    posting an error reply that closes the thread for good. A live, fully
    recorded conversation gets written off as unanalysable.

    Rows are expected sorted by `created`. Returns the LATEST message-bearing
    span, falling back to the last row when none carries messages -- so a
    genuinely empty span set still reports exactly the shape it did before.
    """
    for row in reversed(bt_rows):
        if _bt_extract_messages(row):
            return row
    return bt_rows[-1]


def _bt_build_conversation(conversation_id: str, bt_rows: list[dict]) -> dict:
    """Build a conversation record from BT spans.

    Mirrors build_conversation() but without the fetch_messages() HTTP call —
    BT spans carry their input messages inline in `input.messages`.
    """
    bt_rows.sort(key=lambda r: _bt_iso_to_unix(r.get("created")))
    earliest, latest = bt_rows[0], bt_rows[-1]
    # The span set's extent (started_at/ended_at/completion_count/total turns)
    # comes from ALL rows; the transcript comes from the message-bearing span,
    # which is not always the last one -- see `_bt_transcript_row`.
    source = _bt_transcript_row(bt_rows)
    source_unix = _bt_iso_to_unix(source.get("created"))
    messages = _bt_extract_messages(source)
    turns = messages_to_turns(
        messages,
        _iso(source_unix) if source_unix else None,
        source.get("metadata") or {},
    )
    _bt_attach_latest_output(turns, source)
    total_turns = max(_distinct_turn_count(bt_rows), len(turns))
    return {
        "conversation_id": conversation_id,
        "started_at": earliest.get("created"),
        "ended_at": latest.get("created"),
        "completion_count": len(bt_rows),
        "turn_count": len(turns),
        "total_turn_count": total_turns,
        "transcript_truncated": total_turns > len(turns),
        # The span the transcript was reconstructed from, which is what every
        # turn's `created` is stamped with -- naming the trailing bookkeeping
        # span here would point a reader at a span that contributed nothing.
        "latest_completion_id": source.get("id") or source.get("span_id") or "",
        "turns": turns,
        "source": "braintrust_fallback",
    }


def _bt_attach_latest_output(turns: list[dict], source_row: dict) -> None:
    """BT analogue of _attach_latest_output — fill the trailing turn's reply.

    A BT span's `output` holds the assistant message (a string, a
    `{role, content}` dict, or a `{choices: [...]}` envelope). Defined here
    rather than reusing the OpenAI helper because the output shape differs.
    """
    if not turns:
        return
    last = turns[-1]
    if last.get("output"):
        return
    out = source_row.get("output")
    content = None
    tool_calls = None
    if isinstance(out, str):
        content = out
    elif isinstance(out, dict):
        if isinstance(out.get("choices"), list) and out["choices"]:
            msg = out["choices"][0].get("message") or {}
            content = _msg_text(msg)
            tool_calls = msg.get("tool_calls")
        else:
            content = _msg_text(out)
            tool_calls = out.get("tool_calls")
    if content or tool_calls:
        last["output"] = {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        }


def _bt_fetch_by_user(token: str, user_id: str, since_unix: int) -> list[dict]:
    """Fetch BT spans for a user from the Conversation project via BTQL.

    The prompter filter is applied in BTQL rather than in Python so the 200-span
    cap is spent on conversation turns. Filtering afterwards would let side-effect
    prompters consume the budget and silently truncate the real transcripts --
    on a 7-day sample the unfiltered query returned 200 spans led by insight
    cards, while the filtered one returned 200 spans of coach and
    quick-processing turns.

    Note the key: Braintrust spans carry `metadata.prompterName`, not the `name`
    that OpenAI stored completions use.
    """
    project_id = _bt_resolve_project_id(token, _BT_CONV_PROJECT)
    if not project_id:
        return []
    names = ", ".join(f"'{p}'" for p in sorted(CONVERSATION_PROMPTERS))
    query = (
        "select id, span_id, metadata, input, output, created "
        f"from project_logs('{project_id}') "
        f"where metadata.user = '{user_id}' "
        f"and created >= '{_iso(since_unix)}' "
        f"and metadata.prompterName in ({names}) "
        "limit 200"
    )
    return _bt_run_query(token, query)


def _bt_fetch_by_conversation_id(token: str, conversation_id: str) -> list[dict]:
    """Fetch BT spans for a conversation via BTQL. Tries as-given, upper, lower."""
    project_id = _bt_resolve_project_id(token, _BT_CONV_PROJECT)
    if not project_id:
        return []
    seen: set[str] = set()
    for cid in [conversation_id, conversation_id.upper(), conversation_id.lower()]:
        if cid in seen:
            continue
        seen.add(cid)
        query = (
            "select id, span_id, metadata, input, output, created "
            f"from project_logs('{project_id}') "
            f"where metadata.conversationId = '{cid}' "
            f"limit {_BT_CONV_ID_LIMIT}"
        )
        rows = _bt_run_query(token, query)
        if rows:
            return rows
    return []


# ---------------------------------------------------------------------------
# Window parsing
# ---------------------------------------------------------------------------

def parse_window(window: str) -> dict[str, Any]:
    """Resolve --window into a structured spec.

    Returns one of:
      { "kind": "days",            "since_unix": <int>, "days": <int> }
      { "kind": "last_conversation" }
      { "kind": "last_n",          "n": <int> }
    """
    if window in ("30d", "7d") or (window.endswith("d") and window[:-1].isdigit()):
        days = int(window[:-1])
        since_unix = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        return {"kind": "days", "since_unix": since_unix, "days": days}
    if window == "last_conversation":
        return {"kind": "last_conversation"}
    if window.startswith("last_n="):
        n = int(window.split("=", 1)[1])
        if n <= 0:
            raise FetchLogsError(f"--window last_n=<int> requires a positive integer, got {n}")
        return {"kind": "last_n", "n": n}
    raise FetchLogsError(
        f"Invalid --window {window!r}. Use 30d / 7d / NNd / last_conversation / last_n=<int>."
    )


# ---------------------------------------------------------------------------
# Completion walking
# ---------------------------------------------------------------------------

def walk_completions(
    api_key: str,
    project_id: str,
    since_unix: int,
    match: Any,
    early_stop_pages_after_first_match: int | None = None,
    metadata_filter: dict | None = None,
) -> list[dict]:
    """Walk the project's stored chat-completions newest-first.

    Stops when `created < since_unix` OR `has_more` is false OR MAX_WALK is hit.

    `metadata_filter`: when set (e.g. `{"metadata[user]": uid}` or
    `{"metadata[conversationId]": cid}`), the filter is sent as query params so
    OpenAI filters **server-side** -- pages then contain only matching rows,
    which collapses the walk from "every completion in the project" to "this
    user's / this conversation's completions". Verified 2026-06-05 that the
    list endpoint filters metadata server-side and combines keys as AND (see AI
    Knowledge `Tools/OpenAI` -> "Metadata field reference"). The `match`
    callback still runs as a defensive client-side refinement (it covers the
    `input_user` fallback and conversationId case variants) and is a no-op when
    the server-side filter already narrowed the set.

    There is no native time filter, so the `since_unix` lower bound is still
    applied client-side (stop once a newest-first page drops below it).

    `early_stop_pages_after_first_match`: kept as a backstop for the
    no-server-filter path. With a `metadata_filter` it rarely fires (every page
    matches, so the filtered set simply exhausts via `has_more`), but it does no
    harm.
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
    """Return the full message transcript for one completion id.

    Paginates with `after=<last_msg_id>` if /messages reports `has_more`. The
    response is the conversation's complete message list at the moment this
    completion was created: system + every user turn + every assistant turn,
    including tool-call messages.
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


# ---------------------------------------------------------------------------
# Transcript shaping
# ---------------------------------------------------------------------------

def _msg_text(msg: dict) -> str | None:
    """Extract plain text from a /messages entry.

    The stored shape on /messages mirrors the live chat-completions shape:
    `content` is usually a string, occasionally a list of parts (multimodal).
    Return a flat string for the synthesis to quote.
    """
    content = msg.get("content")
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                # Common shape: {"type": "text", "text": "..."}
                text = p.get("text") or p.get("content")
                if text:
                    parts.append(text)
        return "\n".join(parts) if parts else None
    return None


def _context_delimiter_index(messages: list[dict]) -> int | None:
    """Index of the user `---` of the context delimiter pair, or None.

    The pair sits right after the leading system message(s): a developer `---`
    then a user `---`. Only that position counts.
    """
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        i += 1
    if (i and i + 1 < len(messages)
            and messages[i].get("role") == "developer" and _msg_text(messages[i]) == "---"
            and messages[i + 1].get("role") == "user" and _msg_text(messages[i + 1]) == "---"):
        return i + 1
    return None


def messages_to_turns(
    messages: list[dict],
    completion_created_iso: str | None,
    metadata: dict,
) -> list[dict]:
    """Group the /messages list into user->assistant turn pairs.

    A turn = one user message followed by the next assistant message. System,
    developer and tool messages are stored alongside but not their own turn.
    Per-turn timestamps aren't available -- approximate to the completion's
    `created`.

    From Intelligence Service 1.40 the context follows a developer `---` + user
    `---` cache-delimiter pair right after the system message
    (`utils/buildContextMessages.js`). That user `---` is not a user turn; a
    user typing `---` later in the conversation is.
    """
    turns: list[dict] = []
    pending_user: dict | None = None
    delimiter = _context_delimiter_index(messages)

    for i, msg in enumerate(messages):
        role = msg.get("role")
        if role in ("system", "developer") or i == delimiter:
            continue
        if role == "user":
            # Flush any prior user that had no assistant follow-up.
            if pending_user is not None:
                turns.append({
                    "created": completion_created_iso,
                    "input": {"role": "user", "content": _msg_text(pending_user)},
                    "output": None,
                    "metadata": metadata,
                })
            pending_user = msg
            continue
        if role == "assistant" and pending_user is not None:
            turns.append({
                "created": completion_created_iso,
                "input": {"role": "user", "content": _msg_text(pending_user)},
                "output": {
                    "role": "assistant",
                    "content": _msg_text(msg),
                    "tool_calls": msg.get("tool_calls"),
                },
                "metadata": metadata,
            })
            pending_user = None
            continue
        # `tool` and other roles are not their own turn.

    # Trailing user turn with no assistant reply.
    if pending_user is not None:
        turns.append({
            "created": completion_created_iso,
            "input": {"role": "user", "content": _msg_text(pending_user)},
            "output": None,
            "metadata": metadata,
        })

    return turns


def _completion_output_message(completion: dict) -> dict | None:
    """Return the assistant message a completion produced, or None.

    The completion-list objects carry `choices[0].message` inline (the model's
    OUTPUT), so no extra fetch is needed.
    """
    choices = completion.get("choices") or []
    if not choices:
        return None
    return choices[0].get("message") or None


def _attach_latest_output(turns: list[dict], latest: dict) -> None:
    """Fill the trailing user turn's assistant reply from the latest completion.

    `/messages` is a completion's INPUT, so the latest completion's messages end
    with the final user message — its assistant answer is that completion's
    OUTPUT, which is not part of the input. Left alone, messages_to_turns emits a
    trailing turn with `output: None`, which downstream synthesis reads as a
    phantom "empty terminal turn". The completion's own output is that reply.
    """
    if not turns:
        return
    last = turns[-1]
    if last.get("output"):
        return
    msg = _completion_output_message(latest)
    if not msg:
        return
    content = _msg_text(msg)
    tool_calls = msg.get("tool_calls")
    if content or tool_calls:
        last["output"] = {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        }


def _distinct_turn_count(completions: list[dict]) -> int:
    """True assistant-turn count = distinct dialogueIds across all completions.

    A single user turn can spawn several completions (tool-call restarts), so
    completion_count over-counts; distinct dialogueId is one per assistant reply.
    Falls back to completion_count when dialogueId is absent.
    """
    dialogue_ids = {
        (c.get("metadata") or {}).get("dialogueId") for c in completions
    }
    dialogue_ids.discard(None)
    return len(dialogue_ids) if dialogue_ids else len(completions)


def build_conversation(
    api_key: str,
    project_id: str,
    conversation_id: str,
    completions: list[dict],
) -> dict:
    """Build the conversation record from its set of completions.

    Strategy: sort completions chronologically, fetch /messages for the latest
    (which carries the tail history), parse into turn pairs, then attach the
    latest completion's OUTPUT as the final assistant reply.

    `/messages` is capped at the prompter's `numberOfPreviousMessages`, so the
    reconstructed `turns` cover only the tail of a long conversation. The true
    length is reported via `total_turn_count` (distinct dialogueIds) and
    `transcript_truncated`, so callers don't mistake the tail window for the
    whole call.
    """
    completions.sort(key=lambda c: c.get("created") or 0)
    earliest = completions[0]
    latest = completions[-1]
    latest_id = latest["id"]

    messages = fetch_messages(api_key, project_id, latest_id)
    turns = messages_to_turns(
        messages,
        _iso(latest.get("created")),
        latest.get("metadata") or {},
    )
    _attach_latest_output(turns, latest)

    # total is the distinct-dialogueId count, but the completion set can be
    # time-windowed (--user-id) or capped while /messages still exposes the full
    # tail, so the reconstructed turns can outnumber it. Clamp so total never
    # reports fewer turns than were actually reconstructed.
    total_turns = max(_distinct_turn_count(completions), len(turns))

    return {
        "conversation_id": conversation_id,
        "started_at": _iso(earliest.get("created")),
        "ended_at": _iso(latest.get("created")),
        "completion_count": len(completions),
        "turn_count": len(turns),
        "total_turn_count": total_turns,
        "transcript_truncated": total_turns > len(turns),
        "latest_completion_id": latest_id,
        "turns": turns,
    }


BARGE_IN_PLACEHOLDER = "[reply not in history — likely voice barge-in]"


def _dialogue_calls(items: list[dict]) -> list[dict]:
    """The call that produced each assistant reply, oldest first.

    One per distinct dialogueId -- the last call in it, since tool-call restarts
    put several calls under one dialogue and only the final one carries the
    reply. Items without a dialogueId stand alone. `items` must be sorted.
    """
    last_by_key: dict[str, dict] = {}
    for idx, item in enumerate(items):
        did = (item.get("metadata") or {}).get("dialogueId")
        last_by_key[did or f"__nodialogue_{idx}"] = item
    return sorted(last_by_key.values(), key=lambda c: c.get("created") or 0)


def _newest_user_text(input_items: list[dict]) -> str | None:
    # `/input_items` is newest-first, so the first user item is the one the
    # call was answering.
    for item in input_items:
        if item.get("role") == "user":
            return _item_text(item)
    return None


def _fill_barge_in_turns(
    turns: list[dict],
    items: list[dict],
    source: dict,
    api_key: str | None,
    project_id: str | None,
) -> int:
    """Fill user turns whose reply the voice agent dropped. Returns the count.

    LiveKit removes an assistant reply from history when the user speaks again
    before it is spoken (a barge-in), so the next call's input carries two user
    turns back to back and `messages_to_turns` leaves the first with no output.
    The model did answer: that reply is still on the barged-in call's own stored
    response. So each such turn is mapped to its call by dialogue position,
    counting back from the transcript source; the mapping is accepted only when
    the call's newest input user message is this turn's text, then filled from
    its output and marked `interrupted`. Anything unresolved renders as
    `BARGE_IN_PLACEHOLDER` rather than `None`, which reads as an empty reply.
    """
    gaps = [i for i, t in enumerate(turns[:-1]) if t.get("output") is None]
    if not gaps:
        return 0
    # Same prompter as the source only: guardrails and sentinel calls share
    # the turn's user text, so one between the barged-in call and the source
    # would shift the position onto it and still pass the text check.
    prompter = (source.get("metadata") or {}).get("name")
    calls = _dialogue_calls(
        [c for c in items if (c.get("metadata") or {}).get("name") == prompter]
    )
    source_did = (source.get("metadata") or {}).get("dialogueId")
    anchor = next(
        (k for k, c in enumerate(calls)
         if c is source or (source_did and
                            (c.get("metadata") or {}).get("dialogueId") == source_did)),
        None,
    )
    for i in gaps:
        turn = turns[i]
        turn["interrupted"] = True
        text = None
        rid = None
        k = None if anchor is None else anchor - (len(turns) - 1 - i)
        if api_key and k is not None and k >= 0:
            rid = calls[k].get("id")
            try:
                if rid and _newest_user_text(
                    fetch_input_items(api_key, project_id, rid)
                ) == (turn.get("input") or {}).get("content"):
                    text = _output_text(fetch_response(api_key, project_id, rid)) or None
            except ResponsesReaderError as exc:
                print(f"# barge-in fill failed for {rid}: {exc}", file=sys.stderr)
        if text:
            turn["output"] = {"role": "assistant", "content": text, "tool_calls": None}
            turn["reply_source"] = rid
        else:
            turn["output"] = {
                "role": "assistant",
                "content": BARGE_IN_PLACEHOLDER,
                "tool_calls": None,
            }
            turn["reply_source"] = None
    return len(gaps)


def build_conversation_from_responses(
    conversation_id: str,
    items: list[dict],
    api_key: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Build a conversation record from Responses-API items.

    Mirrors ``build_conversation`` exactly, minus the fetch: ``responses_reader``
    has already put the input history on ``item["messages"]`` (the ``/messages``
    equivalent) and the output on ``choices``, so the same shaping applies. The
    one fetch here is `_fill_barge_in_turns`, and only for a turn the voice agent
    dropped the reply to; without ``api_key`` those render as a placeholder.

    The tail history comes from the **latest** item only. Every response's
    ``input_items`` carries the whole prior history, so concatenating per-item
    would duplicate the transcript once per call -- ~7x on a 14-call
    conversation, observed while verifying this against real data.
    """
    items.sort(key=lambda c: c.get("created") or 0)
    earliest = items[0]
    latest = items[-1]

    # The transcript comes from whichever item actually carries `messages`, not
    # from `latest` by assumption. `responses_reader` fetches /input_items for
    # only one item per conversation (they all carry the same full history, so
    # fetching the rest is waste) -- but it picks that item with its own
    # "latest" rule, and the same conversation can appear under two id casings,
    # so the two rules can disagree on a tie. Selecting on "has messages"
    # decouples them: whichever item was hydrated is the transcript source, and
    # a mismatch degrades to nothing rather than to a silently assistant-only
    # transcript. The trailing assistant reply still comes from the true latest.
    source = next(
        (i for i in reversed(items) if not i.get("_ledger_only")), latest
    )
    turns = messages_to_turns(
        source.get("messages") or [],
        _iso(latest.get("created")),
        latest.get("metadata") or {},
    )
    # Also from `source`, not `latest`: the non-fetched items are ledger stubs
    # with no `choices`, so attaching from `latest` would silently drop the
    # trailing assistant reply whenever the two differ.
    _attach_latest_output(turns, source)
    interrupted = _fill_barge_in_turns(turns, items, source, api_key, project_id)

    total_turns = max(_distinct_turn_count(items), len(turns))

    return {
        "conversation_id": conversation_id,
        "started_at": _iso(earliest.get("created")),
        "ended_at": _iso(latest.get("created")),
        "completion_count": len(items),
        "turn_count": len(turns),
        "total_turn_count": total_turns,
        "transcript_truncated": total_turns > len(turns),
        "latest_completion_id": latest.get("id"),
        "turns": turns,
        "interrupted_turn_count": interrupted,
        "openai_api": "responses",
    }


def _build_mixed_conversation(
    api_key: str,
    project_id: str,
    conversation_id: str,
    items: list[dict],
) -> dict:
    """Dispatch to the right builder for one conversation's items.

    A conversation is normally all-Completions or all-Responses, but it can be
    mixed: the arm is resolved per app run, so a reconnect after a relaunch can
    land the same conversation on the other API. The transcript tail is taken
    from whichever source produced the **latest** call, since that item carries
    the most complete history.
    """
    items.sort(key=lambda c: c.get("created") or 0)
    latest = items[-1]
    if latest.get("_source") == "responses":
        conv = build_conversation_from_responses(
            conversation_id, items, api_key, project_id
        )
    else:
        completions = [c for c in items if c.get("_source") != "responses"]
        conv = build_conversation(api_key, project_id, conversation_id, completions)
        conv["openai_api"] = "chatCompletions"

    apis = {
        "responses" if c.get("_source") == "responses" else "chatCompletions"
        for c in items
    }
    if len(apis) > 1:
        # Surface the split rather than hiding it -- a conversation spanning both
        # APIs means the user changed arm mid-conversation, which matters when
        # reading per-arm behaviour off a transcript.
        conv["openai_api"] = "mixed"
        conv["openai_apis"] = sorted(apis)
    return conv


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def group_by_conversation(items: list[dict]) -> dict[str, list[dict]]:
    """Group items by conversationId, matching case-insensitively.

    Grouping is case-insensitive because the same conversation can appear under
    both casings: the app owns upper-case UUIDs on the voice paths while other
    call sites emit lower-case, so a raw-string key silently splits one
    conversation into two. `_match_conv` already tolerates both casings; this
    keeps the grouper consistent with it.

    The returned key preserves the first-seen casing rather than normalising, so
    output is unchanged for the common single-casing case.
    """
    grouped: dict[str, list[dict]] = {}
    display: dict[str, str] = {}
    for it in items:
        md = it.get("metadata") or {}
        raw = (
            md.get("conversationId")
            or md.get("conversation_id")
            or "unknown"
        )
        key = raw.upper() if raw != "unknown" else raw
        display.setdefault(key, raw)
        grouped.setdefault(key, []).append(it)
    return {display[k]: v for k, v in grouped.items()}


def apply_window(
    conv_ids_sorted: list[str],
    window_spec: dict,
) -> list[str]:
    kind = window_spec.get("kind")
    if kind == "last_conversation":
        return conv_ids_sorted[:1]
    if kind == "last_n":
        return conv_ids_sorted[: window_spec["n"]]
    return conv_ids_sorted


def run_user_id(
    api_key: str,
    project_id: str,
    user_id: str,
    window: str,
) -> dict:
    window_spec = parse_window(window)

    # `last_conversation` and `last_n` apply at the conversation level. Walk
    # 30 days as the fetch ceiling so we have enough conversations to pick from.
    if window_spec["kind"] == "days":
        since_unix = window_spec["since_unix"]
    else:
        since_unix = int(
            (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        )

    # Early-stop only on the `last_conversation` fast path -- once we've
    # found a hit for this user and walked 10 pages past it, the user's most
    # recent conversation is fully captured. For `last_n` and full-window
    # walks we need the entire window, so no early stop.
    early_stop = 10 if window_spec.get("kind") == "last_conversation" else None
    # The prompter check is part of the match, not a post-filter: a side-effect
    # prompter that slipped through would otherwise satisfy `early_stop`'s
    # "found a hit for this user" and could end the walk before a real
    # conversation was reached.
    matches = walk_completions(
        api_key,
        project_id,
        since_unix,
        lambda it: _match_user(it, user_id) and _is_conversation_prompter(it),
        early_stop_pages_after_first_match=early_stop,
        metadata_filter={"metadata[user]": user_id},
    )

    # Responses-API conversations are invisible to the walk above: `GET
    # /v1/responses` (the list form) rejects API keys ("must be made with a
    # session key"), so stored responses are readable but not enumerable. The
    # llmCall ledger is the per-user index instead. Both surfaces are read and
    # merged, because a user can be split across arms mid-window.
    responses_diag: dict[str, Any] = {}
    try:
        responses_result = fetch_responses_conversations(
            api_key, project_id, user_id, since_unix
        )
        matches = matches + responses_result["items"]
        responses_diag = {
            "ledger_row_count": responses_result["ledger_row_count"],
            "responses_row_count": responses_result["responses_row_count"],
            "unfetchable_count": len(responses_result["unfetchable"]),
        }
        if responses_result["unfetchable"]:
            print(
                f"# WARNING: {len(responses_result['unfetchable'])} Responses "
                "object(s) were in the ledger but could not be fetched from "
                "OpenAI -- the transcript is incomplete, not empty.",
                file=sys.stderr,
            )
    except ResponsesReaderError as exc:
        # Never let the Responses path break a run that the completions path can
        # still serve. But say so loudly: silently returning Completions-only
        # results looks like a complete answer.
        responses_diag = {"error": str(exc)[:300]}
        print(
            f"# WARNING: could not read Responses-API conversations ({exc}). "
            "Results below cover the Chat-Completions half ONLY and may be "
            "incomplete for any user on the aiResponseAPI treatment arm.",
            file=sys.stderr,
        )

    if not matches:
        bt_token = os.environ.get("BRAINTRUST_API_KEY")
        if bt_token:
            print(
                "# OpenAI returned 0 matches; trying Braintrust fallback "
                f"(Conversation project, user={user_id[:8]}…)…",
                file=sys.stderr,
            )
            bt_rows = _bt_fetch_by_user(bt_token, user_id, since_unix)
            if len(bt_rows) >= 200:
                print(
                    "# WARNING: Braintrust fallback hit the 200-span cap — "
                    "oldest conversations may be missing.",
                    file=sys.stderr,
                )
            if bt_rows:
                print("# Recovered from Braintrust fallback.", file=sys.stderr)
                by_conv_bt = group_by_conversation(bt_rows)

                def _bt_ended_at(cid: str) -> int:
                    return max(_bt_iso_to_unix(r.get("created")) for r in by_conv_bt[cid])

                conv_ids_sorted_bt = sorted(by_conv_bt.keys(), key=_bt_ended_at, reverse=True)
                conv_ids_picked_bt = apply_window(conv_ids_sorted_bt, window_spec)
                conversations = [
                    _bt_build_conversation(cid, by_conv_bt[cid])
                    for cid in conv_ids_picked_bt
                ]
                return {
                    "user_id": user_id,
                    "window": window_spec,
                    "conversation_count": len(conversations),
                    "turn_count": sum(c["turn_count"] for c in conversations),
                    "total_turn_count": sum(
                        c.get("total_turn_count", c["turn_count"])
                        for c in conversations
                    ),
                    "transcript_truncated": any(
                        c.get("transcript_truncated") for c in conversations
                    ),
                    "conversations": conversations,
                    "source": "braintrust_fallback",
                }
            print(
                "# Braintrust fallback also returned no conversations for this user.",
                file=sys.stderr,
            )
        # Distinguish "no conversations exist" from "conversations exist but
        # could not be read". The ledger is the oracle: rows there with nothing
        # fetchable is a retrieval failure, and reporting it as an empty result
        # is the exact bug this path was rewritten to avoid.
        ledger_rows = responses_diag.get("responses_row_count") or 0
        unfetchable = responses_diag.get("unfetchable_count") or 0
        if responses_diag.get("error"):
            hint = (
                "Zero conversations returned, but the Responses-API surface "
                f"could not be read at all ({responses_diag['error']}). This is "
                "NOT evidence the user had no conversations -- only the "
                "Chat-Completions half was checked. Fix the ledger read "
                "(GCP_KEY, BigQuery llm.llm_call) and re-run before concluding anything."
            )
        elif ledger_rows and unfetchable >= ledger_rows:
            hint = (
                f"Zero conversations returned, but the llmCall ledger lists "
                f"{ledger_rows} Responses-API call(s) for this user in the "
                f"window and all {unfetchable} stored object(s) failed to "
                "fetch. The conversations exist; retrieval failed. Do NOT read "
                "this as an inactive user."
            )
        else:
            hint = (
                "Zero completions matched, and the ledger lists no "
                "Responses-API conversation calls either, so this looks like a "
                "genuinely inactive window. Check: (1) the UID is correct, "
                "(2) the user actually used a coach/quick-processing prompter "
                "in the window (other prompters live in different OpenAI "
                "projects), (3) the window isn't pre-2026-05-12 (subscription "
                "metadata tagging started on that date; pre-rollout traffic "
                "doesn't carry metadata.user)."
            )
        return {
            "user_id": user_id,
            "window": window_spec,
            "conversation_count": 0,
            "turn_count": 0,
            "conversations": [],
            "responses_source": responses_diag,
            "hint": hint,
        }

    by_conv = group_by_conversation(matches)

    # Sort conversations by ended_at (latest completion in each), most-recent first.
    def _ended_at(conv_id: str) -> int:
        return max((c.get("created") or 0) for c in by_conv[conv_id])

    conv_ids_sorted = sorted(by_conv.keys(), key=_ended_at, reverse=True)
    conv_ids_picked = apply_window(conv_ids_sorted, window_spec)

    conversations = [
        _build_mixed_conversation(api_key, project_id, cid, by_conv[cid])
        for cid in conv_ids_picked
    ]

    return {
        "user_id": user_id,
        "window": window_spec,
        "conversation_count": len(conversations),
        "turn_count": sum(c["turn_count"] for c in conversations),
        "total_turn_count": sum(
            c.get("total_turn_count", c["turn_count"]) for c in conversations
        ),
        "transcript_truncated": any(
            c.get("transcript_truncated") for c in conversations
        ),
        "conversations": conversations,
        # Which API each conversation ran on, so a reader can tell a
        # Completions-only result from a genuinely complete one.
        "api_breakdown": {
            api: sum(1 for c in conversations if c.get("openai_api") == api)
            for api in sorted({c.get("openai_api") or "chatCompletions"
                               for c in conversations})
        },
        "responses_source": responses_diag,
    }


def run_conversation_id(
    api_key: str,
    project_id: str,
    conversation_id: str,
) -> dict:
    since_unix = int(
        (datetime.now(timezone.utc) - timedelta(days=CONVERSATION_ID_WALK_CEILING_DAYS)).timestamp()
    )
    # Server-side `metadata[conversationId]` filter: OpenAI returns only this
    # conversation's completions, so the walk exhausts in one or two pages
    # (a conversation is a handful of completions) regardless of how busy the
    # project is -- near-instant vs the old full-project walk.
    #
    # Case fallback: coach conversationIds are stored as upper-case UUIDs, but a
    # caller might paste a lower-case variant. The server-side filter is
    # exact-match, so if the as-given query returns nothing we retry with the
    # opposite case before concluding "not found". The client-side `_match_conv`
    # still accepts either case among whatever the server returns.
    def _walk_with_conv(cid: str) -> list[dict]:
        return walk_completions(
            api_key,
            project_id,
            since_unix,
            lambda it: _match_conv(it, conversation_id),
            early_stop_pages_after_first_match=5,
            metadata_filter={"metadata[conversationId]": cid},
        )

    matches = _walk_with_conv(conversation_id)
    if not matches:
        for variant in (conversation_id.upper(), conversation_id.lower()):
            if variant != conversation_id:
                matches = _walk_with_conv(variant)
                if matches:
                    break

    # Same reasoning as run_user_id: Responses-API calls are not enumerable on
    # OpenAI, so the ledger indexes them. Here the pivot is conversationId,
    # which the ledger also filters on.
    responses_diag: dict[str, Any] = {}
    try:
        responses_result = fetch_responses_for_conversation(
            api_key, project_id, conversation_id
        )
        matches = matches + responses_result["items"]
        responses_diag = {
            "ledger_row_count": responses_result["ledger_row_count"],
            "responses_row_count": responses_result["responses_row_count"],
            "unfetchable_count": len(responses_result["unfetchable"]),
        }
        if responses_result["unfetchable"]:
            print(
                f"# WARNING: {len(responses_result['unfetchable'])} Responses "
                "object(s) were in the ledger but could not be fetched -- the "
                "transcript is incomplete, not empty.",
                file=sys.stderr,
            )
    except ResponsesReaderError as exc:
        responses_diag = {"error": str(exc)[:300]}
        print(
            f"# WARNING: could not read Responses-API calls ({exc}). Results "
            "cover the Chat-Completions half ONLY.",
            file=sys.stderr,
        )

    if not matches:
        bt_token = os.environ.get("BRAINTRUST_API_KEY")
        if bt_token:
            print(
                "# OpenAI returned 0 matches; trying Braintrust fallback "
                f"(Conversation project, conversationId={conversation_id})…",
                file=sys.stderr,
            )
            bt_rows = _bt_fetch_by_conversation_id(bt_token, conversation_id)
            if len(bt_rows) >= _BT_CONV_ID_LIMIT:
                print(
                    "# WARNING: Braintrust fallback hit the "
                    f"{_BT_CONV_ID_LIMIT}-span conversationId cap — total_turn_count "
                    "may understate a long conversation.",
                    file=sys.stderr,
                )
            if bt_rows:
                print("# Recovered from Braintrust fallback.", file=sys.stderr)
                conversation_bt = _bt_build_conversation(conversation_id, bt_rows)
                return {
                    "conversation_id": conversation_id,
                    "conversation_count": 1,
                    "turn_count": conversation_bt["turn_count"],
                    "total_turn_count": conversation_bt.get(
                        "total_turn_count", conversation_bt["turn_count"]
                    ),
                    "transcript_truncated": conversation_bt.get(
                        "transcript_truncated", False
                    ),
                    "conversations": [conversation_bt],
                    "source": "braintrust_fallback",
                }
            print(
                "# Braintrust fallback also returned no spans for this conversationId.",
                file=sys.stderr,
            )
        # Same three-way distinction as the user pivot: a Responses-only
        # conversation whose ledger read failed, or whose stored objects would
        # not fetch, must NOT be reported as "wrong id / too old".
        ledger_rows = responses_diag.get("responses_row_count") or 0
        unfetchable = responses_diag.get("unfetchable_count") or 0
        if responses_diag.get("error"):
            hint = (
                "Zero completions matched, and the Responses-API surface could "
                f"not be read at all ({responses_diag['error']}). This is NOT "
                "evidence the conversation is missing -- only the "
                "Chat-Completions half was checked. Fix the ledger read "
                "(GCP_KEY, BigQuery llm.llm_call) and re-run before concluding anything."
            )
        elif ledger_rows and unfetchable >= ledger_rows:
            hint = (
                f"Zero conversations returned, but the llmCall ledger lists "
                f"{ledger_rows} Responses-API call(s) for this conversationId "
                f"and all {unfetchable} stored object(s) failed to fetch. The "
                "conversation exists; retrieval failed."
            )
        else:
            hint = (
                "Zero completions matched conversationId, and the ledger lists "
                "no Responses-API calls for it either. Either the ID is "
                f"wrong, or the conversation is older than {CONVERSATION_ID_WALK_CEILING_DAYS} "
                "days (the conversation-pivot walk ceiling)."
            )
        return {
            "conversation_id": conversation_id,
            "conversation_count": 0,
            "turn_count": 0,
            "conversations": [],
            "responses_source": responses_diag,
            "hint": hint,
        }

    conversation = _build_mixed_conversation(
        api_key, project_id, conversation_id, matches
    )

    return {
        "conversation_id": conversation_id,
        "conversation_count": 1,
        "turn_count": conversation["turn_count"],
        "total_turn_count": conversation.get("total_turn_count", conversation["turn_count"]),
        "transcript_truncated": conversation.get("transcript_truncated", False),
        "conversations": [conversation],
        "openai_api": conversation.get("openai_api"),
        "responses_source": responses_diag,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser()
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--user-id", help="Firebase UID")
    target.add_argument(
        "--conversation-id",
        help=(
            "Conversation UUID. Bypasses user-id walking and pulls every "
            "completion with this conversationId."
        ),
    )
    ap.add_argument(
        "--window",
        default=None,
        help=(
            "30d (default for --user-id) | 7d | NNd | last_conversation | "
            "last_n=<int>. Ignored when --conversation-id is set."
        ),
    )
    ap.add_argument(
        "--project-id",
        default=CONVERSATION_PROJECT_ID,
        help=(
            f"OpenAI project ID (default: Conversation [Production] "
            f"{CONVERSATION_PROJECT_ID}). Override only when analysing a "
            "non-Conversation prompter."
        ),
    )
    ap.add_argument("--json", dest="json_out", help="Write JSON output here.")
    args = ap.parse_args()

    # The library path raises FetchLogsError (not SystemExit) so in-process
    # batch callers can isolate a bad UID; as the CLI, map it back to a
    # non-zero exit with the same stderr message sys.exit() used to print.
    try:
        api_key = _resolve_api_key(args.project_id)

        if args.conversation_id:
            if args.window is not None:
                print(
                    f"# Note: --window {args.window!r} is ignored when --conversation-id is set.",
                    file=sys.stderr,
                )
            report = run_conversation_id(api_key, args.project_id, args.conversation_id)
        else:
            report = run_user_id(
                api_key,
                args.project_id,
                args.user_id,
                args.window or "60d",
            )
    except FetchLogsError as exc:
        sys.exit(str(exc))

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Wrote {args.json_out}")
        if args.conversation_id:
            conv0 = report["conversations"][0] if report["conversations"] else {}
            trunc = " (tail only — full conversation is longer)" if report.get("transcript_truncated") else ""
            print(
                f"  conversation={args.conversation_id}  "
                f"turns={report['turn_count']} of {report.get('total_turn_count', report['turn_count'])}{trunc}  "
                f"completions={conv0.get('completion_count', 0)}"
            )
        else:
            print(
                f"  user={args.user_id}  window={args.window or '30d'}  "
                f"conversations={report['conversation_count']}  "
                f"turns={report['turn_count']}"
            )
    else:
        json.dump(report, sys.stdout, indent=2, default=str)
        print()


if __name__ == "__main__":
    main()
