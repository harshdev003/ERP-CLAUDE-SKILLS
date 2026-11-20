#!/usr/bin/env python3
"""Talk to the Aurie voice agent over LiveKit (text mode), for QA.

Subcommands:
  preflight  - verify `lk` CLI, the `livekit` Python SDK, and LIVEKIT_* env.
  session    - hold a room open, dispatch the agent, and drive a text conversation
               via a file protocol (turns/<n>.txt in, replies/<n>.json out, DONE to end).
  observe    - join a room as a silent (non-speaking) participant and capture a live
               transcript of both sides as the user talks in a browser/console. Optionally
               dispatches the agent first (--dispatch). Omit --room to auto-detect the next
               new room (e.g. one the LiveKit Cloud Console mints). Stops on a DONE file or Ctrl-C.

Secrets (LIVEKIT_API_SECRET, the minted token) never touch stdout or argv.
v1: text streams only (no audio). The exact livekit-rtc text-stream API can vary by
SDK version — pin `livekit`/`livekit-api` if the handlers below don't match your install.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import pathlib
import re
from pathlib import Path


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


CHAT_TOPIC = "lk.chat"  # participant -> agent text input
TRANSCRIPTION_TOPIC = "lk.transcription"  # agent -> participant synced transcript
DATA_TOPIC = "conversation"  # agent <-> client protocol channel (tool_call / tool_result / vad_score …)
OUTPUTS = Path(__file__).resolve().parents[1] / "outputs"


class _TranscriptDecoder:
    """Decode one ``lk.transcription`` stream's chunks into visible text (and word timings).

    The voice agent publishes each agent-transcript word as a newline-terminated JSON
    ``TimedString`` — ``{"text": "hello", "end_time": 0.67}`` — so a client can highlight words in
    sync with playback. Two payload shapes arrive on this topic and a reader has to handle both:

    * **JSON lines** — the agent transcript. Every timing key is optional and **absent** rather than
      null, so neither can be required. In practice ``start_time`` is simply not sent while the
      transcript is synced to playback (``sync_transcription=True``): the synchronizer restamps each
      word with the moment to reveal it, as ``end_time`` relative to that utterance's start. Audio
      played from pre-synthesized bytes carries no alignment at all and arrives as
      ``{"text": "hello"}``; requiring the keys would drop those words entirely.
    * **Raw text** — the user's own STT transcript (the agent applies the JSON encoding to its own
      output only, so a reader branches on payload rather than on sender), and every stream from an
      agent running with ``AGENT_TRANSCRIPT_JSON_FORMAT=false``.

    A chunk boundary need not align with a line boundary, so a partial line is buffered until its
    newline arrives. Call :meth:`push` per chunk, then :meth:`flush` once the stream closes.
    """

    # MARK: - Initialization

    def __init__(self) -> None:
        self._buf = ""
        self.timings: list[dict] = []  # one entry per word that carried alignment

    # MARK: - Public Methods

    def flush(self) -> str:
        """Return text left buffered when the stream closed without a trailing newline.

        Returns:
            str: the decoded remainder, or "" when the buffer was already drained.
        """
        rest, self._buf = self._buf, ""
        return self._decode_line(rest) if rest.lstrip().startswith("{") else rest

    def push(self, chunk: str) -> str:
        """Decode one stream chunk.

        Args:
            chunk (str): the bytes just read from the stream.

        Returns:
            str: the visible text decoded so far — empty while a JSON line is still arriving.
        """
        self._buf += chunk
        out: list[str] = []
        while self._buf:
            if not self._buf.lstrip().startswith("{"):
                out.append(self._buf)  # a raw-text stream: there is no line boundary to wait for
                self._buf = ""
                break
            newline = self._buf.find("\n")
            if newline < 0:
                break  # a JSON object is still mid-flight; keep it buffered
            line, self._buf = self._buf[:newline], self._buf[newline + 1 :]
            out.append(self._decode_line(line))
        return "".join(out)

    # MARK: - Private Methods

    def _decode_line(self, line: str) -> str:
        """Return one line's visible text, recording its timings when it carries any.

        Args:
            line (str): a single newline-delimited payload.

        Returns:
            str: the JSON ``text`` field, or ``line`` unchanged when it isn't a JSON TimedString —
            a brace-leading line that doesn't parse is passed through rather than dropped, so a
            malformed payload degrades to visible text instead of a silently empty reply.
        """
        stripped = line.strip()
        if not stripped:
            return ""
        try:
            payload = json.loads(stripped)
        except ValueError:
            return line
        if not isinstance(payload, dict) or "text" not in payload:
            return line
        text = payload.get("text") or ""
        start, end = payload.get("start_time"), payload.get("end_time")
        if start is not None or end is not None:
            self.timings.append({"text": text, "start_time": start, "end_time": end})
        return text

# Client-side tools the backend prompter can drive (IntelligenceService `isClientSide()`), with the
# result string our QA client returns — mirrors the iOS `confirmToolResult` contract so the agent's
# forwarded tool completes instead of going unanswered. Tools in TERMINAL_CLIENT_TOOLS end the
# conversation after their ack (the QA client wraps up the session, as the app would).
CLIENT_TOOL_ACK_RESULT = {
    "createInsightCard": "completed",
    "endOnboarding": "Onboarding completed successfully",
    "showCrisisModal": "completed",
}
TERMINAL_CLIENT_TOOLS = {"createInsightCard", "endOnboarding"}


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _verify_livekit_auth() -> tuple[bool, str]:
    """Make one authenticated LiveKit API call with the env creds to confirm the project
    actually accepts them. Catches stale/rotated keys that are *set* but rejected (a 401),
    before we stand up a worker — the failure mode that an "is it set?" check misses."""
    from livekit import api

    url = _env("LIVEKIT_URL").replace("wss://", "https://").replace("ws://", "http://")

    async def _check() -> None:
        lkapi = api.LiveKitAPI(url, _env("LIVEKIT_API_KEY"), _env("LIVEKIT_API_SECRET"))
        try:
            # Bounded so a hung/unreachable endpoint can't block preflight indefinitely.
            await asyncio.wait_for(lkapi.room.list_rooms(api.ListRoomsRequest()), timeout=10)
        finally:
            await lkapi.aclose()

    try:
        asyncio.run(_check())
        return True, ""
    except asyncio.TimeoutError:
        return False, "timed out after 10s (check LIVEKIT_URL / network)"
    except Exception as exc:  # noqa: BLE001 - report any auth/connection failure verbatim
        return False, (str(exc) or type(exc).__name__)[:140]


def cmd_preflight(_args: argparse.Namespace) -> int:
    """Check tooling + env; print fixes. Never prints secret values."""
    ok = True

    if shutil.which("lk"):
        try:
            ver = subprocess.run(["lk", "--version"], capture_output=True, text=True, timeout=10)
            print(f"PASS  lk CLI: {ver.stdout.strip() or ver.stderr.strip()}")
        except Exception as exc:  # pragma: no cover
            ok = False
            print(f"FAIL  lk CLI present but not runnable: {exc}")
        proj = subprocess.run(["lk", "project", "list"], capture_output=True, text=True, timeout=15)
        if proj.returncode == 0:
            print("PASS  lk authenticated (lk project list ok)")
        else:
            ok = False
            print("FAIL  lk not authenticated -> run: lk cloud auth")
    else:
        ok = False
        print("FAIL  lk CLI missing -> curl -sSL https://get.livekit.io/cli | bash ; then: lk cloud auth")

    sdk_ok = True
    try:
        import livekit.api  # noqa: F401
        import livekit.rtc  # noqa: F401

        print("PASS  livekit SDK importable (livekit.api + livekit.rtc)")
    except Exception:
        ok = sdk_ok = False
        print("FAIL  livekit SDK missing -> pip install livekit livekit-api")

    missing = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if not _env(k)]
    if missing:
        ok = False
        print(f"FAIL  env not set: {', '.join(missing)} (from ~/.claude/settings.json env or `lk project`)")
    else:
        print("PASS  LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET set")
        # Set is not the same as valid — verify the project accepts the creds. Needs the SDK;
        # skip (don't misreport a missing SDK as bad keys) if the import check above failed.
        if not sdk_ok:
            print("SKIP  LiveKit auth check (livekit SDK not importable)")
        else:
            auth_ok, detail = _verify_livekit_auth()
            if auth_ok:
                print("PASS  LiveKit credentials accepted by the project")
            else:
                ok = False
                print(f"FAIL  LiveKit credentials rejected ({detail}) -> refresh LIVEKIT_API_KEY/SECRET for this project")

    print("PREFLIGHT_OK" if ok else "PREFLIGHT_FAILED")
    return 0 if ok else 1


def _fetch_endpoint_token(room: str) -> tuple[str, str]:
    """Fetch a room-join token for the test user from `POST /ai/livekit/conversation/token`.

    The token is minted server-side (the LiveKit secret never leaves the backend) for the user the
    `AURA_AUTH_TOKEN` identifies. Agent dispatch is left to this script's own `lk dispatch`
    (`dispatchAgent: false`), so only the token sourcing changes.

    Returns (token, room). The token is for the room the response names, which can differ from the
    one asked for: with `LIVEKIT_ROOM_CHECK=enforce` (IntelligenceService#748) a room not bound to
    the caller is replaced by a new one. Everything after this call uses the returned room.
    """
    base = _env("AURA_API_URL").rstrip("/")
    auth = _env("AURA_AUTH_TOKEN")
    if not base or not auth:
        raise RuntimeError(
            "AURA_LIVEKIT_TOKEN_ENDPOINT is set but AURA_API_URL / AURA_AUTH_TOKEN are missing"
        )
    payload = json.dumps({"dispatchAgent": False, "roomName": room}).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/ai/livekit/conversation/token",
        data=payload,
        headers={"Authorization": f"Bearer {auth}", "User-Agent": user_agent(), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))
    token = data.get("token")
    if not token:
        raise RuntimeError(f"token endpoint returned no token (room={data.get('room')!r})")
    return token, data.get("room") or room


def _read_text_arg(inline: str, path: str) -> str:
    """Resolve text from an inline value or a file path. Inline wins; returns ''
    when neither is given."""
    if path:
        return Path(path).read_text(encoding="utf-8").rstrip("\n")
    return inline or ""


def _load_json_object(inline: str, path: str, label: str) -> dict | None:
    """Resolve an optional JSON object from an inline string or a file. Returns
    None when neither is given. Exits loudly on invalid JSON or a non-object so
    a malformed value never silently drops.

    Self-contained copy of chat-with-aurie's loader (skills don't cross-import)."""
    if inline and path:
        print(f"FAIL  pass only one of --{label} / --{label}-file", flush=True)
        sys.exit(1)
    if path:
        raw = Path(path).read_text(encoding="utf-8")
    elif inline:
        raw = inline
    else:
        return None
    if not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"FAIL  --{label} is not valid JSON — {exc}", flush=True)
        sys.exit(1)
    if not isinstance(obj, dict):
        print(f"FAIL  --{label} must be a JSON object", flush=True)
        sys.exit(1)
    return obj


def cmd_start_conversation(args: argparse.Namespace) -> int:
    """Open the Aura conversation over HTTP before the LiveKit session, so the
    voice agent can resume it by conversation_id (the same flow the Aura
    Consumer app uses: /start with firstMessage + firstMessageContext, then pass
    conversation_id + first_message in the dispatch metadata).

    Prints one JSON line {conversation_id, http_status, body}; the
    conversation_id is echoed to stderr so it can be piped into metadata.json.
    """
    base = _env("AURA_API_URL").rstrip("/")
    auth = _env("AURA_AUTH_TOKEN")
    if not base or not auth:
        print("FAIL  AURA_API_URL / AURA_AUTH_TOKEN not set", flush=True)
        return 1

    first_message = _read_text_arg(args.first_message, args.first_message_file)
    if not first_message:
        print("FAIL  --first-message or --first-message-file required", flush=True)
        return 1

    conversation_id = args.conversation_id or str(uuid.uuid4())
    payload = {"firstMessage": first_message}
    context = _load_json_object(
        args.first_message_context, args.first_message_context_file,
        "first-message-context",
    )
    if context is not None:
        payload["firstMessageContext"] = context

    request = urllib.request.Request(
        f"{base}/ai/conversations/{conversation_id}/start",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {auth}", "User-Agent": user_agent(), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        print(f"FAIL  connection failed — {exc.reason}", flush=True)
        return 1

    print(json.dumps({"conversation_id": conversation_id, "http_status": status, "body": body}))
    print(f"conversation_id: {conversation_id}", file=sys.stderr)
    return 0 if 200 <= status < 300 else 2


def _mint_token(identity: str, room: str, *, use_endpoint: bool = False) -> tuple[str, str]:
    """(token, room the token is for). Only the endpoint path can change the room."""
    # When this participant is the test user (use_endpoint=True) AND opted in
    # (AURA_LIVEKIT_TOKEN_ENDPOINT), source the token from the backend endpoint — exercising the
    # server-side path that keeps the LiveKit secret off clients. The silent observer always mints
    # locally (use_endpoint=False): it needs its own distinct identity, and the endpoint always mints
    # for the AURA_AUTH_TOKEN user.
    if use_endpoint and _env("AURA_LIVEKIT_TOKEN_ENDPOINT").lower() in ("1", "true", "yes", "on"):
        return _fetch_endpoint_token(room)

    from livekit import api

    return (
        api.AccessToken(_env("LIVEKIT_API_KEY"), _env("LIVEKIT_API_SECRET"))
        .with_identity(identity)
        .with_name(identity)
        # can_publish_data lets the QA client send client-tool `tool_result` acks on the data channel.
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish_data=True))
        .to_jwt()
    ), room


def _register_client_tool_acker(room, *, label: str, on_acked=None, disconnect_on_terminal: bool = True) -> set:
    """Ack the agent's client-tool forwards over the data channel, standing in for the app.

    The backend prompter drives client-side tools (createInsightCard / endOnboarding /
    showCrisisModal); the agent forwards each as
    ``{"type":"tool_call","toolName","toolCallId","parameters"}`` on the ``conversation`` topic and
    waits for the client's ack. With no real app in the room those forwards go unanswered, so this
    replies with ``{"type":"tool_result","toolCallId","result"}`` exactly as the iOS
    ``confirmToolResult`` does. ``on_acked(tool_name)`` fires after each ack — callers use it to end
    their own loop on a terminal tool.

    When ``disconnect_on_terminal`` (default), a terminal tool (createInsightCard / endOnboarding)
    also **disconnects our participant** right after acking — mirroring mobile, whose `endSession()`
    calls `room.disconnect()` (it never deletes the room). The caller's `on_acked` still finalizes the
    loop + transcript; this just makes leaving prompt and explicit rather than waiting on the poll.

    Returns the set of in-flight ack tasks (so the caller can drain them on teardown).
    """
    tasks: set = set()

    async def _ack(tool_name: str, call_id: str) -> None:
        result = CLIENT_TOOL_ACK_RESULT.get(tool_name, "completed")
        payload = json.dumps({"type": "tool_result", "toolCallId": call_id, "result": result}).encode("utf-8")
        await room.local_participant.publish_data(payload, reliable=True, topic=DATA_TOPIC)
        print(f"[{label}] acked client tool {tool_name} ({call_id}) -> {result!r}", flush=True)
        if on_acked is not None:
            on_acked(tool_name)  # caller finalizes its own loop/transcript first
        # A terminal tool ends the conversation — disconnect our own participant, like mobile's
        # endSession() (which calls room.disconnect(), not a server-side room delete).
        if disconnect_on_terminal and tool_name in TERMINAL_CLIENT_TOOLS:
            try:
                await room.disconnect()
                print(f"[{label}] disconnected (terminal tool {tool_name})", flush=True)
            except Exception as exc:  # noqa: BLE001 - best-effort; teardown will disconnect too
                print(f"[{label}] disconnect after terminal tool failed ({exc})", flush=True)

    def _on_data(packet) -> None:
        if getattr(packet, "topic", None) != DATA_TOPIC:
            return
        try:
            msg = json.loads(bytes(packet.data).decode("utf-8"))
        except (AttributeError, TypeError, ValueError):
            return
        if not isinstance(msg, dict) or msg.get("type") != "tool_call":
            return
        call_id = msg.get("toolCallId", "")
        if not call_id:
            return
        task = asyncio.create_task(_ack(msg.get("toolName", ""), call_id))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    room.on("data_received", _on_data)
    return tasks


def _dispatch_agent(agent_name: str, room: str, metadata: str) -> None:
    """Explicitly dispatch the agent into the room (visible via the lk CLI).

    Best-effort and non-fatal: the agent may also auto-join (livekit.toml [agent.rooms]),
    so a non-zero exit, a hang/timeout, or a missing `lk` must NOT abort the session —
    the caller still has a connected room to drive and a transcript to write.
    """
    args = ["lk", "dispatch", "create", "--agent-name", agent_name, "--room", room]
    if metadata:
        args += ["--metadata", metadata]
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"WARN  lk dispatch create failed ({exc}); relying on auto-join if configured", flush=True)
        return
    if res.returncode != 0:
        print(f"WARN  lk dispatch create returned {res.returncode}: {res.stderr.strip()}", flush=True)
    else:
        print("dispatched agent", flush=True)


async def _run_session(
    room_name: str, agent_name: str, metadata: str, io_dir: Path, require_first: bool = True
) -> None:
    from livekit import rtc

    turns_dir = io_dir / "turns"
    replies_dir = io_dir / "replies"
    turns_dir.mkdir(parents=True, exist_ok=True)
    replies_dir.mkdir(parents=True, exist_ok=True)
    done_flag = io_dir / "DONE"

    transcript: list[dict] = []
    # Reply accumulation. `text` grows as transcription chunks arrive; `active` counts
    # in-flight stream readers so _settle never flushes while a stream is still being read;
    # `tasks` tracks the reader tasks so we can cancel stragglers before advancing a turn.
    state: dict = {"text": "", "active": 0, "tasks": set()}

    room = rtc.Room()

    async def _read_stream(reader) -> None:
        state["active"] += 1
        decoder = _TranscriptDecoder()  # the agent sends JSON TimedString lines; the user sends text
        try:
            async for chunk in reader:
                # Append incrementally, not only after the stream closes, so _settle sees it grow.
                state["text"] += decoder.push(chunk)
            state["text"] += decoder.flush()  # a final line that arrived without its newline
        finally:
            state["active"] -= 1

    def _on_transcription(reader, *_info) -> None:
        task = asyncio.create_task(_read_stream(reader))
        state["tasks"].add(task)
        task.add_done_callback(state["tasks"].discard)

    async def _cancel_readers() -> None:
        """Cancel + await any still-running reader tasks so late chunks from a timed-out
        turn can't bleed into the next turn's reply (no-op when _settle already saw active==0)."""
        pending = [t for t in state["tasks"] if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # Register the transcription handler. Without it every reply is empty, so a
    # registration failure must ABORT the session rather than silently write blanks.
    try:
        room.register_text_stream_handler(TRANSCRIPTION_TOPIC, _on_transcription)
    except Exception as exc:
        raise RuntimeError(
            f"could not register text stream handler for {TRANSCRIPTION_TOPIC!r} ({exc}); "
            "pin a compatible livekit SDK (pip install -U livekit) — aborting so replies aren't silently empty"
        ) from exc

    # Mint first: the token endpoint can bind the session to another room, and the join-order guard
    # below must check the room that is actually joined.
    asked = room_name
    token, room_name = _mint_token("qa-talk-to-aurie", room_name, use_endpoint=True)  # the test user (drives turns)
    if room_name != asked:
        print(f"ROOM_CHANGED: the token endpoint bound this session to {room_name} (asked for {asked}); "
              "attach observers to that room", flush=True)

    # We are the one sending turns, so we have to be the first non-agent participant in the room.
    # Fail before connecting rather than after: a session that starts second answers nothing, and
    # the symptom (empty replies, then the away timer) points at the agent instead of the join order.
    if require_first:
        try:
            peers = _peers(await _list_participants(room_name), "qa-talk-to-aurie")
        except RoomStateUnavailable as exc:
            raise RoomStateUnavailable(
                f"{exc} — can't confirm this session joins first, so refusing to connect "
                "(pass --allow-late to connect anyway)."
            ) from exc
        if peers:
            raise RuntimeError(
                f"{room_name} already has participant(s) {', '.join(peers)} — this session would "
                f"join second and every turn would be silently discarded, because {JOIN_ORDER_NOTE}. "
                "Start `session` first and attach any observer afterwards, or use a fresh room "
                "(pass --allow-late to connect anyway)."
            )

    await room.connect(_env("LIVEKIT_URL"), token)
    print(f"connected to {room_name}", flush=True)

    # Stand in for the app: ack the agent's client-tool forwards so a tool the prompter drives (e.g.
    # createInsightCard) completes instead of going unanswered; a terminal tool ends the session.
    def _on_client_tool(tool_name: str) -> None:
        transcript.append({"turn": None, "role": "system", "text": f"[client tool {tool_name} acked]"})
        if tool_name in TERMINAL_CLIENT_TOOLS:
            print(f"terminal client tool {tool_name} acked — ending session", flush=True)
            done_flag.touch()  # the conversation loop wraps up on the next tick

    ack_tasks = _register_client_tool_acker(room, label="client-tool", on_acked=_on_client_tool)

    # Dispatch off the event loop — `lk dispatch` is a blocking subprocess and would
    # otherwise stall the room, dropping or delaying transcription streams.
    await asyncio.to_thread(_dispatch_agent, agent_name, room_name, metadata)

    def _flush_reply(idx: int, settled: bool = True) -> None:
        text = state["text"].strip()
        reply = {"turn": idx, "text": text, "settled": settled}
        if not settled:
            reply["partial"] = True  # _settle timed out — reply may be truncated/empty
        (replies_dir / f"{idx}.json").write_text(json.dumps(reply, ensure_ascii=False), encoding="utf-8")
        transcript.append({"turn": idx, "role": "assistant", "text": text, "settled": settled})
        state["text"] = ""
        if not settled:
            print(f"WARN  turn {idx} reply may be incomplete (_settle timed out)", flush=True)

    # Capture the greeting (first_message) as reply 0.
    settled = await _settle(state)
    await _cancel_readers()
    _flush_reply(0, settled)
    print("greeting captured", flush=True)

    # Conversation loop: drain any pending turn file BEFORE honoring DONE, so a turn
    # written just as the sentinel appears isn't dropped.
    next_turn = 1
    while True:
        turn_file = turns_dir / f"{next_turn}.txt"
        if turn_file.exists():
            user_text = turn_file.read_text(encoding="utf-8")
            transcript.append({"turn": next_turn, "role": "user", "text": user_text})
            state["text"] = ""
            await room.local_participant.send_text(user_text, topic=CHAT_TOPIC)
            settled = await _settle(state)
            await _cancel_readers()
            _flush_reply(next_turn, settled)
            print(f"turn {next_turn} answered", flush=True)
            next_turn += 1
            continue
        if done_flag.exists():
            break
        await asyncio.sleep(0.4)

    # Drain in-flight acks first — a terminal tool's ack disconnects us, so let it finish before
    # we (idempotently) disconnect and write outputs.
    if ack_tasks:
        await asyncio.gather(*ack_tasks, return_exceptions=True)
    try:
        await room.disconnect()
    except Exception as exc:  # noqa: BLE001 - a terminal tool may have already disconnected us
        print(f"disconnect after teardown: {exc}", flush=True)
    _write_transcript(room_name, agent_name, metadata, transcript, io_dir)
    print("session ended", flush=True)


async def _settle(state: dict, quiet: float = 1.5, timeout: float = 45.0) -> bool:
    """Return True once the streamed reply is stable for `quiet`s AND no transcription
    stream is still being read (state["active"] == 0). Return False on `timeout` — the
    reply may be partial/empty and the caller flags it. Gating on the in-flight reader
    count avoids concluding mid-stream while chunks are still arriving."""
    start = time.monotonic()
    last = state["text"]
    last_change = time.monotonic()
    while time.monotonic() - start < timeout:
        await asyncio.sleep(0.3)
        cur = state["text"]
        if cur != last:
            last, last_change = cur, time.monotonic()
        elif cur and state["active"] == 0 and time.monotonic() - last_change >= quiet:
            return True
    return False


def _write_transcript(room: str, agent: str, metadata: str, transcript: list[dict], io_dir: Path) -> None:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    meta = {}
    try:
        meta = json.loads(metadata) if metadata else {}
    except Exception:
        meta = {}
    raw = {"room": room, "agent_name": agent, "metadata": meta, "transcript": transcript}
    (OUTPUTS / f"{room}.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# talk-to-aurie — {room}", "", f"- agent: `{agent}`", f"- provider: `{meta.get('tts_provider', '?')}`", ""]
    role_label = {"user": "You", "assistant": "Aurie", "system": "System"}
    for t in transcript:
        who = role_label.get(t["role"], "Aurie")
        lines.append(f"**{who}:** {t['text']}\n")
    (OUTPUTS / f"{room}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"TRANSCRIPT_MD: {OUTPUTS / f'{room}.md'}", flush=True)


def _sender_identity(info: tuple) -> str:
    """Best-effort sender identity from the text-stream handler's trailing args, which vary
    by SDK version: some pass the identity as a bare str, others a participant-info object."""
    if not info:
        return ""
    first = info[0]
    if isinstance(first, str):
        return first
    for attr in ("identity", "participant_identity"):
        val = getattr(first, attr, None)
        if val:
            return str(val)
    return str(first)


# livekit-agents stamps these on each lk.transcription text stream (livekit.agents.types).
ATTR_TRANSCRIBED_TRACK_ID = "lk.transcribed_track_id"  # the audio track the segment transcribes
ATTR_TRANSCRIPTION_FINAL = "lk.transcription_final"  # "false" while interim, absent/"true" when final
# The agent participant carries these (set when an agents worker joins); used to tell it apart.
_AGENT_PARTICIPANT_ATTRS = ("lk.agent.name", "lk.agent.state")


def _track_owner(room, track_sid: str) -> tuple[str, bool]:
    """Map a transcribed-track sid to (owner_identity, owner_is_agent) by scanning room
    participants' published tracks. The agent is identified by its `lk.agent.*` attributes,
    not an identity substring — so attribution is reliable even with opaque identities."""
    if not track_sid:
        return "", False
    for part in getattr(room, "remote_participants", {}).values():
        for pub in (getattr(part, "track_publications", {}) or {}).values():
            if getattr(pub, "sid", None) == track_sid:
                attrs = dict(getattr(part, "attributes", {}) or {})
                return part.identity, any(k in attrs for k in _AGENT_PARTICIPANT_ATTRS)
    return "", False


def _resolve_role(room, track_sid: str, sender: str, agent_name: str, self_identity: str, human_identity: str) -> str:
    """Prefer track-ownership attribution (reliable): the agent's own audio track -> Aurie, any
    other participant's audio track -> a human talker (You). Fall back to the identity heuristic
    only when the SDK doesn't surface a transcribed-track id."""
    owner, is_agent = _track_owner(room, track_sid)
    if is_agent:
        return "Aurie"
    if owner:  # a non-agent participant published this audio track => a human speaker
        return "You"
    return _role_for(sender, agent_name, self_identity, human_identity)


