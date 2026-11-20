#!/usr/bin/env python3
"""`_el_caller_of` — the ElevenLabs key → caller mapping in `store.py`.

Why this needs a test at all
----------------------------
The function decides which vendor column a day's ElevenLabs spend lands in. Every
way it can be wrong is **silent**: the day total still reconciles to the workspace
bill either way, so a misattribution shows up as a plausible-looking number in the
wrong column, not as an error. Two real ones have shipped:

  - `product_type=TTS` read as LiveKit booked ~8 weeks of Intelligence Service
    spend into `voice_11labs_livekit` (ENG-982).
  - The LiveKit service-account key went unmapped when LiveKit finally started
    calling ElevenLabs on 2026-07-31, folding its spend back into Intelligence
    Service — the same bug in the opposite direction (PROD-4316).

Both were found by eye, days later. The mapping is a pure function over a string,
so it costs nothing to pin.

What the cases are
------------------
`KNOWN_LABELS` is the full set of labels `breakdown_type=all_api_keys` has
returned with non-zero spend, cross-referenced against `GET /v1/service-accounts`
(whose `hashed_xi_api_key` field is byte-for-byte the label prefix). It is a
corpus, not a sample — the ENG-982 bug survived precisely because the earlier
check looked at the two keys that were billing and not at the inventory.

`observed` in `test_observed_label_forms_per_day` is the other half of the corpus,
and the reason it exists: a label is sometimes the key hash and sometimes the key
NAME, chosen per query by ElevenLabs. The same LiveKit key answers
"7e7d1476d1b33139 (...7e7d)" for one window and "TTS [LiveKit Prod]" for another.
A suite that only fed it hashes would pass while the name path was broken — which
is what happened. A console rename is also applied RETROACTIVELY, so a label
recorded for a past day is not stable either.

The load-bearing case is `test_caller_token_beats_a_legacy_bare_name`. Every live
key is now named after its caller ("TTS [LiveKit Prod]"), which is what makes a
name label self-attributing — but matching is substring-based, so
"TTS [LiveKit Prod]" must not be read as the legacy bare "tts[prod]". That
misreading is the original defect, so the ordering is pinned rather than trusted.

Loading `store.py` without the cloud SDKs
----------------------------------------
`store.py` imports its sibling `livekit_minutes`, which does
`from google.cloud import logging` at module scope and `sys.exit(2)` if it is
missing — so a plain import would kill the whole `unittest discover` process on
any machine without `google-cloud-logging`, CI included (the suite is
deliberately stdlib-only). `_load_store` therefore seeds `sys.modules` with
stubs for the `google.*` namespace before executing the module, the same
stub-the-third-party-edge trick `reader_harness` uses for the transport. The
function under test touches none of it — it maps a string to a string.

Stdlib only, no network, no keys.
"""
from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import sys
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
STORE = (REPO_ROOT / "plugins" / "eng" / "skills" / "store-aurie-user-daily-cost"
         / "scripts" / "store.py")

# label → expected caller. Every entry corresponds to a key confirmed against the
# live service-account inventory. The two "pre-cutover" ones are deleted user keys,
# which still report under their original bare names as well as by hash.
KNOWN_LABELS = {
    # SA LiveKit / TTS [LiveKit Prod] — first billed 2026-07-31
    "7e7d1476d1b33139 (...7e7d)": "livekit",
    # SA Intelligence Service
    "b81e082fbbfddd02 (...b81e)": "intelligence",   # TTS [Intelligence Service Prod] (…7d22)
    "0bcda5a1f7649c3c (...0bcd)": "intelligence",   # TTS [Intelligence Service Dev]  (…4697)
    "9408b12da82bc96a (...9408)": "intelligence",   # Conversational [Intelligence Service Prod]
    "0d611bc23559de9d (...0d61)": "intelligence",   # Conversational [Intelligence Service Dev]
    # pre-cutover user keys on <redacted-email>, both Intelligence Service
    "944c051ecf733e9c (...944c)": "intelligence",   # billed daily    → TTS [Prod]
    "482373b5eec12192 (...4823)": "intelligence",   # sporadic        → TTS [Dev]
}



# Only the module paths `livekit_minutes` actually imports. Kept explicit rather
# than a catch-all import hook so that a NEW cloud dependency appearing in the
# import chain fails loudly here instead of being silently absorbed.
_GOOGLE_STUBS = ("google", "google.auth", "google.cloud", "google.cloud.logging",
                 "google.cloud.bigquery", "google.oauth2",
                 "google.oauth2.service_account",
                 # livekit_minutes.py catches google.api_core's quota errors, and
                 # store.py imports it, so the stub has to reach that far or
                 # store.py's dependency guard exits the interpreter (ENG-1214).
                 "google.api_core", "google.api_core.exceptions",
                 # Every google-cloud client is built with client_info (PROD-7815).
                 "google.api_core.gapic_v1", "google.api_core.gapic_v1.client_info")

