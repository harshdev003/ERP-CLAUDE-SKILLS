# Voice signals: what each source can and cannot tell you

Read this before interpreting a digest. It covers the joins between sources, the
thresholds the script grades against, and the traps that make a confident-looking
wrong answer.

## The join that makes a complaint traceable

A rating names an **Aura** conversation id. A voice call lives in ElevenLabs under a
**vendor** id (`conv_…`). They are tied together in exactly one place:

```
conversation_initiation_client_data.dynamic_variables = {
  "conversationId": "ECEFBC4B-…",   # the Aura conversation id from the rating
  "userId":         "vXNh…",         # Firebase uid
  "platform":       "mobile",
  "givenName":      "…",             # PII — never render
  ...
}
```

Two consequences:

- **The list endpoint cannot answer the question.** `GET /v1/convai/conversations`
  returns summaries with no dynamic variables, so a match needs a detail fetch per
  candidate. Narrow by `start_time_unix_secs` first, then fetch.
- **The client event is a shortcut to *one* call, not to the session.** A Bugsnag voice
  event carries `conversation.elevenLabsConversationId` directly. Use it — then still
  search on that record's own `conversationId`, because the named call is usually one leg
  of a longer session and the rest are needed to attribute anything timed.
- **One Aura conversation maps to *many* vendor calls.** A reconnect mints a new `conv_…`
  carrying the same `conversationId`, so the relationship is one-to-many, not one-to-one.
  Nine calls in an hour is a real observed session. Treating one as "the" call is what
  Trap 1b describes.

Case matters in neither direction — the Aura id appears upper-case in the vendor
record and lower-case in the Slack rating, so compare case-insensitively.

## Retention

ElevenLabs keeps convai conversation detail for roughly **10 days**. An older incident
resolves to no record. That is a retention miss, **not** evidence the call never
happened — say so rather than reporting "no vendor conversation".

## Which metric is the symptom

Five per-turn metrics are available; only one is what a user actually experiences.

| Metric | Means | Use for |
|---|---|---|
| `convai_ttf_audio_since_silence` | User stops talking → first agent audio plays | **The felt number.** Grade on this |
| `convai_llm_service_ttf_sentence` | LLM to first sentence | Explaining a bad felt number |
| `convai_llm_service_tt_last_sentence` | LLM to last sentence | Long-reply stalls |
| `convai_tts_service_ttfb` | TTS first byte | Isolating vendor TTS |
| `convai_asr_trailing_service_latency` | ASR trailing on user turns | Turn-taking snappiness |

Thresholds in `correlate.py` (`FELT_LATENCY_DEGRADED` = 3.0s, `FELT_LATENCY_BAD` = 5.0s)
are **heuristics anchored on one calibrated incident**, not vendor SLOs: a user
complained about a turn measured at 6.4s while turns at 1.2–3.0s in the same call drew
no complaint. Treat them as a starting point and revisit when there is a second
calibrated example. `eng:analyze-elevenlabs-latency` gives the population percentiles to
judge whether one call is actually unusual.

## Trap 1: the reply watchdog is usually a false positive

