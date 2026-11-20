---
description: Structured logging standards
globs:
alwaysApply: true
---

# Logging

**Formatting is owned by the LiveKit runtime, not us.** LiveKit's CLI installs the handler +
formatter — a readable colored formatter in dev, single-line JSON in production — and per job
attaches a filter that injects `JobContext.log_context_fields` into every record. So there is **no
custom formatter / Stackdriver envelope** (the agent runs on LiveKit Cloud, not GCP).

- Get a logger with `logging.getLogger(__name__)` (or a dotted name like `"agent.entrypoint"`).
  Never `print`.
- `setup_logging(config)` in **`src/logger.py`** only applies `LOGGER_LEVEL` (case-insensitive) to
  the root logger and quiets noisy third-party loggers (httpx/httpcore/openai). It installs no
  handler. It's **idempotent** — called in `main.py` and re-asserted per job in `entrypoint.py` (so
  `LOGGER_LEVEL` wins over the CLI default).
- **No emoji** in log messages (✅/❌/ℹ️). Keep messages plain; put structured data in `extra=`
  (LiveKit's formatter merges `extra={...}` fields into the log line).
- **Start the message with a capital letter** — `logger.info("Session started")`, not
  `"session started"`. (A message that opens with an interpolated value — `f"{provider} ready"` —
  is fine; the value supplies the leading word.)
- **Don't prefix the message with the function name** when it's already structured context —
  the logger name is already on the record.
- **Carry `component` + `method`** on every log — the shared Aura convention (EmotionSystem /
  IntelligenceService). `component` is the **logger name** (the filter maps `record.name` →
  `jsonPayload.component`), so don't set it by hand — just name the logger for the module/area.
  `method` is added per function: declare `meta = {"method": "<function_name>"}` as the first line
  of the body and thread it through every log call — `logger.info("Msg", extra=meta)`, merging any
  other fields as `logger.error("Msg", extra={**meta, "error_code": ...})`. Pass it to timing too:
  `log_duration(logger, "label", meta=meta)`. Module-level logs (e.g. import guards) have no method
  scope — the logger name alone is fine there.
- Per-session correlation: the entrypoint publishes `Context.meta()` (see the `error-handling`
  rule) to `ctx.log_context_fields`, and LiveKit's own filter stamps those ids
  (`transaction`/`transactionSlice`/`room`/`userId`/`conversationId`) onto every record — re-publish it
  when ids resolve. No custom log filter of ours.

## Levels & message pattern

Log significant **operations and lifecycle transitions** as a **start → done pair** — the Aura
convention shared with EmotionSystem (Python) and IntelligenceService (Node): a gerund when the work
starts and a past-tense/milestone line when it finishes, e.g.
`logger.debug("Building AgentSession")` … `logger.info("Session config resolved", extra={...})`.
Don't log trivial getters or narrate every line — log what an operator would want to trace.

Pick the level by intent. This is the shared Aura level set (EmotionSystem / IntelligenceService),
with the same GCP severity mapping — so the agent's rows sort and filter identically cross-service:

| Level | GCP severity | When |
|---|---|---|
| `debug` | `DEBUG` | operation starts; verbose/diagnostic detail (metadata dumps, "found X", per-attempt polling) |
| `info` | `INFO` | completed milestones; key lifecycle events (session started, provider selected, metrics sent) |
| `warning` | `WARNING` | recoverable problems; empty-but-handled results; fallbacks (e.g. no provider in metadata → env default) |
| `error` | `ERROR` | failures — **reference the `errors.py` catalog code** (see the `error-handling` rule), e.g. `logger.error(AgentErrors.TTS_INIT_FAILED.message, extra={"error_code": AgentErrors.TTS_INIT_FAILED.code, "detail": ...})` |
| `critical` | `CRITICAL` | (renders as shared `crit`) a failure demanding immediate attention. Rare — most failures are `error`. |
| `audit` | `DEFAULT` | audit-trail events, above `CRITICAL`. `logger.audit(...)` / `logging.audit(...)` (registered in `src/log/log_levels.py`). |

The shared short names (`crit` for `CRITICAL`, `warn` for `WARNING`, `audit`) and the GCP severity
above are emitted by the Cloud Logging forwarder's filter (`src/log/gcp_logging.py` → `jsonPayload.level`
+ top-level `severity`); parity with EmotionSystem's `LEVELS` / `STACKDRIVER` maps.

