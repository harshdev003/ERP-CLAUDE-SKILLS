#!/usr/bin/env python3
"""A failed vendor usage read must never be stored as a $0 day.

Why this needs a test
---------------------
Each voice leg in the per-user cost store returns `($0, "<vendor>_pending: …")` when its
usage read fails, and the notes were only printed. So the store wrote the $0 as a
measurement: on 2026-11-03 a 401 on the Cartesia admin key booked Cartesia at $0 for a
day it billed 6.58M credits. The same fallthrough existed for the direct Deepgram read.
A LiveKit log failure keeps its documented store-and-flag path, but it hides every
LiveKit-path vendor's traffic, so a failed vendor read during one is refused too.

The guard refuses the day when a read failed and the vendor carried traffic (or its
traffic is unknown). These tests drive `store.main()` itself, with the transport stubbed
at each leg's chokepoint, so they pin the wiring as well as the rule: a test of the
helper alone passes while main() hands it the wrong note.

Stdlib only, no network, no keys.
"""
from __future__ import annotations

import contextlib
import importlib.abc
import importlib.util
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts"
DAY = "2026-11-03"


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        cls = type(name, (Exception,), {"__module__": self.__name__})
        setattr(self, name, cls)
        return cls


class _GoogleStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "google" or fullname.startswith("google."):
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        mod = _StubModule(spec.name)
        mod.__path__ = []
        return mod

    def exec_module(self, module):
        pass


@contextlib.contextmanager
def _stubbed_google():
    saved = {k: v for k, v in sys.modules.items() if k == "google" or k.startswith("google.")}
    finder = _GoogleStubFinder()
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        for k in [k for k in sys.modules if k == "google" or k.startswith("google.")]:
            del sys.modules[k]
        sys.modules.update(saved)


def _load(name):
    sys.path.insert(0, str(SCRIPTS))
    try:
        with _stubbed_google():
            spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


store = _load("store")
unit_costs = _load("unit_costs")


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.headers = status, body or {}, {}
        self.ok = status < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class _PastGuard(Exception):
    """Raised by the first call after the guard: the day was NOT refused."""


def _meta(cartesia_min):
    return {"minutes_by_provider": {"cartesia": cartesia_min, "elevenlabs": 20.0 - cartesia_min},
            "by_user_tts": {}, "sessions": 2, "stt_minutes": 20.0,
            "stt_sessions_measured": 2, "stt_minutes_by_transport": {"deepgram": 20.0}}


def _cartesia(status):
    """Stub the Cartesia transport: `status` 200 returns a day of credits."""
    def get(url, **_kw):
        assert "cartesia" in url, url
        return _Resp(status, {"data": [{"credits": 1_000_000}]})
    return get


class MainRefusesAFailedRead(unittest.TestCase):
    """store.main() with every non-voice dependency stubbed and the voice reads injected."""

    def _run(self, *, cartesia_status=200, cartesia_min=10.0, deepgram=(1.0, ""),
             livekit_down=False, extra=()):
        tmp = tempfile.mkdtemp()
        csv_path = os.path.join(tmp, "per_user.csv")
        bq = types.ModuleType("bq")
        bq.partition_exists = lambda *a, **k: False
        bq.BQUnavailable = type("BQUnavailable", (Exception,), {})
        bq.BQError = type("BQError", (Exception,), {})
        bq.PROJECT, bq.DATASET = "p", "d"
        bq_sink = types.ModuleType("bq_sink")
        bq_sink.STORES = {"per_user": {"table": "t"}}

        def livekit(day):
            if livekit_down:
                raise store.LiveKitUnavailable("livekit_pending: GCP auth failed (401)")
            return {"u1": 20.0}, 20.0, _meta(cartesia_min)

        patches = [
            mock.patch.dict(sys.modules, {"bq": bq, "bq_sink": bq_sink}),
            mock.patch.dict(os.environ, {"ELEVENLABS_ADMIN_API_KEY": "x", "AURA_API_TOKEN": "x",
                                         "CARTESIA_ADMIN_API_KEY": "x"}),
            mock.patch.object(store, "llm_costs", return_value=({"u1": 1.0}, 1.0, "p")),
            mock.patch.object(store.engagement, "day_engagement", return_value=({}, {}, "off")),
            mock.patch.object(store, "assert_user_count_plausible", return_value=None),
            mock.patch.object(store, "walk_eligible_dollars", return_value=1.0),
            mock.patch.object(store, "elevenlabs_caller_split", return_value=(0.0, 0.0, 0.0, "")),
            mock.patch.object(store, "voice_costs", return_value=({}, 0.0)),
            mock.patch.object(store, "livekit_minutes_for_day", side_effect=livekit),
            mock.patch.object(store, "mtd_livekit_minutes_before", return_value=0.0),
            mock.patch.object(store, "mtd_inference_drawdown_before", return_value=0.0),
            mock.patch.object(store, "inworld_day_total", return_value=(0.0, "")),
            mock.patch.object(store, "deepgram_day_total", return_value=deepgram),
            mock.patch.object(store.requests, "get", side_effect=_cartesia(cartesia_status)),
            mock.patch.object(store, "inference_tts_note", side_effect=_PastGuard),
            mock.patch.object(sys, "argv", ["store.py", "--date", DAY, "--llm-json", "x.json",
                                            "--csv", csv_path, *extra]),
        ]
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            try:
                store.main()
            finally:
                self.assertFalse(os.path.exists(csv_path) and os.path.getsize(csv_path),
                                 "nothing may be written before the guard decides")

    def assertRefused(self, vendor, **kw):
        with self.assertRaises(SystemExit) as cm:
            self._run(**kw)
        msg = str(cm.exception.code)
        self.assertIn("REFUSING to store", msg)
        self.assertIn(f"{vendor}_pending", msg)
        return msg

    def test_the_incident_cartesia_401_with_cartesia_traffic_is_refused(self):
        msg = self.assertRefused("cartesia", cartesia_status=401)
        self.assertIn("10.0 min of traffic", msg)

    def test_cartesia_rate_limited_past_retries_is_refused(self):
        with mock.patch.object(store.time, "sleep"):
            self.assertRefused("cartesia", cartesia_status=429)

    def test_cartesia_failure_on_a_day_without_cartesia_traffic_stores(self):
        with self.assertRaises(_PastGuard):
            self._run(cartesia_status=401, cartesia_min=0.0)

    def test_healthy_reads_store(self):
        with self.assertRaises(_PastGuard):
            self._run()

    def test_a_failed_direct_deepgram_read_is_refused(self):
        self.assertRefused("deepgram", deepgram=(0.0, "deepgram_pending: no project usage "
                                                      "read succeeded (7e42b984:403)"))

    def test_a_partial_deepgram_read_is_refused(self):
        self.assertRefused("deepgram", deepgram=(0.5, "deepgram_pending: partial usage read, "
                                                      "total may undercount (abc:500)"))

    def test_a_livekit_log_failure_alone_keeps_store_and_flag(self):
        with self.assertRaises(_PastGuard):
            self._run(livekit_down=True)

    def test_a_cartesia_failure_while_livekit_is_down_is_refused_as_unknown_traffic(self):
        msg = self.assertRefused("cartesia", livekit_down=True, cartesia_status=401)
        self.assertIn("traffic unknown", msg)

    def test_the_override_stores_deliberately(self):
        with self.assertRaises(_PastGuard):
            self._run(cartesia_status=401, extra=("--allow-unmeasured-vendor",))