# The stubbed `google.api_core.exceptions` needs real exception CLASSES, not bare
# attributes: they appear in an `except (...)` tuple, which raises TypeError at
# runtime on anything that isn't a BaseException subclass.
_API_CORE_EXCEPTIONS = ("ResourceExhausted", "ServiceUnavailable", "DeadlineExceeded")


@contextlib.contextmanager
def _stubbed_google():
    """Seed `sys.modules` with empty `google.*` modules, then restore.

    Restoring matters: `unittest discover` shares one interpreter, so leaving
    stubs installed would let a later test import a fake `google` and pass for
    the wrong reason.
    """
    saved = {name: sys.modules.get(name) for name in _GOOGLE_STUBS}
    try:
        for name in _GOOGLE_STUBS:
            if sys.modules.get(name) is None:
                mod = types.ModuleType(name)
                # `from google.cloud import logging` needs the attribute on the
                # parent package, not just the sys.modules entry.
                if "." in name:
                    parent, _, leaf = name.rpartition(".")
                    setattr(sys.modules[parent], leaf, mod)
                if name == "google.api_core.gapic_v1.client_info":
                    mod.ClientInfo = type("ClientInfo", (), {"__init__": lambda self, **kw: self.__dict__.update(kw)})
                if name == "google.api_core.exceptions":
                    for exc in _API_CORE_EXCEPTIONS:
                        setattr(mod, exc, type(exc, (Exception,), {}))
                sys.modules[name] = mod
        yield
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


def _load_store():
    with _stubbed_google():
        spec = importlib.util.spec_from_file_location("store_under_test", STORE)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["store_under_test"] = mod
        spec.loader.exec_module(mod)
    return mod


class ElCallerMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = _load_store()

    def test_known_labels_map_to_their_confirmed_caller(self):
        for label, expected in KNOWN_LABELS.items():
            with self.subTest(label=label):
                self.assertEqual(self.store._el_caller_of(label), expected)

    def test_bare_hash_without_hint_suffix_still_maps(self):
        """The hint suffix is cosmetic; matching must not depend on it."""
        for label, expected in KNOWN_LABELS.items():
            bare = label.split(" ")[0]
            with self.subTest(label=bare):
                self.assertEqual(self.store._el_caller_of(bare), expected)

    def test_livekit_key_is_not_folded_into_intelligence(self):
        """The regression PROD-4316 fixed, pinned on its own.

        LiveKit is the narrow side of the split and Intelligence Service is the
        fold-default, so this is the only direction in which a mistake is
        invisible in the day total.
        """
        self.assertEqual(self.store._el_caller_of("7e7d1476d1b33139 (...7e7d)"),
                         "livekit")

    def test_caller_qualified_names_resolve_without_a_per_key_entry(self):
        """The payoff of naming each key after its caller.

        Every live service-account key carries its caller in its name, so a name
        label is self-attributing and a newly-minted key splits correctly with no
        code change. These are the exact strings the live API returns.
        """
        cases = {
            "TTS [LiveKit Prod]": "livekit",
            "TTS [Intelligence Service Prod]": "intelligence",
            "TTS [Intelligence Service Dev]": "intelligence",
            "Conversational [Intelligence Service Prod]": "intelligence",
            "Conversational [Intelligence Service Dev]": "intelligence",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.store._el_caller_of(name), expected)

    def test_leading_whitespace_in_a_key_name_is_tolerated(self):
        """The live Prod key is literally named ' TTS [Intelligence Service Prod]'.

        A stray leading space in the console is cosmetic and must not change
        attribution, so normalisation has to strip it.
        """
        self.assertEqual(
            self.store._el_caller_of(" TTS [Intelligence Service Prod]"),
            "intelligence")

    def test_legacy_bare_names_resolve_to_intelligence(self):
        """Pre-cutover user keys, deleted, so these appear only in old windows.

        Both were Intelligence Service — LiveKit held no ElevenLabs key before the
        2026-07-28 cutover, so no LiveKit spend can hide behind either label.
        Confirmed by hash: TTS [Dev] = 482373b5eec12192, TTS [Prod] = 944c…
        """
        for name in ("TTS [Prod]", "TTS [Dev]", "tts[prod]", "tts[dev]"):
            with self.subTest(name=name):
                self.assertEqual(self.store._el_caller_of(name), "intelligence")

    def test_caller_token_beats_a_legacy_bare_name(self):
        """Ordering guarantee, and the one that actually bites.

        "TTS [LiveKit Prod]" must not be read as the legacy "tts[prod]" — that
        would book LiveKit spend to Intelligence Service, which is the original
        defect. Substring matching makes this an ordering property, not an
        accident, so it is pinned.
        """
        self.assertEqual(self.store._el_caller_of("TTS [LiveKit Prod]"), "livekit")
        self.assertEqual(
            self.store._el_caller_of("TTS [Intelligence Service Prod]"),
            "intelligence")

    def test_observed_label_forms_per_day(self):
        """The real corpus: every label form seen on a single-day window.

        Recorded from live `all_api_keys` responses. Two things a synthetic list
        would have missed: the label is sometimes a hash and sometimes a name, and
        a console rename is applied RETROACTIVELY — 2026-07-31 answered
        "TTS [Prod]" before the keys were renamed and "TTS [LiveKit Prod]" after,
        for the same day and the same key.
        """
        observed = {
            "2026-07-31": [("TTS [LiveKit Prod]", "livekit")],
            "2026-08-03": [("TTS [Intelligence Service Dev]", "intelligence"),
                           (" TTS [Intelligence Service Prod]", "intelligence")],
            "2026-08-04": [("TTS [LiveKit Prod]", "livekit"),
                           (" TTS [Intelligence Service Prod]", "intelligence")],
            "2026-08-06": [("TTS [Intelligence Service Dev]", "intelligence"),
                           ("TTS [LiveKit Prod]", "livekit"),
                           (" TTS [Intelligence Service Prod]", "intelligence")],
            # the six-week window, where the deleted pre-cutover keys still appear
            "2026-06-25..08-07": [("TTS [Dev]", "intelligence"),
                                  ("TTS [Prod]", "intelligence"),
                                  ("TTS [LiveKit Prod]", "livekit")],
        }
        for day, labels in observed.items():
            for label, expected in labels:
                with self.subTest(day=day, label=label):
                    self.assertEqual(self.store._el_caller_of(label), expected)

    def test_no_caller_token_appears_in_another_callers_token(self):
        """Tokens are substring-matched, so containment would cross-attribute."""
        tokens = [t for t, _ in self.store._EL_CALLER_TOKENS]
        for a in tokens:
            for b in tokens:
                if a != b:
                    with self.subTest(a=a, b=b):
                        self.assertNotIn(a, b)

    def test_every_live_key_name_is_caller_qualified(self):
        """States the invariant that makes the legacy bare names safe.

        The bare labels "TTS [Prod]" / "TTS [Dev]" resolve to Intelligence Service
        only because no LIVE key carries a bare environment-only name any more. If
        someone creates one, it collides with the legacy label and the caller
        becomes unrecoverable from a name response.

        This cannot check the live workspace offline, so it asserts the local half:
        no caller token is itself a bare legacy name, and every bare legacy name
        lacks a caller token — i.e. the two tiers cannot be confused.
        """
        tokens = [t for t, _ in self.store._EL_CALLER_TOKENS]
        for bare in self.store._EL_INTELLIGENCE_NAMES:
            with self.subTest(bare=bare):
                self.assertFalse(any(t in bare for t in tokens),
                                 f"{bare!r} carries a caller token; it is not a bare name")

    def test_caller_named_key_still_caught_by_substring_fallback(self):
        """If the keys are ever renamed per the Tools/11Labs action item."""
        for name in ("LiveKit TTS [Prod]", "TTS [LiveKit Prod]", "livekit-tts-prod"):
            with self.subTest(name=name):
                self.assertEqual(self.store._el_caller_of(name), "livekit")

    def test_unknown_key_returns_none_so_the_caller_warns(self):
        """An unmapped key must be reported, never silently attributed.

        `elevenlabs_caller_split` folds a None into Intelligence Service *and*
        prints a warning. Returning a caller here instead would keep the dollars
        but lose the signal that the inventory changed.
        """
        for label in ("deadbeefdeadbeef (...dead)", "", "None", "0"):
            with self.subTest(label=label):
                self.assertIsNone(self.store._el_caller_of(label))

    def test_no_key_is_claimed_by_both_callers(self):
        """The tuples must stay disjoint — order would decide, silently."""
        overlap = set(self.store._EL_INTELLIGENCE_KEYS) & set(self.store._EL_LIVEKIT_KEYS)
        self.assertEqual(overlap, set())

    def test_no_intelligence_token_is_a_substring_of_a_livekit_token(self):
        """Matching is substring-based, so containment is as bad as equality."""
        for lk in self.store._EL_LIVEKIT_KEYS:
            for intel in self.store._EL_INTELLIGENCE_KEYS:
                with self.subTest(livekit=lk, intelligence=intel):
                    self.assertNotIn(intel, lk)

    def test_every_known_label_is_covered_by_the_constants(self):
        """Guards the inventory drifting ahead of the mapping again.

        A key that starts billing while unmapped is exactly the PROD-4316
        failure. This does not detect a *new* key (nothing offline can), but it
        does stop a refactor from dropping one that is already known to bill.
        """
        mapped = set(self.store._EL_INTELLIGENCE_KEYS) | set(self.store._EL_LIVEKIT_KEYS)
        for label in KNOWN_LABELS:
            h = label.split(" ")[0]
            with self.subTest(label=h):
                self.assertIn(h, mapped)


if __name__ == "__main__":
    unittest.main()