## Which process a log comes from decides whether it survives

The Cloud Logging handler is attached by `setup_logging` **per job**, in `entrypoint.py`. Anything
logged from the **worker process** — `prewarm()`, the `main.py` startup banner — therefore reaches
stdout only and is **invisible in production**. Not theoretical: a `Silero VAD loaded` line was added
in `prewarm()` precisely so the deployed VAD window would be checkable, and it never appeared in Cloud
Logging once (PROD-4030).

So when a value must be observable in production, log it from a **per-session** path, and prefer
reading it off the live object over re-reading config — `SessionManager.build`'s
`Turn handling configured` line is the pattern: one info row per session carrying the whole
turn-latency surface, with `vad_min_silence_duration` taken from the VAD instance so a `load()` that
ignored the kwarg is still reported truthfully. A deployed secret can silently disagree with the code
default, so "which config produced this turn" has to be answerable from the logs alone.

**The release version is the exception — it is already on every forwarded row.**
`gcp_logging.py`'s `_ContextToJsonFields` stamps `serviceContext.version` (read from `pyproject.toml`
via `src/version.py`) onto every record the **job** process forwards, so a log window splits by
release with `jsonPayload.serviceContext.version` — group by it rather than reaching for deploy
timestamps, which cannot separate overlapping deploys, a rollback, or a long-lived job process from
the release before it. Worth stating explicitly because the obvious place to look is the `main.py`
startup banner, which logs `version:` from the **worker** process and therefore never arrives:
reading only that line leads to the conclusion that a log window can't be attributed to a release at
all, which is the opposite of true. The corollary is a free tell — a forwarded row carrying no
`serviceContext` did not come through the per-job handler.

## Borrowing a third-party DEBUG line

`LOGGER_LEVEL` is the blunt instrument; when the runtime already logs exactly what we need but at
DEBUG, don't re-derive it — unlock that one record. `src/log/eot_logging.py` is the pattern: raise the one
third-party logger (`livekit.agents`) to DEBUG behind an allow-list filter matched on the raw
`record.msg` template, so everything else below INFO is dropped and INFO+ always passes.

Two gotchas that shape the design:

- **A raised level is inherited by descendants, and a logger's filters are not consulted for records
  that merely propagate through it.** So screening only `livekit.agents` would let a DEBUG record from
  `livekit.agents.<child>` through unfiltered. Attach the filter to the logger **and** to every handler
  on the root logger — the handler is the boundary every propagated record must cross. Don't rely on
  "that logger has no children"; it's an SDK implementation detail that changes between releases.
- **A filter on a shared handler sees everything**, so it must be name-scoped and pass through records
  from outside the tree it screens — otherwise it silently eats our own logs at `LOGGER_LEVEL=debug`.

Wire it from the **end** of `setup_logging`, after the Cloud Logging handler is attached, so that
handler is screened too; keep `enable_*()` idempotent (our logging setup is re-asserted per job). The
same filter is the place to stamp a third-party warning with an `errors.py` code (e.g.
`EOT_DETECTOR_DEGRADED`) so prose-only vendor messages become groupable.

## Annotating a third-party record we can't change

Nine filters (in `src/log/`) now screen records we don't own, and the choice between them is about what they do to a
record, not which vendor they watch:

