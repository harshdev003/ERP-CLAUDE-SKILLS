# QA card template and planning rules

Read by `qa-linear-task` in Step 2. The body below is the shape of ENGMOB-2908, the card
this skill was modelled on.

## Body layout

```markdown
## What

QA for [PR #n](url), which <one sentence on the change>. After the PR, <the new behaviour>.

## Why

<What the change touches, and why unit tests are not enough: real mic, audio session,
network, device, backend.> **Expected result everywhere: "same as production today"**
<unless the change is meant to alter behaviour — then say what changes, and where>.

## Setup

* Build `<branch>` to <simulator / physical device> (DEBUG).
* <Launch arguments, flags, feature-flag / experiment arm to force, test account shape.>
* Analytics from DEBUG land in **<Mixpanel project>**. Filter on your `$user_id`.

## Test cases

### 1. <Area — e.g. Launch and baseline>

- [ ] 1.1 <Action> → <expected result>. <Why this case exists, if not obvious.>
- [ ] 1.2 …

### 2. <Area>

- [ ] 2.1 …

## Done when

* Every case above passes, or has a filed QA Feedback card that the team agrees is not a
  blocker.
* Only then does PR #n come out of draft (and <TASK-ID> move to In Review).

## Resources

* PR: <url>
* Parent: <TASK-ID>
```

## The case line format is a contract

`qa_card.py` parses exactly this, so don't vary it:

```
- [ ] <id> <text>
```

- `<id>` is dotted and numeric (`2.3`, or `2.3.1` for one case per device), with the section
  number first. Letters are not part of an id: `2.3a` does not parse. It must be unique
  on the card. Ids are how cases are ticked, moved and referred to in chat and on follow-up
  cards, so **never renumber** after the card is filed. Append new cases at the end of their
  section with the next free id.
- `-` or `*` bullet, one case per line. Put sub-steps inside the sentence, not as nested
  bullets.
- Tags go at the end in backticks when the runner isn't obvious from the section:
  `` `device` ``, `` `human` ``. Untagged means Claude can run it.

## Writing a good case

- **Action → observable result.** "Mute → Aurie stops hearing you and the mic icon shows
  muted", not "check mute works".
- **Anchor the expected result to something checkable**: production behaviour, an event with
  a property value, a backend call count, a screen. "Same as production" is the default for
  refactors and removals. Where you can't tell what production does, the case says *compare
  on a TestFlight / production build*, and that comparison is part of the case.
- **Cover the change, then its blast radius.** The diff decides the first group. The second
  covers paths that share code with the change: bootstrap, logout/reset, reconnect, teardown,
  analytics, error paths that can no longer fire (or now fire differently).
- **At least one regression-only case** on a neighbouring surface (e.g. text chat still
  works after a voice change).
- **Telemetry gets its own section** when the change touches events or engine or variant
  properties. Name the events and the property value you expect.
- **Include a forced-failure case** for every new or rewritten error path. Happy-path-only
  QA is how silent failure branches ship.

## Tagging by runner

| Tag | Meaning | Typical examples |
|---|---|---|
| *(none)* / `sim` | Claude runs it on the simulator or a local build | UI flows, audio by acoustic coupling (`say` + `afplay`), launch args |
| `backend` | Claude verifies it in logs or the API | token-mint calls, GCP logs, Aura API state |
| `analytics` | Claude verifies it in Mixpanel or Bugsnag | event fired with property X |
| `device` | Needs physical hardware | lock screen, phone call, Siri, real airplane mode, push |
| `human` | Needs a person's judgement or hands | voice quality, "feels right", a second account holder |

For an experiment change, [`experiment-cases.md`](experiment-cases.md) lists which cases the
simulator covers (StoreKit purchases, the device matrix for layout, Mixpanel by UID, a designer
comparison against the approved sheet), so `device` and `human` stay rare.

Planning is where the split is decided. The Step 4 mode recommendation and the Step 6
follow-up card both come from these tags.
