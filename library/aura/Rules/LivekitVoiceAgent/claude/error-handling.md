---
description: Error handling and the error catalog
globs:
alwaysApply: true
---

# Error handling

## Per-session Context

`src/context.py` provides a `Context` (stable `transaction_id` uuid + room/user/conversation
ids), held in a `contextvars.ContextVar`. The entrypoint creates one per session
(`Context(room=...).bind()`), enriches it as ids resolve, and publishes `Context.meta()` to
`ctx.log_context_fields` — LiveKit's own log filter then stamps those ids onto every record (so we
keep no custom log filter).

## Error catalog

`src/errors.py` is the canonical catalog (`AgentErrors` → `ErrorSpec(code, message)`), with an
`ERRORS` code→spec lookup. Known failure modes (`TTS_INIT_FAILED`, `STT_INIT_FAILED`,
`LLM_REQUEST_FAILED`, `ROOM_CONNECT_FAILED`, `DISPATCH_FAILED`, `METADATA_PARSE_FAILED`,
`CONFIG_MISSING`, `USER_ID_MISSING`, `SESSION_ERROR`) **must** reference the catalog:

```python
logger.error(AgentErrors.TTS_INIT_FAILED.message,
             extra={"error_code": AgentErrors.TTS_INIT_FAILED.code, "detail": str(exc)})
```

Add a new failure mode to the catalog rather than inventing ad-hoc strings.

A failure the **runtime** reports and we only annotate gets a code too, stamped from a log filter
rather than a call site (`EOT_DETECTOR_DEGRADED`, `LLM_RESPONSE_FILTERED`, `STT_STREAM_CLOSED`, `TTS_STREAM_CLOSED` — see the `logging` rule). Where
that failure produces more than one record, stamp each of them with the same code and let the
session's `transaction` id join them, rather than holding state in the filter to merge them into one
row: with preemptive generation enabled, more than one turn can be in flight, so a merged row can be
confidently wrong about which turn it describes. It is off by default today, but the filter must stay
correct for the session that turns it back on.

## Patterns

- **Degrade gracefully in the audio path.** Generation runs through the stock LLM plugin
  (`BaseAgent.llm_node` only passes its chunks through and reports completion — it never catches the
  plugin's errors, and a failure in its own reporting is logged, not raised), so per-turn failures
  surface as the session's `error` event. `SessionManager`
  subscribes to it (`session.on("error")`): it logs with `SESSION_ERROR` and, for an *unrecoverable*
  non-TTS error, speaks a user-facing fallback (one of `ERROR_FALLBACK_MESSAGES`, e.g. "I'm having trouble connecting…") so the turn fails
  audibly instead of silently. A provider content-filter refusal is the exception: nothing is broken,
  so it logs `LLM_RESPONSE_FILTERED` at WARNING and speaks its own line instead of the connection one. A
  generation that **closes normally with no text and no tool call** never raises an `error` event at
  all (the backend can end a failed turn's stream that way), so `BaseAgent.llm_node` hands it to
  `SessionManager.handle_empty_llm_reply`, which logs `LLM_EMPTY_RESPONSE` at ERROR and speaks the same
  connection fallback. An empty answer to a tool result is exempt: it is the model's follow-up to a
  client tool, not a lost turn. Recoverable errors are left to the runtime's own retry. TTS-provider
  failures can't be voiced through a broken TTS — the **TTS `FallbackAdapter`** (auto-failover to a
  backup provider) covers that path instead.
- A broad `except Exception` is acceptable at well-defined boundaries (provider init, room
  connect, per-turn generation) **when it logs with an error code and re-raises or falls back** —
  never silently swallow.
- Required-config problems fail fast at startup (see the `config` rule), not mid-session.
