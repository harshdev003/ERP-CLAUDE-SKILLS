"""The talk-to-aurie join-order guards (ENG-1125).

livekit-agents links the agent's text input to the FIRST non-agent participant to join and drops
`lk.chat` from every other identity with no log line. So `session` (which sends turns) must join
before any observer, and getting it wrong produces no error at all — empty replies, then the away
timer. That silence is the whole reason these guards exist, and it is also why they need tests:
the branches that matter only fire on the wrong join order, which a passing live QA run never
reaches.

The livekit SDK isn't a CI dependency, so a minimal stub stands in for the participant-kind enum
the classifier reads. Everything else is exercised for real, with `_list_participants` — the single
place the room state is read — stubbed so a room can be made empty, populated, or agent-only.
"""

import asyncio
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugins/qa/skills/talk-to-aurie/scripts/livekit_chat.py"


def _install_livekit_stub() -> None:
    """Minimal `livekit.api` + `livekit.rtc` so the module imports without the real SDK."""
    if "livekit" in sys.modules:
        return
    livekit = types.ModuleType("livekit")
    api = types.ModuleType("livekit.api")

    class _Kind:
        AGENT = "AGENT"
        STANDARD = "STANDARD"

    class ParticipantInfo:
        Kind = _Kind

    api.ParticipantInfo = ParticipantInfo
    rtc = types.ModuleType("livekit.rtc")
    livekit.api = api
    livekit.rtc = rtc
    sys.modules["livekit"] = livekit
    sys.modules["livekit.api"] = api
    sys.modules["livekit.rtc"] = rtc


