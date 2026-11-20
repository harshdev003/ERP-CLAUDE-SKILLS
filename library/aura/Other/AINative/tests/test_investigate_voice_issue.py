"""Tests for eng:investigate-voice-issue.

Covers the three branches real data cannot reach on demand: a rating whose uid is
not plain alphanumeric, a Bugsnag window that has to reach backwards in time, and
a reply watchdog whose window has to be judged against a vendor transcript.

Stdlib unittest, no network — the Bugsnag paginator and the ElevenLabs record are
stubbed at their single chokepoints so the out-of-window and no-agent-turn cases
can be forced.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

import os as _os
import tempfile as _tempfile

# bugsnag.py keeps a disk cache and a rate-limit cooldown file; never touch the real ones.
_os.environ["AINATIVE_BUGSNAG_CACHE_DIR"] = _tempfile.mkdtemp(prefix="bugsnag-test-")
_os.environ["BUGSNAG_CACHE"] = "0"
from datetime import datetime, timedelta, timezone

SKILL = (
    pathlib.Path(__file__).resolve().parents[1]
    / "plugins" / "eng" / "skills" / "investigate-voice-issue" / "scripts"
)
sys.path.insert(0, str(SKILL))

import bugsnag  # noqa: E402
import correlate  # noqa: E402
import elevenlabs as el  # noqa: E402
import investigate as inv  # noqa: E402

# Enough of `GET /projects/<id>/event_fields` for the filter guard to validate
# the keys these code paths send. Pre-cached so no stub needs a transport.
_EVENT_FIELDS = [
    {"display_id": "event.since", "filter_options": {"name": "Since"}},
    {"display_id": "event.before", "filter_options": {"name": "Before"}},
    {"display_id": "user.id",
     "filter_options": {"name": "User ID", "match_types": ["eq", "ne"]}},
    {"display_id": "error",
     "filter_options": {"name": "Error ID", "match_types": ["eq", "ne"]}},
]


class RatingParseTest(unittest.TestCase):
    """A Firebase uid is not always alphanumeric — see ENG-1060."""

    BLOCK = (
        "`[dialogue: A7B06648-D32E-4B55-8D96-FA19C03891FB, "
        "conversation: ecefbc4b-16da-499c-85bc-17fe7bdbd254, "
        "user: {uid}, prompt:coach-prompter:1.10.1]`"
    )

    def test_firebase_28_char_uid(self) -> None:
        got = inv.parse_rating_text(self.BLOCK.format(uid="vXNhqLTBnganmU7nmBW9UeXrJDg2"))
        self.assertEqual(got["user"], "vXNhqLTBnganmU7nmBW9UeXrJDg2")
        self.assertEqual(got["conversation"], "ecefbc4b-16da-499c-85bc-17fe7bdbd254")

    def test_provider_prefixed_uid_is_not_truncated(self) -> None:
        """`google:112…` must survive whole — a colon is not a delimiter here."""
        got = inv.parse_rating_text(self.BLOCK.format(uid="google:112345678901234567890"))
        self.assertEqual(got["user"], "google:112345678901234567890")

    def test_push_key_uid_with_underscores(self) -> None:
        got = inv.parse_rating_text(self.BLOCK.format(uid="-Nz_aBc123_xyZ-9"))
        self.assertEqual(got["user"], "-Nz_aBc123_xyZ-9")

    def test_missing_block_yields_nothing(self) -> None:
        self.assertEqual(inv.parse_rating_text("just a thumbs down, no ids"), {})


class AtWindowOffsetTest(unittest.TestCase):
    """`--at` must be normalised to UTC, not just tagged with its input offset.

    `collect_gcp` and `bugsnag_since`'s caller format `start`/`end` with
    `strftime("...Z")`, which prints wall-clock numbers verbatim and appends a
    literal Z — it does not convert a timezone. A k8bot rating time pasted
    straight from Slack carries the reporter's local offset (PDT here), so if
    `_parse_iso` returned that offset as-is, every downstream filter would
    silently query hours off from the intended window with no error anywhere
    in the digest — a real incident (2026-09-16) came back "could not
    investigate" for exactly this reason.
    """

    def test_parse_iso_converts_a_non_utc_offset(self) -> None:
        got = inv._parse_iso("2026-09-16T18:50:48-07:00")
        self.assertEqual(got, datetime(2026, 9, 17, 1, 50, 48, tzinfo=timezone.utc))
        self.assertEqual(got.utcoffset(), timedelta(0))

    def test_parse_iso_is_a_no_op_on_z_form(self) -> None:
        got = inv._parse_iso("2026-09-17T01:50:48Z")
        self.assertEqual(got, datetime(2026, 9, 17, 1, 50, 48, tzinfo=timezone.utc))

    def test_parse_iso_treats_naive_input_as_utc(self) -> None:
        got = inv._parse_iso("2026-09-17T01:50:48")
        self.assertEqual(got, datetime(2026, 9, 17, 1, 50, 48, tzinfo=timezone.utc))

    def test_resolve_window_matches_regardless_of_input_offset(self) -> None:
        """A PDT `--at` and its UTC-equivalent `--at` must resolve identically."""
        pdt_start, pdt_end, _ = inv.resolve_window("3h", "2026-09-16T18:50:48-07:00")
        utc_start, utc_end, _ = inv.resolve_window("3h", "2026-09-17T01:50:48Z")
        self.assertEqual(pdt_start, utc_start)
        self.assertEqual(pdt_end, utc_end)

    def test_resolve_window_bounds_survive_the_z_suffix_format(self) -> None:
        """Reproduces the bug exactly as `collect_gcp` and `event.before` build filters."""
        start, end, _ = inv.resolve_window("3h", "2026-09-16T18:50:48-07:00")
        self.assertEqual(start.strftime("%Y-%m-%dT%H:%M:%SZ"), "2026-09-17T00:20:48Z")
        self.assertEqual(end.strftime("%Y-%m-%dT%H:%M:%SZ"), "2026-09-17T03:20:48Z")


class BugsnagWindowTest(unittest.TestCase):
    """`event.since` is a trailing duration, so it must be widened to reach back."""

    def test_duration_reaches_the_window_start(self) -> None:
        now = datetime.now(timezone.utc)
        for delta, expected in [
            (timedelta(minutes=30), "1h"),
            (timedelta(hours=3), "3h"),
            (timedelta(hours=26), "2d"),
            (timedelta(days=5), "5d"),
        ]:
            with self.subTest(delta=delta):
                self.assertEqual(inv.bugsnag_since(now - delta), expected)

    def test_duration_is_capped(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(days=400)
        self.assertEqual(inv.bugsnag_since(old), f"{inv.BUGSNAG_MAX_SINCE_DAYS}d")


class _StubClient:
    """Bugsnag client stub yielding newest-first events, like the real endpoint."""

    def __init__(self, stamps: list[datetime]) -> None:
        self.stamps = stamps
        self.calls: list[dict] = []
        # Real filter builder, real validation — only the transport is stubbed.
        # So a bogus filter key in the code under test raises here exactly as it
        # would in production, instead of quietly widening the query.
        self._real = bugsnag.BugsnagClient(token="t")
        self._real._event_fields["pid"] = _EVENT_FIELDS

    def build_filters(self, project_id, filters):
        return self._real.build_filters(project_id, filters)

    def _paginate(self, path, params=None, max_pages=10):
        self.calls.append(dict(params or {}))
        before = (params or {}).get("filters[event.before]")
        cutoff = inv._parse_iso(before) if before else None
        for i, stamp in enumerate(self.stamps):
            # The real API applies event.before server-side; mirror that so the
            # test exercises the same shape the endpoint produces.
            if cutoff and stamp > cutoff:
                continue
            yield {"id": f"ev{i}", "received_at": stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")}


class UserEventWindowTest(unittest.TestCase):
    """The regression: newest-first events + a client-side trim = silently empty."""

    def setUp(self) -> None:
        self.incident = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
        self.start = self.incident - timedelta(hours=1)
        self.end = self.incident + timedelta(hours=1)
        recent = [datetime.now(timezone.utc) - timedelta(minutes=m) for m in range(0, 300, 10)]
        in_window = [self.incident, self.incident - timedelta(minutes=5)]
        older = [self.start - timedelta(hours=6)]
        self.stub = _StubClient(sorted(recent + in_window + older, reverse=True))

    def test_finds_events_in_a_historical_window(self) -> None:
        got = inv._user_events(
            self.stub, "pid", "err", "uid", "7d", start=self.start, end=self.end
        )
        self.assertTrue(got, "a historical window must still return its in-window events")
        for row in got:
            stamp = inv._received_at(row)
            self.assertGreaterEqual(stamp, self.start)
            self.assertLessEqual(stamp, self.end)

    def test_bounds_the_upper_end_server_side(self) -> None:
        inv._user_events(self.stub, "pid", "err", "uid", "7d", start=self.start, end=self.end)
        self.assertIn("filters[event.before]", self.stub.calls[0])

    def test_stops_once_events_predate_the_window(self) -> None:
        got = inv._user_events(
            self.stub, "pid", "err", "uid", "7d", start=self.start, end=self.end
        )
        stamps = [inv._received_at(r) for r in got]
        self.assertFalse([s for s in stamps if s < self.start])

    def test_undateable_rows_are_kept_not_dropped(self) -> None:
        class Undateable(_StubClient):
            def _paginate(self, path, params=None, max_pages=10):
                yield {"id": "ev0"}

        got = inv._user_events(
            Undateable([]), "pid", "err", "uid", "7d", start=self.start, end=self.end
        )
        self.assertEqual(len(got), 1)


def _conversation(start_unix: int, agent_offsets: list[int]) -> el.Conversation:
    turns = [
        el.Turn(index=i, role="agent", time_in_call_secs=off, chars=100, interrupted=False)
        for i, off in enumerate(agent_offsets)
    ]
    return el.Conversation(
        conversation_id="conv_test", agent_id="agent_test", status="done",
        start_unix=start_unix, duration_secs=600, message_count=len(turns),
        termination_reason="Client disconnected: 1000", call_successful="success",
        aura_conversation_id="ABC", aura_user_id="uid", platform="mobile", turns=turns,
    )


class WatchdogClassificationTest(unittest.TestCase):
    """The skill's core judgement: did a reply actually arrive in the window?"""

    START = 1_786_403_759  # call start

    def _meta(self, reply_start: int, waited: int = 45) -> dict:
        return {
            "replyStartTime": reply_start,
            "timestamp": reply_start + waited,
            "agentState": "listening",
            "timeoutDuration": waited,
        }

    def test_agent_replied_inside_window_is_a_false_positive(self) -> None:
        # Agent turns 234s and 271s into the call; watchdog window 229s-274s.
        conv = _conversation(self.START, [234, 271])
        got = correlate.classify_watchdog(self._meta(self.START + 229), [conv])
        self.assertEqual(got.verdict, "false-positive")
        self.assertEqual(got.agent_turns_in_window, 2)

    def test_silence_in_window_is_a_genuine_stall(self) -> None:
        conv = _conversation(self.START, [10, 400])
        got = correlate.classify_watchdog(self._meta(self.START + 229), [conv])
        self.assertEqual(got.verdict, "genuine")
        self.assertEqual(got.agent_turns_in_window, 0)

    def test_no_vendor_conversation_is_unverifiable_not_genuine(self) -> None:
        """Without the vendor leg a watchdog event is not evidence either way."""
        got = correlate.classify_watchdog(self._meta(self.START + 229), [])
        self.assertEqual(got.verdict, "unknown")

    def test_missing_metadata_is_unverifiable(self) -> None:
        conv = _conversation(self.START, [234])
        self.assertEqual(correlate.classify_watchdog({}, [conv]).verdict, "unknown")


