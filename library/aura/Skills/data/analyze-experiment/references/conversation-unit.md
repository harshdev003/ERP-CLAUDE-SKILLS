# The unit of analysis: what counts as a conversation

Companion to Step 5.8 and Step 6. Covers Aurie's canonical voice-conversation definition, the
merge rule behind it, and how to confirm a client-measured arm gap against backend records.

## Canonical definition (Aurie voice)

> A **voice conversation** is one client voice start — `AI Conversation Session Started` with
> `Current Conversation Mode == "Voice Mode"`, restarts within **5 minutes** merged into the
> first — whose window contains **at least 3 `coach-prompter` turns** in the backend `llmCall`
> ledger. The window runs from the start to the next start or +60 minutes, whichever is first.

Both halves earn their place. Merging removes reconnects the client counts as new conversations
(~24% of raw starts). The turn floor removes husks — a session that connected and produced
nothing. Without the floor, `>=1 conversation` counts a user who heard silence; without the
merge, one conversation with a dropped connection counts as two or three.

State the definition inline in the readout. "Conversation", "session" and "active user" each have
several defensible readings, and a comparison that switches between them silently reports a
definition artifact as a finding. Where a different unit is wanted (text included, or a stricter
depth floor), say so and keep it fixed across every arm and every rerun.

Depth tiers worth reporting alongside it, when engagement is the primary metric: **real** (>=3
turns, above), **deep** (>=10 turns), and **husk** (0 turns). Husk rate is a product signal in its
own right — on `aiLiveKitIntegration4` it sat at 13-17% on all four arms.

## The merge rule

`AI Conversation Session Started` fires again when a voice session reconnects, so the raw event
counts restarts as conversations — 33% of consecutive starts land within five minutes of the
previous one, 21% within sixty seconds (aiLiveKitIntegration4, 671 gaps). Collapse starts
separated by less than **5 minutes**, which removes ~24% of counted sessions. A reconnect is
rarely instant: the user reopens the app and re-enters the conversation, so the window has to
cover that recovery, and a minute-wide one leaves most of it counted twice. The cost is that a
genuine second conversation begun within five minutes merges into the first — rarer than the
restarts the window catches.

Two constraints on how. The merge needs per-user timestamps, so it runs on the Step 5.6 per-user
JQL pass — whose template returns `sessionTimes` for exactly this — rather than on aggregate
segmentation; an aggregate-only run reports the engagement figure as a restart-inflated session
count, never as a conversation count. And do **not** merge on the agent's `conversationId`: 23.8%
of those ids serve more than one session, 12.9% of same-id pairs are an hour or more apart, and
the mobile client reuses an id well beyond a single sitting, so that key collapses genuinely
separate conversations. The gap is the only sound signal — the event carries no session or
conversation id of its own.

Report the merge's effect (`raw starts -> conversations, N% removed`) so a reader can see the
correction rather than infer it, and check that the arm ordering survives a sensitivity sweep
over the window (0 / 1 / 5 / 15 / 60 min) before reading anything off a per-user conversation
count. A `>=1` rate is invariant to the window by construction — merging never takes a user from
one conversation to none — so an arm gap in `>=1` can never be a merge artifact.

## Backend truth

**Client instrumentation is variant-asymmetric until proven otherwise.** A client event's emitter
can sit on a code path only one arm reaches, so a per-arm gap in it may be an emitter difference
rather than behaviour. Two checks before reporting any client-measured gap:

1. **Find the emitter's call site** and confirm both arms reach it. Read the guard conditions, not
   just the file — an emitter gated on a UI state (`state != .error`) fires for one arm's users and
   not the other's once a failure has occurred, without either arm's code differing.
2. **Confirm against a backend record** written by one shared code path. For conversation turns
   that is the IntelligenceService `llmCall` ledger: BigQuery `aura-main.llm.llm_call` (by `createdAt` window and `userId`), one row
   per LLM call, `GCP_KEY`. Neither voice SDK touches it. Reader module:
   `plugins/data/skills/report-aurie-conversations/scripts/ledger_reader.py` (`walk_ledger`,
   keyset-paginated; `prompterName=coach-prompter` for conversation turns).

**Prefer a metric defined by the ABSENCE of a success signal.** "Never reached a successful
session" cannot be manufactured by a classification asymmetry, because no classifier runs — a
mis-labelled failure still leaves the user with no success event. A metric defined by the presence
of a *failure* signal can be, and will be wrong in whichever direction the two code paths differ.

On `aiLiveKitIntegration4` this distinction decided four findings. A per-attempt failure-rate gap
(LiveKit 3.9% vs ElevenLabs 0.4%) collapsed once it emerged that one SDK's connect errors fell
through an unrecognised-reason default and were never classified as failures at all. A
session-end-duration gap collapsed for the same reason — the end event fires only on user-driven
exits, at different rates per engine. The two that survived were both absence-defined or
backend-defined: users with no successful session (9.6% vs 1.7%, p=5e-7) and backend turns per
active minute (1.85/1.89 vs 3.09/3.09).

## Joining Mixpanel to the backend ledger

**Mixpanel JQL `event.time` is in the project timezone, not UTC.** Aura's project is Pacific, so a
JQL timestamp runs behind the matching UTC `createdAt` by the project's offset **at that instant** —
7 hours under PDT, 8 under PST. Never hardcode one of them. A one-hour error against a 60-minute
attribution window pushes a conversation's turns outside it and reproduces the near-total-miss
failure this section exists to prevent, and a window spanning a DST changeover needs the offset
resolved per event rather than once per run.

**Derive the offset by sweeping it, on every run.** Try -12h to +12h and take the peak attribution
rate; the right offset stands far clear of its neighbours. On `aiLiveKitIntegration4` (September,
PDT) the peak sat at +7h with 78-86% of turns matched against ~5% everywhere else. A sweep whose
peak is shallow, or lands somewhere other than the project's expected offset for that date, is a
reason to stop rather than a number to use.

**A uniform miss rate across arms that should differ is a clock or join bug, not a finding.** The
first run of this join attributed almost nothing on all four arms, which read as a 95% husk rate.
Identical failure on arms that differ elsewhere is the tell.

**Calibrate the window against ground truth before reading anything off it.** Where one arm's
backend rows carry a marker the others lack, that arm measures the window's accuracy for all of
them. On `aiLiveKitIntegration4`, LiveKit voice turns are stamped `platform=livekit` while every
ElevenLabs voice turn is `platform=mobile`, indistinguishable from text — so the LiveKit arms gave
99.3% recall, 17.1% text contamination, 93% precision, and that is the error bar on every arm's
figure. Report those three numbers with the metric.

**Do not compare a per-user rate whose denominator is a client event against a backend numerator
without checking coverage both ways.** Users can appear in one source and not the other in both
directions: a client session with no backend turns (husk), and backend turns with no client
session (the client event was dropped). Count both and report them.
