#!/usr/bin/env python3
"""ElevenLabs HTTP on the daily cost path retries 5xx / 429 / transport errors (ENG-1439).

On 2026-09-29 `/v1/usage/character-stats` returned intermittent 500s for every
breakdown_type for minutes at a time (one probe got a 200 after ~2 min, then 500
again). `store.py` made a single GET, so `elevenlabs_caller_split` raised
"has_api_key breakdown unavailable" and 17 whole-script retries over ~25 minutes
never stored 2026-09-28.

What this pins:

  * **A flapping endpoint is ridden out.** 500 x N then 200 succeeds, in one run.
  * **Exhaustion is loud and specific.** A persistent 500 raises naming the status
    and the attempt count — "unavailable" alone cannot tell an outage from a
    permissions fault.
  * **429 honours Retry-After.**
  * **The 403 degrade is unchanged and not retried.** `all_api_keys` needs the
    workspace Admin role; a 403 there is a fact about the key, not a transient,
    and still degrades to the `11labs_caller_degraded` note.
  * **The ConvAI walk no longer drops attribution on a 5xx.** A detail lookup that
    5xx'd used to return None, moving that conversation's minutes onto Other.

The transport is stubbed at its single chokepoint (`requests.get`) and sleep is
patched, so the suite takes milliseconds. No network, no keys.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_el_caller_mapping import _stubbed_google  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts"
REPORT = REPO / "plugins/eng/skills/report-aurie-daily-costs/scripts/report.py"


def _load(path: pathlib.Path, name: str):
    with _stubbed_google():
        sys.path.insert(0, str(path.parent))
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


S = _load(SCRIPTS / "store.py", "store_under_retry_test")
U = _load(SCRIPTS / "unit_costs.py", "unit_costs_under_retry_test")
R = _load(REPORT, "aurie_daily_report_under_retry_test")


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise S.requests.HTTPError(f"HTTP {self.status_code}")


class Stub:
    """Answers GETs from per-breakdown (or per-URL-suffix) queues; records every call."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []

    def __call__(self, url, headers=None, params=None, timeout=None):
        key = (params or {}).get("breakdown_type") or url.rsplit("/", 1)[-1]
        self.calls.append(key)
        q = self.routes[key]
        nxt = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


HASKEY_OK = Resp(200, {"usage": {"false": [300], "true": [200]}})
ALLKEYS_OK = Resp(200, {"usage": {"TTS [LiveKit Prod]": [50], "TTS [Intelligence Service Prod]": [150]}})


class _Patched(unittest.TestCase):
    """Patch the shared `requests.get` / `time.sleep` / jitter for each test."""

    def run_with(self, routes, fn):
        stub = Stub(routes)
        sleep = mock.MagicMock()
        with mock.patch.object(S.requests, "get", stub), \
             mock.patch.object(S.time, "sleep", sleep), \
             mock.patch.object(S.random, "uniform", lambda a, b: b):
            return fn(), stub, sleep


class TestCallerSplitRetry(_Patched):

    def test_flapping_500_then_200_succeeds(self):
        (agent, intel, lk, note), stub, sleep = self.run_with(
            {"has_api_key": [Resp(500)] * 4 + [HASKEY_OK], "all_api_keys": [ALLKEYS_OK]},
            lambda: S.elevenlabs_caller_split("2026-09-28", "k"))
        self.assertEqual((agent, intel, lk, note), (3.0, 1.5, 0.5, ""))
        self.assertEqual(stub.calls.count("has_api_key"), 5)
        self.assertEqual(sleep.call_count, 4)

    def test_persistent_500_raises_with_status_and_attempts(self):
        with self.assertRaises(S.ElevenLabsUnavailable) as cm:
            self.run_with({"has_api_key": [Resp(500)]},
                          lambda: S.elevenlabs_caller_split("2026-09-28", "k"))
        msg = str(cm.exception)
        self.assertIn("HTTP 500", msg)
        self.assertRegex(msg, r"after \d+ attempt")
        self.assertIn("has_api_key", msg)

    def test_the_budget_bounds_the_total_wait(self):
        sleeps = []
        with mock.patch.object(S.requests, "get", Stub({"has_api_key": [Resp(503)]})), \
             mock.patch.object(S.time, "sleep", sleeps.append), \
             mock.patch.object(S.random, "uniform", lambda a, b: b):
            with self.assertRaises(S.ElevenLabsUnavailable):
                S.elevenlabs_caller_split("2026-09-28", "k")
        self.assertLessEqual(sum(sleeps), S.EL_RETRY_BUDGET_S)
        # Long enough to ride out the ~2-minute flap observed on 2026-09-29.
        self.assertGreaterEqual(sum(sleeps), 120)

    def test_429_waits_retry_after(self):
        _, stub, sleep = self.run_with(
            {"has_api_key": [Resp(429, headers={"Retry-After": "7"}), HASKEY_OK],
             "all_api_keys": [ALLKEYS_OK]},
            lambda: S.elevenlabs_caller_split("2026-09-28", "k"))
        sleep.assert_called_once_with(7.0)

    def test_connection_error_is_retried(self):
        (agent, *_), stub, _ = self.run_with(
            {"has_api_key": [S.requests.ConnectionError("reset"), S.requests.Timeout("slow"),
                             HASKEY_OK],
             "all_api_keys": [ALLKEYS_OK]},
            lambda: S.elevenlabs_caller_split("2026-09-28", "k"))
        self.assertEqual(agent, 3.0)
        self.assertEqual(stub.calls.count("has_api_key"), 3)

    def test_403_on_all_api_keys_degrades_with_note_and_is_not_retried(self):
        (agent, intel, lk, note), stub, sleep = self.run_with(
            {"has_api_key": [HASKEY_OK], "all_api_keys": [Resp(403)]},
            lambda: S.elevenlabs_caller_split("2026-09-28", "k"))
        self.assertEqual((agent, intel, lk), (3.0, 2.0, 0.0))
        self.assertTrue(note.startswith("11labs_caller_degraded"))
        self.assertIn("HTTP 403", note)
        self.assertEqual(stub.calls.count("all_api_keys"), 1)
        sleep.assert_not_called()

    def test_4xx_on_has_api_key_fails_fast_naming_the_status(self):
        with self.assertRaises(RuntimeError) as cm:
            self.run_with({"has_api_key": [Resp(401)]},
                          lambda: S.elevenlabs_caller_split("2026-09-28", "k"))
        self.assertNotIsInstance(cm.exception, S.ElevenLabsUnavailable)
        self.assertIn("HTTP 401", str(cm.exception))


