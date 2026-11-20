---
name: talk-to-aurie
description: >
  QA the Aurie voice agent (aurie-voice-agent) end-to-end THROUGH LiveKit — the realtime voice
  path. Sibling to `chat-with-aurie`, which hits the Aura AI API directly; this connects to a
  LiveKit room, dispatches the agent with client-like metadata, and either lets YOU talk to
  Aurie by voice (Playground or a `console` mic session) or lets Claude play the user over
  text streams. Targets a local worker by default or the remote agent, with a silent in-room
  observer capturing a transcript. Trigger: "talk to Aurie over LiveKit", "let me talk to
  Aurie", "QA the voice agent end to end", "test the LiveKit agent", "watch an Aurie call",
  "test livekit", "test voice", "talk to Aurie by voice", "livekit console", "test Aurie from
  the console", "give me a console link to talk to Aurie". This is the skill for ANY voice /
  mic / console / LiveKit Aurie test — anything but scripted text-only dialogue-API QA (that's
  `chat-with-aurie`). Also triggers on "QA the walking / morning check-in / evening wind-down
  practice conversation".

metadata:
  version: "3.3.0"
knowledge:
  reads:
    - Guide/Index
    - Products/Aurie
    - Tools/LiveKit
    - Tools/Aura API
---

# talk-to-aurie

> **Scripted text-only QA of the dialogue API (no LiveKit, no voice) → use [`chat-with-aurie`](../chat-with-aurie/SKILL.md).**
> This skill is the realtime **voice/LiveKit** path; for a Claude-plays-user text run straight against
> `/ai/dialogue/ask`, `chat-with-aurie` is the right tool.

QA driver for the **Aurie voice agent over LiveKit**. Where `chat-with-aurie` calls the Aura
dialogue API directly, this connects to a real LiveKit room and exercises the full agent path
(audio/text input → STT → Aura OpenAI-compatible LLM (`/ai/openai/responses` by default, `/ai/openai/chat/completions` when overridden) → TTS → synced transcription) against the deployed
or a locally-run `aurie-voice-agent` worker.

The agent accepts **text** on the `lk.chat` stream and publishes replies as a **synced
transcription** stream (`RoomOptions(text_output=TextOutputOptions(sync_transcription=True))`),
so both a voice client (mic/speaker) and a text-only client work.

## The transcript payload

Two shapes arrive on `lk.transcription`, and `livekit_chat.py` decodes both — nothing to configure:

| Sender | Payload |
|---|---|
| the **agent** | one newline-terminated JSON `TimedString` per word — `{"text": "hello", "end_time": 0.67}` |
| the **user** (STT), or any agent with `AGENT_TRANSCRIPT_JSON_FORMAT=false` | raw text |

Each timing key is **optional and absent** (never null) rather than guaranteed, so treat both as
nullable. Two reasons a key goes missing, and the first is the normal case:

- **`start_time` is not sent when the transcript is synced to playback** (`sync_transcription=True`,
  the default whenever the agent has audio output). The synchronizer restamps each word with the
  moment it should be revealed, as `end_time` relative to the start of that utterance — so a client
  that wants a word's start derives it from the previous word's `end_time`, and 0 for the first word.
- Audio played from **pre-synthesized bytes** carries no alignment at all, and the agent logs
  `use_tts_aligned_transcript is enabled but no agent transcript was returned from tts`. Aurie's
  greeting is *not* this case — it is `session.say(text)`, which is synthesized and does come back
  timed.

Observe mode records the per-word timings on each transcript entry as a `timings` array and prints a
`[N timed]` marker on the line. The marker shows timings reached the client; whether they came from
real TTS alignment rather than the SDK's estimate is a separate question, answered by the **absence**
of that `no agent transcript was returned from tts` warning in the agent log.

## The matrix (defaults in **bold**)

| Axis | Options | Default |
|------|---------|---------|
| **mode** | `voice` · `text` | **`voice`** |
| **route** (voice only) | `browser` (LiveKit Agents Playground) · `console` (terminal mic/speaker) | **`browser`** |
| **target** | `local` (a worker you run) · `remote` (deployed agent) | **`local`** |
| **LLM API** (`llm_api_mode`) | `responses` · `chat_completions` | **`responses`** |
| **monitoring** | silent observer (live transcript) + log tail | **on** |