def _role_for(sender: str, agent_name: str, self_identity: str, human_identity: str = "") -> str:
    """Label a transcription segment by its sender identity. Reliable when we control the
    identities (browser flow: the human joins as `human_identity` via our minted token, the
    agent is dispatched under `agent_name`). For anything else we DON'T guess "You" — we return
    "unknown" so the renderer shows the raw identity verbatim rather than risk swapping speakers."""
    sender_l = (sender or "").lower()
    if not sender_l or sender_l == (self_identity or "").lower():
        return "system"
    if human_identity and sender_l == human_identity.lower():
        return "You"
    if "agent" in sender_l or "aurie" in sender_l or (agent_name and agent_name.lower() in sender_l):
        return "Aurie"
    return "unknown"  # shown by raw sender identity, never guessed as the human


def _write_observe_transcript(room: str, transcript: list[dict], io_dir: Path) -> None:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    raw = {"room": room, "mode": "observe", "transcript": transcript}
    (OUTPUTS / f"{room}.observe.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# talk-to-aurie observer — {room}", ""]
    for seg in transcript:
        who = seg["role"] if seg["role"] in ("You", "Aurie", "System") else seg.get("sender", "?")
        suffix = "" if seg.get("final", True) else " _(interim)_"
        lines.append(f"**{who}:**{suffix} {seg['text']}\n")
    (OUTPUTS / f"{room}.observe.md").write_text("\n".join(lines), encoding="utf-8")


