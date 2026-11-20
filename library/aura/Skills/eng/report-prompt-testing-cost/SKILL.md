---
name: report-prompt-testing-cost
description: >
  Daily DERIVED OpenAI cost of AINative eval and prompt-testing traffic — spend on the
  `Claude`-named service-account keys our QA skills use. Two skills dominate, in different
  projects: `eng:qa-conversation` ~85% and `eng:qa-prompt` ~15%, so a spike is usually the
  eval harness. The Costs API reports per project, never per key, so dollars are
  back-calculated from daily per-model, per-tier rates. Trigger on "prompt testing cost",
  "eval cost", "how much does qa-conversation cost", "cost of the Claude keys", "how much are
  we spending on prompt testing", "openai cost by api key", "which keys are spending", "is
  prompt testing inflating per-user cost", or "what caused the per-user cost spike". Do NOT
  use for org-wide or per-project spend (`eng:analyze-openai-costs`), one project charted over
  time (`eng:chart-openai-project-cost`), a daily all-org snapshot
  (`eng:report-openai-costs`), token counts with no dollars (`eng:analyze-openai-tokens`), or
  per-end-user attribution (`eng:analyze-openai-cost-per-user`).

metadata:
  version: "1.0.6"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Tools/OpenAI                 # admin endpoints, project/key inventory, rates
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Report prompt-testing cost

Reports what **Aura's own eval and prompt-testing traffic** costs per day, by
isolating the `Claude`-named OpenAI service-account API keys and pricing them.

## Why this skill exists

Aura's QA and prompt-engineering skills authenticate with a per-project
service-account key named `Claude` (26 of them today, one per OpenAI project,
created in two batches on 2026-07-15 and 2026-08-03 as part of the
`OPENAI_<PROJECT>_API_KEY` family). That spend is real but structurally
invisible:

- **It hides inside product projects.** The bulk of it runs in `Conversation
  [Production]`, not in an eval project. Every project-level report therefore
  shows it as product spend.
- **It is absent from the ledger.** The Intelligence Service `llmCall` ledger (BigQuery `llm.llm_call`)
  ledger only records product traffic, so `eng:analyze-openai-cost-per-user` has
  no row to attribute these calls to and spreads their dollars across the real
  users it *can* see. On **2026-08-21** that pushed **\$837** into the per-user
  cost store, moving reported per-user cost from a ~\$0.06–0.09 baseline to
  **\$0.318**.

No existing skill can separate the two, because the separating dimension — the
API key — is the one dimension OpenAI never attaches dollars to.

### Which skill is spending, and where it must land

Two skills authenticate with these keys, and both bill to **`Evaluations
[Production]`** via `OPENAI_EVALUATIONS_API_KEY`:

| Skill | What it runs |
|---|---|
| `eng:qa-conversation` | the rubric-scored conversation eval harness — the larger spender by roughly 6:1 |
| `eng:qa-prompt` | prompt-under-test inference + the self-critique scorer |

`Evaluations` is the sole member of `INTERNAL_PROJECT_BASE_NAMES` in
`eng:analyze-openai-cost-per-user`, so its dollars are excluded from per-user
attribution **and** from the attribution coverage ratio. Any other project is
walked instead: the dollars resolve to no user, inflate every real user's cost,
and count as uncovered against `store.py`'s 75% floor.

**So the by-project table is a correctness check, not just a breakdown.** Spend
appearing under a *product* project means a QA skill is authenticating with the
wrong key — treat it as a regression and find the key, not as a curiosity. The
skill sees it either way: every project, `Evaluations [Production]` included,
holds a `Claude`-named key.

The measurement that set this up — including why the intuitive attribution is
wrong by two orders of magnitude — is in
[`references/derivation.md`](references/derivation.md).

## Dollars here are DERIVED

**State this whenever you report a number from this skill.** OpenAI exposes cost
per project, never per API key. There is no endpoint that will ever return the
true dollar figure for one key, so this skill back-calculates it:

1. Derive a rate per `(model, service_tier, dimension)` for each day, from
   org-wide `/costs?group_by=line_item` ÷ org-wide `/usage/completions` tokens.
2. Apply those rates to the `Claude` keys' own token counts.

The result is a well-founded estimate, not an invoice line. It reconciled
against billed org totals within 3% on the two days that could be checked
independently. This is the same honesty posture as `eng:analyze-livekit-cost`
and `eng:analyze-cartesia-costs` — the script prints the caveat in its own
footer, and you should not strip it when summarising.

