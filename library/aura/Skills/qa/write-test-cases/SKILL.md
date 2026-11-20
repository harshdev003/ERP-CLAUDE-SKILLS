---
name: write-test-cases
description: >
  Author and update the standing Aurie or Aura regression suite in AI Knowledge — the durable
  per-feature test cases that outlive any one release. Reads the iOS repo's own sim-test
  knowledge for screen identifiers, drivability and live experiment arms, drafts cases in
  the house format, dedups against what is already in the Test Cases database, and writes
  the rows. Trigger on "write test cases for {feature}", "add a QA case for this screen",
  "the suite is missing cases for X", "update the test cases after this change", "retire
  that test case", or "what does the regression suite cover for {feature}". Do NOT use to
  QA a single pull request (`qa:qa-linear-task`), to run the suite (`qa:run-test-cases`),
  or to write cases for a surface that has its own skill (`qa:qa-aurie-usage-limits`).
metadata:
  version: "1.2.0"
knowledge:
  reads:
    - Guide/Index
    - QA
    - QA/Conventions
    - QA/Test Plan and Test Cases/Aurie/Test Cases
    - QA/Test Plan and Test Cases/Aura/Aura Mobile Test Cases
  writes:
    - QA/Test Plan and Test Cases/Aurie/Test Cases
    - QA/Test Plan and Test Cases/Aura/Aura Mobile Test Cases
---

# Write Aurie regression test cases

You are adding to a suite someone will run without you in the room. A case that only makes
sense to its author is a case that gets skipped, or worse, passed on a guess.

**The division of labour is the whole design.** The case says *what* to check and *what
good looks like*. The iOS repo's `AuraConsumerAppSimTests/knowledge/` says *how to drive
it* — identifiers, the way off each screen, the quirks. Copy identifiers into a case and
you have created a second source of truth that will be wrong within a month. Name the
screen; let the repo say how to reach it.

This skill is **REST-first**. Enumerating and filtering a database is not something the
Notion MCP can do — it is semantic, capped at 25 results and has no cursor. Go straight to
the bundled script.

```bash
S=${CLAUDE_PLUGIN_ROOT}/skills/write-test-cases/scripts/qa_notion.py
python3 $S list --feature ONB                 # what already exists
python3 $S list --automatable-only --priority P1
python3 $S get --id ONB-003                   # one case, properties + body
python3 $S next-id --prefix VOI               # next free id in a feature
python3 $S --app aura list --feature I18N     # the same, against the Aura suite
```


## Two suites: Aurie and Aura

The same scripts serve both apps' suites. Pass `--app aura` (or set `QA_APP=aura`) to
read and write the Aura one; with neither, they act on Aurie.

| | Aurie | Aura |
|---|---|---|
| Test Cases | `QA/Test Plan and Test Cases/Aurie/Test Cases` | `QA/Test Plan and Test Cases/Aura/Aura Mobile Test Cases` |
| Test Runs | `QA/Test Execution & Reports/Aurie/Test Runs` | `QA/Test Execution & Reports/Aura/Test Runs` |
| App repo | `AuraConsumerApp` (Swift) | `Aura` (React Native) |
| Sim-test knowledge | `AuraConsumerAppSimTests/knowledge/` | `AuraSimTests/knowledge/` |

Aura's case rows add two fields:
- **Locale scope**: `All locales`, or `English only` for surfaces `UserController.canShow*` hides outside English.
- **Environment**: `Prod`, `Dev` or `Either`.

Its prefixes add `SRC LIB COA LIV COM FAM STK AUR I18N` to the shared set. Wherever this
skill names an Aurie path, read the Aura column instead.

## Step 0: Find the gap before choosing what to write

Most new cases should come from a **surface the suite asserts nothing about**, not from
a hunch. Two lists produce them, and both are one command:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-test-cases/scripts/sync_test_cases.py \
  check --repo /path/to/AuraConsumerApp --ignore-screens orb_reactions_lab,paymentPending_screen
