---
name: analyze-openai-tokens
description: >
  Daily OpenAI token usage report for the Aura organization, broken down twice: once by model
  and once by project. Defaults to yesterday UTC, the closest full-day authoritative bucket;
  also today (partial), last-7d, last-30d, month-to-date, or a custom range. Surfaces every
  token field the usage endpoint returns — input, input cached, input audio, output, output
  audio — and automatically picks up any new token field OpenAI ships (reasoning tokens and
  the like) with no code change. `--post-to-slack` drops a condensed summary into #eng-budget.
  Trigger on "openai token usage", "token usage by model", "token usage by project", "how many
  tokens did we burn yesterday", "daily openai token report", "tokens per model", "tokens per
  project", "reasoning tokens used", "audio tokens used", "breakdown of token types", "input
  cached output token breakdown", "token consumption daily", or "analyze openai tokens". Do
  NOT use for dollar reports — this skill never converts tokens to dollars.

metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/OpenAI  # canonical reference for OpenAI endpoints, auth, and rate-limit semantics
    - Tools/Slack   # channel directory + Post format footer spec for #eng-budget post (--post-to-slack)
---

# Analyze OpenAI Tokens

Sibling to [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md) and [`eng:report-openai-costs`](../report-openai-costs/SKILL.md) — same Admin API key, different endpoint and different unit. Those skills report **dollars** via the Costs API. This skill reports **tokens** via the Usage API, broken into the dimensions OpenAI actually meters: input, cached input, input audio, output, output audio, plus any newer `*_tokens` field the endpoint surfaces (e.g. reasoning tokens when they land on the org endpoint).

Two tables per run:

1. **By model** — every model the org touched in the window, ranked by total tokens
2. **By project** — every project the org touched, mapped to human-readable names

Each table has five primary token columns — Input, Cached input, Input audio, Output, Output audio — plus `Total tokens` (input + output) and `Requests`. A bold footer row shows the org-wide totals so the by-model and by-project tables reconcile.

**`--all-fields` (opt-in detail).** The Usage endpoint also returns sub-decompositions of input/output: `input_text_tokens`, `input_image_tokens`, `input_uncached_tokens`, `input_cached_text_tokens`, `input_cached_image_tokens`, `input_cached_audio_tokens`, `output_text_tokens`, `output_image_tokens`. These are slices of the primary fields (not additional tokens), so they're hidden by default to keep the table scannable. Pass `--all-fields` for the deep view — useful when investigating multimodal traffic (text vs image vs audio mix) or cache-hit rates by modality. `Total tokens` math is unchanged (still `input + output`) so the footer reconciles whether `--all-fields` is on or off.

## Step 0 — Check prerequisites

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "OK: admin key set" || echo "MISSING"
```

If missing, walk the user through setup — the key must be an **Admin API key** (`sk-admin-...`), not a regular `OPENAI_API_KEY` (`sk-proj-...` / `sk-...`). Create one at <https://platform.openai.com/settings/organization/admin-keys> (requires Owner role on the OpenAI org), store it in `~/.claude/settings.json` under the `env` block, and restart Claude Code. Hand off to `update-config` to add `OPENAI_ADMIN_API_KEY` without clobbering existing entries.

Other prereqs: `python3` with `requests` (`pip3 install requests` if missing). **No MCP fallback exists** for the OpenAI Admin API — missing key is a hard fail.

## Step 1 — Resolve the time window

Default is **yesterday UTC** — the closest full day with authoritative Usage API data. Other forms:

- **Yesterday UTC** (`--window yesterday`, default) — 24h ending at today's UTC midnight
- **Today UTC** (`--window today`) — partial day; the header flags incompleteness
- **Last 7 days** (`--window last-7d`) — rolling 7d ending now
- **Last 30 days** (`--window last-30d`) — rolling 30d ending now
- **Month-to-date** (`--window month-to-date`) — first of this calendar month UTC → now
- **Custom** — `--since YYYY-MM-DD --until YYYY-MM-DD` (UTC, half-open)

If the user names a date directly ("yesterday", "2026-05-16", "last Monday"), translate to `--since` / `--until` and pass through. If they name a calendar month ("April"), use `--since YYYY-04-01 --until YYYY-05-01`.

**Why yesterday UTC and not yesterday PT.** The Usage API only returns daily UTC buckets — no sub-day granularity. "Yesterday PT" would span two UTC bucket fragments, the later of which usually hasn't propagated yet (OpenAI lags ~24h on the most recent bucket). Yesterday UTC is the latest fully-propagated window. The header in the rendered report names the window explicitly so readers don't conflate it with their local TZ.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-tokens/scripts/report.py \
  --window yesterday \
  --json /tmp/openai_tokens.json
```

