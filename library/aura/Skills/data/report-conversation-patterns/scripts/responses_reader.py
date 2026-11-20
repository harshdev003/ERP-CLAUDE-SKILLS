#!/usr/bin/env python3
"""Read Aurie conversations that ran on the OpenAI **Responses** API.

Why this exists
---------------
`fetch_logs.py` finds conversations by *walking* OpenAI stored chat-completions
(`GET /v1/chat/completions` + metadata filters). That is impossible for the
Responses API: `GET /v1/responses` (the list form) rejects API keys outright --

    HTTP 401: "must be made with a session key (that is, it can only be made
               from the browser). You made it with the following key type: ."

so stored responses are *readable but not enumerable*. Individual objects fetch
fine by id (`GET /v1/responses/{id}`), there is just no way to ask OpenAI "which
responses belong to this user".

The missing index is Aura-side: IntelligenceService's `llmCall` ledger records
one row per LLM call with `openaiId`, `openaiAPI`, `prompterName`, `project` and
`conversationId`. So the access pattern inverts -- ledger first, OpenAI second:

    llmCall ledger (BigQuery) by userId   -> resp_ ids for this user
    GET /v1/responses/{id}                -> the assistant output
    GET /v1/responses/{id}/input_items    -> the prior turns (user + assistant)
    group by conversationId               -> conversations

The same shape is already proven in `eng:investigate-memory-ingestion-guardrail`
(`fetch_guardrail.py` -> `fetch_ledger_row()` "Rung 2" + `_response_to_completion`),
which had to solve this when the memory prompters moved to Responses.

**Both halves matter.** `GET /v1/responses/{id}` returns `input: null` -- the
user's turns are *only* on `/input_items`. Reading just the response object
yields an assistant-only transcript, which looks like it worked.

Emits chat-completion-shaped dicts so the existing renderers/parsers in
`fetch_logs.py` consume them unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import pathlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import requests

import llm_ledger


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


OPENAI_API = "https://api.openai.com/v1"

# Prompters whose output reaches the user as a conversation turn. That question
# -- does the user see it? -- is the whole membership test. The ledger also
# carries side-effect prompters (memory, summary, profile, memory-sorting, ...)
# whose output the user never sees; they are not transcript turns and would
# pollute the turn count.
#
# `guardrails-handler-prompter` belongs here: it is the reply shown when a
# guardrail intercepts, and k8bot `*CHAT MESSAGE RATED*` messages rate exactly
# those replies.
#
# Quick Processing runs as two prompters (IntelligenceService
# `quickProcessingTypes`): the plain one and the sentinel one. Both are turns;
# omitting either returns 0 turns for every conversation that flavour served.
#
# One set serves both pivots. Narrowing it per-pivot would make the user pivot
# under-report turns the conversation pivot returns.
CONVERSATION_PROMPTERS = frozenset({
    "chat-prompter",
    "coach-prompter",
    "companion-prompter",
    "guardrails-handler-prompter",
    "quick-processing-prompter",
    "quick-processing-sentinel-prompter",
})

# The journal surface is a single-shot prompter in its own OpenAI project, not a
# conversation turn, so it gets its own set rather than joining the one above.
JOURNAL_PROMPTERS = frozenset({"journal-prompter"})

_MAX_RETRIES = 4


class ResponsesReaderError(Exception):
    """Raised for unrecoverable auth/transport problems."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _get_json(url: str, headers: dict[str, str], params: dict | None = None) -> dict:
    """GET with retry on 429/5xx. Returns parsed JSON.

    Retries are deliberate rather than fail-fast: a per-id fan-out over a
    chatty user is one request per call (two, with input items), so a single
    429 partway through shouldn't lose the run.
    """
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=60)
        except requests.RequestException as exc:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                last_err = exc
                continue
            raise ResponsesReaderError(f"network error on {url}: {exc}") from exc
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES - 1:
            time.sleep(2 ** attempt)
            last_err = RuntimeError(f"HTTP {resp.status_code}")
            continue
        if resp.status_code >= 400:
            raise ResponsesReaderError(
                f"HTTP {resp.status_code} on {url}: {resp.text[:400]}"
            )
        return resp.json()
    raise ResponsesReaderError(f"exhausted retries on {url}: {last_err}")


