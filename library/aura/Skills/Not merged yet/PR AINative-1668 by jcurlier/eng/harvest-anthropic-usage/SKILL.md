---
name: harvest-anthropic-usage
description: Harvest per-user Claude usage from the claude.ai Team/Enterprise org analytics export into BigQuery (`aura-main.anthropic_usage`) — daily token counts, request counts and overage spend per user, product and model, plus a daily seat-tier snapshot. Use when someone says "harvest Claude usage", "pull our Anthropic usage into BigQuery", "backfill Claude token usage", "how much Claude are we using per person", "update the anthropic_usage dataset", "refresh Claude seat data", or asks to measure usage on our Claude subscription. Reports on the harvested data are `eng:report-anthropic-usage` — this skill only loads it. Do NOT use for OpenAI cost skills (`eng:analyze-openai-costs`) or the Aurie cost store (`eng:store-aurie-user-daily-cost`).
metadata:
  version: "1.8.2"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
---

# Harvest Anthropic (claude.ai) usage into BigQuery

Loads the claude.ai organization analytics export into
`aura-main.anthropic_usage`, one BigQuery day-partition per calendar day.

## Why this exists

Our Claude Team subscription bills per seat. The analytics UI and the spend
report show **overage dollars only** — usage inside a seat allowance shows as
`\$0.00`. But the export's **token columns are complete**: they cover all usage,
inside the allowance and beyond it.

So tokens, not dollars, are the measure of what a person actually uses. A user
can move billions of tokens and bill nothing. That gap is the whole reason for
this dataset.

## Two ways in — pick one

| | **Browser-assisted** (`load_downloads.py`) | **Headless** (`harvest.py`) |
|---|---|---|
| Credential | None. A human is already signed in. | `CLAUDE_ADMIN_SESSION_KEY` |
| Good for | A weekly session with someone at the keyboard | Scheduled or unattended runs |
| Cost | One navigation per day harvested | One command |

**Prefer headless. Keep the browser as the fallback.**

The headless path is one command and needs no browser open, which is what makes a
weekly cadence survivable: a cadence that needs a human at a keyboard is a cadence
that stops. Re-harvesting a day already held replaces its partition and leaves the
table unchanged, so overlapping runs are safe and `--window` can stay generous.

The browser path exists because the cookie expires and something has to work while
it is dead. Navigating to the export URL downloads the CSV using the live session,
so a week is seven navigations and then one load command.

Both are known to produce the same numbers for the same day — they read the same
export — so either may be used for any day without reconciling afterwards.

```
https://claude.ai/api/organizations/<ORG_ID>/analytics/spend-report-export?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
https://claude.ai/api/organizations/<ORG_ID>/members/export
```

Set `start_date` and `end_date` to the **same day**. A range export is a single
aggregate over its window with no way to split it back into days, so the loader
refuses those rather than booking a week onto one date.

Then:

```bash
PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3
$PY ${CLAUDE_PLUGIN_ROOT}/skills/harvest-anthropic-usage/scripts/load_downloads.py
```

It reads `~/Downloads`, takes the date from each filename, loads every **new**
single-day file plus any members snapshot, and reports what it skipped.

**Days already recorded in `harvest_log` are skipped.** A download directory
accumulates — after a backfill it holds every day ever exported — and re-parsing
all of them costs minutes to write nothing new. Pass `--reload` to redo them
anyway. This is a time saving, never a correctness one: the partition replace is
idempotent either way, so if `harvest_log` cannot be read the loader **loads
everything** rather than skipping blind, and says so.

What does **not** work, all tested: blob downloads do not land even behind a real
click gesture, the site's CSP blocks posting to a local receiver, and large tool
returns truncate. The app's own download is the only way out of the browser.

## What you need before running (headless path only)

