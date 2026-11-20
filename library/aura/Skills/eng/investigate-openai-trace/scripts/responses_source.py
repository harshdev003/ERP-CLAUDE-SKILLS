#!/usr/bin/env python3
"""Read Responses-API traffic for the trace skill, indexed by the llmCall ledger.

Why this exists
---------------
Every read in `openai_client.py` goes to stored chat-completions
(`/v1/chat/completions`, `/{id}`, `/{id}/messages`). Responses-API traffic never
appears there, and it cannot be walked either: `GET /v1/responses` (the list
form) rejects API keys outright --

    HTTP 401: "must be made with a session key (that is, it can only be made
               from the browser)."

So stored responses are *readable but not enumerable*. That matters here more
than it might elsewhere, because this skill fans out across production projects
and **most of them already run on Responses** -- `memory`, `profile`, `summary`,
`journal`, `intent`, `notification`, `guardrail`, `suggestions`, `insights` moved
in ENGBACK-1235/1246. Those are exactly the side-effect prompters someone opens
this skill to debug.

IntelligenceService's `llmCall` ledger is the index OpenAI won't provide. It
filters on `transaction`, `userId` and `openaiId` -- the three pivots this skill
already has -- and each row carries `openaiId`, `openaiAPI`, `prompterName` and
`project`.

That last field is a bonus, not just parity: the completions path has to *try
each project's key* and read 404s as misses, because an id doesn't say which
project owns it. The ledger says. So a resolved row goes straight to the right
project key on the first attempt.

Emits **chat-completion-shaped** dicts so `normalize()` and `format.py` consume
them with no changes.
"""

from __future__ import annotations

import json
import os
import sys
import time
import pathlib
import re
from datetime import datetime, timezone
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

_MAX_RETRIES = 4


class ResponsesSourceError(Exception):
    """Unrecoverable ledger/OpenAI failure on the Responses path."""


# ---------------------------------------------------------------------------
# Aura ledger -- the index
# ---------------------------------------------------------------------------

def _get_json(url: str, headers: dict[str, str], params: dict | None = None) -> dict:
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=60)
        except requests.RequestException as exc:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                last_err = exc
                continue
            raise ResponsesSourceError(f"network error on {url}: {exc}") from exc
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES - 1:
            time.sleep(2 ** attempt)
            last_err = RuntimeError(f"HTTP {resp.status_code}")
            continue
        if resp.status_code >= 400:
            raise ResponsesSourceError(
                f"HTTP {resp.status_code} on {url}: {resp.text[:400]}"
            )
        return resp.json()
    raise ResponsesSourceError(f"exhausted retries on {url}: {last_err}")


def _ledger(params: dict[str, Any]) -> list[dict]:
    """The ledger read, from BigQuery `llm.llm_call` as GCP_KEY. Without it this
    skill sees the Chat-Completions half only, so a failure raises."""
    try:
        rows = llm_ledger.rows(params)
    except llm_ledger.LedgerError as e:
        raise ResponsesSourceError(str(e)) from e
    limit = params.get("limit")
    if limit and len(rows) >= int(limit):
        # A full page means there may be more we never see. Say so -- silently
        # returning a capped set reads as a complete history, which is the exact
        # failure mode this whole reader exists to remove.
        print(
            f"# WARNING: ledger returned a full page ({limit} rows) for this "
            "pivot; older calls may be missing. Narrow --since or raise the limit.",
            file=sys.stderr,
        )
    return [r for r in rows if str(r.get("openaiId") or "").startswith("resp_")]


def rows_for_transaction(transaction: str, prompter: str | None = None) -> list[dict]:
    """Responses rows for one transaction, trying both UUID casings.

    The ledger's filters are exact-match and case-sensitive, and transaction ids
    appear in both casings depending on the emitting path, so a caller pasting
    either has to work -- same reason `mode_focus` already retries the OpenAI
    metadata filter lower-cased.
    """
    seen: list[str] = []
    for variant in (transaction, transaction.upper(), transaction.lower()):
        if variant in seen:
            continue
        seen.append(variant)
        params: dict[str, Any] = {"transaction": variant, "limit": 200}
        if prompter:
            params["prompterName"] = prompter
        rows = _ledger(params)
        if rows:
            return rows
    return []


