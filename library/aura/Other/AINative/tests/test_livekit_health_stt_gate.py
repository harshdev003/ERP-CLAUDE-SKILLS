"""`eng:report-livekit-health` — the STT-gate and noise-guard lines.

v1.7.0 of the LiveKit agent gates short low-confidence STT finals (PROD-5455) and logs the two
noise guards a session ran with (ENG-1229). The daily health report is the only thing that reads
either signal, so what it computes has to be right in the places a wrong answer is silent:

- the gate's share is taken over turns **plus** suppressed finals — a suppressed final never
  becomes a turn, and that is the base the expectation is measured against;
- Cloud Logging hands JSON numbers back as floats, so `min_interruption_words=1` arrives as
  `1.0` and must not read as a second distinct value;
- a guard with two values in one window is drift and is flagged; rows from an agent that never
  logged the fields are reported as such, not as drift.

Stdlib `unittest`; `google.*` is stubbed so the module imports without the SDK or a key.
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
REPORT = REPO / "plugins/eng/skills/report-livekit-health/scripts/report.py"


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
    finder = _GoogleStubFinder()
    saved = {k: v for k, v in sys.modules.items() if k == "google" or k.startswith("google.")}
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


def _load(path: pathlib.Path, name: str):
    with _stubbed_google():
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


R = _load(REPORT, "livekit_health_report_under_test")


def _entry(message: str, **fields) -> dict:
    return {"jsonPayload": {"message": message, **fields}}


def _suppressed(confidence: float, floor: float = 0.5) -> dict:
    return _entry("Suppressed low-confidence STT final", confidence=confidence, floor=floor, word_count=1)


def _config(**guards) -> dict:
    return _entry("Turn handling configured", endpointing_mode="dynamic", **guards)


class SttGateShare(unittest.TestCase):
    def test_share_is_over_turns_plus_suppressed(self):
        # 4 dropped finals against 996 turns that happened -> 4 / 1000, not 4 / 996.
        stats = R.stt_gate_stats([_suppressed(0.3)] * 4, user_turns=996)
        self.assertEqual(stats["suppressed"], 4)
        self.assertEqual(stats["turns_plus_suppressed"], 1000)
        self.assertEqual(stats["pct"], 0.4)
        self.assertEqual(stats["flag"], "ok")
        self.assertEqual(stats["floor"], 0.5)

    def test_an_ordinary_rate_near_the_expectation_does_not_flag(self):
        # The gate's measured share sits around 2.2%, and daily noise carries it well past 3% on
        # its own. Flagging there is what made the report cry wolf every day (ENG-1295).
        stats = R.stt_gate_stats([_suppressed(0.3)] * 30, user_turns=970)
        self.assertEqual(stats["pct"], 3.0)
        self.assertEqual(stats["flag"], "ok")

    def test_flags_high_well_above_the_expectation(self):
        stats = R.stt_gate_stats([_suppressed(0.2)] * 60, user_turns=940)
        self.assertEqual(stats["pct"], 6.0)
        self.assertEqual(stats["flag"], "high")

    def test_no_turns_and_no_suppressions_has_no_share(self):
        stats = R.stt_gate_stats([_entry("Turn latency", metric="eou_delay_ms")], user_turns=0)
        self.assertEqual(stats["suppressed"], 0)
        self.assertIsNone(stats["pct"])
        self.assertIsNone(stats["flag"])

    def test_unrelated_rows_are_not_counted(self):
        entries = [_entry("User turn shape", word_count=1, transcript_confidence=0.1), _suppressed(0.4)]
        self.assertEqual(R.stt_gate_stats(entries, user_turns=10)["suppressed"], 1)

    def test_expectation_and_threshold_travel_in_the_payload(self):
        stats = R.stt_gate_stats([], user_turns=1)
        self.assertEqual(stats["expected_pct"], R.STT_GATE_EXPECTED_PCT)
        self.assertEqual(stats["high_pct"], R.STT_GATE_HIGH_PCT)
        self.assertGreater(R.STT_GATE_HIGH_PCT, R.STT_GATE_EXPECTED_PCT)


class NoiseGuards(unittest.TestCase):
    def test_float_and_int_of_the_same_value_are_one_value(self):
        # Cloud Logging returns 1 as 1.0; a naive str() would count "1" and "1.0" separately.
        entries = [_config(noise_cancellation="bvc", min_interruption_words=1.0),
                   _config(noise_cancellation="bvc", min_interruption_words=1)]
        stats = R.noise_guard_stats(entries)
        self.assertEqual(stats["values"]["min_interruption_words"], {"1": 2})
        self.assertEqual(stats["values"]["noise_cancellation"], {"bvc": 2})
        self.assertFalse(stats["drift"])
        self.assertEqual(stats["sessions_with_fields"], 2)

    def test_two_values_in_one_window_is_drift(self):
        entries = [_config(noise_cancellation="bvc", min_interruption_words=1)] * 3 + [
            _config(noise_cancellation="nc", min_interruption_words=1)
        ]
        stats = R.noise_guard_stats(entries)
        self.assertTrue(stats["drift"])
        self.assertEqual(stats["values"]["noise_cancellation"], {"bvc": 3, "nc": 1})
        self.assertEqual(R._fmt_guard(stats["values"]["noise_cancellation"]), "bvc×3, nc×1")

    def test_rows_without_the_fields_are_not_drift(self):
        # An agent older than 1.7.0 logs the row without either guard.
        stats = R.noise_guard_stats([_config(), _config()])
        self.assertEqual(stats["sessions"], 2)
        self.assertEqual(stats["sessions_with_fields"], 0)
        self.assertFalse(stats["drift"])
        self.assertEqual(R._fmt_guard({}), "?")


class RenderedLines(unittest.TestCase):
    def _render(self, entries):
        worker = {"available": False, "reason": "test"}
        text, payload = R.render(worker, entries, "test window", None)
        return text, payload

    def test_both_lines_render_and_land_in_the_payload(self):
        entries = [_config(noise_cancellation="bvc", min_interruption_words=1.0)]
        text, payload = self._render(entries)
        self.assertIn("- STT gate:            0 suppressed finals (no turns in window)", text)
        self.assertIn("noise_cancellation=bvc, min_interruption_words=1 (n=1 sessions)", text)
        self.assertNotIn("⚠️ more than one value", text)
        self.assertEqual(payload["stt_gate"]["suppressed"], 0)
        self.assertEqual(payload["noise_guards"]["values"]["noise_cancellation"], {"bvc": 1})

    def test_high_share_and_drift_are_marked(self):
        entries = [_suppressed(0.1)] + [_config(noise_cancellation="bvc", min_interruption_words=1),
                                        _config(noise_cancellation="off", min_interruption_words=1)]
        text, _ = self._render(entries)
        # No turns happened, so the one suppressed final is 100% of the base — flagged, not hidden.
        self.assertIn("1 suppressed finals = 100.00% of turns", text)
        self.assertIn("⚠️ high", text)
        self.assertIn("⚠️ more than one value in window", text)

    def test_old_agent_reports_guards_as_not_logged(self):
        text, _ = self._render([_config()])
        self.assertIn("- Noise guards:        not logged by this agent version", text)


class WindowRowSelectionTest(unittest.TestCase):
    """The server-side filter must select every row the report counts — a row it misses reads as zero."""

    def test_every_counted_row_is_selected(self):
        clause = R.window_rows_clause()
        for needle in (
            'jsonPayload.message:"Session starting"',
            'jsonPayload.message="Session config resolved"',
            'jsonPayload.message="Session ended; reporting conversation metrics"',
            'jsonPayload.metric=("eou_delay_ms" OR "llm_ttft_ms")',
            f'jsonPayload.message:"{R._STT_GATE_MESSAGE}"',
            f'jsonPayload.message:"{R._TURN_CONFIG_MESSAGE}"',
            "severity>=ERROR",
        ):
            self.assertIn(needle, clause)
        for marker in R._DISPATCH_FAILURE_MARKERS:
            self.assertIn(f'jsonPayload.message:"{marker}"', clause)

    def test_baseline_reads_only_session_open_and_close(self):
        clause = R.session_span_clause()
        self.assertIn("Session config resolved", clause)
        self.assertIn("Session ended; reporting conversation metrics", clause)
        self.assertNotIn("Turn latency", clause)

    def test_delta_window_side_keeps_only_session_span_rows(self):
        rows = [
            _entry("Session starting (room=aurie-room-1)", room="aurie-room-1"),
            _entry("Session config resolved", room="aurie-room-1"),
            _entry("Turn latency", room="aurie-room-1", metric="eou_delay_ms", value_ms=400),
            _entry("Session ended; reporting conversation metrics", room="aurie-room-1"),
            _entry("Turn latency", room="aurie-room-2", metric="eou_delay_ms", value_ms=400),
        ]
        kept = [e["jsonPayload"]["message"] for e in R.session_span_rows(rows)]
        self.assertEqual(
            kept,
            [
                "Session starting (room=aurie-room-1)",
                "Session config resolved",
                "Session ended; reporting conversation metrics",
            ],
        )

    def test_clauses_are_parenthesised_for_the_and_join(self):
        for clause in (R.window_rows_clause(), R.session_span_clause()):
            self.assertTrue(clause.startswith("(") and clause.endswith(")"))


if __name__ == "__main__":
    unittest.main()
