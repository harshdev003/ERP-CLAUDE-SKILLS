---
name: run-test-cases
description: >
  Run the standing Aurie or Aura regression suite and report what actually happened. Scopes the run
  (all / feature / priority / runner), drives the automatable cases on a simulator through
  the sim-test harness, writes each verdict back to its row, files a QA sub-card under an
  existing Linear card with one checkbox per case, files a bug card per failure, and
  publishes a run report that stands on its own. Trigger on "run the test cases", "run the
  regression suite", "run the P1 cases", "run the QA suite for {feature}", "weekly QA run",
  "execute the Aurie test cases", or "continue the QA run". Do NOT use to QA a single pull
  request (`qa:qa-linear-task`) or to author cases (`qa:write-test-cases`).
metadata:
  version: "1.3.0"
knowledge:
  reads:
    - Guide/Index
    - QA/Conventions
    - QA/Test Plan and Test Cases/Aurie/Test Cases
    - QA/Test Plan and Test Cases/Aura/Aura Mobile Test Cases
  writes:
    - QA/Test Plan and Test Cases/Aurie/Test Cases
    - QA/Test Execution & Reports/Aurie/Test Runs
    - QA/Test Plan and Test Cases/Aura/Aura Mobile Test Cases
    - QA/Test Execution & Reports/Aura/Test Runs
---

# Run the Aurie regression suite

Your job is to find out what is true, and to say so. A run that reports green because it
skipped the hard cases is worse than no run: it buys confidence nobody has earned.

**Three rules that decide whether this run is worth reading.**

1. **Blocked is not Fail.** A case that could not reach its own first step has told you
   nothing about the thing it tests. Calling it a failure hides the real one upstream.
2. **A gate that is not live means Not run.** A case for a screen behind a control-rolled
   experiment is not failing — it is not applicable to this build. Check the arm before
   judging.
3. **Not run is a result.** Every `device` and `human` case is a coverage gap this pass did
   not close. The report names them. Quietly omitting them is how a suite starts lying.

