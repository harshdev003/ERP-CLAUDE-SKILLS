"""One Aura conversation spans many ElevenLabs calls — eng:investigate-voice-issue.

A reconnect mints a new `conv_…` carrying the same `dynamic_variables.conversationId`,
so "the vendor call" for a rating is routinely nine calls, not one. The skill used
to match a single call and judge every reply-watchdog event against it, which
fabricates the one verdict the design treats as trustworthy: an event that never
touched that call finds no agent turns and is reported as a **genuine stall** at
high confidence.

The load-bearing assertions here are the negatives. A test that only checks the
owning call is picked correctly passes just as well against a version that
returns `genuine` for an unattributable event — so the cases that matter are
"outside every call resolves to unknown, never genuine" and "a single-call
session behaves exactly as before".

Observed instance: Aura `2828DE4B…` over 9 calls in 64 minutes, where a watchdog
window at 01:12:08–01:12:53Z was judged against a call that started 01:14:40Z.
Stdlib unittest, no network.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

SKILL = (
    pathlib.Path(__file__).resolve().parents[1]
    / "plugins" / "eng" / "skills" / "investigate-voice-issue" / "scripts"
)
sys.path.insert(0, str(SKILL))

import correlate  # noqa: E402
import elevenlabs as el  # noqa: E402


def _load(path: pathlib.Path, name: str):
    """Load a skill module under a unique name.

    Nine skills ship a `format.py`, so a plain `import format` binds whichever
    one the shared suite imported first — the module passes in isolation and
    fails under `unittest discover`. Loading by path sidesteps the collision.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fmt = _load(SKILL / "format.py", "voice_issue_format")

BASE = 1_788_483_000  # 2026-09-04T01:10:00Z-ish, the observed session


def call(cid, start, duration, agent_offsets=(), status="done", termination="Client disconnected: 1000"):
    turns = [
        el.Turn(index=i, role="agent", time_in_call_secs=off, chars=80, interrupted=False)
        for i, off in enumerate(agent_offsets)
    ]
    return el.Conversation(
        conversation_id=cid, agent_id="agent_test", status=status,
        start_unix=start, duration_secs=duration, message_count=len(turns),
        termination_reason=termination, call_successful="success",
        aura_conversation_id="2828DE4B", aura_user_id="uid", platform="mobile", turns=turns,
    )


def meta(reply_start, waited=45):
    return {
        "replyStartTime": reply_start,
        "timestamp": reply_start + waited,
        "agentState": "listening",
        "timeoutDuration": waited,
    }


class CallSpan(unittest.TestCase):
    def test_contains_covers_start_to_start_plus_duration(self) -> None:
        c = call("c1", BASE, 200)
        self.assertEqual(c.end_unix, BASE + 200)
        self.assertTrue(c.contains(BASE))
        self.assertTrue(c.contains(BASE + 200))
        self.assertFalse(c.contains(BASE - 1))
        self.assertFalse(c.contains(BASE + 201))

    def test_call_containing_picks_the_running_call(self) -> None:
        a = call("a", BASE, 100)
        b = call("b", BASE + 200, 100)
        self.assertIs(el.call_containing([a, b], BASE + 50), a)
        self.assertIs(el.call_containing([a, b], BASE + 250), b)
        self.assertIsNone(el.call_containing([a, b], BASE + 150))

    def test_a_live_call_has_no_final_duration(self) -> None:
        """A running call reports 0s/0 turns, which reads exactly like a dead one."""
        live = call("live", BASE, 0, status="in-progress")
        self.assertTrue(live.in_progress)
        self.assertFalse(call("done", BASE, 100).in_progress)


