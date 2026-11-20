"""talk-to-aurie joins the room the token endpoint returns, not the one it asked for (ENG-1689).

IntelligenceService#748 binds a LiveKit token's room to the caller. With
`LIVEKIT_ROOM_CHECK=enforce`, `POST /ai/livekit/conversation/token` replaces a `roomName` not
bound to the caller with a new room and returns it as `room`. The token is then valid for that
room only, so the session must connect, dispatch the agent and write its transcript there.

The endpoint is stubbed at `urllib.request.urlopen`, and the session's LiveKit calls at the
module's own seams (`_mint_token`, `_dispatch_agent`, `_write_transcript`, the Room class).
"""

import asyncio
import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_livekit_join_order import LK  # noqa: E402  (loads livekit_chat with the SDK stubbed)


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


ENV = {"AURA_API_URL": "https://api.example", "AURA_AUTH_TOKEN": "user-token",
       "AURA_LIVEKIT_TOKEN_ENDPOINT": "1"}


class FetchEndpointToken(unittest.TestCase):
    def fetch(self, body):
        sent = []

        def urlopen(request, timeout=None):
            sent.append(json.loads(request.data))
            return _Response(json.dumps(body).encode())

        with mock.patch.dict(os.environ, ENV), mock.patch.object(LK.urllib.request, "urlopen", urlopen):
            got = LK._fetch_endpoint_token("qa-room-1")
        return got, sent[0]

    def test_returns_the_room_the_endpoint_bound(self):
        got, sent = self.fetch({"token": "jwt", "room": "bound-room-9"})
        self.assertEqual(got, ("jwt", "bound-room-9"))
        self.assertEqual(sent["roomName"], "qa-room-1")

    def test_keeps_the_asked_room_when_the_response_names_none(self):
        self.assertEqual(self.fetch({"token": "jwt"})[0], ("jwt", "qa-room-1"))

    def test_mint_token_passes_the_room_through_on_the_endpoint_path_only(self):
        with mock.patch.dict(os.environ, ENV), \
                mock.patch.object(LK, "_fetch_endpoint_token", lambda room: ("jwt", "bound-room-9")):
            self.assertEqual(LK._mint_token("qa", "qa-room-1", use_endpoint=True), ("jwt", "bound-room-9"))


class SessionUsesTheBoundRoom(unittest.TestCase):
    def test_connect_dispatch_and_transcript_use_the_returned_room(self):
        seen = {}

        class _Room:
            local_participant = types.SimpleNamespace(send_text=None)

            def register_text_stream_handler(self, *_a, **_k):
                return None

            def on(self, *_a, **_k):
                return None

            async def connect(self, _url, token):
                seen["token"] = token

            async def disconnect(self):
                return None

        async def no_peers(_room):
            return []

        async def settled(_state, *a, **k):
            return True

        with tempfile.TemporaryDirectory() as tmp:
            io_dir = pathlib.Path(tmp)
            (io_dir / "DONE").parent.mkdir(parents=True, exist_ok=True)
            (io_dir / "DONE").touch()
            out = io.StringIO()
            with mock.patch.object(LK, "rtc", types.SimpleNamespace(Room=_Room), create=True), \
                    mock.patch.dict(sys.modules["livekit.rtc"].__dict__, {"Room": _Room}), \
                    mock.patch.object(LK, "_list_participants", no_peers), \
                    mock.patch.object(LK, "_mint_token", lambda *a, **k: ("jwt", "bound-room-9")), \
                    mock.patch.object(LK, "_register_client_tool_acker", lambda *a, **k: set()), \
                    mock.patch.object(LK, "_dispatch_agent", lambda agent, room, meta: seen.update(dispatch=room)), \
                    mock.patch.object(LK, "_write_transcript", lambda room, *a: seen.update(transcript=room)), \
                    mock.patch.object(LK, "_settle", settled), redirect_stdout(out):
                asyncio.run(LK._run_session("qa-room-1", "aurie-voice-agent", "", io_dir))
        self.assertEqual(seen, {"token": "jwt", "dispatch": "bound-room-9", "transcript": "bound-room-9"})
        self.assertIn("ROOM_CHANGED: the token endpoint bound this session to bound-room-9", out.getvalue())
        self.assertIn("connected to bound-room-9", out.getvalue())


class JoinOrderGuardChecksTheBoundRoom(unittest.TestCase):
    """The ENG-1125 guard must inspect the room the session will join, not the one it asked for."""

    def run_session(self, occupied):
        listed = []

        class _Room:
            def register_text_stream_handler(self, *_a, **_k):
                return None

        async def participants(room):
            listed.append(room)
            return [types.SimpleNamespace(identity="qa-observer", kind="STANDARD", attributes={})] \
                if room == occupied else []

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(sys.modules["livekit.rtc"].__dict__, {"Room": _Room}), \
                mock.patch.object(LK, "_list_participants", participants), \
                mock.patch.object(LK, "_mint_token", lambda *a, **k: ("jwt", "bound-room-9")), \
                redirect_stdout(io.StringIO()):
            try:
                asyncio.run(LK._run_session("qa-room-1", "aurie-voice-agent", "", pathlib.Path(tmp)))
            except RuntimeError as exc:
                return listed, str(exc)
            except Exception:  # past the guard: fails on the stubbed Room, which is fine here
                return listed, ""
        return listed, ""

    def test_an_occupied_bound_room_aborts(self):
        listed, error = self.run_session(occupied="bound-room-9")
        self.assertEqual(listed, ["bound-room-9"])
        self.assertIn("bound-room-9 already has participant(s) qa-observer", error)

    def test_an_occupied_requested_room_no_longer_matters(self):
        listed, error = self.run_session(occupied="qa-room-1")
        self.assertEqual(listed, ["bound-room-9"])
        self.assertNotIn("already has participant", error)


class TokenCommandPrintsTheBoundRoom(unittest.TestCase):
    def test_room_line(self):
        env = {"LIVEKIT_URL": "wss://x", "LIVEKIT_API_KEY": "k", "LIVEKIT_API_SECRET": "s"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env), \
                mock.patch.object(LK, "_mint_token", lambda *a, **k: ("jwt", "bound-room-9")):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = LK.cmd_token(types.SimpleNamespace(identity="qa", room="qa-room-1", out=f"{tmp}/t"))
            self.assertEqual(rc, 0)
            self.assertIn("ROOM: bound-room-9", out.getvalue())
            self.assertNotIn("jwt", out.getvalue())


if __name__ == "__main__":
    unittest.main()
