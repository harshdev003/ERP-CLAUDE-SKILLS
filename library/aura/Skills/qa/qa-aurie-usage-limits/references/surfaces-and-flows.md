# Surfaces to QA, and how each one is wired

Six surfaces reach the limiter. They do **not** share one code path: each charges
the ledgers from its own call site and enforces caps through its own gates, so a
pass on one surface says nothing about any other. Pick the surfaces for the round
deliberately and state in the report which ones you covered.

## The matrix

| Surface | Charges messages | Charges voice time | Entry gate | Pre-send gate | Mid-call observer |
|---|---|---|---|---|---|
| Live-session chat | `recordMessageAndHandleCrossing()` | — | — | ✅ `blockReason` → `presentUsageBlock`, `return` | — |
| Live-session voice | one per user turn | shared accrual | `checkPackageOnVoiceConnect()` | — | `observePackageUsageDuringVoice()` (package), `observeVoiceUsageLimit()` (free duration — inert) |
| History chat | `applySendGates()` | — | — | ✅ returns `false`, aborts | — |
| Quick Processing chat | `recordMessageAndHandleCrossing()`, 3 call sites | — | `qpLockReason` (premium + 5-message gate only) | ❌ none | — |
| Quick Processing voice | one per user turn, deduped on `eventCanonicalId` | shared accrual | `passesUsageGates()` + `activateVoiceMode` | — | `observePackageUsage()`, `observeVoiceUsageLimit()` |
| Onboarding voice | — | shared accrual | — | — | — |

Voice **duration** needs no per-surface wiring: the accrual is armed and stopped
inside the shared LiveKit / ElevenLabs handlers, so every surface that opens a
call accrues through the same path. Voice **turns**, message sends, and every gate
are per-surface.

Two entries in that table are load-bearing for scoping a round:

- **Quick Processing chat has no pre-send gate.** The cap stops the send that
  *crosses* it and nothing after: `recordMessageAndHandleCrossing()` charges
  before it checks, and its `wasBlocked` guard suppresses the modal once the user
  is already over. So an over-cap QP send is charged and delivered silently.
  Tracked as PROD-4437 — expect it until that closes, and don't read it as a fresh
  finding.
- **Onboarding voice has no touchpoints at all.** It accrues duration and consults
  nothing. A cap set anywhere does not bound it.
- **Two mid-call observers exist and only one of them fires.**
  `observePackageUsageDuringVoice()` watches `packageLimitHit` and is what actually
  ends a call on a package cap. `observeVoiceUsageLimit()` watches
  `isFreeVoiceLimitReached`, which its own doc marks *"Always `false` as shipped"* —
  `voiceCapSeconds` is `0` and the free tier is premium-exempt regardless — so it is
  unreachable in any shipped build. Attribute a mid-call termination to the package
  observer; reaching the free one at all requires the config change described under
  "Which tier applies".

## Which tier applies

- **Package (paid-tier) caps** are *not* premium-bypassed — they are the paid
  tiers' own limits. The limiter is armed only for a user on a paid package, so a
  free account exercises none of it.
- **Free-tier caps** are premium-exempt by construction
  (`isFreeTierEnforced: !isPremium`), and the shipped free voice cap is `0`
  (measured, not enforced). The free lifetime *message* counter is the one free
  cap that binds.
- On Quick Processing the free branch is unreachable: `qpLockReason` routes every
  non-premium user to the paywall instead of QP, and premium is exempt from the
  free tier. QP therefore exercises package caps only.

So: **package caps need a paid test account; free message caps need a non-premium
one.** A round cannot cover both on the same user.

**A voice cap can only be tested on a package.** Two things stand between a free
account and an enforced voice cap, and neither is worth fighting:

- `UsageLimitsConfig.voiceCapSeconds` and `chatMessagesLifetime` are **production**
  constants, outside `#if DEBUG` — `chatMessagesLifetime` is even documented as the
  same value in DEBUG and RELEASE. Editing them to run a test puts a shipping value
  in the working tree, where a `git add -A` ships it.
