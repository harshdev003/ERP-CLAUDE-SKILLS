---
name: build-idea
description: >
  Take one approved idea from the `Learnings/Loop/{Loop}` Notion backlog through to a
  reviewable pull request: Linear card, worktree, implementation behind an experiment
  flag, lint/test/build gates, a sim test of both variants with screenshots and video,
  media attached to the card and the row, PR opened and BugBot-clean. Then it stops —
  code review, merge and release stay human. Use when the user says "build this idea",
  "implement idea {fingerprint}", "take {idea} to a PR", "start the build leg", or
  "build the approved ideas". Default target is the Aurie Features Ideas database; pass
  `--data-source-id {id}` for the other loops.

metadata:
  version: "1.15.0"
knowledge:
  reads:
    - Guide/Index
    - Learnings/Loop/Aurie Features Ideas
    - Learnings/Experiments
    - Tools/Notion
    - Tools/Linear
    - Tools/Slack                     # channel directory + Post format footer spec; `finish --post` replies in the idea's thread
  writes:
    - Learnings/Loop/Aurie Features Ideas
---

# build-idea

## What this skill will never do

Read this first. Everything below is process; this part is the contract.

1. **It never merges a pull request.** It opens one, gets it BugBot-clean, and stops.
2. **It never releases.** No TestFlight, no tag, no version bump.
3. **It never forces an experiment variant on anything but a sim-test account.** Enforced in code, not prose — `guard-variant` refuses any address that is not `test+…@aurahealth.io` or `{prefix}+test+…@aurahealth.io`, and there is no override flag.
4. **It never builds an idea nobody approved.** `plan` refuses any row whose status is not `approved`.
5. **It never builds an idea on a screen no experiment can reach.** `plan` and `start` refuse a pre-consent screen (`welcome`, the splash — see `brainstorm-ideas` → `references/pre-consent-screens.md`) unless the row's Instrumentation carries `bucketing path: <how a user is bucketed and exposed>`.

The third one is in code for a specific reason, and the reason is stronger than it first looks.

Every other failure in the build leg is loud — a bad branch, a failing test, a rejected PR. Forcing a variant on a real user is **silent**: the run looks fine, and the damage is a contaminated metric nobody sees until an experiment reads wrong weeks later. Assignments persist server-side and do not roll back.

**And nothing upstream stops you.** Probed against the live service on 2026-09-14 with a nonexistent UUID, so nothing could mutate:

| Call | valid service token | bogus token | no auth |
|---|---|---|---|
| `GET /experiments/{name}` | 200 | 403 | 403 |
| `POST /experiments/{name}/set/{uid}/{variant}` | 404 `USER_NOT_FOUND` | 404 `USER_NOT_FOUND` | 404 `USER_NOT_FOUND` |

The read is authenticated. The write is not — all three returned the same error, so the route resolves the user before validating any bearer token. Whether it validates *after* resolving a real user could not be tested without mutating a real account, so treat "unauthenticated" as the working assumption rather than proven.

This guard is therefore the first and only protection, not a second layer. It fails closed and has no override.

## Why REST and not the Notion MCP

Repo default is MCP-first (best-practices principle 11). This skill uses REST for the same reason its siblings do: every operation here either enumerates the database to resolve one row out of many (`plan`, `start`, `finish` all call `find_idea`) or writes a specific property set, and `notion-search` can neither enumerate a data source nor filter reliably on `Status`.

Without `NOTION_TOKEN` the script exits 2 with a message rather than degrading — there is no MCP fallback path, because a partial read here would resolve the *wrong row* and then write to it. See AINative `CLAUDE.md` → "Notion REST integration" for the token.

## The steps

| # | Step | Owned by |
|---|---|---|
| 0 | **Card, then plan** — the ENGMOB card (current cycle, under the loop parent) linked to the Slack thread; the plan on the row **and** the card; open questions → `hold` and ask, none → continue; work outside the app → `sub-card` | `build_idea.py card --post`, `record-plan`, `sub-card --post`, `hold` |
| 1 | Notion row → `implementing`, card → In Progress | `build_idea.py start` |
| 2 | Branch — worktree, gitignored config restored (**real** plist copy, not a symlink) | `parallel-agents` Phase 3 |
| 3 | Implement behind the flag — `a` = treatment, `b`/`nil` = control | you |
| 4 | Gates — `swiftlint --strict`, unit tests, build | `.claude/rules/testing.md` |
| 5 | Sim-test both variants, capture screenshots + video | `/sim-test`, after `guard-variant` |
| 6 | Attach media to the row, the card and the PR | `build_idea.py finish` |
| 7 | PR — `gh pr create --draft`, `bugbot run`, fix, re-run until clean, **then** `gh pr ready` and `build_idea.py ready` (card → In Review) | `eng:bugbot-review` |
| 8 | **Stop.** | a human |