| Requirement | How to satisfy |
|---|---|
| `CLAUDE_ADMIN_SESSION_KEY` | claude.ai session cookie for an **Owner**. See below. |
| `CLAUDE_ADMIN_ORG_ID` | The org UUID. Visible in any admin URL, or in a manually exported filename `spend-report-<org-id>-<dates>.csv`. |
| `GCP_KEY` | Existing service-account JSON, already used by the cost skills. |
| `google-cloud-bigquery` | Use the framework Python that has it installed. |

**Getting the session key.** There is no API key for this. The Claude Enterprise
Analytics API is Enterprise-only and explicitly unavailable on Teams, and an
Admin API key addresses the separate Console organization, not the claude.ai
subscription org. So:

1. Sign in to claude.ai as an **Owner or Primary Owner** (Admin is not enough —
   analytics is Owner-only on Team plans).
2. DevTools → Application → Cookies → `https://claude.ai` → copy `sessionKey`.
3. Put it in `~/.claude/settings.json` under `env` as `CLAUDE_ADMIN_SESSION_KEY`,
   then restart Claude Code.

**A pasted key lasts about 28 days, and that is the cadence.** claude.ai renews
the cookie by sending a fresh `Set-Cookie` as you browse, so a live browser
session rolls over indefinitely — but a value copied into `settings.json` is a
frozen snapshot that never receives that renewal. So it expires roughly 28 days
from when it was *issued*, not from when it was pasted, and the first period can
be shorter. One paste covers about four weekly harvests.

**Nobody should have to remember to check.** `--check` needs no credential, so
schedule it and let it say when the data has gone stale — that is the one signal
that keeps working while the cookie is dead. On macOS a user LaunchAgent runs
[`scripts/weekly_check.py`](scripts/weekly_check.py) every Monday with the framework
Python: it harvests the trailing 10 days when a session key is set, always ends on
`--check`, and logs every run to `~/.claude/jobs/anthropic-usage-check.log`.

**Every stop is a follow-up.** `weekly_check.py` raises one `shared:raise-followup`
post (kind `blocked`, with a card) through this skill's own copy of `followup.py`, keyed
`harvest-anthropic-usage:<stage>:stop`: `credentials` (`GCP_KEY` unset), `harvest` (no
session key, an expired one, or any other failure) and `check` (a stale or gappy
window, or BigQuery unreadable). An expired key needs a fresh `sessionKey` pasted; a stale window gets closed
with `--since`. It is silent on success and posts nothing to #alerts-ai: a stop that
needs a person is a follow-up, not an alert.

**It rotates, and there is no way around that.** `claude.ai/analytics/api-keys`
(the Enterprise `read:analytics` key) redirects to organization settings on a
Team plan — verified 2026-09-18. A cookie is the only credential that reaches
these endpoints.

So the design assumes expiry rather than hoping against it:

- **It fails closed.** A 401/403 exits 2 and writes nothing, so a dead cookie
  never overwrites real partitions with zero rows.
- **A lapse is fully recoverable.** The export is a re-readable archive back to
  2026-03-02, not a decaying log. Days missed while the cookie was dead can be
  fetched afterwards with `--since`, and the partition replace makes that
  idempotent. An expired cookie costs a delay, never lost history.
- **Staleness is detectable without the cookie.** `--check` reads BigQuery only
  and exits 1 when the data is behind **or has holes**, so the monitor keeps
  working in exactly the situation it exists to catch. Both conditions matter:
  lag catches a harvest that stopped running, gaps catch one that died
  mid-window or days nobody backfilled. Checking only lag passes a window
  missing most of its days whenever the newest one happens to be present.

That last point is the one that matters. The realistic failure is not the expiry
itself but nobody noticing for weeks. Run `--check` on a schedule; run the
harvest when it complains.

## Run it

