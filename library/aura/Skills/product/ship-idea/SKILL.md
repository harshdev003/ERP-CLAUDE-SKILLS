---
name: ship-idea
description: >
  Carry a built idea through the release gate: the merged PR is the release
  approval, so record the merge and the release it ships in, ask for the
  experiment, record the TestFlight build, and land it `shipped_dormant` —
  shipped with its flag off — when testing holds it back. Asks Viktor in #support-bot to create the experiment and
  replies in the idea's Slack thread. Use when the user says "ship this idea",
  "it's in TestFlight", "the PR merged", "approved for release", "we're not shipping this one",
  "iterate on it", "abandon it", or "create the experiment for {idea}". Default
  target is the Aurie Features Ideas database; pass `--data-source-id {id}` for the
  other loops.

metadata:
  version: "1.11.0"
knowledge:
  reads:
    - Guide/Index
    - Learnings/Loop/Aurie Features Ideas
    - Learnings/Experiments
    - Tools/Notion
    - Tools/Slack
  writes:
    - Learnings/Loop/Aurie Features Ideas
---

# ship-idea

## What this skill will never do

1. **It never merges a pull request.** It records that a human did.
2. **It never releases.** No TestFlight, no tag, no version bump. It records the version a human shipped.
3. **It never creates an experiment.** It *asks* Viktor to, and waits.
4. **It never moves a row backwards.** Every transition is forward-only and guarded on the step before it.

The fourth is the one that needs saying out loud. This skill's entire output is a
log somebody reads months later to answer "when did this ship, and what did it
do?" A row that skipped a step, or got rewound, still *reads* fine — which is
exactly why a wrong transition has to be refused at the point of writing rather
than noticed at the point of reading.

## The chain: loop → Viktor → AuraBot

Experiments are created by a structured command to `@AuraBot` in the **private
`#support-bot`** channel:

```
@AuraBot create experiment {name} with notes {notes} and variants {ab|abc|abcd}
  [and minimum version {x.y.z}] [and targeting platform ios] [and targetApp ai]
```

**This skill does not emit that command.** It asks **Viktor** (`U0AV303B4VB`) in
prose, in the same channel, and Viktor issues the AuraBot command *and* does the
documentation — Mixpanel dashboard and the AB sheet. One ask covers both, and
Viktor posts the outcome back into the same channel.

Addressing Viktor rather than AuraBot is a reliability choice, not a stylistic
one. A skill that emits a fixed grammar gets it subtly wrong eventually — a
dropped `and`, a variant list that disagrees with the notes — and a malformed
command to a parser fails **silently**: no experiment, no error, and a row that
polls forever. Viktor reads prose and answers when something is off, which turns
a stuck row into a question in a channel people watch.

Conventions, read off every real request in the channel rather than from a spec:

| Field | Rule |
|---|---|
| `targetApp ai` | Aurie. **Omitted entirely** for Aura — not set to something else. |
| `minimum version` | the release the flag shipped in (`5.0.0` Aurie mobile, `3.19.x`/`3.2x` Aura) |
| `variants` | `ab` / `abc` / `abcd` |
| `notes` | spells out every arm, and always names one `control` |

`#abtest-monitoring` is where Viktor *monitors* — it already does that for every
live experiment on its own cadence, so the loop asks for nothing there.
`#dev-viktor` is a personal path, not the shared one.

## The AuraBot fallback: `--via aurabot`

When Viktor is unavailable, `--via aurabot` skips it and emits the grammar
directly. It is also the likely eventual default — Viktor is a paid agent and
AuraBot is the primitive underneath it.

It is the fallback rather than the default because going direct gives up two
things, and the command says so on stderr every time rather than substituting
quietly:

- **The documentation half.** Viktor also builds the Mixpanel dashboard and the
  AB-sheet entry. Without it the experiment runs with nothing to read it in, so
  that has to be arranged separately.
- **The error channel.** AuraBot parses a fixed grammar; a malformed command
  produces no experiment and no complaint. Check the channel for a confirmation
  rather than assuming the post worked.

If Viktor is retired, the documentation step needs an owner before this becomes
the default — otherwise the loop ships experiments nobody can read.

## `--arms` is required past two variants

