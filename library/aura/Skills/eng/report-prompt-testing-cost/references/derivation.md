# How the per-key dollars are derived

OpenAI's Costs API reports dollars grouped by project, line item, or a handful
of other dimensions — **never by API key**. The Usage API reports tokens grouped
by `api_key_id`, but carries no dollars. Neither endpoint alone answers "what
did this key cost", and no third endpoint does either.

So the figure is derived, by joining the two on their shared dimensions.

## The method

For each UTC day in the window:

**1. Back-calculate an empirical rate.** Org-wide dollars ÷ org-wide tokens,
keyed by `(model, service_tier, dimension)`:

```
rate[day, model, tier, dim] = costs[day, model, tier, dim] / tokens[day, model, tier, dim]
```

- Dollars come from `/v1/organization/costs?group_by=line_item&bucket_width=1d`.
  A line item looks like `gpt-5.4-2026-03-05, cached input` or, on a non-default
  tier, `flex | gpt-5.4-2026-03-05, cached input`.
- Tokens come from
  `/v1/organization/usage/completions?group_by=model,service_tier&bucket_width=1d`.

The rate is *empirical*, not a published price — it therefore already carries
whatever negotiated discount the org has, and adapts on its own when OpenAI
changes pricing or ships a new model.

**2. Apply the rate to the key's own tokens.** Per-key token counts come from
`/v1/organization/usage/completions?group_by=api_key_id,model,service_tier`,
scoped per project:

```
usd = Σ  rate[day, model, tier, dim] × key_tokens[day, model, tier, dim]
     dim
```

**3. Fall back where a day is too thin.** A `(day, model, tier, dimension)` cell
with tokens but no dollars of its own uses the window-wide rate for that
`(model, tier, dimension)`. The count of cells that did this is reported in the
footnote.

**4. Disclose what could not be priced.** A combination with no rate on either
basis contributes $0.00 and is listed under an explicit ⚠️ undercount warning.
The alternative — silently costing it at zero — is the failure mode described
below, and it is the reason this warning exists at all.

## The two failure modes

Both produce a report that looks entirely reasonable. Neither raises.

### 1. The service tier is spelled differently by the two endpoints

This is a real bug that was hit while building the prototype.

| Endpoint | Spelling |
|---|---|
| `/usage/completions` | `service_tier: "flex-tier"` |
| `/costs` | line item prefixed `"flex \| "` |

The rate table is keyed by tier. If the usage side files traffic under
`flex-tier` while the cost side files rates under `flex`, the lookup **misses**,
and every flex request is priced at **$0.00** — real requests, real tokens, zero
dollars, no error anywhere.

`normalize_tier()` folds both onto one token: strip a trailing `-tier`, then map
`standard` / `auto` / empty onto `default`. An unrecognised tier is deliberately
**preserved rather than defaulted** — folding an unknown tier into `default`
would price it at the standard rate, which is wrong in a way that is equally
invisible.

The signature to test for is *non-zero requests against $0.00 derived cost*.
`FlexTrafficIsPricedTests` asserts it directly, and
`test_broken_normalisation_would_be_caught` injects the broken version to
confirm the guard itself works.

### 2. A blended per-token rate is wrong by multiples

Cached input costs roughly a tenth of uncached input. The cache-hit rate on
these keys is not stable — measured across the 30-day window it swings from
**30.2%** to **96%** between days.

So a single blended $/token (total dollars ÷ total tokens) is not a small
approximation. Worked example, using the corpus in `CachedRateSplitTests`:

| | tokens | dollars | rate |
|---|---:|---:|---:|
| uncached | 1,000,000 | $10.00 | $10.00 / 1M |
| cached | 9,000,000 | $9.00 | $1.00 / 1M |
| **blended** | 10,000,000 | $19.00 | **$1.90 / 1M** |

A key that spent 1,000,000 tokens at a *cache-light* 90/10 mix actually cost
`900k × $10/1M + 100k × $1/1M` = **$9.10**. The blended rate says
`1M × $1.90/1M` = **$1.90** — off by 4.8×. Invert the mix and the error inverts
with it.

Hence the four dimensions (`uncached`, `cached`, `cachewrite`, `output`) are
kept distinct from the line-item parse all the way through to the sum, and
`test_blended_rate_answer_is_rejected` pins the blended answer as *wrong* rather
than merely asserting the right one.

## Key discovery

Keys are found by walking `/v1/organization/projects` (archived included — a key
that ran traffic in a since-archived project still spent real money) and then
`/v1/organization/projects/{id}/api_keys`, matching **name**, not id:

- the key's own `name` == `claude`, **or**
- its owning `service_account` / `user` name == `claude`

both case-insensitive, both exact (so `claude-code` does not match).

Ids are deliberately not hardcoded. There are 26 keys today because there are 26
projects; the 27th project to be created will get a 27th key, and a hardcoded
list would under-report from that day on without anything failing.

A project whose keys cannot be enumerated (a 403 on an older project outside the
admin key's grants) is skipped rather than fatal, so one inaccessible project
cannot take down an org-wide walk.

## Historical measurement: 2026-07-26 → 2026-08-24

Measured over 2026-07-26 → 2026-08-24, the `Claude`-key spend splits across two
projects, and the split is the interesting part:

| Day | Total | `Conversation [Production]` | `Claude` project |
|---|---:|---:|---:|
| 2026-08-18 (ordinary) | $2.52 | $0.33 | $2.19 |
| 2026-08-21 (spike) | **$837.01** | **$836.87** | $0.14 |

Over the full window: `Conversation [Production]` $1,018.70 (85%), `Claude`
$179.02 (15%).

Over that window `eng:qa-prompt` used `OPENAI_CLAUDE_API_KEY` → the standalone
`Claude` project, and `eng:qa-conversation` (merged 2026-08-20) used
`OPENAI_CONVERSATION_API_KEY` → a `Claude`-named key inside `Conversation
[Production]`, the only skill running *inference* through that key — its other consumers merely read stored
completions, which produces no inference tokens.

The workload shape corroborates the attribution: at the 2026-08-19 boundary the
cache-hit rate fell from ~94% to 30–73% and daily output tokens went from ~30k
to 16.2M. That is a new high-output eval workload, not a change in the existing
read traffic.

A current run shows this spend under `Evaluations [Production]`, where both QA skills authenticate ([ENG-1171](https://linear.app/aura-health/issue/ENG-1171)). Spend under a *product* project is a regression — find the misconfigured key.

**This is a dated snapshot, not an invariant.** It is recorded because the intuitive attribution ("the spike was prompt testing")
is wrong by two orders of magnitude. Re-run the by-project table rather than
quoting these figures — that is what the skill is for.

## Reconciliation

The derived total matched billed org totals within **3%** on the two days that
could be checked independently. The 2026-08-21 figure of **$837** — the spike
that moved per-user cost from a ~$0.06–0.09 baseline to $0.318 — reproduces
exactly on both a 30-day window and a `--date 2026-08-21` single-day run.