### Step 0 — Plan, then build or ask

**Every build starts with a card and a written plan, before any code.** First
`card IDEA-42 --post`: it creates the ENGMOB card, attaches the idea's Slack thread to it,
records it on the row and replies in the thread with its link. Then read the idea, its design files and the code it would touch, and write a short build
plan in this shape:

```markdown
## What changes
The screens and components, and what each arm shows. Control is today's behaviour exactly.
## Experiment
Flag name, arms, where assignment happens, where exposure fires — and confirm the surface
can be bucketed there (consent, auth, existing assignment path).
## Outside the app
Backend endpoints, data model, services, analytics pipelines, third parties (Branch, Stripe…)
— or "none".
## Risks
What could go wrong, and what it would cost.
## Open questions
Each decision a person has to make, with two or three options and the cost of each —
or "none".
```

Record it on the idea's row and post it to the card, so the reasoning sits next to the
decision it leads to. Work the feature needs outside its own PR — a backend change, a
service — gets a sub-card under the feature card (`sub-card --title … --body-file … --post`,
`--team ENGBACK` for a service change), also linked to the thread. Linking uses Linear's
Slack link (comments sync both ways) when the Linear app is in the channel, else a URL
attachment. The PR title carries the card id (`[ENGMOB-123]`).

```bash
python3 "$SCRIPT" record-plan IDEA-42 --file plan.md
```

**Then the plan decides what happens next:**

- **No open questions** → continue to step 1 and build. Most screen and copy changes are
  this case; the plan costs a few minutes and is the record of what was intended.
- **Any open question** → stop and ask. A question belongs here when the build needs:
  a new backend endpoint or service, or a change to one another repo owns; a data-model or
  schema change; bucketing outside the normal path (a surface before consent, an assignment
  the experiment service cannot make, an exposure that cannot fire where the change
  renders); a privacy, consent, security or money trade-off (a gift, a refund rule, a
  reward); or a choice between architectures with different costs. Hold the idea with the
  questions as a person will read them:

  ```bash
  python3 "$SCRIPT" hold IDEA-42 --post --decision "Plan on the row. Questions: (1) … options … (2) …"
  ```

`hold` keeps the row `approved` (nothing was built) with an `ARCHITECTURE DECISION PENDING`
note, moves the card to **Blocked** if one exists, comments the questions on it, and with
`--post` replies in the idea's thread. `plan` and `start` refuse a held row, and the features
loop builds the next approved idea instead. When the questions are answered, record the
decision and the build resumes from step 1 — re-run `record-plan` first if the answers
changed the plan:

```bash
python3 "$SCRIPT" unhold IDEA-42 --post --decision "Option 2 — Jerome, 2026-10-06"
```

Stopping here is cheap and stopping later is not: IDEA-1 reached a worktree before its
pre-consent screen surfaced, and was dropped there; IDEA-41's gift needed Aura's
pay-then-reward referral rules, which a tracked link alone would have skipped.

**Open the PR as a draft.** `--draft` on creation, `gh pr ready` only once a BugBot run on the current head comes back clean. This skill opens the PR itself, and `bugbot run` is asynchronous — a PR that is ready the moment it exists can be merged before the review lands, stranding the fixes on an already-merged branch.

### The feature card lives on the engineers' board and moves as the build does

The `Loop: ab:{experimentName}: {title}` card is engineering work (every card the loop files, sub-cards included, starts with `Loop: ` and carries the workspace `Loop` label), so it is an **ENGMOB (Mobile) card in that team's current cycle** — resolve the cycle at creation with `linear_api current-cycle --team 5497338e-312b-4ddf-ac5f-3339399c956d` and pass it to `shared:add-linear-issues` (with `teamId` = that Mobile team `5497338e-312b-4ddf-ac5f-3339399c956d`, `parentId` = the loop parent, `stateId` = the Mobile **Todo** `090653f2-f21a-4718-89cd-0438e781f82c`). Use the **Mobile column** of `add-linear-issues`' status table — every state below is team-scoped, and a `PROD`/`ENG` lookalike id on an `ENGMOB` card is rejected by Linear. It stays parented under the loop issue for tracking, but shows up where engineers actually work.

