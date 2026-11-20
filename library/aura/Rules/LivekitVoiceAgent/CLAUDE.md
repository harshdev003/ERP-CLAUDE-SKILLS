# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security-review.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security-review.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every other code repo. Change that page first, then every copy.

## Rules

Repo rules are organized by topic in `.claude/rules/` (mirrored to `.cursor/rules/*.mdc` for Cursor). Keep this list and both folders in sync whenever a rule is added, renamed, or removed.

@.claude/rules/project-rules.md
@.claude/rules/architecture.md
@.claude/rules/coding.md
@.claude/rules/config.md
@.claude/rules/deployment.md
@.claude/rules/documentation.md
@.claude/rules/error-handling.md
@.claude/rules/logging.md
@.claude/rules/naming.md
@.claude/rules/performance.md
@.claude/rules/security.md
@.claude/rules/testing.md

## Project Overview

LiveKit Voice Agent — a production-ready voice AI agent built on LiveKit with multi-provider TTS, Deepgram STT, OpenAI/Aura LLM, and real-time turn detection. Integrates with the Aura Health backend for LLM calls and metrics reporting.

## Common Commands

```bash
# Setup
make setup          # Full dev setup (venv + install + download models)
make install-dev    # Install deps + pre-commit hooks
make download       # Pre-download ML models (Silero VAD)

# Run
make dev            # Development mode (debug logging)
make start          # Production mode

# Test
make test           # All tests
make test-unit      # Unit tests only
make test-behavioral # Behavioral tests (drive AgentSession.run())
make test-cov       # With HTML coverage report

# Lint / Format
make lint           # Check only
make lint-fix       # Auto-fix with black + isort
make format-check   # Read-only black + isort check (the CI gate)

# Docker
make docker-build
make docker-run
```

**Run a single test file:**
```bash
venv/bin/pytest test/unit/test_foo.py -v
```

## Architecture

### Entry Points

