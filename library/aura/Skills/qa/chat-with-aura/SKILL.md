---
name: chat-with-aura
description: >
  Drives a simulated user conversation with Aura against the Aura AI API for QA testing, with
  `prompterName` hard-coded to `chat` (the variant of `chat-with-aurie` dedicated to the chat
  prompter family). Claude plays the user, composing each turn from a scenario you describe,
  while the skill's scripts handle the API plumbing — conversation start, one ask per turn,
  and a guaranteed end so server state stays clean even on error. Runs against prod by
  default, or any local/dev override; auth via `AURA_USER_TOKEN`, with JWT expiry pre-checked
  so a stale token fails fast instead of mid-loop. Trigger when the user says: "chat with
  Aura", "QA test Aura", "simulate a user conversation with Aura", "drive a chat with Aura",
  "test the Aura conversation API", "run a scripted Aura chat with the `chat` prompter", or
  "exercise /ai/dialogue/ask with the chat prompter".

metadata:
  version: "3.2.0"
knowledge:
  reads:
    - Guide/Index
    - Products/Aurie
    - Tools/Aura API
    - Tools/Aura API/Conversations/Start Conversation
    - Tools/Aura API/Conversations/Ask
    - Tools/Aura API/Conversations/End Conversation
    - Tools/Aura API/Users/Me
    - Tools/Aura API/Users/Verify Token
---

# chat-with-aura

QA test driver for Aura's `chat` prompter. Same flow as `chat-with-aurie` but with `prompterName` hard-coded to `chat` so the operator never has to pick. Claude plays the simulated user, and two small Python scripts handle the API plumbing and transcript bookkeeping. The value is Claude composing each user turn intelligently from a scenario — the rest is mechanical.

## Scripts

Both live in this skill's `scripts/` dir and are invoked via `python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/<name>.py …`.