So the default run is **voice · browser · local · responses · monitoring on**: you talk to a
locally-run Aurie in your browser while the skill dispatches the agent, silently observes the room
to build a live transcript, and tails the worker log.

- **voice** = *you* (the human) talk to Aurie with your real voice; Claude orchestrates + observes.
- **text** = *Claude* plays the user, composing each turn over the text streams (no audio).
- **console ⇒ local always** (`python src/main.py console` runs the worker in-process on your machine).
- **LLM API** = the backend OpenAI path the agent's LLM uses, set via the `llm_api_mode` key in job
  metadata. The deployed `aurie-voice-agent` and a local worker both default to **`responses`**
  (`/ai/openai/responses`), so the metadata templates below omit the key — a run then exercises the
  same path production serves, which is what you usually want. Set
  `llm_api_mode: "chat_completions"` explicitly to QA the legacy Completions path.

## Join order: the talker connects first

**Whoever sends turns joins first; the observer attaches after.** `livekit-agents` links the agent's
input to the first non-agent participant to connect and drops `lk.chat` from every other identity —
no warning, no debug line. An observer that joins first swallows the conversation, and the symptom
accuses the agent rather than the order: greeting plays, every turn comes back empty, away timer
closes the session ~120 s later. Once claimed the link can't be stolen, so attaching after is safe.

