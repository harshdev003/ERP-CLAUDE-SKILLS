---
name: chart-aurie-lifetime-cost
description: >
  Aurie projected LIFETIME cost per joined user to a fixed horizon (52w + 104w by default) — the cost-decay
  curve extrapolated with a geometric-decay tail and summed, per segment + per-cohort. Answers "estimated
  lifetime cost per weekly cohort." Same three segments as eng:chart-aurie-cost-curve: all users, premium
  (all-activated), free-never-subscribed. Trigger for: "lifetime cost", "projected cost per user", "cost per
  cohort to 52 weeks / 1 year / 2 years", "estimated lifetime cost", "LTV cost side", "project the decay". The
  projection companion to eng:chart-aurie-cumulative-cost (observed cost-to-date) and eng:chart-aurie-cost-curve
  (per-week decay). PROJECTION — directional; fit on a few weeks of data. Composes
  eng:backfill-aurie-user-ai-created; reads BigQuery aura-main.aurie_cost. Aggregate-only — never emits a UID.
metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/BigQuery               # dataset/table conventions, GCP_KEY auth, bq.py
    - Compliance/Privacy           # reads PII-bearing tables — output must stay aggregate
---

# Chart Aurie Lifetime Cost

## Goal

Estimate the **lifetime cost per joined user** by extrapolating the cost-decay curve to a fixed horizon
(52w and 104w by default) — the cost side of LTV / payback, per segment and per join-week cohort.

- **Chart A — weeks since AI signup, all users.**
- **Chart B — weeks since subscription activation, users who activated premium** (all-activated denominator
  — NOT survivorship-filtered, else lifetime is overstated by conditioning on survivors).
- **Chart C — weeks since AI signup, free users who never bought a subscription.**

## Method

The per-joined-user cost curve `avg_per_user(w)` = `Σ(cohort-week-w cost) ÷ Σ(cohort size)` already bakes
in retention (a churned user counts as \$0), so its running total is the cumulative lifetime cost. The
projection extends the decaying tail:

```
projected_lifetime(H) = Σ_{w=0..last} avg_per_user(w)                       # observed cost-to-date
                      + Σ_{w=last+1..H} avg_per_user(last) · r^(w-last)      # geometric-decay tail
```

`r` = geometric mean of the post-onboarding (w≥1) week-over-week ratios, clamped to (0, 0.98) so the tail
converges. Because per-joined-user cost decays toward \$0 as a cohort churns, **52w and 104w usually differ
little** — the finding itself: cost is front-loaded, so lifetime ≈ cost-to-date + a small tail.

## Step 0 — Prerequisites

Reads BigQuery (`aura-main.aurie_cost` + the Mixpanel Nessie `ai_subscription_activated` event) via the
bundled `bq.py` — needs `GCP_KEY` and `google-cloud-bigquery`. No BigQuery MCP, so this is API-first.

## Step 1 — Coverage guard

Charts A/C join `aura-main.aurie_cost.aurie_user_ai_created`; run
[`eng:backfill-aurie-user-ai-created`](../backfill-aurie-user-ai-created/SKILL.md) first if `missing` is
non-zero. `lifetime.py` reports coverage.

## Step 2 — Aggregate

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/chart-aurie-lifetime-cost/scripts/lifetime.py --max-week 8 --horizons 52,104
```

Emits per segment: `observed_curve`, `observed_cost_to_date`, `decay_ratio` + `fit_method`,
`converged_lifetime_per_user`, `projections` (`[{horizon_weeks, projected_lifetime_per_user, tail_added}]`),
and `cohorts` (`[{cohort_week, cohort_size, cost_to_date, projected:{w52, w104}}]`). Flags: `--horizons`
(default `52,104`), `--max-week N`, `--last-weeks N` (fit on a trailing window), `--start YYYY-MM-DD`.

## Step 3 — Render

Render the projected lifetime per segment as a **bar** (premium / all / free) at each horizon, with the
observed cost-to-date marked so the projected tail is visible. `eng:build-aurie-cost-report` renders this.
Always state it's a projection. If the widget tool is unavailable, print a table: `segment` | `cost_to_date`
| `52w` | `104w` | `r`.

## Notes & caveats

- **PROJECTION — directional.** Fit on ~5–6 weeks; premium samples thin in later weeks. Treat as an
  order-of-magnitude estimate, not a committed number.
- **Per-joined-user (retention baked in).** No separate retention multiply — the cost curve is already cost
  per joined user, so summing it is the lifetime. `eng:chart-aurie-retention` is the standalone retention view.
- **Premium = all-activated**, not current-premium — the correct base (survivor-conditioned costs overstate).
- **52w ≈ 104w** when the decay converges (the usual case) — report both; a large gap between them would
  signal a slow/non-converging tail worth scrutinising.
- **Geometric-decay assumption.** If the fitted `r ≥ 1` (flat/rising tail), it's clamped to 0.98 and flagged
  in `fit_method`; revisit with more weeks.
- **Privacy.** Output is aggregate-only (per-segment / per-cohort projections), never a UID.

- **Prod-only (PROD-3589 Part B).** Reads `aurie_per_user_daily_cost_prod` (env='prod') instead
  of the base table — dev/QA and not-yet-tagged users are excluded from these aggregates; see
  eng:backfill-aurie-user-env, which owns and refreshes the view. Still aggregate-only, never
  emits a UID.

## Origin

Steve's "estimated lifetime cost per weekly cohort" ask ([PROD-3560](https://linear.app/aura-health/issue/PROD-3560));
requested to a 52w / 104w horizon. Part of the cost-understanding chart set
([PROD-3498](https://linear.app/aura-health/issue/PROD-3498)).
