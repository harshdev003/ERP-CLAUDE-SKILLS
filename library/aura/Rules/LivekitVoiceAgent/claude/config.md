---
description: Configuration and settings
globs:
alwaysApply: true
---

# Configuration

## Where it lives

Typed dataclasses in the top-level **`src/config/`** package — one class per file
(`livekit.py`, `tts.py`, `stt.py`, `aura.py`, `agent_session.py`, `audio.py`, `experiment.py`, `logger.py`).
`app.py` holds the composite `Config` + the cached `get_config()`. Import via
`from src.config import get_config` (or `Config`).

- Each field reads its env var lazily in a `field(default_factory=lambda: os.getenv(...))`.
- `load_dotenv()` runs once in `config/__init__.py`.
- Add a new setting next to its peers in the right sub-module, with a sensible default.

## Defaults live in `.env.sample`, not in the code

A field's `os.getenv` default is the fallback for an **unconfigured** environment — not the value the
deployment runs with. Pick it once, when you add the field, and **do not update it when the
deployment's value changes.** Flipping a setting is an `.env` + `make sync-secrets-apply` change; if
it needs a code change, the default has been mistaken for the live value.

That has three consequences, and all three are the point:

- **`.env.sample` is the single home for the value and the case for it** — what it's set to, why,
  what it trades, what was measured. Not the code.
- **Field comments say what a setting *does*.** Never its default, a recommended value, or the
  argument for the current one. `# Off by default (…) because <measurements>` is the shape to delete:
  it goes stale the moment someone changes the env, and it is what turns a config change into a PR.
- **The literal is not production.** Don't read it as such, and don't cite it in an incident. The
  effective values are logged per session (`SessionManager`'s `Turn handling configured` /
  `Starting AgentSession`) — read those. A deployed secret and a code default can disagree in either
  direction, by design.

**Writing the case so it can't rot.** `.env.sample` is where the measurement belongs, which makes it
the one place a stale measurement is most believed. A frozen percentage is the shape that goes bad:
nothing re-derives it, and it keeps its authority long after the thing it described changed — or
after it turns out to have been wrong from the start. `AGENT_PREEMPTIVE_GENERATION_ENABLED` carried
"the turn detector reads ~78% of turns as unfinished" for months; the figure pooled every `eot
prediction` record when only the last one per turn is a verdict, and it sat next to a *correct*
adjacent number, which is what made it credible. It reached a research card and nearly justified
retuning the endpointing delays.

So state the **mechanism and the trade**, which stay true, and name **how to re-derive** the number
rather than pasting it. Where a specific figure genuinely earns its place, give it the sample size
and the date it was measured, so a reader can tell how much weight it still carries. A number with no
provenance in this file will be trusted; make sure it deserves it.

`.env.sample` also defines the key set that `make sync-secrets-apply` pushes
(`scripts/build-agent-secrets.sh` allow-lists on it), so a var missing from it is a var that can
never be configured in production. `test/unit/test_env_sample_documents_config.py` fails CI if the
code reads a var this file doesn't document.

The allow-list is the `.env.sample` in the checkout you run the target from, so a key added on an
unmerged branch is not pushed from a `master` checkout. Run `make sync-secrets` (the dry run) first
and confirm the new key is in its list; apply after the PR that adds the key is merged and released,
then check the key on the deployed agent. **Why:** an apply run before the merge would have
restarted the agent and pushed only the keys it already had.

## Resolution priority

Job metadata → environment (`.env`). The participant contributes only its identity, as the `user_id`
fallback. Its own metadata and attributes are not read for config: TTS (provider/voice/model/tuning)
comes from job metadata, which the token endpoint allow-lists, so a client can't pick a vendor resource
outside it. A TTS key a client still sends there is logged by name (`Participant TTS override ignored`)
and ignored. Removed in ENG-1688: 0 uses in 7 days, and the poll cost the control arm 0.5s (median) /
2s (p90) before the greeting.

## Fail-fast validation

`Config` splits validation into **fatal** vs **advisory**:

- `required_config_errors()` — fatal (LiveKit creds, Deepgram, the *active* TTS provider's key,
  and Aura url/token — the LLM always routes through the Aura backend, so they're unconditional).
  `OPENAI_API_KEY` is **not** required for the LLM (only for the OpenAI TTS provider). `main.py`
  logs these and `sys.exit(1)` **only for run subcommands** (`start`/`dev`/`console`) — never for
  `download-files` (it runs at Docker build time with no creds).
- `config_warnings()` — advisory: a value that survives parsing but that the consumer will not honour
  as written. Each of the four checks names the fallback actually taken, because that is the part a
  reader can't infer — the session runs, just not on the setting that was asked for:
  `AGENT_ENDPOINTING_MODE` (not `fixed`/`dynamic`) and `AGENT_INTERRUPTION_MODE` (not
  `adaptive`/`vad`) go to the SDK's `turn_handling`, which falls back to `fixed` and `adaptive`;
  `AGENT_NOISE_CANCELLATION` (not `bvc`/`bvc_telephony`/`nc`/`off`) falls back to `bvc` in
  `sessions/session_options.py`; `LLM_API_MODE` (not `chat_completions`/`responses`) falls back to
  `chat_completions` in `llm/aura_provider.py`. The `Literal[...]` annotations on these fields are
  not enforcement — every one reads a raw `os.getenv`, so a typo arrives as an ordinary `str` and
  only this check sees it. They stay advisory rather than fatal because each degrades to a working
  default; contrast `STT_PROVIDER`, which is fatal above precisely because its bad-value path
  degrades to *no* speech recognition.

  Warnings are emitted twice — `main.py` (worker) and `entrypoint.py` (per job). Only the per-job
  copy reaches Cloud Logging (see the `logging` rule), so grep the session's own rows, not the
  startup banner.

Document every env var in `.env.sample`.
