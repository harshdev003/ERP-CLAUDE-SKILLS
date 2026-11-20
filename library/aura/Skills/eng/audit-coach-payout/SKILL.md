---
name: audit-coach-payout
description: >
  Explain why an Aura coach's dashboard listen numbers don't match what they were paid, month
  by month. Reads the dashboard side from Firebase RTDB (the exact numbers the coach sees) and
  the payout side from the archived payout queries, then splits the gap into its two causes:
  listens dropped by the 6-per-day-per-coach cap, and a small timing residual. Emits the
  reconciliation table plus a plain-English explanation support can send to the coach. Trigger
  on "audit coach payout", "review payout for coach X", "why doesn't the coach dashboard match
  the payout", "coach says her listens don't match her statement", "explain the listen gap for
  {coach}", "reconcile dashboard vs payout", "payout discrepancy for {coach}", "how many
  listens did the cap remove for {coach}", "coach payout complaint", or a pasted coach name or
  UID alongside a payout question. Do NOT use to rank coaches by earnings
  (`eng:analyze-aura-cost-per-coach`) or to assess content and upload cadence
  (`data:investigate-coach`).

metadata:
  version: "1.3.0"
knowledge:
  reads:
    - Guide/Index           # baseline routing aid (universal)
    - Tools/Firebase        # RTDB read of /coachAnalytics/<uid> - the coach-facing dashboard numbers
    - Tools/Aura API        # GET /payouts/analytics, POST /payouts/listens/aggregate, GET /coaches
    - Compliance/Privacy    # archive rows carry coachEmail + coachPaypal; burst rows carry end-user UIDs
---

# Audit Coach Payout

A coach looks at their dashboard, sees one number, then reads their payout statement and
sees a smaller one. This skill reconciles the two and attributes the whole difference.

The gap is **not** a bug and it is **not** rounding. It decomposes exactly:

```
gap  =  excluded_by_cap  -  residual

  gap             dashboard premium listens - premium listens actually paid
  excluded_by_cap listens dropped by the 6-per-day-per-coach payout cap
  residual        no-cap payout query - dashboard (timing / cached listens /
                  wrong device date; historically 1-13 listens a month)
```

Typical result: the cap is ~3-4% of a coach's premium listens and explains essentially
all of the gap, while the residual is a rounding-scale handful running the *other* way.

## Prerequisites

| Need | Why | Failure mode |
|---|---|---|
| `AURA_ADMIN_TOKEN` (or `AURA_API_TOKEN`) | `/payouts/analytics` needs role `admin`, `creator` or `service` | script exits with the role hint |
| `GCP_KEY` (or `GOOGLE_APPLICATION_CREDENTIALS`) | service-account read of the Firebase RTDB | script exits naming what is missing |
| `node`, and `AURA_CLI_DIR` (a checkout whose `node_modules` has `firebase-admin`) | the RTDB read goes through the bundled `rtdb_get.js`: the Admin SDK realtime protocol, exact paths only, never REST | script exits naming what is missing |

`RTDB_URL` (or `FIREBASE_DATABASE_URL`) and `AURA_API_BASE` default to production and rarely need setting.

## Step 1 - Resolve inputs

Ask for the **coach** and the **month range**. If either is missing, ask; do not guess
a range.

**The coach id (28-char Firebase UID) is the canonical key** — all three data sources are
keyed by it (`/payouts/analytics?coachId=`, `/coachAnalytics/<uid>`, and the aggregate
endpoint). Prefer it whenever the requester has one; the script echoes the resolved id in
the report header so any audit can be re-run exactly.

A display name is a convenience layer over a mutable field and resolves only against the
**active** catalog. A coach who has left still has archived payout rows and audits fine
**by id**, but their name will not resolve at all — if a name lookup comes back empty,
reach for the id rather than concluding there is no data.

Name matching is **exact-first**: a full name that equals a catalog entry wins outright,
and only if nothing matches exactly does the script fall back to substring search. This
matters because three names in the catalog (*Hannah*, *Elizabeth*, *Aina*) are strict
substrings of a longer coach name — under substring-only matching those coaches are
unreachable by name, since their own correct full name always looks ambiguous.

A name that is still ambiguous after that is a stop, not a guess — the script prints the
candidates with their ids and exits. The catalog is also walked page by page, because a
name unique on the first page can have a twin further in; matching one page would audit
the wrong coach instead of stopping.

**Never start before 2025-11.** The no-cap archive (`monthlyListenTotalNoCap`) only
landed 2025-11-07; earlier archived rows do not follow the same pattern and will
silently mislead. The script refuses earlier ranges.

## Step 2 - Run the audit

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/audit-coach-payout/scripts/audit.py \
  --coach "Hannah Castillo" --from 2026-04 --to 2026-07
