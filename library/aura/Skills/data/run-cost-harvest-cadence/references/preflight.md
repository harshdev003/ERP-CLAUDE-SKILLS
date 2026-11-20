# Store-vs-warehouse preflight (Step 1, probe 1a)

Read this when probe 1a reports anything other than three clean lines.

The probe is `bq_preflight.py`, shipped by
[`eng:store-aurie-user-daily-cost`](../../../../eng/skills/store-aurie-user-daily-cost/SKILL.md).
Invoke that skill to run it — never reach into its `scripts/` directly (Architecture
principle 1); its SKILL.md resolves its own script path. It asks BigQuery's own
`INFORMATION_SCHEMA` two questions and returns one line per table.

## Schema drift → the run is REFUSED (exit 1)

A column the store declares and BigQuery lacks makes **every** partition load 400
with `Cannot add fields` — the load carries no schema-update option, so no day can
sync until the table is rolled forward.

**Stop the cadence here.** Do not run Step 2. Report the refusal, name the column
and the table, and hand the operator the remedy the probe prints:
`bq_sink.py --recreate`. That drops and rebuilds all three tables from the local
CSVs — a destructive migration that needs a human, and needs the
CSV-is-a-strict-superset-of-BigQuery check re-verified **in the same session**
before it runs (days present in BigQuery but not in the CSV must be empty; shared
days must agree on row count and `total_cost`).

Do not fall back to running the steps the refusal does not literally name:
Steps 2b–2d read the population Step 2 was supposed to extend, so they would
report coverage against a stale one and call it full.

## Max-date divergence → reported, never refused (exit 0)

Each table's local max date next to its warehouse max date, a `** BEHIND **`
marker per table whose warehouse lags its store, and a `** MIXED STATE **` line
when the three warehouse tables disagree with each other.

This is reported rather than refused because divergence has benign causes that
drift does not — a store legitimately skips a date it has no rows for, and the
KPI store is rewritten fresh each run so it can trail by a day mid-catch-up.

## Exit 2 → the probe could not run

Say so and carry on. An unresolvable probe must not read as a clean one, but it
must not block the cadence either.

## Which direction this errs, and why

Toward **refusing** — stated here as this cadence's own decision, not as the
composed skill's rule (`/CLAUDE.md` → *Orchestrators: restrict what you show*).

A cadence tick stopped early costs one re-run once the migration lands. A tick
that proceeds into a store that cannot sync costs a day that exists only locally
— and every `chart-aurie-*` read of `aurie_per_user_daily_cost_prod` then renders
clean, plausible and **understated** with no error. A missing day has no shape
(`/CLAUDE.md` → *Tag, don't drop, wherever the output persists*).

That is exactly what happened: `voice_fish` was added to the store in ENG-1306 and
never to BigQuery. `bq_sink.py` printed the 400 and exited non-zero **every day for
three days**, and the days kept accumulating locally because nothing upstream
treated a failed sync as a reason to stop. The three tables drifted into a mixed
state (per-user at 2026-09-17, the other two at 09-19) and nothing reported it.
[PROD-6241](https://linear.app/aura-health/issue/PROD-6241) ran the migration;
[PROD-6242](https://linear.app/aura-health/issue/PROD-6242) added this probe.
Same failure class as [ENG-1120](https://linear.app/aura-health/issue/ENG-1120).