```

- **Screen docs no active case names.** `AuraConsumerAppSimTests/knowledge/screens/` holds
  one file per surface the app can show. A file no case references is a documented screen
  with no assertion on it. Walk that list first.
- **Live experiments no case mentions.** A running arm nothing tests.

For each screen on the list, read its knowledge doc before drafting — the doc says how it is
reached, what gates it, what its elements are called, and usually which trap has already
stalled a run on it. Then decide:

- **Extend an existing case** when the screen is a step inside a flow a case already drives.
  A case that reaches the screen and asserts one more thing about it is better than a second
  case that repeats the journey.
- **Write a new case** when the screen has its own behaviour — a sheet with its own controls,
  a fallback path, a one-shot surface.
- **Exclude it, with the reason on the card** when it is not a feature: a DEBUG-only harness
  (`orb_reactions_lab`), or a screen whose experiment rolled out away (`paymentPending_screen`).
  Add it to `--ignore-screens` so the list stays actionable.

A screen name in a case's `Screens` field must match the knowledge file's stem **exactly,
including case** — `livesession_screen`, not `liveSession_screen`. The sync check reports a
mismatch as `screen-cased-differently`; it looks right to a reader and fails a lookup.

## Step 1: Read before writing

Three sources, in this order. Skipping any of them produces cases that cannot be run.

1. **`QA/Conventions`** — the body shape, the runner tags, the priority scale, and what
   each verdict means. Everything you write has to conform to it.
2. **The existing rows for the feature** (`list --feature <PREFIX>`). You are extending a
   suite, not starting one. A near-duplicate case is worse than no case: it doubles the
   run cost and the two copies drift apart.
3. **The iOS repo's sim-test knowledge**, at
   `AuraConsumerApp/AuraConsumerAppSimTests/knowledge/`:
   - `screens/<name>.md` — one file per screen. The **Advance** section says what actually
     leaves the screen and what must be true first.
   - `flows.md` — the multi-screen sequences and their real ordering.
   - `experiments.md` — which arm is live. **Read this before writing any case for a
     screen that is behind an experiment.**
   - `workarounds.md` — the quirks that make a step flaky.

## Step 2: Check the experiment gate first

This is the step people skip, and it is the one that decides whether the suite is honest.

An experiment rolled out to control turns a whole screen family off. A case written for a
screen nobody sees will fail every run forever, for a reason that has nothing to do with
quality — and a suite that is permanently red is a suite nobody reads.

So, for every case touching an experiment-gated screen, record the key and the arm in
**Experiment gate**, in the words `experiments.md` uses. If the screen is currently off
for everyone, you may still write the case — but say so in Notes, and say what would have
to change for it to run.

## Step 3: Draft

Use [`references/case-template.md`](references/case-template.md) for the exact body and the
property values. The rules that matter most:

- **One case, one verdict.** If the body needs "and also check…", that is a second case.
- **Action, then observable result.** A step whose expected column reads "works correctly"
  can neither pass nor fail.
- **Say what a blocked state looks like.** Most CTAs in this app stay enabled and simply do
  nothing until a precondition is met, so a successful tap proves nothing. Where a step can
  silently not advance, the expected column says how to tell.
- **Every feature gets at least one P1 and at least one forced-failure case** — a wrong
  password, a dropped network, a refused purchase, a denied permission. A suite that only
  walks the happy path is how silent failure branches ship.
- **Tag honestly.** `human` and `device` are not admissions of defeat; they are how the run
  report tells the truth about what an unattended pass did not cover. Tagging a case `sim`
  because you wish it were automatable produces a false pass.
- **Never write a password into a case body.** Declare the account *shape*; the run picks
  or provisions one.

**Show the draft in chat and wait for approval before writing anything.** Reviewing a case
list costs a minute; rewriting thirty rows costs an afternoon.

## Step 4: Write the rows

Ids are permanent. `next-id --prefix <FEAT>` gives the next free number for a feature;
never reuse a number, even from a retired case, because run reports and bug cards refer to
them.

To **retire** a case, set its Status to `Retired` rather than deleting the row. Deleting it
breaks every report that mentions it.

To **update** a case after a change to the app, edit the row in place and leave the id
alone. A changed expectation with the same id is exactly what history should show.

## Step 5: Report

Say what was created, what was updated, and — specifically — which screens in the repo's
`knowledge/screens/` are still not referenced by any case. That list is the honest measure
of coverage, and it is the thing a reader cannot work out for themselves.

## Related

- `qa:run-test-cases` — executes what this skill writes.
- `qa:qa-linear-task` — change-scoped QA for one pull request. Those cases are throwaway
  and live on a Linear card; these are durable and live in Notion. When a PR's QA finds a
  gap that should be checked on every release, that is the signal to add a case here.