Auto-generated notes can only name `a` and `b`: nothing on the idea row says what
a third or fourth arm would be. `--variants abcd` without `--arms` is **refused**,
because an experiment with two undocumented arms cannot be interpreted from its
result weeks later — and by then the person who knew is not in the thread.

```bash
python3 $SCRIPT request-experiment ffe899463174 --variants abc \
  --arms "a: streak on Settings, b: control, c: streak on Home"
```

## Why REST and not the Notion MCP

Same reason as its siblings: every command resolves one row out of many via
`find_idea`, and `notion-search` can neither enumerate a data source nor filter
reliably on `Status`. Without `NOTION_TOKEN` the script exits 2 rather than
degrading — a partial read here resolves the **wrong row** and then writes a
status to it.

## The shape

`implementing` covers everything from the first commit to a build a person can
hold — PR, code review, merge, TestFlight, and every round the release gate sends
back.

Pull-request mechanics get no status of their own, deliberately. GitHub already
tracks whether a branch exists, a PR is open or a commit landed, and tracks it
better. What the loop needs to know is narrower: **is somebody building this, and
is it live.** Everything between those two facts belongs in the note and the
thread, where a person will actually read it.

```
approved → implementing ──[ RELEASE GATE — the PR was merged ]
                │                    │                        │
                │            changes requested          not approved
                │                    │                        │
                │              (stays implementing)    shipped_dormant
                ↓                                       │          │
         experiment_running                         iterate     abandon
                                                        │          │
                                                 implementing   abandoned
```

| Step | Command | Writes |
|---|---|---|
| The PR merged | `merged --post` | nothing — records the approval and the release it ships in |
| The build is ready to try | `testflight --version --post` | nothing — records the release version |
| A person approved it in the thread | `release [--release-post-url] --post` | nothing — records the approval, replies the verdict |
| QA found a bug in the build | `qa-bug --reason --post` | nothing — reopens the gate; the fix PR's `merged --pr` approves the next round and the request asks Viktor to raise the minimum version |
| A person said no | `unwind --reason --post` | `shipped_dormant`, replies which mode |
| Another round | `iterate` | `implementing` |
| Stop for good | `abandon --reason --file-cleanup --post` | `abandoned`, files + links the cleanup card, replies |
| Ask Viktor for the experiment | `request-experiment --post` | nothing — records the request, once |
| Viktor created it | `check-experiment` | nothing — records `Experiment created` and replies in the idea's thread with the link to the `#support-bot` request |
| Its minimum version is on the App Store, or in a `#releases` post | `check-experiment` | `experiment_running` — the minimum is the higher of the row's and the server record's `minVersion`, since users below the record's are never assigned |
| Released, but the record's `active` is one arm (pinned, e.g. for TestFlight QA) | `check-experiment` | nothing — records `Experiment pinned` and replies in the idea's thread once; it goes live when `active` is the split again |

### A status change replies in the idea's thread

The thread is where the team watched the idea move and answered both gates, so
every status change speaks there too. `release`, `unwind` and `abandon` take a
`--post` that replies the verdict — approved (optionally linking the existing
build / release post via `--release-post-url`), not-shipping-and-which-mode, or
abandoned — the same shape `testflight` and `announce` already use. The loop does
**not** re-announce a build or release to a channel; the release process already
posts to `#qa-…-mobile` / `#releases`, and `--release-post-url` links that post
rather than duplicating it. Without `--post` the row is written and nothing is sent
(a dry run); a row with no `Slack thread` is refused. A new bot reply is safe next
to the gate — the harvester flags bot replies, so one can never be read as a human
verdict.

### The merge is the release approval

The PR is reviewed and merged after QA, so the merge **is** Gate 2. `merged`
reads it off GitHub (`gh pr view`) — it refuses an unmerged PR — and records
`Release approved (PR merged by …)` with the release the merge ships in: the earliest
release tag that contains the merge commit, or, before that release is cut, the version
in git — `MARKETING_VERSION` in `project.yml` at the merge, or the next patch once that
version is tagged. Every build at or past it is cut from a `main` that already holds the
flag. Then `request-experiment --post` asks Viktor straight away. It reads
`Experiments.swift` at the merge: a flag declared under `Experiments.Onboarding` is
assigned before sign-in, so the request adds `targeting onboarding true` (AuraBot reads
only the literal `true`). Aurie requests carry `targetApp ai`; Aura's omit it.
When the TestFlight build exists, `testflight --version` records its version on the row.
Testing can still hold an idea back: `unwind` lands it `shipped_dormant`.

