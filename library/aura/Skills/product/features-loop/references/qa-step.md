# The QA step

Read by `features-loop` at `run` step 3c. A merged feature PR is QA'd on simulators before
its experiment can go live. The loop composes `qa:qa-linear-task` for the whole step —
planning, the card, the runner, the checklist and the gate are that skill's, and this file
only says how the loop calls it and what it does with the result.

## When

An `implementing` row whose PR has merged and whose feature card has **no `QA`-labelled
sub-card yet** (`qa_card.py children --issue <card>`). The card is the dedup key: a row with a
QA card is checked, never re-planned.

**In flight, before the card exists.** Planning takes a while, and a tick in that window sees
no QA card yet. So a row is also skipped while a QA agent this session dispatched for it is
still running — the same guard `build` and `ideate` use. The agent files the card first, before
running any case, which keeps that window short. After a restart no agent is live, so the card
decides again: a card that exists is resumed (`qa-linear-task` continues an open QA card
rather than planning a second), and a row with none is dispatched afresh.

## Overrides the loop takes, and why

`qa-linear-task` asks a person twice and offers two outward steps. A tick has no person at
decision time, so the loop decides them itself. Each is the loop's own decision, not
`qa-linear-task`'s rule, and is re-checked whenever that skill changes:

| `qa-linear-task` step | The loop does | Why | Errs toward |
|---|---|---|---|
| Step 2 — wait for approval of the draft cases | files the draft as planned | the card is where the team reviews the plan; a wrong case surfaces as a failure or `cannot run` on the results comment | more cases, never fewer — a case it doubts is filed tagged `human`, not dropped |
| Step 4 — ask how to execute | subagent mode, automatable cases only | one background runner keeps the tick's context small | `device` / `human` cases go to a follow-up card assigned to the tester, so nothing is silently skipped |
| Step 7 — offer `gh pr ready`, move the task | nothing | the PR is already merged; the task card belongs to the release process | — |

It follows the skill's **Unattended caller** contract for all three.

## Planning

- Plan from the feature card and the merged PR, and read `qa-linear-task`'s
  `references/experiment-cases.md` — every loop feature ships behind an experiment. In
  particular: read from the code **when** the flag is assigned (pre-auth draws can't be put on
  an account — reinstall until the arm shows; post-auth draws happen at sign-up), take event and
  property names from the code, and plan returning-user cases on an account created before the
  experiment's `minVersion`.
- **Mixpanel project for a simulator pass:** events land in **Aura Pre Prod** (Debug build);
  the account's arm, written by the backend at sign-up, is in the production project.
- **Simulator first.** Tag every case a simulator can run `sim`, including purchases
  (StoreKit testing) and design fit (`product:designer` against the approved sheet).
- **Device matrix.** Layout and visual cases run on every simulator in the matrix — iPhone SE
  (375pt), a standard iPhone 16/17, the Pro Max, and the standard device at a large Dynamic
  Type size — one case line per device, with a screenshot per device as the evidence. Flow
  and analytics cases run on one device.
- Assignee: the operator. Follow-up card for `device` / `human` cases: the tester the
  operator names, else the operator.

## Running

**One QA pass in flight per card**, the same rule as builds. Dispatch one background
subagent with the subagent prompt from `qa-linear-task`'s `references/execution-modes.md`,
giving it the card, the open automatable cases and the setup section. It runs the device
matrix sequentially on its own simulators (one UDID per device), or the loop splits each
device's cases to its own follow-up card and gives each runner its own simulator. Never two
runners on one card.

Before any case, the runner checks the experiment record is **splitting** (`active` lists
more than one arm). A pinned record is reported `ended: true` and hides super-property
defects, so the analytics cases cannot run: the runner marks them `cannot run`, and the loop
says so in the idea's thread. Analytics cases check **every event** per test user
(`$all_events` by `Experiment: <key>`), not only the exposure event.

## Reading the result on the next tick

Re-read `qa_card.py checklist` and the QA card's children, never the agent's summary.

| The card says | The loop does |
|---|---|
| `open: 0`, no open `QA Feedback` sub-card | moves the QA card to Done and replies **QA passed** in the idea's thread, with the card link |
| any open `QA Feedback` sub-card | replies in the idea's thread with each bug's link and leaves the blocker call to a person. A person-confirmed blocker → `ship-idea qa-bug <IDEA-N> --reason … --post`, which reopens Gate 2. A bug a person calls not a blocker is listed with its tracking card on the QA card's closing comment, the gate closes, and the *QA passed* reply lists those non-blockers too |
| cases still open, no live runner | re-dispatches the pass (a restart or an early stop) |

Each reply is made once per card state; the thread already holding it is the dedup. Post it by
invoking **`shared:post-to-slack`** as a threaded reply (thread `ts` from the row's `Slack
thread`), `footer=product:features-loop · qa`, non-interactively — the same composition the
digest uses, so the reply carries the loop's attribution.

## After it ships

When the experiment goes live, a case worth checking on every release — "the paywall
headline matches the archetype", "slide 5 shows the CTA" — belongs in the standing suite.
Hand it to `qa:write-test-cases` rather than letting it close with the card. For every
onboarding experiment that includes "the arm appears only from exposure onwards and matches
the account's arm after login".