Other forms:

```bash
# Wider window
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-tokens/scripts/report.py --window last-7d

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-tokens/scripts/report.py \
  --since 2026-05-15 --until 2026-05-17

# Just the model table (skip the project pull entirely)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-tokens/scripts/report.py --no-projects

# Just the project table
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-tokens/scripts/report.py --no-models

# Full token-field detail (adds text/image/audio sub-decomposition columns)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-tokens/scripts/report.py --all-fields

# Post a condensed summary to #eng-budget after rendering
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-tokens/scripts/report.py --post-to-slack
```

What the script does:

- Calls `/v1/organization/usage/completions?group_by=model` for the by-model table
- Calls `/v1/organization/usage/completions?group_by=project_id` for the by-project table
- Calls `/v1/organization/projects` once to map project IDs → human names (including archived)
- For each row, dynamically picks up **every field ending in `_tokens`** the API returned — so when OpenAI adds `output_reasoning_tokens` (or similar) the column appears automatically with no code change
- Ranks rows by total tokens desc, appends a bold **Total** footer row to anchor the table to the org-wide sum
- Prints stdout (markdown); writes structured JSON if `--json` given

## Step 3 — Show the report inline

Print the script's stdout verbatim. Lead with a one-line headline picked from the first paragraph of the report, e.g. _"Aura burned 18.4M tokens yesterday across 312 requests — gpt-5.4 took 71%, aurie-prod was 84% of project tokens."_ Don't reformat, don't paginate.

If a row stands out — one model with 90%+ of tokens, one project doubling vs the prior day, an unexpected legacy model showing up — call it out plainly. Don't speculate about cause; the user is closer to the code.

## Step 4 — Ask what to do next

ALWAYS use `AskUserQuestion` to offer follow-ups. Don't auto-route. Common picks:

1. **Drill into dollars for the same window** — chains to [`eng:report-openai-costs`](../report-openai-costs/SKILL.md) (yesterday/daily) or [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md) (wider windows) so the user can see how the token mix maps to spend. Pass the same `--window` / `--since` / `--until` straight through.
2. **Drill into top users for the same window** — chains to [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md) when the token totals are anomalous and the user wants to know which UIDs drove it.
3. **Post a Slack summary** — re-runs the script with `--post-to-slack` (the payload carries no footer), then posts the top-N model/project summary to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`) by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with `--footer eng:analyze-openai-tokens --footer-extra prod` so it stamps the versioned footer — don't hand-type one.
4. **Compare windows** — re-run for a different window so the user can spot a trend (yesterday vs week ago, this week vs last).
5. **Nothing, just show it** — stop here.

## When to use this vs the sibling skills

| Question | Skill |
|---|---|
| "How many tokens did we use yesterday, by model?" | **`analyze-openai-tokens`** (this) |
| "Are reasoning tokens dominating output now?" | **`analyze-openai-tokens`** (this) |
| "What did we *spend* on OpenAI yesterday?" | `eng:report-openai-costs` |
| "Where's all our spend going this month?" | `eng:analyze-openai-costs` |
| "Per-model dollar rates (not token counts)" | `eng:analyze-openai-costs --by-model` |
| "Who are the top 20 users by cost?" | `eng:analyze-openai-cost-per-user` |
| "Premium Paid vs Trial vs Free cost split" | `eng:analyze-openai-cost-per-subscription-state` |

If the user asks for both tokens and dollars in one breath, run this skill first (cheaper — two API calls), then offer the dollar drill-down via Step 4.

## Output reconciliation

The bold **Total** footer on the by-model table should match the bold **Total** footer on the by-project table — both are summing the same underlying rows just grouped on a different dimension. If they diverge by more than a rounding cent on tokens (they shouldn't — token counts are integers), one of the API calls returned partial data; re-run.

A small slice of usage with no `model` or no `project_id` lands under `(none)` rather than being dropped. Treat that row as a data-quality signal — typically rounds to zero, but a non-trivial `(none)` row in either table is worth flagging to eng.

## Slack message format (Step 4 option 3)

`--post-to-slack` prints a condensed payload to stdout so the agent can route it via the Slack MCP `slack_send_message` tool. Shape:

```markdown
*OpenAI Token Usage — yesterday UTC (2026-05-18)*
Total: 18,432,114 tokens