```bash
S=${CLAUDE_PLUGIN_ROOT}/skills/run-test-cases/scripts/qa_notion.py
python3 $S list --automatable-only --priority P1     # scope the run
python3 $S get --id VOI-003                          # the case, with its body
python3 $S result --id VOI-003 --verdict Pass --date 2026-09-27
python3 $S --app aura list --automatable-only --priority P1   # the Aura suite
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

On an Aura pass, also:
- Follow the Aura repo's own `sim-test` skill for the simulator.
- A case tagged `English only` is **Not run** in a non-English pass, never Fail. Its
  surface is hidden there by design.
- A case whose Experiment gate names an arm that is not live needs `--experiment
  <key>=<arm>` to reach its surface. Without it the case is **Not run**.
- Run `sync-test-cases --app aura` first. It reads live arms from the Aura repo's
  `sim_experiments.py state`.

## Step 1: Scope and confirm

Ask what to run if the prompt does not say. Then resolve the case list and show it before
touching a simulator:

| Scope | Command |
|---|---|
| Everything automatable | `list --automatable-only` |
| One feature | `list --feature VOI` |
| Critical only | `list --automatable-only --priority P1` |
| A weekly pass | `list --automatable-only` plus the `device` / `human` cases listed as Not run |

Report the split up front: how many will run, how many are `device` or `human`, how many
are gated on an arm you have not yet read. The user should know the shape of the answer
before the run costs an hour.

**Then list what this run will write, and wait for a yes.** A run is not read-only — it
files a Linear sub-card, writes a verdict onto every case row it touches, files a bug card
per failure, and publishes a run report. Name all four, plus the safety permissions you are
asking for (purchase, deletion, logout, premium redemption, experiment force-set — default
none), and do not touch a simulator until the user has agreed. Re-ask if the scope changes
mid-run.

## Step 2: Resolve the Linear card

The run needs a card to hang its QA sub-card under. In order, stop at the first hit:

1. A card id or Linear URL in the prompt.
2. The card the user names when you ask.

**Never invent one, and never guess from recency.** If there is no card, ask — a run with
nowhere to report is a run nobody will act on.

File the QA sub-card with the bundled Linear scripts from `qa:qa-linear-task`, which
already own this contract:

```bash
Q=${CLAUDE_PLUGIN_ROOT}/skills/qa-linear-task/scripts
python3 $Q/linear_api.py get-issue --id ENG-1430
python3 $Q/qa_card.py label --name QA --team ENG        # label id, never the QA *team* id
python3 $Q/linear_api.py create-issue --input-file card.json
python3 $Q/qa_card.py tick --issue <QA-CARD> --case 2.3
```

The sub-card carries one checkbox per case that this run will attempt, numbered by feature
section, so a half-finished pass can be picked up from the card alone. Title it
`QA run: Aurie <scope> — <date>`, label `QA`, parent the resolved card, same team, project
and cycle.

## Step 3: Set up the simulator

Follow `sim-test` in the iOS repo — it owns this, and its rules are not optional here:

- **One simulator per session**, named for the worktree. Never `shutdown`, `erase` or
  `boot` a simulator by model name; another session is using it.
- Build and install the build under test, and record the version and build number — the
  report is worthless without them.
- **Reset only when the case asks for it.** An erase clears the keychain session and
  permissions but not account-scoped server flags. A first-run screen needs a *new account*,
  not an erase.

Provision accounts to match the Preconditions of the cases in scope, and record which
account each case used. Never hard-code an address into a case.

## Step 3b: Size the time budget, and ask before launching

A pass is sized per run, not left to the default. Estimate it, put the number in front of the
user with the rest of Step 1, and pass it explicitly.

### The two guards are different

| Guard | Catches | Default |
|---|---|---|
| Idle guard — `maxConsecutiveTimeouts` x `timeoutPerStep` | a hung run, or a dead orchestrating agent | 15 x 120s = 30 minutes of silence |
| Total budget — `--test-timeout` | runaway wall clock | 7200s, graceful exit at 90% |

A hang is caught by the idle guard whatever the total is. Raising the total therefore does not
weaken hang protection; it only stops a slow but healthy run being killed mid-slice. Never
raise the idle guard to buy time — that is the guard that makes a long total safe.

### Estimating

Budget six to eight minutes per case, then add the fixed setup the slice pays once: sign-in,
any provisioning, and onboarding from scratch where the slice needs it. Onboarding alone can
exceed twenty minutes on a loaded machine.

| Slice | `--test-timeout` |
|---|---|
| Up to 8 cases on an existing account | 7200 |
| 12-18 cases on one simulator | 14400 |
| A slice that onboards from scratch | 18000 |
| One agent running the whole automatable suite | split it instead |

Size from the step cost observed under the parallelism actually in use, not a solo-run figure:
concurrent simulators slow each other, so the same slice costs more in a wide run than a narrow
one.

### Ask

State the estimate and the number, and wait:

> 15 cases on one simulator from an existing premium account. Estimated 90-120 minutes; passing
> `--test-timeout 14400`. A hang is still caught at 30 minutes by the idle guard. Proceed?

Pass it on `sim_test.sh --test-timeout <seconds>`. Do not edit the script default: two hours is
deliberate, so an unattended ad-hoc run cannot burn a machine, and other skills rely on it.

Cases a slice never reaches are **not** failures. Leave their rows untouched and name them in
the report as never reached.

## Step 3c: Maximise how many cases run

The suite grows every cycle, so a pass is a question of throughput. Six things move it.

### 1. Order by priority

Run P1 first, then P2, then P3, within every slice. A slice that is cut off loses its tail, so
the tail must be the cheapest thing to lose.

### 2. Slice by precondition

Re-provisioning is the largest fixed cost in a pass. Group cases that share a precondition into
one slice and pay that setup once — all `Existing premium` together, all `Existing free`
together, all `New account` in a slice that onboards once and then runs them all. A slice that
alternates between account states pays setup repeatedly and finishes far fewer cases.

A slice is often a whole feature, because features tend to share a precondition. Where they do
not, the precondition wins: it is the thing that costs time to change.

### 3. Right-size parallelism

Every simulator on one machine slows the others. Past saturation, another simulator lowers total
throughput rather than raising it. Five runners on one Mac is a known-workable configuration and
is the default starting point; step cost rises noticeably at that width, so read it from
`app.log` timestamps and stop widening once it climbs.

### 4. Defer the known-slow cases

Some cases are slow by construction and will consume a slice from the middle of it. A practice
with a finite timer cannot be hurried — there is no debug speed-up. Put cases like it at the end
of their slice, or give it a slice of its own.

### 5. Grow what is runnable at all

The ceiling on a pass is how many cases can be attempted, not the clock. A case whose
precondition cannot be produced — cancellation permission, a spent allowance, a non-US
storefront, a forced catalogue failure — is unavailable in every pass until someone makes that
state reachable. Unblocking one is worth more than any scheduling change.

Each pass records every case it could not attempt and the state it needed, so the list of
blockers shrinks instead of being rediscovered.

### 6. Tokens are a ceiling too

Wall clock and machine load are visible; token spend is not, and it binds sooner. Each runner
carries its own context, and a slice that exhausts it dies mid-run.

Match the model to the job:

| Work | Model |
|---|---|
| Driving a slice | `sonnet` |
| Orchestrating — scope, aggregate, report, file bugs | the session's own model |
| Mechanical passes with no judgement | `haiku` |

Driving the bridge is a long task rather than a reasoning-hard one, so it does not need the
strongest model — and length is what costs. It does need judgement, so it does not belong on
`haiku` either: deciding whether an observed screen satisfies an expected outcome is exactly
what a weak model gets wrong, and a wrong Pass is worse than no run.

Keep each runner's context small per the `sim-test` rules: compact bridge output, screenshots
only when stuck, and never read large knowledge files into a runner.

**Record verdicts as they are reached, never batched at the end.** This is what makes every
ceiling survivable: an agent that runs out of context or clock has still banked what it proved.

### Record the numbers

Put these in the run report so the fan-out question can be settled from evidence:

| Number | Source |
|---|---|
| Runners started, and how many finished vs died at a ceiling | the orchestrator |
| Cases attempted and completed, per runner | each runner |
| Median seconds per step, per simulator | `app.log` timestamps |
| Wall clock per slice, and what ended it | each runner |
| Fixed setup cost paid per slice | each runner |
| Model per runner, and tokens per model for the run | the orchestrator |

Compare passes on **completed cases per runner-hour**, alongside whether any runner died before
recording. Two questions these answer: whether another simulator still raises completed cases,
and whether a slice dies from the clock or from context — those have opposite fixes.

#### Reading the token spend

The session transcript carries `model` and a `usage` block on every assistant message:

```bash
python3 - <<'EOF'
import json, collections, glob, os
f = sorted(glob.glob(os.path.expanduser('~/.claude/projects/*/*.jsonl')), key=os.path.getmtime)[-1]
agg = collections.defaultdict(collections.Counter)
for line in open(f, errors='replace'):
    try: d = json.loads(line)
    except Exception: continue
    m = d.get('message') or {}
    u, mdl = m.get('usage'), m.get('model')
    if not u or not mdl: continue
    c = agg[mdl]
    c['in'] += u.get('input_tokens', 0)
    c['out'] += u.get('output_tokens', 0)
    c['cache_w'] += u.get('cache_creation_input_tokens', 0)
    c['cache_r'] += u.get('cache_read_input_tokens', 0)
