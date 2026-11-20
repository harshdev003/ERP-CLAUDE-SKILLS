---
name: investigate-voice-issue
description: >
  Triage one reported Aurie voice complaint and say what the user actually experienced.
  Correlates four read-only sources — Bugsnag client errors, the ElevenLabs call record and
  per-turn latency, the Aura prod GCP logs, and the client device context — then returns a
  verdict separating the real symptom from instrumentation artifacts. Takes a Slack rating
  URL, a Firebase UID, or an Aura conversation id. Trigger on "investigate voice issue", "why
  is voice broken for this user", "voice quality complaint", "audio breaking up", "the agent
  went silent", "agent stopped replying", "voice is choppy", "voice is laggy", "aurie didn't
  hear me", "dead air on the call", "triage this voice rating", or a pasted thumbs-down rating
  mentioning voice or audio. Do NOT use for aggregate latency across many calls
  (`eng:analyze-elevenlabs-latency`), LiveKit runtime logs (`eng:investigate-livekit-logs`), a
  non-voice Bugsnag error (`eng:investigate-bugsnag-errors`), or what was said in a
  conversation (`data:analyze-user-conversations`).

metadata:
  version: "1.9.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/ElevenLabs         # convai conversation endpoints, key, retention window
    - Tools/Bugsnag        # auth shape, project inventory, filter fields, PII rules
    - Tools/GCP            # SA name, project, env-var, log-schema cheatsheet
    - Compliance/Privacy   # user-scoped output — UID prefix + email domain only
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Tools/Braintrust               # Braintrust fallback path (BRAINTRUST_API_KEY)
    - Tools/LiveKit                  # LiveKit voice-agent log stream
---

# investigate-voice-issue

The entrypoint for *"a user says voice was broken — what actually happened?"*.
Read-only across every source; it never writes, posts, or resolves anything.

## Goal

Turn one voice complaint into one verdict that answers two questions separately:

1. **What did the user experience?** — the symptom.
2. **What is just noise?** — client errors that fire on healthy calls.

Keeping those apart is the whole job. The loudest error in the iOS project is a
false positive (see [`references/signals.md`](references/signals.md) → Trap 1), so a
triage that starts and stops at Bugsnag reliably reaches the wrong conclusion.

## Inputs

One of:

| Input | What to do |
|---|---|
| Slack rating URL | Fetch the message with `shared:read-slack`, then pass its identifiers |
| Pasted k8bot rating message | Pass it via `--rating-text`; the script parses `user:` / `conversation:` out of it |
| Firebase UID | Pass `--user` |
| Aura conversation id | Pass `--conversation` (add `--user` when known — it unlocks the Bugsnag and GCP legs) |

A UID alone is enough. A conversation id alone limits the run to the vendor leg,
because both the Bugsnag and GCP queries pivot on the user id.

## Workflow

### 1. Resolve the input

If given a Slack URL, read the message with `shared:read-slack` before anything
else — do not guess the identifiers, and do not call Slack directly. A k8bot rating
carries them in a trailing block:

```
`[dialogue: <uuid>, conversation: <uuid>, user: <uid>, prompt:<name>:<version>]`
```

Note the **rating time** as well. Ratings arrive minutes after the call, so
anchoring the search window on it with `--at` is much cheaper than a blind
trailing window.

### 2. Run the correlation

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-voice-issue/scripts/investigate.py \
  --user <firebase-uid> [--conversation <aura-conversation-id>] \
  [--at <rating-time-iso8601>] [--since 2h] [--project ai-aurie-ios]
