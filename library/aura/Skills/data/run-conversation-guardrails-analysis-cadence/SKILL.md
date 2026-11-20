---
name: run-conversation-guardrails-analysis-cadence
description: >
  Orchestrator for the recurring conversation-guardrail triage cadence ("Cadence Group 6").
  Sweeps #guardrails-conversation over a trailing 7-day window for k8bot "Conversation guardrail
  triggered" alerts with no thread reply, hands each to `eng:investigate-conversation-guardrail`
  (whole-conversation read, crisis-reply check, CS draft for review, threaded reply), and ends
  with a summary that counts crisis-reply-check failures. Trigger on "run the conversation
  guardrails cadence", "run group 6 analysis", "run cadence group 6 analysis", "sweep the
  conversation guardrails", "conversation guardrails sweep", or the standing Cadence Group 6
  sweep prompt. Also the last group a `data:run-all-analysis-cadence` tick sweeps. Do NOT
  trigger on a bare "run group 6" (require "analysis"), on "guardrails sweep" or "run group 5
  analysis" (memory-ingestion cadence, `data:run-guardrails-analysis-cadence`), or to analyse
  one alert (`eng:investigate-conversation-guardrail`).

metadata:
  version: "1.0.2"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Tools/Slack          # channel ID + bot-membership for the sweep read and the reviewer reply
    - Compliance/Privacy   # UID redaction + never-echo-conversation rules on every Slack reply
---

# Run Conversation Guardrails Analysis Cadence

## Goal

Keep the conversation-guardrail queue drained: on each sweep, find k8bot alerts in
`#guardrails-conversation` that nobody has replied to, and get each one triaged and answered on
its thread — without ever sending anything to a user.

This skill is the **orchestrator** ("Cadence Group 6"). It owns the sweep and the dedup, not a
schedule: one invocation is one sweep, and repeat cadence is the operator's (normally a tick of
[`data:run-all-analysis-cadence`](../run-all-analysis-cadence/SKILL.md), which runs this group
last). The per-alert reasoning — the whole-conversation fetch, the classification, the
crisis-reply check, the outreach level, the CS draft and the reviewer tag — belongs to
[`eng:investigate-conversation-guardrail`](../../../eng/skills/investigate-conversation-guardrail/SKILL.md).
Invoke it via the Skill tool; never re-implement it here.

Group 5 ([`data:run-guardrails-analysis-cadence`](../run-guardrails-analysis-cadence/SKILL.md))
is the **memory-ingestion** guardrail cadence — a different channel, a different alert and a
different per-alert skill. The two never share an alert.

## Inputs

- **None required.** Defaults: channel `C0C7HEZ7CEA` (`#guardrails-conversation`), trailing 7-day window.
- Optional: a different trailing window (`--days`).

## Standing policy

These govern the **loop** only — never the per-alert reasoning:

- **Draft and check policy belong to the composed skill — not restated or overridden here.** On
  active risk it always produces a draft unless its documented resources-in-place downgrade
  applies, and it always runs the crisis-reply check. A missing name or country never withholds
  a draft. This orchestrator adds no rule that suppresses a draft, a check line or a page.
- **The skill never sends.** Every CS email is a draft for a human reviewer.
- **Never delete Slack messages.** To redo a reply, post a fresh one that supersedes it and tell
  the operator which ts to remove by hand.
- **Unattended auto-post is pre-authorized** for this sweep, the same autonomy the other
  analysis groups have: the composed skill posts its reply without a draft-and-confirm gate.

## Workflow

### Step 1 — Sweep for unanswered alerts (deterministic)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/run-conversation-guardrails-analysis-cadence/scripts/sweep_unanalyzed.py --days 7
```

Prints `{"channel", "window_days", "total_messages", "alerts", "unanalyzed": [{"ts", "user",
"transaction", "conversation", "permalink_ts"}, …]}` — only alerts with `reply_count == 0`. The
dedup is Slack thread state; there is no local watermark.

**Prerequisites.** `SLACK_BOT_TOKEN`, with the `aura_ai` bot a member of the channel (the script
reads `conversations.history` directly, which needs cursor pagination the Slack MCP does not
give). `#guardrails-conversation` is a **private** channel, restricted to the reviewers who
action these alerts, so the bot token also needs the private-channel scopes **`groups:history`**
(read history and threads) and **`groups:read`** — the `channels:*` scopes cover public channels
only. A missing scope returns `missing_scope`; a missing membership returns `not_in_channel`.
Either way stop and ask a human to fix it (scope: reinstall the `aura-ai` app; membership:
`/invite @aura_ai` to `#guardrails-conversation`).