class RedactionTest(unittest.TestCase):
    """Redaction is enforced in code, not left to the model's judgement."""

    def test_audio_route_drops_the_device_name(self) -> None:
        got = correlate.redact_audio_route("BluetoothHFP:Someone's AirPods #5")
        self.assertEqual(got, "BluetoothHFP:<device>")
        self.assertNotIn("Someone", got)

    def test_multiple_routes_each_redacted(self) -> None:
        got = correlate.redact_audio_route("BluetoothHFP:A's Buds,Speaker")
        self.assertEqual(got, "BluetoothHFP:<device>,Speaker")

    def test_uid_is_truncated_to_a_prefix(self) -> None:
        self.assertEqual(correlate.uid_prefix("vXNhqLTBnganmU7nmBW9UeXrJDg2"), "vXNhqLTB…")

    def test_uid_prefix_handles_missing_value(self) -> None:
        self.assertEqual(correlate.uid_prefix(None), "?")


class VerdictWithoutVendorTest(unittest.TestCase):
    """A missing vendor call must not discard the Aura-side evidence."""

    def _leg(self, worst_ms: int) -> correlate.OurLeg:
        leg = correlate.OurLeg()
        leg.openai_ms = [1000, worst_ms]
        leg.entry_count = 2
        return leg

    def test_slow_our_leg_is_reported_without_a_vendor_call(self) -> None:
        leg = self._leg(9000)
        self.assertEqual(leg.grade, "bad")
        got = correlate.build_verdict([], correlate.LatencyReport(), [], leg)
        self.assertIn("Our own LLM leg", got.symptom)
        self.assertIn("9.0s", got.symptom)
        self.assertEqual(got.confidence, "medium")

    def test_healthy_our_leg_still_reports_no_attribution(self) -> None:
        got = correlate.build_verdict([], correlate.LatencyReport(), [], self._leg(1200))
        self.assertIn("Cannot attribute", got.symptom)
        self.assertNotIn("Our own LLM leg", got.symptom)
        self.assertEqual(got.confidence, "low")

    def test_truncated_read_is_flagged_in_the_verdict(self) -> None:
        leg = self._leg(1200)
        leg.truncated = True
        got = correlate.build_verdict([], correlate.LatencyReport(), [], leg)
        self.assertIn("truncated", got.symptom)

    def test_nothing_answered_reads_differently_from_a_clean_call(self) -> None:
        """An all-legs-down run must not look like an absence of findings."""
        got = correlate.build_verdict([], correlate.LatencyReport(), [], correlate.OurLeg())
        self.assertIn("Could not investigate", got.symptom)
        self.assertIn("not evidence of a healthy call", got.symptom)
        self.assertEqual(got.confidence, "none")

    def test_a_watchdog_alone_still_counts_as_an_answer(self) -> None:
        wd = correlate.WatchdogVerdict("unknown", None, None, None, "listening", 0, "d")
        got = correlate.build_verdict([], correlate.LatencyReport(), [wd], correlate.OurLeg())
        self.assertNotIn("Could not investigate", got.symptom)

    def test_client_errors_alone_still_count_as_an_answer(self) -> None:
        """A transport signature yields rows but no watchdog meta — still an answer."""
        got = correlate.build_verdict(
            [], correlate.LatencyReport(), [], correlate.OurLeg(), client_errors=2
        )
        self.assertNotIn("Could not investigate", got.symptom)

    def test_watchdog_artifacts_survive_a_missing_vendor_call(self) -> None:
        wd = correlate.WatchdogVerdict(
            "false-positive", 100.0, 55.0, 45.0, "listening", 2, "detail"
        )
        got = correlate.build_verdict([], correlate.LatencyReport(), [wd], self._leg(1200))
        self.assertTrue(got.artifacts)