async def _run_observe(  # noqa: PLR0913 - small QA helper; flags are clearer than a config object
    room_name: str,
    io_dir: Path,
    agent_name: str = "",
    metadata: str = "",
    dispatch: bool = False,
    identity: str = "qa-observer",
    duration: float = 0.0,
    human_identity: str = "qa-human",
    ack_client_tools: bool = True,
    disconnect_on_terminal: bool = True,
    require_peer: bool = True,
    peer_timeout: float = 120.0,
) -> None:
    from livekit import rtc

    io_dir.mkdir(parents=True, exist_ok=True)
    done_flag = io_dir / "DONE"
    transcript: list[dict] = []
    state: dict = {"tasks": set()}  # tasks: in-flight stream readers
    room = rtc.Room()

    def _stream_attrs(reader) -> dict:
        info = getattr(reader, "info", None)
        return dict(getattr(info, "attributes", None) or {}) if info is not None else {}

    async def _read_stream(reader, sender: str) -> None:
        track_id = _stream_attrs(reader).get(ATTR_TRANSCRIBED_TRACK_ID, "")  # present in the header
        decoder = _TranscriptDecoder()  # the agent sends JSON TimedString lines; the user sends text
        chunks: list[str] = []
        try:
            async for chunk in reader:
                chunks.append(decoder.push(chunk))
        finally:
            chunks.append(decoder.flush())  # a final line that arrived without its newline
            attrs = _stream_attrs(reader)  # re-read: the trailer adds the final flag at close
            track_id = track_id or attrs.get(ATTR_TRANSCRIBED_TRACK_ID, "")
            is_final = attrs.get(ATTR_TRANSCRIPTION_FINAL, "true").lower() != "false"
            text = "".join(chunks).strip()
            if text:
                role = _resolve_role(room, track_id, sender, agent_name, identity, human_identity)
                entry = {"role": role, "sender": sender, "track_id": track_id, "final": is_final, "text": text}
                # Per-word alignment, when the segment carried any. Recorded rather than discarded so
                # a run is evidence that real TTS timings are on the wire, not just that the
                # transcript rendered — which is the thing worth checking after enabling them.
                if decoder.timings:
                    entry["timings"] = decoder.timings
                transcript.append(entry)
                _write_observe_transcript(room_name, transcript, io_dir)  # live update
                who = role if role in ("You", "Aurie") else (sender or "?")
                timed = f" [{len(decoder.timings)} timed]" if decoder.timings else ""
                print(f"[{who}]{'' if is_final else ' (interim)'}{timed} {text}", flush=True)

    def _on_transcription(reader, *info) -> None:
        task = asyncio.create_task(_read_stream(reader, _sender_identity(info)))
        state["tasks"].add(task)
        task.add_done_callback(state["tasks"].discard)

    # Without the handler the observer captures nothing, so a registration failure must ABORT.
    try:
        room.register_text_stream_handler(TRANSCRIPTION_TOPIC, _on_transcription)
    except Exception as exc:
        raise RuntimeError(
            f"could not register text stream handler for {TRANSCRIPTION_TOPIC!r} ({exc}); "
            "pin a compatible livekit SDK (pip install -U livekit) — aborting so the transcript isn't silently empty"
        ) from exc

    # Stand in for the app: ack the agent's client-tool forwards, and end on a terminal tool.
    if ack_client_tools:
        def _on_client_tool(tool_name: str) -> None:
            transcript.append(
                {"role": "System", "sender": identity, "track_id": "", "final": True,
                 "text": f"[client tool {tool_name} acked]"}
            )
            _write_observe_transcript(room_name, transcript, io_dir)
            # End the observe loop on a terminal tool only when we're ending the session — with
            # --no-disconnect (disconnect_on_terminal=False) we ack and keep observing instead.
            if disconnect_on_terminal and tool_name in TERMINAL_CLIENT_TOOLS:
                print(f"terminal client tool {tool_name} acked — ending session", flush=True)
                done_flag.touch()  # the observe loop wraps up on the next tick

        state["ack_tasks"] = _register_client_tool_acker(
            room, label="client-tool", on_acked=_on_client_tool, disconnect_on_terminal=disconnect_on_terminal
        )

    # Never be the first non-agent participant: the observer is silent, so claiming the agent's
    # text link would swallow every turn the real talker sends. Waiting also covers the browser
    # flows, where the human is expected to be connected before we attach.
    if require_peer:
        try:
            peer = await _await_peer(room_name, identity, timeout=peer_timeout)
        except RoomStateUnavailable as exc:
            raise RoomStateUnavailable(
                f"{exc} — can't confirm a talker is present, so refusing to observe "
                "(pass --allow-first to observe anyway)."
            ) from exc
        if not peer:
            raise RuntimeError(
                f"no talker joined {room_name} within {peer_timeout:.0f}s. Observing now would make "
                f"this silent observer the agent's linked participant, and {JOIN_ORDER_NOTE}. "
                "Start the text `session` (or connect the browser/Console) first, then observe "
                "(pass --allow-first to observe an empty room anyway)."
            )
        print(f"talker {peer} present — attaching observer", flush=True)

    token, _ = _mint_token(identity, room_name)
    connected = False
    # connect + dispatch live INSIDE the try so a Ctrl-C/cancellation during them still runs
    # the cleanup, transcript write, and TRANSCRIPT_MD line below (all in finally).
    try:
        await room.connect(_env("LIVEKIT_URL"), token)
        connected = True
        print(
            f"observing {room_name} (silent) — talk in your browser/console; touch DONE or Ctrl-C to end",
            flush=True,
        )

        # Dispatch off the event loop — `lk dispatch` blocks and would stall transcription streams.
        if dispatch and agent_name:
            await asyncio.to_thread(_dispatch_agent, agent_name, room_name, metadata)
        elif dispatch and not agent_name:
            print("WARN  --dispatch ignored: no --agent-name given (relying on auto-join if configured)", flush=True)

        start = time.monotonic()
        while not done_flag.exists():
            if duration and time.monotonic() - start >= duration:
                print(f"observe duration {duration:.0f}s reached", flush=True)
                break
            await asyncio.sleep(0.5)
    finally:
        # Let any in-flight ack finish (a terminal tool's ack disconnects us) before teardown —
        # don't cancel these.
        ack_pending = [t for t in state.get("ack_tasks", ()) if not t.done()]
        if ack_pending:
            await asyncio.gather(*ack_pending, return_exceptions=True)
        # Cancel stragglers (each flushes its partial text in its own finally).
        pending = [t for t in state["tasks"] if not t.done()]
        for t in pending:
            t.cancel()
        try:
            # Drain readers + disconnect. Both await and can be re-delivered CancelledError
            # mid-teardown (Ctrl-C) — which `except Exception` would NOT catch — so the inner
            # `finally` below still finalizes the transcript + output regardless.
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if connected:
                await room.disconnect()
        except BaseException:  # noqa: BLE001 - swallow re-delivered cancellation during teardown
            pass
        finally:
            _write_observe_transcript(room_name, transcript, io_dir)
            print(f"TRANSCRIPT_MD: {OUTPUTS / f'{room_name}.observe.md'}", flush=True)
            print("observe ended", flush=True)


