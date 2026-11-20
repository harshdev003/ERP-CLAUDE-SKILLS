---
name: learn-idea
description: >
  Close the loop: carry an ended experiment's verdict back to the
  `Learnings/Loop/{Loop}` idea that started it — Status `won` / `lost` /
  `inconclusive` with a one-line learning, a reply in the idea's Slack thread,
  and an ideas funnel that renders from the database alone. Use when the user
  says "the experiment ended", "record the result on the idea", "sweep ended
  experiments back to the ideas", or "show the ideas funnel". Default target is
  the Aurie Features Ideas database; pass `--data-source-id {id}` for the other loops.

metadata:
  version: "1.8.1"
knowledge:
  reads:
    - Guide/Index
    - Learnings/Loop/Aurie Features Ideas
    - Learnings/Experiments
    - Tools/Notion
    - Tools/Slack
  writes:
    - Learnings/Loop/Aurie Features Ideas
    - Learnings/Loop/Loop Learnings   # one learning per ended loop experiment
---

# learn-idea

## Why this exists

Without it the loop has no memory. An idea that lost still reads as
`experiment_running` forever, the ideation prior-knowledge block never learns the
question was answered, and the same proposal returns next month in different
words.

That is not hypothetical. A paywall testimonial was proposed again *after*
`aiTestimonials` had already lost 21 days of traffic proving it, and a delayed
paywall close after `aiDelayedClose` had lost too. The prior-knowledge block
(PROD-5922) can only suppress what the database knows, and the database only
knows what this skill writes.

## What it will never do

1. **It never decides a verdict.** The verdict is read from `Learnings/Experiments`, where `data:analyze-experiment` writes it.
2. **It never interprets `Turned off`.** See below.
3. **It never writes to a row whose experiment did not run** — only `experiment_running` can receive a result.

## `Turned off` is not a verdict

122 rows in `Learnings/Experiments` carry `Turned off`, and it means two
different things: *it lost, so we killed it*, and *it was broken, so we killed
it*. `aurieCustomWebPaywall` was turned off for a social-login checkout bug and
re-run as `aurieCustomWebPaywall2` — that is not a result about the idea at all.

So `Turned off` is refused, and `--status` must be passed explicitly once someone
knows which it was. This is the asymmetry worth understanding: writing `lost`
onto an idea that was never actually tested silently removes it from circulation,
because the prior-knowledge block treats a `lost` row as answered. A false
"already answered" is worse than no answer, and nothing errors when it happens.

| Experiments `Status` | Ideas `Status` |
|---|---|
| `Won` / `Lost` / `Inconclusive` | `won` / `lost` / `inconclusive` |
| `Running` / `In flight` / blank | *nothing — not ended* |
| `Turned off` | *refused — pass `--status`* |
| `Stopped splitting` | *refused, and surfaced — needs a person* |
| `Feature flag` | *refused, and surfaced — will never have a verdict* |
| anything else | *refused — a new select value must not become a guess* |

## Two statuses that will never resolve on their own