class _Entry:
    """Minimal stand-in for normalize.Normalized."""

    def __init__(self, timestamp: str, message: str, severity: str = "INFO") -> None:
        self.timestamp = timestamp
        self.message = message
        self.severity = severity
        self.component = ""


class OurLegFoldTest(unittest.TestCase):
    """The fold must not depend on the order entries arrive in."""

    ENTRIES = [
        _Entry("2026-08-10T23:10:00.000000Z", "Assigned experiment 'aiLiveKitIntegration3' value 'a'"),
        _Entry("2026-08-10T23:20:00.000000Z", "Assigned experiment 'aiLiveKitIntegration3' value 'b'"),
        _Entry("2026-08-10T23:15:00.000000Z", "Execution time for openai response: 2985 ms"),
    ]

    def test_newest_experiment_arm_wins_oldest_first(self) -> None:
        leg = correlate.summarize_our_leg(list(self.ENTRIES))
        self.assertEqual(leg.experiments["aiLiveKitIntegration3"], "b")

    def test_newest_experiment_arm_wins_newest_first(self) -> None:
        """The descending read must reach the same answer, not the oldest arm."""
        leg = correlate.summarize_our_leg(sorted(self.ENTRIES, key=lambda e: e.timestamp, reverse=True))
        self.assertEqual(leg.experiments["aiLiveKitIntegration3"], "b")

    def test_span_is_order_independent(self) -> None:
        asc = correlate.summarize_our_leg(list(self.ENTRIES))
        desc = correlate.summarize_our_leg(list(reversed(self.ENTRIES)))
        self.assertEqual((asc.first_ts, asc.last_ts), (desc.first_ts, desc.last_ts))
        self.assertEqual(asc.openai_ms, desc.openai_ms)

    def test_unstamped_entry_does_not_beat_a_stamped_one(self) -> None:
        entries = list(self.ENTRIES) + [
            _Entry("", "Assigned experiment 'aiLiveKitIntegration3' value 'c'")
        ]
        leg = correlate.summarize_our_leg(entries)
        self.assertEqual(leg.experiments["aiLiveKitIntegration3"], "b")


