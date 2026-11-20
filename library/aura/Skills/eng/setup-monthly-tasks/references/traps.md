# Six silent-failure traps

All six were found during the 2026-09 pass (closing August 2026). Each one fails by
returning a plausible wrong answer rather than by erroring, which is why they are worth
writing down.

## 1. Analytics months are 1-12, not 0-11

`src/jobs/analytics.js` writes the month as `lastMonth.month() + 1`, with an explicit
`// jan=1, dec=12` comment. The live table stores August 2026 as `month=8`. So July is
`7` and August is `8`.

Three places claimed otherwise: the Notion runbook's NOTE block (corrected 2026-09-01),
and the `Monthly Coach Analytics for %month%` and `Monthly community payout for %month%`
ENGBACK templates, which at time of writing still say "0..11 … so July is month 6".

The `gather-previous-month-analytics` skill's own query is right (`month=7` for July) but
its prose label repeats the wrong claim. Its `--month` flag has always been 1-based.

**Why it matters:** a wrong month returns the neighbouring month's numbers. Nothing fails.

## 2. Monthly rows are only ever written on the 1st, and cannot be backfilled

Two independent mechanisms combine here:

- `analytics.js` gates the monthly report types on `isNewMonth` — true only when
  yesterday's month differs from today's, i.e. **only on the 1st**.
- Each monthly SQL file hardcodes its own window to
  `date_trunc('month', current_date - interval '1' month)`. There is no month parameter
  anywhere in the job.

So if the job does not run on the 1st, the month's rows are never written, and
`gather-previous-month-analytics` finds nothing to pull. Re-running the job later does not
backfill it — run on October 3rd it produces September's numbers, not August's. Recovery
means overriding the date, not re-running the job.

This is why the pre-flight in Step 2 exists.

## 3. A payout-rate change is applied retroactively to the whole month

`src/jobs/payout.js` recomputes each component from the **monthly aggregate** — e.g.
`donationRevenue * donationPercentage` — so it uses whatever rate is deployed *when the
job runs*. `src/models/ledger.js calculatePayout()` stores the payout **per transaction,
at transaction time**. The two disagree whenever a rate changes mid-month.

Worked example. PROD-5281 changed `payout.donationPercentage` 50 → 70 and shipped in
Release 2.199.0 on 2026-08-31 at 22:23 UTC — the last day of August. The payout job ran
the next morning and paid all of August's donations at 70%, while all 102 August ledger
rows still recorded 50%: $455.98 gross, $227.99 recorded, $319.186 paid, a $91.20 gap.

Resolved by deciding the payout stood and correcting the ledger to match. The exports were
left alone — they already carried the new rate and were correct as generated.

**If a payout rate changed during the month being closed, surface it in the report.** It
is a decision the user has to make, and it should not be settled by deploy timing.

## 4. Templated creates drop labels and estimates

Passing `labels` or `estimate` to `save_issue` **alongside `template`** returns them
empty. No error. The Linear MCP's own documentation claims labels "merge with the
template's own" — that is wrong whenever a template is set.

The loss is template-specific: the same fields applied on a create with **no** template
work fine. So card creation must be two passes — create from template, then update with
labels and estimate. `project` passed on a templated create is kept (verified 2026-10-01 on
all nine cards); the second pass reapplies it anyway because it is free.

It lives in Linear's `issueCreate` + `templateId` path itself, not in the MCP wrapper — a raw
GraphQL create with a `templateId` drops labels exactly the same way. No call shape avoids
it, so a template's declared labels serve only as the source of truth the second pass reads.

The estimate loss is the expensive one and was undocumented before 2026-09: all nine cards
came through with no points, which silently removes 42 points from the cycle.

## 5. Moving cards between teams costs labels, project and status

Dropped by a team move: **labels**, **project**, and **status** (Todo was remapped to
"Eng In Progress" with `startedAt` stamped). Survived: estimates, due dates, assignee,
parent links — and the original identifier is reclaimed if you move back.

The labels cannot simply be re-applied on the other team. ENGBACK's operational labels —
Payout, Database, Iterable, Budget, Accounting, Quality, Coaching — **do not exist on
PROD at all**. PROD's taxonomy is outcome-shaped (Growth & Monetization, Core UX &
Activation, Engagement & Retention), and its nearest name, "Coaching & Processing
Efficacy", means something else entirely.

Also: **moving a parent drags every child with it.** That is how a whole pass loses its
labels in one action.

This is the reason for the epic-on-PROD / cards-on-ENGBACK split in Step 4. It is not an
accident of history — it is what lets the epic show in the PROD cycle while the cards keep
a label vocabulary that only Backend has.

## 6. A multi-concern `save_issue` can silently no-op

Passing `labels` in the same `save_issue` call as a `parentId` change aborts the whole
mutation without erroring. It returns the issue **unchanged**, carrying its previous
`updatedAt` — so a caller that trusts the response believes the parent moved, the cycle moved
and the labels landed, when none of them did.

Split it: one call for the parent/cycle move, another for labels. Then read `updatedAt` back
on each to confirm it advanced. This is the same shape as trap 4 — a write that reports
success and does nothing — and the only defence is verifying rather than trusting.

The response can also mislead in the other direction: it can show the issue as it was before a
write that did land. Retrying a description `append` on that evidence writes the text twice.
Before retrying any write, re-read the issue with `get_issue`.

## Bonus: two editing traps

Not failure modes of the pass, but of documenting it.

- **Linear rewrites issue mentions into `<issue>` tags on save**, so a later
  search-and-replace anchored on an issue identifier silently misses. Anchor patches on
  plain prose.
- **Linear templates take ProseMirror, and an unsupported mark voids the whole body
  silently.** A `bold` mark stores the document as a single `unsupported_block_node`; the
  write returns success and every card created from it comes out empty. Edit templates in
  the Linear UI, which generates valid ProseMirror by construction. The Linear MCP exposes
  no template-update mutation anyway.