```bash
PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3
S=${CLAUDE_PLUGIN_ROOT}/skills/harvest-anthropic-usage/scripts

# normal incremental run — re-harvests the trailing 7 days
$PY $S/harvest.py

# full history (data starts 2026-03-02)
$PY $S/harvest.py --backfill

# a specific window
$PY $S/harvest.py --since 2026-08-01 --through 2026-08-31

# see what would load, write nothing
$PY $S/harvest.py --dry-run --since 2026-09-01

# is the warehouse current? reads BigQuery only, needs NO session key.
# exit 1 when stale — this is the one to put on a schedule.
$PY $S/harvest.py --check
$PY $S/harvest.py --check --window 30 --max-age-days 2

# refresh the hand-entered list prices (rarely)
$PY $S/seed_pricing.py
```

Exit codes: `0` clean, `1` some days failed, `2` auth failed (nothing written),
`3` pre-flight failed.

## Design rules that are load-bearing

**An auth failure must never write a partition.** An expired cookie and a
genuinely idle day both produce zero rows. Only one of them should reach
BigQuery. Writing an empty partition on a 403 overwrites real data with nothing
and reads downstream exactly like a quiet day — the most expensive kind of
silent failure. So `check_auth()` runs before any load, a mid-run 401/403 stops
the whole run rather than continuing, and `ClaudeAdminAuthError` is a distinct
exception class so it can never be caught as "no usage".

**The export lags one day.** Spend data refreshes daily with a one-day delay, so
today's partition would be partial. `--through` defaults to yesterday UTC and a
future date is clamped with a warning rather than silently accepted.

**Re-harvest a trailing window, don't only append.** Days can settle after first
harvest. Each run re-pulls the last 7 days by default, and partitions are
replaced wholesale, so a late revision corrects itself instead of freezing wrong.

**Parse by column name, never position.** The export has 17 columns today. A
reordered or newly-inserted upstream column would shift every value if parsed
positionally. Missing required columns raise rather than loading nulls.

**Coverage is asserted in `harvest_log`, not inferred from the facts.** Writing a
zero-row partition to `daily_user_usage` achieves nothing observable: to every
query, a partition holding no rows is identical to one that was never written.
So "we read this day and nobody worked" cannot be a statement made in the fact
table. Every successful day — idle ones included — writes a `harvest_log` row,
and that is what `--check` and the report's coverage banner read. Inferring
coverage from the fact table reported every quiet weekend as a gap and would
equally have hidden a real gap beside a busy day.

**A failed day must not end the run.** Read errors and BigQuery errors are both
caught per day, so one bad day costs that day rather than the rest of the window
and the members snapshot. Only an auth failure stops everything, because after
one the remaining days would all be empty anyway.

## What lands where

| Table | Grain | Notes |
|---|---|---|
| `daily_user_usage` | day × user × product × model | Token columns = all usage. `net_spend_usd` = overage only. |
| `member_seat` | day × user | Seat tier, role, status. Departed members stop appearing. |
| `model_pricing` | effective_from × model | Hand-entered list prices, dated. See `seed_pricing.py`. |

Column-level detail: [`references/schema.md`](references/schema.md).

## Scope limits, state them when reporting

- **Claude Code, Chat, Cowork, Claude Tag and Claude in Chrome** are covered —
  whatever the org export includes.
- **API-key usage is not here.** Claude Code authenticated with an API key
  reports to the Console organization, not this dashboard. Those costs live in
  the OpenAI-style admin skills, not this dataset.
- **History starts 2026-03-02.** Probing back to 2024 returns zero rows. That is
  the true start, not a retention window.
- **The UI caps its date picker at 90 days; the endpoint does not.** Backfill
  reaches the full history regardless of what the picker offers.

## When it fails

| Symptom | Cause | Fix |
|---|---|---|
| exit 2, "AUTH FAILED" | Cookie expired, or account is not Owner | Re-copy `sessionKey`; confirm the account is an Owner |
| "missing expected column(s)" | Upstream export shape changed | Inspect the raw CSV; update `SPEND_COLUMNS` and the schema together |
| exit 3 at pre-flight | Org id wrong, or network | Check `CLAUDE_ADMIN_ORG_ID` |
| `BQUnavailable` | `GCP_KEY` unset or wrong Python | Use the framework Python; confirm the env var |
