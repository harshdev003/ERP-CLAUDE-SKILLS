---
name: features-loop
description: >
  The orchestrator for the Aurie Features loop — one runbook that sequences the legs and
  the schedule. A thin composer over the loop leg skills;
  it reimplements none of their logic and never touches the human gates. Subcommands:
  `start --app {aurie|aura}` (start the loop in this session),
  `run` (one autonomous tick), `ideate` (knowledge base → record → design → share),
  `harvest` (Slack decisions → Notion), `build` (next approved idea, one at a time),
  `status` (funnel + queue from Notion), and `digest` (daily review nudge + weekly
  roll-up). Use when the user says "start the features loop", "start the loop", "run the
  features loop", "run one loop tick", "ideate on {domain}", "harvest and build the next
  idea", "show the loop status", or "post the loop digest". Default target is the **Aurie Features** loop; pass `--app aura` (once it
  lands) or `--data-source-id {id}` for the other loops. Do NOT use to decide at a gate
  (human only), end an experiment (Yawar/Viktor), or run a single leg you can invoke directly.

metadata:
  version: "1.14.0"
knowledge:
  reads:
    - Guide/Index                    # baseline routing aid (universal)
    - Learnings/Loop/Aurie Features Ideas  # the ideas database the whole loop turns on
    - Learnings/Experiments          # experiment verdicts the learn leg reads
    - Procedures/AI Native/Features Loop  # the runbook this skill executes
    - Tools/Notion                   # NOTION_TOKEN — every leg's Notion I/O
    - Tools/Slack                    # channel IDs + SLACK tokens for share/harvest/digest
    - Tools/Linear                   # LINEAR_API_KEY — build leg's card
---

# features-loop

The orchestrator for the **Aurie Features** loop. One place that sequences the legs and
carries the schedule; every piece of real work — the ideation, the Notion writes, the
Slack posts, the PR — lives in the leg skills this composes. It adds no ideation, no
posting, and no build logic of its own, and it never crosses a human gate.

