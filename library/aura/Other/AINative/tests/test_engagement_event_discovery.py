#!/usr/bin/env python3
"""`engagement.py` — AI event discovery: pagination cap and the AIAura namespace.

Two defects, both silent by construction (a 200 response with a well-formed,
truncated list looks identical to a complete one; a string that doesn't start
with a specific prefix silently returns False), and both measured to be
**currently inert** — see PROD-6144. Widening the discovered event set to
include everything the fixes below cover added zero user-days and zero users
over the full 2026-07-03..2026-09-15 window: every user who fires an obscure
or AIAura-prefixed event also fires a common "AI "-prefixed one the same day.
So this suite pins behaviour to stop a currently-harmless gap from becoming a
silent undercount, not because a live number moved.

Defect 1 — `events/names` has no cursor, just a `limit`
--------------------------------------------------------
`_ai_event_names()` used to request `limit=500`. The Aurie project had 1,224
event names on 2026-09-16; the call returned exactly 500, HTTP 200, no error,
no truncation marker — so the code saw 95 of the real 178 "AI "-prefixed
events. `_NAMES_FETCH_LIMIT` is now 5000 (headroom, not "unlimited" — see the
comment above it in engagement.py), and a response that comes back exactly at
the cap is flagged on stderr rather than silently trusted.

Defect 2 — `AIAura *` never matched `AI_EVENT_PREFIX = "AI "`
----------------------------------------------------------------
`"AIAura Aurie Opened"` starts `AIA`, not `AI` + space, so it silently missed
every string-prefix check keyed on the single literal `"AI "`. This is the
same PROD-6096 blind spot (Aurie-in-Aura's raw event namespace) reproduced in
Mixpanel's event-name space rather than BigQuery's table-name space.
`AI_EVENT_PREFIXES` is now a tuple checked via `str.startswith(tuple)`, and
`ENGAGED_EVENTS` is a tuple covering both namespaces' "sent a message" event.

Stdlib unittest, no network. Follows this repo's stub-the-transport-at-its-
chokepoint convention (see tests/test_elevenlabs_user_filter.py): engagement.py
calls `requests.get` / `requests.post` directly rather than through a client
object, so the stub replaces those two module attributes directly.
"""
from __future__ import annotations

import importlib.util
import io
import pathlib
import sys
import unittest
from contextlib import redirect_stderr

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts/engagement.py"