| Module | Does | Scope |
|---|---|---|
| `eot_logging.py` | **drops** (allow-list) + stamps a code; **downgrades** a detector degrade that lands during teardown (`eot_degraded_during_teardown`, gated on `transport_logging`'s window) | name-scoped — must be, or it eats our own logs |
| `redact_logging.py` | **rewrites** `msg`/`args`/traceback | unscoped — a rewrite is safe everywhere |
| `transport_logging.py` | **downgrades** expected teardown noise, **gated** on a session-state window | two trees that share that gate: the bridged Rust core (`livekit`, excluding the `.agents` / `.plugins` trees — data-channel/negotiation/signalling rows stamped `transport_teardown`, the engine's resume/restart family stamped `engine_reconnect_teardown`; outside the window, only the engine's reconnect *progress* rows (`resuming`/`restarting connection... attempt`) are demoted, stamped `engine_reconnect_attempt`, and its `resuming connection failed` row keeps ERROR) and the runtime's `failed to send session event` plus its close-time `speech not done in time after interruption` (stamped `interruption_timeout_teardown`) |
| `tts_logging.py` | **stamps** a code + **downgrades** a duplicate row | recognizes its records, passes the rest |
| `llm_logging.py` | **downgrades** a duplicate traceback + stamps `llm_inference_traceback` (and `LLM_RESPONSE_FILTERED` for a content-filter refusal) | name-scoped to the runtime logger; narrowed to `APIError` |
| `stt_logging.py` | **stamps** a code + **downgrades** a duplicate traceback | name-scoped to the runtime logger; the demotion is narrowed to the Inference STT close exception |
| `deprecation_logging.py` | **downgrades** a vendor notice about a choice we made on purpose | name-scoped to the runtime logger |
| `recorder_logging.py` | **downgrades** a routine recorder drop, **gated** on how much audio was lost | name-scoped to the runtime logger |
| `telemetry_logging.py` | **downgrades** LiveKit Cloud telemetry export failures (OTLP batches, the session-report upload) + stamps `telemetry_export_failed` / `telemetry_status` | name-scoped to the OTLP exporter loggers and the runtime logger |

`transport_logging.py` adds a third rule the others don't need: **when a vendor record is only noise
in one situation, gate the screen on that situation rather than suppressing the message outright.**
The Rust core reports peer-connection teardown at ERROR, which is correct-but-useless when the user
has just left and genuinely alarming when they have not. Downgrading it unconditionally would have
silenced the one case worth an alert, so the screen opens on participant disconnect and closes again
on reconnect. Establish which situation dominates from data first — every observed occurrence (11/11
data-channel, 4/4 renegotiation) sat on a teardown, and that is what justified the gate rather than a
guess about severity.

The gate must open on **every** path a close can start from, and a state gate can only see what
Python is told. `SessionManager` opens the window from three seams — `participant_disconnected`,
`_end_session`, and the room's own `disconnected` event — because one production close reached
`Deleting room on agent session close` through none of the first two: when the *agent's* connection
drops, the runtime's job process ends the job from `room.on("disconnected")` itself, so no participant
event fires and `_end_session` never runs. Three data-channel rows sat at ERROR, unstamped, two seconds
before anything opened the window. When a screen gated on state fires on some sessions and not others,
list the close paths before extending the message list — a message that is already covered and still
arrives at ERROR is a gate hole, not a matching bug. And know the residual: the core logs *before* it
dispatches the event (~1–2 ms earlier when reproduced against a server-side room delete), so a first
burst that precedes the event cannot be caught by any Python-side gate **opened by that event**.

That residual is closed for the case that produced it by a fourth seam opened *ahead* of time: the
window is open from `start()` until the first `participant_connected`. Before anyone joins, a transport
close can only be the room going away around nobody (LiveKit closing an empty room with `ROOM_CLOSED`,
or our own no-participant close). Every unstamped data-channel ERROR burst on 1.12.4 (7 of 7) was
exactly that, landing 2–5 ms before `Room disconnected`. An event-driven gate cannot beat the core's
log line, but a *state* known in advance can.

`transport_logging.py` also shows **when a second vendor tree belongs in an existing screen rather than
a new module**: the runtime's `failed to send session event` (a `RuntimeError: room session transport is
closed`, logged with `exc_info=True`, so every occurrence costs a full traceback) is a different logger
and a different record shape from the Rust core's data-channel errors, but it is the *same situation* —
the transport went away on purpose — and so needs the *same gate*. Two modules owning one window means
two copies of that window, which is how they drift apart. Group screens by the state they are gated on,
not by the vendor they watch. Give each branch its **own stamp field** even so: a stamp is the only way
to confirm after deploy that a branch matched anything, and one shared with an already-verified branch
cannot answer that for the new one.

Four more rules fall out, and all of them fail silently when broken:

- **Match `record.getMessage()`, not `record.msg`, anywhere in the bridged `livekit` tree.** The Rust
  core reaches Python through one lazy call in `livekit.rtc._ffi_client` —
  `logger.log(level, "%s:%s:%s - %s", target, line, module_path, message)` — so `record.msg` is only
  the template and the whole vendor string lives in `record.args`. A screen matching `record.msg`
  there matches **nothing**, in every session. `transport_logging.py` shipped that way and downgraded
  not one record for a full release; the tell was `jsonPayload.transport_teardown` sitting at zero
  while the ERROR rows it targets kept arriving. The `livekit.agents` / `livekit.plugins` / `asyncio`
  records the sibling screens read *do* carry a real string, which is why `eot_logging.py` and
  `tts_logging.py` match `record.msg` correctly — the line is the FFI bridge, not the vendor. Wrap the
  call: `getMessage()` raises on a malformed args tuple, and a filter that raises loses the row it
  only meant to relabel.
- **Never read `record.msg` directly; pick a helper from `src/log/log_screening.py` by matching style.**
  `screened_message()` (formatted, lowercased, never raises) for prefix/substring matching;
  `record_template()` for equality against a whole template, where an interpolated value must not
  change what the match sees. `eot_logging.py` needs **both** — its allow-list compares templates while
  its degrade branches match prefixes — so this is a per-matching-style choice, not a per-vendor one.
  A blanket switch to `getMessage()` breaks template equality; a blanket `record.msg` breaks the bridged
  core. Note also that a lazy record puts the vendor text *mid-string*, so a lazy-capable screen must
  match with `in`, not `startswith`.
- **Register every screen in `test/unit/test_log_screen_conformance.py`.** That suite is the
  before-merge answer to this whole class of bug, and it asserts four things per screen: that it fires
  on **each record shape its vendor actually produces** (the registry declares the shape, because that
  was the missing knowledge), that it leaves our own records untouched, that it never raises on a
  malformed `args` tuple, and — discovered from the source tree, not a hand-kept list — that no
  `src/log/*_logging.py` filter is missing from the registry. Verified by fault injection: reverting
  `transport_logging` to `record.msg` fails it with *"transport_teardown did not fire on a lazy
  record"*.

  It does **not** prove a screen matches the right vendor *string* — only production confirms that. It
  proves the screen can match a string of that shape at all, which is precisely what three shipped
  screens could not do, plus (over the full two-handler stack) that no *other* screen removes the row
  afterwards. So a stamp field still has to be checked in production after deploy; what has moved
  before the merge is the failure that is invisible afterwards.

  Two traps the registry itself hit while being written, both worth knowing because both *passed*
  while asserting nothing: a screen gated on a vendor `extra` field (the recorder's `samples`) silently
  tests the unreadable path if the entry omits it, and a `fired` predicate phrased as an absence
  (`"name" not in message`) is trivially true of every unrelated record. Phrase `fired` as a positive
  assertion that the mutation happened.
- **Match the vendor message case-insensitively.** The Cloud Logging forwarder capitalizes lowercase
  messages *in place* (`gcp_logging.py`), so a filter matching an exact lowercase template only works
  when it happens to run first.
- **Insert at the front of the handler's filter list** (`filters.insert(0, …)`, not `addFilter`) for
  anything that changes a record's level. The forwarder's `_ContextToJsonFields` reads
  `record.levelname` to build `jsonPayload.level` and is already attached, so an appended filter
  downgrades *after* the severity was recorded and the row still forwards at its old severity.

**Demote through `demote_to_debug` (`src/log/log_screening.py`), never by setting `record.levelno` by
hand** — and understand what that helper is for, because the bug it fixes is invisible twice over.
Handler filters mutate the **one** record every later handler sees, and `Logger.callHandlers` re-reads
`record.levelno` per handler. Production has two root handlers: the runtime's stdout handler first,
our Cloud Logging handler second. So a row a screen demotes on the first handler arrives at the second
already *looking like* a vendor DEBUG record — and `eot_logging.py`'s allow-list, which drops
`livekit.agents` records below INFO, dropped it there. The helper marks the record as demoted on
purpose (an underscore-prefixed attribute, so it never reaches `jsonPayload`) and the allow-list passes
anything carrying that mark.

The symptom was `recorder_drop_minor` and `metrics_collected_deprecated` at **zero rows in 30 days**
while `transport_teardown` (947 rows/13d) and `TTS_STREAM_CLOSED` (10) were healthy — same demotion,
different logger tree. That asymmetry is the whole diagnosis, and it was twice mistaken for "a demoted
row is simply not forwarded", once in a rule here and once in a screen's own docstring, each citing the
other's zero as corroboration. Two screens reading zero is not evidence about the transport; it is
evidence they share a cause. Chase the *difference* between a stamp that works and one that doesn't
before concluding anything about the pipeline.

A demoted row does forward, and does stay countable. What it will not do is survive a *second* screen
that judges it by level, so `test/unit/test_log_screen_conformance.py`'s stack test runs the whole
installed filter chain over two handlers rather than each filter alone — a per-filter test cannot see a
screen-versus-screen interaction, and all of them passed throughout.

A fifth rule, learned by breaking it: **a screen that must always apply cannot live in a flag-gated
module.** The `metrics_collected` deprecation screen is its own module rather than a branch in
`eot_logging.py` precisely because that one is attached only when `AGENT_EOT_DIAGNOSTICS_ENABLED` is
on — folding it in there would have made turning diagnostics *off* make the log *noisier*.

And the notice it screens is worth remembering as a category: **a vendor deprecation warning for a
migration you have already done, kept alive by the one case the replacement does not cover.**
`InterruptionMetrics` is reachable through no event other than `metrics_collected`, so the
subscription is deliberate and permanent until upstream moves it — but unscreened the notice fires
once per session, which at this traffic would make it the loudest line in the log. Screen it, stamp it
so the subscription stays visible, and revisit if the vendor ever provides another route.

Note a level change in a filter **relabels** a record, it does not suppress it: `Handler.handle` runs
filters after the level check, so the row still emits — at the new severity. That is enough to keep it
out of Error Reporting (which groups ERROR+), which is the point when de-duplicating one incident
that the runtime reports more than once (`TTS_STREAM_CLOSED`).

Put **operation-specific** fields in `extra={...}` (provider, voice, model, counts, durations). Do
**not** put the correlation ids (`transaction`/`room`/`userId`/`conversationId`) in `extra` —
LiveKit's filter auto-stamps those on every record, so duplicating them is redundant. Never log full
end-user PII (see the `security` rule); log a length or prefix instead.

## Timing operations

Time latency-sensitive operations with `log_duration` (`src/utils/timing.py`) — the shared Aura
`start_time`/`end_time` convention (EmotionSystem / IntelligenceService). It logs the elapsed ms at
**warning** (over `EXECUTION_TIME_WARN_THRESHOLD`), **info** (over `EXECUTION_TIME_INFO_THRESHOLD`),
or **debug** otherwise, so a slow op surfaces without every fast one adding noise. Duration rides in
`extra` as `operation` + `duration_ms`, so it's queryable in Cloud Logging (`jsonPayload.duration_ms`).

```python
with log_duration(logger, "connect to room"):
    await ctx.connect()   # await inside the block is fine — it's timed end to end
```

Reach for it on network/backend round-trips (room connect, experiment assign, API POSTs) — not on
trivial local work. Per-turn conversational latency (LLM TTFT, end-of-utterance delay) is already
tracked separately in `src/metrics/session_metrics.py`.