Full method, including the two failure modes that produce a plausible-but-wrong
report: [`references/derivation.md`](references/derivation.md). Read it before
changing anything in `scripts/cost.py`.

## Access path

`/v1/organization/*` is Admin-scoped and **has no MCP equivalent**, so this
skill is API-first by necessity (`/CLAUDE.md` → *API-first by necessity*). It
requires `OPENAI_ADMIN_API_KEY` — the org Admin key, not a project-scoped
`OPENAI_<PROJECT>_API_KEY`. A missing key is a hard fail with a pointer to the
token inventory; there is nothing to degrade to.

## Workflow

### Step 1 — resolve the window

Default is the trailing 30 days. Windows are UTC and half-open internally, but
the flags are the friendly form:

| Flag | Meaning |
|---|---|
| *(none)* | Last 30 days, including today's partial bucket |
| `--last N` | Trailing N days |
| `--date YYYY-MM-DD` | Exactly that UTC day |
| `--from A --to B` | A through B, **`--to` inclusive** |

If the user named a window in words ("yesterday", "last week", "August"),
translate it to a flag and say which one you used.

### Step 2 — run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-prompt-testing-cost/scripts/report.py --last 30
```

Add `--json` when another skill or a follow-up analysis consumes the output
rather than a human. A 30-day run takes ~50s — it walks the org cost buckets,
the org usage buckets, and then per-project usage for each project holding a
`Claude` key.

The script is fully deterministic (best-practice 9): key discovery, rate
derivation, costing, and rendering are all script-side. Nothing about the
numbers is left to the model.

### Step 3 — read the output honestly

The report has three tables — daily, by project, by model/tier — plus two
footnotes that are part of the result, not decoration:

- **Fallback note.** Cells too thin to price on their own day fall back to the
  window-wide rate for that model. A large count means the window is sparse and
  the daily shape is softer than it looks.
- **Unpriced warning (⚠️).** A `(model, tier, dimension)` that carried tokens
  but had no derivable rate on either basis contributes **\$0.00**, so the total
  is an undercount. Surface this if it appears — never report the total as
  complete while it is present.

### Step 4 — report

Lead with the total and the window, name the derivation, then the shape:

> Our own eval + prompt-testing traffic (the 26 `Claude` service-account keys)
> cost a **derived** \$1,197.74 over 2026-07-26 → 2026-08-24. 85% is
> `eng:qa-conversation` running inside `Conversation [Production]`, where it is
> indistinguishable from product traffic; `eng:qa-prompt` is the other 15% and
> sits in its own project. One day dominates: **2026-08-21 at \$837**, of which
> \$836.87 is the eval harness — the spike that moved per-user cost to \$0.318.

**Name the project split whenever you quote a total.** Attributing it all to
"prompt testing" points at `eng:qa-prompt`, which is usually the wrong skill by
two orders of magnitude.

Do not post to Slack unless the user asks. If they do, route through
`shared:post-to-slack` with `--footer eng:report-prompt-testing-cost` so the
version resolves itself — never hand-type the footer.

## What this skill does not do

- **It does not attribute to a person.** The keys are per-project service
  accounts, not per-engineer. "Who ran this test" is not answerable from here.
- **It does not mutate anything.** Read-only against three org endpoints.
- **It does not exclude this spend from other reports.** That is the follow-up
  below.

## Known follow-up (separate change)

`eng:qa-conversation` arguably belongs in `Evaluations [Production]`, which
`INTERNAL_PROJECT_BASE_NAMES` in `eng:analyze-openai-cost-per-user` already
excludes from per-user attribution. Moving it there would fix the per-user
contamination at the source rather than measuring it after the fact. That is a
separate change with its own card — this skill deliberately only measures.

## Files

| Path | Role |
|---|---|
| `scripts/report.py` | CLI, window resolution, collection, markdown/JSON rendering |
| `scripts/cost.py` | Pure rate derivation — tier normalisation, line-item parsing, `RateTable`, key matching. No I/O |
| `scripts/openai_admin.py` | The only place that opens a socket; retry + pagination |
| `references/derivation.md` | The method and its two silent failure modes |

Behaviour is pinned by [`tests/test_prompt_testing_cost.py`](../../../../tests/test_prompt_testing_cost.py)
(31 stdlib-unittest cases, transport stubbed at `openai_admin.request`, no
network). Run them after any change to `cost.py`:

```bash
python3 -m unittest discover -s tests -q
```
