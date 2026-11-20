---
name: report-anthropic-usage
description: Report per-user Claude usage from `aura-main.anthropic_usage` — who used how many tokens, what that usage is worth at API list price, and whether each person's seat tier (standard vs premium) matches what they actually use. Use when someone asks "who is using Claude the most", "report our Claude usage", "is the premium seat worth it", "should we upgrade or downgrade seats", "Claude usage per person this month", "how much Claude did we use last week", "show Claude usage trend", or "what is our Claude usage by user". Reads only — the loader is `eng:harvest-anthropic-usage`. Do NOT use for OpenAI API cost (`eng:report-openai-costs`) or Aurie per-user cost (`eng:report-aurie-daily-costs`).
metadata:
  version: "1.3.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
---

# Report Anthropic (claude.ai) usage

Read-side of `aura-main.anthropic_usage`. Answers who is using Claude, how
heavily, and whether their seat tier fits.

## The two dollar figures, and why they differ

Every mode prints both. Conflating them produces confident nonsense.

| Figure | What it is | Use it for |
|---|---|---|
| **API value** | The same usage priced at public API list rates. Derived. | Comparing a person's usage against their seat price. |
| **Billed** | Real overage dollars from the export. | Reconciling to the invoice. |

On a seat-based plan most people bill **\$0.00** while using billions of tokens,
because their usage sits inside the seat allowance. So billed dollars rank
"who overflowed their personal cap", not "who uses Claude". API value is the
ranking that answers the second question.

API value is **not a cash saving**. It is the counterfactual price of the same
work on the API. Say that when quoting it.

## Run it

```bash
PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3
S=${CLAUDE_PLUGIN_ROOT}/skills/report-anthropic-usage/scripts

# org + per-user totals, last 30 days
$PY $S/report.py summary

# a specific window
$PY $S/report.py --since 2026-08-01 --through 2026-08-31 summary

# seat tier vs actual usage — the upgrade/downgrade call
$PY $S/report.py seats

# time series
$PY $S/report.py trend --grain week
$PY $S/report.py --days 90 trend --grain month

# one person, split by product and model
$PY $S/report.py user <redacted-email>
```

## Reading the `seats` mode

Each row shows the seat's monthly price next to the API-equivalent value of that
person's usage, projected to 30 days, and their ratio.

- **ratio above 1x** — the seat costs less than the same work would on the API.
- **"under-using a premium seat"** — a premium seat whose monthly value is below
  \$100. Worth a conversation, not an automatic downgrade: a low month may be
  leave, or a role that uses Claude in bursts.
- **"heavy — premium may pay off"** — a standard seat whose value already exceeds
  the premium price.

Seat tier comes from the latest `member_seat` snapshot, so it reflects today's
assignment, not the assignment during the window. Say so if the window is long
and someone changed tier inside it.

## Coverage is checked, not assumed

Before any total, the report counts how many days in the window actually have
harvested rows. If days are missing it prints `INCOMPLETE` and labels the totals
a lower bound.

This matters because an unharvested day and a genuinely idle day both contribute
zero. Without the check, a half-harvested month renders exactly like a quiet one
— the same failure shape the harvest skill guards on the write side.

## Scope limits, repeat them when sharing

- **Claude Code dominates.** Chat, Cowork, Claude Tag and Claude in Chrome are
  included but small.
- **API-key usage is absent** — it reports to the Console org, not this dataset.
- **Prices are hand-entered and dated.** `model_pricing.verified_on` says when a
  human last checked. A stale rate produces a plausible wrong number, never an
  error, so check the date before quoting API value externally.
- **A departed member** keeps their historical usage rows but drops out of the
  seat snapshot, and shows as `departed/none` in `summary`.

## This output names people

Rows are keyed by work email and rank colleagues by usage. That is the point —
it is a seat-allocation tool — but it makes the output **internal**. Do not
publish it to a shared artifact, a public channel, or anywhere outside the
people making the seat decision without asking first. Aggregate rows (the
`trend` mode, or the TOTAL line) carry no individual attribution and are safe to
share more widely.
