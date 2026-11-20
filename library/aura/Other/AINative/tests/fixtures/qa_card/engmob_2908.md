## What

Device QA for [PR #1640](<https://github.com/AuraHealth/AuraConsumerApp/pull/1640>), which removes the ElevenLabs Conversational AI engine, the engine A/B router and the ElevenLabs SDK. After the PR, every voice session goes straight to `LiveKitVoiceService`.

## Why

This PR rewrites the voice code path: connect, reconnect, teardown and analytics. Unit tests pass (1970/1970), but a real mic, audio session and network are needed to prove voice still works end to end. **The expected result everywhere is "same as production today"**: production users were already 100% on LiveKit, so nothing a user sees should change.

## Setup

* Build the PR branch `jerome/engmob-2906-remove-elevenlabs-voice-engine` to a **physical iPhone** (DEBUG) for the device-only cases. Most cases can also run on the simulator: speech reaches it by acoustic coupling (`say` + `afplay` through the Mac speakers).
* DEBUG builds disable usage limits by default. Use the `-ENABLE_USAGE_LIMITS` launch argument only for section 6.
* Analytics from DEBUG builds land in **Mixpanel Aura Pre Prod**. Filter on your `$user_id`.

## Test cases

### 1. Launch and baseline

- [X] 1.1 Cold launch (fresh install) → onboarding / home loads normally, no crash. The voice service is now built at app bootstrap.
- [X] 1.2 Warm launch while already logged in → home loads, no crash.
- [X] 1.3 A text chat still works end to end (send a message, get a reply). Regression check only.

### 2. Live session voice (Home → voice)

- [X] 2.1 Start voice from Home → mic permission prompt on a fresh install → connects, Aurie greets with the first message.
- [X] 2.2 Talk for 3+ turns → Aurie replies with audio, captions/transcript update, and the orb reacts while Aurie speaks.
- [X] 2.3 Mute → Aurie stops hearing you and the mic icon shows muted. Unmute → the conversation continues.
- [X] 2.4 End the call → the done screen appears and the quote is **not spoken aloud**. Rating / encourage-rating still shows as before.
- [X] 2.5 Start a second voice session in the same app run → it connects cleanly, with no stale transcript and no duplicate greeting.
- [X] 2.6 Switch voice → text mid-conversation, then back to voice → the session continues and no messages are lost.

### 3. Interruptions and reconnect

- [X] 3.1 Background the app for \~10 s mid-call, then return → the session is still live or ends gracefully, with no dead mic.
- [ ] 3.2 Lock the screen mid-call, then unlock → same as 3.1.
- [ ] 3.3 Airplane mode on for \~5 s mid-call, then off → the reconnect banner/state shows and the session re-joins (or ends with a clear error), with no stuck UI.
- [ ] 3.4 Take/decline a phone call (or play a Siri prompt) mid-call → audio recovers afterwards and the mic works after unmute.
- [X] 3.5 Deny mic permission (Settings → reset) → a voice start shows the permission path, with no crash.

### 4. Quick Processing voice

- [X] 4.1 Start QP in voice → connects on the QP agent (short wrap-up after 2–3 turns).
- [X] 4.2 Finish QP voice → the affirmation / done screen shows and is **not spoken aloud**.
- [X] 4.3 QP "dig deeper" in voice → connects on the coach agent, and the done sheet is silent.
- [X] 4.4 QP in **text only** (never tap voice) → affirmation behaviour matches production (compare on a TestFlight build: if production speaks it, the branch must too).
- [X] 4.5 Sentinel done screen (`qpNewDoneScreen` arm a, if your account is on it) → the voice close ends the session cleanly and the done screen loads its copy.

### 5. Incognito and account switch

- [ ] 5.1 Toggle incognito during a live voice call → the session restarts under the new conversation and the temporary-chat toast shows.
- [ ] 5.2 Same in QP voice.
- [X] 5.3 Log out → log in as a **different** account → start voice → connects, and nothing from the previous user leaks (transcript, conversation id). The logout reset hook was removed.

### 6. Usage limits (launch with `-ENABLE_USAGE_LIMITS`)

- [ ] 6.1 Free user: the voice connect gate / Limited Free intro modal behaves as in production.
- [ ] 6.2 Voice usage accrues during a call (remaining minutes / package voice budget go down), and hitting the cap ends the call with the limit sheet.

### 7. Telemetry

- [X] 7.1 Mixpanel Pre Prod: `AI Connection Starting`, `AI Connection Succeeded`, `AI Voice First Audio Received`, `AI Connection Started`, and the conversation started/ended events all carry `Voice Engine = LiveKit`.
- [ ] 7.2 Force a failure (e.g. airplane mode before tapping voice) → `AI Connection Failed` fires with a sensible reason and `Voice Engine = LiveKit`.
- [X] 7.3 Bugsnag (if anything reports during the pass): the conversation metadata shows `Voice Engine: LiveKit`, and no ElevenLabs errors appear.
- [X] 7.4 Backend: the test user's sessions show LiveKit token mints (`/ai/livekit/conversation/token`) and **zero** `/ai/elevenlabs/conversation/token` calls.

## Done when

* Every case above passes, or has a filed QA Feedback card that the team agrees is not a blocker.
* Only then does PR #1640 come out of draft (and ENGMOB-2906 move to In Review).

## Resources

* PR: [https://github.com/AuraHealth/AuraConsumerApp/pull/1640](<https://github.com/AuraHealth/AuraConsumerApp/pull/1640>)
* Parent: ENGMOB-2906