```

Add `--bursts` to also name the listeners who drove the cap, and `--json` for machine
output. Show the markdown table inline, verbatim.

The script pulls, per month:

| Row | Source |
|---|---|
| Dashboard - Premium / Total | Firebase RTDB `/coachAnalytics/<uid>/<year>[monthIndex]` |
| Payout - All listens / Premium Paid | `GET /payouts/analytics?type=monthlyCombined` |
| Query used for payout | `…&type=monthlyListenBreakdown` (sum of the 5 tier columns) |
| Same query, no 6/day cap | `…&type=monthlyListenBreakdownNoCap` |
| Excluded by cap / Residual / Gap | derived from the four above |

These archived rows **are** the verbatim output of `PaymentService/sql/<type>.sql`, run
by the monthly analytics job — not a reimplementation. See
[`references/method.md`](references/method.md) for the provenance proof and why this is
the *only* correct source for older months.

## Step 3 - Check the identity before interpreting

The script prints whether `gap == excluded_by_cap - residual` holds for every month.

- **Holds** — the gap is fully attributed; go to Step 4.
- **Fails for a month** — stop and investigate before telling anyone the cap explains
  it. Something new is contributing. Read `references/method.md` → "When the identity
  breaks" first.
- **A month shows `-`** — no archived payout row exists for it (job didn't run, or the
  month hasn't been paid yet). Say so explicitly rather than treating it as zero.

## Step 4 - Bursty listeners (optional, recent months only)

`--bursts` diffs `POST /payouts/listens/aggregate` with `respectDailyCap` true vs false,
grouped by user, and ranks who lost the most listens to the cap.

**This only works inside the ~40-day `userContentListen` retention window**, and there
are three outcomes — read which one you got before quoting anything:

| Script says | Meaning |
|---|---|
| a normal table | month fully retained; totals are the month's |
| **PARTIAL MONTH** + coverage % | retention cuts through this month — rows are real but cover only part of it |
| "raw listens aged out" | nothing retained; no per-listener answer from this path |

Zero listeners is aged-out data, never "no bursts". The partial case is the dangerous
one: a boundary month returns real rows for a fraction of its days, which look like a
complete result. The script detects it by comparing the live no-cap count against the
archived no-cap total for that month and refuses to present partial totals as the
month's (July 2026, for instance, retains only ~19%). **Rankings from a partial month
are indicative; totals are not.** For a partial or aged-out month, run the backup-table
SQL in [`references/method.md`](references/method.md) → "Per-listener detail for purged
months".

The finding is usually concentrated: a handful of listeners generate a third of all
capped listens, while steady daily listeners lose nothing. Coaches with many **short**
tracks hit the cap far harder than coaches with long ones, because the cap counts
listens, not minutes.

## Step 5 - Write the explanation

Close with plain English the requester can forward. State, for each month: the gap, how
much was the cap, and how much was residual. Then the one-line cause:

> Your dashboard counts every premium listen. Payout counts at most 6 listens per
> listener per day for a given coach, so a listener who plays 28 of your tracks in one
> day is paid for 6. In <month> that accounted for <N> of the <G> listen difference;
> the remaining <R> is listen-timing noise.

Do not promise a fix or a statement change — whether payout statements should show the
capped count is a product decision, not this skill's call.

**PII.** Coach display name and coach id are fine — the id is the audit key and belongs
in the output. The `monthlyCombined` rows also
carry `coachEmail` and `coachPaypal` — never surface those. Listener rows are end-user
UIDs: keep the 10-char prefix the script emits, never a full UID or an email
(`Compliance/Privacy`).

## Interpreting the numbers

| Observation | Meaning |
|---|---|
| Cap ~2.5-3% of premium listens | normal for a coach with typical track lengths |
| Cap ~4%+ | short tracks and/or a few heavy binge listeners — check `--bursts` |
| Residual 1-15 | expected timing noise; not worth investigating |
| Residual in the hundreds | not normal — investigate before answering |
| Residual negative (dashboard > no-cap query) | normal; the dashboard lags slightly |
| Dashboard differs from an old screenshot | expected — `coachAnalytics` keeps incrementing as late listens arrive |

## Gotchas

- **The route prefix is `/payouts/...`, not `/payments/payouts/...`.** The latter
  returns a 404 HTML page that is easy to misread as "no data".
- **Never re-run `sql/monthlyListenBreakdown.sql` live for an old month.**
  `userContentListen` keeps ~40 days, so it returns 0 rows — which reads as "no
  listens", not "data gone". Use the archive.
- **The archive is the payout-time snapshot** — i.e. what was actually paid. Re-running
  the same SQL later can drift by a few listens as late data lands. Prefer the archive.
- **`--bursts` on a boundary month is partial, not empty.** The script flags it with a
  coverage %; never quote a partial month's totals.
- **Burst totals come from the response `summary`, never from summing rows.** The server
  clamps returned rows to `maxLimit` (5000) — a coach with more listeners than that gets a
  truncated row list with no error. `summary.total_listens` / `summary.groups` are computed
  over every group and stay exact; the script uses those and flags a truncated ranking.
  The **count of listeners hit by the cap** cannot come from the summary (no per-group
  cap-vs-no-cap comparison is exposed), so on a truncated month it degrades to a lower
  bound and prints as "at least N" rather than a count. Today's largest coach sits at
  ~4,500 listeners a month, so this is close, not theoretical.
- **Row 8's value is the sum of the five tier columns.** The ranges are exhaustive and
  disjoint, so the sum equals the query's row count; it should equal
  `monthlyCombined.premiumPaidListens` exactly. If it doesn't, read `references/method.md`.