def _is_agent_participant(p) -> bool:
    """True if this participant is the voice agent (identity ``agent-…`` or the agent kind)."""
    from livekit import api

    info = getattr(api, "ParticipantInfo", None)
    agent_kind = getattr(getattr(info, "Kind", None), "AGENT", object())
    if (p.identity or "").startswith("agent"):
        return True
    if getattr(p, "kind", None) == agent_kind:
        return True
    return any(k.startswith("lk.agent") for k in (getattr(p, "attributes", None) or {}))


def _room_has_agent(participants) -> bool:
    """True if any participant is the voice agent.

    The agent only joins the real session room, never the idle ``console-<id>`` room the Console
    mints on open — so agent-presence uniquely identifies the room we want to observe.
    """
    return any(_is_agent_participant(p) for p in participants)


class RoomStateUnavailable(RuntimeError):
    """The room listing failed, so we do NOT know who is in the room."""


def _is_room_not_found(exc: BaseException) -> bool:
    """True for LiveKit's not-found signal — the room simply hasn't been created yet."""
    code = getattr(exc, "code", None)
    return code == "not_found" or "not_found" in str(exc)


async def _list_participants(room_name: str) -> list:
    """Current participants in ``room_name``; empty when the room doesn't exist yet.

    A room that doesn't exist is genuinely "nobody here". Every OTHER failure (auth, network,
    a 5xx) means the room state is *unknown*, and returning [] for those would make the join-order
    guards below read "empty room" and wave the caller through — reintroducing the silent hang
    they exist to prevent, at exactly the moments things are already going wrong. So they raise.
    """
    from livekit import api

    url = _env("LIVEKIT_URL").replace("wss://", "https://").replace("ws://", "http://")
    lkapi = api.LiveKitAPI(url, _env("LIVEKIT_API_KEY"), _env("LIVEKIT_API_SECRET"))
    try:
        res = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room_name))
        return list(res.participants)
    except Exception as exc:  # noqa: BLE001 - re-raised below unless it's the not-yet-created room
        if _is_room_not_found(exc):
            return []
        raise RoomStateUnavailable(f"could not read participants for {room_name}: {exc}") from exc
    finally:
        await lkapi.aclose()