def _openai_headers(api_key: str, project_id: str | None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {api_key}"}
    if project_id:
        h["OpenAI-Project"] = project_id
    return h


# ---------------------------------------------------------------------------
# Step 1 -- the ledger is the index
# ---------------------------------------------------------------------------

def fetch_ledger_rows(
    user_id: str,
    since_unix: int | None = None,
    limit: int = 1000,
) -> list[dict]:
    """Return `llmCall` ledger rows for one user, newest-first.

    The ledger is Aura-side, so it stays reachable during an OpenAI outage and
    doubles as an existence oracle: rows here with no fetchable OpenAI object
    mean "stored object gone", not "user had no conversations" -- a distinction
    the caller must preserve rather than collapsing to an empty result.
    """
    params: dict[str, Any] = {"userId": user_id, "limit": limit}
    # Bound the window in the query. Without this we read the user's most recent
    # calls across all prompters and filter afterwards, so a busy user's
    # side-effect traffic (memory, summary, profile, ...) crowds the conversation
    # rows out of the page and those chats stay invisible.
    if since_unix is not None:
        params["from"] = datetime.fromtimestamp(
            since_unix, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = _ledger(params)
    if since_unix is not None:
        rows = [r for r in rows if _row_unix(r) >= since_unix]
    if len(rows) >= limit:
        # A full page means there may be more the caller never sees. Say so --
        # silently returning a capped set reads as a complete history.
        print(
            f"# WARNING: ledger returned a full page ({limit} rows) for this "
            "window; older calls may be missing. Narrow the window or raise "
            "`limit`.",
            file=sys.stderr,
        )
    return rows


def _ledger(params: dict) -> list[dict]:
    """The ledger read, from BigQuery. A failure raises rather than reading as no rows."""
    try:
        return llm_ledger.rows(params)
    except llm_ledger.LedgerError as e:
        raise ResponsesReaderError(str(e)) from e


def _row_unix(row: dict) -> int:
    raw = row.get("createdAt") or ""
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError):
        return 0


def responses_rows(
    rows: Iterable[dict],
    prompters: frozenset[str] = CONVERSATION_PROMPTERS,
) -> list[dict]:
    """Keep only rows that ran on the Responses API for the given prompters.

    `prompters` is a parameter rather than a constant so the same reader serves
    other surfaces -- e.g. the journal skill passes `{"journal-prompter"}`.
    Without a filter the ledger's side-effect prompters (memory, summary,
    profile, ...) would be pulled in as if they were transcript turns.

    The default set is the same on both pivots -- the prompters whose output the
    user sees -- so nothing that reached the user is filtered out on either. Do
    not narrow it per-pivot; see the note on `CONVERSATION_PROMPTERS`.
    """
    out = []
    for r in rows:
        if r.get("openaiAPI") != "responses":
            continue
        if r.get("prompterName") not in prompters:
            continue
        if not str(r.get("openaiId") or "").startswith("resp_"):
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Step 2 -- fetch the stored objects by id
# ---------------------------------------------------------------------------

def fetch_response(api_key: str, project_id: str | None, resp_id: str) -> dict:
    return _get_json(
        f"{OPENAI_API}/responses/{resp_id}",
        _openai_headers(api_key, project_id),
    )


def fetch_input_items(
    api_key: str,
    project_id: str | None,
    resp_id: str,
    limit: int = 100,
) -> list[dict]:
    """Return **all** the response's input items -- the prior turns.

    This is the *only* source of the user's messages: the response object's own
    `input` field comes back null.

    OpenAI caps this endpoint at 100 items per page and paginates with `after`,
    so a single request silently truncates a long conversation to its most recent
    100 items while still looking complete. Mirrors the `after`/`has_more` loop
    `fetch_logs.fetch_messages` already uses for `/chat/completions/{id}/messages`.
    """
    items: list[dict] = []
    after: str | None = None
    while True:
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        data = _get_json(
            f"{OPENAI_API}/responses/{resp_id}/input_items",
            _openai_headers(api_key, project_id),
            params,
        )
        page = data.get("data") or []
        items.extend(page)
        if not data.get("has_more") or not page:
            break
        last_id = page[-1].get("id")
        if not last_id:
            # No cursor to continue from; stop rather than loop forever.
            break
        after = last_id
    return items


# ---------------------------------------------------------------------------
# Step 3 -- adapt to the chat-completion shape the renderers expect
# ---------------------------------------------------------------------------

def _item_text(item: dict) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") in ("output_text", "input_text", "text"):
            t = c.get("text")
            if t:
                parts.append(t)
    return "\n".join(parts)


def _output_text(resp: dict) -> str:
    parts = []
    for out in resp.get("output") or []:
        if out.get("type") != "message":
            continue
        parts.append(_item_text(out))
    return "\n".join(p for p in parts if p)


def response_to_completion(resp: dict, row: dict, input_items: list[dict]) -> dict:
    """Adapt a `/v1/responses` object into a chat-completion-shaped dict.

    Mirrors `_response_to_completion` in `fetch_guardrail.py` but additionally
    reconstructs `messages` from `/input_items`, which that one does not need.

    Two shape decisions make this a drop-in for the completions path:

    * `messages` holds the **input only**, exactly like `GET
      /chat/completions/{id}/messages`. The response's own output is *not*
      appended, so `messages_to_turns` leaves the trailing user turn unanswered
      and `_attach_latest_output` fills it from `choices` -- identical control
      flow to the completions path. Appending it here instead would have the
      output arrive twice.
    * `choices[0].message` carries the output, which is where
      `_completion_output_message` looks.

    `input_items` arrives newest-first from OpenAI; the transcript wants
    oldest-first, so it is reversed.
    """
    usage = resp.get("usage") or {}
    messages: list[dict] = []
    for item in reversed(input_items):
        role = item.get("role")
        if role not in ("system", "user", "assistant"):
            # function_call / function_call_output carry no transcript text --
            # kept out of turns, same as tool messages on the completions path.
            continue
        text = _item_text(item)
        if text:
            messages.append({"role": role, "content": text})

    final = _output_text(resp)

    return {
        "id": resp.get("id") or row.get("openaiId"),
        "created": _row_unix(row) or resp.get("created_at"),
        "model": resp.get("model") or row.get("model"),
        "metadata": {
            "user": row.get("userId"),
            "conversationId": row.get("conversationId"),
            "name": row.get("prompterName"),
            # `_distinct_turn_count` counts distinct dialogueIds to get the true
            # assistant-turn count, so carry it through from the ledger.
            "dialogueId": row.get("dialogueId"),
        },
        "usage": {
            "prompt_tokens": usage.get("input_tokens") or row.get("inputTokens"),
            "completion_tokens": usage.get("output_tokens") or row.get("outputTokens"),
            "total_tokens": usage.get("total_tokens") or row.get("totalTokens"),
        },
        "messages": messages,
        "choices": [{"message": {"role": "assistant", "content": final}}],
        "_source": "responses",
        "_openai_api": "responses",
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _hydrate(
    api_key: str,
    project_id: str | None,
    picked: list[dict],
    verbose: bool = False,
    input_items_for: str = "latest_per_conversation",
) -> tuple[list[dict], list[dict]]:
    """Fetch the stored object for each ledger row. Returns (items, unfetchable).

    A row whose object won't fetch is recorded rather than dropped, so callers
    can tell "no conversations" from "conversations exist but were unreadable".

    `input_items_for` controls the expensive half:

    * ``"latest_per_conversation"`` (default) -- fetch `/input_items` only for the
      newest call in each conversation. Every response's input items carry the
      WHOLE prior history, and `build_conversation_from_responses` already takes
      the transcript from the latest item alone, so fetching the rest was pure
      waste: two round-trips per call where one plus one per conversation does.
      On a heavy user that was the difference between a 1-day window finishing in
      ~40s and a 7-day window not finishing at all. When the newest call will not
      fetch, the next-newest in that conversation is tried in its place, so one
      unreadable response costs the last turn rather than the whole transcript.
    * ``"all"`` -- fetch for every row. Required when each row is its own record
      rather than a turn in a shared conversation (the journal skill builds one
      record per call and needs each one's own input for its fuzzy match).
      Journals happen to be 1:1 with conversationId today, so grouping would not
      break them in practice -- but that is an accident of how they are keyed,
      not a guarantee, so the caller states it rather than relying on it.
    """
    # Pass 1 -- decide, from the ledger alone, which rows need an OpenAI fetch.
    # Doing this BEFORE any request is the whole optimisation: only the
    # transcript source of each conversation is fetched.
    if input_items_for == "all":
        # Every row is its own record and needs its own fetch. One-row groups,
        # so the promote-on-failure loop below is a no-op here.
        groups = [[idx] for idx in range(len(picked))]
    else:
        by_conversation: dict[str, list[int]] = {}
        for idx, row in enumerate(picked):
            # Match group_by_conversation: case-insensitive, and a row with no
            # conversationId is its own group rather than lumped under "".
            raw = row.get("conversationId")
            key = raw.upper() if raw else f"__unkeyed_{idx}"
            by_conversation.setdefault(key, []).append(idx)
        # Newest first: that call's input items carry the whole history, so it
        # is the preferred transcript source. The rest are fallbacks.
        groups = [
            sorted(idxs, key=lambda i: _row_unix(picked[i]), reverse=True)
            for idxs in by_conversation.values()
        ]
        if verbose:
            print(
                f"# fetching 1 row per conversation -- {len(groups)} of "
                f"{len(picked)}; the rest come from the ledger",
                file=sys.stderr,
            )

    # Pass 2 -- fetch one row per group, falling down the group when a fetch
    # fails. The fallback matters: fetching only the newest call means a single
    # failed response would otherwise leave that conversation as nothing but
    # ledger stubs -- an empty transcript, strictly worse than the next-latest
    # history that is still readable and was still shown before this became a
    # one-fetch-per-conversation path.
    fetched: dict[int, dict] = {}
    failed: set[int] = set()
    unfetchable: list[dict] = []
    for candidates in groups:
        for idx in candidates:
            rid = picked[idx]["openaiId"]
            try:
                fetched[idx] = fetch_response(api_key, project_id, rid)
            except ResponsesReaderError as exc:
                failed.add(idx)
                unfetchable.append({"openaiId": rid, "error": str(exc)[:200]})
                if verbose:
                    print(f"# unfetchable {rid[:24]}…: {exc}", file=sys.stderr)
                continue
            break

    items: list[dict] = []
    for idx, row in enumerate(picked):
        if idx in failed:
            # Attempted and unreadable. Dropped rather than stubbed so it stays
            # visible in `unfetchable` only, matching how an unreadable row has
            # always been reported.
            continue
        resp = fetched.get(idx)
        if resp is None:
            # Nothing on this row is ever read from OpenAI. The transcript comes
            # from the winner's input_items, and the trailing assistant reply
            # from the winner's output; everything else this doc contributes --
            # `created` (ordering), `dialogueId` (the distinct-turn count),
            # `conversationId`, `model`, tokens -- is already on the ledger row.
            # So synthesise it and skip the fetch entirely.
            items.append(_row_to_stub(row))
            continue
        inputs: list[dict] = []
        err: str | None = None
        try:
            inputs = fetch_input_items(api_key, project_id, row["openaiId"])
        except ResponsesReaderError as exc:
            err = str(exc)[:200]
            print(
                f"# WARNING: could not fetch input_items for "
                f"{row['openaiId'][:24]}… ({exc}). That conversation's "
                "transcript is the assistant side ONLY.",
                file=sys.stderr,
            )
        doc = response_to_completion(resp, row, inputs)
        if err:
            doc["_messages_error"] = err
        items.append(doc)
    return items, unfetchable


def _row_to_stub(row: dict) -> dict:
    """A completion-shaped doc built from the ledger row alone, no OpenAI call.

    Used for the calls in a conversation that are not the transcript source.
    Carries no output text and no messages by design -- if one of these is ever
    treated as the transcript source it yields an empty section, which the
    caller can see, rather than a plausible-looking partial one it cannot.
    """
    return {
        "id": row.get("openaiId"),
        "created": _row_unix(row),
        "model": row.get("model"),
        "metadata": {
            "user": row.get("userId"),
            "conversationId": row.get("conversationId"),
            "name": row.get("prompterName"),
            "dialogueId": row.get("dialogueId"),
        },
        "usage": {
            "prompt_tokens": row.get("inputTokens"),
            "completion_tokens": row.get("outputTokens"),
            "total_tokens": row.get("totalTokens"),
        },
        "messages": [],
        "choices": [],
        "_source": "responses",
        "_openai_api": "responses",
        "_ledger_only": True,
    }


def fetch_responses_conversations(
    api_key: str,
    project_id: str | None,
    user_id: str,
    since_unix: int | None = None,
    verbose: bool = False,
    prompters: frozenset[str] = CONVERSATION_PROMPTERS,
    input_items_for: str = "latest_per_conversation",
) -> dict[str, Any]:
    """Return completion-shaped items for a user's Responses-API calls.

    The `unfetchable` count is load-bearing: it separates "the user had no
    conversations" from "the ledger knew about N calls but the stored objects
    could not be read". Collapsing those into an empty list is the bug this
    module exists to avoid.
    """
    ledger = fetch_ledger_rows(user_id, since_unix)
    picked = responses_rows(ledger, prompters)
    if verbose:
        print(
            f"# ledger: {len(ledger)} rows, {len(picked)} matching prompter "
            f"rows on the Responses API",
            file=sys.stderr,
        )
    items, unfetchable = _hydrate(
        api_key, project_id, picked, verbose, input_items_for
    )
    return {
        "items": items,
        "ledger_row_count": len(ledger),
        "responses_row_count": len(picked),
        "unfetchable": unfetchable,
    }


def fetch_ledger_rows_by_conversation(
    conversation_id: str,
    limit: int = 500,
) -> list[dict]:
    """Return ledger rows for one conversationId, merged across UUID casings.

    The ledger's `conversationId` filter is **exact-match and case-sensitive**,
    and Aurie stores upper-case UUIDs on the voice paths while other call sites
    emit lower-case. The part that makes this more than a pick-the-right-one
    problem: **one conversation can hold rows under both casings at the same
    time**, because different call sites write it differently within a single
    session. So every distinct casing is queried and the results are merged.

    Returning the first casing that yields anything is wrong and fails
    silently. Observed on a real conversation: the lower-case form -- which is
    what k8bot rating footers carry -- returned 3 rows, all of them side-effect
    prompters, while the upper-case form held the other 23 including the whole
    user-facing transcript. `responses_rows` then filters those 3 out, so the
    caller gets an empty transcript rather than an error, and empty is
    indistinguishable from a conversation that genuinely had no turns.

    Dedup is on `openaiId`. The observed overlap between casings was zero, but
    nothing guarantees a row cannot come back from more than one variant.
    """
    seen: list[str] = []
    merged: dict[str, dict] = {}
    unkeyed: list[dict] = []
    for variant in (conversation_id, conversation_id.upper(), conversation_id.lower()):
        if variant in seen:
            continue
        seen.append(variant)
        for row in _ledger({"conversationId": variant, "limit": limit}):
            rid = row.get("openaiId")
            if not rid:
                # No id to dedup on -- keep it rather than drop it; a row the
                # ledger returned is evidence even when it can't be keyed.
                unkeyed.append(row)
            elif rid not in merged:
                merged[rid] = row
    return list(merged.values()) + unkeyed


def fetch_responses_for_conversation(
    api_key: str,
    project_id: str | None,
    conversation_id: str,
    verbose: bool = False,
) -> dict[str, Any]:
    """Same as ``fetch_responses_conversations`` but pivoted on conversationId."""
    ledger = fetch_ledger_rows_by_conversation(conversation_id)
    picked = responses_rows(ledger)
    if verbose:
        print(
            f"# ledger: {len(ledger)} rows for conversation {conversation_id}, "
            f"{len(picked)} on the Responses API",
            file=sys.stderr,
        )
    items, unfetchable = _hydrate(api_key, project_id, picked, verbose)
    return {
        "items": items,
        "ledger_row_count": len(ledger),
        "responses_row_count": len(picked),
        "unfetchable": unfetchable,
    }


def main() -> None:
    # Must be first: this module prints non-ASCII (the … in truncated ids), which
    # raises UnicodeEncodeError on a Windows cp1252 console otherwise. Same guard
    # as every other Python entrypoint in this repo.
    sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(
        description="Probe: read a user's Responses-API conversations via the ledger."
    )
    ap.add_argument("--user", required=True, help="Firebase UID")
    ap.add_argument("--days", type=int, default=7, help="lookback window (default 7)")
    ap.add_argument("--project", default=os.environ.get("OPENAI_CONVERSATION_PROJECT_ID"))
    ap.add_argument("--json", action="store_true", help="emit the full payload")
    args = ap.parse_args()

    api_key = os.environ.get("OPENAI_CONVERSATION_API_KEY")
    if not api_key:
        raise SystemExit(
            "OPENAI_CONVERSATION_API_KEY is not set. Stored-object reads need the "
            "project-scoped service-account key; the Admin key cannot hold the "
            "required scope."
        )

    since = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp())
    result = fetch_responses_conversations(
        api_key, args.project, args.user, since, verbose=True
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    by_conv: dict[str, list[dict]] = {}
    for it in result["items"]:
        by_conv.setdefault((it["metadata"] or {}).get("conversationId") or "unknown", []).append(it)

    print(f"ledger rows          : {result['ledger_row_count']}")
    print(f"responses rows        : {result['responses_row_count']}")
    print(f"fetched               : {len(result['items'])}")
    print(f"unfetchable           : {len(result['unfetchable'])}")
    print(f"conversations          : {len(by_conv)}")
    for cid, its in by_conv.items():
        turns = sum(len(i.get("messages") or []) for i in its)
        print(f"\n  {cid}  ({len(its)} calls, {turns} messages)")
        for m in (its[0].get("messages") or [])[:4]:
            print(f"    [{m['role']}] {m['content'][:90].replace(chr(10), ' ')}")


if __name__ == "__main__":
    main()
