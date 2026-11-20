---
name: setup-monthly-tasks
description: >
  Scaffold the monthly engineering pass in Linear in one invocation: two PROD epics (month-end
  and mid-month, naming different calendar months) plus twelve ENGBACK child cards, including
  the monthly Node.js, node-packages and Kubernetes updates, labelled, estimated, dated and
  ordered. Pre-flight verifies the analytics table holds the closed month's rows, then applies
  labels and estimates in a REQUIRED second pass, since neither carries through a templated
  create. Trigger on "setup the monthly tasks", "set up the monthly pass", "create the monthly
  cards", "create the monthly epic", "create the mid month epic", "scaffold the mid month
  tasks", "it is the beginning of the month, create the cards", "start the monthly pass",
  "instantiate the monthly templates", "make this month's monthly task cards", or "add the
  monthly node and kubernetes cards". Do NOT use to WORK the pass, or for the weekly cycle
  rollover (`product:setup-weekly-cycle`).
metadata:
  version: "1.3.0"
knowledge:
  reads:
    - Guide/Index      # baseline routing aid (universal)
    - Tools/Linear     # team / cycle / template / label ids and conventions
    - Tools/Notion     # the Monthly Tasks runbook this skill implements
    - Tools/Postgres   # PaymentService connection for the analytics pre-flight
---

# Setup Monthly Tasks

