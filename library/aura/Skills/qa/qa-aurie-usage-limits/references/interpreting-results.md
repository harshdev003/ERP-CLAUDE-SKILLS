# Interpreting usage-limit QA results

The ledger numbers are the evidence. Logs are supporting material at best (see the
log traps in SKILL.md step 7). Every caveat below is a way a plausible reading of
the numbers turns out to be wrong.

## How the accrual works, in one paragraph

A voice call arms an accrual at agent-ready. A ticker commits every 45 s, charging
`elapsed − committed` where `elapsed` is wall clock since the call started — so the
total is derived from an absolute anchor, never accumulated from per-tick deltas.
Each commit fans out to **both** ledgers: the free lifetime counter
(`voiceUsedSeconds`) and the package budget (`voice.dailyUsed` / `weeklyUsed`).
Teardown flushes whatever is left and stops the ticker.

Two consequences worth holding onto:

- The total **can never exceed** wall clock since the accrual armed. A sustained
  faster-than-realtime rate is arithmetically impossible with one accrual running.
- The total **lags** wall clock by up to one tick interval, because it only moves
  on commits.

## The signature that matters: whole tick vs partial

This rule separates a real teardown flush from a skipped one, and it needs no
logs.

Teardown calls the commit **before** cancelling, so it charges the leftover since
the last tick — a value that is essentially never a clean 45 s.

- **Charges land on ~45 s multiples throughout, with no partial anywhere** →
  teardown never ran. The ticker is simply still going.
- **A partial charge appears, then flat** → teardown ran and flushed. Correct.
- **One more whole ~45 s after the end, then flat** → the liveness probe caught it
  on the next tick rather than teardown flushing promptly. Recovered, but the
  teardown path is still broken; worth a bug.
- **Nothing at all after the end** → teardown ran within a hair of the last tick,
  so the remainder was sub-second. Fine.

Corollary: if the ledger goes flat with **no** extra charge, the probe cannot have
been what stopped it — the probe can only evaluate on the next tick and would have
flushed that whole gap. That is how you tell "teardown fixed" from "probe covered".

## Pass / fail

**Pass** — after the call ends, both ledgers settle and stay settled across at
least three samples spanning two tick intervals, and the settling charge is a
partial (or nothing).

**Fail** — continued ~45 s increments while the message counter is frozen. The
frozen message counter is the control: it proves no conversation is active while
voice climbs. If both climb, there is a live call and billing is probably correct.

## Reading a mid-series change correctly

Not every movement is an accrual tick. Tell them apart by shape:

| Observation | What it is |
|---|---|
| Both ledgers move by the same amount | An accrual tick — they always fan out together |
| **Only** the package ledger moves, and lands on a round whole-second value | A `/ai/usage/stats` reconcile replacing the local total |
| Package ledger moves **down** | Also a reconcile; the backend revised it |
| One ledger moves alone by an amount the other then matches | Read lag, not a divergence — see below |

The free counter has no backend to reconcile against, which is why a
package-only, round-numbered change is unambiguous. Round versus non-round is the
sharpest tell available: local accrual charges `elapsed − committed`, which is
essentially never a whole second, so a whole-second multiple came from the backend.

## One ledger moving alone is usually read lag

The free and package records live under **separate `UserDefaults` keys**, and
`cfprefsd` flushes them to the plist independently. A single pull can therefore
catch one key updated and the other not, which reads exactly like the fan-out
having broken on one ledger.

It resolves on the next sample: the lagging counter catches up and the two agree
again. So treat a one-sided move as **provisional** and confirm across two
consecutive reads before calling it a divergence. A genuine divergence persists.

This is the most common false positive in a round — it looks like the most
alarming possible finding, which makes it tempting to report immediately.

## Sampling artefacts — do not trust raw deltas

**`UserDefaults` flushes lazily.** A read can return a stale snapshot. A single
flat sample proves nothing; span at least two tick intervals before concluding.