`NSError: Conversation Reply Timeout Exceeded`, from
`ElevenLabsVoiceService+ReplyTimeout.swift`, is the **loudest error in `ai-aurie-ios`**
and mostly says nothing. It is a fixed 45s timer armed when the client expects a reply,
and it is not cancelled when the reply arrives — so it fires on healthy calls. It fires
with `agentState = listening`, i.e. after the agent already handed the turn back.
Tracked in [PROD-4395](https://linear.app/aura-health/issue/PROD-4395).

**Never report it as "the agent hung" on its own.** Check the vendor transcript for agent
turns inside the watchdog's own window, which the event's metadata bounds exactly:

```
metadata.replyStartTime  → window start (unix)
metadata.timestamp       → window end   (unix, == start + timeoutDuration)
metadata.agentState      → what the client thought the agent was doing
```

An agent turn inside that window means a reply did arrive → **false positive**. No agent
turn in the window → **genuine stall**, and that is a real finding worth escalating —
but only once the window has been checked against the *right* call. See Trap 1b.

Because the signature is endemic, always pair a per-user hit with its org-wide count in
the same window. A signature firing for hundreds of users rarely explains one complaint.

## Trap 1b: check the window against the call that was running

A watchdog window can only be judged against the call it happened on. One Aura
conversation spans several vendor calls, so checking an event against a different call of
the same session compares it to turns that were never on its wire — and that fabricates
both verdicts:

- turns absent because the call had not started yet → reported as a **genuine stall**
- another call's turns present → a **real stall wrongly cleared**

The first is the dangerous one, because `genuine` is the one verdict this skill treats as
evidence, and it is reported at high confidence. Observed: a window at 01:12:08–01:12:53Z
judged against a call that started 01:14:40Z, reported as a genuine stall while the call
that actually owned it had replied 1s into the window.

The rule: the watchdog arms when the user stops speaking, which happens on a live call, so
the call containing `replyStartTime` owns the event. If no matched call was running then,
the answer is **unverifiable** — never `genuine`.

Two spans can also overlap, because a record that never closed keeps covering later calls
— the zombie shape this skill investigates. The most recently started covering call is the
one the user was on. And a still-running call reports `0s`, so its span has to be treated
as open-ended or every event on it becomes unattributable.

## Trap 2: LLM traffic in our logs is not a text fallback

Seeing `openaiHandler.chatResponse` / "Streaming Aura Ask over SSE" in the prod logs
during a voice session does **not** mean the user gave up and started typing. The voice
agent calls back into Aura for reply text, so voice turns appear as ordinary LLM calls.

The authoritative signal is the Bugsnag breadcrumb metadata:

```
"Current Conversation Mode": "Voice Mode"
```

Check it before claiming a fallback to text. Reading the log traffic alone produces a
confident, wrong narrative about user behaviour.

## Trap 3: the audio route is both a clue and a PII leak

`metaData.audio` explains device-side audio problems — a Bluetooth hands-free route
forces a lower sample rate (e.g. 24000Hz) than the agent output format (`pcm_48000`),
which is a plausible contributor to perceived breakup.

Two rules:

- **Check whether it generalises before blaming it.** In the calibrated incident the
  affected user was on Bluetooth, but only 3 of 14 sampled events were — so it explained
  that user and not the population.
- **`routeOutputs` contains a person's name.** Devices are named like
  `BluetoothHFP:<owner>'s AirPods`. `correlate.redact_audio_route()` keeps the port type
  and replaces the name; never print the raw field.

## Trap 4: a handled report's stack is not a fault stack

An event with `unhandled: false` was *reported*, not crashed. Its stack is the reporting
thread's state at the moment `BugsnagReportService.report` was called — not the frames that
produced the error. Unrelated call sites therefore share one trace, and a frame naming a
function is not evidence that function failed.

Observed: one event carried `Swift runtime failure: Range requires lowerBound <= upperBound`
under `Logger.fileName(from:)`, alongside `AIRatingButtons.onReasonSelected` and
`BranchService.handleSession` — three unrelated call sites in a single trace. The shipped
`Logger.fileName` guards with `lastIndex(of:)` before `index(after:)`, so that failure is
unreachable there.

**Read the message and the metadata, not the frames.** Those are recorded deliberately:

```
metaData.nserror.NSErrorFailingURLStringKey  → the request that actually failed
metaData.metadata.context / .operation       → the call site that reported it
```

A signature named "A TLS error caused the secure connection to fail." resolved this way to a
Branch SDK call to `api3.branch.io/v1/open`, and "The network connection was lost." to
`ChatInterractionService.syncConversationFromApi` — neither one touching the voice transport
the signature names suggested.

**Verify against shipped source before filing a bug**, checking out the tag matching the
event's `app.version` rather than `main`.

**`received_at` is receipt time, not event time.** It lags, and by a variable amount — one
watchdog event recorded `metadata.timestamp` 14:28:54 against a `received_at` of 14:29:31.
Order events within a call by `metaData.metadata.timestamp`; use `received_at` only to find
them.

## Which voice stack served the call

The `aiLiveKitIntegration*` assignment names the experiment arm, but the direct evidence
is which vendor call actually happened — an 11Labs conversation-token fetch in the prod
logs means the ElevenLabs path ran. When arm and evidence disagree, the evidence wins.

**The experiment is re-cut under a new trailing version each time it restarts** (`…2`
ended 2026-08-06, `…3` has been live since 2026-08-11, `…4` is planned), so
`correlate.newest_livekit_experiment()` matches the whole family case-insensitively —
every member resolves, the unreleased one included — and the readout names the
experiment that answered rather than a pinned one.

Never pin a version in a lookup key. The log-parsing regex is generic, so a stale key
still populates `leg.experiments` and only the lookup misses — and it misses *only* on
the LiveKit arm, which is the one case the line exists to explain. Nothing errors; the
voice path just silently reads `unknown`.

**When two family members appear in one window the tiebreak is the assignment
timestamp**, not the version — `leg.experiment_stamps` carries each winning
assignment's stamp for exactly this. Highest-version-wins would agree with the clock
today, because the cuts have launched sequentially, but it silently encodes a
non-overlap assumption; the version is kept only as the fallback for an assignment
that arrived with no stamp.

For LiveKit-path incidents this skill's vendor leg does not apply: LiveKit runtime logs
are forwarded to GCP under a different log name, and `lk agent logs` tails live only.
Use `eng:analyze-livekit-latency` / `eng:investigate-livekit-logs` for that path.

## Privacy

Per `Compliance/Privacy`: UID prefix + email domain only. This skill additionally never
renders transcript text — turn *shape* (role, timing, length, latency) is enough to
diagnose latency and stalls, and the words are the user's private disclosure. When a
transcript quote feels necessary, paraphrase the symptom instead.
