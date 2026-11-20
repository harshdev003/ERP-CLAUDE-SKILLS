---
name: monitor-coach-replies
description: >
  Daily anomaly check on Aurie coach replies, as a routine: yesterday against the median of the 7 days
  before, chat and voice apart. Output-token median and p90, the share of replies with an emoji,
  incomplete max_output_tokens responses and double failures. Lists IntelligenceService releases and
  coach prompt changes in the window, so a step we caused stands apart from chat-latest drift. Counts
  only; posts to Slack only when something is anomalous, plus a weekly all-clear. Trigger on "check the
  coach replies", "are coach replies longer", "did emoji usage jump", "any anomaly in the replies",
  "monitor coach reply length", "run the coach reply check", or a tick of its daily routine. Do NOT use
  to read a conversation (eng:investigate-openai-trace), for guardrail alerts
  (eng:investigate-conversation-guardrail), or for message drops (eng:monitor-response-message-drops).
metadata:
  version: "1.2.1"
knowledge:
  reads:
    - Guide/Index
    - Products/Aurie          # what the coach is
    - Tools/BigQuery          # llm.llm_call
    - Tools/GCP               # GCP_KEY, Cloud Logging
    - Tools/Slack             # post format and footer
    - Compliance/Privacy      # counts only, never a reply or a uid
---

# monitor-coach-replies

## Goal

Catch a change in how the Aurie coach replies the day after it happens, and say whether we caused it.
The coach runs on OpenAI's `chat-latest`, which can't be pinned, so the model can change under us. On
2026-10-07 the emoji share tripled overnight with no change of ours, and on 2026-11-03 replies grew
about 15% longer. Both were found by hand, days later.

**Counts only.** Every number is an aggregate: a median, a share or a count. No reply text, prompt or
uid is read into the output, the history file or Slack.

Sources, queries, the empty-reply gap and the history the thresholds were checked against:
[`references/queries.md`](references/queries.md). Thresholds:
[`references/thresholds.json`](references/thresholds.json) (tunable).

```bash
S="python3 ${CLAUDE_PLUGIN_ROOT}/skills/monitor-coach-replies/scripts/monitor.py"
```

## Step 1 — Collect

```bash
$S collect                      # yesterday (UTC) and the 7 days before it
$S collect --date 2026-11-03    # any day
```

It fills `~/.claude/monitor-coach-replies/history.json` (mode 600; one entry per day, counts only) and
reads only the days missing from it (`--refresh` re-reads them):

- **Length and emoji:** BigQuery `aura-main.llm.llm_call`, one query each for all missing days. The emoji
  share counts one row per turn (`emojiCount > 0`, stored since 2026-09-25).
- **Incomplete, double-failure and empty replies:** one Cloud Logging count per day and signal.
- **Emoji fallback:** for a day BigQuery has no `emojiCount` for, IntelligenceService Postgres, only when
  `INTELLIGENCE_DB_URL` (read-only) is set. Otherwise that day's emoji is not measured, never 0.

## Step 2 — Check

```bash
$S check                        # print the verdict and the report
$S check --post                 # also post to Slack when something is anomalous
$S check --post --weekly        # Mondays: post an all-clear when the week had no anomaly
```

A metric is flagged when, against the median of the 7 days before:

| Metric | Flag |
|---|---|
| output tokens median or p90, chat or voice | ±12% |
| replies with emoji | ±10 points |
| incomplete responses | 3× and at least 20 |
| double failures | any |
| empty completed replies | 3× and at least 10 |

A day with fewer than 500 replies in a mode, or fewer than 4 baseline days, is "not measured" for that
metric, never normal. A step keeps flagging until the 7-day median catches up, so a flag the day before
also carried is marked *continuing*: listed, but not posted again. A double failure is always new. The
report lists IntelligenceService releases (publish time, the flagged day and the day before) and
coach-prompt commits in the window. When there are none, it says the step is likely `chat-latest` drift.

The post goes to `C07EX6CL43A` by default (`--channel`, or `MONITOR_COACH_REPLIES_CHANNEL`), with the
standard footer. Nothing is posted on a normal day, except the weekly all-clear.

## Step 3 — Act on a flag

- **A step with our change in the window:** tell the owner of that change (the IntelligenceService owner
  for a prompt or release), with the numbers.
- **A step with no change of ours:** likely model drift. Raise it with the IntelligenceService owner, who
  decides whether the prompt should compensate.
- **Double failures and empty replies:** each double failure is a user who saw a failed reply. Link the count
  to the incomplete-response card, if one is open.

## Running it as a routine

One run a day, after midnight UTC: `collect`, then `check --post`, with `--weekly` on Mondays. The routine
lives in `~/.claude/scheduled-tasks/` with the other routines, and it says how to rebuild its environment
if that folder is wiped.