**Container pulls have variable latency** — 3 s to 25 s over Wi-Fi, and stalls of
several minutes happen. `read_usage_ledgers.py` stamps `readAt` *after* the pull
returns, so a sample's data predates its printed timestamp by an inconsistent
amount, and **inter-sample deltas are unreliable**. Deltas can show 45 s of charge
across a 33 s gap, an apparent 1.36× over-bill, while the running totals show
correct 1:1 billing. That is read latency, not billing.

Keep that ordering if you write your own reader: stamping before the pull inverts
the relationship, and a total that legitimately arrived after the stamp then looks
like it predated the event that produced it.

So: reason from **running totals against a known anchor** (call start time), not
from consecutive differences. Back-solving the anchor from several samples and
seeing it drift is itself a sign of variable lag rather than of a billing bug.

**Sample deltas can also hide a big jump.** A long stall followed by a catch-up
read looks like one enormous increment. Check it against wall clock before
believing it.

## Behaviours that look like bugs but are not

- **Accrual continues while backgrounded or screen-locked.** `UIBackgroundModes`
  includes `audio` and `voip`, so iOS keeps the process alive during a call. The
  call really is running; billing is correct. Confirm via the message counter
  still incrementing.
- **A cap binds mid-call and ends the session.** Intended: incremental accrual
  exists so a cap can bind during a session rather than only at teardown.
- **The daily bucket resets at local midnight** while the weekly persists. Both
  are clock-derived; a run spanning midnight will show daily drop to zero.
- **The free lifetime counter never resets and cannot be reconciled.** It also
  gates nothing while the shipped free voice cap is `0`.

## Known open gaps

Things a normal round does **not** cover — state them in the report rather than
implying full coverage:

- **Engine coverage.** The live `aiLiveKitIntegration<N>` experiment picks ElevenLabs
  or LiveKit per user (variant `a` → LiveKit; `surfaces-and-flows.md` → Engine coverage
  names the current version), and their teardown paths differ materially. A pass on one
  is not a pass on the other. The reliable tell is the source file in each log line —
  `LiveKitVoiceService*` plus `AudioSessionManager — Caller: LiveKitVoiceService`,
  against `ElevenLabsVoiceService*` — alongside the router's own
  `aiLiveKitIntegration<N> resolved: variant '<x>' → …`.
- **Surface coverage is also read off those file names.** `LiveSessionViewModel*`
  against `QPVoiceModeViewModel` / `QuickProcessingViewModel*` is what distinguishes
  a live-session call from Quick Processing. They run different entry gates and
  different mid-call observers, so a ledger series on its own cannot tell you which
  surface a verdict belongs to — and a verdict filed against the wrong one reads as
  coverage the code never received.
- **Process suspended mid-call, then resumed.** Unreachable while a call holds the
  audio session. To reach it the call must die while backgrounded. Because the
  charge is derived from wall clock, the first tick after resume would bill the
  whole suspended gap — untested.
- **Onboarding voice.** It arms the same accrual but has no usage-limit touchpoint
  of any kind — no entry gate, no observer, no charge. A cap set anywhere does not
  bound it. Quick Processing, by contrast, is fully instrumented on both its chat
  and voice surfaces; see `references/surfaces-and-flows.md` for what each surface
  wires up.
- **Surfaces not driven this round.** Six reach the limiter and each has its own
  gates, so name the ones covered rather than reporting "the limiter passes".
- **A stuck accrual suppresses the reconcile.** The stats refresh skips while a
  call is in progress. If that skip is keyed on the accrual merely existing rather
  than on the call being live, a leaked accrual also blocks the correction that
  would fix the number it inflated — so the total only self-heals after the
  process dies. Verify a reconcile actually runs after a call.
- **The free voice counter never self-heals.** It is local-only, so phantom
  seconds from an accrual leak stay on the account permanently. Harmless while the
  free voice cap ships at `0`, but it means a polluted QA account gives a
  misleading baseline the day that cap is switched on — and the pollution is not
  evidence of a live bug.