def _load_module():
    _install_livekit_stub()
    spec = importlib.util.spec_from_file_location("livekit_chat_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LK = _load_module()


class _P:
    """A participant as the LiveKit API returns it."""

    def __init__(self, identity, kind="STANDARD", attributes=None):
        self.identity = identity
        self.kind = kind
        self.attributes = attributes or {}


class ClassifyParticipantsTest(unittest.TestCase):
    def test_agents_are_recognised_by_any_of_the_three_signals(self):
        for participant in (
            _P("agent-AJ_x9"),  # identity prefix
            _P("worker-7", kind="AGENT"),  # participant kind
            _P("worker-7", attributes={"lk.agent.state": "listening"}),  # attribute namespace
        ):
            with self.subTest(participant=participant.identity):
                self.assertTrue(LK._is_agent_participant(participant))

    def test_a_plain_participant_is_not_an_agent(self):
        self.assertFalse(LK._is_agent_participant(_P("qa-human")))

    def test_peers_excludes_us_and_the_agent(self):
        room = [_P("qa-observer"), _P("agent-AJ_x9"), _P("qa-talk-to-aurie")]
        self.assertEqual(LK._peers(room, "qa-observer"), ["qa-talk-to-aurie"])

    def test_an_agent_only_room_has_no_peer(self):
        # The trap case: the agent is present, so the room looks alive — but nobody has claimed
        # the text link yet, so an observer joining now would claim it.
        self.assertEqual(LK._peers([_P("agent-AJ_x9")], "qa-observer"), [])


class AwaitPeerTest(unittest.TestCase):
    def _with_rooms(self, *snapshots):
        """Stub `_list_participants` to return each snapshot in turn (last one repeats)."""
        calls = list(snapshots)

        async def _fake(_room):
            return calls.pop(0) if len(calls) > 1 else calls[0]

        LK._list_participants = _fake

    def test_returns_the_talker_once_it_appears(self):
        self._with_rooms([], [_P("agent-AJ_x9")], [_P("agent-AJ_x9"), _P("qa-human")])
        peer = asyncio.run(LK._await_peer("room-1", "qa-observer", timeout=5.0, poll=0.0))
        self.assertEqual(peer, "qa-human")

    def test_returns_none_when_nobody_ever_joins(self):
        self._with_rooms([])
        self.assertIsNone(asyncio.run(LK._await_peer("room-1", "qa-observer", timeout=0.0, poll=0.0)))

    def test_does_not_latch_onto_the_agent(self):
        self._with_rooms([_P("agent-AJ_x9")])
        self.assertIsNone(asyncio.run(LK._await_peer("room-1", "qa-observer", timeout=0.0, poll=0.0)))


class RoomStateFailureTest(unittest.TestCase):
    """A guard that can't read the room must not conclude the room is empty.

    Collapsing every listing failure into `[]` would make an auth or network blip read as "nobody
    here", which is precisely the wave-through the guards exist to prevent — and it would happen
    at the moments things are already going wrong.
    """

    def _raise(self, exc):
        async def _fake(_room):
            raise exc

        LK._list_participants = _fake

    def test_a_missing_room_is_empty_not_an_error(self):
        self.assertTrue(LK._is_room_not_found(Exception("twirp error not_found: room does not exist")))
        self.assertTrue(LK._is_room_not_found(type("E", (Exception,), {"code": "not_found"})()))

    def test_an_auth_failure_is_not_a_missing_room(self):
        self.assertFalse(LK._is_room_not_found(Exception("twirp error unauthenticated: invalid token")))

    def test_await_peer_raises_rather_than_reporting_an_empty_room(self):
        self._raise(LK.RoomStateUnavailable("boom"))
        with self.assertRaises(LK.RoomStateUnavailable):
            asyncio.run(LK._await_peer("room-1", "qa-observer", timeout=0.0, poll=0.0))

    def test_a_transient_blip_does_not_end_the_wait(self):
        calls = [LK.RoomStateUnavailable("blip"), [_P("qa-human")]]

        async def _fake(_room):
            nxt = calls.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        LK._list_participants = _fake
        self.assertEqual(
            asyncio.run(LK._await_peer("room-1", "qa-observer", timeout=5.0, poll=0.0)), "qa-human"
        )


class SessionGuardTest(unittest.TestCase):
    """`session` refuses to join second, because joining second answers nothing."""

    def setUp(self):
        class _Room:
            def register_text_stream_handler(self, *_a, **_k):
                return None

        LK.rtc = types.SimpleNamespace(Room=_Room)
        sys.modules["livekit.rtc"].Room = _Room
        # The token is minted before the guard (the endpoint can bind another room); keep the room.
        real_mint = LK._mint_token
        LK._mint_token = lambda _identity, room, **_k: ("token", room)
        self.addCleanup(setattr, LK, "_mint_token", real_mint)

    def _run(self, participants, **kw):
        async def _fake(_room):
            return participants

        LK._list_participants = _fake
        with tempfile.TemporaryDirectory() as tmp:
            return asyncio.run(
                LK._run_session("room-1", "aurie-voice-agent", "", pathlib.Path(tmp), **kw)
            )

    def test_aborts_when_an_observer_already_holds_the_room(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._run([_P("qa-observer")])
        message = str(ctx.exception)
        self.assertIn("qa-observer", message)
        self.assertIn("--allow-late", message)

    def test_an_agent_alone_does_not_trip_the_guard(self):
        # The agent doesn't claim its own text link, so its presence is not a reason to abort —
        # it would only look like one to a check that counted participants.
        with self.assertRaises(Exception) as ctx:
            self._run([_P("agent-AJ_x9")])
        self.assertNotIsInstance(ctx.exception, RuntimeError)

    def test_fails_closed_when_the_room_state_cant_be_read(self):
        async def _fake(_room):
            raise LK.RoomStateUnavailable("could not read participants for room-1: 401")

        LK._list_participants = _fake
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LK.RoomStateUnavailable) as ctx:
                asyncio.run(LK._run_session("room-1", "aurie-voice-agent", "", pathlib.Path(tmp)))
        self.assertIn("--allow-late", str(ctx.exception))

    def test_allow_late_overrides_the_guard(self):
        # Past the guard it fails on something else (no LiveKit env in tests) — which is the point:
        # the guard is what we're asserting is skipped, not that the session connects.
        with self.assertRaises(Exception) as ctx:
            self._run([_P("qa-observer")], require_first=False)
        self.assertNotIn("--allow-late", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
