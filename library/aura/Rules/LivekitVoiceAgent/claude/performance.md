---
description: Performance and latency
globs:
alwaysApply: true
---

# Performance & latency

Voice is latency-sensitive — every turn's round-trip is felt. **The tuned values and the case for
each live in `.env.sample`, not here** (see the `config` rule); this file holds the mechanisms and
the invariants that survive a retune. Change a value there, deliberately, and measure.

- **Prewarm the VAD — and the first-turn imports.** Silero VAD loads in `prewarm()` before jobs start, not per
  session. `prewarm()` also runs `src/warmup.py`: the openai SDK imports every resource module (~160 admin type
  modules included) on the first `client.responses` access and defers every pydantic model build to first use, and
  the Cloud Logging handler imports lazily — all three landed on turns as `Event loop blocked` stalls (404 / 668 /
  202 ms in production on 1.8.1). LiveKit runs `prewarm_fnc` in the idle job process right after spawn, so work
  paid there is off the turn path. The pass is best-effort and reported per session as `warmup_*` on the
  `Session starting` row; a `warmup_failures` above 0 means a turn is paying that cost again.
- **Turn detector** (`AGENT_TURN_DETECTOR_VERSION`): `v1` is the Cloud-hosted full model, free for
  Cloud-deployed agents; `v1-mini` runs in-process (ONNX). Don't "optimize" to `v1-mini` to dodge the
  first-turn WebSocket cold start: that was tried and cost ~1.7s on *every* turn instead. v1-mini's
  weaker discrimination (LiveKit's eot-bench: 27.8% false cutoffs on a 300ms budget vs 9.9% for v1)
  left the detector voting "not finished" on 47% of turns — 64% of 1–3 word answers — which then
  waited out `max_delay`. The cold start is per-session (one job per process, so nothing is reused)
  but the socket opens during `session.start()`, before the greeting, and a not-yet-ready socket
  commits at `min_delay`, so it costs at most part of turn 1. A mid-session cloud failure degrades
  the session to `v1-mini` **stickily** in the SDK — logged as `EOT_DETECTOR_DEGRADED`, so check that before
  concluding which model was in effect. `sessions/turn_detector_recovery.py` hands a degraded session a
  fresh cloud detector after a 30s cool-off, at most 3 times (`AGENT_TURN_DETECTOR_REPROBE_ENABLED`),
  logged as `Turn detector re-probed on the cloud model`.
- **Endpointing**: `AGENT_MIN_ENDPOINTING_DELAY` / `AGENT_MAX_ENDPOINTING_DELAY` bound the
  end-of-turn wait. Don't raise the min without measuring, and reach for detector accuracy before the
  max — the ceiling only applies when the detector says the user isn't finished, so lowering it
  truncates real reflective pauses without fixing the verdict. (The SDK's own max is 2.5s for a
  session with an audio turn detector, 3.0s without one.) `AGENT_ENDPOINTING_MODE`: `dynamic` has the
  SDK adapt `min_delay` per-turn from the user's intra-utterance pauses, trimming the wait for users
  who end crisply; `fixed` gives constant, predictable delays. Dynamic needs
  **livekit-agents ≥1.6.7**: before that it also learned `max_delay` from the utterance-end →
  agent-speech gap, which contains our own wait plus LLM and TTS, so it ratcheted to the ceiling.
  Dynamic learns the **min only** — `max_delay` is a fixed ceiling in `voice/endpointing.py` — so the
  mode is neither cause nor cure for a turn that waits the max.
- **Narrowing the end-of-turn window has been measured twice, and loses both times.** Roughly a
  quarter of turns wait the ceiling, which reads like recoverable dead air; it is the residue of a
  detector that is otherwise right. Over the 4 days to 2026-09-15 (4,910 turns), **93.5%** of "not
  finished" verdicts were followed by more speech. Replaying each turn's prediction sequence against a
  lower `unlikely_threshold` converts those correct holds into mid-sentence cut-offs faster than it
  rescues dead-air turns — **≥1.7 cut-offs per rescue at every setting**, against the <1 that would
  justify it, reproducing the 72h measurement to 2026-09-08 (1.5–3.2:1) on a 2.4× sample. Lowering
  `max_endpointing_delay` is the same trade in blunter form. Language, per-user concentration and
  audio quality were each ruled out on that window, so don't re-open those either. The mechanism that
  survives: the dead air is the price of the detector's accuracy, and the lever is overlapping work
  into the wait, not narrowing it. Re-derive the cut:rescue ratio (deciding predictions only, see
  below) before proposing otherwise.
- **The VAD silence window is the floor under the endpointing delay.** The endpointing delay is
  measured from the *true* end of speech, but a turn can't commit until Silero VAD fires
  `END_OF_SPEECH` — so `min_delay` is inert below the VAD's `min_silence_duration`. The plugin's own
  default (0.55s) pinned every fast turn at ~578ms and made a 0.3s `min_delay` a no-op.
  `AGENT_VAD_MIN_SILENCE_DURATION` sets the window; the SDK's hard floor with an audio turn detector
  is `MIN_SILENCE_DURATION_MS + 50`, below which `AudioRecognition` raises, so
  `required_config_errors` rejects it at startup. The detector's own context is unaffected — it
  predicts at 200ms of raw silence regardless of this window — but a shorter window does risk
  splitting one utterance into two turns, so watch `interrupted_turns` and 1-2 word turn counts.
- **Keep the detector observable.** `AGENT_EOT_DIAGNOSTICS_ENABLED` (`src/log/eot_logging.py`) surfaces
  the runtime's per-turn verdict — end-of-turn probability vs the language threshold, and the delay it
  produced. `eou_delay_ms` alone can't tell "the model said the user isn't finished" (waits
  `max_delay`) from "the model never answered" (commits at `min_delay`) — opposite mechanisms,
  opposite fixes. Diagnose which one before tuning anything in this section.
