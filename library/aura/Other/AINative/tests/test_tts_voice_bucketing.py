#!/usr/bin/env python3
"""LiveKit minutes split by `(provider, voice_id)`, not just by provider (ENG-1308).

Why this needs a test
---------------------
PROD-6068 runs two ElevenLabs arms that differ **only by voice id**. The store books
LiveKit minutes per `(userId, TTS-provider)`, so both arms land in one bucket and the
experiment has no per-arm minutes or cost. Every way the finer split can be wrong is
silent: minutes that fold into the wrong voice, or into a real voice when the id was
never logged, still sum to a plausible provider total.

What is pinned
--------------
- The resolved voice (`TTS provider created`, `TTS hot-swapped`) beats the requested one
  (`Session config resolved`), and a swap moves the session to the new voice.
- A session with a provider but no voice id books under `UNKNOWN_VOICE_ID` — explicit,
  never a real voice, never dropped — and is counted.
- The per-voice minutes sum back to the per-provider minutes, per user and in total,
  because both views come from one pass over the same sessions.
- Sessions logged before the resolved field existed still split, on the requested id,
  and the day says how many did (`sessions_with_requested_voice_only`).

Stdlib only, no network, no keys — `google.*` is stubbed the way the sibling suites do it.
"""
from __future__ import annotations

import contextlib
import importlib.abc
import importlib.util
import pathlib
import sys
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugins/eng/skills/store-aurie-user-daily-cost/scripts"


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


def _entry(room, ts, **payload):
    return {"timestamp": ts, "jsonPayload": dict(room=room, **payload)}


T0, T10, T20 = "2026-09-18T10:00:00Z", "2026-09-18T10:10:00Z", "2026-09-18T10:20:00Z"


class TestVoiceCapture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lm = _load("livekit_minutes")

    def test_the_resolved_voice_is_read_structurally_from_the_created_row(self):
        s = self.lm._build_sessions([
            _entry("r1", T0, userId="u1", message="TTS provider created: elevenlabs (voice: vA)",
                   tts_provider="elevenlabs", voice_id="vA"),
        ])["r1"]
        self.assertEqual((s.tts_provider, s.tts_voice_id, s.tts_voice_resolved), ("elevenlabs", "vA", True))

    def test_the_requested_voice_only_fills_the_gap(self):
        """`Session config resolved` says what the client asked for; the created row says what ran."""
        s = self.lm._build_sessions([
            _entry("r1", T0, userId="u1", message="Session config resolved", voice_id="asked"),
            _entry("r1", T10, userId="u1", message="TTS provider created: elevenlabs (voice: ran)",
                   voice_id="ran"),
        ])["r1"]
        self.assertEqual((s.tts_voice_id, s.tts_voice_resolved), ("ran", True))

    def test_a_requested_voice_is_kept_when_no_resolved_row_exists(self):
        """A day logged before the resolved field: the requested id is the best that window offers."""
        s = self.lm._build_sessions([
            _entry("r1", T0, userId="u1", message="Session config resolved", voice_id="asked"),
            _entry("r1", T10, userId="u1", message="TTS provider created: elevenlabs (voice: x)"),
        ])["r1"]
        self.assertEqual((s.tts_voice_id, s.tts_voice_resolved), ("asked", False))

    def test_a_resolved_row_is_not_overwritten_by_a_later_requested_one(self):
        s = self.lm._build_sessions([
            _entry("r1", T0, userId="u1", message="TTS provider created: cartesia (voice: ran)", voice_id="ran"),
            _entry("r1", T10, userId="u1", message="Session config resolved", voice_id="asked-late"),
        ])["r1"]
        self.assertEqual(s.tts_voice_id, "ran")

    def test_a_hot_swap_moves_the_session_to_the_new_voice(self):
        s = self.lm._build_sessions([
            _entry("r1", T0, userId="u1", message="TTS provider created: elevenlabs (voice: vA)", voice_id="vA"),
            _entry("r1", T10, userId="u1", message="TTS hot-swapped to elevenlabs (voice: vB)", voice_id="vB"),
        ])["r1"]
        self.assertEqual((s.tts_voice_id, s.tts_voice_resolved), ("vB", True))

    def test_a_non_string_or_empty_voice_is_never_logged_not_a_voice(self):
        for bad in ("", None, 42):
            with self.subTest(voice=bad):
                s = self.lm._build_sessions([
                    _entry("r1", T0, userId="u1", message="TTS provider created: elevenlabs", voice_id=bad),
                ])["r1"]
                self.assertIsNone(s.tts_voice_id)