Step 1 of the **Monthly Tasks** pass, as one invocation. The runbook it implements is
[Procedures / Engineering / Monthly Tasks](https://app.notion.com/p/3b175e23af2081a8acfdcaa5bb27e4d4),
which is the authority on everything downstream; this skill owns only the scaffolding.

This skill creates Linear structure and reads Postgres. It does **not** touch the
compensation spreadsheets, the payout jobs, Slack, or the ledger. Everything after the
cards exist is human work, driven from the card checklists.

## Before any writes: show the plan

Creating two epics plus twelve cards is fourteen writes across two teams. Resolve every input
first, print the plan as a table (both months, both epic titles, the twelve card titles with
the epic each sits under (the Node.js title carries a resolved version — Step 3b), labels, estimates, due dates, and **two** target cycles per team —
one for the month-end epic, one for the mid-month pair), and **wait for the user to approve
it**. A misread month silently produces a whole pass against the wrong period, and a plan
approved with one cycle per team files the dated pair in a cycle that closes before the
10th — weekly cycles put the 10th outside the cycle the pass runs in almost every month.

## Step 0 — Prerequisites

- Linear MCP available (`list_templates`, `list_teams`, `list_cycles`, `list_projects`,
  `list_issue_labels`, `save_issue`, `list_issues`).
- For the pre-flight only: `node`, plus a PaymentService checkout for its `.env` and
  `node_modules` (`pg`, `dotenv`). The script resolves it from
  `--payment-service-path`, then `$PAYMENT_SERVICE_PATH`, then a short list of
  conventional locations under `$HOME`; a candidate only counts if it has both `.env`
  and `node_modules/pg`. If none match it lists what it tried and exits — pass the flag
  or set the env var.
- The pre-flight is read-only. Never echo `POSTGRES_DATABASE_URI`.

## Step 1 — Resolve both months

Two months are in play, and they are different. Resolve both before anything else:

| Token | Value | Used for |
|---|---|---|
| `<ClosedMonth>` | the **previous** calendar month | the month-end epic title, and every month-carrying card title |
| `<RunMonth>` | the calendar month the pass **runs in** (today's) | the mid-month epic title only |

In the 2026-09 pass these were August and September: `Monthly Tasks for August` and
`Mid Month Tasks for September`. Note the asymmetry — the mid-month epic is the only
thing named for the run month. Its two children still carry the closed month, so
`Monthly update to payout for August` sits under `Mid Month Tasks for September`.

`<ClosedMonth>` is the **previous calendar month**, named for the month being closed, not the
current one. On 2026-09-01 the pass closes **August 2026** → `year=2026`, `month=8`.

Postgres `analytics` stores months **1-12** (`jan=1`, `dec=12`). Older copies of the
runbook and two ENGBACK templates claim `0-11` / "July is 6" — that is wrong, and the
error is silent: it returns the neighbouring month's numbers rather than failing. See
[`references/traps.md`](references/traps.md).

Confirm the resolved month with the user in one line before connecting to anything.

## Step 2 — Pre-flight: do the month's analytics rows exist?

Run this before creating cards. If the rows are missing there is no backfill path, and
that changes what the pass can do at all.

```bash
node scripts/check_analytics_rows.js --year=2026 --month=8
```

It prints which of the expected monthly types are present, which are missing, and whether
`payout` was written. A clean month is **18 monthly types + `payout`**, with
`monthlyVideoSubscription` and `monthlyVideoSubscriptionDetails` absent and expected —
they are retired and have never been emitted.

If rows are missing, **stop and tell the user** rather than creating cards. The job only
queues monthly types on the 1st and each SQL file hardcodes its own window, so re-running
later produces a different month. Details in [`references/traps.md`](references/traps.md).

## Step 3 — Resolve teams, cycles, project, templates, labels

| Thing | How |
|---|---|
| Epic team | Product/Design (`PROD`) — `list_teams` |
| Card team | Backend (`ENGBACK`) — `list_teams` |
| Cycle | `list_cycles(teamId, type: "current")` and `type: "next"`, **per team separately** — the mid-month pair sits in whichever cycle contains the 10th |
| Project | `Aura - Core App` — `list_projects` |
| Templates | `list_templates(team: "ENGBACK", type: "issue")` — resolve by name |
| Labels | `list_issue_labels(team: <Backend id>)` — resolve by name |

**Match cycles by date range, never by number.** The two teams number their cycles
independently: in the 2026-09 pass PROD's current cycle was number 42 and Backend's was
number 44, both titled "Cycle 42", both covering Aug 31 to Sep 7.

The Backend operational labels (Accounting, Coaching, Payout, Iterable, Quality, Database,
Budget, Infra) carry a `retiredAt` date in `list_issue_labels`. They still apply on update and are
the labels every monthly card uses — apply them as listed; do not stop or substitute.

Resolve templates and labels **by name**, not by hardcoded id. The id table in
[`references/card-map.md`](references/card-map.md) is a cross-check, not the source.

## Step 3b — Resolve the Node.js target (card 10)

Card 10's title names a version: `Update nodejs to version <target>`. Resolve it the way
`eng:update-nodejs` does, without scanning the repos:

1. **Fleet version** — the version in the title of the most recent **Done**
   `Update nodejs to version …` card on ENGBACK (`list_issues(team: ENGBACK, query:
   "Update nodejs to version", state: completed)`).
2. **Target** — the latest Node **LTS**, from `https://nodejs.org/dist/index.json`: services
   follow LTS, new major included. When that is a new major, the plan notes that Functions
   join only once Cloud Functions lists it as GA (`eng:update-nodejs` resolves that).
3. **Target equals the fleet version** → there is no new Node release. Show card 10 as
   `skipped — fleet already on <version>` in the plan rather than leaving it out silently;
   cards 11 and 12 are still created. The user can override either way.
   **Except when Functions were held:** if that Done card has a comment starting
   `Functions held on` (written by `eng:update-nodejs`), the Functions join is still owed, so
   card 10 is created with the same title and the plan says it carries the Functions join
   once Cloud Functions lists the major as GA.

```bash
curl -s https://nodejs.org/dist/index.json | python3 -c \
  "import json,sys; print([v for v in json.load(sys.stdin) if v['lts']][0]['version'].lstrip('v'))"
```

## Step 4 — Create the two epics (PROD team)

The pass has two cadences, so it has two epics. Both on **PROD**, both in project
`Aura - Core App`, assignee the user, state Todo, priority Medium, and no estimate — the
children carry the points, and an estimate on a parent double-counts them.

| Epic | Title | Cycle | Due |
|---|---|---|---|
| Month-end | `Monthly Tasks for <ClosedMonth>` — no year | PROD's current cycle | none |
| Mid-month | `Mid Month Tasks for <RunMonth>` — no year | PROD's cycle containing the 10th | the **10th** |

The naming asymmetry is deliberate: the month-end epic is identified by what it closes, the
mid-month one by when it runs, because that is the only thing distinguishing it. Children keep
their own attribution either way — `Monthly update to payout for August` stays August-titled
inside a September epic, so nothing is ambiguous at card level.

Two epics rather than one because the month-end set finishes in the first days of the month
while two cards are dated the 10th. A single epic sits open for ten days after its other
cards are done.

Put both epics on **PROD** and every child on **ENGBACK**. That split is deliberate: an epic
then appears in the PROD active cycle while the cards keep the ENGBACK label taxonomy, which
does not exist on PROD at all. Cross-team parenting works fine.

## Step 5 — Create the twelve cards (ENGBACK team), in run order

Create them **sequentially, in run order** — `subIssueSortOrder` is not exposed by the
MCP, so creation order is what sets sub-issue order, and the runbook wants that order to
match the run order so each epic reads as its own checklist.

| Under | Cards, in order |
|---|---|
| Month-end epic | gift redemption · Coach Analytics · coach payout · community payout · Iterable Sync · data tasks · Engineering Budget · Node.js · node packages · Kubernetes |
| Mid-month epic | Refund for Coaches, then Monthly update to payout |

For each: pass `template`, `title`, `parentId` (its own epic), `project`, `cycle`,
`assignee`, `state: Todo`, `priority: 3`. Do **not** pass `description` — that would
replace the template body with your text.

The mid-month pair takes Backend's cycle containing the 10th, not Backend's current one.

Titles, labels, estimates, which epic each card belongs under, and the two naming quirks (the
gift card drops the `Monthly` prefix; `Refund for Coaches` never carries a month) are in
[`references/card-map.md`](references/card-map.md).

## Step 6 — Second pass: labels, estimates, dates, relation

**This pass is required, not optional.** A templated create silently drops `labels` and
`estimate` — passing them alongside `template` returns them empty. Nothing errors; the cards
simply come through bare, and the missing estimates quietly remove 57 points from the cycle.
`project` passed on the create is kept.

The loss lives in Linear's `issueCreate` + `templateId` path itself rather than in the MCP
wrapper, so no call shape avoids it. A template's declared labels serve only as the source of
truth this pass reads from.

For each card, `save_issue` with its id, the label ids and estimate from the card map,
**and `project`**. Reapplying `project` costs nothing and closes the one way a card can end
up off `Aura - Core App` without anything signalling it.

**One concern per call.** Passing `labels` in the same `save_issue` call as a `parentId`
change silently no-ops the entire mutation — it returns the unchanged issue with its previous
`updatedAt`, so nothing errors and nothing applies. Issue a parent or cycle move and a label
write as separate calls, and read `updatedAt` back to confirm each landed. A response can also
lag the write it reports — confirm with `get_issue` before retrying, since a retried
description `append` that had in fact landed duplicates its text.

Then:

- `Refund for Coaches` and `Monthly update to payout for <ClosedMonth>` → `dueDate` the **10th**.
- `Refund for Coaches` → `blocks: [<update-to-payout id>]`. It writes the Adjustment
  column that update-to-payout propagates, so an adjustment entered later misses the
  payout entirely. Since both share one due date, the relation is what carries the ordering.
- Node.js → `blocks: [<node packages id>]`, and node packages → `blocks: [<Kubernetes id>]`.
  Packages are bumped on the new runtime, and the cluster is upgraded once the services are
  redeployed on both. When card 10 was skipped, only the packages → Kubernetes relation applies.

## Step 7 — Verify, then report

Re-read **both** trees with `list_issues(parentId: <epic>, fields: [...])` and confirm, per
card: correct team, correct cycle id, assignee set, labels present, estimate present,
project set. Do not report success from the create responses — verify.

Then report: both epic URLs, a table of the cards with points and which epic each sits
under, the points total (**57** if the card map is unchanged, **52** when the Node.js card was skipped), the run order, and what is
human-only next. Name the two time-sensitive items: refunds then update-to-payout on the
10th, in that order. Cards 10-11 are worked with `eng:update-nodejs` and
`eng:update-node-packages`; card 12 runs after both.

## Notes and edge cases

- **Items 2-5 are a strict chain**; 6-8 are independent hygiene; 10 → 11 → 12 are their own
  chain (Node runtime, then packages, then the Kubernetes cluster). The three export cards
  pull Postgres data into the sheets, and refunds are applied *on top* of what was
  exported — running refunds first is overwritten by the next export.
- **A payout-rate change is applied retroactively** to the whole in-flight month, because
  the payout job recomputes from monthly aggregates rather than summing stored ledger
  payouts. If a rate changed during the month being closed, surface it in the report as a
  decision the user has to make. See [`references/traps.md`](references/traps.md).
- **The analytics export needs a PR.** `AnalyticsReports` master is branch-protected, so
  the export commit cannot be pushed directly. Two ENGBACK templates still say
  "commit + push to `master`".
- **Idempotency, per epic independently.** Look up both `Monthly Tasks for <ClosedMonth>`
  and `Mid Month Tasks for <RunMonth>`, and decide about each on its own: skip the one that
  exists, **create the one that does not**. Finding the month-end epic is not a reason to
  stop — a re-run that halts there leaves the mid-month epic missing, which is the recovery
  path this exists to serve. For an epic that already exists, offer to fill in only its
  missing children, and check where the dated pair currently hangs: `Refund for Coaches` and
  `Monthly update to payout` belong to the mid-month epic, and a partial re-run can leave
  them parented under the month-end one.
- **Started-state names differ per team.** ENGBACK's is `In Progress`, PROD's is
  `Eng In Progress`. Pass `state: "started"` (the state type) to move either team's card,
  rather than a name that exists on only one.
- **Ad-hoc cards are fine.** The twelve are the floor, not a cap; a pass that finds
  a real problem may add a fix card under whichever epic owns the work.

## References

- [`references/card-map.md`](references/card-map.md) — the twelve cards (templates, titles, labels, estimates, run order).
- [`references/traps.md`](references/traps.md) — the six silent-failure traps in full.
- Runbook: [Procedures / Engineering / Monthly Tasks](https://app.notion.com/p/3b175e23af2081a8acfdcaa5bb27e4d4).
