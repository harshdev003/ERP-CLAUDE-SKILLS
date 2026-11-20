---
name: monitor-memory-lookups
description: >
  Daily read-only check that Aurie's coach replies still run memory lookups. Per UTC day it divides
  IntelligenceService's findSimilarInMemory "Retrieving memory" log lines (GCP Cloud Logging) by
  coach-prompter calls in the llmCall ledger (BigQuery), and alerts when yesterday's share falls
  under half the median of the 7 days before it (on a day with at least 5,000 coach calls), or
  when such a day had no lookup. Counts only; a rise never alerts. Trigger on "check memory
  lookups", "are memory lookups dropping", "run the memory lookup monitor", "what share of coach
  replies look up memories", or the daily routine tick. Do NOT use to read one user's memories
  (eng:export-user-memory) or to change the memory prompts (eng:update-intelligence-prompt).
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/GCP            # GCP_KEY service account, Cloud Logging query shape
    - Tools/BigQuery       # the llmCall ledger, aura-main.llm.llm_call
---

# monitor-memory-lookups

You watch one number: the share of Aurie coach replies that look up the user's memories. On
2026-10-07 it fell from about 6% to 0.6% and nobody noticed for a month
([PROD-8674](https://linear.app/aura-health/issue/PROD-8674)). This check fires the first morning
a drop like that happens.

## What it reads

| Count | Source | Filter |
|---|---|---|
| Lookups | GCP Cloud Logging, project `firebase-auratech16`, through the bundled `gcloud_sa.py` (`GCP_KEY`) | `jsonPayload.component="findSimilarInMemory"`, `jsonPayload.message="Retrieving memory"`: one line per tool call |
| Coach calls | BigQuery `aura-main.llm.llm_call`, through the bundled `bq.py` (`GCP_KEY`) | `prompterName = 'coach-prompter'` |

Both are counted per UTC day over the last 8 full days. Nothing per user is read or printed.

The share reads slightly higher than IntelligenceService's own figure (0.29% vs 0.26% on
2026-11-18): the log line also counts chat and Quick Processing lookups and a second lookup in one
reply. The owner (BackendIntelligence) accepted it for a drop alert, since a real drop moves both.

**The log line is the contract.** If IntelligenceService renames the `Retrieving memory` message,
the `findSimilarInMemory` component or the tool, or stops logging it, the numerator silently
reads 0, which raises a false "no lookups" alert at best. Change `LOOKUP_FILTER` in
`scripts/monitor.py` in the same release. The same holds for the `coach-prompter` name in the
ledger. Aura chat moving onto `coach-prompter` (ENGBACK-3309) raises the denominator by 1-2%,
which does not trip the rule.

## Run it

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-memory-lookups/scripts/monitor.py
```

`--today YYYY-MM-DD` runs as of another UTC date (yesterday relative to it is the day judged), for
a backtest: `--today 2026-10-09` fires on the Oct 8 drop. `--json` prints the verdict.

Run it daily after 01:00 UTC, so yesterday's logs and ledger rows are complete.

## The rule

Judge **yesterday**:

- **alert** when it had at least 5,000 coach calls and **no lookup at all**;
- **alert** when it had at least 5,000 coach calls and its share is **under half the median** of
  the 7 days before it (days with no coach calls are left out of the median);
- otherwise no alert. A rise never alerts, and a quiet day under 5,000 calls never alerts.

## Output and follow-up

| Exit | Line | Then |
|---|---|---|
| 0 | `ok <day>: <share> … prior 7-day median <m>` | Report that one line. No post. |
| 1 | `ALERT <reason>: <day>: …` | Raise a follow-up (below). |
| 3 | `UNMEASURED <day>: <why>` | A read failed, crashed or came back incomplete. Raise a `blocked` follow-up for the routine's owner; never report it as no alert or as a drop. |
| 2 | argparse error | Fix the call. |

On exit 1 or 3, raise one follow-up with `shared:raise-followup`. Both use `--kind blocked`,
which requires `--what`, `--why` and `--ask` and also comments on PROD-8674; a raise missing
one of them is refused and posts nothing. Counts only, no ids.

| Flag | Exit 1 (drop) | Exit 3 (unmeasured) |
|---|---|---|
| `--title` | `Coach memory lookups dropped on <day>` | `Memory lookup check could not measure <day>` |
| `--what` | the ALERT line | the UNMEASURED line |
| `--why` | `Coach replies stopped looking up memories, so Aurie answers without what the user told it before. The Oct 7 drop went unnoticed for a month.` | `Without this check a drop in memory lookups goes unnoticed.` |
| `--ask` | `BackendIntelligence: check whether findSimilarInMemory stopped being called (prompt, tool definition or model change).` | `Fix the failed read and re-run eng:monitor-memory-lookups.` |
| `--key` | `memory-lookups:<day>` | `memory-lookups:<day>:unmeasured` |

Both also pass `--kind blocked --for owner --card PROD-8674 --session <the routine's name>`.