def _peers(participants, self_identity: str) -> list[str]:
    """Identities of the non-agent participants that aren't us — i.e. candidate talkers."""
    return [
        p.identity
        for p in participants
        if (p.identity or "") != self_identity and not _is_agent_participant(p)
    ]


# Why both guards below exist: livekit-agents links its RoomIO to exactly ONE participant — the
# first non-agent one to connect — and then drops `lk.chat` text from every other identity with no
# warning and no debug line. So whoever sends turns MUST join before anyone else, and getting it
# wrong is invisible: the greeting plays, each turn returns empty, and the away timer closes the
# session ~120s later, all of which reads as a broken agent. Documenting the order wasn't enough
# (ENG-1125) — these two checks make the ordering impossible to get wrong from either side.

JOIN_ORDER_NOTE = (
    "livekit-agents links the agent's text input to the FIRST non-agent participant to join and "
    "silently drops turns from anyone else (ENG-1125)"
)


async def _await_peer(room_name: str, self_identity: str, timeout: float = 120.0, poll: float = 1.0) -> str | None:
    """Block until someone else (non-agent) is in ``room_name``, so we can't claim the agent's link.

    Returns that participant's identity, or None on timeout.
    """
    deadline = time.monotonic() + timeout
    announced = False
    last_error: RoomStateUnavailable | None = None
    while True:
        try:
            peers = _peers(await _list_participants(room_name), self_identity)
            last_error = None
        except RoomStateUnavailable as exc:
            # A blip mid-wait shouldn't end the wait — keep polling and let the deadline decide.
            # But never treat "couldn't ask" as "nobody's there": that's the fail-open we're avoiding.
            peers, last_error = [], exc
        if peers:
            return peers[0]
        if time.monotonic() >= deadline:
            if last_error is not None:
                raise last_error
            return None
        if not announced:
            print(
                f"waiting for the talker to join {room_name} before observing — {JOIN_ORDER_NOTE}",
                flush=True,
            )
            announced = True
        await asyncio.sleep(poll)