def rows_for_user(user_id: str, limit: int = 1000,
                  prompter: str | None = None,
                  since_unix: int | None = None) -> list[dict]:
    """Responses rows for one user, bounded server-side by `since_unix`.

    The window MUST be pushed to the server. Filtering a single page
    client-side means a busy user's non-conversation ledger traffic (memory,
    summary, profile, ...) crowds the in-window rows off the page and they
    vanish with no warning -- on a `--since 7d` run that is most of them.
    """
    params: dict[str, Any] = {"userId": user_id, "limit": limit}
    if prompter:
        params["prompterName"] = prompter
    if since_unix is not None:
        params["from"] = datetime.fromtimestamp(
            since_unix, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _ledger(params)


def _row_unix_ok(row: dict, since_unix: int | None) -> bool:
    """True when the row is at or after `since_unix` (or no bound was given)."""
    if since_unix is None:
        return True
    raw = row.get("createdAt") or ""
    try:
        return int(
            datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        ) >= since_unix
    except (ValueError, AttributeError):
        # Undatable row: keep it rather than silently dropping a real call.
        return True


def row_for_openai_id(openai_id: str) -> dict | None:
    rows = _ledger({"openaiId": openai_id, "limit": 5})
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# OpenAI -- retrieve by id
# ---------------------------------------------------------------------------

def _openai_headers(api_key: str, project_id: str | None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {api_key}"}
    if project_id:
        h["OpenAI-Project"] = project_id
    return h


def fetch_response(api_key: str, project_id: str | None, resp_id: str) -> dict:
    return _get_json(
        f"{OPENAI_API}/responses/{resp_id}",
        _openai_headers(api_key, project_id),
    )


def fetch_input_items(api_key: str, project_id: str | None, resp_id: str,
                      limit: int = 100) -> list[dict]:
    """All input items for a response -- the prior turns.

    The response object's own `input` is null, so this is the only source of the
    user side. Paginates on `after`/`has_more`: OpenAI caps the endpoint at 100
    per page, and a single request silently truncates a long exchange while still
    looking complete.
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
            break
        after = last_id
    return items


# ---------------------------------------------------------------------------
# Adapt to the chat-completion shape normalize() expects
# ---------------------------------------------------------------------------

def _item_text(item: dict) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        c.get("text", "")
        for c in content
        if isinstance(c, dict) and c.get("type") in ("output_text", "input_text", "text")
    ]
    return "\n".join(p for p in parts if p)


def _output_text(resp: dict) -> str:
    return "\n".join(
        t for t in (
            _item_text(o) for o in (resp.get("output") or [])
            if o.get("type") == "message"
        ) if t
    )


def to_completion(resp: dict, row: dict) -> dict:
    """Adapt a `/v1/responses` object into the doc shape `normalize()` reads.

    `normalize()` pulls `metadata.name` (prompter), `metadata.transaction`,
    `metadata.user`, `metadata.conversationId`, `metadata.dialogueId`,
    `choices[0].message.content`, `usage`, `id`, `created` and `model`. The
    Responses object carries its own `metadata` with the same keys (IS writes it
    identically on both paths), so prefer that and fall back to the ledger row --
    which is the authority for the ids the ledger owns.
    """
    meta = dict(resp.get("metadata") or {})
    meta.setdefault("name", row.get("prompterName"))
    meta.setdefault("transaction", row.get("transaction"))
    meta.setdefault("user", row.get("userId"))
    meta.setdefault("conversationId", row.get("conversationId"))
    meta.setdefault("dialogueId", row.get("dialogueId"))

    usage = resp.get("usage") or {}
    final = _output_text(resp)
    return {
        "id": resp.get("id") or row.get("openaiId"),
        "created": resp.get("created_at"),
        "model": resp.get("model") or row.get("model"),
        "metadata": meta,
        "usage": {
            "prompt_tokens": usage.get("input_tokens") or row.get("inputTokens"),
            "completion_tokens": usage.get("output_tokens") or row.get("outputTokens"),
            "total_tokens": usage.get("total_tokens") or row.get("totalTokens"),
            "prompt_tokens_details": {
                "cached_tokens": (usage.get("input_tokens_details") or {}).get(
                    "cached_tokens", row.get("cachedTokens")
                ),
            },
        },
        "choices": [{"message": {"role": "assistant", "content": final}}],
        "_source": "responses",
        "_openai_api": "responses",
        "_ledger_project": row.get("project"),
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def hydrate(rows: Iterable[dict], key_for_project: Any,
            *, with_messages: bool = False,
            verbose: bool = False) -> tuple[list[dict], list[dict]]:
    """Fetch each ledger row's stored object. Returns (docs, unfetchable).

    `key_for_project` maps a ledger `project` short name (e.g. `"memory"`) to
    `(api_key, project_id)`, or returns `(None, None)` when that project has no
    key configured -- mirroring the soft-skip the completions path already does.

    Unfetchable rows are returned rather than dropped: the ledger knowing about a
    call whose stored object won't load is "retrieval failed", not "nothing
    happened", and collapsing the two is how a blind spot reads as an empty
    result.
    """
    docs: list[dict] = []
    unfetchable: list[dict] = []
    for row in rows:
        rid = row["openaiId"]
        api_key, project_id = key_for_project(row.get("project"))
        if not api_key:
            unfetchable.append({
                "openaiId": rid,
                "project": row.get("project"),
                "error": "no project-scoped key configured for this project",
            })
            continue
        try:
            resp = fetch_response(api_key, project_id, rid)
        except ResponsesSourceError as exc:
            unfetchable.append({"openaiId": rid, "error": str(exc)[:200]})
            if verbose:
                print(f"# unfetchable {rid[:24]}…: {exc}", file=sys.stderr)
            continue
        doc = to_completion(resp, row)
        if with_messages:
            try:
                items = fetch_input_items(api_key, project_id, rid)
            except ResponsesSourceError as exc:
                items = []
                # NOT verbose-gated, and recorded on the doc. `input_items` is the
                # only source of the user side, so swallowing this renders an
                # assistant-only trace that looks complete -- precisely the failure
                # this reader exists to remove. The caller must be able to say
                # "transcript unavailable" rather than "there was no input".
                doc["_messages_error"] = str(exc)[:200]
                print(
                    f"# WARNING: could not fetch input_items for {rid[:24]}… "
                    f"({exc}). The transcript below is the assistant side ONLY.",
                    file=sys.stderr,
                )
            # Oldest-first, and system/user/assistant only -- function_call items
            # carry no transcript text, same as tool messages on the chat path.
            doc["_messages"] = [
                {"role": it.get("role"), "content": _item_text(it)}
                for it in reversed(items)
                if it.get("role") in ("system", "user", "assistant") and _item_text(it)
            ]
        docs.append(doc)
    return docs, unfetchable


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse

    ap = argparse.ArgumentParser(
        description="Probe: list Responses-API ledger rows for a pivot."
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--transaction")
    g.add_argument("--user")
    g.add_argument("--openai-id")
    ap.add_argument("--prompter")
    args = ap.parse_args()

    if args.transaction:
        rows = rows_for_transaction(args.transaction, args.prompter)
    elif args.user:
        rows = rows_for_user(args.user, prompter=args.prompter)
    else:
        row = row_for_openai_id(args.openai_id)
        rows = [row] if row else []

    print(f"{len(rows)} Responses row(s)")
    for r in rows:
        print(f"  {str(r.get('createdAt'))[:19]}  {r.get('prompterName'):28} "
              f"project={r.get('project'):12} {str(r.get('openaiId'))[:26]}")
    if not rows:
        print(json.dumps({"hint": "no Responses-API rows for this pivot"}))


if __name__ == "__main__":
    main()