- **`aura_chat.py`** — thin API client. Subcommands: `health`, `whoami` (server-authoritative via `GET /users/me`), `verify-token` (server-authoritative via `POST /users/verifyToken`), `start`, `ask`, `end`. Token-expiry guidance is "warn, never block" — the server is the only authority on token validity. Token never appears in stdout/argv. Output is one JSON line `{http_status, body}` per call (or, for whoami's success path, a focused subset of the user record).
- **`aura_transcript.py`** — per-conversation transcript bookkeeping. Subcommands: `init`, `append`, `finalize`, `render`, `show`. Writes the raw `outputs/<conversation_id>.json` and a human-readable `outputs/<conversation_id>.md` (rendered automatically on `finalize`, or on demand via `render`). Never records auth tokens or request headers.

## When to use

Trigger when the user says any of:

- "chat with Aura"
- "QA test Aura"
- "simulate a user conversation with Aura"
- "drive a chat with Aura for QA"
- "test the Aura conversation API"
- "exercise `/ai/dialogue/ask` with the chat prompter"
- "run a scripted Aura chat against \<env\>"

Use the sibling `qa:chat-with-aurie` skill instead when the operator needs to pick a different prompter (`companion`, `coach`, or `quickProcessing`) — this skill is the focused `chat`-prompter variant.

## Step 1: Collect inputs (in order)

Ask each prompt in chat. Don't proceed to Step 2 until all five are answered.

1. **Environment** — default `https://services.aurahealth.io`. User may paste an override (e.g. `http://localhost:3015`, `https://services.dev.aurahealth.io`). Strip any trailing slash. Will be set as `AURA_BASE_URL` for the scripts.
2. **`AURA_USER_TOKEN` and `AURA_API_TOKEN`** — both come from `~/.claude/settings.json` env block (AuraHealth team standard).
   - `AURA_USER_TOKEN` — per-user bearer for the test account. Used on `start`, `ask`, `end`, `whoami`, and as the token-being-verified by `verify-token`.
   - `AURA_API_TOKEN` — service-level bearer. Used as the auth header on `verify-token`. Same value is used by `data:sync-experiments` and `data:analyze-experiment` for `/experiments/*`.
   - The skill loads both into the shell via `python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/settings.json')))['env']['AURA_USER_TOKEN'])"` and exports them for the script — never echoed in chat or written to `outputs/`.
   - **Cowork**: workspace bash strips host env (PROD-1305). Ask the user to paste both tokens; export them once at the top of the workspace bash session. Same in-memory rules.
3. **Scenario / persona** — what kind of user is Claude playing? Examples:
   - "First-time user feeling overwhelmed at work"
   - "Returning user dealing with grief, second session this week"
   - "Skeptical user testing whether Aura can hold boundaries"
   - "Memory feature test (drop concrete personal/work facts to verify what gets extracted)"

   ⚠ The conversation runs under whatever `AURA_USER_TOKEN` is in env, and any memory the IntelligenceService extracts attaches to that user's real Aura account. If the token is your personal account, **don't pick a fictional persona** unless you genuinely want fake facts in your own memory store. The Step 2.5 identity-confirmation gate enforces this — you'll see whose account this run lands on before any calls go out.
4. **Stop condition** — accept either:
   - A turn count (e.g. `8 turns`), or
   - A natural-language trigger (e.g. *"stop when Aura suggests a meditation"*).
   Apply a hard cap of **30 turns** regardless of the trigger.
5. **`createInsightCard` on `/end`** — opt-in flag. Default `false`. Set `true` if the user wants the post-conversation insight pass (populates the `/end` response's `insight: { affirmation }` field — `summary`/`followup`/`tone`/`nextFirstMessage` may stay `null` regardless, that's not a bug; `trackId` is no longer returned here).

The prompter is fixed to `chat` — don't ask for it.

## Step 2: Pre-flight

Run three quick checks before any conversation calls. Fail fast on any of them.

```bash
export AURA_BASE_URL="<env from Step 1>"
export AURA_USER_TOKEN="$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/settings.json')))['env']['AURA_USER_TOKEN'])")"
export AURA_API_TOKEN="$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/settings.json')))['env']['AURA_API_TOKEN'])")"

python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/aura_chat.py health
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/aura_chat.py whoami
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/aura_chat.py verify-token
```

- **`health`** — unauthenticated GET against `$AURA_BASE_URL/`. Confirms the server is reachable. If this returns non-2xx or a connection error, stop and surface the error to the user.
- **`whoami`** — server-authoritative via `GET /users/me`. Returns a focused subset of the user record (`id`, `givenName`, `familyName`, `premium`, `deviceOS`, `version`, `countryCode`, `timezone`, `createdAt`, `loggedInAt`, `isCoach`, `persona`, `total_fields_in_response`). Echo `id` + `givenName`/`familyName` back to the user so they can confirm they're impersonating the right account. If `/users/me` returns 4xx/5xx, the script falls back to a local JWT decode with a clear "NOT server-authoritative" warning — that's the cue to refresh the token.
- **`verify-token`** — server-authoritative via `POST /users/verifyToken`. Sends `AURA_USER_TOKEN` in the body, signed with `AURA_API_TOKEN` as the service bearer. Always HTTP 200 — verdict is in the body: `{verified: true, tokenUserId, id}` or `{verified: false}`. **If `verified: false`, stop and ask the user to refresh the token** (this is the authoritative source — JWT-decode lies for users with server-side persisted tokens).

## Step 2.5: Identity-confirmation gate (mandatory)

After `whoami` succeeds and **before generating the conversationId**, surface a clear warning in chat that names the owner of the token and gets explicit confirmation before any conversation calls land on their account.

The warning isn't optional. A prior run of the sibling skill drove a 15-turn fictional persona ("Mira, illustrator in Oakland") against the wrong user's `AURA_USER_TOKEN` — every memory extraction, summary, and insight card from that run attached to that user's real Aura account, polluting their actual memory store with fake facts. The fix is procedural: stop and confirm.

Print this banner verbatim, substituting the values from the `whoami` response:

```
⚠ Heads-up: this conversation will execute under <givenName> <familyName> (id: <id>) on <BASE_URL>.

  Every /ask call, every memory extraction the IntelligenceService runs, and any
  insight card from /end will attach to *this user's real Aura account*. If you're
  about to play a fictional persona ("Mira the illustrator", "Devon the ER nurse",
  etc.), those facts will land in <givenName>'s memory store as if they were true.

  Use a fictional persona only if:
    - You're hitting a dedicated test user (not your own account), OR
    - You're knowingly testing the memory feature and accept the pollution

  Otherwise, drive the conversation as <givenName> talking about <givenName>'s
  actual life — that's the safe default.

  Type "yes" to continue, anything else to abort.
```

If the user replies anything other than `yes` (case-insensitive), abort cleanly: do not generate a `conversationId`, do not call `/start`, do not call `/end` (no server state to clean up). Print a one-line "Aborted, no conversation calls were made" and stop.

If `whoami` fell back to JWT decode (e.g. on `localhost:3015` where `/users/me` 404s), use the JWT-decoded `name` and `user_id` instead. Add a one-line note: `(identity from JWT decode — server didn't answer; double-check this is the right account)`.

## Step 3: Generate `conversationId`

```bash
CONVERSATION_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
echo "conversationId: $CONVERSATION_ID"
```

Echo it prominently — it's the trace key for this entire run and the transcript filename.

## Step 4: Compose first message + `/start`

Compose a realistic opening for the persona. Keep it natural, first-person, present-tense. Don't roleplay aggressively or test edge cases unless the scenario specifically calls for it — most QA value comes from realistic interactions.

```bash
mkdir -p /tmp/aura-chat
cat > /tmp/aura-chat/turn0.txt <<EOF
<the opening message>
EOF

python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/aura_chat.py \
  start "$CONVERSATION_ID" --message-file /tmp/aura-chat/turn0.txt \
  > /tmp/aura-chat/start.json

python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/aura_transcript.py \
  init "$CONVERSATION_ID" \
  --base-url "$AURA_BASE_URL" \
  --scenario "<one-line scenario>" \
  --prompter chat \
  --create-insight-card "<true|false>" \
  --first-message-file /tmp/aura-chat/turn0.txt \
  --start-response /tmp/aura-chat/start.json
```

If `start` exits non-zero (HTTP >= 400), surface the body and stop. Do **not** proceed to `/ask`. Still attempt `/end` for cleanup — partial server state may exist.

## Step 5: Conversation loop

Repeat until the stop condition is met (or 30 turns elapsed):

### 5a. Compose the next user message

Based on the scenario / persona, Aura's last reply, and the dialogue so far. Stay in character. If Aura asks a question, answer it the way that persona would. If Aura offers an exercise, respond honestly — accept, refuse, or push back as fits the persona. Don't artificially extend the conversation; if the persona would naturally wind down, do.

### 5b. POST `/ask`

```bash
N=<turn number, 1-indexed>
cat > /tmp/aura-chat/turn${N}.txt <<EOF
<the user message>
EOF

python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/aura_chat.py \
  ask "$CONVERSATION_ID" \
  --message-file /tmp/aura-chat/turn${N}.txt \
  --prompter chat \
  > /tmp/aura-chat/turn${N}.json
```

### 5c. Render and append

Print the exchange in chat:

```
### Turn N
> **User:** <user message>
>
> **Aura:** <aura reply, extracted from response.body.message>
```

Then append to the transcript:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/aura_transcript.py \
  append "$CONVERSATION_ID" --turn $N \
  --user-message-file /tmp/aura-chat/turn${N}.txt \
  --ask-response /tmp/aura-chat/turn${N}.json
```

### 5d. Check stop condition

- **Defensive:** if Aura's `/ask` response includes a non-empty `toolCalls` array, stop. The `chat` prompter has not been observed to emit `toolCalls` on close — even when Aura verbally says "I'm ending the conversation", the response field stays `null` — so in practice expect to stop via one of the bullets below. Keep the check anyway in case server behaviour evolves.
- If `N >= turn cap`, stop.
- If a natural-language trigger was given, evaluate it against Aura's latest reply (your judgement call, not a regex). When it fires, stop.
- If Aura's reply or the persona's natural arc has clearly ended the conversation, stop early and note why in the readout.

## Step 6: POST `/end` (happy path) or let it auto-end (error / interrupt)

**Happy path** — the loop ended cleanly (persona's arc closed naturally, turn cap reached without error, or natural-language trigger fired): **call `/end`**. Pass `--insight` if Step 1.5 opted in.

**Error or interrupt path** — `/ask` returned 4xx/5xx mid-loop, or the operator stopped the run: **letting the conversation auto-end is acceptable**; a manual `/end` is best-effort. The server tracks `endedAt` server-side, and there's no transcript value to chase from a half-broken session. Surface the failure in the readout instead.

```bash
# Default close — no insight pass
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/aura_chat.py \
  end "$CONVERSATION_ID" > /tmp/aura-chat/end.json

# Opt-in: trigger the insight pass at close
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/aura_chat.py \
  end "$CONVERSATION_ID" --insight > /tmp/aura-chat/end.json
```

When `--insight` is passed, the response body's `insight` field is populated as `{ affirmation }`. `trackId` is no longer returned on `/end --insight` for the `chat` prompter. The legacy fields `summary` / `followup` / `tone` / `nextFirstMessage` may all stay `null` even with `--insight=true` — that's expected, not a bug.

Then finalize the transcript:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/aura_transcript.py \
  finalize "$CONVERSATION_ID" \
  --end-response /tmp/aura-chat/end.json \
  --stop-reason "<one-line reason>"
```

## Step 7: Readout

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chat-with-aura/scripts/aura_transcript.py \
  show "$CONVERSATION_ID"
```

`show` prints the metadata, insight fields, **and the full turn-by-turn dialogue**
to stdout. `finalize` (Step 6) already wrote the human-readable transcript at
`outputs/<conversationId>.md`; if you need to (re)generate it on its own, run
`aura_transcript.py render "$CONVERSATION_ID"` (prints `TRANSCRIPT_MD: <path>`).

Then print a chat readout that includes:

- **📄 Full transcript:** a clickable markdown link to `outputs/<conversationId>.md` — the human-readable conversation. List the raw-data `outputs/<conversationId>.json` second.
- environment, conversationId, userId, prompter (`chat`), turn count, stop reason
- **Insight:**
  - With `--insight=true` → from the `/end` response body's `insight: { affirmation }`. Surface `affirmation` only. Don't expect `summary` / `followup` / `tone` / `nextFirstMessage` — those typically stay `null` regardless and are not the right fields to surface.
  - Without `--insight` → no insight to surface; note "(no insight requested)".

**Standing rule — always surface the transcript.** The readout must always lead
with the clickable `outputs/<conversationId>.md` link so the reviewer can read the
whole conversation. Never deliver only the insight, a summary, or scores: the
transcript link must always be present, even when you also summarize.

## Endpoint reference (canonical: `Tools/Aura API/Conversations/*` in AI Knowledge)

| Endpoint | Method | Path | Body |
|---|---|---|---|
| Start | POST | `/ai/conversations/:conversationId/start` | `{ firstMessage }` |
| Ask | POST | `/ai/dialogue/ask` | `{ conversationId, message, polling, stream, voice, prompterName: "chat" }` |
| End | POST | `/ai/conversations/:conversationId/end` | `{}` or `{ createInsightCard: true }` |

All three: `Authorization: Bearer ${AURA_USER_TOKEN}`, `Content-Type: application/json`. `conversationId` is a client-generated UUID v4 in the path for `/start` and `/end`, and in the body for `/ask`.

The `/ask` response includes both `message` (plain text) and `markup` (same content, markdown-formatted) — extract `message` for plain rendering. Other fields: `id`, `role: "assistant"`, `createdAt`, `updatedAt`, `userId`, `conversationId`, `suggestions`, `toolCalls`, `payload` (mostly null today).

## Known quirks

1. **Firebase ID tokens have a 1h `exp` claim, but some users live longer.** `AURA_USER_TOKEN` in settings is typically a Firebase ID token via Google sign-in (`iss: securetoken.google.com/firebase-auratech16`, 1-hour `exp`). For most users that means a fresh token is needed every hour. **However**, certain Aura test/staff users have their JWT persisted server-side and the API accepts it past `exp` — JWT-decode lies for these users. The skill's pre-flight uses **server-authoritative checks** (`whoami` → `GET /users/me`, `verify-token` → `POST /users/verifyToken`) to dodge this entirely. Don't decode JWTs yourself.
2. **`createInsightCard: false` returns a sparse `/end` body.** No `summary`, `followup`, `title`, `tone`, etc. — those fields are only populated by the insight pass (asynchronously). Default is `false`; opt in if you want the readout.
3. **Memory prompt fires every 5 user turns + on end** (per [ENG-433](https://linear.app/aura-health/issue/ENG-433)). Plan turn counts accordingly when QA-ing memory: 5 user turns is the minimum to see one mid-conversation extraction.

## Error handling

- **Non-2xx from any call** — `aura_chat.py` exits code 2 and prints `{http_status, body}` to stdout. Surface that to the user.
- **`/start` fails** — don't `/ask`. Still `/end` for cleanup.
- **`/ask` fails mid-loop** — stop the loop. Still `/end`. Finalize the transcript with the partial turns log.
- **`/end` fails** — surface and continue to readout. Don't retry indefinitely. The transcript records the failure under `endResponse`.
- **`verify-token` returns `verified: false`** — token isn't valid (expired without persistence, malformed, or unknown). Stop, ask the user to refresh `AURA_USER_TOKEN`, and re-run pre-flight before continuing.
- **Network timeout / connection refused** — `aura_chat.py` exits code 1 with a connection error. For local dev, suggest checking `services.aurahealth.io` is running on the expected port.
- **Hard cap reached without trigger** — note in `stoppedBecause` and proceed to `/end` normally.

## What this skill does NOT do

- **Does not let you pick a different `prompterName`.** This skill is the `chat` variant by design. Use `qa:chat-with-aurie` if you need `companion`, `coach`, or `quickProcessing`.
- **Does not call the polling or streaming variants of `/ask`.** Out of scope for v1.
- **Does not test `/ai/dialogue/prompt-debugging`.** Separate skill if/when needed.
- **Does not write to Notion or any other write surface.** Pure API client; transcripts only land in `outputs/`.
- **Does not query the memory store post-run.** Verifying what Aura's memory extracted from the conversation is a follow-up — see PROD-1485's "out of scope" notes.
- **Does not refresh the token automatically.** Firebase ID tokens need a refresh token to be minted, which we don't store today. Easier path: use a long-lived dev token if Aura issues one.

## Notes

- **Brand voice** — `Products/Aurie` is in `knowledge.reads` so Claude has the canonical Aura persona on hand. Use it to judge whether Aura's replies are on-brand when summarizing the QA result.
- **Cowork caveat** — `mcp__workspace__bash` strips host env ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)), so `AURA_USER_TOKEN` and `AURA_BASE_URL` won't survive into a Cowork sandbox automatically. The paste-and-export-once-per-session pattern handles this; document it in the skill output if a run is happening inside Cowork.
- **Repo convention**: scripts live inside this skill folder and are invoked via `${CLAUDE_PLUGIN_ROOT}` (per AINative architecture principle 1 — skills are self-contained). Don't reach into other skills' `scripts/`.