Modelled on `data:run-all-analysis-cadence`: a thin composer that invokes each leg via the
Skill tool (or the leg's own script). **The session running it is the orchestrator, not the
worker**: it reads the channel, settles decisions, and dispatches the long legs — a build,
an ideation pass — to background agents, then checks their work on the next tick. That keeps
its own context small enough to run all day. Start it with [`start`](#start--start-the-loop-in-this-session).

**It runs to the workflow's gates and stops only there.** Everything between them —
recording a verdict, marking the thread, answering a question from the priors, filing the
card, building behind a flag, opening the PR, the BugBot cycle, ideating, designing, sharing
— is the loop's job and needs no permission. It asks a person only when the workflow itself
has no answer: an approval too ambiguous to classify, or something no leg anticipated.
Jerome, 2026-09-30: *"this session should follow the workflow designed and be as autonomous
as possible."*

## The loop it drives

The full process — sources, both gates, the status lifecycle, the marks, ownership — is
documented on the **`Learnings/Loop/Aurie Features Ideas`** Notion page and the
**`Procedures/AI Native/Features Loop`** runbook. Read those for the *why*; this file is the
*how to run it*. The one-line shape:

```
ideate → record → share → GATE 1 (human) → build → GATE 2 (PR merged) → experiment → learn → back to ideate
```

The two gates and the experiment lifecycle are **never** automated:

- **Gate 1** (is it worth building?) is a human reply in the idea's Slack thread — on the Aurie loop **Steve's** reply specifically; anyone else's is input the row waits on (`harvest-idea-decisions` → `DECIDER`). This skill posts the idea and harvests the verdict; it never supplies one.
- **Gate 2** (is it worth shipping?) is **the PR being merged** after review and QA. The loop reads the merge off GitHub and never merges. Testing in TestFlight can still hold an idea back (`ship-idea unwind`).
- **Yawar and Viktor own the experiment** — creating, monitoring, analysing, ending it. The
  loop asks for one at the start (`ship-idea`) and reads the verdict at the end
  (`learn-idea`); it never decides a test is over.

## What this skill will never do

Read this first — it is the contract, and most of it is inherited from the legs, which
enforce it in code.

1. **It never decides at a gate.** Approvals and release sign-offs are human replies. `harvest`
   only *reads* them.
2. **It never merges or releases.** `build-idea` opens a BugBot-clean PR and stops; merge,
   review and release stay human.
3. **It never ends an experiment.** `ship-idea` asks Viktor to create one; `learn-idea` reads
   the result once Yawar/Viktor have reached one.
4. **It never forces a variant on a real user.** `build-idea`'s `guard-variant` refuses any
   address that is not a sim-test account — there is no override.
5. **It never schedules itself silently.** A tick is one sweep. The only way it repeats is
   [`start`](#start--start-the-loop-in-this-session), which asks the harness to — so a loop
   is always one an operator started, and it stops when that session ends.

## `--app` and the target database

Default target is the **Aurie Features** loop (Notion data source
`8d14f3eb-0c55-4f25-9322-ab2b2a69f092`, channel `#ai-loop-aurie-features` `C0C1JTK3YCC`).
Every leg already accepts `--data-source-id {id}` for the other loops. This skill exposes the
same choice as `--app`:

| `--app` | Loop | Data source | Channel | Sim-test target |
|---|---|---|---|---|
| `aurie` (default) | Aurie Features | `8d14f3eb-0c55-4f25-9322-ab2b2a69f092` | `#ai-loop-aurie-features` `C0C1JTK3YCC` | AuraConsumerApp |
| `aura` | Aura Features | *not created yet* | `#ai-loop-aura-features` `C0C0ZSMNZM5` | *not wired yet* |

Pass `--app aura` (or a raw `--data-source-id`) once the [Aura readiness](#aura-readiness)
items land. Until then only `aurie` runs; a request for `aura` should report the gap rather
than run against the Aurie database.

**This orchestrator covers the two *Features* loops only.** The programme has four loops:
Aurie Features and Aura Features (both this skill, via `--app`), plus **Aurie Conversations**
(autotune the Aurie prompt, `#ai-loop-aurie-conversations` `C0C29JPEUQG`) and **Aura
Recommendations** (`#ai-loop-aura-recommendations` `C0C0ZSMU7C7`). Those last two are
structurally different — they do not ideate UI changes or build a UI PR, so they
carry different legs and get **their own orchestrators**, not another `--app` value here.
The Notion schema, channel-per-loop, and decision-protocol shape are what clone across all
four; the legs are not.

## Standing defaults

- **Composition only.** Always invoke a leg via its skill (or its documented script); never
  inline or duplicate its logic. If a leg's behaviour changes, this orchestrator inherits it.
- **One tick, one thing.** `build` takes exactly one approved idea per tick (token budget +
  one-experiment-per-surface discipline). `ideate` runs one domain. `harvest` sweeps once.
- **Idempotent.** Every leg dedups on the row itself (Notion `Status` / `Slack thread` /
  fingerprint), so re-running a tick never double-posts, double-records, or double-builds.
- **Human gates never run here.** No subcommand approves, rejects, merges, releases, or ends
  an experiment.
- **A quiet channel is a no-op.** `harvest` on a channel with no new decisions writes nothing
  and that is success, not a failure.

## Subcommands

Each subcommand delegates — prefer invoking the leg **skill** (via the Skill tool), which
runs its full judgment/guard logic. The bundled scripts below are shown for the read-only legs
(`status`) and for reference; **resolve their paths from `$CLAUDE_PLUGIN_ROOT`, never as a
bare `plugins/…` path.** `$CLAUDE_PLUGIN_ROOT` is the product plugin root, so a same-plugin
script is `${CLAUDE_PLUGIN_ROOT}/skills/{skill}/scripts/{x}.py`. A bare relative path resolves
only when the current directory happens to be the AINative repo root — and it is not during
`ideate`, which runs from the AuraConsumerApp checkout.

### `start` — start the loop in this session

```
/product:features-loop start --app aurie
```

The one command an operator types. **`--app` is required here**, with the same values as
every other two-app skill (`aurie`, `aura`). The one-shot subcommands below may default to
Aurie, but `start` launches something that runs unattended all day, and starting the wrong
app's loop is the expensive mistake — so it names the app or refuses, listing the apps it
knows. It then hands the harness's self-pacing loop a single body, this skill's own `run`
carrying that app, so every tick reads the current skill definitions from disk:

> Invoke the **`loop`** skill with no interval (dynamic mode) and the prompt
> `/product:features-loop run --app {app}`.

That is the whole of it. The loop runs the first tick immediately, then self-paces: about
every 20–30 minutes while the channel is quiet, sooner when a dispatched agent finishes and
wakes it.

A session runs **one** loop, so Aurie and Aura at once take two sessions. Nothing is lost
by a restart — every leg dedups on the Notion row and the Slack thread — and dispatched
agents stop with the session too, so the first tick after one resumes their work. Loops
expire after seven days. Details: [`references/unattended.md`](references/unattended.md#a-session-runs-one-loop-and-its-agents-live-in-it).

Before the first `start` in a new session, make sure the installed plugins match `main`
(`shared:reload-aura-skills`): the tick invokes the leg skills by name, and a stale install
runs yesterday's rules.

### `status` — the funnel and the queue, from Notion alone

Prints where the backlog stands. No writes, no Slack, no simulator.

```bash
LEARN="${CLAUDE_PLUGIN_ROOT}/skills/learn-idea/scripts/learn_idea.py"
MANAGE="${CLAUDE_PLUGIN_ROOT}/skills/manage-ideas/scripts/manage_ideas.py"

# Funnel: proposed → shared → approved → built → released → decided, by month
python3 "$LEARN" funnel

# The queue: what is waiting at each actionable state
python3 "$MANAGE" list --status approved       # ready to build
python3 "$MANAGE" list --status shared         # waiting on Gate 1
python3 "$MANAGE" list --status implementing   # in build / review
```

Read the two together into one short readout: the funnel (conversion), then the queue depth
per actionable state, then the one-line "next action" (build the head of the approved queue,
or ideate if it is empty). For another loop add `--data-source-id {id}` to each call.

### `harvest` — Slack decisions → Notion

Invoke the skill so its judgment layer settles `needs_judgment` threads:

> Invoke `product:harvest-idea-decisions` — one sweep, non-interactive; write only verdicts
> read with confidence, hand ambiguous threads back for judgment; never chase anyone for a
> reason.

Zero-write on a quiet channel. Carry any `needs_judgment` rows into the tick summary — they
are not failures, they are threads a human still has to make legible.

A `needs_redesign` verdict ("I like the idea but not the design") is the loop's to act on, not
a person's, and it is two steps with two owners:

1. Hand the row and the quoted request to the **`product:designer`** agent. It re-renders via
   `product:design-idea` and **replaces the row's Screenshot** — it never posts.
2. Then run **`product:share-ideas post-redesign --idea IDEA-N --confirm`**, which posts the
   new Current-vs-Proposed as a **reply in the same thread**. That bot reply is what clears
   `needs_redesign` on the next harvest — skip it and the tick re-renders forever.

The row stays `shared`. Two redesign rounds per thread, then it goes to the tick summary as a
thread that needs a human.

### `build` — the next approved idea, one at a time

The head of the approved queue is the first approved row, in `status` order, that
`build_idea.py plan` accepts. `plan` exits **2** for any row it refuses — held for an
architecture decision, a pre-consent screen with no bucketing path, a domain collision — and
says why; move to the next approved row. `status` cannot show these, so `plan` is the check. `plan` takes the
`ref` (it refuses anything not `approved`, and refuses a domain that already carries a running
experiment):

```bash
BUILD="${CLAUDE_PLUGIN_ROOT}/skills/build-idea/scripts/build_idea.py"
python3 "$BUILD" plan <fingerprint>   # <fingerprint> = head of `status`'s approved queue
```

Prefer the skill so the full build runs (worktree, flag, gates, sim-test, media, PR, BugBot):
> Invoke `product:build-idea` on the head of the `approved` queue — one idea only. Stop at a
> BugBot-clean PR; do not merge or release.

**Dispatch it; do not run it in the tick.** A build is hours of work; running it inline
would fill the orchestrator's context with one idea's detail. Spawn **one background agent** for it,
isolated in its own worktree, with everything it needs in the prompt so it never has to
ask: the idea's `IDEA-N` and fingerprint, the approved arm and what it is, the experiment
flag name, the repo (AuraConsumerApp for Aurie), and the instruction to invoke
`product:build-idea` and stop at a BugBot-clean PR.

**Name the experiment flag before dispatching**, so the agent never has to invent one:
camelCase `{surface}{Change}`, from the row's `Screen` and the approved arm — `welcome` +
a trust badge → `welcomeSocialProof`; slide 5's CTA → `valuePropsExplicitCTA`. Check the name
is not already in `Experiments.swift` before using it, and state it in the tick summary.

**One build agent at a time; PRs queue for review.** A build is in flight while its agent
runs or its `implementing` row is short of a ready-for-review PR (resume that first). A row
whose PR is ready for review waits on a person and does **not** block the next approved idea.
Rules: [`references/unattended.md`](references/unattended.md#builds-in-flight-stalled-done).

### `ideate` — knowledge base → record → design → share

Proposes the next ideas from **the whole AI Knowledge base** — what users ask for and why they
leave, what every experiment settled, the market, and the screens as captured — through
`product:brainstorm-ideas`. It needs **no simulator**: the screenshot store already holds
every screen in all three skies, so ideation reads the captures rather than driving the app.
That is what lets it run inside the unattended tick.

One domain per run, rotating (see [domain rotation](#domain-rotation)). Steps:

1. **Brainstorm** for the chosen domain:
   > Invoke `product:brainstorm-ideas` for `{domain}` — three altitudes (improve a screen,
   > improve a feature, or propose one that does not exist), every idea citing the signal
   > rows it came from, predicting trial / paid / session / retention and naming the risk,
   > and carrying `priorArt` wherever an experiment already touched the same lever.

   It emits the JSON `record-ideas` ingests.
2. **Record** it into Notion (dedup on fingerprint, prior-art warning on already-answered
   ideas):
   > Invoke `product:record-ideas` with `--from-json` pointing at that output, and **`apply
   > --confirm`**.
3. **Design** each newly recorded idea before anyone sees it — a Gate 1 thread opens with a
   picture, not just prose:
   > Hand each new `pending` row to the **`product:designer`** agent (one invocation per idea,
   > no fan-out). It reads the app's design system, chooses the element, the **real** copy and
   > the placement(s) — often more than one, as A/B arms — renders the Current-vs-Proposed
   > sheet via `product:design-idea`, and writes it to the row's Screenshot property.

   **Ideate ⇄ design is bounded.** The designer may hand a note back (a placement the screen
   cannot take, a copy candidate, a constraint) and the idea may be re-briefed once — two rounds
   at most, then it proceeds to share with what it has. The capture it designs on comes from
   the screenshot store (`AuraConsumerAppSimTests/knowledge/screenshots/`), kept current by the
   weekly harvest, so no simulator time is spent here either.

   **Nothing is posted on a first render.** A sheet goes to Slack only after an adversarial
   review round has run over it and its findings are fixed — art direction, engineering,
   growth/UX (`product:designer` step 7). That is a gate on step 4, not a suggestion: a
   thread is the one place a half-finished design costs the team's attention rather than
   ours. Jerome, 2026-09-30: *"we should definitely add a step to review our design
   proposition before we post to slack."*

   Run the lenses **in-session** by default. Sub-agents are one `opus` call per lens per
   round, which is the most expensive thing in this loop — reach for them only when the
   sheet count makes reading them all yourself the slower path, and say what it will cost
   before you do. That same day the reviewers were spawned and stopped again on the spot,
   because the week's budget was at 80 %.
4. **Share** the newly-pending rows to the loop channel:
   > Invoke `product:share-ideas` with **`post-pending --confirm`** — post each pending, unshared row as a short summary, the
   > reasoning and hypothesis as its first two replies, and every file on the row's Screenshot
   > (the sheet, then the variants) into the thread; the decision protocol rides in every post
   > and the thread URL is written back.

**Unattended, it passes `--confirm`** to `record-ideas` and `share-ideas` — dispatching
`ideate` from a tick is the standing approval. See [`references/unattended.md`](references/unattended.md#unattended-legs-pass---confirm).

**A sim-test run is an optional extra source, not the default.** When a sim-test session
with the `ideas` analyzer has just run for other reasons, its `findings_ideas.json` can be
recorded in step 2 alongside the brainstorm. Nothing in the loop schedules one for ideation.

**Dispatch it, like `build`.** A brainstorm reads widely and a design round renders and
reviews several sheets; spawn one background agent for the domain, and read the new rows and
posts on the next tick.

### `digest` — the roll-up post

Posts one summary message to **`#ai-loop` (`C0C5TSVA8U9`)**, the one status channel every loop
shares — never to the loop's own channel. A loop channel (`#ai-loop-aurie-features`,
`#ai-loop-aura-features`) carries **one thread per idea and nothing else**, so a roll-up there
would be the one post with no idea to belong to. Open the digest with the loop's name
(`Aurie Features · ready for review`, `Aurie Features · the week`) so it reads in a channel
other loops post to as well. Two flavours, because the loop needs both a fast review nudge and
a slower retrospective:

- **`digest --daily`** (the review nudge) — **what is ready for a human right now**: rows
  `implementing` with an open BugBot-clean PR (ready for code-owner review — the merge is Gate 2), and any `approved` row
  the build has not started. This is the "collect what is ready to review" digest the
  throughput target depends on — a human clears it in minutes.
- **`digest --weekly`** (the retrospective) — the week's flow: **new ideas, decisions taken,
  what is in review, live experiments, and results** — each with a link to its Notion row.

Compose the body from `status` output plus a `last seen` / `decided at` window over the Notion
rows, then post it by invoking the **`shared:post-to-slack`** skill (composition — do not reach
into another plugin's script path):

> Invoke `shared:post-to-slack` — post `$DIGEST` to `#ai-loop` `C0C5TSVA8U9` (both apps), no
> unfurl, no thread, `footer=product:features-loop · daily` (or `· weekly`) with the app in the
> footer extra (`aurie` / `aura`), so the post is attributed to the loop, and `run` can tell the
> two flavours and the two apps apart when it checks which is due. **This is a pre-authorized
> unattended post** — under a scheduled `run` there is no human to confirm, so tell the composed
> skill to post non-interactively (skip its plan-and-confirm step); invoking `digest` is standing
> approval to post it.

No per-idea threads, no reactions — the digest is a summary, not a decision surface. Decisions
still happen in each idea's own thread.

**The loop channel carries ideas only.** Anything about one idea —
a question answered, the build started or blocked, the PR ready, a TestFlight build, a drop —
is a **reply in that idea's thread**, never its own post. The legs already reply there
(`share-ideas`, `harvest-idea-decisions`, `build-idea`, `ship-idea`); a tick that has
something to say about an idea outside them replies in its thread too.

**A daily posts only when something changed.** Compare what the digest **reports** — the
set of `IDEA-N` → state it lists (building, approved and not started, waiting on Gate 1, in
review) — against the last `· daily` post for this app in `#ai-loop`, read the same way. Never compare the
raw text: the post ends with the attribution footer, whose date and runner change every day,
so a text match never fires. If the set is the same, skip it and say so in the tick summary. A daily that repeats yesterday's is noise
the team learns to ignore. The weekly posts on its slot regardless; a week with no movement
is worth saying.

### `run` — one autonomous tick

What [`start`](#start--start-the-loop-in-this-session) repeats. One tick, zero human input,
in this order:

1. **`harvest`**, in the tick — pull the decisions the team left since the last one. This
   writes each clear verdict, puts the 🚀 or ❌ on its thread, answers questions from the
   priors in-thread, hands a "like the idea, not the design" ask to the designer, and
   addresses every `needs_follow_up` — a new message in any open idea thread. Every
   `approved` row without a card then gets one: `build_idea.py card <IDEA-N> --post`.
2. **Check dispatched work** from earlier ticks — see *check the work, not the worker*
   under [`build`](#build--the-next-approved-idea-one-at-a-time). A finished build gets
   `build-idea finish --post`, `gh pr ready <PR URL>` and `build-idea ready` for whichever it
   did not reach; any other stall is re-dispatched. A ready-for-review PR is done. The same holds for ideation: any row still `pending` with
   no Slack thread and **no live ideation agent** stopped short of design or share — a pass
   that ended early, or one cut off by a restart. Resume it here: design it (with its review
   round) if it has no Screenshot, then share. Its `First seen` resets the ideation clock, so
   nothing else would pick it up for days.
3. **`build`**, dispatched — if the approved queue is non-empty **and** no build is in flight.
   **Skip a row `plan` refuses** (exit 2: held, pre-consent with no bucketing path, …) and
   pick the head with `build_idea.py plan` — see [`build`](#build--the-next-approved-idea-one-at-a-time).
   List each refused idea, with `plan`'s reason, in the tick summary under what waits on a human.
3b. **`ship`**, in the tick — for each `implementing` row whose PR has merged: `ship-idea merged
   <IDEA-N> --post`, then `ship-idea request-experiment <IDEA-N> --post` (asks Viktor), then
   `check-experiment` on later ticks — it replies with the `#support-bot` link once Viktor creates
   it, swaps 🚀 for ✅ once the card is Done, refuses to go live while the record is pinned to
   one arm, and marks it live on release.
3c. **`qa`**, dispatched — a merged row with no QA card **and no QA agent in flight** gets one
   from `qa:qa-linear-task`, run unattended on simulators across the device matrix, one runner
   per card; the next tick reads the card and replies *QA passed* or the bug links in the
   thread. Guard, overrides, results: [`references/qa-step.md`](references/qa-step.md).
3d. **`learn`**, in the tick — `learn_idea.py sweep --apply --announce` writes the verdict Yawar and
   Viktor reached and its learning for every ended loop experiment, replies in its thread,
   and files the code-cleanup card for it: [`references/learn-step.md`](references/learn-step.md).
3e. **`cleanup`**, dispatched — one loop cleanup card with no removal PR **and no removal agent in
   flight** gets one background agent that removes that experiment's code and opens a ready PR:
   [`references/cleanup-step.md`](references/cleanup-step.md).
4. **`ideate`**, dispatched — when it is due **and** no ideation agent this session
   dispatched is still running. It is due when the **newest** `First seen` across the whole
   backlog is more than three and a half days old — the last time the loop ideated on
   anything, which paces it to about twice a week. (Not the least-recently-ideated domain:
   with eleven domains one is nearly always stale, and every tick would dispatch another.)
   The rotation then decides *which* domain. The in-flight guard covers the gap before
   `record-ideas` writes its first row, when the backlog still looks stale.
5. **`digest`** — `--daily` when no **daily** digest for this app is in `#ai-loop` from the
   last 24 hours **and** its body differs from the last daily's, `--weekly` when no **weekly** one is
   from the last seven days. Each flavour posts
   with its own footer — `product:features-loop · daily` or `· weekly` — and the check reads
   only posts carrying that flavour's marker; one shared marker would let the daily post
   hide the weekly forever.

   Read `#ai-loop` via **`shared:read-slack`** (`--oldest` 8 days), this skill's and app's posts only.

A single self-pacing loop has no schedule to say which slot a tick is, so steps 4 and 5
decide it from **the state the legs leave behind** — the newest `First seen` in the backlog,
the last digest of each flavour in the channel. That is also what makes a restarted loop pick
up where the last one stopped.

End every `run` with the [tick summary](#tick-summary); a quiet tick says so in one line.

## Cadence

Everything below runs from one loop, started with
[`start`](#start--start-the-loop-in-this-session); each tick is a `run`. The body invokes
**this** skill by name so every tick picks up the current definitions from disk.

| Leg | When a tick runs it | How | Human gate? |
|---|---|---|---|
| `harvest` + `card` | every tick | in the tick | no |
| `build` | approved queue non-empty, no build agent running, no unfinished build (an `implementing` row short of a ready-for-review PR) | background agent | **PR review is human** |
| `ideate` | newest `First seen` in the backlog over 3½ days old, no ideation agent running | background agent | no |
| `digest --daily` | no `· daily` post in 24 h, and something changed since the last one | in the tick | no |
| `digest --weekly` | no `· weekly` post in 7 days | in the tick | no |
| `ship` | an `implementing` row's PR merged | in the tick | **the merge is the gate** |
| `learn` | an `experiment_running` row's experiment ended | in the tick | **Yawar and Viktor decide the verdict** |
| `cleanup` | a loop cleanup card with no removal PR, no removal agent running | background agent | **PR review is human** |
| Gate 1 / Gate 2 | — | Gate 1: Steve in Slack · Gate 2: the PR merge | **never automated** |

The loop self-paces: about every 20–30 minutes while quiet, sooner when a dispatched agent
finishes. Human gates are never on a timer; the next tick reads the team's replies.

### Domain rotation

`ideate` covers **one domain per run**, cycling so every surface gets fresh eyes and no single
domain is over-mined. Twice a week is roughly what keeps the build queue fed. The domains are
the Notion `Domain` select values, which `brainstorm-ideas` scopes its reading by:

```
onboarding · paywall · home/feed · conversation · journal · grounding · affirmations · profile · settings · notifications · growth/referral
```

(`other` is the catch-all bucket, not a rotation target.) Pick the **least-recently-ideated**
domain: the one whose most recent `First seen` idea is oldest (or which has never been
ideated). That reads out of the same Notion rows `status` already loads — no separate cursor to
keep. Pass that domain to `brainstorm-ideas` so it reads that domain's signal rows and
experiments first.

## Tick summary

Every `run` (and every manual multi-leg invocation) ends with one readout:

- **harvest** — threads swept → decisions written (by verdict) → `needs_judgment` handed back →
  reason-less rejections recorded with reason `other`.
- **dispatched work** — each agent in flight: what it is on, and whether its row and PR say
  done, in progress, or stalled.
- **build** — the idea dispatched (IDEA-N · title · experiment flag), the PR a finished one
  opened, or why none (empty queue / build in flight / collision skip).
- **ideate** — the domain dispatched and the rows it recorded, or why not (not due).
- **digest** — posted (which flavour + permalink) or skipped (not a digest slot, or nothing
  changed since the last daily).
- **queue after the tick** — approved / shared / implementing depths, and the single next action.
- **failures & skips** — anything a leg couldn't do, with the single-leg skill to re-run it.
  A leg that skipped for a transient reason leaves no marker, so it retries next tick — say so
  rather than reading a quiet leg as a clean one.

## Aura readiness

Before `--app aura` runs: an Aura Features ideas database from the same schema, a sim-test
target for its builds, and the data source wired into the `--app` table. The checklist is
in [`references/unattended.md`](references/unattended.md#aura-readiness).

## Notes & edge cases

- **Composition, not reimplementation.** If a leg changes, this inherits it for free — do not
  copy a leg's steps into this file.
- **"0 done" is not "0 outstanding".** Because each leg dedups on the row, an item skipped for
  a transient reason (a token, a 5xx, a lagging findings file) keeps no marker and retries next
  tick. Carry each leg's own reported failures into the summary.
- **The gates are the point.** The loop's value is the two human decisions and the recorded
  reasons behind them. A tick that races past a gate is a bug, not throughput.
- **Source of truth is Notion.** The `Learnings/Loop/Aurie Features Ideas` page describes the process;
  update it in the same change whenever the loop's shape changes (a new gate, status, or
  ownership boundary).

## References

- Runbook: AI Knowledge → `Procedures/AI Native/Features Loop`
- Unattended running — dispatch, in-flight, stalls, restarts, `--confirm`, Aura readiness:
  [`references/unattended.md`](references/unattended.md)
- Process (source of truth): AI Knowledge → `Learnings/Loop/Aurie Features Ideas`
- Model composer: `data:run-all-analysis-cadence`
- Legs: [`brainstorm-ideas`](../brainstorm-ideas/SKILL.md) · [`record-ideas`](../record-ideas/SKILL.md) ·
  [`share-ideas`](../share-ideas/SKILL.md) · [`harvest-idea-decisions`](../harvest-idea-decisions/SKILL.md) ·
  [`build-idea`](../build-idea/SKILL.md) · [`ship-idea`](../ship-idea/SKILL.md) ·
  [`learn-idea`](../learn-idea/SKILL.md) · [`manage-ideas`](../manage-ideas/SKILL.md) (all `product:`)
- QA step — overrides, device matrix, reading the card: [`references/qa-step.md`](references/qa-step.md)
- Code cleanup — card via `eng:sweep-ended-experiments`, removal PR by the loop's own agent:
  [`references/cleanup-step.md`](references/cleanup-step.md)
