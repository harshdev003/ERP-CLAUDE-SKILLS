---
name: gather-previous-month-analytics
description: Pull last month's coach and community analytics from the PaymentService Postgres `analytics` table and write the seven monthly JSON reports plus their CSVs into this repo. Trigger when the user says "gather previous month analytics", "run the monthly analytics report", "pull last month's analytics", "regenerate the monthly coach reports", "do the monthly payout files", or just "monthly analytics" while in the AnalyticsReports repo.
---

# gather-previous-month-analytics

Generates the seven monthly analytics files for the previous calendar month (or a user-supplied year/month) by reading directly from the PaymentService Postgres `analytics` table, then runs `src/jsontocsv.js` to produce the CSVs.

## When to use

The user runs this once per month (typically on the 1st-2nd) to refresh `reports/`. They previously did it by hand: run a SQL query in a DB client, copy 7 result rows into 7 JSON files, edit 3 of them to remove the `result` wrapper, then `node ./src/jsontocsv.js`. This skill collapses all of that into one command.

## What the user wants

Equivalent to running, on the 1st of the month:

```sql
select *
from "analytics"
where "day" is null
  and "createdAt" >= date_trunc('month', now())
order by "createdAt" desc
```

…and then writing one JSON file per `type` into the locations below, stripping the top-level `result` key for combined / payout / community files, and finally running `node ./src/jsontocsv.js` to emit the CSVs.

## Type → file mapping

| analytics.type | path (relative to repo root) | shape on disk |
|---|---|---|
| `monthlyCoachSubscriptionDetails` | `reports/coaches/subscriptions/coach/{YYYY}-{MM}-users.json` | full `data` object (`{result: [...]}`) |
| `monthlyCoachingSubscriptionDetails` | `reports/coaches/subscriptions/coaching/{YYYY}-{MM}-users.json` | full `data` object |
| `monthlyVideoSubscriptionDetails` | `reports/coaches/subscriptions/video/{YYYY}-{MM}-users.json` | full `data` object — **RETIRED, never emitted**; kept in the mapping only so the script reports it as an expected skip |
| `monthlySession` | `reports/coaches/sessions/{YYYY}-{MM}-sessions.json` | full `data` object (aggregated per coach) |
| `monthlyCombined` | `reports/coaches/combined/{YYYY}-{MM}-combined.json` | unwrapped — just the `result` array |
| `payout` | `reports/coaches/payouts/{YYYY}-{MM}-payout.json` | unwrapped — just the `result` array |
| `monthlyCommunityPayout` | `reports/communities/payouts/{YYYY}-{MM}-community.json` | unwrapped — just the `result` array |

`{MM}` is two-digit zero-padded (e.g. `04`, not `4`) to match existing files.

The combined / payout / community files are unwrapped because `src/jsontocsv.js` consumes a top-level array. The detail / subscription / session files keep the `{result: [...]}` shape because that's what the prior months on disk look like.

## Workflow

### 1. Determine target year/month

Default: the **previous calendar month** relative to today's date. If the user gives a different month explicitly ("redo March", "for 2026-03"), use that instead.

Confirm the resolved `{YYYY}-{MM}` with the user in one short line before connecting to the database, so a misread date doesn't silently overwrite the wrong files.

### 2. Run the gather script

From the repo root:

```bash
node .claude/skills/gather-previous-month-analytics/scripts/gather.js \
  --year=2026 --month=04
```

If `--year` / `--month` are omitted the script defaults to the previous month. Other supported flags:

- `--payment-service-path=<dir>` — directory holding the PaymentService `.env` and `node_modules`. Defaults to `/Users/jeromecurlier/Development/Aura/Services/PaymentService`. The script `require`s `pg` and `dotenv` from there, so this repo doesn't need its own `node_modules`.
- `--repo-root=<dir>` — root of AnalyticsReports. Defaults to two levels above the skill folder.
- `--skip-csv` — skip the `node ./src/jsontocsv.js` step at the end.
- `--overwrite` — re-write JSON files even if they already exist (default: skip existing files and warn).

The script:

1. Loads `${paymentServicePath}/.env` for `POSTGRES_DATABASE_URI`.
2. Connects with `pg.Client` and runs:
   ```sql
   select "type", "year", "month", "data"
   from "analytics"
   where "day" is null and "year" = $1 and "month" = $2
   ```