- **`src/main.py`** — CLI wrapper (the `livekit.toml` entrypoint); loads `.env`, validates configuration, starts the LiveKit worker with `prewarm_fnc` (loads the Silero VAD model and runs `src/warmup.py`, which pays the first-turn import and pydantic schema-build costs before a job is assigned) and `entrypoint_fnc`.
- **`src/entrypoint.py`** — Async per-session setup (thin orchestrator): builds the per-session `Context`, resolves config, creates LLM/TTS/STT, builds the agent (`agents/`) + session (`sessions/`), wires metrics, then starts and greets. The LiveKit `JobContext` is passed explicitly to the collaborators that need it.
- **`src/agents/aurie.py`** — `Aurie` subclasses LiveKit `Agent` (built by `agents/manager.py`'s `AgentManager`). Generation runs through the session LLM (`BaseAgent.llm_node` is a pass-through that only reports when a generation completes, for `agent_reply_saved`); per-turn prompt parameters (`userId`/`conversationId`/`locale`/`platform`/`incognito`/`prompterName`) are embedded in the system prompt under the `** Prompt Parameters:**` marker.

### Configuration Resolution (Priority Order)

1. Job metadata (from LiveKit server dispatch)
2. Participant identity — only `user_id` falls back to it. The participant's own metadata and attributes are **not** read for config: a TTS key a client still sends there is logged by name (`Participant TTS override ignored`) and ignored. All other config (conversation_id, prompter_name, locale, incognito, platform, first_message, and the logged-only app_version / os_version / audio_session_mode / audio_route) comes from job metadata only.
3. Environment variables (`.env`)

Parsed by `src/utils/room_parser.py`. Config classes are typed dataclasses in the top-level `src/config/` package (one class per file; `app.py` holds the composite `Config` + `get_config`).

### Key Services

| File | Role |
|------|------|
| `src/tts/` | OO TTS package: `TTSProvider` base, one provider per file (ElevenLabs/OpenAI/Cartesia/Inworld/Fish Audio), `TTSManager` factory; `create_tts_provider()` is the call seam |
| `src/stt/` | OO STT package mirroring TTS: `STTProvider` base, `DeepgramInferenceSTTProvider` (Deepgram models through LiveKit Inference, with an optional server-side `fallback`), `STTManager`; `create_stt_provider()` is the call seam. The direct plugin transport was removed in ENG-1263, so `KNOWN_STT_PROVIDERS` has one entry; an unrecognized `STT_PROVIDER` is still fatal at startup rather than a session with no speech recognition. **Do not rename the key to `deepgram`** — it is logged per turn as `stt_provider`, and the Aurie cost store classifies the day's STT transport by matching `inference` in it |
| `src/agents/` | `BaseAgent` (`base.py`, shared base) + the `Aurie` agent (`aurie.py`) + `AgentManager` (`manager.py`, the `create_agent()` registry seam); embeds the per-turn `** Prompt Parameters:**` block. Generation flows through the session LLM (`BaseAgent.llm_node` is a pass-through that only reports when a generation completes, for `agent_reply_saved`) |
| `src/llm/` | Session-LLM factory: `LLMManager` / `create_llm()` (today always Aura's OpenAI-compatible endpoint; the seam for a future multi-provider LLM) + the `PromptParameters` dataclass |
| `src/sessions/` | All per-session concerns: `SessionConfig`, `SessionResolver` (connect + resolve config), `SessionManager` (build/wire/start the `AgentSession` + greeting), plus its collaborators — `SttStallRecovery` (stall classification + STT stream recovery), `TtsStallRecovery` (re-speaks, as written, a reply a silent TTS retry left without audio; #264), `StuckSpeechWatchdog` (interrupts a reply that stays "speaking" with nothing playing, `AGENT_SPEECH_STUCK`), `TurnDetectorRecovery` (re-probes the cloud turn detector after a degrade; #301), `register_observability_events()` (monitoring-only handlers), `session_options` (the two `session.start` option resolvers), `messages` and `ReplySavedNotifier` (when to publish `agent_reply_saved`) |
| `src/services/intelligence.py` | Aura API client — `send_conversation_metrics()` for session-end conversation metrics (the LiveKit counterpart of the ElevenLabs post-call webhook). The LLM does not route through this client; it uses the OpenAI-compatible endpoint via the stock plugin |
| `src/metrics/` | `SessionMetrics` — accumulates per-session LiveKit usage (agent-turn count + STT/TTS audio) and POSTs the conversation-level totals to Aura on shutdown. No per-turn dialogue metrics (the OpenAI endpoint owns dialogue ids) |

### Data Flow

```
User audio → Deepgram STT → session LLM → [Aura /ai/openai/chat/completions] → TTS provider → LiveKit room
                                                         ↓
                                  SessionMetrics (LiveKit usage totals, accumulated per turn)
                                                         ↓
              on session end → POST Aura /ai/conversations/{id}/metrics (duration, agentTurns,
              stt/tts audio) — the LiveKit counterpart of the ElevenLabs post-call webhook.
```

### Multi-Provider TTS

`create_tts_provider(provider, voice_id, model, tts_settings)` in `src/tts/` (delegating to `TTSManager`) returns the appropriate LiveKit TTS plugin. Provider is selected at session start from metadata or `TTS_DEFAULT_PROVIDER` env var. Each provider is a `TTSProvider` subclass in its own file; every provider builds on an official `livekit-plugins-*` plugin (Inworld included since ENG-1304 retired the vendored HTTP plugin). Setting `TTS_FALLBACK_PROVIDER` to a different provider wraps the session TTS in a LiveKit `FallbackAdapter`, so a primary-provider outage auto-fails-over to the backup (best-effort: a missing backup never blocks the primary). It is **disabled by default** — failover swaps the user to a different voice mid-session, and an empty value can't survive the secrets sync, so opting in must be explicit.

`tts_settings` is an optional dict of per-session tuning params extracted from job metadata by `extract_tts_settings` (`src/tts/settings_keys.py`). Supported keys:

| Key | ElevenLabs | OpenAI | Cartesia | Inworld | Fish |
|-----|-----------|--------|---------|---------|------|
| `speed` | VoiceSettings (0.8–1.2) | float | float | alias for `speaking_rate` | float |
| `stability` | float 0–1 | — | — | — | — |
| `similarity_boost` | float 0–1 | — | — | — | — |
| `style` | float 0–1 (expressiveness) | — | — | — | — |
| `use_speaker_boost` | bool | — | — | — | — |
| `language` | str (BCP-47) | — | str (default `"en"`) | str | — |
| `instructions` | — | str style prompt | — | — | — |
| `emotion` | — | — | list or comma-string e.g. `"positivity:high,curiosity"` | — | — |
| `volume` | — | — | float | — | float (dB) |
| `speaking_rate` | — | — | — | float | — |
| `temperature` | — | — | — | float (1.x/1.5) | float 0–1 |
| `top_p` | — | — | — | — | float 0–1 |
| `latency_mode` | — | — | — | — | `normal`/`balanced`/`low` |

**`playback_rate`** (job metadata, float 0.8–1.25) is not a provider setting: `BaseAgent.tts_node` stretches the synthesized audio to that pace with its pitch kept (`src/tts/playback_rate.py`, WSOLA in `src/tts/time_stretcher.py`) and scales the word timings to match. It is for voices whose provider ignores `speed` — Cartesia Pro Voice Clones, such as the Aurie V7 voices. Absent, empty or `1.0` leaves the default TTS node untouched; an invalid value is logged and ignored; a failure mid-reply logs `TTS_PLAYBACK_RATE_FAILED` and plays the rest of the reply at the normal pace.

**An absent key and an empty one mean different things**, and a client needs both. Absent = not configured, so the provider falls back to the deployed default (`CARTESIA_EMOTION`, `ELEVENLABS_STABILITY`, …). Present but empty = *explicitly nothing*, which `extract_tts_settings` (`src/tts/settings_keys.py`) normalises to `None` so the provider skips that default and the vendor's own applies — there is no sentinel value. Empty is the only way a client can say it: LiveKit deletes a participant attribute set to `""` and reports the delete as `""`, and both metadata channels are string-valued maps, so a JSON `null` never reaches the agent. `0`, `0.0` and `False` are values, not blanks. Tuning is fixed for the session once resolved: there is no mid-session TTS swap (the participant-attribute hot-swap was removed in ENG-1688; the TTS choice comes from job metadata only).

### Interruption / Turn Detection

- Silero VAD is pre-warmed before jobs start, and so are the first-turn import/schema costs (`src/warmup.py`, reported on the `Session starting` row as `warmup_*`)
- False interruptions are recovered via `session.generate_reply()`
- LiveKit's audio turn detector (`inference.TurnDetector`, livekit-agents ≥1.6.7) handles end-of-utterance detection — the full `v1` model on LiveKit Cloud by default (free for Cloud-deployed agents), local-CPU `v1-mini` for `start`/console and as the fallback after a mid-session cloud failure (logged as `EOT_DETECTOR_DEGRADED`). Replaces the deprecated transcript-based `MultilingualModel`.
- The SDK's fallback is sticky, so `TurnDetectorRecovery` (`src/sessions/turn_detector_recovery.py`) re-probes the cloud model at most 3 times per session, each time on the next transition into agent speaking that comes at least 30s after the degrade was first noticed (not on a timer) (`AGENT_TURN_DETECTOR_REPROBE_ENABLED`, default on; #301).
- **A fallback event logged as a session closes is usually teardown noise, not a failure.** The SDK's async close keeps the audio pipeline running for about a second after `Ending session`, so a degrade in that window is stamped `eot_degraded_during_teardown` instead of counted (#310: about 80% of `EOT_DETECTOR_DEGRADED` rows were these). Check an event's timing against the close before counting it.
- The detector's verdict picks the endpointing bound: probability ≥ the language's `unlikely_threshold` commits at `AGENT_MIN_ENDPOINTING_DELAY` (0.3s), below it waits `AGENT_MAX_ENDPOINTING_DELAY` (2.5s). A prediction that never arrives commits at the **min** — so a long turn means the model said "not finished", not that it timed out. `AGENT_EOT_DIAGNOSTICS_ENABLED` (see `src/log/eot_logging.py`) logs the per-turn probability/threshold so the two are distinguishable; don't retune the delays without it (PROD-4009).
- Both bounds sit on top of Silero VAD's trailing-silence window (`AGENT_VAD_MIN_SILENCE_DURATION`, 0.25s — the SDK's hard floor with an audio turn detector): the delay is measured from the true end of speech but a turn can't commit until VAD fires, so `min` is inert below that window. The plugin's own 0.55s default pinned every fast turn at ~578ms (PROD-4030).
- BVC noise cancellation enabled by default

## Key Environment Variables

Copy `.env.sample` to `.env`. Required vars:

- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `AURA_API_URL`, `AURA_API_KEY` — the LLM **always** routes through Aura's OpenAI-compatible
  `/ai/openai/chat/completions` endpoint, so these are required.
- TTS provider key matching `TTS_DEFAULT_PROVIDER` (e.g. `ELEVENLABS_API_KEY`)

`OPENAI_API_KEY` is only needed when OpenAI is the active TTS provider — not for the LLM. The
per-turn user is taken from the dispatch metadata (`user_id`), carried in the system prompt; it is
**required** — a session with no resolvable `user_id` (metadata → participant identity) fails fast
with `USER_ID_MISSING`. There is no `AURA_USER_ID` default and no `USE_AURA_LLM` flag.

## Tech Notes

- **Python 3.12+** required; async/await throughout.
- Line length: **120** characters (black + pylint configured).
- `asyncio_mode = auto` in pytest — no `@pytest.mark.asyncio` needed.
- `livekit.toml` configures the agent for LiveKit Cloud deployment (room glob `aurie-room-*`; the legacy `poc-room-*` rooms are served by the separate, soon-retired `lk-voice-agent`). The `entrypoint` is `src/main.py`.
- `package.json` npm scripts mirror Makefile targets (used by CI/release tooling).