for mdl, c in agg.items():
    print(mdl, dict(c))
EOF
```

Cache reads dominate and are not the cost driver — compare on `input + output + cache_creation`
and keep cache reads in their own column. Per-runner attribution is not available: a runner
cannot read its own usage and its transcript is not reachable from the parent session. Record
the model per runner and the run-level totals, and do not invent a split by dividing the total,
since the orchestrator's own context is usually the largest single consumer.

### An idle runner kills its own session in thirty minutes

The XCTest runner exits when no action arrives for 15 consecutive step timeouts — 15 x 120s, so
**thirty minutes of silence ends the slice**, whatever `--test-timeout` was set to. It writes
`Test ended on graceful timeout at step N: No action received after 15 consecutive timeouts` and
tears the app down. Everything not already recorded is lost.

The harness counts actions, not intent. It cannot distinguish a thinking agent from a dead one,
so waiting and dying look identical to it.

Three rules follow, and they cover every way a slice goes quiet:

- **A bridge timeout is a signal to act, not to wait longer.** Screenshot, re-orient, send the
  next action. This is what screenshot-on-confusion exists for.
- **A returned bridge call is not still working because its shell wrapper is open.** Read the
  output and act on it.
- **Poll through any long wait** — a track playing out, a practice timer, a narration deck, an
  out-of-band backend step. A `read` every thirty to sixty seconds resets the timeout and tells
  you whether the thing you are waiting on is still progressing.
- **A timer is not a bridge action.** Delegating a wait to a sleep, a background job or a monitor
  resets nothing: the runner counts actions arriving, so a wait implemented that way is
  indistinguishable from a dead agent and is killed as one. Implement every wait **as polling**.
  When a case says to wait for an asynchronous step, it is telling you to retry across a window —
  not to stop driving for the length of it.

If you genuinely cannot proceed, that is a verdict — `Blocked`, with the reason — not a reason to
wait.

## Watch for stalled runners

A runner that has stopped acting looks exactly like one that is working. Check every few minutes:

| Signal | Meaning |
|---|---|
| `Sessions/<udid>/state.json` mtime older than ~5 minutes | the agent has stopped driving |
| No verdicts recorded after roughly the first ten minutes | nothing is banked, so a death costs the whole slice |
| An idle notice that does **not** report the slice complete | stalled, not finished |

The third signal is the one that misleads. An agent reporting that it is waiting on something
reads as progress and means the opposite. What distinguishes a finished slice from a stalled one
is whether the message carries a completed slice report, not whether a notification arrived.

Nudge on the first signal rather than the third. An agent that has only gone quiet can be
restarted into the same session and carry on; one whose runner has already exited has lost the
slice. The window is thirty minutes measured from its last action.

When a runner does die, diagnose before redistributing the work. `app.log` says whether the test
ended on a graceful timeout, and the last screenshot says whether the app was healthy. A dead
runner and a wedged app need opposite fixes, and only one of them is a bug.

## Step 4: Run

Per case, in this order:

1. `get --id <ID>` — read the whole case, including Notes. The Notes exist because someone
   already lost a run to that trap.
2. Read the screens it names in the repo's `knowledge/screens/`, for the identifiers and
   the **Advance** section.
3. If it has an experiment gate, read the arm **before** driving anything, and read it
   without writing anything. Gate not live → `Not run`, with the arm you actually read
   written down.

   **How to read an arm, in order of preference:**

   | Route | What it does |
   |---|---|
   | **Preferred:** the app's own log — `Resolved experiment assignments [authenticated]: <key>=<arm>` in `app.log` | Reads a line the app already produced. No request, so nothing can change. |
   | Fallback: `POST /experiments/assign?includeFlags=true` with **that user's own** Firebase token | Resolves the caller's own assignments. It is the client bucketing path, so for a key the user has **never** been bucketed on it creates and persists that bucket. On a QA account that has already run the app the bucket exists and this returns it unchanged. |

   So prefer the log, and reach for `/assign` only when the log does not carry the key —
   on a fresh account it is not a passive read. There is no read-only assignments
   endpoint; `/assign` is the only one the app has, and it only ever answers for the
   token's own user.

   **Never `POST /experiments/{key}/set/{uid}/{variant}` to find out what arm a user is
   on.** That writes a bucket server-side, persists, and contaminates product metrics —
   it is a force-set wearing a read's clothing, and this skill forbids force-setting
   without explicit permission. Reading is always allowed; writing never is by default.
4. Drive the steps. Compare `state.screen` before and after — `lastAction.success` is not
   evidence that anything moved.
5. Record the verdict immediately: `result --id <ID> --verdict <V> --date <today>`, and
   tick the checkbox on the QA card.

**Safety constraints are absolute, and they come from the repo's `knowledge/rules.md`.**
Without explicit permission in this session: no purchases, no account deletion, no
conversation or journal deletion, no logout, no premium redemption, no experiment force-set.
Never tap a crisis call control. Never tap an external link. A case needing any of these is
`Not run` with the constraint as the reason — never a guess at what would have happened.

**When something is confusing, take a screenshot before retrying.** State not advancing,
a failed action, a bridge timeout, a hint in the state: screenshot first, re-orient, then
act. Blind retries destroy runs.

## Step 5: Record failures properly

For each failure, capture, at the moment it happens: the case id, the step number, what you
expected, what you saw, a screenshot, and the relevant log lines. Then file a bug card in
Linear with those details and attach the screenshot.

**The Linear card links to the run report; the report does not link to the card.** AI
Knowledge pages carry no Linear links — cards close, move and get archived, and a report
whose evidence lives behind a dead link is not evidence. Write the failure in full in the
report so it stands alone, then let the card point back.

## Step 6: Publish the run

Write the Test Runs row and its report with
[`references/run-report.md`](references/run-report.md) as the shape. `Result` is:

| | |
|---|---|
| Green | no failures, and no P1 left unrun |
| Amber | failures, none P1 — or a P1 could not be run |
| Red | any P1 failure |

Then say, in chat: the counts, every failure in one line each, and every case that did not
run with its reason. Lead with what broke, not with how many passed.

## Running in parallel

A full pass is long. To split it, give each **slice** its own simulator and spawn one
`qa:test-runner` agent per simulator, each owning its slice end to end. A slice is the set of
cases sharing one precondition, ordered P1 first (Step 3c); often that is a whole feature,
because features tend to share a precondition.

Each agent is given its explicit case list, writes its own verdicts back, and returns a compact
summary; you merge them into one run report. **Do not split a slice across agents** — the setup
it pays for is shared, and splitting makes two agents pay it twice.

## Related

- `qa:write-test-cases` — authors the suite this skill runs. A case that proved
  unrunnable, ambiguous or wrong belongs back there, not patched in place during a run.
- `qa:qa-linear-task` — change-scoped QA for one PR. If that pass finds something worth
  checking every release, it becomes a case here.