class UnitCostFallbackRefusesAFailedRead(unittest.TestCase):
    """unit_costs.build_row recomputes LiveKit-path $ from the vendor legs when the per-user
    store lacks the day, and persists the row — so it needs the same guard."""

    def _build(self, *, cartesia, deepgram=(1.0, ""), meta=None, allow=False):
        with mock.patch.object(unit_costs, "by_project", return_value={}), \
             mock.patch.object(unit_costs, "elevenlabs_split", return_value=(0.0, 0.0)), \
             mock.patch.object(store, "cartesia_day_total", return_value=cartesia), \
             mock.patch.object(store, "deepgram_day_total", return_value=deepgram), \
             mock.patch.object(store, "livekit_costs",
                               return_value=({}, 20.0, 5.0, "", meta or _meta(10.0))), \
             mock.patch.object(store, "fish_day_total", return_value=(0.0, "")), \
             mock.patch.object(store, "inworld_day_total", return_value=(0.0, "")):
            rates = {k: 1.0 for k in ("cartesia", "deepgram", "livekit", "fish", "inworld")}
            return unit_costs.build_row(DAY, {}, None, None, rates, None, allow_unmeasured=allow)

    def test_cartesia_401_with_traffic_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self._build(cartesia=(0.0, "cartesia_pending: fetch failed (HTTPError)"))
        self.assertIn("cartesia_pending", str(cm.exception.code))

    def test_a_deepgram_failure_on_an_inference_only_day_builds(self):
        # Every STT minute went through LiveKit Inference, so Deepgram carried no traffic.
        meta = dict(_meta(10.0), stt_minutes_by_transport={"deepgram-inference": 20.0})
        self._build(cartesia=(2.0, ""), meta=meta,
                    deepgram=(0.0, "deepgram_pending: no project usage read succeeded (x:403)"))

    def test_a_deepgram_failure_with_direct_stt_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self._build(cartesia=(2.0, ""),
                        deepgram=(0.0, "deepgram_pending: no project usage read succeeded (x:403)"))
        self.assertIn("deepgram_pending", str(cm.exception.code))

    def test_override_and_healthy_reads_build(self):
        self._build(cartesia=(0.0, "cartesia_pending: fetch failed (HTTPError)"), allow=True)
        self._build(cartesia=(2.0, ""))


class CartesiaLegReportsTheFailure(unittest.TestCase):
    """The leg itself must keep returning the pending note the guard keys on."""

    def test_401_is_a_pending_note_not_a_quiet_zero(self):
        with mock.patch.dict(os.environ, {"CARTESIA_ADMIN_API_KEY": "x"}), \
             mock.patch.object(store.requests, "get", side_effect=_cartesia(401)):
            usd, note = store.cartesia_day_total(DAY, 38.0)
        self.assertEqual(usd, 0.0)
        self.assertTrue(note.startswith("cartesia_pending"), note)

    def test_missing_key_is_a_pending_note(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            _usd, note = store.cartesia_day_total(DAY, 38.0)
        self.assertTrue(note.startswith("cartesia_pending"), note)


if __name__ == "__main__":
    unittest.main()
