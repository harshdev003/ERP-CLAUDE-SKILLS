---
name: investigate-elevenlabs-conversations
description: Monitor or review ElevenLabs Conversational AI conversations — live failure monitoring or historical error analysis. Use only when the user explicitly asks to monitor ElevenLabs, check conversation failures, or review ElevenLabs errors. Renamed from `investigate-11labs-conversations`, so also trigger on the old spelling: "investigate 11labs conversations", "monitor 11labs", "11labs conversations", "check 11labs errors". Do NOT use for a casual mention of ElevenLabs without an explicit monitor or review request.
metadata:
  version: "4.0.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/ElevenLabs                   # ElevenLabs API access (ELEVENLABS_API_KEY)
---

# ElevenLabs Conversations

## Invocation Guard

This workflow can run live monitors and external API calls. Treat direct slash-command
invocation of `investigate-elevenlabs-conversations` as explicit. Otherwise, before
checking env or running scripts, verify the user's latest message explicitly asks to
monitor or review ElevenLabs conversations. If not, stop and ask whether to run this
skill.

Analyze ElevenLabs conversations. Arguments: $ARGUMENTS

## Setup

Normalize `$ARGUMENTS` into a canonical command before doing anything else:

1. **Subcommand** — the intent is either `monitor` or `review-errors`. Match loosely:
   - "monitor", "watch", "live" → `monitor`
   - "review", "review errors", "errors", "report" → `review-errors`
   - If ambiguous, default to `review-errors`

2. **Time window** — extract any number + unit from the arguments:
   - "7 days", "7d", "last 7 days" → `--days 7`
   - "6 hours", "6h", "last 6 hours" → `--hours 6`
   - "last week" → `--days 7`, "last month" → `--days 30`
   - If no time specified, omit (script defaults to 48 hours)

3. **Agent ID** — if a string that looks like an agent ID is present (not a number, not a keyword), pass it as `--agent-id <value>`

4. **API key check** — before running the script, verify the key is available:

```sh
echo "${ELEVENLABS_API_KEY:+set}"
```

If the output is empty, stop immediately and tell the user:

> `ELEVENLABS_API_KEY` is not set. Run the following in your terminal, then retry:
>
> ```sh
> export ELEVENLABS_API_KEY=your_key_here
> ```

Once the key is confirmed, run the fetch script at `scripts/fetch.js` relative to this skill's directory:

```sh
node scripts/fetch.js <subcommand> [--agent-id <id>] [--hours N] [--days N]
```

If the script exits with an error, report it clearly and stop.

The script outputs a JSON object with:

- `summary` — totals (total, failed, succeeded, unknown)
- `agentFilter` — the agent ID used, or null if none
- `conversations` — all conversations in the window (light data)
- `failedDetails` — full detail objects for each failed conversation

---

## monitor

Present a real-time snapshot of the last 10 minutes.

**Header**: "ElevenLabs Monitor — [timestamp]" (include agent ID if filtered)

**At-a-glance status**:

- Total conversations in window
- Pass rate (succeeded / total as %)
- Number of failures — flag as critical if > 0

**If there are failures**, for each failed conversation:

- Conversation ID, agent ID, start time, duration
- Error diagnosis: inspect `detail.analysis`, `detail.metadata`, and the last few `detail.transcript` turns to determine *what went wrong* — be specific (e.g. "Agent timed out after 45s with no user response", "LLM returned an error on turn 3", "Connection dropped mid-session")
- Any metadata fields that provide context (user ID, session type, etc.)

**Assessment**: brief judgement — is this a spike, isolated incident, or within normal range based on the failure count and rate?

---

## review-errors

Produce a structured error report for the requested time window.

### Summary

- Period and agent filter (if any), total conversations, pass rate
- Total failures, unique agents affected
- Avg duration of failed conversations vs all conversations
- Number of users affected (from `metadata.user_id` if present)

### Error Pattern Analysis

Group failed conversations by root cause. For each group:

- **Error type** — inferred from `detail.analysis`, `detail.metadata`, transcript content, or HTTP status codes
- **Count** and **% of failures**
- **Avg duration** — does the error happen early (setup failure) or late (mid-session drop)?
- **Representative example** — one conversation ID with a concise description of what happened
- **Trend signal** — are occurrences clustered in time, or spread evenly?

### Impact

- Which agents are most affected (if multiple agents in results)
- User impact: number of affected users, any users with repeated failures
- Severity assessment: are these hard errors (conversation never started), mid-session drops, or soft failures (completed but marked unsuccessful)?

### Recommendations

Based on the patterns, suggest 2–4 specific things worth investigating or fixing. Be concrete — reference the error types and affected agents by name.

---

Keep the analysis tight. Lead with the most severe issues. If there are no failures, say so clearly and note the pass rate.

---

## Output

After completing the analysis, always write the full report as a markdown file to:

```
./reports/investigate-elevenlabs-conversations/{action}-{datetime}.md
```

Where:
- `{action}` is the subcommand used: `monitor` or `review-errors`
- `{datetime}` is the current date and time in `YYYY-MM-DD-HHmm` format (e.g. `2026-04-03-1430`)

Use the Write tool to create the file. After writing, tell the user the path.