class WatchdogAttribution(unittest.TestCase):
    """Each event is judged against the call that was actually running."""

    def test_event_outside_every_call_is_unknown_not_genuine(self) -> None:
        """The exact regression: a later call cannot answer for an earlier window.

        Window 01:12:08–01:12:53 against a call that starts at 01:14:40 finds no
        turns — and reporting that as a stall is a fabricated finding.
        """
        later = call("conv_2301", BASE + 280, 422, agent_offsets=[10, 60])
        got = correlate.classify_watchdog(meta(BASE + 128), [later])
        self.assertEqual(got.verdict, "unknown")
        self.assertNotEqual(got.verdict, "genuine")
        self.assertIn("outside every matched call", got.detail)

    def test_event_is_verified_against_its_own_call_not_the_first(self) -> None:
        """A reply in call B clears an event in B even when A is listed first."""
        a = call("conv_A", BASE, 100, agent_offsets=[])
        b = call("conv_B", BASE + 200, 300, agent_offsets=[20, 40])
        # Window opens 210s in, i.e. 10s into B; B replies at +20s and +40s.
        got = correlate.classify_watchdog(meta(BASE + 210), [a, b])
        self.assertEqual(got.verdict, "false-positive")
        self.assertEqual(got.agent_turns_in_window, 2)
        self.assertIn("conv_B", got.detail)

    def test_a_real_stall_in_a_later_call_is_not_cleared_by_another_call(self) -> None:
        """The quiet direction: another call's turns must not absolve this one."""
        a = call("conv_A", BASE, 100, agent_offsets=[10, 20, 30])
        b = call("conv_B", BASE + 200, 300, agent_offsets=[])
        got = correlate.classify_watchdog(meta(BASE + 210), [a, b])
        self.assertEqual(got.verdict, "genuine")
        self.assertEqual(got.agent_turns_in_window, 0)
        self.assertIn("conv_B", got.detail)

    def test_single_call_session_is_unchanged(self) -> None:
        """No fragmentation, no behaviour change."""
        only = call("conv_only", BASE, 600, agent_offsets=[234, 271])
        got = correlate.classify_watchdog(meta(BASE + 229), [only])
        self.assertEqual(got.verdict, "false-positive")
        self.assertEqual(got.agent_turns_in_window, 2)

    def test_no_calls_at_all_is_unknown(self) -> None:
        self.assertEqual(correlate.classify_watchdog(meta(BASE), []).verdict, "unknown")


class OverlappingAndLiveSpans(unittest.TestCase):
    """The three edge cases a session of tidy, non-overlapping calls never shows."""

    def test_a_stale_open_record_does_not_answer_for_the_live_call(self) -> None:
        """A record that never closed keeps covering later calls.

        That is the zombie-session shape this skill exists to investigate, so it
        is the one overlap that will actually occur — and judged against the
        stale empty record, a healthy reconnect reads as a genuine stall.
        """
        zombie = call("conv_zombie", BASE, 1200, agent_offsets=[])
        live = call("conv_live", BASE + 300, 300, agent_offsets=[20, 40])
        got = correlate.classify_watchdog(meta(BASE + 310), [zombie, live])
        self.assertEqual(got.verdict, "false-positive")
        self.assertIn("conv_live", got.detail)

    def test_an_event_before_the_reconnect_still_belongs_to_the_older_call(self) -> None:
        """The negative case — preferring the live call must not steal every event."""
        older = call("conv_older", BASE, 1200, agent_offsets=[10])
        live = call("conv_live", BASE + 300, 300, agent_offsets=[20])
        got = correlate.classify_watchdog(meta(BASE + 5), [older, live])
        self.assertIn("conv_older", got.detail)

    def test_a_watchdog_on_a_live_call_is_attributable(self) -> None:
        """A live record reports 0s, so a closed span would cover only its start."""
        live = call("conv_live", BASE, 0, agent_offsets=[30], status="in-progress")
        got = correlate.classify_watchdog(meta(BASE + 20), [live])
        self.assertNotEqual(got.verdict, "unknown")
        self.assertEqual(got.verdict, "false-positive")

    def test_a_processing_call_has_ended_and_keeps_a_bounded_span(self) -> None:
        """`processing` is post-call analysis on a finished call, not a live one.

        Open-ending it would let a just-finished call claim every later event of
        the session and report a stall on a call that was not running.
        """
        done = call("conv_done", BASE, 120, agent_offsets=[], status="processing")
        self.assertFalse(done.in_progress)
        self.assertEqual(done.end_unix, BASE + 120)
        self.assertFalse(done.contains(BASE + 5000))
        got = correlate.classify_watchdog(meta(BASE + 5000), [done])
        self.assertEqual(got.verdict, "unknown")

    def test_a_live_call_has_an_open_ended_span(self) -> None:
        live = call("live", BASE, 0, status="in-progress")
        self.assertIsNone(live.end_unix)
        self.assertTrue(live.contains(BASE + 10_000))
        self.assertFalse(live.contains(BASE - 1))


