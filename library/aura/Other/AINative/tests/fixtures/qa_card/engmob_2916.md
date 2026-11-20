## What

The cases from [ENGMOB-2908](https://linear.app/aura-health/issue/ENGMOB-2908/qa-remove-elevenlabs-voice-engine-pr-1640-device-test-pass) that the first sim pass did not cover, for [AuraHealth/AuraConsumerApp#1640](https://linear.app/aura-health/review/featvoice-remove-the-elevenlabs-voice-engine-engmob-2906-fcb67990f6f1) (ElevenLabs engine removal, LiveKit only). Expected result everywhere: **same as production today** (production users were already 100% on LiveKit).

Split by who can run it. Claude runs the simulator cases on its own dedicated simulator (speech by acoustic coupling, `say` + `afplay`). The device cases need Jerome with a physical iPhone, with Claude reading logs, Mixpanel and Bugsnag live.

## Simulator (Claude)

- [X] 6.1 Free user with `-ENABLE_USAGE_LIMITS`: the voice connect gate / Limited Free intro modal behaves as in production.
- [X] 6.2 Voice usage accrues during a call and the package voice budget goes down. Cap reached **mid-call** ends the call with the limit sheet (needs an account close to its cap; if none can be set up, it moves to the device list).
- [ ] ~~3.2 Lock the screen mid-call (sim)~~ → moved to the device list (the sim can only lock via the Simulator menu, and background/foreground already passed as 3.1).

## Device (Jerome + Claude)

- [ ] 3.3 Airplane mode on for \~5 s mid-call, then off → reconnect state shows and the session re-joins (or ends with a clear error), no stuck UI.
- [ ] 3.4 Take/decline a phone call, or trigger Siri, mid-call → audio recovers afterwards and the mic works after unmute.
- [ ] 7.2 Airplane mode **before** tapping voice → `AI Connection Failed` fires with a sensible reason and `Voice Engine = LiveKit`; a clear error, no stuck "Connecting…".
- [ ] 3.2 on a real lock screen, if the sim version was not conclusive.

## Setup (device)

* Build `jerome/engmob-2906-remove-elevenlabs-voice-engine` to the iPhone (DEBUG). Analytics land in Mixpanel **Aura Pre Prod**.
* Heads-up from the sim pass: a foreground push with no destination disables the mic button until relaunch ([PROD-6536](https://linear.app/aura-health/issue/PROD-6536/voice-mic-button-stays-disabled-after-a-foreground-push-with-no)). If voice suddenly won't start, relaunch; it is not this PR.

## Done when

Every case passes or has a filed card the team agrees is not a blocker. Results go on this card and [ENGMOB-2908](https://linear.app/aura-health/issue/ENGMOB-2908/qa-remove-elevenlabs-voice-engine-pr-1640-device-test-pass).

## Resources

* PR: [AuraHealth/AuraConsumerApp#1640](https://linear.app/aura-health/review/featvoice-remove-the-elevenlabs-voice-engine-engmob-2906-fcb67990f6f1)
* First pass results: comment on [ENGMOB-2908](https://linear.app/aura-health/issue/ENGMOB-2908/qa-remove-elevenlabs-voice-engine-pr-1640-device-test-pass)