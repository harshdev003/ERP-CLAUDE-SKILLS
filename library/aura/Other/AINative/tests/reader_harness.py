#!/usr/bin/env python3
"""Load a `responses_reader.py` and stub its network, for tests.

Why a harness rather than tests that call the module directly
------------------------------------------------------------
Every defect this suite exists to catch lives in a **failure branch** — a
response that will not fetch, an `/input_items` call that raises, a page cap
reached. Real data cannot reach those branches: OpenAI does not fail on demand,
and a fixture small enough to hand-write never crosses the 100-item page cap. So
the only way to test them is to make the transport fail on request.

Two chokepoints cover every network path with no `requests` monkeypatching and
no live keys: `_ledger` (the BigQuery `llm.llm_call` read) and `_get_json`
(`fetch_response` and `fetch_input_items`). The stub routes the ledger through
`_get_json` under the marker URL `LEDGER_URL`, so `fail()` and `urls()` treat
both the same way.

The suite runs against **every** copy of `responses_reader.py` in the repo, not
just one. The file is duplicated per skill (self-containment, Architecture
principle 1), so a per-copy run doubles as a drift check: a copy that diverges
behaviourally fails here even when a byte-diff has been reconciled.

Stdlib only — no pytest, no `requests` at import time beyond what the module
under test already needs.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def script_paths(filename: str) -> list[pathlib.Path]:
    """Every copy of `filename` under `plugins/`, sorted for stable test ids.

    The sort matters: a suite that parameterises over copies names its cases
    after the path, so an unsorted glob would reshuffle test ids between runs.
    """
    return sorted((REPO_ROOT / "plugins").rglob(filename))


def reader_paths() -> list[pathlib.Path]:
    """Every `responses_reader.py` in the repo, sorted for stable test ids."""
    return script_paths("responses_reader.py")


# Modules a skill script imports from its own `scripts/` dir. These resolve by
# their plain name, so they cache in `sys.modules` under a name that is the same
# for every copy — see `load_reader`.
SIBLING_MODULES = ("responses_reader", "fetch_logs", "llm_ledger", "bq")


def load_reader(path: pathlib.Path, *, siblings: tuple[str, ...] = SIBLING_MODULES):
    """Import one skill script under a unique module name, siblings included.

    Two distinct caching traps, and missing either one silently tests the same
    copy N times while reporting N passes:

    1. **The file itself.** The copies share a filename, so loading under a fixed
       name would hand back the first-loaded module. Hence the uuid name.

    2. **Its siblings.** `fetch_logs.py` does `from responses_reader import ...`
       after putting its own dir on `sys.path`. That import caches under the
       plain name `responses_reader`, which is identical across copies — so the
       second copy's `fetch_logs` binds the *first* copy's reader, and drift in
       the sibling becomes invisible. Verified: breaking one copy's
       `CONVERSATION_PROMPTERS` left the fetch_logs suite fully green until this
       purge was added. So sibling names are evicted before and after each load.

    `sys.path` is snapshotted and restored wholesale rather than by removing the
    one entry we added — the module under test inserts its own dir at import
    time, so a targeted removal leaks a path per load and later copies resolve
    siblings against an earlier copy's directory.
    """
    name = f"_reader_{uuid.uuid4().hex}"
    saved_path = list(sys.path)
    saved_siblings = {s: sys.modules.pop(s, None) for s in siblings}
    try:
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved_path
        for s in siblings:
            sys.modules.pop(s, None)
            if saved_siblings[s] is not None:
                sys.modules[s] = saved_siblings[s]


LEDGER_URL = "bigquery://aura-main.llm.llm_call"


class FakeTransport:
    """Routes `_get_json` calls to canned data, and fails on demand.

    Register failures with `fail(substring)`: any URL containing that substring
    raises `ResponsesReaderError`. That is how the failure branches — the ones
    real data can never reach — get exercised.

    Every call is recorded in `self.calls` so a test can assert on what was NOT
    fetched, which is the only way to check the one-fetch-per-conversation
    optimisation and the `input_items_for` modes.
    """

    def __init__(self, mod, *, ledger_rows=None, responses=None, input_items=None,
                 ledger_by_conversation=None):
        self.mod = mod
        self.ledger_rows = ledger_rows or []
        # Exact-match `conversationId` -> rows, mirroring the real ledger's
        # case-sensitive filter. Keyed by the literal string, so a test can give
        # `abc-1` and `ABC-1` *different* rows — which is the real ledger's
        # behaviour, not a contrived one: one conversation genuinely holds rows
        # under both casings because different call sites write it differently.
        # Falls back to `ledger_rows` when no map is supplied.
        self.ledger_by_conversation = ledger_by_conversation
        self.responses = responses or {}          # resp_id -> response object
        self.input_items = input_items or {}      # resp_id -> list[item]
        self.failures: set[str] = set()
        self.calls: list[tuple[str, dict]] = []
        self._offsets: dict[tuple[int, int], int] = {}

    def fail(self, substring: str) -> "FakeTransport":
        self.failures.add(substring)
        return self

    def install(self) -> "FakeTransport":
        self.mod._get_json = self._get_json
        self.mod._ledger = self._ledger
        return self

    def _ledger(self, params: dict) -> list[dict]:
        return self._get_json(LEDGER_URL, {}, params)["data"]

    # -- the stub ---------------------------------------------------------

    def _get_json(self, url: str, headers: dict, params: dict | None = None) -> dict:
        self.calls.append((url, dict(params or {})))
        for bad in self.failures:
            if bad in url:
                raise self.mod.ResponsesReaderError(f"stubbed failure on {url}")

        if url == LEDGER_URL:
            if self.ledger_by_conversation is not None:
                key = (params or {}).get("conversationId")
                return {"data": list(self.ledger_by_conversation.get(key, []))}
            return {"data": list(self.ledger_rows)}

        if "/input_items" in url:
            resp_id = url.split("/responses/")[1].split("/")[0]
            return self._page(self.input_items.get(resp_id, []), params or {})

        if "/responses/" in url:
            resp_id = url.rsplit("/", 1)[1]
            if resp_id not in self.responses:
                raise self.mod.ResponsesReaderError(f"HTTP 404 on {url}")
            return self.responses[resp_id]

        raise AssertionError(f"unrouted URL in test: {url}")

    # A client that paginates correctly never re-requests the same offset. Two
    # identical requests in a row means the cursor is not advancing.
    _MAX_REPEATS = 3

    def _page(self, items: list[dict], params: dict) -> dict:
        """Emulate OpenAI's `after`/`has_more` cursor over `items`.

        Deliberately a real cursor rather than "return everything": a stub that
        ignores `after` cannot tell a paginating client from a non-paginating
        one, which is the exact defect being guarded against.

        **Fails fast on a non-advancing cursor.** If the client stops advancing
        `after` — which is precisely what a regression in the reader's `last_id`
        stop guard looks like — a naive stub keeps answering `has_more=True` for
        page 0 forever and the test spins until the CI job's timeout. That turns
        the regression this suite exists to catch into a 10-minute hang that
        reads like an infra flake instead of a failure with a stack trace.
        Verified: removing the reader's stop guard hung the suite indefinitely
        before this guard existed.
        """
        limit = int(params.get("limit") or 100)
        after = params.get("after")
        start = 0
        if after:
            ids = [i.get("id") for i in items]
            # `index` finds the FIRST match, so duplicate ids would also send
            # the offset backwards — same non-termination, caught by the same
            # repeat guard below.
            start = ids.index(after) + 1 if after in ids else len(items)

        key = (id(items), start)
        self._offsets[key] = self._offsets.get(key, 0) + 1
        if self._offsets[key] > self._MAX_REPEATS:
            raise AssertionError(
                f"pagination did not advance: offset {start} requested "
                f"{self._offsets[key]} times with after={after!r}. The client is "
                "not consuming the cursor — check the reader's `last_id` stop "
                "guard. Failing here rather than spinning until the CI timeout."
            )

        page = items[start:start + limit]
        return {"data": page, "has_more": (start + limit) < len(items)}

    # -- assertions helpers ----------------------------------------------

    def urls(self, containing: str) -> list[str]:
        return [u for u, _ in self.calls if containing in u]


# ---------------------------------------------------------------------------
# Fixture builders — small, explicit, no hidden defaults
# ---------------------------------------------------------------------------

def ledger_row(resp_id, *, conversation=None, prompter="coach-prompter",
               created="2026-08-01T12:00:00Z", api="responses", user="u1"):
    return {
        "openaiId": resp_id,
        "openaiAPI": api,
        "prompterName": prompter,
        "conversationId": conversation,
        "createdAt": created,
        "userId": user,
        "project": "conversation",
    }


def response_obj(resp_id, text="assistant reply", created=1785000000):
    return {
        "id": resp_id,
        "created_at": created,
        "model": "gpt-5",
        "output": [{"type": "message",
                    "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def input_item(item_id, role, text):
    kind = "output_text" if role == "assistant" else "input_text"
    return {"id": item_id, "type": "message", "role": role,
            "content": [{"type": kind, "text": text}]}


def conversation_items(n, prefix="it"):
    """`n` alternating user/assistant items — enough to cross the page cap."""
    return [
        input_item(f"{prefix}{i}", "user" if i % 2 == 0 else "assistant", f"turn {i}")
        for i in range(n)
    ]