class VoicePathFamilyTest(unittest.TestCase):
    """The LiveKit arm is looked up by family, never by a pinned version.

    `aiLiveKitIntegration2` ended 2026-08-06 and `…3` replaced it on 2026-08-11;
    `…4` is planned. All three must resolve. The regex populating `leg.experiments`
    is generic, so a pinned lookup key fails silently — it only misses when no
    11Labs token was issued, which is exactly the LiveKit arm this readout exists
    to explain.

    When more than one family member is present the tiebreak is the assignment
    timestamp, with the trailing version as the fallback for unstamped names.
    """

    @staticmethod
    def _leg(**experiments: str) -> correlate.OurLeg:
        return correlate.OurLeg(experiments=dict(experiments))

    def test_no_version_in_the_family_falls_through_to_unknown(self) -> None:
        """The load-bearing negative: nothing in the family may read as unknown."""
        for name in ("aiLiveKitIntegration2", "aiLiveKitIntegration3", "aiLiveKitIntegration4",
                     "aiLiveKitIntegration12", "ailivekitintegration5", "aiLiveKitIntegration"):
            with self.subTest(name=name):
                path = self._leg(**{name: "b"}).voice_path
                self.assertNotEqual(path, "unknown")
                self.assertIn(name, path)
                self.assertIn("=b", path)

    def test_highest_version_is_the_fallback_when_nothing_is_stamped(self) -> None:
        """No stamps to compare, so the version decides — the fallback, not the rule."""
        leg = self._leg(aiLiveKitIntegration2="a", aiLiveKitIntegration3="b", aiLiveKitIntegration4="c")
        self.assertEqual(
            leg.voice_path, "unclear — aiLiveKitIntegration4=c, no 11Labs token call seen"
        )

    def test_double_digit_version_beats_a_single_digit_one(self) -> None:
        """String ordering would put …9 above …10; the compare must be numeric."""
        leg = self._leg(aiLiveKitIntegration9="a", aiLiveKitIntegration10="b")
        self.assertIn("aiLiveKitIntegration10=b", leg.voice_path)

    def test_versioned_name_beats_an_unversioned_one(self) -> None:
        leg = self._leg(aiLiveKitIntegration="a", aiLiveKitIntegration3="b")
        self.assertIn("aiLiveKitIntegration3=b", leg.voice_path)

    def test_the_stamped_assignment_wins_over_the_higher_version(self) -> None:
        """Timestamp first: version order must not override which arm was in force."""
        leg = correlate.OurLeg(
            experiments={"aiLiveKitIntegration3": "a", "aiLiveKitIntegration4": "b"},
            experiment_stamps={
                "aiLiveKitIntegration4": "2026-09-01T09:00:00.000000Z",
                "aiLiveKitIntegration3": "2026-09-01T10:00:00.000000Z",
            },
        )
        self.assertIn("aiLiveKitIntegration3=a", leg.voice_path)

    def test_a_stamped_name_beats_an_unstamped_one(self) -> None:
        """Same rule the per-name fold applies: unstamped only wins by default."""
        leg = correlate.OurLeg(
            experiments={"aiLiveKitIntegration4": "a", "aiLiveKitIntegration2": "b"},
            experiment_stamps={"aiLiveKitIntegration2": "2026-09-01T10:00:00.000000Z"},
        )
        self.assertIn("aiLiveKitIntegration2=b", leg.voice_path)

    def test_equal_stamps_fall_back_to_the_version(self) -> None:
        at = "2026-09-01T10:00:00.000000Z"
        leg = correlate.OurLeg(
            experiments={"aiLiveKitIntegration3": "a", "aiLiveKitIntegration4": "b"},
            experiment_stamps={"aiLiveKitIntegration3": at, "aiLiveKitIntegration4": at},
        )
        self.assertIn("aiLiveKitIntegration4=b", leg.voice_path)

    def test_overlapping_cuts_resolve_by_time_end_to_end(self) -> None:
        """Two family members in one window, read straight out of the log entries."""
        leg = correlate.summarize_our_leg([
            _Entry("2026-09-01T10:00:00.000000Z", "Assigned experiment 'aiLiveKitIntegration4' value 'a'"),
            _Entry("2026-09-01T11:00:00.000000Z", "Assigned experiment 'aiLiveKitIntegration3' value 'b'"),
        ])
        self.assertEqual(leg.experiment_stamps["aiLiveKitIntegration3"], "2026-09-01T11:00:00.000000Z")
        self.assertIn("aiLiveKitIntegration3=b", leg.voice_path)

    def test_an_unrelated_experiment_is_not_the_livekit_arm(self) -> None:
        leg = self._leg(aiSomethingElse2="b")
        self.assertEqual(leg.voice_path, "unknown")
        self.assertIsNone(correlate.newest_livekit_experiment(leg.experiments))

    def test_no_experiments_at_all_is_unknown(self) -> None:
        self.assertEqual(correlate.OurLeg().voice_path, "unknown")

    def test_an_issued_11labs_token_still_wins_over_the_arm(self) -> None:
        """Direct evidence beats the assignment, whichever version is present."""
        leg = correlate.OurLeg(
            experiments={"aiLiveKitIntegration4": "b"}, elevenlabs_token_issued=True
        )
        self.assertEqual(leg.voice_path, "ElevenLabs (conversation token issued)")

    def test_the_fold_and_the_lookup_agree_end_to_end(self) -> None:
        """A future version read straight out of the logs must resolve."""
        leg = correlate.summarize_our_leg([
            _Entry("2026-09-01T10:00:00.000000Z", "Assigned experiment 'aiLiveKitIntegration4' value 'a'"),
            _Entry("2026-09-01T10:05:00.000000Z", "Assigned experiment 'aiLiveKitIntegration4' value 'b'"),
        ])
        self.assertIn("aiLiveKitIntegration4=b", leg.voice_path)


class LatencyGradeTest(unittest.TestCase):
    def test_grades_against_the_felt_thresholds(self) -> None:
        self.assertEqual(correlate.grade_felt(1.2), "ok")
        self.assertEqual(correlate.grade_felt(correlate.FELT_LATENCY_DEGRADED), "degraded")
        self.assertEqual(correlate.grade_felt(correlate.FELT_LATENCY_BAD), "bad")

    def test_no_conversation_yields_no_findings(self) -> None:
        report = correlate.assess_latency([])
        self.assertEqual(report.findings, [])


if __name__ == "__main__":
    unittest.main()
