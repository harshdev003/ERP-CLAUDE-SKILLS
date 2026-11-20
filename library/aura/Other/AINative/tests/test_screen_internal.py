"""Tests for `data:analyze-experiment`'s internal-account screen — code matching + cache rule.

Two branches real data cannot reach, and both fail silently in the same direction: an
internal account that stays in the experiment population, inflating one arm.

1. **Code shape coverage.** A prefix-anchored pattern reads as if it covers the codes,
   because the ones anybody thinks to test with (`AURIE-TESTER`, `AURIE_TESTER_UNLIMITED`)
   are prefixed. The ones it misses — `SUSAN2026-AURIE`, `AURIE-DESTINY` — surface only as
   a slightly-off rate nobody can attribute. So the full live corpus is asserted, not a
   sample of it, and genuine acquisition codes are asserted to survive: over-matching
   silently drops real users from the analysis.

2. **Cache invalidation on a rule change.** Verdicts persist with a 7-day TTL, so widening
   the rule and trusting the cache means a `clean` decided by the narrower rule keeps
   answering for a week. Nothing about the run looks degraded. The transport is stubbed at
   its single chokepoint so the test can distinguish "re-checked" from "trusted".
"""

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCREEN = REPO_ROOT / "plugins/data/skills/analyze-experiment/scripts/screen_internal.py"


def load_screen():
    spec = importlib.util.spec_from_file_location("screen_internal", SCREEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


screen = load_screen()

# Every redemption code live in the Aura subscription table, verbatim. Each one is a staff,
# tester or comp grant, so each one must screen out.
LIVE_INTERNAL_CODES = [
    "AURIE-CREATOR",
    "AURIE-DESTINY",
    "AURIE-EMPLOYEE",
    "AURIE-FRIEND",
    "AURIE-LEAH",
    "AURIE-TESTER",
    "AURIE_TESTER_NO_EXPERIMENT_UNLIMITED",
    "AURIE_TESTER_UNLIMITED",
    "JERRY2026-AURIE",
    "MARIE2026-AURIE",
    "NEIL2026-AURIE",
    "QA_AURIE_SIM_TEST_1MONTH",
    "SHANNON2026-AURIE",
    "SUSAN2026-AURIE",
    "AURA-TESTER",
]

# Codes held by real acquired users. Dropping one of these removes a genuine participant.
ACQUISITION_CODES = [
    "PODCAST20",
    "SUMMER2026",
    "AURA-SUMMER-SALE",
    "AURAPODCAST50",
    "BLACKFRIDAY",
    "HUBERMAN",
    "MINDFUL-AURA-10",
]


class CodeMatching(unittest.TestCase):
    def test_every_live_internal_code_matches(self):
        for code in LIVE_INTERNAL_CODES:
            with self.subTest(code=code):
                self.assertTrue(screen.is_internal_code(code))

    def test_formatting_variants_of_the_same_code_match(self):
        # Normalisation is the whole reason a single rule can cover the corpus.
        for code in ("aurie-tester", " AURIE_TESTER ", "aurie tester", "Susan2026-Aurie"):
            with self.subTest(code=code):
                self.assertTrue(screen.is_internal_code(code))

    def test_acquisition_codes_do_not_match(self):
        for code in ACQUISITION_CODES:
            with self.subTest(code=code):
                self.assertFalse(screen.is_internal_code(code))

    def test_absent_code_is_not_internal(self):
        # A subscription with no code must be clean, never internal — `None` reaches this
        # function directly from the API payload.
        for code in (None, "", "   "):
            with self.subTest(code=code):
                self.assertFalse(screen.is_internal_code(code))


class CacheRuleTagging(unittest.TestCase):
    """`code_uids` must not trust a verdict decided by a different matching rule."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = str(pathlib.Path(self.tmp.name) / "verdicts.json")
        self.calls = []

        # Single transport chokepoint. Every UID answers with one AURIE-suffixed
        # subscription, so anything actually re-checked comes back internal.
        def fake_urlopen(req, **_kwargs):
            uid = req.full_url.rsplit("/", 1)[-1]
            self.calls.append(uid)
            return FakeResponse([{"type": "ai", "code": "SUSAN2026-AURIE"}])

        self._patch(screen.urllib.request, "urlopen", fake_urlopen)
        self._patch(screen, "_aura_token", lambda _probe_uid: "token")
        self._patch(screen, "_ssl_context", lambda: None)

    def _patch(self, target, name, value):
        original = getattr(target, name)
        setattr(target, name, value)
        self.addCleanup(setattr, target, name, original)

    def _write_cache(self, entry):
        with open(self.cache, "w", encoding="utf-8") as handle:
            json.dump({"u1": entry}, handle)

    def test_verdict_from_the_current_rule_is_trusted(self):
        self._write_cache({"internal": False, "at": screen._now_iso(),
                           "rule": screen.CODE_RULE})
        internal, failed = screen.code_uids(["u1"], self.cache)
        self.assertEqual((internal, failed, self.calls), (set(), [], []))

    def test_verdict_from_a_superseded_rule_is_rechecked(self):
        self._write_cache({"internal": False, "at": screen._now_iso(), "rule": "deadbeef"})
        internal, failed = screen.code_uids(["u1"], self.cache)
        self.assertEqual(self.calls, ["u1"])
        self.assertEqual((internal, failed), ({"u1"}, []))

    def test_verdict_with_no_rule_is_rechecked(self):
        # Written by a script version that stamped no rule — unverifiable, same as a
        # timestampless entry.
        self._write_cache({"internal": False, "at": screen._now_iso()})
        internal, _failed = screen.code_uids(["u1"], self.cache)
        self.assertEqual(self.calls, ["u1"])
        self.assertEqual(internal, {"u1"})

    def test_written_verdicts_carry_the_current_rule(self):
        screen.code_uids(["u1"], self.cache)
        with open(self.cache, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["u1"]["rule"], screen.CODE_RULE)

    def test_failed_lookup_under_a_superseded_internal_verdict_keeps_excluding(self):
        # A re-check that cannot answer must not readmit a known tester, whichever rule
        # decided it — the conservative direction on this path.
        self._write_cache({"internal": True, "at": "2000-01-01T00:00:00+00:00",
                           "rule": "deadbeef"})

        def raising_urlopen(req, **_kwargs):
            self.calls.append(req.full_url)
            raise OSError("network down")

        self._patch(screen.urllib.request, "urlopen", raising_urlopen)
        internal, failed = screen.code_uids(["u1"], self.cache)
        self.assertEqual((internal, failed), ({"u1"}, []))


class FakeResponse:
    """Minimal stand-in for the `urlopen` context manager `json.load` is handed."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


if __name__ == "__main__":
    sys.exit(unittest.main())