`request-experiment` records the request in the note and refuses a second one without
`--again`, so a tick that runs it every time asks once.

### The thread mark follows the card

The feature card's state belongs to the release process: on the Mobile team, **Done means a
TestFlight build carries the work** (the GitHub integration moves it to To Be Released on
merge, the release process to Done). The loop never moves the card. On every `merged` and
`check-experiment` run it reads the card, and once it is Done the idea's first message swaps
🚀 for ✅ — the same moment Linear's Slack link marks the thread.

### Two commands deliberately write no status

`release` records that the gate passed and stops there. An idea is live when an
experiment is **actually assigning users**, not when somebody approved it —
collapsing those would mark a row live while the request sat unanswered in
`#support-bot`. `check-experiment` is what confirms it.

`testflight` is the same idea in reverse: the row is already `implementing` and
stays there, but which build each round was against gets recorded. "We tried it
and it was wrong" is unreadable a month later without knowing which build "it"
was.

**`testflight` records the build; `--post` opens the hold-back window.** The merge already
approved the release, and the release process announces the build to QA, so the post is
optional. When it is posted, it announces the build in the idea's thread, and
[`harvest-idea-decisions`](../harvest-idea-decisions/SKILL.md) reads **only
replies newer than the most recent announcement**. That is what keeps gate one's
"ship it" from being read as gate two's, and what stops an `iterate` round from
replaying the previous round's refusal.

Without `--post` the row is recorded and the gate stays shut — the harvester
reports `no_decision` and says why. The announcement sentence is shared with the
harvester (`TESTFLIGHT_ANCHOR` here, `RELEASE_ANCHOR_RE` there); **rewording it on
one side silently closes the gate**.

### `shipped_dormant` is where most rejected ideas land

A release carries several ideas, so one failing its gate cannot hold the build.
The code goes out and that idea's experiment is simply not turned on — everyone
sees control, nobody sees the treatment.

It is **not a result**. Filing it as `lost` would retire a question nobody ever
asked, the same failure [`learn-idea`](../learn-idea/SKILL.md) refuses
`Turned off` for. The funnel counts it as *built*, never as *live*: counting it
live would report a release rate that includes ideas nobody saw.

It is also the only status a row moves **backwards** from. Everything else is
forward-only, because the row is a log somebody reads months later and a rewound
step still reads perfectly plausible. `iterate` is the documented exception, and
it exists because the code is already in the build — the next round is an edit,
not a rebuild.

### The gate is enforced, not advised

`request-experiment` refuses unless `merged` or `release` has recorded an approval
on the row. Without that check the second gate would be a suggestion: nothing would stop
an experiment being requested for a build no human ever opened, which is the
entire failure the gate exists to prevent.

### Abandoning carries work — and files it

It is the one exit that leaves something behind. A status flip alone leaves a
dormant flag nobody will ever turn on — which is how a codebase accumulates
branches no one dares delete. So `abandon --file-cleanup` **files a Linear cleanup
card** — naming the flag, the treatment path and a removal checklist — and links it
back on the row's `Cleanup issue`. Without `--file-cleanup` it prints the card it
would file (dry-run); it never files a second card once the row carries one.

This is the one cleanup trigger the loop files itself. It cannot go through
[`eng:sweep-ended-experiments`](../../../eng/skills/sweep-ended-experiments/SKILL.md):
that sweep only detects experiments the server reports **ended**, and an abandoned
idea's experiment may never have run, so there is nothing "ended" to find. The card
lands in the same Mobile team and Aurie project the sweep uses, so the two are
indistinguishable on the board. (A *result* — won/lost/inconclusive — is the other
trigger, and that one **does** go through the sweep; see [`learn-idea`](../learn-idea/SKILL.md).)