The script enforces this ([ENG-1125](https://linear.app/aura-health/issue/ENG-1125)): `session`
aborts if anyone is already in the room, `observe` waits (`--peer-timeout`, default 120 s) for a
talker; override with `--allow-late` / `--allow-first`. An agent alone doesn't count — it never
claims its own link. A room listing that fails to *read* is never treated as empty: both fail closed.

**Staging a deliberate wrong-order test needs a DIFFERENT identity.** `_peers()` filters the
hardcoded self-identity `qa-talk-to-aurie`, so two `session` processes do not trip the occupancy
guard — the first is excluded as "self", and LiveKit kicks the duplicate identity anyway, so the
room genuinely is empty. Hold the room with an observer (`qa-observer`) instead.

## Script

`scripts/livekit_chat.py` — `python3 ${CLAUDE_PLUGIN_ROOT}/skills/talk-to-aurie/scripts/livekit_chat.py …`.
Subcommands:

| Subcommand | Role |
|---|---|
| `preflight` | checks `lk` CLI + `livekit` SDK + `LIVEKIT_*` env + an active auth call |
| `observe` | join a room as a **silent** participant, capture both sides' transcription to a live transcript, and **ack the agent's client-tool forwards** (ending on a terminal tool); `--dispatch` also dispatches the agent (voice/browser default). Omit `--room` to **auto-detect** the agent's session room (the fresh `console-<id>` the Console mints on Start session — keys on agent-presence to skip the idle console room) |
| `token` | mint a browser-join token to a **file** (path printed, never the token) for the Playground |
| `session` | persistent text client: Claude drives turns via files (text mode) |

Tokens/secrets never appear in stdout or argv (the `token` file is `chmod 0600`; only its path is printed).

## Step 1: Preflight (help set up if needed)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/talk-to-aurie/scripts/livekit_chat.py preflight
```

Checks, and tells the user how to fix any gap:
- **`lk` CLI** installed (`lk --version`) and authed (`lk project list`). If missing:
  `curl -sSL https://get.livekit.io/cli | bash`, then `lk cloud auth`.
- **`livekit` Python SDK** importable — `pip install livekit livekit-api`.
- **Env**: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` (from `~/.claude/settings.json` env or
  `lk project` config). Used to mint join tokens. Never echoed.
- **LiveKit auth (active check)**: one authenticated API call, so a *set-but-rejected* key
  (stale/rotated → `401`) fails here, not as a silent connect failure later. Fix: refresh the
  key/secret for the project (LiveKit Cloud dashboard, or `~/.livekit/cli-config.yaml` if `lk` is
  authed to the same project).

Stop and surface any failure before continuing.

## Step 2: Collect inputs

1. **Mode** — `voice` (default) or `text`.
2. **Route** (voice only) — `browser` (default) or `console`. `console` forces `target=local`.
3. **Target** — `local` (default) or `remote`.
4. **Agent name** — `remote` → `aurie-voice-agent`. `local` → **`local-aurie-voice-agent`**
   (start the worker with that `AGENT_NAME`; see the local note) so dispatch can't land on the
   deployed agent.
5. **Metadata** — client-like config, same schema as `docs/local-qa.md`:
   `tts_provider` (cartesia/elevenlabs/openai/inworld), optional `voice_id`, `model`, `tts_settings`,
   `user_id`, `first_message`, `conversation_id`, `prompter_name`, `llm_api_mode`.
   `llm_api_mode` picks the backend LLM path — `responses` for `/ai/openai/responses`,
   `chat_completions` for the legacy path; omit it to run on the agent's own `responses` default.
   `prompter_name` (default `coach`)
   picks the backend prompter — set it to `quickProcessing` to drive the `createInsightCard`
   client tool or `onboarding` to drive `endOnboarding` (see *Testing client-side tools*); the
   backend validates the value. Use a provider whose **key is set
   AND whose `voice_id`/`model` are valid** (a valid key alone can still yield no audio). ⚠ **`user_id`
   must be a REAL Aura user id** — the backend coach prompter loads that user's context to generate
   each turn, so a fake/non-existent id yields **empty replies** (the static greeting still plays, but
   every user turn comes back blank). See the identity gate.
6. **Scenario / persona** (text mode only) — what Claude role-plays.
7. **Stop condition** — turn count or a natural trigger; hard cap **20 turns** (text). For voice,
   the user ends the call (`touch DONE`, hang up, or Ctrl-C the observer).

## Step 2.5: Identity gate

The LLM always routes through the Aura backend (`{AURA_API_URL}/ai/openai/responses` by default, or
`/ai/openai/chat/completions` when `llm_api_mode` is overridden — authed by
the `AURA_API_KEY` service token), and **every user turn (text or voice) is a real coaching turn for
the metadata `user_id`** — the backend coach prompter loads that user's context to generate the reply.
So confirm whose account before connecting, and abort without an explicit "yes".

**The metadata `user_id` matters — it must be a REAL Aura user id.** A fake/non-existent id makes the
prompter return **no content**, so every user turn comes back **empty** (the static greeting still
plays, which masks the problem — see [`references/troubleshooting.md`](references/troubleshooting.md)). To exercise the LLM path, use a real account
(e.g. your own), and remember turns operate against **that user's** data. To test as a different user,
set `user_id` to that user's real id. (The greeting is a static `say()` and writes nothing — only user
turns hit the prompter.)

## Local-target note (start the worker first)

`local` and `remote` both use LiveKit **Cloud** as the server; only the worker differs. Dispatch
routes to any worker registered under the agent name, so a stray match (a local `dev` worker AND
the deployed agent) is answered by either. **To force local, give the local worker a distinct
`AGENT_NAME`:**

```bash
# in the LivekitVoiceAgent repo, with .env populated:
AGENT_NAME=local-aurie-voice-agent python src/main.py dev 2>&1 | tee /tmp/lk-talk/worker.log
```

Pass `--agent-name local-aurie-voice-agent`. The `tee` gives the monitor a log to tail. For
`remote`, target `aurie-voice-agent` with no local worker running, and tail `lk agent logs`.

## Step 3 — run the chosen mode

Generate a room once and reuse it. Metadata always goes in a file, never on argv.

```bash
ROOM="aurie-room-qa-$(python3 -c 'import uuid;print(uuid.uuid4().hex[:8])')"
mkdir -p /tmp/lk-talk/$ROOM/turns /tmp/lk-talk/$ROOM/replies
cat > /tmp/lk-talk/$ROOM/metadata.json <<'JSON'
{"tts_provider":"cartesia","user_id":"<REAL-aura-user-id>","first_message":"Hey! I'm Aurie. It's great to connect with you. How are you doing today?"}
JSON
```

`first_message` is **required** for a spoken greeting: the agent no longer ships an
`AGENT_FIRST_MESSAGE` env default (ENG-803), so a session whose metadata omits it opens silently.
Always include one — the default above is a safe local-QA greeting.

### Optional: start the conversation with context (`conversation_id` + `firstMessageContext`)

To QA a voice conversation that begins from a specific in-app entry point (feed tap, notification, Profile Insights CTA, …), open the Aura conversation over HTTP **before** dispatch and hand the agent that same `conversation_id` — the exact flow the Aura Consumer app uses (`LiveKitVoiceService+AuthToken.swift`: the app owns the `conversationId`, `/start`s it with `firstMessage` + `firstMessageContext`, then passes `conversation_id` + `first_message` in the token metadata so the agent **resumes that same conversation**).

```bash
# 1. Mint + start the conversation with the opener and the context.
#    Prints {conversation_id, http_status, body}; conversation_id also on stderr.
python3 ${CLAUDE_PLUGIN_ROOT}/skills/talk-to-aurie/scripts/livekit_chat.py \
  start-conversation \
  --first-message "Hey! I'm Aurie. It's great to connect with you. How are you doing today?" \
  --first-message-context '{"isProfile": true}' \
  > /tmp/lk-talk/$ROOM/start.json
CONVERSATION_ID=$(python3 -c 'import json;print(json.load(open("/tmp/lk-talk/'"$ROOM"'/start.json"))["conversation_id"])')

# 2. Put the SAME conversation_id + first_message in the metadata so the agent resumes it.
cat > /tmp/lk-talk/$ROOM/metadata.json <<JSON
{"tts_provider":"cartesia","user_id":"<REAL-aura-user-id>","conversation_id":"$CONVERSATION_ID","first_message":"Hey! I'm Aurie. It's great to connect with you. How are you doing today?"}
JSON
```

Keep the metadata `first_message` identical to the `--first-message` you `/start`ed with. Supported `firstMessageContext` shapes (from the IntelligenceService coach prompt — [`buildCurrentConversationContextInstructions`](https://github.com/AuraHealth/IntelligenceService/blob/master/src/prompters/prompter.js)):

| firstMessageContext | Injected context |
|---|---|
| `{"isNotification": true, "notificationId": "<uuid>"}` | Push notification the user tapped |
| `{"isConversation": true, "conversationId": "<uuid>"}` | Previous conversation used to start this one |
| `{"isOnboarding": true}` | Onboarding conversation continuity |
| `{"isFeed": true, "feedId": "<uuid>"}` | Feed card the user tapped |
| `{"isProfile": true}` | Profile Insights "Talk with Aurie" CTA — renders from the user's compiled insight renderers |
| `{"isMorningCheckIn": true}` | Morning Check-In daily practice — warm, unhurried "arrive in the day" grounding tone |
| `{"isEveningCheckIn": true}` | Evening Wind-Down daily practice — calm, slowing-down, reflect-and-set-down-the-day tone |
| `{"isWalkingPractice": true}` | Walking daily practice — hands-free **voice**; concise, in-motion conversational rhythm |

`start-conversation` needs `AURA_API_URL` + `AURA_AUTH_TOKEN` (the same env the token endpoint uses). Omit this step entirely for a plain session — the agent then creates its own conversation as before.

### A) Voice · browser (default)

The user talks in a browser; the skill silently observes the room for the transcript. The
**preferred** entry point is the **LiveKit Cloud Console** (A1) — when the operator is logged into
LiveKit Cloud, `agents-playground.livekit.io` redirects to the Console anyway, so the Console *is*
the normal path. The standalone Playground "Manual connect" (A2) is the fallback for when you are
**not** logged into LiveKit Cloud and the Playground actually shows a Manual tab.

The Console mints its own `console-<id>` room and dispatches the agent itself, so you don't know the
room name in advance — the observer (and any in-room ack helper) **auto-detects** it by polling the
LiveKit API (omit `--room`).

**A1 — LiveKit Cloud Console (preferred).** The Console dispatches with **no job metadata by
default**, and `user_id` is required (the agent fails fast without it), so you supply it explicitly.

**A1-remote — deployed agent (`target=remote`, the default voice-test path).** Use the Console's
built-in **Job metadata** field:

1. Open the Console agent console for the Aura project and select/confirm `aurie-voice-agent`:
   <https://cloud.livekit.io/projects/p_5bw0of3laoy/agents/console?agentName=aurie-voice-agent>
2. Click **Configure** (gear icon) and paste the session config into the **Job metadata** field —
   `user_id` must be a **real** Aura id (see the identity gate); set `prompter_name` to drive a
   specific flow (e.g. `quickProcessing` → `createInsightCard`, `onboarding` → `endOnboarding`):
   ```json
   {"user_id":"<real-aura-user-id>","prompter_name":"quickProcessing","first_message":"Hey! I'm Aurie. It's great to connect with you. How are you doing today?"}
   ```
   Add `tts_provider` / `voice_id` etc. as needed (same schema as `metadata.json`).
3. **Start the auto-detecting observer FIRST**, in the background — **omit `--room`** so it polls the
   LiveKit API for the new `console-<id>` room (you never read the name back). It must be running
   **before** you start the session: auto-detect baselines the rooms that already exist and only
   matches one created afterward, so a room that exists before the observer starts is never detected.
   ```bash
   python3 …/livekit_chat.py observe --io-dir /tmp/lk-talk/auto
   ```
4. **Then** click **Start session** and allow the mic — the new `console-<id>` room appears after the
   baseline and the observer latches onto it.

**A1-local — local worker (`target=local`).** The Console's Job metadata field dispatches to the
deployed agent, not your local worker. Bake the config into the worker via `AGENT_METADATA` instead:

1. Start the worker with the config baked in (see the local-target note), e.g.:
   ```bash
   AGENT_METADATA='{"tts_provider":"cartesia","voice_id":"<valid-voice>","user_id":"<uid>","first_message":"Hey! I'\''m Aurie. It'\''s great to connect with you. How are you doing today?"}' \
     TTS_DEFAULT_PROVIDER=cartesia AGENT_NAME=local-aurie-voice-agent \
     python src/main.py dev 2>&1 | tee /tmp/lk-talk/worker.log
   ```
   `TTS_DEFAULT_PROVIDER` must name a provider whose **key is set** — the startup gate fail-fasts otherwise.
2. **Start the auto-detecting observer FIRST**, in the background (same as A1-remote — omit `--room`,
   and it must be running **before** Start session so the new room appears after its baseline):
   ```bash
   python3 …/livekit_chat.py observe --io-dir /tmp/lk-talk/auto   # --io-dir also optional → /tmp/lk-talk/<room>
   ```
3. Open the Console pre-filtered for the local worker:
   <https://cloud.livekit.io/projects/p_5bw0of3laoy/agents/console?agentName=local-aurie-voice-agent>
   — leave room **METADATA blank**, **then** click **Start session**, allow the mic.

**A2 — Standalone Agents Playground (Manual connect, fallback).** Only when you are **not** logged
into LiveKit Cloud and <https://agents-playground.livekit.io/> shows a **Manual** connect tab. Here
you control the room name, so you dispatch into it explicitly:

1. **Mint a browser token** (written to a file; only the path is printed):
   ```bash
   python3 …/livekit_chat.py token --room "$ROOM" --identity qa-human \
     --out /tmp/lk-talk/$ROOM/user-token.txt
   ```
2. Tell the user: open the Playground, choose **Manual** connect, paste the printed `LIVEKIT_URL`
   and the token from `…/user-token.txt` (open the file — don't paste the token into chat), then
   **Connect** and allow the mic.
3. **Start the silent observer + dispatch the agent** in the **background**:
   ```bash
   python3 …/livekit_chat.py observe --room "$ROOM" --io-dir /tmp/lk-talk/$ROOM \
     --dispatch --agent-name local-aurie-voice-agent \
     --metadata-file /tmp/lk-talk/$ROOM/metadata.json
   ```
   (Remote target: drop `--dispatch` if the room matches the `aurie-room-*` auto-join glob, or keep
   `--dispatch --agent-name aurie-voice-agent` to be explicit.)

**Client-side tools are acked automatically.** `observe` (and `session`) stand in for the app: when
the agent forwards a `tool_call` (`createInsightCard` / `endOnboarding` / `showCrisisModal`) on the
`conversation` topic, the QA client replies with `{"type":"tool_result","toolCallId":…,"result":…}`
just as iOS `confirmToolResult` does — so the agent's tool completes instead of going unanswered. A
**terminal** tool (`createInsightCard` / `endOnboarding`) then **disconnects our participant** right
after acking — mirroring mobile, whose `endSession()` calls `room.disconnect()` (it does *not* delete
the room; the room lives on for any remaining participant, exactly as when a mobile client leaves).
No separate helper is needed; via the Console the auto-detected observer already covers it. Pass
`--no-ack-client-tools` to skip acking, or `--no-disconnect` to ack + keep observing after the tool.
See **Testing client-side tools**.

**Then, for any path:**
4. While the call runs, render the live transcript and monitor (see **Monitoring**).
5. End: `touch <io-dir>/DONE` (or the user Ctrl-C's the observer) → it finalizes
   `outputs/<room>.observe.{md,json}`.

### B) Voice · console (local hand-off)

`console` runs the worker **in your own terminal** with offline mic/speaker. It needs a real TTY
and **cannot be driven by the skill headless** (it reads keys via `termios`), so this is a
**hand-off**: give the user the exact command, then monitor.

Console reads its config from env, not dispatch metadata, so **both** must be set:
- `TTS_DEFAULT_PROVIDER` — the worker fail-fasts at startup on the active provider's missing key.
- `AGENT_METADATA` — the entrypoint raises *"TTS provider not found in metadata"* without it.

```bash
# user runs this in the LivekitVoiceAgent repo terminal:
export TTS_DEFAULT_PROVIDER=openai
export AGENT_METADATA="$(cat /tmp/lk-talk/$ROOM/metadata.json)"
python src/main.py console
```

The transcript is the console's own on-screen output. The skill monitors via the worker log
(metrics/errors); there's no separate room to observe (console is in-process, not a Cloud room).

### C) Text (Claude plays the user)

A LiveKit room is a **persistent** connection, so one long-running background process
(`session`) holds it open and Claude drives it via files.

> ⚠️ **`session` must join before any `observe`** — see [Join order](#join-order-the-talker-connects-first).
> The script enforces it, so a wrong order gives a `FAIL` rather than a silent hang.

1. Start `session` **in the background**:
   ```bash
   python3 …/livekit_chat.py session --room "$ROOM" \
     --agent-name local-aurie-voice-agent \
     --metadata-file /tmp/lk-talk/$ROOM/metadata.json --io-dir /tmp/lk-talk/$ROOM
   ```
   It mints a token, connects, dispatches the agent, captures the greeting as `replies/0.json`,
   then watches `turns/`.
2. **Loop** (until the stop condition or 20 turns):
   - Read `replies/<n>.json` (`text`) → render `> **Aurie:** …`.
   - Compose the next user turn and write it: `printf '%s' "<msg>" > …/$ROOM/turns/<n+1>.txt`.
   - Poll `replies/<n+1>.json` until it appears; render `> **You:** …` / `> **Aurie:** …`.
3. End: `touch /tmp/lk-talk/$ROOM/DONE` → writes `outputs/<room>.{json,md}`.

## Testing client-side tools

Client tools (`createInsightCard`, `endOnboarding`) are driven by the **backend prompter**, not the
agent, and the default `coach` prompter declares none — so two things must both be set to see one fire:

1. **`prompter_name` in `metadata.json`** — `quickProcessing` (→ `createInsightCard`) or `onboarding`
   (→ `endOnboarding`). The value is the camelCase backend prompter key, e.g.:
   ```bash
   cat > /tmp/lk-talk/$ROOM/metadata.json <<'JSON'
   {"tts_provider":"cartesia","user_id":"<id>","prompter_name":"quickProcessing","first_message":"Hey! Let's do a quick check-in."}
   JSON
   ```
2. **`AGENT_CLIENT_TOOLS_ENABLED=true` on the worker** — the agent only registers the client tools
   (so the streamed `tool_calls` bind) when this flag is on. For a local target:
   ```bash
   AGENT_NAME=local-aurie-voice-agent AGENT_CLIENT_TOOLS_ENABLED=true python src/main.py dev 2>&1 | tee /tmp/lk-talk/worker.log
   ```

Then drive the conversation toward where that prompter calls its tool (e.g. let a quick-processing
session reach its insight). When it fires, the agent forwards a `{"type":"tool_call","toolName":…,
"toolCallId":…,"parameters":{…}}` data message on the **`conversation`** topic.

`observe` and `session` **ack it automatically** (standing in for the app — see *Client-side tools
are acked automatically* under the voice section): they reply with the matching
`{"type":"tool_result","toolCallId":…,"result":…}` and, for a terminal tool (`createInsightCard` /
`endOnboarding`), **disconnect our participant** after the ack (mirroring mobile's `endSession()`).
Watch the run output for `[client-tool] acked …` then `disconnected (terminal tool …)`, and, on the
agent side, `Client tool acked` in the worker / `lk agent logs`. Pass `--no-ack-client-tools` to
leave forwards unanswered (e.g. to exercise the agent's ack-timeout fallback), or `--no-disconnect`
to ack + keep observing. Background + full contract: `docs/client-tools.md` in the LivekitVoiceAgent repo.

## Sourcing the token from the backend (opt-in)

By default the script mints the test user's room-join token locally from `LIVEKIT_API_KEY/SECRET`.
To instead exercise the **production path** — where the LiveKit secret stays server-side and the
backend mints the token via `POST /ai/livekit/conversation/token` — set:

```bash
export AURA_LIVEKIT_TOKEN_ENDPOINT=1      # opt in
export AURA_API_URL=https://services.dev.aurahealth.io   # or http://localhost:3015 / prod
# AURA_AUTH_TOKEN must already be set (the test user's Firebase token — see the identity gate)
```

The script then fetches the **test user's** token from the endpoint (minted for the
`AURA_AUTH_TOKEN` user); the silent observer keeps minting locally (it needs its own identity, and
the endpoint always mints for the auth-token user). Agent dispatch is unchanged — the script still
runs its own `lk dispatch`, so the endpoint is asked for the token only (`dispatchAgent: false`).
Requires the endpoint to be deployed/reachable at `AURA_API_URL`.

**The token is for the room the endpoint returns**, which may not be the `--room` you passed: with
`LIVEKIT_ROOM_CHECK=enforce` (IntelligenceService#748) the backend replaces a room not bound to the
caller with a new one. `session` then connects, dispatches and writes its transcript under that
room and prints `ROOM_CHANGED: … bound this session to <room>`. Attach an observer to that room,
not `$ROOM`. `token` prints the bound room on its `ROOM:` line.

## Monitoring (on by default)

Run alongside any mode; turn off only if the user asks.

- **Silent observer (transcript)** — voice/browser uses the `observe` process above; its
  `outputs/<room>.observe.md` updates live. Render new lines as they arrive. Speaker labels come
  from the transcription stream's `lk.transcribed_track_id` attribute mapped to its owning
  participant: the agent's own audio track (the participant carrying `lk.agent.*` attributes) →
  **Aurie**, any other audio track → the human (**You**). This is reliable even when the human's
  browser identity is opaque. Only if the SDK omits the track id does it fall back to the
  identity heuristic (`--speaker-identity`, default `qa-human`); unknown senders then show by
  their raw identity rather than being guessed. Interim (non-final) segments are marked. (Text
  mode captures the transcript directly; console's transcript is its on-screen output.)
- **Log tail** — surface warnings/errors as they happen:
  - local → `tail -f /tmp/lk-talk/worker.log` (the `tee`'d worker output).
  - remote → `lk agent logs` (streams the deployed agent).
  Flag `TTS_INIT_FAILED`, `failed to synthesize speech: no audio frames`, `LLM_REQUEST_FAILED`,
  `ROOM_CONNECT_FAILED`, fallback replies ("I'm having trouble connecting").

## Step 5: Readout

Lead with a clickable link to the transcript (`outputs/<room>.observe.md` for voice, `outputs/<room>.md`
for text). Then: mode/route/target, room, agent name, resolved provider/voice, user_id, turn or
call length, stop reason. Summarize monitoring: any error/fallback lines from the log. **Always
surface the transcript link**, even when summarizing.

## Troubleshooting

Symptom → cause → fix catalogue: [`references/troubleshooting.md`](references/troubleshooting.md).
Covers blank/partial replies and the TTS-sync trap, empty turns from a non-real `user_id`,
wrong-join-order symptoms, whether the LLM path fired at all, an agent that never joins, the two
`console` startup errors, the LiveKit Cloud Console rendering raw JSON transcripts, and a
Playground room the agent ignores.

## Notes

- Repo under test: `AuraHealth/LivekitVoiceAgent` — `src/entrypoint.py` + `src/sessions/session_manager.py`
  (`session.start` RoomOptions; `AGENT_METADATA` fallback), `docs/local-qa.md` (metadata schema +
  dev/console/dispatch flow). Rooms are `aurie-room-*`.
- Secrets (`LIVEKIT_API_SECRET`, JWTs) stay in env / files / in-memory — never in stdout, argv, or
  `outputs/`. The browser token is a 0600 file; share the path, not the value.
- Brand voice: `Products/Aurie` is in `knowledge.reads` so Claude can judge on-brand replies.
