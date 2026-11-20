---
description: Naming conventions
globs:
alwaysApply: true
---

# Naming

- **Modules/files**: `snake_case.py`. One config dataclass per file in `src/config/`
  (`livekit.py` → `LiveKitConfig`, etc.). Cross-cutting modules are top-level and singular:
  `logger.py`, `context.py`, `errors.py`.
- **Env vars**: `UPPER_SNAKE_CASE`, prefixed by area — `LIVEKIT_*`, `DEEPGRAM_*`, `ELEVENLABS_*`,
  `CARTESIA_*`, `INWORLD_*`, `OPENAI_*`, `AURA_*`, `AGENT_*`, `LOGGER_*`, `TTS_*`, `STT_*`, `LLM_*`.
  The ElevenLabs key is **`ELEVENLABS_API_KEY`** (consistent with `ELEVENLABS_VOICE_ID`/`_MODEL`),
  not the legacy `ELEVEN_API_KEY`.
- **Error codes**: `UPPER_SNAKE_CASE` in `src/errors.py` (`TTS_INIT_FAILED`, …).
- **Log field keys**: `camelCase` for ids in `Context.meta()` (`transaction` + `transactionSlice`,
  `userId`, `conversationId`) — published to LiveKit's `log_context_fields` and emitted in its
  structured (prod JSON) logs.

## Room names

Rooms are `aurie-room-{...}`. This agent (`aurie-voice-agent`) serves **`aurie-room-*`** only
(`livekit.toml` join glob). Legacy `poc-room-*` rooms are handled by the separate, soon-retired
`lk-voice-agent`. Room parsing keys off the `-tts-`/`-voice-` segments, never the prefix.