class TestVoiceBuckets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lm = _load("livekit_minutes")

    def _sessions(self, *specs):
        """specs: (room, user, provider, voice, minutes)."""
        out = {}
        for room, user, provider, voice, minutes in specs:
            s = self.lm._Session(room=room, user_id=user, tts_provider=provider, tts_voice_id=voice,
                                 tts_voice_resolved=voice is not None)
            s.first_ts = self.lm._parse_ts(T0)
            s.last_ts = self.lm._parse_ts(T0).__class__.fromtimestamp(
                s.first_ts.timestamp() + minutes * 60, tz=s.first_ts.tzinfo)
            out[room] = s
        return out

    def test_two_voices_on_one_provider_split_and_sum_back_to_the_provider_total(self):
        """The acceptance criterion: a day's ElevenLabs minutes split by voice, same total as before."""
        _, total, b = self.lm._aggregate_sessions(self._sessions(
            ("r1", "u1", "elevenlabs", "vA", 10.0),
            ("r2", "u2", "elevenlabs", "vB", 5.0),
            ("r3", "u1", "elevenlabs", "vB", 2.5),
            ("r4", "u3", "cartesia", "c1", 4.0),
        ))
        self.assertAlmostEqual(total, 21.5)
        self.assertAlmostEqual(b["minutes_by_provider"]["elevenlabs"], 17.5)
        self.assertEqual({v: round(m, 6) for v, m in b["minutes_by_provider_voice"]["elevenlabs"].items()},
                         {"vA": 10.0, "vB": 7.5})
        self.assertAlmostEqual(sum(b["minutes_by_provider_voice"]["elevenlabs"].values()),
                               b["minutes_by_provider"]["elevenlabs"])
        # per user, too: u1 = 10 (vA) + 2.5 (vB) == its provider bucket
        self.assertEqual(b["by_user_tts_voice"]["elevenlabs"]["vA"], {"u1": 10.0})
        self.assertEqual(b["by_user_tts_voice"]["elevenlabs"]["vB"], {"u2": 5.0, "u1": 2.5})
        self.assertAlmostEqual(b["by_user_tts"]["elevenlabs"]["u1"], 12.5)
        # the provider-level shape the store consumes is untouched
        self.assertAlmostEqual(b["minutes_by_provider"]["cartesia"], 4.0)
        self.assertEqual(b["by_user_tts"]["cartesia"], {"u3": 4.0})

    def test_a_provider_with_no_voice_id_books_under_the_explicit_unknown_key(self):
        """Never a real voice, never dropped — and counted."""
        _, _, b = self.lm._aggregate_sessions(self._sessions(
            ("r1", "u1", "elevenlabs", None, 3.0),
            ("r2", "u2", "elevenlabs", "vA", 1.0),
        ))
        self.assertEqual(b["minutes_by_provider_voice"]["elevenlabs"],
                         {self.lm.UNKNOWN_VOICE_ID: 3.0, "vA": 1.0})
        self.assertEqual(b["sessions_without_voice_id"], 1)
        self.assertNotIn(None, b["by_user_tts_voice"]["elevenlabs"])
        self.assertNotIn("", b["by_user_tts_voice"]["elevenlabs"])

    def test_a_session_with_no_provider_follows_the_default_provider_into_the_voice_split(self):
        """Old-window sessions keep their pre-existing behaviour: default provider, unknown voice."""
        _, _, b = self.lm._aggregate_sessions(self._sessions(("r1", "u1", None, None, 2.0)))
        default = self.lm.DEFAULT_TTS_PROVIDER
        self.assertAlmostEqual(b["minutes_by_provider"][default], 2.0)
        self.assertEqual(b["minutes_by_provider_voice"][default], {self.lm.UNKNOWN_VOICE_ID: 2.0})
        self.assertEqual(b["sessions_without_provider"], 1)

    def test_userless_minutes_count_toward_the_voice_denominator_but_not_a_user(self):
        by_user, total, b = self.lm._aggregate_sessions(self._sessions(
            ("r1", None, "elevenlabs", "vA", 6.0),
            ("r2", "u1", "elevenlabs", "vA", 4.0),
        ))
        self.assertAlmostEqual(total, 10.0)
        self.assertEqual(by_user, {"u1": 4.0})
        self.assertAlmostEqual(b["minutes_by_provider_voice"]["elevenlabs"]["vA"], 10.0)
        self.assertEqual(b["by_user_tts_voice"]["elevenlabs"]["vA"], {"u1": 4.0})

    def test_requested_only_sessions_are_split_and_counted(self):
        sessions = self._sessions(("r1", "u1", "elevenlabs", "asked", 1.0))
        sessions["r1"].tts_voice_resolved = False
        _, _, b = self.lm._aggregate_sessions(sessions)
        self.assertEqual(b["minutes_by_provider_voice"]["elevenlabs"], {"asked": 1.0})
        self.assertEqual(b["sessions_with_requested_voice_only"], 1)
        self.assertEqual(b["sessions_without_voice_id"], 0)


class TestEndToEndFromEntries(unittest.TestCase):
    """Entries → sessions → buckets, the way `day_minutes_by_user` composes them."""

    @classmethod
    def setUpClass(cls):
        cls.lm = _load("livekit_minutes")

    def test_two_arms_of_one_provider_are_distinguishable_from_log_rows_alone(self):
        entries = [
            _entry("r1", T0, userId="u1", message="TTS provider created: elevenlabs (voice: vA)",
                   tts_provider="elevenlabs", voice_id="vA"),
            _entry("r1", T10, userId="u1", message="Session usage totals"),
            _entry("r2", T0, userId="u2", message="TTS provider created: elevenlabs (voice: vB)",
                   tts_provider="elevenlabs", voice_id="vB"),
            _entry("r2", T20, userId="u2", message="Session usage totals"),
        ]
        _, total, b = self.lm._aggregate_sessions(self.lm._build_sessions(entries))
        self.assertAlmostEqual(total, 30.0)
        self.assertEqual({v: round(m, 6) for v, m in b["minutes_by_provider_voice"]["elevenlabs"].items()},
                         {"vA": 10.0, "vB": 20.0})
        self.assertAlmostEqual(b["minutes_by_provider"]["elevenlabs"], 30.0)


if __name__ == "__main__":
    unittest.main()
