---
description: Security and privacy
globs:
alwaysApply: true
---

# Security & privacy

## End-user PII

Never log full end-user identifiers. Use the redaction helpers in **`src/utils/redact.py`**:

- `preview(text, limit)` — elide free-text content (e.g. a user's first message → length only).
- `redact_metadata(meta)` — keep ids/config; redact PII (UID → prefix only, email → domain only).
  Never log a full email, given name, or family name.
- `redact_error_detail(detail)` — for a vendor error string logged at WARNING/ERROR: keeps the
  diagnosis (`status_code=1001`, `Request timed out.`) and elides only spoken content, then caps
  the result. Use it for any `"detail": str(exc)` field, not just the ones known to carry text.
- `redact_spoken_text(text)` — the elision half, uncapped and idempotent; used by the log filter.

Job metadata is logged via `redact_metadata` — keep it that way. Participant TTS keys are logged by name only, never their values.

**The runtime leaks the same content independently.** A TTS zero-audio failure raises
`APIError(f"no audio frames were pushed for text: {text}")` — the agent's own reply, or the greeting
carrying the user's given name — and `livekit-agents` logs it itself as `"failed to synthesize
speech: %s"`. Redacting only our call site closes half the leak, so **`src/log/redact_logging.py`**
(`SpokenTextRedactionFilter`, wired unconditionally from `setup_logging`) screens records we don't
own. Unlike the EOT screen it is unscoped and rewrites `record.args` — the runtime's lazy `%s`
formatting keeps the content out of `record.msg` until the formatter runs. It also screens
**tracebacks**: the runtime's `@log_exceptions` wrapper logs `Error in _tts_inference_task` with the
`APIError` on `exc_info`, so the greeting (user's name included) sat only in the rendered traceback
and reached Cloud Logging verbatim. A traceback carrying spoken text is rendered, redacted into
`exc_text`, and `exc_info` cleared, because the runtime's stdout JSON formatter reads `exc_info` first. Add a new marker there
rather than accepting a new leak.

## Secrets

- **No secrets in code, logs, docs, commits, or AI Knowledge pages.** Reference them by env-var
  name only. `.env` is gitignored; commit only `.env.sample` with placeholder values.
- Deployed secrets live in LiveKit Cloud (manage via `lk agent secrets` / `update-secrets`),
  not in the repo. Rotate anything that leaks; never reuse a compromised key.
- Webhook/signature verification uses official SDKs (e.g. livekit-server-sdk), not hand-rolled crypto.

## Safety

Generation runs server-side on the Aura backend (the OpenAI-compatible endpoint + coach prompter),
so the crisis-response for self-harm/violence ideation and the prompt-injection guard live in that
**backend prompter**, not in the agent. `Aurie`'s system prompt is only the `** Prompt Parameters:**`
JSON block. Don't reintroduce generation/safety prompting in the agent, and don't weaken
the backend prompter's guardrails without product sign-off.