async def _await_new_room(detect_timeout: float = 1200.0, poll: float = 2.0) -> str | None:
    """Poll the LiveKit API for the new session room the **agent** joins, and return its name.

    Baselines existing rooms, then waits for a fresh room that actually contains the agent — that
    uniquely identifies the session room. This matters because the LiveKit Cloud Console mints an
    idle ``console-<id>`` room when you *open* it, before the session room that **Start session**
    creates; keying on agent-presence skips that idle room instead of grabbing whichever appears
    first. Falls back to a populated fresh room only if no agent room shows up before the timeout.
    (The hosted Agents Playground redirects to the Console when you're logged into Cloud, so there's
    no Manual URL+token tab to drive our own room name through — hence auto-detect.)
    """
    from livekit import api

    url = _env("LIVEKIT_URL").replace("wss://", "https://").replace("ws://", "http://")
    lkapi = api.LiveKitAPI(url, _env("LIVEKIT_API_KEY"), _env("LIVEKIT_API_SECRET"))
    try:
        baseline = {r.name for r in (await lkapi.room.list_rooms(api.ListRoomsRequest())).rooms}
        print(f"waiting for the agent's session room (baseline: {len(baseline)} existing)…", flush=True)
        deadline = time.monotonic() + detect_timeout
        fallback: str | None = None
        while time.monotonic() < deadline:
            rooms = (await lkapi.room.list_rooms(api.ListRoomsRequest())).rooms
            fresh = sorted((r for r in rooms if r.name not in baseline), key=lambda r: r.creation_time, reverse=True)
            newest_populated: str | None = None
            for r in fresh:
                if r.num_participants == 0:
                    continue
                # fresh is newest-first, so the first populated room this poll is the newest one —
                # the likeliest session room if we never positively identify the agent.
                newest_populated = newest_populated or r.name
                parts = (await lkapi.room.list_participants(api.ListParticipantsRequest(room=r.name))).participants
                if _room_has_agent(parts):
                    return r.name
            # Refresh the fallback each poll to the current newest populated fresh room, so a stale
            # idle room seen early can't win over a session room that fills in later.
            if newest_populated:
                fallback = newest_populated
            await asyncio.sleep(poll)
        return fallback  # timed out waiting for an agent room
    finally:
        await lkapi.aclose()