- **Only the LAST eot prediction of a turn is a verdict — the rest are mid-utterance.** The runtime
  emits a mean of **4.9** `eot prediction` records per committed turn; the early ones fire while the
  user is still speaking and correctly report "not finished". Pooling them answers a different
  question from the one you asked, and the two differ by an order of magnitude. Measured over 24h,
  1,572 committed turns:

  | population | median probability | share below the language threshold |
  |---|---|---|
  | all `eot prediction` records (n=7,851) | **0.084** | **80.6%** |
  | the deciding prediction only (n=1,572) | **0.693** | **29.6%** |

  So the detector votes *finished* on roughly **70%** of turns at the moment it decides, with healthy
  confidence — it is not systematically unsure. The 29.6% below-threshold share also matches the
  independently measured 29.0% of turns that consume the 2.5s ceiling, which is the consistency check
  that confirms the pairing. **Do not cite a pooled figure as "the detector reads N% of turns as
  unfinished"** — that framing produced a fictitious "78% unfinished, p50 0.10" reading that survived
  into a research card and nearly justified retuning the delays. Pair each verdict to the turn it
  committed (the `User turn shape` record) before drawing any conclusion.

  Turn length is only a weak predictor: 1-3 word turns sit at 37.0% below threshold against 29.3% for
  21+ words — a real gradient, but not the clean "short turns are the problem" story that would make
  "wait longer only on short turns" a usable lever.
- **Interruption detection** (`AGENT_INTERRUPTION_MODE`): `adaptive` is backchannel-aware — a
  "mm-hmm" won't cut Aurie off — and needs an aligned-transcript STT (Deepgram qualifies); if its
  prerequisites aren't met the SDK warns and falls back to `vad`. Set it explicitly so it's the same
  across console/dev/Cloud: the SDK disables adaptive by default in non-hosted production.
- **Talk-over before playout: no agent-side guard can fix it; don't rebuild one.** When the user resumes
  during LLM latency, the runtime *pauses* the unplayed reply and resumes it at the user's first
  micro-pause, through a fixed zero-second false-interruption timer that `false_interruption_timeout` does not
  reach. A talk-over guard that cancelled such replies ran in shadow from 2026-10-27 to 10-29 (ENG-1618) and
  was removed. Over about 62h, 828 stale replies played after the user resumed, in 21% of sessions, at a
  median 1.17s after the resume. That is earlier than the transcript can deliver two resumed words, so the
  best word rule stopped only 21% of them before playout. Speech-only (VAD) rules were early enough but
  would have cancelled most noise episodes (≥0.5s: 2,845 of 3,401). The lever is the runtime's auto-resume
  itself (ENG-1695), not a threshold. It is not an endpointing problem either: most of these turns commit
  on a confident verdict. Measure via the share of `Interrupting turn shape` rows with `barge_in_ms < 0`.
  The lever is now pulled directly: `src/sessions/preplayout_resume.py` overrides the SDK's private
  `AgentActivity._update_paused_speech` so that path's `timeout=0` becomes `AGENT_PREPLAYOUT_RESUME_DELAY`.
  It is pinned to the livekit-agents version whose call sites were read, and `test_preplayout_resume.py`
  fails when the pin moves, so **an SDK bump must re-read those call sites** before moving
  `PATCHED_SDK_VERSION`. On a different SDK it logs `Pre-playout resume delay not applied` and does nothing.