class UidOnlySearchKeepsOneSession(unittest.TestCase):
    """A uid-only window can hold several unrelated Aura conversations."""

    @staticmethod
    def _search(convs, want_conv=""):
        groups = el._session_groups(convs)
        return groups

    def test_groups_split_on_the_aura_conversation_id(self) -> None:
        a1 = call("a1", BASE, 100)
        a2 = call("a2", BASE + 200, 100)
        b1 = call("b1", BASE + 400, 100)
        b1.aura_conversation_id = "OTHER"
        groups = el._session_groups([a1, a2, b1])
        self.assertEqual(len(groups), 2)
        self.assertEqual(sorted(len(g) for g in groups), [1, 2])

    def test_calls_with_no_aura_id_are_not_pooled_together(self) -> None:
        """Two unidentified calls are not evidence of one session."""
        x, y = call("x", BASE, 100), call("y", BASE + 200, 100)
        x.aura_conversation_id = None
        y.aura_conversation_id = None
        self.assertEqual(len(el._session_groups([x, y])), 2)

    def test_sessions_seen_is_reported(self) -> None:
        search = el.ConversationSearch([call("a", BASE, 100)], 1, False, "uid", 0, False, 3)
        self.assertEqual(search.sessions_seen, 3)

    def test_default_sessions_seen_is_one(self) -> None:
        search = el.ConversationSearch([call("a", BASE, 100)], 1, False, "uid", 0)
        self.assertEqual(search.sessions_seen, 1)


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeSession:
    """Stubs the one HTTP chokepoint, with durations so "largest" is decidable."""

    def __init__(self, rows):
        self.rows = rows
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        if url.endswith("/v1/convai/conversations"):
            return _Resp({
                "conversations": [
                    {"conversation_id": r["id"], "start_time_unix_secs": r["ts"]}
                    for r in self.rows
                ],
                "has_more": False,
                "next_cursor": None,
            })
        cid = url.rsplit("/", 1)[-1]
        r = next(x for x in self.rows if x["id"] == cid)
        return _Resp({
            "conversation_id": r["id"],
            "metadata": {
                "start_time_unix_secs": r["ts"],
                "call_duration_secs": r["dur"],
                "termination_reason": "Client disconnected: 1000",
            },
            "status": "done",
            "conversation_initiation_client_data": {
                "dynamic_variables": {"userId": r["uid"], "conversationId": r["aura"]}
            },
            "transcript": [],
        })


class UidOnlyNarrowsToOneSession(unittest.TestCase):
    """Asserted through `find_conversation`, not on the grouping helper alone.

    Testing `_session_groups` in isolation passes even when the narrowing is
    never applied — which is exactly the mutation this class exists to catch.
    """

    UID = "uid-target"

    def _client(self, rows):
        c = el.ElevenLabsClient(key="test-key")
        c.session = _FakeSession(rows)
        return c

    ROWS = [
        {"id": "s1a", "ts": BASE, "dur": 100, "uid": UID, "aura": "SESSION-ONE"},
        {"id": "s2a", "ts": BASE + 500, "dur": 400, "uid": UID, "aura": "SESSION-TWO"},
        {"id": "s2b", "ts": BASE + 1000, "dur": 400, "uid": UID, "aura": "SESSION-TWO"},
    ]

    def test_a_uid_only_search_returns_one_conversation_not_a_mix(self) -> None:
        got = self._client(self.ROWS).find_conversation(
            user_id=self.UID, start_unix=BASE - 1, end_unix=BASE + 5000
        )
        ids = {c.aura_conversation_id for c in got.conversations}
        self.assertEqual(ids, {"SESSION-TWO"}, "returned a mix of unrelated conversations")
        self.assertEqual(got.reconnects, 1)
        self.assertEqual(got.sessions_seen, 2)

    def test_asking_for_a_conversation_keeps_exactly_that_one(self) -> None:
        """The negative case — an explicit id must not be narrowed away."""
        got = self._client(self.ROWS).find_conversation(
            aura_conversation_id="SESSION-ONE", user_id=self.UID,
            start_unix=BASE - 1, end_unix=BASE + 5000,
        )
        self.assertEqual([c.conversation_id for c in got.conversations], ["s1a"])

    def test_a_single_session_window_is_untouched(self) -> None:
        rows = [r for r in self.ROWS if r["aura"] == "SESSION-TWO"]
        got = self._client(rows).find_conversation(
            user_id=self.UID, start_unix=BASE - 1, end_unix=BASE + 5000
        )
        self.assertEqual(len(got.conversations), 2)
        self.assertEqual(got.sessions_seen, 1)