Its status is then **driven by the build**, not left as a convention:

| Moment | Command | Card state |
|---|---|---|
| Card created at planning | `build_idea.py card --post` | **Todo**, in the current cycle, Slack thread attached |
| Work starts | `build_idea.py start` | → **In Progress** |
| PR opened as a **draft** | `build_idea.py finish` | stays **In Progress** (a draft is not ready to review) |
| PR marked **ready** | `build_idea.py ready` (alongside `gh pr ready`) | → **In Review** (no reviewer is requested; who reviews is the team's call) |
| Merged / TestFlight | `ship-idea` | → merge/release states |

This follows `add-linear-issues`' own convention: a **draft** PR is In Progress, a **ready** PR is In Review. `finish` runs at step 6 while the PR is still a draft, so it reads the PR's `isDraft` and **holds In Progress** rather than marking review prematurely — `ready` moves the card when a human opens the PR for review at step 7 (and `finish` will move it directly if you happen to run it after the PR is already ready). Every move is via the vendored `linear_api update-issue`, **best-effort** (a Linear miss prints a warning but never fails the Notion write) and **idempotent** (setting a held state is a no-op). Merge and release stay with `ship-idea` (PROD-5925), not here.


## CLI

An idea is addressed by its **Idea ID** — `42`, `#42`, or `IDEA-42` (the immutable, human-friendly address Notion mints). The fingerprint and a title substring still resolve too.

```bash
SCRIPT=${CLAUDE_PLUGIN_ROOT}/skills/build-idea/scripts/build_idea.py

# 1. Is this idea buildable, and what does its card say?  (<ref> = Idea ID / fingerprint / title)
python3 $SCRIPT plan IDEA-42 --experiment aiPersonalizedGreeting

# 2. Does this domain already have a live experiment that would confound the readout?
python3 $SCRIPT guard-collision --domain home/feed

# 3. Before forcing a variant to sim-test the treatment
python3 $SCRIPT guard-variant --email <redacted-email> \
  --experiment aiPersonalizedGreeting --variant a

# 4. After the PR is open and BugBot-clean. --post announces it in the idea's
#    thread with the PR link + both-variant screenshots/video, so a reviewer can
#    judge the thing rather than the description; without it, nothing is sent.
python3 $SCRIPT finish IDEA-42 --pr https://github.com/AuraHealth/AuraConsumerApp/pull/1234 --post
```

`card` creates the feature card through the vendored `linear_api` (the same `IssueCreateInput` `shared:add-linear-issues` sends: Mobile team, current cycle, loop parent, Todo, assigned to the operator), adopts an existing one with `--linear`, and is idempotent: a row that already has a card is only re-linked to its thread. `start` uses the row's card.

**`finish --post` speaks in the idea's thread.** The thread is where the team watches an idea move and answers Gate 2; a PR opened in silence leaves a reviewer with only the description. With `--post`, `finish` records the PR on the row and then replies in the thread with the PR link, uploading the `Screenshot` and `Video` (both variants) from the row as follow-up attachments — the same download-from-Notion, upload-to-Slack path `share-ideas` uses, so a failed upload cannot cost the PR post. A dry run (no `--post`) writes the row and sends nothing; a row with no `Slack thread` is refused.

## The collision gate

`guard-collision` refuses to start a build whose **domain** already carries a running Aurie experiment.

The card that specified this asked for "the same surface and the same primary metric". Neither is a structured field anywhere — surface lives in prose, the primary metric usually only in the ticket — so this matches on domain, which is broader. A false stop costs one `--override` and a sentence; a confounded pair of experiments costs the readout of *both*, and you find out weeks later when the numbers disagree with themselves. `webYearlyPricing2` running concurrently over the same UserWeb traffic is the live example — it made each test's cross-plan cash unreadable.

### The live set comes from the API

The running set is `GET /experiments?limit=10000`, filtered to `endedAt` unset and `active` carrying more than one letter — because an experiment only confounds another while it is **splitting traffic**. A single letter means it has stopped splitting, and says nothing about why: it covers a pre-release force, a rollout, a disabled flag and a permanent kill switch alike, with an empty `result` in every case. That ambiguity costs this gate nothing — none of them is splitting — but it means **no verdict may be derived from it**. `?limit=N` is the only query knob the endpoint honours; offset, page and cursor are silently ignored, so a default request returns the first 100 of ~3,000 rows and looks complete.

`Learnings/Experiments` supplies only the hypothesis prose the domain keywords match against. It is a **mirror** — `data:sync-experiments` populates it on its own cadence — so it cannot be the live signal. Measured against the API on 2026-09-14 it carried 7 experiments as `Turned off` that were live, and 30 as `Running` that had ended.

Those two errors are not symmetric, and the loud one is the safer one. An over-block gets read, recognised as a dead experiment, and overridden. A miss is silent: "no collision" reported for something that is live, which is exactly what this gate exists to prevent. False blocks corrode it too — a gate that fires on dead experiments teaches people to reach for `--override`, after which it guards nothing.

A live experiment with no mirror row yet is matched on its **name alone** and flagged in the output as such. An unreachable `AURA_API_TOKEN` falls back to the mirror **with a warning** — never silently, because "could not check" reading as "all clear" is the same failure in a different coat.

### Names are matched word-wise

Experiment names are camelCase, which breaks both naive approaches.

A substring test reads words nobody wrote: `feed` sits inside `webSignupChooseFeeDefaultZero` — "Choose **Fee D**efault" — putting a web sign-up test on `home/feed`.

Camel-splitting alone misses in the worse direction. `aurieCustomWebPaywall2` is not `paywall`, `aurieAnimations2` is a plural with a generation number, and `aiLiveKitIntegration4` splits to "live kit", which is not `livekit`.

So names are split on the humps, each token folded (trailing digits off, simple plural off), and a keyword's word sequence matched against the token sequence — plus against runs of up to three adjacent tokens joined back up, which recovers `livekit` without letting `feed` match "fee default", because the join is compared for equality and never for containment.

### What this gate structurally cannot see

A **shared-metric collision**: two experiments on different surfaces judged by the same primary metric. Independent randomisation means neither invalidates the other, but attribution goes noisy when both move the metric in the same window.

Nothing in Notion or the API records a primary metric, so no amount of work here finds it. Ask Viktor in `#support-bot`, which reads the team's own tracker. On the settings pilot it confirmed the domain was clear *and* flagged that a retention-focused settings row shares Day 1 / Week 1 Retention and Paid Cancel Rate with three live engagement tests — a real caveat this gate had no way to raise.

`guard-collision` prints that prompt on every pass, clean or overridden, so the question is asked rather than remembered.

## Throughput: 5–10 ideas a week

- **Build in parallel, not in sequence.** One worktree and one bound simulator UDID per idea (`parallel-agents`; `xcrun simctl clone` for extra sims). Three to five concurrent, each one flag and one small PR.
- **Human review is the bottleneck, so optimise for it.** Small PRs — one flag, one surface. BugBot-clean before a human looks. A daily "ready for review" digest with screenshots and video, so review is minutes.
- **Release cadence has to match.** 5–10 flags a week means at least a weekly TestFlight; batching merged ideas into one release is fine, and the ship leg records the version per row.
- **Ideation has to feed the queue.** 5–10 approved a week needs roughly 20–30 proposed, so the ideation preset runs at least twice a week on rotating domains.

## Analytics: stamp exposure where the surface renders

Not at assignment. An experiment stamped at user creation cannot distinguish an unexposed user from an unaffected one — `dailyPractices2` recorded a paid-cancel effect it could not have caused, because only 23 of 57 arm-a payers ever opened the feature. The rendered card body says this, so the engineer reads it on the ticket rather than here.

## Related skills

- [`product:manage-ideas`](../manage-ideas/SKILL.md) — where an idea becomes `approved` in the first place.
- [`product:harvest-idea-decisions`](../harvest-idea-decisions/SKILL.md) — reads the team's verdict out of Slack.
- `parallel-agents`, `/sim-test`, `eng:bugbot-review` (AuraConsumerApp) — steps 2, 5 and 7.

## References

- Notion: [Aurie Features Ideas](https://www.notion.so/7e721e75374645a7acfa085e3aeca6df) (data source `8d14f3eb-0c55-4f25-9322-ab2b2a69f092`)
- Card body shape: [PROD-2126](https://linear.app/aura-health/issue/PROD-2126)
- Linear: [PROD-5924](https://linear.app/aura-health/issue/PROD-5924) (parent [PROD-5917](https://linear.app/aura-health/issue/PROD-5917))