- **Preemptive generation** (`AGENT_PREEMPTIVE_GENERATION_ENABLED`). Firing the LLM on a not-yet-final
  transcript overlaps LLM TTFT with the end-of-turn wait, and it is the single biggest lever on felt
  response latency — but the speculation is only as good as the fraction of the utterance already
  transcribed, so a wide end-of-turn window buys speed by answering a partial turn. `.env.sample`
  carries the measured trade and which way it is currently set. Four things to hold either way:
  - **The old duplicate-side-effect caveat is stale and must not be re-cited:** the Aura backend now
    dedupes/cancels speculative completions, so analytics, memory writes and tool calls are not at
    risk. The live trade is reply quality and determinism (one LLM call per turn, easier to
    cost-attribute and debug) against latency.
  - It works **against** the turn-taking work in the opposite direction, so land them in separate
    windows or neither effect is measurable. The narrower the end-of-turn window gets, the less there
    is left to overlap — so the case for enabling it shrinks as that work lands.
  - A speculation the SDK discards is `speech_handle._cancel()`-ed after the LLM request is already in
    flight, so it is generated then aborted, not never sent — the tokens produced before the abort are
    paid for.
  - **It cannot desync the caption from the audio.** A discarded speculation reaches neither the
    transcript sink nor the audio output nor the tool executor:
    `AgentActivity.on_preemptive_generation` builds the handle with `schedule_speech=False`, and in
    `pipeline_reply_task` transcript forwarding and `perform_tool_executions` both sit *after* the
    authorization await; `preemptive_tts` is off, so not even TTS runs early. Caption/audio mismatches
    have to be explained somewhere else (client render path, or the dual transcript paths below).

- **The agent transcript is a wire contract with the app** — changing it is a client-breaking change,
  not a tuning knob. The two flags:
  `AGENT_USE_TTS_ALIGNED_TRANSCRIPT` takes word timings from real TTS timestamps instead of the SDK's
  estimated hyphens-per-second model (wire-compatible on its own), and
  `AGENT_TRANSCRIPT_JSON_FORMAT` publishes each chunk as a JSON `TimedString` line
  (`{"text":"hello","end_time":0.67}`) so the app can highlight words in sync with
  playback. A client that concatenates chunks as raw text renders JSON at the user, so the two must
  ship together. Four asymmetries that decide client design — the first three verified in the SDK
  source, the fourth measured against the deployed agent:
  - `json_format` reaches the **agent** transcript output only, not the user STT output
    (`voice/room_io/room_io.py`), so user transcripts stay plain text and a client must branch on
    sender identity.
  - The SDK publishes **every** transcript twice — the deprecated `rtc.Transcription` segment path
    *and* the `lk.transcription` text-stream path (`_ParticipantTranscriptionOutput` fans out to both,
    with no flag to disable either). Only the stream path carries the timings, and the two sinks mint
    independent `SG_` segment ids, so a client cannot reconcile them by id — it must ignore one path.
  - Audio played from pre-synthesized **bytes** has no alignment and logs `use_tts_aligned_
    transcript is enabled but no agent transcript was returned from tts`; LiveKit closed that
    as not-planned (agents#3667). The greeting is **not** this case — `_say_first_message` passes
    text, which is synthesized and comes back timed.
  - **Only `end_time` is on the wire.** `sync_transcription` is on, so `TranscriptSynchronizer`
    (`voice/transcription/synchronizer.py`) restamps each word as
    `TimedString(word, end_time=<reveal time relative to the utterance>)` and drops the provider's
    `start_time`. A client derives a word's start from the previous word's `end_time`, and 0 for the
    first — reading `start_time` gets nothing. Confirmed in a live session on 1.5.0: every word
    carried `end_time` only. So `AGENT_USE_TTS_ALIGNED_TRANSCRIPT` doesn't decide *whether* timings
    arrive (the synchronizer emits them regardless) — it decides whether those `end_time`s are paced
    from real TTS timestamps or estimated. The absence of the warning above is what tells you which.

- **TTS init**: the provider is fixed per session from job metadata or env. Init failures surface a clear
  error (`TTS_INIT_FAILED`) rather than hanging. Avoid blocking calls during init.
- **Nothing waits for participant metadata before the greeting.** The old poll for participant TTS cost
  57% of sessions (the control arm, with no provider in job metadata) a median 0.5s and p90 2s before the
  greeting, for a channel no client used. Don't reintroduce a wait on the session-start path.
- **Metrics** flush ~2s after TTS completes (avoids premature sends from streaming STT). Keep
  metric collection off the hot path.
- Don't add synchronous network/disk work to the per-turn path; prefer async + background tasks
  (and hold references to detached tasks).