**The loop never ends an experiment.** Yawar and Viktor monitor, analyse and end
them. This skill asks for one and reads the verdict; it does not decide a test is
over.

## CLI

Every command's `ref` accepts the **Idea ID** — `42`, `#42`, or `IDEA-42` (the immutable, human-friendly address Notion mints) — as well as the fingerprint or a title substring.

```bash
SCRIPT=${CLAUDE_PLUGIN_ROOT}/skills/ship-idea/scripts/ship_idea.py

# Where is this row, and what runs next? No writes.
python3 $SCRIPT plan ffe899463174

# Record the TestFlight build. --post (optional) announces it in the thread, so a
# "not shipping" reply after QA testing is read as this round's verdict.
python3 $SCRIPT testflight ffe899463174 --version "1.30.0 (1)" --post

# The gate decided. --post replies the verdict in the thread; --release-post-url
# links the existing #qa-aurie-mobile / #releases post rather than re-announcing it.
python3 $SCRIPT release ffe899463174 --by "Steve" \
  --release-post-url https://auramobile.slack.com/archives/C08B28RG6JX/p1789675389374609 --post
python3 $SCRIPT unwind  ffe899463174 --reason "Copy reads abrupt on the second line." --post

# After an unwind.
python3 $SCRIPT iterate ffe899463174 --note "Shortened the second line."
python3 $SCRIPT abandon ffe899463174 --reason "Superseded by the streak idea." --file-cleanup --post

# The PR merged — that is the approval. Records the release it ships in.
python3 $SCRIPT merged ffe899463174 --post

# QA found a bug: back to the build. The fix PR's merge is the next round's approval.
python3 $SCRIPT qa-bug ffe899463174 --reason "Continue pill overlaps the page dots on SE." --post
python3 $SCRIPT merged ffe899463174 --pr https://github.com/AuraHealth/AuraConsumerApp/pull/1790 --post

# Once approved: ask, then confirm. Only the confirm makes the row live.
python3 $SCRIPT request-experiment ffe899463174 --experiment aiSettingsStreak --post
python3 $SCRIPT check-experiment   ffe899463174   # going live posts the "Live as experiment" line once

# Re-post the live line by hand (skipped when the thread already has it).
python3 $SCRIPT announce ffe899463174 --post
```

## `check-experiment` is tri-state, and that is the point

`GET /experiments/{name}` is **authenticated**: 200 with a valid token, 401 with
a missing or invalid one, 403 with a valid token of the wrong role. So a
missing or rejected token fails loudly here, which is what makes the read safe to
poll at all.

Re-running is safe and sometimes necessary. `data:sync-experiments` creates the
`Learnings/Experiments` row on its own cadence, so a row can be correctly
`experiment_running` with nothing linked yet — a second run attaches the relation
**without** rewriting the status or restamping the note. A run with nothing left
to do says so and exits 0.

The command returns three outcomes, and they are deliberately not two:

| Outcome | Exit | Meaning |
|---|---|---|
| exists | 0 | row flipped to `experiment_running` |
| absent (404) | 1 | not created yet — wait, row untouched |
| **unknown** | 2 | could not check — row untouched |

Collapsing *unknown* into *absent* is the failure worth naming: a network blip or
an expired token would read as "Viktor hasn't done it yet", and the loop would
sit on a row whose experiment is already live and already assigning users.

## Related skills

- [`product:build-idea`](../build-idea/SKILL.md) — writes the `implementing` this leg picks up.
- [`product:manage-ideas`](../manage-ideas/SKILL.md) — the status writer for the decision half.
- `data:sync-experiments` — creates the `Learnings/Experiments` row this links to, on its own cadence.

## References

- Notion: [Aurie Features Ideas](https://www.notion.so/7e721e75374645a7acfa085e3aeca6df) (data source `8d14f3eb-0c55-4f25-9322-ab2b2a69f092`)
- Notion: [Learnings/Experiments](https://www.notion.so/2cbe45a3246040658b9f20559b5b452b) (data source `c0209685-17ac-49e2-849a-85fa8178decd`)
- Linear: [PROD-5925](https://linear.app/aura-health/issue/PROD-5925) (parent [PROD-5917](https://linear.app/aura-health/issue/PROD-5917))