```

Useful flags:

| Flag | Default | Meaning |
|---|---|---|
| `--at <iso>` | — | Centre the window on the incident time (strongly preferred) |
| `--since <dur>` | `24h` | Window size — with `--at` it becomes the total span, centred |
| `--project <slug>` | all | Limit Bugsnag to one project (fuzzy, so `aurie-ios` resolves) |
| `--rating-text "<msg>"` | — | Parse identifiers from a pasted k8bot message |
| `--max-detail-fetches <n>` | `40` | Backstop on vendor detail fetches. With a UID the list call is narrowed server-side, so this rarely binds; it only bites when no UID was supplied or the narrowing stopped working |
| `--json` | off | Emit the correlated payload instead of the digest |

**One asymmetry worth knowing when reading the output.** Bugsnag's `event.since`
filter only accepts a trailing duration from now — there is no absolute range. So a
window centred on `--at` is reached by widening the Bugsnag query back to the window
start and trimming the per-event detail client-side. Signature and org-wide counts
therefore describe that wider trailing period, not the centred window; the Sources
block says so explicitly whenever the two differ. The vendor and GCP legs use the
centred window directly.

The script fetches and correlates; **you interpret**. It does not decide what to
tell the user.

### 3. Read the digest against the traps

Before writing anything, check [`references/signals.md`](references/signals.md).
It is short and every section exists because a plausible reading was wrong:

- **Trap 1** — the reply watchdog fires on healthy calls. The digest classifies each
  event as false-positive / genuine / unverifiable by checking the vendor transcript
  for agent turns inside the watchdog's own window. Only a `genuine` verdict supports
  "the agent stopped replying".
- **Trap 1b** — one Aura conversation spans **many** ElevenLabs calls, because a
  reconnect mints a new `conv_…` carrying the same `conversationId`. Each event is
  therefore judged against the call that was running when it armed, and an event no
  matched call covers is `unverifiable` — never `genuine`. Read the session table
  before the watchdog list: a nine-call session is usually the symptom, not a detail.
- **Trap 2** — LLM traffic in our logs during a voice call is *not* the user typing.
- **Trap 3** — the audio route explains device-side breakup, but check whether it
  generalises before blaming it, and never print the raw route (it contains a name).
- **Trap 4** — a handled report (`unhandled: false`) carries the reporting thread's stack,
  not a fault stack. Read the error metadata for the real failing request, and verify
  against shipped source before filing a bug from a frame.

Also sanity-check the felt-latency grade: the thresholds are heuristics from one
calibrated incident. When a call grades `degraded`, `eng:analyze-elevenlabs-latency`
says whether that is unusual for the population or an ordinary day.

### 4. Report

Lead with the symptom, then the artifacts, then the evidence. State plainly when a
source was unreachable — the digest's Sources block already records it.

When the finding is a genuine product bug, offer to file it via
`shared:add-linear-issues`; when it belongs in a Slack thread, offer
`shared:post-to-slack`. Both are the user's call — this skill posts nothing itself.

## Output

A markdown digest, in this order:

1. **Verdict** — symptom, instrumentation artifacts, session health, confidence
2. **Vendor call** — every call of the session (one row each: record, start, duration,
   turns, termination), the reconnect count, then the longest call in detail
3. **Turn latency** — worst value per metric, graded on time-to-first-audio only
4. **Reply watchdog events** — per event: false positive / genuine / unverifiable
5. **Client errors** — signatures the user hit, each with org-wide scale, plus device/audio context
6. **Our leg** — voice path, Aura LLM response times, log span, any errors
7. **Sources** — what answered and what degraded
8. **Drill-in hints** — the sibling skills for a deeper pass

`--json` returns the same content as a payload for a calling skill.

## Degradation

Every leg is independent and optional. A missing token narrows the report and is
recorded under Sources; the run still completes.

| Missing | Effect |
|---|---|
| `ELEVENLABS_ADMIN_API_KEY` (or `ELEVENLABS_API_KEY`) | No vendor call, so no latency and no watchdog verification — watchdog events stay `unverifiable` |
| `BUGSNAG_TOKEN` | No client errors and no device/audio context; the vendor call is then found by search rather than named directly |
| GCP credentials | No Aura-side latency and no voice-path confirmation |

**`GCP_KEY` unset does not disable the GCP leg.** `gcp.py` walks an auth ladder —
`GCP_KEY`, then `GOOGLE_APPLICATION_CREDENTIALS`, then ADC — so on a machine with
`gcloud` already authenticated the leg keeps working and reports which rung it used.
Only a failure of the whole ladder removes it.

When **no** leg returns anything the verdict says *could not investigate* with
confidence `none`, rather than reporting an absence of findings — those two read
identically in a digest and only one of them is good news.

A `genuine`-looking watchdog verdict is impossible without the vendor leg. That is
deliberate: the false-positive rate is high enough that an unverified watchdog event
is not evidence, so the skill downgrades to `unverifiable` instead of guessing.

## Privacy

Per `Compliance/Privacy`, and enforced in `correlate.py` rather than left to
judgement:

- Firebase uids render as an 8-char prefix (`uid_prefix`).
- Audio routes render with the device name stripped (`redact_audio_route`) — the raw
  field contains a person's name.
- **Transcript text is never fetched into the output.** Turn role, timing, length and
  latency are enough to diagnose latency and stalls. Paraphrase a symptom rather than
  quoting the user.

The digest is safe to paste into Slack or Linear as rendered.

## Edge cases

- **Incident older than ~10 days** → vendor detail is past retention. The digest says
  so; do not report it as "the call never happened".
- **The session lists several calls** → normal, and usually the finding. A reconnect
  mints a new vendor record under the same Aura `conversationId`, so the digest reports
  the whole set with a reconnect count. Latency spans every call; a ⚠️ row is a call
  that ran past 60s with no agent turn at all, which is the zombie-session shape
  ([ENGMOB-2781](https://linear.app/aura-health/issue/ENGMOB-2781)) rather than a quiet
  user — 61 of 69 genuinely empty calls in a two-day org-wide window were under 30s.
- **A call renders "in progress"** → it was still live at fetch time, so its duration
  and turn count are not final. Re-run rather than reading it as a dead call; a live
  record otherwise reports `0s / 0 turns`, which looks identical to one.
- **Given a UID but no conversation id** → the window can hold several unrelated Aura
  conversations, and treating them as one session would corrupt the reconnect count and
  the aggregates. The digest narrows to the largest session and says how many it saw;
  pass `--conversation` to pick a different one.
- **No vendor conversation matched** → read *which* miss the digest reports, because three
  different outcomes wear that phrasing and only one of them is a finding. An **exhausted**
  search really did look at every candidate in the window. A **capped** one stopped at
  `--max-detail-fetches`. A **page-limited** one ran out of list pages before reaching the
  end of the window. The last two are not evidence of anything — narrow with `--at`, or
  raise the cap, and run it again. Never restate either as "no such call".
- **Digest says the server-side user filter was not in effect** → the vendor leg still
  produced a correct answer (identity is decided on the detail record, not on the list
  call), but it walked the whole workspace to get there and may have hit the cap. The
  `user_id` list param has most likely been renamed upstream — see
  `scripts/elevenlabs.py`. ElevenLabs ignores unknown query params silently, so this
  surfaces as slowness, never as an error.
- **LiveKit-path incident** → this skill's vendor leg does not apply. Use
  `eng:analyze-livekit-latency` / `eng:investigate-livekit-logs`.
- **Conversation id only** → vendor leg only; ask for the UID to unlock the rest.
- **Re-run** → read-only and side-effect-free, so re-running is always safe and
  returns the same verdict for a window that has stopped changing.

## References

- [`references/signals.md`](references/signals.md) — the cross-source joins, the
  latency metric table and thresholds, the five traps, and the privacy rules.

## Setup

```bash
python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/investigate-voice-issue/scripts/requirements.txt
```

Tokens live in `~/.claude/settings.json` under `env` (restart Claude Code after
editing): `ELEVENLABS_ADMIN_API_KEY`, `BUGSNAG_TOKEN`, `GCP_KEY`. None of the three
services has an MCP server, so this skill is API-first by necessity — see
`/CLAUDE.md` → "Tokens are soft requirements" → "API-first by necessity".

`scripts/gcp.py`, `scripts/normalize.py` and `scripts/bugsnag.py` are byte-identical
copies of their canonical sources (skills are self-contained per Architecture
principle 1). Edit the canonical first, then propagate — see `/CLAUDE.md`.