class TestConvaiWalkRetry(_Patched):

    def test_detail_5xx_is_retried_not_dropped_to_other(self):
        listing = Resp(200, {"conversations": [{"conversation_id": "c1", "call_duration_secs": 120}],
                             "has_more": False})
        detail = Resp(200, {"conversation_initiation_client_data": {"user_id": "u1"}})
        (by_user, day_total), stub, _ = self.run_with(
            {"conversations": [Resp(502), listing], "c1": [Resp(500), Resp(500), detail]},
            lambda: S.voice_costs("2026-09-28", "k", 4.0))
        self.assertEqual(by_user, {"u1": 4.0})
        self.assertEqual(stub.calls.count("c1"), 3)

    def test_detail_404_is_still_an_unattributed_conversation(self):
        listing = Resp(200, {"conversations": [{"conversation_id": "c1", "call_duration_secs": 60}],
                             "has_more": False})
        (by_user, _), stub, sleep = self.run_with(
            {"conversations": [listing], "c1": [Resp(404)]},
            lambda: S.voice_costs("2026-09-28", "k", 1.0))
        self.assertEqual(by_user, {})
        sleep.assert_not_called()


class TestUnitCostsRetry(_Patched):

    def test_unit_costs_elevenlabs_rides_out_a_500(self):
        with mock.patch.object(U.store_mod.requests, "get",
                               Stub({"none": [Resp(500), Resp(200, {"usage": {"All": [123]}})]})), \
             mock.patch.object(U.store_mod.time, "sleep"):
            self.assertEqual(U.elevenlabs({}, "fiat_units_spent", "2026-09-28", "2026-09-29"), 123)

    def test_unit_costs_uses_the_store_retry_helper(self):
        # unit_costs imports `store` by its own name; the helper must be the same one
        # this suite pins, not a second implementation.
        self.assertTrue(hasattr(U.store_mod, "_el_get"))


class TestDailyReportRetry(unittest.TestCase):

    def _run(self, routes):
        stub, sleep = Stub(routes), mock.MagicMock()
        with mock.patch.object(R.requests, "get", stub), \
             mock.patch.object(R.time, "sleep", sleep), \
             mock.patch.object(R.random, "uniform", lambda a, b: b):
            return R._get("https://api.elevenlabs.io/v1/usage/character-stats", {},
                          {"breakdown_type": "none"}), stub, sleep

    def test_500_then_200(self):
        r, stub, sleep = self._run({"none": [Resp(500), Resp(500), Resp(200)]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sleep.call_count, 2)

    def test_persistent_500_names_status_and_attempts(self):
        with self.assertRaises(R.RetryExhausted) as cm:
            self._run({"none": [Resp(500)]})
        # Optional lookups (`fetch_openai_project_names`) catch Exception; SystemExit
        # would sail past them and abort the whole report.
        self.assertIsInstance(cm.exception, Exception)
        self.assertIn("HTTP 500", str(cm.exception))
        self.assertRegex(str(cm.exception), r"after \d+ attempt")

    def test_403_fails_fast(self):
        with self.assertRaises(S.requests.HTTPError):
            self._run({"none": [Resp(403)]})

    def test_split_loop_stops_at_the_first_exhausted_day(self):
        calls = []

        class FakeStore:
            ElevenLabsUnavailable = S.ElevenLabsUnavailable

            def elevenlabs_caller_split(self, ds, key):
                calls.append(ds)
                raise S.ElevenLabsUnavailable("HTTP 500 after 10 attempt(s)")

        import datetime as dt
        with mock.patch.object(R, "_store", FakeStore()):
            out = R.fetch_elevenlabs_split_daily(dt.date(2026, 9, 22), dt.date(2026, 9, 28))
        self.assertEqual(out, {})
        self.assertEqual(calls, ["2026-09-22"])


if __name__ == "__main__":
    unittest.main()
