# Step 1f — sweep-mode pre-classification

How a sweep (N>10 experiments, or "all Running") sorts rows into `NEVER_LAUNCHED` / `TOO_YOUNG` / `RUNNING_WITH_DATA` from cheap signals before any deep analysis runs.

The point is cost: only the third bucket earns the full Slack + Linear + Mixpanel pipeline. The first two get boilerplate close-outs, so a 27-row sweep doesn't pay 27 full analyses.

**Skip this step entirely** if the input is fewer than 10 experiments or a
single Notion URL / DB query — Steps 2–7 handle that scope fine.

When the input is a large list (N>10) or a wildcard like `all Running Aurie`
/ `all Status: Running`, **do not** loop the full Steps 0–7 pipeline on
every row (validated 2026-05-29 — a large fraction are never-launched stubs
knowable from cheap signals alone). Instead:

**1. Gather cheap signals in parallel** for every input row — four non-blocking sources: **Aura API** (`GET /experiments/<name>` → `endedAt`, `notes`, `minVersion`, `targetApp`, `active`), **Mixpanel SUL count** (per-variant), **Slack mention count** (bulk dump `#data-experiment` + `#support-bot` once, then in-memory grep), and **Linear ticket count**.

> See [`references/examples.md`](examples.md) → "Sweep-mode cheap-signal queries (Step 1f, step 1)" for the exact per-signal queries and the validation run.

**2. Classify each row** into one of three buckets:

| # | Bucket | Conditions |
|---|---|---|
| 1 | `TOO_YOUNG` | `endedAt == null` AND **age < 14 days**. Owns every young row, whatever its other signals — nothing is closed before it has had a chance to run |
| 2 | `NEVER_LAUNCHED` | `active` carries at most one distinct variant AND `endedAt == null` AND total Mixpanel SUL < 20 AND Slack non-create/end mentions ≤ 2 AND **either** `minVersion` gated to an unshipped version **or** `notes` states it is not a real experiment |
| 3 | `REGISTERED_STILL_ASSIGNING` | `active` > 1 distinct letter AND `endedAt == null` AND total Mixpanel SUL < 20. No `notes` or `minVersion` clause — those belong to `NEVER_LAUNCHED` |
| 4 | `RUNNING_WITH_DATA` | everything else — `endedAt != null`, OR SUL ≥ 100, OR meaningful Slack/Linear activity |

**Hard gate on `NEVER_LAUNCHED`: a row with `active` carrying more than one
distinct letter and no `endedAt` must not be CLOSED — but it does not thereby
earn a full analysis.** It goes to a fourth outcome:

| bucket | when | what it gets |
|---|---|---|
| `NEVER_LAUNCHED` | not splitting, and the cheap signals agree | boilerplate close-out |
| `REGISTERED_STILL_ASSIGNING` | splitting, past 14 days, SUL < 20 | left open, reported, **no write** |
| `TOO_YOUNG` | splitting, young, thin | boilerplate, left open |
| `RUNNING_WITH_DATA` | splitting with real traffic | full Steps 2-7 |

**Evaluate in numbered order, first match wins.** The order is load-bearing, not
presentational.

`TOO_YOUNG` is first because **nothing should be closed before it has had a
chance to run.** A dev fixture registered three days ago matches
`NEVER_LAUNCHED`'s signals too — thin traffic, a `notes` field saying so — and at
three days those signals cannot distinguish it from a real experiment that has
not started moving. Left open it costs nothing; closed it is wrong and looks
settled. The age floor in `REGISTERED_STILL_ASSIGNING` exists only because
`TOO_YOUNG` already owns that range.

`NEVER_LAUNCHED` then takes the non-splitting rows, and
`REGISTERED_STILL_ASSIGNING` the splitting ones with no traffic. They are
separated by `active`, not by order — a row cannot satisfy both.

**The middle row is the one this gate exists for, and getting it wrong costs in
both directions.** Closing a still-assigning experiment tells every later reader
the question is settled — the 2026-05-29 sweep did that to six rows that were
still splitting four months later. But forcing them into `RUNNING_WITH_DATA`
would run a full Slack + Linear + Mixpanel pipeline over rows with **zero users**,
producing nothing at real cost. Measured on those same six: five had 0-4 assigned
users after four to six months, and none had a single client-code reference. The
sweep's read of them as never-launched was substantively right; only the *write*
was wrong.

So for `REGISTERED_STILL_ASSIGNING`: **write nothing, report it, move on.** No
`Status`, no `End Date`, no boilerplate body. The row stays as it is and appears
in the run summary as needing a human decision — end it, or implement it.

Every other `NEVER_LAUNCHED` condition measures the **absence** of evidence — few
users, little chatter, a gated `minVersion` — and a quiet experiment is
indistinguishable from a never-launched one on all of them. `active` is the one
signal that states the fact directly, and the API call is already made
([PROD-6200](https://linear.app/aura-health/issue/PROD-6200)).

**An experiment with no `endedAt` has no `End Date` — leave it blank.** Never
write the sweep's run date.

**Read `notes` as a statement of intent, not only as a regex target.** An author
writing "for development only" is saying the row is not a real experiment; that is
the signal worth closing on, and it is a different signal from low traffic.
Matching `/dev|QA|test|internal/i` approximates it and also fires on any
experiment whose description happens to contain "test".

Also resolve any false-positive Slack-ended hits: an end-note for `X` may
mention another experiment `Y` in its free text, making `Y` *look* ended when
it isn't — only treat as ended when the experiment's own name is in the post's
`` `<name>` `` slot.

**3. Report the split** (per-bucket counts) before any deep work begins, and
ask the user to confirm before proceeding, since the boilerplate close-outs
are still real Notion writes.

> See [`references/examples.md`](examples.md) → "Sweep-mode false-positive & split-report examples (Step 1f)" for the false-positive end-note example and the split-report format.

**4. Apply boilerplate templates** to the cheap buckets without further fetches.

> See [`references/examples.md`](examples.md) → "Sweep-mode boilerplate templates (Step 1f)" for the full `NEVER_LAUNCHED` and `TOO_YOUNG` close-out templates.

**5. Run Steps 2–7 per row** only on the `RUNNING_WITH_DATA` subset.
Skip Step 0 (knowledge pre-fetch) per-row since it's already loaded once
for the sweep — re-use the cached priors across all rows.

**6. Cross-cutting learnings** at the end of the sweep should explicitly
call out the classification breakdown (`X% of rows were never-launched
stubs — recommend catalog hygiene rule Y`) so the sweep produces
process-level signal, not just per-row readouts.
