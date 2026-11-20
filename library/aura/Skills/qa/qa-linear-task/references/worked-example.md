# Worked example: ENGMOB-2906

The hand-run pass this skill was built from (2026-09-24).

## The task

[ENGMOB-2906](https://linear.app/aura-health/issue/ENGMOB-2906): remove the ElevenLabs
Conversational AI engine from the iOS app after `aiLiveKitIntegration4` shipped LiveKit to
100% of voice traffic. PR [AuraConsumerApp#1640](https://github.com/AuraHealth/AuraConsumerApp/pull/1640)
rewrote connect, reconnect, teardown and analytics, and 1970/1970 unit tests passed.

## Step 2: planning

The expected result was **"same as production today"**, because production users were
already 100% on LiveKit. Seven areas came from the diff and its blast radius:

1. Launch and baseline: the voice service is now built at bootstrap. Plus a text-chat
   regression case.
2. Live session voice: start, turns, mute, end (the done-screen quote must stay silent),
   second session, voice ↔ text.
3. Interruptions and reconnect: background, lock, airplane mode, phone call, mic denied.
4. Quick Processing voice, including "dig deeper" and a text-only comparison against
   TestFlight.
5. Incognito and account switch: the logout reset hook was removed, so check for leaks
   across accounts.
6. Usage limits (`-ENABLE_USAGE_LIMITS`), routed to `qa:qa-aurie-usage-limits`.
7. Telemetry: `Voice Engine = LiveKit` on every connection event, a forced
   `AI Connection Failed`, Bugsnag metadata, and **zero** `/ai/elevenlabs/conversation/token`
   calls in the backend.

## Step 3: the card

[ENGMOB-2908](https://linear.app/aura-health/issue/ENGMOB-2908): `QA: remove ElevenLabs
voice engine (PR #1640) — device test pass`, label `QA`, parent ENGMOB-2906, 28 cases.

## Step 5: execution (this session, simulator)

20 of 28 passed on the simulator, with speech delivered by acoustic coupling. Telemetry
(7.1, 7.3, 7.4) was verified in Mixpanel Pre Prod, Bugsnag and backend logs.

One unrelated, pre-existing defect surfaced: a foreground push with no destination disables
the mic button until relaunch. It was filed as its own bug,
[PROD-6536](https://linear.app/aura-health/issue/PROD-6536), and noted on the follow-up card
as a heads-up rather than logged as a failure of this change.

## Step 6: the split

The 8 open cases (3.2, 3.3, 3.4, 5.1, 5.2, 6.1, 6.2, 7.2) went to
[ENGMOB-2916](https://linear.app/aura-health/issue/ENGMOB-2916): `QA: remaining voice cases
for PR #1640 (device + usage limits)`. Its sections were **Simulator (Claude)** for 6.1 and
6.2, and **Device (Jerome + Claude)** for airplane mode, the phone call, the real lock screen
and the forced failure. Case ids were kept. 3.2 was struck on the follow-up with the reason
the simulator couldn't lock the screen meaningfully.

What the hand-run pass missed, and the skill now does: the 8 moved cases stayed as plain
open boxes on ENGMOB-2908, so its counts read 20/28 done with 8 open, when in fact none were
open. `qa_card.py move` strikes them and points them at ENGMOB-2916, so the original card
reads accurately.

## Step 7: gate

The pass is complete only when `checklist` reads `open: 0` on both ENGMOB-2908 and
ENGMOB-2916, and PROD-6536 is agreed as not blocking.