class SessionAggregation(unittest.TestCase):
    def test_latency_spans_every_call(self) -> None:
        """The user rated the session; the worst turn may be in any of its calls."""
        a = call("a", BASE, 100)
        a.turns = [el.Turn(index=0, role="agent", time_in_call_secs=10, chars=10,
                           interrupted=False, metrics={correlate.FELT_METRIC: 1.0})]
        b = call("b", BASE + 200, 100)
        b.turns = [el.Turn(index=0, role="agent", time_in_call_secs=10, chars=10,
                           interrupted=False, metrics={correlate.FELT_METRIC: 4.2})]
        report = correlate.assess_latency([a, b])
        felt = report.by_metric(correlate.FELT_METRIC)
        self.assertAlmostEqual(felt.worst, 4.2)
        self.assertEqual(report.verdict, "degraded")

    def test_reconnect_count_is_reported(self) -> None:
        convs = [call(f"c{i}", BASE + i * 300, 200, agent_offsets=[10]) for i in range(9)]
        got = correlate.build_verdict(convs, correlate.LatencyReport(), [], correlate.OurLeg())
        self.assertIn("9 calls", got.session_health)
        self.assertIn("8 reconnect", got.session_health)
        self.assertTrue(any("8 reconnect" in a for a in got.artifacts))

    def test_a_long_call_with_no_agent_turn_is_the_symptom(self) -> None:
        convs = [
            call("healthy", BASE, 400, agent_offsets=[10, 50]),
            call("dead", BASE + 500, 120, agent_offsets=[]),
        ]
        got = correlate.build_verdict(convs, correlate.LatencyReport(), [], correlate.OurLeg())
        self.assertIn("no agent turn at all", got.symptom)
        self.assertIn("ENGMOB-2781", got.symptom)

    def test_a_brief_empty_call_is_not_called_dead(self) -> None:
        """Opening and immediately closing is ordinary — 61 of 69 such calls were <30s."""
        convs = [call("blip", BASE, 3, agent_offsets=[])]
        got = correlate.build_verdict(convs, correlate.LatencyReport(), [], correlate.OurLeg())
        self.assertNotIn("no agent turn at all", got.symptom)

    def test_a_live_call_is_not_counted_as_dead(self) -> None:
        convs = [call("live", BASE, 200, agent_offsets=[], status="in-progress")]
        got = correlate.build_verdict(convs, correlate.LatencyReport(), [], correlate.OurLeg())
        self.assertNotIn("no agent turn at all", got.symptom)


class SessionFilterKeepsUnidentifiedCalls(unittest.TestCase):
    """A call with no Aura conversationId is unknown, not a different session.

    ~1.5% of ElevenLabs sessions start without `conversation_initiation_client_data`
    (ENGMOB-2794), so the record the client event named can legitimately carry no
    id. Treating absent as "other" drops the one call we were pointed at.
    """

    @staticmethod
    def _filter(convs, target):
        """The rule as investigate.py applies it."""
        t = (target or "").strip().lower()
        return [c for c in convs if (c.aura_conversation_id or "").lower() in (t, "")]

    def test_a_call_with_no_conversation_id_is_kept(self) -> None:
        named = call("named", BASE, 200, agent_offsets=[10])
        named.aura_conversation_id = None
        sibling = call("sibling", BASE + 300, 200, agent_offsets=[10])
        kept = self._filter([named, sibling], "2828DE4B")
        self.assertIn("named", [c.conversation_id for c in kept])
        self.assertEqual(len(kept), 2)

    def test_a_call_from_another_conversation_is_still_dropped(self) -> None:
        """The negative case — keeping unknowns must not keep genuine outsiders."""
        mine = call("mine", BASE, 200, agent_offsets=[10])
        other = call("other", BASE + 300, 200, agent_offsets=[10])
        other.aura_conversation_id = "SOMETHING-ELSE"
        kept = self._filter([mine, other], "2828DE4B")
        self.assertEqual([c.conversation_id for c in kept], ["mine"])