**Empty `unanalyzed`:** report "N alerts in the last 7 days, all replied, 0 unanalyzed" and stop.

### Step 2 — Triage each alert (delegate, one at a time)

For each entry, invoke **`eng:investigate-conversation-guardrail`** in **Mode A** with the
alert's permalink (`https://auramobile.slack.com/archives/C0C7HEZ7CEA/<permalink_ts>`). Mode A
is required here: it is the mode that posts on the thread, and the mode that runs the composed
skill's pre-post thread re-check (Mode B has no Slack anchor and only prints). Run them
sequentially: each reads a whole conversation, and a parallel fan-out makes one failure hard to
attribute. An entry whose `conversation` or `user` is null could not be parsed — Mode A will
report it; do not guess the ids.

Do not second-guess its classification or its crisis-reply check. This skill's job is that
every unanswered alert gets fed to it and a reply lands.

**The sweep snapshot is not a lock.** Minutes pass between Step 1 and the post (a whole
conversation is read in between), and two ticks can overlap. What prevents a double post — two
drafts, two pages, the account email twice on one crisis alert — is the composed skill's
**pre-post re-check** (its Step 6 re-reads the thread and skips when a reply now exists). When
it reports "already answered — skipped", count that entry as answered elsewhere, not as a
failure.

### Step 3 — Verify

After each call, confirm a reply now exists on the thread (a re-sweep shows `reply_count > 0`,
so it is not reprocessed). If the composed skill could not post (Slack error), leave the thread
unanswered so the next tick retries it, and list it under failures in the summary.

### Step 4 — Summarize

One readout:

- **Counts:** alerts in window → already answered → unanswered → replied → failed.
- **Per alert** (when few): UID (first 8 + `…`) · outcome (active risk + category / non-crisis
  guardrail / false positive / not analysable) · crisis-reply check (a) / (b) · draft produced
  or level 3 (no email) · level 1 (high priority), 2 (daily review) or 3.
- **Crisis-reply check FAILs — always a separate line, even when zero:**
  `Crisis-reply check: <n> FAIL of <m> active-risk conversations — (a) <x>, (b) <y>`, followed by
  the redacted UIDs and thread links of each FAIL. A FAIL is a product-safety finding: it means
  someone at risk did not get a way to reach a human, or was dropped. It must reach the operator
  here, not only in a thread.
- **Not analysable:** list each, with "needs a human look".
- **High-priority tags:** list each thread tagged level 1 (HIGH PRIORITY).

## Edge cases

- **Bot not in channel / missing `groups:history` / `SLACK_BOT_TOKEN` unset** → ask a human to
  invite `@aura_ai` or add the scope, then retry.
- **Session restarted / two sweeps overlap** → nothing to restore; dedup is thread state, so one
  catch-up sweep covers both.
- **A conversation with no transcript anywhere** → the composed skill posts its
  "not analysable — needs a human look" reply. That reply closes the thread for dedup, so it must
  carry the hand-off itself and never read as an all-clear; also surface it in the summary — a
  reply in the channel is not the same as the operator having seen it. Several in one run is a
  storage or access problem to escalate, not alerts to triage one by one.
- **Alert older than the window** with no reply → out of scope for the tick; widen `--days` for a
  catch-up sweep.

## Privacy (per `Compliance/Privacy`)

UIDs redacted to first 8 + `…` everywhere in Slack and in the summary. Nothing a person said in a
conversation is quoted, paraphrased or summarised — turn numbers and categorical labels only.
The account email appears only on an active-risk draft reply, per the composed skill.

## References

- [`scripts/sweep_unanalyzed.py`](scripts/sweep_unanalyzed.py) — trailing-window sweep for `reply_count == 0` alerts (adapted from Group 5's).
- Composed skill: [`eng:investigate-conversation-guardrail`](../../../eng/skills/investigate-conversation-guardrail/SKILL.md).
- [PROD-7238](https://linear.app/aura-health/issue/PROD-7238); alert source [AuraHealth/IntelligenceService#569](https://github.com/AuraHealth/IntelligenceService/pull/569).
- `Tools/Slack` in AI Knowledge — channel directory, bot membership, post format and footer.