3. For each row whose `type` is in the mapping table, writes the JSON to disk (creating directories as needed), unwrapping `result` for combined/payout/community.
4. Logs every write, every skip, and every missing type so the operator can spot if `monthlyVideoSubscriptionDetails` (or any other type) wasn't generated upstream.
5. Runs `node ./src/jsontocsv.js` from the repo root unless `--skip-csv` was passed.

### 3. Verify

After the script finishes, check that `reports/coaches/combined/{YYYY}-{MM}-combined.csv`, `reports/coaches/payouts/{YYYY}-{MM}-payout.csv`, and `reports/communities/payouts/{YYYY}-{MM}-community.csv` were created. The detail / subscription / session JSON files don't have CSV counterparts — `jsontocsv.js` only walks the combined / payouts / communities folders.

### 4. QA check

Run a sanity comparison against the **previous** month's files before reporting done — this is what catches a wrong type, an upstream schema change, or a half-failed run. The skill has done its job only when these all check out:

**a. Row counts vs the previous month.** For each of the seven files, compare the row count to the month before. Coach roster (`combined`, `payouts`) is essentially fixed and should match within ±5; subscriptions / sessions / community payouts move with real activity and ±20% month-over-month is normal, but a 2× or 0.5× swing is a flag.

```bash
prev_year_month=2026-03   # the month before the one you just generated
this_year_month=2026-04   # the month you just generated
for spec in \
  "reports/coaches/subscriptions/coach/{}-users.json" \
  "reports/coaches/subscriptions/coaching/{}-users.json" \
  "reports/coaches/subscriptions/video/{}-users.json" \
  "reports/coaches/sessions/{}-sessions.json" \
  "reports/coaches/combined/{}-combined.json" \
  "reports/coaches/payouts/{}-payout.json" \
  "reports/communities/payouts/{}-community.json" ; do
  prev="${spec//\{\}/$prev_year_month}"
  this="${spec//\{\}/$this_year_month}"
  if [ -f "$prev" ] && [ -f "$this" ]; then
    p=$(node -e "const d=JSON.parse(require('fs').readFileSync('$prev')); console.log(Array.isArray(d)?d.length:(d.result?d.result.length:'?'))")
    t=$(node -e "const d=JSON.parse(require('fs').readFileSync('$this')); console.log(Array.isArray(d)?d.length:(d.result?d.result.length:'?'))")
    printf "%-50s prev=%-5s this=%-5s\n" "$(basename $(dirname $this))/$(basename $this)" "$p" "$t"
  else
    printf "%-50s missing prev or this\n" "$spec"
  fi
done
```

**b. Schema sanity.** The unwrapped files (combined / payouts / community) must be top-level arrays; the wrapped files (subscriptions / sessions) must be `{result: [...]}`. A file with the wrong shape means the unwrap rule was applied to the wrong type. For sessions specifically, the keys on row 0 must be `coachId`, `coaching-session`, `coaching-session-with-referral`, `ala-carte-paid-video`, `ala-carte-paid-video-with-referral` — if you see `chargeAt` / `userId` / `chargeAmount` instead, the script picked up `monthlySessionDetails` rather than `monthlySession` and needs to be fixed.

**c. CSVs landed.** The three CSV files (`combined`, `payout`, `community`) must exist and have the same row count as their JSON source plus 1 (for the header). If a CSV is missing, `node ./src/jsontocsv.js` either didn't run or skipped because the CSV already existed — `jsontocsv.js` is idempotent and won't overwrite. Delete the stale CSV and re-run if needed.

**d. No unexpected missing types.** The script logs `Missing types not present in DB for {YYYY}-{MM}:`. Treat each missing type as a question to bring back to the user — a missing one is either an upstream emission that got skipped or a renamed type. The one **expected** entry is `monthlyVideoSubscriptionDetails`, which is retired (see Notes below) and will be listed every month; six written + that one missing is a clean run.

