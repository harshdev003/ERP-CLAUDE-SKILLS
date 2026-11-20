---
name: analyze-memory-overhead
description: >
  Compute the per-conversation overhead of the Aurie memory pipeline by joining OpenAI stored
  completions from the Memory [Production] and Conversation [Production] projects. Returns
  per-conversation_id counts of LLM calls + tool calls + invocations for both the conversation
  path and the memory path, plus aggregate ratios (median / p90 / max memory:conversation),
  over a chosen window (default: month-to-date). Trigger when the user says: 'memory
  overhead', 'how much memory cost per conversation', 'memory vs conversation ratio', 'how
  many memory LLM calls per turn', 'analyze memory overhead', 'memory tool calls per
  conversation', 'memory turn ratio', or asks how much the memory pipeline is firing relative
  to dialogue.

metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/OpenAI       # auth + "Reading stored completions" access pattern + project inventory
    - Tools/Slack        # channel directory + Post format footer spec for #ai-prompt-engineering post
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Aurie Memory Overhead Report

> **Ledger-first attribution (2026-07).** The call-count attribution reads the **IntelligenceService per-call LLM ledger** first (BigQuery `aura-main.llm.llm_call` — docs: <https://app.notion.com/p/aurahealth/LLM-Calls-3a675e23af208127b217e53af47524e8>), with automatic failover to the OpenAI stored-chat-completions walk. The ledger went live at **2026-07-23T03:25Z** (Intelligence Service Release 0.285.0): any window slice **before** that epoch always uses stored chat completions (the only source for pre-epoch data), and a window that straddles the epoch merges both slices (deduped on the OpenAI object id). The ledger records both Chat Completions **and** Responses API calls and has **no retention limit**; the completions fallback is **chat-API-only and cannot see Responses-API traffic**. The script prints an `Attribution source:` line in the summary saying which source(s) each run used.

> **Migrated off Braintrust 2026-06-05** ([PROD-2465](https://linear.app/aura-health/issue/PROD-2465), umbrella [PROD-2456](https://linear.app/aura-health/issue/PROD-2456)). Reads OpenAI stored chat-completions from `Memory [Production]` (`proj_f0PpUSyYnJNifUZj44FrjUhr`) and `Conversation [Production]` (`proj_esFyd4eq2VIAjKUn69X1ZFfk`) instead of Braintrust BTQL. **Counting model changed**: each stored completion IS one LLM call (no span hierarchy), so `llm_calls` = completion count and `turns`/`invocations` = distinct `metadata.dialogueId` count per conversation; `tool_calls` = the difference. **Two things didn't port**: (1) BTQL's server-side `GROUP BY` — the script now walks every completion and aggregates client-side, so it's slower (see the revised runtime table); (2) the known-incident **regression-alert scan** — it looked for *errored* Memory spans, and OpenAI only stores *successful* completions, so that BT-native monitoring was dropped.

> ⚠️ **Hard rule.** Never use `metadata.transaction` to group, join, or aggregate. It is a **thread/session-level identifier** and a single value can span multiple unrelated user conversations even within one project (empirically: one `transaction` carried 5 different users' conversations on 2026-05-13). Grouping by it fuses unrelated work and produces wrong aggregates. Always group/join on `metadata.conversationId` (per conversation) or `metadata.dialogueId` (per turn). This rule applies to **every** Aurie inference-log skill. Full rationale: `Tools/OpenAI` / `Tools/Braintrust` in AI Knowledge.

## Step 0 — Load context and check prerequisites

Before running, **load the `Tools/OpenAI` page from AI Knowledge** — it carries the stored-completions access pattern, the metadata field reference (the `transaction` warning above, the `metadata.name` = prompter convention), and the project inventory. Use `retrieve-knowledge` with path `Tools/OpenAI`.

Then confirm the OpenAI Admin API key:

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "OK: key set" || echo "MISSING"
```

If missing, walk the user through adding `OPENAI_ADMIN_API_KEY` (`sk-admin-...`, Owner role) to `~/.claude/settings.json` under the `env` block via the `update-config` skill, then have them restart Claude Code. Key creation: <https://platform.openai.com/settings/organization/admin-keys>. Same key the sibling cost skills use. Do not proceed until a key is set.

Also check the Intelligence Service ledger token (primary attribution source for post-epoch windows):

```bash
test -n "$GCP_KEY" && echo "OK: GCP_KEY set" || echo "MISSING (script will warn and fall back to stored completions)"
```

`GCP_KEY` (the skills service account) reads the ledger from BigQuery. If it's missing or the ledger read fails, the script does **not** crash — it prints a warning to stderr and falls back to the stored-completions walk for the post-epoch slice. Remember the fallback is chat-API-only: any Responses-API traffic in that slice will be invisible.

Other prereq (usually already present): `python3` with `requests`.

## Pipelines this skill measures

Aurie's runtime has two independent LLM pipelines per conversation:

- **Conversation** — the user-facing dialogue, logged to OpenAI's `Conversation [Production]` project under `metadata.name = 'coach-prompter'`. One user turn = one `dialogueId`, which carries one or more stored completions (more than one when the model invokes tools mid-turn). The project also hosts sibling prompters (`quick-processing-prompter`, `insight-card-prompter`); the skill filters to `coach-prompter` (server-side) so those don't pollute the "conversation turn" count.
- **Memory** — the every-few-turns memory-prompter, logged to OpenAI's `Memory [Production]` project under `metadata.name = 'memory-prompter'`. Each invocation = one `dialogueId`, carrying an agentic loop of stored completions (one per assistant↔tool round-trip).

## Known cross-project ID mismatch — read before interpreting

The Memory pipeline today stamps an **internal** `conversationId` and `dialogueId` (generated inside the memory-prompter's own agentic loop) rather than the originating user-facing IDs from the Conversation project. As of the May 2026 measurement (the run that produced this skill update):

| Key                       | Memory distinct | Conv distinct | Intersection |
| ------------------------- | --------------: | ------------: | -----------: |
| `metadata.conversationId` |          1,000+ |         2,213 |            0 |
| `metadata.dialogueId`     |          1,000+ |         2,000 |          277 |

Until the Aura backend is fixed to propagate the user-facing `conversationId` and `dialogueId` through the memory pipeline, this report shows near-zero intersection. The script prints a **loud `!!!!` banner** in the summary when the paired/total ratio falls below 50%, and counts unmatched Memory cids under `orphan memory cids`. Track the backend propagation fix on Linear (ENGBACK-1034). **Do not fall back to `metadata.transaction` as a workaround** — see the warning above; that field is shared across unrelated conversations and grouping by it produces wrong answers.

## When to run

- Before sizing memory's contribution inside the per-user / per-subscription OpenAI cost reports ([`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md), [`eng:analyze-openai-cost-per-subscription-state`](../analyze-openai-cost-per-subscription-state/SKILL.md)).
- After a memory-prompt change ships, to compare invocation cadence and tool-call density against a baseline run.
- When triaging a "memory feels too noisy" suspicion — the top-by-ratio list shows the worst offenders.

## Step 1 — Pick a window

Ask the user. Default to **month-to-date** — that's what most cost-comparison conversations want, and it lines up with the OpenAI cost skills' default. Other presets:

- **Month-to-date** (`--mtd`, default) — from the 1st of this calendar month to now (UTC).
- **Last 7 days** (`--days 7`) — fastest, freshest, smaller result set.
- **Last 30 days** (`--days 30`) — wider tail, better p99 / max numbers.
- **Last 24 hours** (`--days 1`) — sanity check after a deploy.
- **Custom range** — pass explicit ISO `--since` / `--until`.

**Expected runtime.** OpenAI's list endpoint has **no server-side aggregation** — the script walks every coach-prompter + memory-prompter completion in the window (100/page) and counts client-side. Conversation [Production] runs ~240 completions/hour. Rough budgets:

| Window | Completions walked (conv + mem) | Wall-clock |
|---|---|---|
| 3h     | ~700 + ~150 (~9 pages) | ~10–20 s |
| 24h    | ~6k + ~1k (~70 pages) | ~1–2 min |
| 7 days | ~40k + ~7k (~470 pages) | ~5–10 min |
| MTD (12d) | ~70k + ~12k (~820 pages) | ~10–20 min |
| 30 days | ~175k + ~30k (~2,050 pages) | ~30–45 min |

This is materially slower than the old BTQL `GROUP BY` (which aggregated server-side in seconds). **If you only need the headline number (totals + ratio percentiles), use `--days 7`** — distributions stabilise fast. Reach for MTD only when you specifically need to line up with the OpenAI cost reports' MTD default, and expect to wait.

**The runtime table above applies to the stored-completions walk only.** Window slices at or after the ledger epoch (2026-07-23T03:25Z) use the Intelligence Service LLM ledger instead — server-side `prompterName` + `project` filters at 1,000 rows/page — which is far faster. As the window moves fully past the epoch, expect run times to drop well below the table's estimates (unless the ledger is unavailable and the script falls back).

## Step 2 — Run the report

From the repo root:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-memory-overhead/scripts/report.py \
  --mtd \
  --json /tmp/memory_overhead.json \
  --csv /tmp/memory_overhead.csv
```

For an explicit range:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-memory-overhead/scripts/report.py \
  --since 2026-04-01T00:00:00Z --until 2026-05-01T00:00:00Z \
  --json /tmp/memory_overhead.json
```

The script walks both projects once each, retrying with backoff on 429/5xx. Post-epoch slices come from the Intelligence Service ledger (server-side `prompterName` + `project` filters, time-paged at 1,000 rows/page); pre-epoch slices and ledger-failure fallbacks use the stored-completions walk (server-side `metadata[name]` filter, client-side time-window). Both feed the same per-`conversationId` aggregation, deduped on the OpenAI object id at the epoch boundary. The walk is serial by design — don't increase concurrency.

Pass `--per-dialogue path.csv` to also emit a per-`dialogueId` CSV. This reuses the rows already collected during the main walk (no second pass). Same cross-project caveat: the dialogueIds Memory stamps don't fully match the Conversation side, so the per-turn CSV is partial. Useful as evidence in the backend ticket (ENGBACK-1034).

## Step 3 — Show the report inline

Print the script's stdout (the summary block + the top-N table) directly in the response. Lead with a one-line headline from the totals, e.g.:

> Across the window, memory makes **0.17x** as many LLM calls as conversation does (106 vs 642), with a median per-conversation ratio of **0.11x** and a p99 of **2.0x**.

Don't sort by raw `memory_llm_calls` for the top-N — heavy users dominate. Default sort is `memory_llm_per_conv_llm` (ratio), which surfaces *outliers* relative to conversation length. Switch to `--sort memory_tool_calls` if the user explicitly asks "which conversations cost the most tool calls."

> **Dropped in the OpenAI migration:** the Braintrust-era **known-incident regression-alert scan** is gone. It scanned the Memory project for *errored* spans matching named incident signatures — but OpenAI only stores *successful* completions (`store: true` fires on success), so errored calls have no stored-completions equivalent. That monitoring is BT-native; if it's revived it belongs in a separate BT-only (or GCP-logs-based) monitoring skill. See [PROD-2465](https://linear.app/aura-health/issue/PROD-2465).

## Step 4 — Ask what to do next

ALWAYS use `AskUserQuestion` to offer routing. Don't auto-route. Options:

1. **Drill into the worst offenders** — hand each top-N `conversation_id` to [`data:analyze-user-conversations`](../../../data/skills/analyze-user-conversations/SKILL.md) with `--conversation-id`. That returns the actual transcript + rating + deliberation gap so you can see *why* the memory pipeline was so chatty for that conversation.
2. **Save the CSV** to `outputs/` (Cowork) or a user-supplied path. Useful when feeding the numbers into a sheet for further pivots.
3. **Post a summary to Slack** — `#ai-prompt-engineering` is the right channel for memory-prompt comparisons. Format as the headline + the totals block + the ratio percentiles. Skip the per-cid top-N (too noisy for Slack). Requires Slack MCP. End the post with the canonical `Tools/Slack` footer — generate it deterministically with `slack_post.py footer --skill eng:analyze-memory-overhead` (via `shared:post-to-slack`; it stamps `:<version>` from `metadata.version` automatically) and paste the output verbatim as the last line: `_Generated by `eng:analyze-memory-overhead:<version>` · <YYYY-MM-DD>_`. Don't hand-type the footer — a model-typed footer drops the version.
4. **Nothing, just show it** — stop here.

## What's in each metric (read me before interpreting)

| Metric | Definition | Caveat |
|---|---|---|
| `conversation_turns` | Count of distinct `metadata.dialogueId` values for this cid in Conversation [Production], filtered to `metadata.name='coach-prompter'`. | One dialogueId per user turn. Excludes failed/cancelled turns that never stored a completion. Excludes `quick-processing-prompter` and `insight-card-prompter` (filtered out by `metadata.name`). |
| `conversation_llm_calls` | Count of stored completions for this cid under `metadata.name='coach-prompter'`. | Each completion is exactly one LLM call. The server-side `metadata[name]` filter isolates coach-prompter, so QP/insight-card calls and any server-side classifier calls never enter the count. |
| `conversation_tool_calls` | `max(conversation_llm_calls - conversation_turns, 0)` | A turn with tool calls stores >1 completion under one dialogueId, so `llm_calls - turns` is the tool-call count. Exact when each completion makes ≤1 tool call (`parallel_tool_calls` is off); treat as a floor otherwise. |
| `memory_invocations` | Count of distinct `metadata.dialogueId` values in Memory [Production] (`metadata.name='memory-prompter'`). | One dialogueId per "every-N-turns" memory firing; the agentic loop reuses it across its completions. |
| `memory_llm_calls` | Count of stored completions in Memory [Production] for this cid. | Each completion is one LLM call. |
| `memory_tool_calls` | `max(memory_llm_calls - memory_invocations, 0)` | Exact for memory — current code sets `parallel_tool_calls: false` and runs the agent loop one tool at a time, so `llm_calls = invocations + tool_calls` holds. |
| `memory_llm_per_conv_llm` | `memory_llm_calls / conversation_llm_calls`. None if conversation side is empty. | The headline LLM ratio. p99 / max are where to look for prompt regressions. |
| `memory_tool_per_conv_tool` | `memory_tool_calls / conversation_tool_calls`. None if conversation tool calls = 0. | The tool-call analog. Tends to be the **most skewed** ratio because conversation often makes 0–1 tool calls while one stuck memory agent can fire 10+. Watch `mean` vs `median` and `stdev` — large gap = runaway behaviour on the tail. |
| `memory_per_5_turns` | `5 * memory_invocations / conversation_turns`. None if turn count is 0. | Sanity check on the "every 5 turns" cadence. Expected median near 1.0; persistent drift means the trigger logic changed. |

The summary block also prints two **totals-based** averages that don't need a per-conversation distribution:

- `conv: LLM/turn  tool/turn` — average LLM and tool calls per user turn across the window. Useful baseline.
- `memory: LLM/invocation  tool/invocation` — average LLM and tool calls per memory firing. **This is the number to watch if you're worried about runaway tool-calling inside the memory agent.** A value materially > 1 means the memory prompt is regularly chaining multiple tool calls per firing.

Every per-conversation distribution reports: `n / min / mean / median / p90 / p99 / max / stdev`.

## Edge cases

- **The stored-completions fallback resolves nothing for most projects (measured 2026-08-04).** What matters is whether a project's completions are **stored and listable**, not which API it calls — `Feed` still spends daily but has had no stored completion since **2026-07-23**. On a post-2026-07-23 window the ledger is effectively the only attribution source: every production project except `Conversation` has nothing stored, and `Conversation` itself is only **partially** covered — the `aiResponseAPI` A/B (since 2026-07-31) serves its treatment arm on the Responses API, so one conversation can span both surfaces. If the ledger is unreachable (or `GCP_KEY` is unset) this run does not degrade gracefully: dollars still come from the Costs API, so the report looks plausible while attribution collapses into the untagged bucket. **Treat a post-2026-07-23 run whose ledger call failed as unusable, not degraded** — re-run it rather than reading the numbers. Canonical per-project detail: `Tools/OpenAI` → "Responses API migration — reading stored data"; don't restate it here. [ENG-1049](https://linear.app/aura-health/issue/ENG-1049)
- **A post-2026-07-23 ledger outage now fails closed (exit 2) — [ENG-1054](https://linear.app/aura-health/issue/ENG-1054).** The caveat above says such a run is unusable rather than degraded; this is the code that enforces it, rather than a stderr warning that scrolls past. Pass `--allow-degraded-attribution` (or set `AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1`) to accept the degraded attribution deliberately. **Unaffected:** pre-epoch slices (the ledger has no rows before 2026-07-23T03:25Z, so stored completions are the correct source), projects the ledger doesn't serve, and the roll-out-ramp union — none of those is a ledger *failure*.

- **Orphan memory cids — the cross-project mismatch canary.** Memory cids that don't appear in the Conversation set are counted under `orphan memory cids` and the conversation-side fields stay at 0. Today this number dominates the report — the memory-prompter stamps an internal `conversationId` instead of the user-facing one (see "Known cross-project ID mismatch" above). Once the backend propagation fix lands, the orphan count should drop close to zero; spikes after that signal a regression. Always read the loud `!!!!` banner before trusting ratios.
- **Conversations seen on only one side.** A cid logged in Conversation but not Memory means memory never fired during the window for that conversation (short conversations don't trigger memory, or the firing happened before the window started). They count in `conversations_conversation_only` and are excluded from ratio percentiles. Distinct from `orphan memory cids` — orphans are *memory cids never seen on the conversation side*, which today is almost everything.
- **Sibling prompters in the Conversation project.** `Conversation [Production]` hosts `coach-prompter` (the user-facing chat), `quick-processing-prompter` (post-conversation summarisation), and `insight-card-prompter` (post-conversation card generation). Only `metadata.name` distinguishes them, and the skill filters to `coach-prompter` server-side, so the "conversation turn" count reflects only the dialogue. If you need QP or insight-card numbers, run a separate walk with a different `metadata[name]` — they're out of scope here.
- **Insight-card cid split.** When `insight-card-prompter` fires at end-of-conversation, the Conversation project stamps a NEW `conversationId` (distinct from the coach `conversationId` for the same chat) on the insight-card completions. End-of-conv memory and summary firings get tied to the insight-card cid, not the coach cid — a smaller secondary mismatch on top of the main cross-project one. Flag for a follow-up if you're chasing the last few percent of orphans.
- **Conversation `metadata.conversationId` vs `conversation_id`.** Conversation logs both field names (camelCase current, snake_case older). The script `COALESCE`s them so historical rows still produce a cid.
- **Field name drift.** If `metadata.conversationId` disappears from one project or starts carrying a different identifier, the orphan rate spikes and ratios go to zero — the script will print the spike, and the summary's "orphan memory cids" line is the canary.
- **Window mismatch with deploy.** If the memory prompt changed mid-window, the totals smear across two regimes. Run two narrower windows (pre- and post-deploy) to see the actual delta — the median ratio is the cleanest single number to compare across runs.
- **Rate limits.** OpenAI's org-level endpoints share a per-minute limit. The script paginates serially at 100 rows/page (OpenAI's list cap — passing >100 silently returns an empty page) with exponential backoff on 429/5xx. Because there's no server-side aggregation, wide windows make many calls (see the runtime table); don't run this script in tight loops or raise concurrency.
