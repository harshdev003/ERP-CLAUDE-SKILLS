# QA cards for experiment changes

Read by `qa-linear-task` in Step 2 when the change ships behind an experiment flag (a new key
in `Experiments.swift`, an A/B arm, an onboarding or paywall variant). These rules come from a
real pass on a TestFlight build ([ENGMOB-2988](https://linear.app/aura-health/issue/ENGMOB-2988)),
and each one names a case or a setup step the card must carry.

## Setup: the experiment record

- **Confirm the record is splitting before any case runs** (`sim_experiments.py state` or
  `GET /experiments/<key>`): `active` must list more than one arm (`ab`). A single letter means
  it is **pinned** — every user gets that arm, a per-user `POST /experiments/<key>/set/<uid>/<arm>`
  returns 200 and is ignored, and the server reports the experiment `ended: true`, so the app
  registers no `Experiment: <key>` super property and analytics defects stay hidden. **Run
  analytics cases only with every arm active.**
- **Find out when the experiment is assigned before writing Setup**, from the code:
  - **pre-auth** (`.onboarding` context, `OnboardingExperimentBootstrapManager`): drawn with no
    user id, so no account can be put in an arm — reinstall until the screen shows the arm you
    need, and expect the draw to repeat after a logout;
  - **post-auth**: drawn at sign-up, so a new account per run and the random draw.
- TestFlight builds are often posted with "experiments are set to a for QA" (#qa-aurie-mobile).
  Put two lines on the card: a **Setup** step "`active` is the split", and a **Done when** gate
  "`active` is back to the split before the App Store release".
- The record's `minVersion` decides who is assigned. A build below it always renders control,
  so state the build version under test in Setup.

## Getting a test account into each arm

- **The account's arm and the event's arm are different things.** The Mixpanel profile property,
  written once at sign-up by `/set`, is the account's arm and the source of truth; an event's
  `Experiment: <key>` can carry a fresh random draw. Check both.
- **Returning-user cases need an account created before the experiment's `minVersion`**, or the
  version gate never shows. Accounts made on the build under test cannot exercise it.
- **Plan on the random draw, not a server force.** Experiments assigned right after sign-up
  leave no window to `set` the arm first. Use a new `test+…@aurahealth.io` account per run and
  budget **4–6 accounts** to cover both arms.
- **Read the arm back** with `GET /experiments/assignments/<uid>` after each sign-up, and record
  which account landed in which arm on the card's results comment.
- Short emails work well (`<redacted-email>`, …); check they are unused first.

## Purchases

- On the package paywall the main CTA opens the **aurie.ai web checkout with real Apple Pay —
  real money.** Never confirm there.
- Purchase through **"Or use in-app purchase"** (StoreKit). On the simulator use StoreKit
  testing; on TestFlight it is free and the sheet says "You will not be charged".
- TestFlight uses the tester's own Apple ID. After one purchase, that subscription is reused by
  every later account on the device, so only the first purchase per Apple ID is a fresh one.
  The simulator's StoreKit testing has no such carry-over.

## Analytics cases

- **Take event and property names from the code, not memory.** A plan said `valueProps_screen_5`;
  the real event was `AI Sign Up Onboarding Screen Seen` with
  `Screen = "Aurie Onboarding Explanation Screen 5"`.
- **Check every event, not just the exposure event.** Query `$all_events` broken down by
  `Experiment: <key>` for each test user; a correct exposure stamp can sit beside a super
  property that labels every other event.
- **Pre-login events carry no `$user_id`.** Filter them by `$app_version_string` or the device id.
- **Always add a case that `Experiment: <key>` is absent on events before the user sees the
  change.** The app registers it at assignment, which can come before exposure (lead-in screens,
  earlier onboarding slides). Exposure is the event stamped where the change renders.
- **Onboarding experiments** (assigned before login) draw a new random arm after logout or a
  logged-out relaunch. Add a case for it.
- `AI Login` fires on every launch with a saved login, not only on real logins. Count logouts
  with `AI Logout`.
- **Which Mixpanel project:** Debug and simulator builds → **Aura Pre Prod**; TestFlight and App
  Store builds → **Project Aura**; backend profile writes (e.g. the arm `/set` writes) → the
  production project. A simulator pass reads events in Aura Pre Prod and the account's arm in
  production.

## Cases that look right but cannot happen

- "Kill and relaunch between the quiz and the paywall": relaunching before sign-up restarts the
  quiz. Write the reachable case — **sign up, kill, reopen**.
- In-memory state (e.g. the onboarding archetype) is not cleared on logout. **Delete and
  reinstall between runs**, or one account inherits the previous account's state.

## Reaching a specific onboarding archetype

`UserArchetype.determine` maps the quiz answers. These paths land on each archetype:

| Archetype | Answers |
|---|---|
| Carrying a lot | Q1 "carrying a lot for a while", Q2 "Completely full" |
| Pushing through | "Someone recommended Aurie to me" / "Moderate — I'm handling things okay" / "I push through and deal with it later" / "I push through or go into overdrive" / "Work or performance pressure" |
| Deep processor | "Something specific has happened" / "Mostly full — …" / "It tends to linger or resurface" / "I overthink and replay things" / "Relationships" |
| Growth builder | "I want better emotional habits" / "Light — …" / "I process it and come back fairly quickly" (Q4–Q5 any) |

## Tag for the simulator first

An experiment pass needs no physical device unless the change is hardware-specific. Tag every
case the simulator can run as `sim`:

| Case | How it runs on the simulator |
|---|---|
| UI flow per arm | sim-test harness, new account per run, arm read back from the server — never a DEBUG launch-argument override, which is compiled out of the build that ships |
| Small-screen fit | the device matrix below — never a single phone |
| Purchase | StoreKit testing through "Or use in-app purchase" |
| Analytics | Mixpanel filtered on the test account's UID |
| Design matches the approved sheet | `product:designer` compares the screenshot with the approved sheet |

Keep `human` for genuine judgement the sheet comparison cannot settle, and `device` for
hardware the simulator lacks (push, lock screen, a phone call).

## Device matrix

Layout and visual cases run on every device in the matrix; flow and analytics cases need one
device. Each simulator is its own UDID.

| Run | Simulator | Why |
|---|---|---|
| Smallest | iPhone SE (375pt width) | the smallest supported width — truncation, wrapping, clipped CTAs |
| Standard | iPhone 16 or 17 | the size most users have |
| Largest | iPhone 16/17 Pro Max | spacing and alignment on the widest screen |
| Large text | the standard device at a large Dynamic Type size (`xcrun simctl ui <udid> content_size extra-extra-large`) | text that wraps into the next element or pushes the CTA off screen |

- **Tag the layout cases `sim` and suffix them with the matrix**, one case line per device, so
  each one is ticked on its own, with a third id level per device:
  `- [ ] 1.4.1 Headline fits without truncation on iPhone SE`, `1.4.2` on the standard device,
  `1.4.3` on Pro Max, `1.4.4` at large Dynamic Type.
- **Attach a screenshot per device** to the results comment as the evidence for each tick.
- **One runner per card still holds.** Run the matrix sequentially inside the one subagent, or
  move each device's cases to its own follow-up card (Step 6) and give each runner its own
  simulator.

## Run shape

The runner drives the app, checks the server arm and Mixpanel after each step, and ticks the
card one case at a time. When a person drives a device, Claude does the checking and ticking
in the same session.

- **Confirm the run's timeline before concluding.** A logout at the end of a session is easy to
  misread as a returning-user login; write down the order of actions per account first.
- **Check a reading of the code against the simulator's logs.** `ExperimentService` logs show
  every fetch and draw; when they disagree with what the code seems to say, the logs win.

## Closing the card

- A failure a person accepts as **not blocking** stays visible: the closing results comment
  lists it with its tracking card, and whoever reports the result (e.g. the loop's thread
  reply) repeats that list.
- A case worth checking on every release — for every onboarding experiment, "the arm appears
  only from exposure onwards and matches the account's arm after login" — goes to
  `qa:write-test-cases`.