To check whether a missing type ever existed rather than assuming it broke, list what the table actually holds for the month (connect **without** SSL — the server rejects `ssl:{rejectUnauthorized:false}`; and note `analytics` stores months **1–12** — `jan=1`, `dec=12`, written as `lastMonth.month() + 1` in
`PaymentService/src/jobs/analytics.js` — so July is `month=7`, matching this skill's `--month=07`):

```bash
PS="${PAYMENT_SERVICE_DIR:-$HOME/Development/Aura/Services/PaymentService}"
PS="$PS" node -e '
const ps=process.env.PS;
require(ps+"/node_modules/dotenv").config({path:ps+"/.env",override:true});
const {Client}=require(ps+"/node_modules/pg");
(async()=>{const c=new Client({connectionString:process.env.POSTGRES_DATABASE_URI});
await c.connect();
const r=await c.query(`select "type", count(*) n from "analytics" where "day" is null and "year"=2026 and "month"=7 group by 1 order by 1`);
r.rows.forEach(x=>console.log("  ",x.type.padEnd(42),x.n)); await c.end();})()
.catch(e=>{console.error("ERR",e.message);process.exit(1)});'
```

If anything in (a)–(d) looks off, surface it to the user with the specific file and what you saw, before claiming the run is good.

### 5. Sheet checkpoints

Once the exports are committed, print the numbers the compensation sheets must show after the paste, so each sheet step is checked against the export rather than eyeballed:

```bash
node .claude/skills/gather-previous-month-analytics/scripts/checkpoints.js --year=2026 --month=09
```

It reads only the files on disk (no database) and prints, for the coach compensation tab: the last coach row, totals row and percentage row; the previous month's totals row to repoint the percentage formulas at; every segment's total and month-over-month % **at its post-separator column letter** (the `Data` column inserted at F shifts F onward one to the right); which segments are **LIVE** (0 last month, so their `#DIV/0!` must be kept); and the dropped/added coaches, with the rollover check: the exact ranges to sum (`BC2:BC<last>` on last month's tab, `BA2:BA<last>` on this one), each dropped coach's `BC` cell on last month's tab, and a ready-to-paste formula for the expected BA total. The exports carry no rollover, so those totals come from the sheet; never sum the whole column, which includes the totals row. For the payout tab: the last row, the export total and the ≥ $10 / under-$10 split, plus the identity `sum(BB) = export total + sum(BA) + adjustments`. For the community sheet: the last row and the total-gross / total-payout the sheet must match.

Column letters come from the **CSV header**, not the JSON key order — the CSV is what gets pasted, and the two orders differ.

Hand the output to whoever works the sheet, or walk them through it one step at a time: each sheet step has a number here to check against.

### 6. Report back

Summarize for the user: target month, which files were written (or skipped because they already existed), any missing types, the row-count comparison from step 4a, and confirmation that the CSVs were emitted. Don't paste the JSON contents.

## Notes & gotchas

- The PaymentService `.env` is gitignored and contains real production credentials. Never echo `POSTGRES_DATABASE_URI` or include it in a commit, log, or response.
- `payout` is the only type without the `monthly` prefix — it comes from `src/jobs/payout.js` in PaymentService which inserts `type: 'payout'` (the `month`/`year` columns identify it as monthly; the `day is null` filter is what scopes it to monthly aggregation). Do not rename it.
- For sessions, use `monthlySession` (per-coach aggregated rows: `coachId`, `coaching-session`, `ala-carte-paid-video`, …) — not `monthlySessionDetails`. The latter exists in the DB and returns one row per charge, which is a different schema and not what prior months on disk look like.
- `monthlyVideoSubscriptionDetails` is **retired, not pending** — a-la-carte video membership is no longer sold, so the type is never emitted. Confirmed 2026-08-03: no `%video%` row has *ever* existed in the `analytics` table, and `reports/coaches/subscriptions/` only contains `coach/` and `coaching/`. Keep logging it as "skipped: not in DB" and treat it as expected; do not chase it upstream. (Earlier revisions of this file described it as "added upstream but not always emitted yet", which wrongly implied it was coming.)
- Do not confuse that with the payout export's `videoSubscription*` fields, which are a **legacy alias for coach subscriptions** and are live — worth thousands of dollars a month (July 2026: 50 subs, $8,580 gross, $6,606.60 payout, identical to the combined export's `coachSubscriptions*`). The genuinely dead product is `alacarte*` ($79 in July).
- The script would write a `reports/coaches/subscriptions/video/` directory if the type ever appeared. Given the retirement above, that directory should stay absent.
- File naming uses **two-digit** zero-padded months for the year-month prefix (e.g. `2026-04-combined.json`). Older files (pre-2022) used unpadded months; do not match that — pad consistently.
