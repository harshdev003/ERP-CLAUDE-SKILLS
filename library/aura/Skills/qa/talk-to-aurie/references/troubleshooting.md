# Troubleshooting

Symptom → cause → fix for a `talk-to-aurie` run. Companion to
[`SKILL.md`](../SKILL.md); consult it when a run misbehaves.

- **Empty reply / `replies/<n>.json` has `"partial": true` / observer transcript blank on Aurie's
  side** — the transcript is **synced to TTS audio** (`sync_transcription=True`), so **no audio ⇒
  no transcript**. Almost always the chosen TTS provider produced no audio. Check the worker log
  for `failed to synthesize speech: no audio frames` / a TTS `APIError`. Use a provider with a
  **working key AND a valid `voice_id`/`model`** (a valid key alone isn't enough). OpenAI TTS is a
  reliable fallback for QA. **Diagnose before connecting** by calling the provider API directly with
  the configured voice id — a stale id returns a clear error (Cartesia `POST /tts/bytes` → `404 Voice
  not found`; ElevenLabs `POST /v1/text-to-speech/{id}`). A scoped ElevenLabs key may `401` on
  account-read endpoints (`/v1/user/*`) yet still synthesize — test the TTS endpoint, not `/user`.
- **Text mode: greeting plays, every turn returns `{"text": "", "partial": true}`, session closes after
  ~120 s** — an `observe` joined before `session`
  ([Join order](../SKILL.md#join-order-the-talker-connects-first)).
  The guards turn this into a `FAIL`, so reaching this symptom means one was overridden — drop
  `--allow-late` / `--allow-first`, start `session` first, and attach the observer after.
- **Greeting plays but every user turn comes back empty** — almost always the `user_id` is **not a
  real Aura account**. The backend coach prompter returns no content for a non-existent user, so the
  turn generates nothing to speak (and with `sync_transcription`, no audio ⇒ no transcript). The static
  greeting still plays, which masks it. Fix: use a **real** `user_id`. (Confirmed via deploy QA — a
  fake id yields blank replies; a real id replies normally.)
- **Did the LLM path fire at all?** — the greeting (`first_message`) is a static `session.say()` and
  never hits the LLM; only a **user turn** calls the Aura LLM endpoint (`/ai/openai/responses` by
  default, `/ai/openai/chat/completions` when `llm_api_mode` is overridden)
  (generation flows through the session LLM — there's no `llm_node` override anymore). Hearing the
  greeting proves TTS works, not the LLM path.
- **Greeting never arrives / agent never joins** — confirm the worker registered under the same
  `--agent-name` you dispatch to (local → `local-aurie-voice-agent`), and that preflight's auth
  check passed (a stale key connects the *client* but the *worker* can't register).
- **console: `termios.error: (19, 'Operation not supported by device')`** — console needs a real
  interactive TTY; it can't run headless under the skill. Hand the command to the user's terminal.
- **console: `TTS provider not found in metadata`** — set `AGENT_METADATA` (and a matching
  `TTS_DEFAULT_PROVIDER`) before `python src/main.py console`.
- **LiveKit Cloud Console shows raw JSON (`{"text": " holding", "end_time": 4.34}`) instead of
  readable text** — expected, not a bug. The Console is a generic LiveKit UI with no Aurie decoder,
  so it renders the `lk.transcription` stream verbatim; `transcript_json_format=true` is the
  production contract that the mobile client and this skill's `observe` / `session` decode (see
  [The transcript payload](../SKILL.md#the-transcript-payload)). Do **not** "fix" it by setting
  `AGENT_TRANSCRIPT_JSON_FORMAT=false`: the `end_time` values **are** the word timings, so text
  format deletes the timing feature under test (every turn renders `[0 timed]`), and it is a
  deploy-level worker env var rather than a per-session metadata key, so it changes prod for
  everyone. For a readable view, use the observer transcript at `outputs/<room>.observe.md`.
- **Browser: agent doesn't answer in a Playground-generated room** — the hosted Playground's
  auto room name won't match the `aurie-room-*` auto-join glob, so dispatch explicitly
  (`observe --dispatch --agent-name …`) into the named `$ROOM` you minted the token for, and join
  *that* room.