def _load():
    spec = importlib.util.spec_from_file_location("engagement_discovery", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class GetStub:
    """Stands in for `requests.get` against events/names. Records the `limit`
    param it was called with, so a revert to a smaller cap is directly
    observable rather than only inferable from behaviour."""

    def __init__(self, names, *, status=200, raise_exc=None):
        self.names = names
        self.status = status
        self.raise_exc = raise_exc
        self.calls = []

    def __call__(self, url, params=None, auth=None, timeout=None):
        self.calls.append(dict(params or {}))
        if self.raise_exc is not None:
            raise self.raise_exc
        return FakeResponse(self.names, status=self.status)


class PostStub:
    """Stands in for `requests.post` against the JQL endpoint. Records every
    script body so a test can assert what the JQL query actually contains,
    per the repo's 'verify the path production takes' rule — the JS text is
    what Mixpanel evaluates, not an intermediate Python value."""

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.scripts = []

    def __call__(self, url, data=None, auth=None, timeout=None):
        self.scripts.append((data or {}).get("script", ""))
        return FakeResponse(self.rows, status=200)


class EventDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self.mod._AI_EVENTS_CACHE = None  # discovery is memoized — clear per test
        self._orig_get = self.mod.requests.get
        self._orig_post = self.mod.requests.post
        self._orig_env = {
            k: self.mod.os.environ.get(k)
            for k in ("MIXPANEL_SERVICE_ACCOUNT_USERNAME", "MIXPANEL_SERVICE_ACCOUNT_SECRET")
        }
        self.mod.os.environ["MIXPANEL_SERVICE_ACCOUNT_USERNAME"] = "svc"
        self.mod.os.environ["MIXPANEL_SERVICE_ACCOUNT_SECRET"] = "sekrit"

    def tearDown(self):
        self.mod.requests.get = self._orig_get
        self.mod.requests.post = self._orig_post
        for k, v in self._orig_env.items():
            if v is None:
                self.mod.os.environ.pop(k, None)
            else:
                self.mod.os.environ[k] = v

    # -- Defect 2: the AIAura namespace ----------------------------------

    def test_aiaura_events_survive_prefix_filtering(self):
        """A single-prefix `startswith("AI ")` check silently drops every
        AIAura event. This is the PROD-6096 shape reproduced in Mixpanel's
        event-name space: a fixed prefix string, not a real membership test."""
        raw = ["AI Conversation Message Sent", "AIAura Aurie Opened",
               "AIAura Conversation Message Sent", "Notification Delivered"]
        self.mod.requests.get = GetStub(raw)
        names = self.mod._ai_event_names()
        self.assertIn("AIAura Aurie Opened", names)
        self.assertIn("AIAura Conversation Message Sent", names)
        self.assertNotIn("Notification Delivered", names)  # non-AI event excluded

    def test_engaged_events_always_present_even_if_api_omits_one(self):
        """Both ENGAGED_EVENTS members must always be in the discovered set —
        active/engaged parity depends on it, or a namespace could show
        active=1 while its own 'sent a message' event never counts toward
        engaged. Feed the API only the standalone-Aurie variant."""
        raw = ["AI Conversation Message Sent"]
        self.mod.requests.get = GetStub(raw)
        names = self.mod._ai_event_names()
        for ev in self.mod.ENGAGED_EVENTS:
            self.assertIn(ev, names)

    def test_fallback_covers_both_namespaces(self):
        """When events/names is unreachable, the static fallback is what
        `active` degrades to. It must not silently narrow to standalone-Aurie
        only — that would reintroduce the PROD-6096 gap on every degraded run."""
        self.mod.requests.get = GetStub(names=None, raise_exc=ConnectionError("down"))
        names = self.mod._ai_event_names()
        self.assertTrue(any(n.startswith("AI ") for n in names))
        self.assertTrue(any(n.startswith("AIAura") for n in names))

    # -- Defect 1: the pagination cap ------------------------------------

    def test_fetch_limit_has_real_headroom(self):
        """Pins the cap well above the observed real count (1,224 names on
        2026-09-16) so a revert to the old limit=500 fails here directly,
        independent of whether a truncation warning also fires."""
        self.assertGreaterEqual(self.mod._NAMES_FETCH_LIMIT, 2000)

    def test_capped_response_is_flagged_on_stderr(self):
        """A response whose length equals the requested limit may be
        truncated — Mixpanel gives no cursor to tell. That must be visible,
        not silently trusted the way the original limit=500 bug was."""
        limit = self.mod._NAMES_FETCH_LIMIT
        raw = [f"AI Event {i}" for i in range(limit)]  # exactly at the cap
        stub = GetStub(raw)
        self.mod.requests.get = stub
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.mod._ai_event_names()
        self.assertEqual(stub.calls[0].get("limit"), limit)
        self.assertIn("truncated", buf.getvalue())

    def test_uncapped_response_prints_no_warning(self):
        """Negative case for the truncation check — a response comfortably
        under the limit must not print the warning. Without this, a check
        that always warns would pass the capped-response test above too."""
        raw = ["AI Conversation Message Sent", "AIAura Aurie Opened"]
        self.mod.requests.get = GetStub(raw)
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.mod._ai_event_names()
        self.assertNotIn("truncated", buf.getvalue())

    # -- Both defects, on the path production actually takes -------------

    def test_selectors_carry_discovered_aiaura_events_into_the_jql_string(self):
        """`_selectors()` builds the literal JQL `event_selectors` array — the
        text Mixpanel evaluates. Confirms an AIAura event discovered above
        actually reaches that string, not just the Python-side name list."""
        raw = ["AI Conversation Message Sent", "AIAura Aurie Opened"]
        self.mod.requests.get = GetStub(raw)
        selectors = self.mod._selectors()
        self.assertIn('{event:"AIAura Aurie Opened"}', selectors)

    def test_day_engagement_ships_both_engaged_events_in_the_jql_script(self):
        """day_engagement's JQL script classifies 'engaged' by membership in
        an array built from ENGAGED_EVENTS. A regression back to a single
        `e.name==="AI Conversation Message Sent"` literal would silently stop
        counting Aurie-in-Aura users as engaged — assert both names are
        actually embedded in the script string sent to Mixpanel."""
        self.mod.requests.get = GetStub(["AI Conversation Message Sent"])
        post_stub = PostStub(rows=[])
        self.mod.requests.post = post_stub
        self.mod.day_engagement("2026-09-10")
        self.assertEqual(len(post_stub.scripts), 1)
        script = post_stub.scripts[0]
        for ev in self.mod.ENGAGED_EVENTS:
            self.assertIn(ev, script)
        self.assertIn("EE.indexOf(e.name)>=0", script)

    def test_window_engagement_ships_both_engaged_events_in_the_jql_script(self):
        """Same assertion as above for the backfill path — window_engagement
        builds its own independent script string and must not drift from
        day_engagement on which events count as 'engaged'."""
        self.mod.requests.get = GetStub(["AI Conversation Message Sent"])
        post_stub = PostStub(rows=[])
        self.mod.requests.post = post_stub
        self.mod.window_engagement("2026-09-01", "2026-09-10")
        self.assertEqual(len(post_stub.scripts), 1)
        script = post_stub.scripts[0]
        for ev in self.mod.ENGAGED_EVENTS:
            self.assertIn(ev, script)
        self.assertIn("EE.indexOf(e.name)>=0", script)


if __name__ == "__main__":
    unittest.main()