def cmd_observe(args: argparse.Namespace) -> int:
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        if not _env(k):
            print(f"FAIL  {k} not set — run preflight", flush=True)
            return 1
    metadata = ""
    if args.metadata_file:
        metadata = Path(args.metadata_file).read_text(encoding="utf-8").strip()

    # --room may be omitted to auto-detect the next new room (e.g. one the Cloud Console mints).
    room = args.room
    if not room:
        if args.dispatch:
            print(
                "FAIL  --dispatch needs --room — auto-detect is for observing a room created "
                "elsewhere (e.g. the Cloud Console), not one we dispatch into",
                flush=True,
            )
            return 1
        room = asyncio.run(_await_new_room(args.detect_timeout))
        if not room:
            print("FAIL  no new room detected before timeout — start the session, or pass --room", flush=True)
            return 1
        print(f"auto-detected room: {room}", flush=True)

    io_dir = Path(args.io_dir) if args.io_dir else Path(f"/tmp/lk-talk/{room}")
    io_dir.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(
            _run_observe(
                room,
                io_dir,
                args.agent_name,
                metadata,
                args.dispatch,
                args.identity,
                args.duration,
                args.speaker_identity,
                ack_client_tools=not args.no_ack_client_tools,
                disconnect_on_terminal=not args.no_disconnect,
                require_peer=not args.allow_first,
                peer_timeout=args.peer_timeout,
            )
        )
    except RuntimeError as exc:  # join-order guard — actionable, not a traceback
        print(f"FAIL  {exc}", flush=True)
        return 1
    except KeyboardInterrupt:
        print("observe interrupted", flush=True)
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    """Mint a room-join token for a human to connect a browser (LiveKit Agents Playground).

    The token is written to a 0600 file and only its PATH is printed — the JWT itself never
    touches stdout/argv/logs (secrets rule). The user opens the Playground 'Manual' tab and
    pastes LIVEKIT_URL (printed) + the token from that file.
    """
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        if not _env(k):
            print(f"FAIL  {k} not set — run preflight", flush=True)
            return 1
    # The browser human is the test user, so source from the endpoint when opted in.
    token, room = _mint_token(args.identity, args.room, use_endpoint=True)  # mint first so a failure leaves no empty file
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Create the file 0600 *before* writing (no world-readable window), and chmod afterwards
    # too so a pre-existing, more-permissive file is also tightened. The token is a credential.
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token)
    try:
        os.chmod(out, 0o600)
    except OSError:  # pragma: no cover - best-effort on platforms without chmod
        pass
    print(f"TOKEN_FILE: {out}  (open this; do not paste it into chat)", flush=True)
    print(f"LIVEKIT_URL: {_env('LIVEKIT_URL')}", flush=True)
    print("PLAYGROUND: https://agents-playground.livekit.io/  (use the Manual connect tab)", flush=True)
    print(f"ROOM: {room}  IDENTITY: {args.identity}", flush=True)
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        if not _env(k):
            print(f"FAIL  {k} not set — run preflight", flush=True)
            return 1
    metadata = ""
    if args.metadata_file:
        metadata = Path(args.metadata_file).read_text(encoding="utf-8").strip()
    try:
        asyncio.run(
            _run_session(
                args.room, args.agent_name, metadata, Path(args.io_dir), require_first=not args.allow_late
            )
        )
    except RuntimeError as exc:  # join-order guard — actionable, not a traceback
        print(f"FAIL  {exc}", flush=True)
        return 1
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Talk to the Aurie voice agent over LiveKit (text mode).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight", help="check lk CLI, livekit SDK, and LIVEKIT_* env")

    s = sub.add_parser("session", help="hold a room open and drive a text conversation")
    s.add_argument("--room", required=True)
    # Default matches the skill's default target (local). Pass --agent-name aurie-voice-agent for remote.
    s.add_argument("--agent-name", default="local-aurie-voice-agent")
    s.add_argument("--metadata-file", default="")
    s.add_argument("--io-dir", required=True, help="dir holding turns/, replies/, DONE")
    s.add_argument(
        "--allow-late",
        action="store_true",
        help="connect even when someone else is already in the room; by default this aborts, "
        "because joining second means the agent drops every turn we send (ENG-1125)",
    )

    o = sub.add_parser("observe", help="join a room as a silent observer and capture a live transcript")
    o.add_argument("--room", help="room to observe; omit to auto-detect the next new room (e.g. Cloud Console)")
    o.add_argument("--io-dir", default="", help="dir holding DONE (touch to stop); defaults to /tmp/lk-talk/<room>")
    o.add_argument(
        "--detect-timeout",
        type=float,
        default=1200.0,
        help="when --room is omitted, seconds to wait for a new room to appear",
    )
    o.add_argument("--agent-name", default="", help="agent to dispatch (with --dispatch); else rely on auto-join")
    o.add_argument("--metadata-file", default="")
    o.add_argument("--dispatch", action="store_true", help="dispatch the agent into the room first (browser mode)")
    o.add_argument("--identity", default="qa-observer", help="the observer's own (silent) identity")
    o.add_argument(
        "--speaker-identity",
        default="qa-human",
        help="the human talker's identity (match the `token` --identity) so their lines label as 'You'",
    )
    o.add_argument("--duration", type=float, default=0.0, help="auto-stop after N seconds (0 = until DONE/Ctrl-C)")
    o.add_argument(
        "--no-ack-client-tools",
        action="store_true",
        help="don't ack the agent's client-tool forwards (createInsightCard/endOnboarding); by "
        "default the observer acks them and ends the session on a terminal tool",
    )
    o.add_argument(
        "--no-disconnect",
        action="store_true",
        help="on a terminal client tool, ack but DON'T disconnect our participant; by default a "
        "terminal tool disconnects us (mirroring mobile's endSession) — pass this to keep observing",
    )
    o.add_argument(
        "--allow-first",
        action="store_true",
        help="observe even when nobody else is in the room; by default we wait for the talker, "
        "because an observer that joins first becomes the agent's linked participant and "
        "silently swallows every turn (ENG-1125)",
    )
    o.add_argument(
        "--peer-timeout",
        type=float,
        default=120.0,
        help="seconds to wait for the talker before giving up (see --allow-first)",
    )

    t = sub.add_parser("token", help="mint a browser-join token to a file (path printed, not the token)")
    t.add_argument("--room", required=True)
    t.add_argument("--identity", default="qa-human")
    t.add_argument("--out", required=True, help="file to write the token to (chmod 0600)")

    sc = sub.add_parser(
        "start-conversation",
        help="POST /ai/conversations/:id/start with firstMessage + optional "
             "firstMessageContext, before dispatch; echoes the conversation_id",
    )
    sc.add_argument("--conversation-id", default="", help="reuse this id; omit to mint a UUID v4")
    sc.add_argument("--first-message", default="", help="the opener (same text you put in metadata first_message)")
    sc.add_argument("--first-message-file", default="", help="path to the opener text (alternative to --first-message)")
    sc.add_argument(
        "--first-message-context",
        default="",
        help="optional firstMessageContext as an inline JSON object, e.g. '{\"isProfile\": true}'",
    )
    sc.add_argument(
        "--first-message-context-file",
        default="",
        help="optional firstMessageContext as a JSON file (mutually exclusive with --first-message-context)",
    )

    args = parser.parse_args()
    if args.cmd == "preflight":
        sys.exit(cmd_preflight(args))
    if args.cmd == "session":
        sys.exit(cmd_session(args))
    if args.cmd == "observe":
        sys.exit(cmd_observe(args))
    if args.cmd == "token":
        sys.exit(cmd_token(args))
    if args.cmd == "start-conversation":
        sys.exit(cmd_start_conversation(args))


if __name__ == "__main__":
    main()