`data:sync-experiments` marks a row `Stopped splitting` when the catalogue shows
the experiment is no longer splitting traffic but the server never stamped an
`endedAt` — the shape a rollout takes, and equally the shape of a variant forced
before release, a kill by an unreachable `minVersion`, and a permanent feature
flag. A person then resolves it into a verdict, or marks it `Feature flag` if it
is a kill switch rather than a test ([PROD-6116](https://linear.app/aura-health/issue/PROD-6116)).

Both are refused here for the same reason `Turned off` is. What is different is
that **waiting does not help**. A `Running` row resolves itself when the
experiment ends; these two resolve only when somebody decides, so `sweep` prints
them as `NEEDS A DECISION` and counts them apart from the rows that are merely
not finished yet:

```
= 0a05fffc41b9 held — experiment status is 'Running' — not ended yet.
! 7c31ab90e2f4 NEEDS A DECISION — experiment status is 'Feature flag' …

2 to write, 5 waiting on an experiment still running, 1 waiting on a person.
```

Collapsing those two counts is the failure the card names: an idea sitting at
`experiment_running` behind a row that cannot change reads exactly like an idea
whose experiment is still running.

## Where it hooks in

`data:sweep-ended-experiments` — named on the card — **does not exist**. The
skills that do are `data:sync-experiments` (imports the catalog, sets `End Date`),
`data:analyze-experiment` (writes the verdict onto the row),
`data:monitor-all-experiments` (daily, running only) and
`data:update-experiments-top10` (reads ended rows).

The hook point is therefore **after `data:analyze-experiment` writes a verdict**.
Ideas-database logic lives here rather than inside a `data` skill, matching
`build-idea` and `ship-idea`.

## Code cleanup on a result

A verdict means the experiment has **ended**, so its code can come out — the flag
either way, plus the losing path on a win once the winner is rolled out, or the
treatment on a loss. Unlike an abandon (which the loop files itself, because its
experiment may never have run), a result **can** go through
[`eng:sweep-ended-experiments`](../../../eng/skills/sweep-ended-experiments/SKILL.md):
the server reports it ended, so the sweep detects it still in the target repo and
files the proper checklist card in the Mobile team.

So on a result, `record` prints the exact routing (the features loop then removes the code
with its own agent rather than routing the card to Cursor): run
`eng:sweep-ended-experiments key={experiment}` to file the removal card, then re-run
`record {ref} --cleanup-issue <url>` to link it on the row's `Cleanup issue`. That
keeps **idea → shipped code → removed code** an unbroken trail. The link is
write-once — an existing `Cleanup issue` is never overwritten, so re-running is
safe.

## The learning is logged where ideation reads it

A verdict alone says *what* happened; the next idea needs *why* and *what to do about it*. So
every `record` and `sweep --apply` also writes a learning, composed by `compose_learning` from
the sources where each part is true:

| Part | From |
|---|---|
| What was tested | the idea row's `Variants` (else `<key> arm a vs control`) |
| The result | the monitor's end post: winner, metric, confidence, n |
| Who ended it and why | the #data-experiment `Experiment ended` post |
| Implication | `--implication` when given, else the verdict's default |

It is written to the idea row's `Learning` property and to one row per idea in
`Learnings/Loop/Loop Learnings` (Title, Idea ID, Verdict, Screen, Domain, Tested, Result,
Ended by, Implication, Source), keyed by `Idea ID` so a correction rewrites it. A failed log
never undoes the verdict; `learning <ref> --apply` writes it again.

The default implication is generic. The caller states the real one — one sentence on what the
next idea on that surface should do or avoid — with `learning <ref> --implication "…" --apply`.
`record-ideas`' already-answered warning shows it, and `brainstorm-ideas` reads the log before
proposing. Only the loop's own experiments get a learning: rows of the loop's ideas database.

## The funnel is cumulative

```
month        proposed     shared   approved      built   released    decided
2026-04            27         25          6          0          0          0
```

A row counts at **every stage it reached**, so the drop between columns is the
conversion at that step. Counted by current status instead, each row would appear
once and every stage would read as a near-total loss — a chart that renders
perfectly and means nothing.

`rejected` and `deferred` sit at `shared`: they were proposed and shown to the
team, then not approved. That is what makes the shared → approved drop the real
decision rate rather than an artefact.

Months are **cohorts keyed on `First seen`** — when the idea was proposed, not
when its experiment ended. Keyed on the end date, every stage would be a
different population and the rates would be meaningless.

## Why it ended comes from Slack

The verdict is the one the people who ended the test declared, so before writing it
`sweep`, `plan` and `record` look for the end posts by experiment key (`search.messages`,
`SLACK_USER_TOKEN`):

- the experiment monitor's **`Experiment Won|Lost|Inconclusive: <key>`** post in
  `#abtest-monitoring` — the verdict, the winner, the metric with its confidence, and who
  stopped it;
- the **`Experiment ended: \`<key>\` …`** post in `#data-experiment` — the reason.

The `Learnings/Experiments` row is one input. When an end post names a different verdict,
**the end post wins**, the disagreement is printed for the tick summary, and the row is left
alone: it belongs to the experiment owners. The `Quick status note` and the in-thread reply
carry the reason — the winner and metric quoted from the post, who ended it, and both
permalinks.

An experiment that ended with **no end post** gets no verdict. `sweep --apply --announce`
asks Yawar once in the idea's thread why it ended and what the result was (marker
`Asked why it ended` on the note), and lists the row as waiting on a person. When Slack
cannot be searched at all, the row is held rather than written without a reason.

**Only the loop's own experiments.** Every command works on rows of the loop's ideas
database and the `Experiment` key on each row. The cleanup instruction names that key —
`eng:sweep-ended-experiments key=<key>`, never a full sweep — and nothing here touches an
experiment outside those rows.

**Correcting a recorded verdict.** `record <ref> --correct --status <won|lost|inconclusive>
--learning "…"` replaces the verdict on a row that already has one.

## CLI

`plan` and `record` take a `ref` — the **Idea ID** (`42`, `#42`, `IDEA-42`), the fingerprint, or a title substring.

```bash
SCRIPT=${CLAUDE_PLUGIN_ROOT}/skills/learn-idea/scripts/learn_idea.py

# What would the write-back do for this row? No writes.
python3 $SCRIPT plan 0a05fffc41b9

# Every experiment_running row whose experiment has ended. Dry by default.
python3 $SCRIPT sweep
python3 $SCRIPT sweep --apply --announce

# One row, with the learning stated.
python3 $SCRIPT record 0a05fffc41b9 --learning "No lift on first-session engagement." --announce

# The learning, with the implication stated (re-runnable; no verdict change).
python3 $SCRIPT learning dcb66925d978 --implication "Keep the proven value-prop headline on the package paywall." --apply

# Fix a verdict recorded wrong.
python3 $SCRIPT record dcb66925d978 --correct --status lost --learning "Monitor: Lost, −27.9% at 87.6%; Steve stopped it."

# A turned-off experiment, once you know which kind it was.
python3 $SCRIPT record 0a05fffc41b9 --status lost --learning "Killed after 9 days, control ahead."

# The funnel.
python3 $SCRIPT funnel
python3 $SCRIPT funnel --month 2026-04
```

`sweep` is a dry run unless `--apply` is passed, matching `record-ideas`.

**`--status` requires `--learning`.** The override exists for `Turned off`, where
the verdict is a human judgement the database does not carry. Recording one with
no stated reason puts an unexplained answer in front of every future ideation
run, in the field that run reads as *the* answer.

**A Slack failure never costs a record.** The Notion write always happens first,
and `announce` catches `SystemExit` as well as `Exception` — `slack_post` calls
`sys.exit()` on a missing token, and `SystemExit` derives from `BaseException`,
so a bare `except Exception` does not stop it. Uncaught, one bad reply in
`sweep --apply --announce` would kill the process after the first row and leave
every remaining ready row at `experiment_running`.

## Related skills

- [`product:ship-idea`](../ship-idea/SKILL.md) — writes the `experiment_running` this leg starts from.
- `data:analyze-experiment` — writes the verdict this reads.
- [`product:record-ideas`](../record-ideas/SKILL.md) — its `ANSWERED_STATUSES` is what makes a `lost` row suppress a re-proposal.

## References

- Notion: [Aurie Features Ideas](https://www.notion.so/7e721e75374645a7acfa085e3aeca6df) (data source `8d14f3eb-0c55-4f25-9322-ab2b2a69f092`)
- Notion: [Learnings/Experiments](https://www.notion.so/2cbe45a3246040658b9f20559b5b452b) (data source `c0209685-17ac-49e2-849a-85fa8178decd`)
- Linear: [PROD-5926](https://linear.app/aura-health/issue/PROD-5926) (parent [PROD-5917](https://linear.app/aura-health/issue/PROD-5917))