- Even edited, a non-zero `voiceCapSeconds` does not reach the persisted record:
  `setChatMessageLimit` re-reads and re-writes the stored record, clobbering the cap
  `loadUsageLimits()` had just rewritten from config
  ([PROD-4663](https://linear.app/aura-health/issue/PROD-4663)).

Package caps have neither problem — `debugPackageVoiceCapMs` and
`debugPackageMessageCap` live inside `#if DEBUG` and are applied on every
`configurePackageLimits`. Upgrade the account instead of editing the free tier.

**Reading the tier off the container:** no `packageUsage` key means the limiter was
never armed, because `configurePackageLimits` is only reached for a paid package.
That is the expected state for a free account, not a fault — and it is also why the
"do the caps match what I compiled?" check has nothing to compare against there.

## Park the caps you are not testing

Every cap — message and voice, daily and weekly — feeds one
`isAnyLimitReached`, and `packageLimitHit` is derived from it. The voice entry
gates read that single verdict, so **an exhausted message cap refuses a voice
call** and leaves every voice path unreachable. The reverse holds too.

Before a round, set the caps you are *not* testing far out of reach, and size the
one you are testing against the account's **existing** period totals rather than
as a round allowance. A QA account carries usage across sessions, so "20 minutes"
may already be spent.

Worked example — targeting the mid-call voice observer on an account at
`voice.weeklyUsed = 630_000`:

| Constant | Value | Reasoning |
|---|---|---|
| `debugPackageMessageCap` | `60` | daily messages out of reach |
| `debugPackageMessageCapWeekly` | `200` | weekly messages out of reach |
| `debugPackageVoiceCapMs` | `700_000` | 70 s above the existing weekly total |

70 s of headroom against a 45 s ticker means tick 1 lands under the cap and tick 2
crosses it, so one ~90-second call exercises accrual *and* the mid-call
teardown. Sizing the cap as a round 20 minutes instead would have needed a
10-minute call to reach the same assertion.

Read the persisted limits back after launching to confirm the build took — the
constants are compiled in, so a stale build is silent otherwise.

## Per-surface recipes

### Message cap on a chat surface

1. Read the baseline; compute remaining headroom on the binding bucket.
2. Send to one short of the cap, confirming each send moves package daily,
   package weekly, and the free counter **by one each**. All three moving together
   is the check that the single-commit fan-out reaches this surface.
3. Send once more — the crossing. The limit modal must appear on *this* send.
4. Dismiss the modal and send again. On a gated surface the send is refused, the
   counter does not move, and the modal returns. On QP chat it is charged and
   delivered (PROD-4437).

A text send must leave both voice buckets untouched; that is the control against
the ledgers being cross-wired.

### Voice cap mid-call

1. Park the message caps, size the voice cap to 1–2 ticks of headroom.
2. Start the call and let it run — do not hang up.
3. Each tick must move the free and package voice ledgers by the **same span**.
   Two discrete increments rather than one lump at teardown is what proves the
   accrual is incremental, which is the only reason a cap can bind mid-call.
4. At the crossing tick the call must end itself and present the modal.
5. Then idle, without starting another call, for two or more tick intervals — see
   the teardown scenarios in SKILL.md.

### Entry gate while already over a cap

Cheap to run immediately after a cap-terminated call, since the account is now
over: attempt another voice call. It must be refused before connecting. Worth
covering all three voice entry paths on QP — normal activation, the idle/error
retry button, and the incognito reconnect — because only the first passes through
the parent's early gate.

## Engine coverage

The live `aiLiveKitIntegration<N>` experiment decides ElevenLabs vs LiveKit per user
— variant `a` is LiveKit, anything else (including a missing assignment or a failed
fetch) is ElevenLabs. A Firebase rollout of `active: "a"` returns that variant for
**every** user regardless of enrollment, which is the cheapest way to pin a round to
LiveKit without writing per-user assignments.

**The experiment is re-cut under a new trailing version each time it restarts**, so
resolve the current one from `GET /experiments?limit=10000` (the name is the `id` field;
an undersized `limit` truncates silently) rather than pinning a version anywhere in this
skill. As of 2026-08-27 it is `aiLiveKitIntegration3` — `…2` ended 2026-08-06 and `…4` is
planned. This paragraph is the only place in the skill that names a version; everywhere
else says `aiLiveKitIntegration<N>` on purpose.

Their teardown paths differ materially. Record which engine ran and treat a pass as
covering that engine only.