class LiveCallIsNotAClosedCall(unittest.TestCase):
    """A still-running call has not closed, so it is neither normal nor abnormal."""

    def test_a_live_call_is_not_called_closed_normally(self) -> None:
        convs = [
            call("done", BASE, 300, agent_offsets=[10]),
            call("live", BASE + 400, 0, agent_offsets=[10], status="in-progress"),
        ]
        got = correlate.build_verdict(convs, correlate.LatencyReport(), [], correlate.OurLeg())
        self.assertNotIn("all closed normally", got.session_health)
        self.assertIn("still in progress", got.session_health)

    def test_a_lone_live_call_is_not_given_a_clean_bill_of_health(self) -> None:
        convs = [call("live", BASE, 0, agent_offsets=[10], status="in-progress")]
        got = correlate.build_verdict(convs, correlate.LatencyReport(), [], correlate.OurLeg())
        self.assertNotIn("closed normally", got.symptom)
        self.assertIn("still in progress", got.symptom)
        self.assertEqual(got.confidence, "low")

    def test_a_finished_healthy_session_still_reads_as_closed_normally(self) -> None:
        """The negative case — the live branch must not swallow the clean verdict."""
        convs = [call("done", BASE, 300, agent_offsets=[10])]
        got = correlate.build_verdict(convs, correlate.LatencyReport(), [], correlate.OurLeg())
        self.assertIn("closed normally", got.session_health)
        self.assertIn("No vendor-side degradation", got.symptom)

    def test_an_abnormal_close_still_wins_over_a_live_sibling(self) -> None:
        convs = [
            call("bad", BASE, 300, agent_offsets=[10], termination="Server error: 1011"),
            call("live", BASE + 400, 0, agent_offsets=[10], status="in-progress"),
        ]
        got = correlate.build_verdict(convs, correlate.LatencyReport(), [], correlate.OurLeg())
        self.assertIn("closed abnormally", got.session_health)


class SearchReturnsEverySibling(unittest.TestCase):
    def test_conversation_property_is_the_longest_call(self) -> None:
        short, long_ = call("short", BASE, 120), call("long", BASE + 300, 900)
        search = el.ConversationSearch([short, long_], 2, False, "uid", 0)
        self.assertEqual(search.conversation.conversation_id, "long")
        self.assertEqual(search.reconnects, 1)
        self.assertTrue(search.found)

    def test_an_empty_search_still_reports_no_match(self) -> None:
        search = el.ConversationSearch([], 3, False, "uid", 0)
        self.assertFalse(search.found)
        self.assertIsNone(search.conversation)
        self.assertEqual(search.reconnects, 0)


class DigestRendering(unittest.TestCase):
    def test_every_call_is_listed_so_fragmentation_is_visible(self) -> None:
        convs = [call(f"conv_{i}", BASE + i * 300, 200, agent_offsets=[10]) for i in range(3)]
        out = fmt.render_vendor_call(convs)
        for i in range(3):
            self.assertIn(f"conv_{i}", out)
        self.assertIn("3 calls", out)
        self.assertIn("2 reconnect", out)

    def test_a_single_call_renders_without_a_session_table(self) -> None:
        out = fmt.render_vendor_call([call("conv_only", BASE, 200, agent_offsets=[10])])
        self.assertIn("conv_only", out)
        self.assertNotIn("reconnect", out)

    def test_a_truncated_log_read_is_marked_where_the_numbers_are(self) -> None:
        """The cap is in Sources, but the figures are what a reader quotes."""
        leg = correlate.OurLeg(entry_count=6000, openai_ms=[2780, 24600], truncated=True)
        out = fmt.render_our_leg(leg, truncated=True)
        self.assertIn("partial", out)
        clean = fmt.render_our_leg(
            correlate.OurLeg(entry_count=10, openai_ms=[100]), truncated=False
        )
        self.assertNotIn("partial", clean)
