---
name: sync-test-cases
description: Check the Aurie or Aura regression suite against the app it tests, and report every case whose premise no longer holds — an experiment gate that has since started, ended or rolled out, or a screen whose knowledge file was renamed or removed. Also lists live experiments no case mentions. Use when the user says "sync the test cases", "are any test cases stale", "check the suite against the app", "did an experiment break a case", "what cases need updating", or after an experiment starts or ends, a feature ships, or a screen is added or renamed.
metadata:
  version: "1.2.0"
knowledge:
  reads:
    - Guide/Index
    - QA/Conventions
    - QA/Test Plan and Test Cases/Aurie/Test Cases
    - QA/Test Plan and Test Cases/Aura/Aura Mobile Test Cases
---

# Sync the test cases with the app

A regression case encodes an assumption about the product. When the product moves, the
case does not — and nothing notices, because a stale case does not fail. It reports
**Not run**, which reads like a scheduling gap rather than a wrong assertion.

This has already happened three times in one suite:

| Case | What it asserted | What was true |
|---|---|---|
| `CNV-005` | the image-upload experiment does not exist | it was RUNNING, and the control it describes was reachable |
| `JRN-001` | a journal entry can be written by hand | the composer belonged to an arm that rolled out to control |
| `JRN-002` | an empty hand-written entry is rejected | there is no hand-written entry any more |

All three sat in the suite for weeks. The first was found by an agent trying to run it;
the other two by an agent reporting a precondition it could not meet. None was found by
the suite.

This skill is the Notion-side counterpart to `sim_experiments.py check` in the app repo,
which already stops an experiment being added or removed without the docs moving.

## Run it

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-test-cases/scripts/sync_test_cases.py \
  check --repo /path/to/AuraConsumerApp

# the Aura suite, against the Aura repo
python3 ${CLAUDE_PLUGIN_ROOT}/skills/sync-test-cases/scripts/sync_test_cases.py \
  check --app aura --repo /path/to/Aura
```

`--app aura` (or `QA_APP=aura`) switches the suite and the app-repo layout:
- Screen docs come from `AuraSimTests/knowledge/screens/`.
- Experiment keys come from `sim_experiments.py keys --plain`, because the Aura app is React
  Native and has no `Experiments.swift`.
- Live state comes from `sim_experiments.py state`. A single live arm (`active='a'`) reads as
  ROLLED OUT, several arms as RUNNING. `AURA_API_TOKEN` is read from the environment or
  `~/.claude/settings.json`.

`--include-drafts` checks Draft cases as though they were live. Use it on a newly written
suite before review, when every row is still a draft and the default check would skip them
all.

`--json` for machine-readable output. Exit `0` when nothing drifted, `1` when something did.
`--ignore-screens a,b` leaves named screen docs out of the coverage list.

Run it **before a regression pass**, so the pass is not spent discovering stale cases one
timeout at a time, and **after an experiment starts or ends**, which is when cases rot.

## What it checks

**Experiment gates.** Every case whose `Experiment gate` names an experiment is compared
against live state from `sim_experiments.py state` and the key list in `Experiments.swift`:

| Finding | Meaning |
|---|---|
| `gate-says-absent` | the gate says the experiment is not created or not live, and it is running. The case asserts something false. |
| `gate-settled` | the experiment has ENDED or ROLLED OUT. Confirm the case asserts the arm that survived, not the one that lost. |
| `gate-key-gone` | the gate names a key that is no longer in `Experiments.swift`. |
| `gate-unrecognised` | the gate text names no known key — usually prose where a key was meant. |

**Screens.** Every name in a case's `Screens` is checked against
`knowledge/screens/*.md`:

| Finding | Meaning |
|---|---|
| `screen-missing` | no knowledge file — the screen was renamed or removed. |
| `screen-cased-differently` | the file exists under a different spelling, so a lookup by name misses it. |

**Coverage, both ways.** Live experiments that no case mentions, and — the more useful
list — **screen docs that no active case names**. A documented surface the suite asserts
nothing about is where the next case comes from. Neither list is a defect on its own.

Screens that genuinely have no place in the suite go in `--ignore-screens`, with the reason
recorded on the card that decided it. Two are excluded today: `orb_reactions_lab` (a
DEBUG-only tuning harness, absent from release builds) and `paymentPending_screen` (its
experiment rolled out to control on 2026-06-16, so the screen no longer appears).

Retired cases are skipped: a retired case is *allowed* to describe behaviour that no longer
exists, which is the point of retiring rather than deleting it.

## What to do with a finding

**The skill reports; it does not edit.** Deciding which arm survived, and whether a case
should be rewritten or retired, is a judgement about the product that a diff cannot make.

- **The premise is false and nothing of the case survives** → set `Status: Retired` and write
  a note saying what changed. Do not delete it: the note is what stops someone re-adding the
  same case next quarter.
- **The premise is false but the assertions still matter** → rewrite it against the surviving
  behaviour. `JRN-001` kept its persistence and edit coverage after losing its first step.
- **The experiment settled and the case asserts the surviving arm** → nothing to do; the
  finding is a prompt to confirm, not a defect.
- **An arm-dependent case that asserts absence** — "the control shows no X" — should name the
  arm in its assertion, so that when the arm moves the case inverts loudly instead of quietly.

## Limits

- It reads gates as **text**, because that is what they are. A gate written as prose without
  naming its key reports `gate-unrecognised` — that is a prompt to name the key, not a bug.
- It cannot tell whether a case's **steps** still match a screen, only whether the screen's
  knowledge file exists. A screen that changed shape without being renamed passes this check
  and is caught by running the case.
- Live state needs the experiment server reachable. Without it the check still runs against
  `Experiments.swift` and reports which keys exist, but not whether they are running.

## Related

- `qa:write-test-cases` — authoring and the house style.
- `qa:run-test-cases` — running a pass. Run this check first.
- `sim_experiments.py check` in the app repo — the same idea for the experiments doc, enforced
  in the pre-commit hook.

# Sources

- Test Cases databases, `QA/Test Plan and Test Cases/Aurie/Test Cases` and `QA/Test Plan and Test Cases/Aura/Aura Mobile Test Cases`.
- `AuraConsumerAppSimTests/Scripts/sim_experiments.py` and
  `AuraConsumerApp/Services/Experiments/Models/Experiments.swift` in the app repo; for Aura,
  `AuraSimTests/Scripts/sim_experiments.py` in the Aura repo.