*Top 5 models*
• `gpt-5.4-2026-03-05` — 12,994,222 tokens
• `gpt-5.4-mini-2026-03-17` — 2,711,003 tokens
• ...

*Top 5 projects*
• aurie-prod — 15,470,330 tokens
• aura-ios — 1,902,440 tokens
• ...
```

Post to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`). The Slack summary intentionally drops the per-token-field detail (kept inline) because the channel is for at-a-glance signals; readers who want the full breakdown can re-run the skill.

## Edge cases

- **API lag on the most-recent bucket.** OpenAI's daily bucket for "today" routinely lags 1–4 hours and is only sometimes propagated by yesterday's standup. The script flags `--window today` as a partial day in the header. If a freshly-finished UTC day shows suspiciously low tokens, wait a few hours and re-run.
- **Reasoning tokens are not separately reported.** As of 2026-05, OpenAI's org-level usage endpoint folds reasoning tokens into `output_tokens` — there is no `reasoning_tokens` or `output_reasoning_tokens` field. Live probe on 2026-05-19 confirmed: the endpoint returns 13 `*_tokens` fields and none of them break out reasoning. The per-call `/v1/chat/completions` list and retrieve endpoints return a simplified usage shape (no `completion_tokens_details`), so you can't get it from there either. The Responses API (`/v1/responses`) does expose `usage.output_tokens_details.reasoning_tokens` but requires the `api.responses.read` Admin-key scope, which is out of scope for this skill. If a user asks "how many reasoning tokens?", say honestly: "Folded into the Output column. OpenAI doesn't split it out at the org-usage level today — if they ever do, this skill picks it up automatically."
- **Unknown `*_tokens` field appears.** New fields only render under `--all-fields`. The script picks them up automatically — column appears with the API's raw key turned title-case (`output_reasoning_tokens` → `Output Reasoning`). When that happens, propose updating `PRETTY_TOKEN_LABEL` in [`scripts/report.py`](scripts/report.py) for the canonical label, and consider whether the field belongs in `PRIMARY_TOKEN_FIELDS` (rendered by default — true top-level totals) or `DETAIL_TOKEN_FIELDS` (rendered only under `--all-fields` — sub-decompositions of input/output). Don't write to `Tools/OpenAI` from here — that's the cost-rate skills' territory.
- **Image / embeddings / audio-only models.** This skill only covers chat completions + responses (the `/usage/completions` endpoint). Image generation, embeddings, vector stores, audio transcription have their own sibling `/usage/<kind>` endpoints with different metering. If the user asks about those, point them at the relevant sub-endpoint — this skill doesn't pull them today.
- **Cowork sandbox.** `mcp__workspace__bash` strips host env (PROD-1305), so `OPENAI_ADMIN_API_KEY` won't reach the sandbox automatically. Use the per-session paste-export workaround documented in AINative `CLAUDE.md` → "Known Cowork constraints". No MCP fallback exists.
- **Empty window.** If the resolved window has zero usage (e.g. a `--since`/`--until` pair that brackets a low-traffic stretch), the report renders the header and a single italic _No token usage recorded in this window._ line — no empty tables.
- **Rate limits.** The org-level endpoints share a single 60 req/min quota. The script paginates serially and retries on 429 with exponential backoff. Two calls per run (model + project) plus the one-shot projects fetch — well inside the quota.

## References

- [`references/openai-usage-api.md`](references/openai-usage-api.md) — endpoint cheat sheet (auth, params, response shape, token field semantics) for when the skill needs to extend to a new dimension or endpoint.
