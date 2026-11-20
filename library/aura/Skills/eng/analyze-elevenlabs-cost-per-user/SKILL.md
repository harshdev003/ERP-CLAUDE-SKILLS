---
name: analyze-elevenlabs-cost-per-user
description: >
  Per-user drill-down for ElevenLabs Conversational AI spend — answers "who's driving cost?",
  "which Firebase UIDs are heavy users?", "is one user disproportionate?", and with `--trend`
  "how is per-user spend shifting day-over-day?". Actuals only: per-user dollars come directly
  from call minutes times the ElevenLabs rate, with no credits-to-minutes derivation. Walks
  the conversations API, then aggregates credits, call minutes and actual cost by the `userId`
  dynamic variable. Defaults to yesterday so it always lands inside the 10-day retention
  window. Privacy-aware: Top-10 plus a cost distribution, never a full per-user dump, and UIDs
  truncated to 8 characters. Trigger on "who are the heavy elevenlabs users", "cost per user",
  "elevenlabs cost by user", "who is driving aurie cost", "top users by minutes", "top users
  by spend", "firebase uid breakdown", "aurie heavy users", "who entered top users this week",
  "elevenlabs user distribution", "elevenlabs trend", or "day over day elevenlabs cost".

metadata:
  version: "3.0.5"
knowledge:
  reads:
    - Guide/Index         # baseline routing aid (universal)
    - Tools/ElevenLabs        # rates, dynamic variables, retention
    - Compliance/Privacy  # UID redaction rule (first 8 chars + ellipsis), no givenName, etc.
---

# Analyze ElevenLabs Cost Per User

Walks the ElevenLabs Conversational AI conversations endpoint and aggregates by the `userId` dynamic variable that the Aurie integration sets on every call.

This is the **drill-down** sibling of [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md):

| | Workspace skill | This skill |
|---|---|---|
| **Question** | How much have we burned this period? Are we already over? | Who's driving cost? Which UIDs are heavy users? |
| **Endpoint** | `/v1/usage/character-stats` (aggregate) | `/v1/convai/conversations` (list + detail per call) |
| **Speed** | ~2 API calls, <2s | ~7 min/day of window (one detail fetch per conversation — see Runtime) |
| **Output framing** | Plan cost, live overage, quota used so far | Top-N users, distribution, per-conversation stats |
| **Cost basis** | `fiat_units_spent` (= minutes × \$0.09) per window | Per-call `duration_secs / 60 × ELABS rate` |

Both skills are **actuals only** — no end-of-period extrapolation.

Run the workspace skill first when you have a budget question; come here when the workspace report flags a number you want to attribute.

## ⚠️ 10-day retention

The Aura workspace has a **10-day Conversations Retention Period** configured under ElevenLabs' Privacy panel (bumped from 5 → 10 days on 2026-05-14). After 10 days the detail endpoint deletes transcripts + audio + dynamic variables (including `userId`), so per-user attribution requires fetching within the 10-day window. The default `yesterday` window always falls inside retention; longer windows produce stderr warnings, and detail fetches for older items will 404.

See [`Tools/ElevenLabs > Data retention`](https://www.notion.so/34c75e23af20819abea7d64a826b72f3) in AI Knowledge for the canonical write-up.

## Privacy posture

This skill touches per-user identifiers, so it follows [`Compliance/Privacy`](https://www.notion.so/35f75e23af20813ea4e7f4165c9e0844):

- **Firebase UIDs are truncated to the first 8 characters + `…`** in every rendered markdown surface (Top-N table, distribution rows, flags, headline, sample output). The full 28-character UID appears only in `--json` output, which is the documented exception ("opt-in body that other agents need to look up by exact match"). Don't paste full UIDs from JSON into Slack, Notion, or anywhere else — re-render through the markdown path or apply the same truncation manually.
- **`givenName` is never read or surfaced.** The script's attribution extractor doesn't even pull it from the response. Don't reach into the raw API response to grab it for "friendlier" output.
- **Default render is Top-10 + aggregate distribution.** `--full` exists for ad-hoc investigation but the UIDs are still truncated; it doesn't unlock anything PII-bearing.
- **The skill makes no local writes.** No cache, no derived files, no sidecar metadata. Every run is fresh from the API. If a multi-week trend renderer is ever added, it must come back through privacy review on its own merits.

If you need to look up a specific user behind a UID, that's an out-of-band step using whatever tooling the Aura internal user directory provides — and the skill output you paste anywhere external still has to follow the redaction rule.

## Step 0 — Check prerequisites

```bash
if [ -n "$ELEVENLABS_ADMIN_API_KEY" ]; then
  echo "OK: admin key set"
elif [ -n "$ELEVENLABS_API_KEY" ]; then
  echo "WARN: only ELEVENLABS_API_KEY set — fallback path, totals may be scoped"
else
  echo "MISSING — see Tools/ElevenLabs for setup"
fi
```

The key needs three scopes (set on <https://elevenlabs.io/app/developers/api-keys>):

| Scope | Why |
|---|---|
| **Workspace Analytics → Access** | Subscription + credits/min derivation |
| **User → Read** | Plan tier + character_limit |
| **ElevenAgents → Read** | `/v1/convai/conversations` list + detail (this is the one most likely missing) |

If the key 401s on conversations, it's the ElevenAgents scope — add it on the same key (no rotation needed) and rerun.

Other prereqs: `python3` with `requests`. `pip3 install requests` if missing.

## Step 1 — Pick a window

Defaults to `yesterday`. Honour the user's intent otherwise:

- **`yesterday`** *(default)* — previous full Pacific calendar day. Always inside retention and matches the dashboard view for the team.
- **`today`** — current Pacific calendar day so far. Live, in-progress conversations are skipped.
- **`last-24h`** — rolling 24h ending now.
- **`last-5d`** — rolling 5 days. Narrower live window, faster.
- **`last-7d`** — rolling 7 days. Default for `--trend`.
- **`last-10d`** — rolling 10 days. Maximum reliable live window before retention bites.
- **`--since YYYY-MM-DD --until YYYY-MM-DD`** — custom date range, half-open, anchored at Pacific midnight (matches the dashboard view). If the start is more than 10 days ago, the skill warns on stderr that detail fetches will 404 for the older portion.

If the user asks for a specific calendar day ("April 28", "May 10"), translate to a `--since` / `--until` pair.

## Step 2 — Run the report

### Default (yesterday)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-cost-per-user/scripts/report.py \
  --json /tmp/per-user.json
```

### Variants

```bash
# Other live windows
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-cost-per-user/scripts/report.py --window last-24h
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-cost-per-user/scripts/report.py --window last-7d
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-cost-per-user/scripts/report.py --window last-10d

# Explicit range (Pacific midnight, half-open)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-cost-per-user/scripts/report.py \
  --since 2026-05-12 --until 2026-05-14

# Larger Top-N (UIDs still truncated)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-cost-per-user/scripts/report.py --top 25

# Full per-UID table (ad-hoc investigation; UIDs still truncated)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-cost-per-user/scripts/report.py --full

# 7-day day-over-day trend (default — within retention; UIDs truncated; ~45 min runtime)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-cost-per-user/scripts/report.py --trend

# Full 10-day trend (maximum retention window; ~65 min runtime)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-elevenlabs-cost-per-user/scripts/report.py --trend --window last-10d
```

**Runtime scales with total workspace voice volume, not with the window's usefulness.** The walk is
serial: one detail fetch per conversation, ~0.37 s each. Measured 2026-08-16 on the identical walk in
`eng:report-elevenlabs-user-cost`: **10,667 conversations over the retention window in 65.9 min**,
≈1,070 conversations/day ⇒ **≈6.6 min per day of window**. Derive from that rate rather than trusting
the absolute figures above — voice traffic grows, so they drift on their own. Re-measure before
sizing a timeout around this skill.

### `--trend` mode

Renders three sections instead of the single-window Top-N:

1. **Per-day rollup** — for each Pacific calendar day in the window: conversation count, unique users, minutes, credits, actual cost (minutes × ELABS rate), day-over-day **ΔΔ cost** percentage.
2. **Top-N daily breakdown** — Top users by *total credits across the full window*, with a column per day showing each user's credit volume that day. The "Days present" column flags users who only appeared on one or two days (often anomalies worth a closer look).
3. **Heavy-hitter shifts** — for each day-over-day transition, which UIDs entered the top-10 vs dropped out. Helps spot a new user surging or a regular dropping off.
4. **Concentration trend** — top user's share of daily credits, day-by-day. Rising = increasing concentration risk; falling = healthier spread.

`--trend` defaults to `last-7d` unless `--since` / `--until` or `--window` is passed explicitly. The window is always bounded by retention (currently 10 days); older spans produce 404s on detail fetches and a stderr warning. For the maximum live window, combine with `--window last-10d`.

## Step 3 — Show the report inline

Show the script's stdout directly. The report contains:

1. **Headline** — total cost, conversations, unique users in the window.
2. **Cost per user — distribution** (this is the key concentration signal):
   - User count + mean / median / p90 / p95 / p99 / max / stddev (all in USD).
   - Matches the framing in [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md) so the two reports can be read side-by-side.
   - Plus a "Top user accounts for X% of cost — ~N× the median" line so the concentration is unambiguous at a glance.
3. **Distribution (minutes per user)** — secondary volume view. Minutes are the unit ELABS bills on, and they're direct API actuals — no credits→minutes derivation in the math.
4. **Top-N users** (or full table with `--full`) — truncated UIDs, conversations, minutes, credits, est. cost, flags.
5. **Per-conversation distribution** — credits per call.

In `--trend` mode, the cost-per-user distribution is computed over the FULL window (7 days by default, up to 10) using each user's total credit volume across all days. So one user contributing 100k credits/day for 7 days shows up as \$40 in the distribution, not 7 separate ~\$6 entries.

Lead any narrative summary with the headline:

> _"Yesterday: **172 conversations** from **85 unique users**, \$186.01 actual cost (minutes × ELABS rate). Top user (`XGxAMqr1…`) did 565 minutes across 23 conversations (\$50.89 of yesterday's spend, 27% of total). Median user did 8 minutes, p90 ran 61 minutes — long tail of heavy users."_

Things to call out when present:

- **Concentration** — if one UID prefix is >10% of total credits, mention it.
- **Burst usage** — if any user has non-zero burst credits, flag it (\$0.19/min vs \$0.09 in-plan is a 2× hit).
- **Incognito skew** — the `incognito` flag tells us if the user disabled call retention for billable analytics; doesn't change the bill but worth tagging.
- **Platform mix** — if a single UID shows multiple platforms in one day (`mobile + web`), that's interesting context.

In any of these callouts: keep UIDs at the 8-char prefix. Never re-expand from JSON when narrating.

## Step 4 — Ask where to route it

After showing the report:

1. **Save the JSON** — useful if the user wants to import into a spreadsheet for further slicing. (Full UIDs in JSON are fine; the spreadsheet is treated as the same opt-in surface the privacy rule carves out.)
2. **Investigate a specific UID** — use [`eng:investigate-elevenlabs-conversations`](../investigate-elevenlabs-conversations/SKILL.md) to pull transcripts for individual conversations.
3. **Cross-check workspace totals** — run [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md) to confirm the per-user sum aligns with what the workspace endpoint shows.
4. **Nothing, just show it** — stop here.

**Do NOT post per-UID tables to Slack without explicit user confirmation.** Even with 8-character truncation, a Slack post is an external surface. If the user does want a Slack post, default to **aggregate-only**: headline + distribution + bottom-line cost, no Top-N table. If they want the table, render with truncated UIDs only — never expand from JSON.

## Notes & edge cases

- **Cost-rate ambiguity.** Per-conversation cost uses **\$0.09/min** (in-plan rate) for normal calls and **\$0.19/min** for `is_burst: true` calls. At the conversation level we can't know whether the call was the one that pushed the workspace into overage, so \$0.09 is the most honest default. The workspace skill handles portfolio-level overage separately.
- **`metadata.cost` == `metadata.charging.call_charge`.** Same value in different paths; the script reads `metadata.cost` first.
- **Aurie's `userId` dynamic variable carries the Firebase UID.** ElevenLabs also surfaces it at the top-level `user_id` field on the detail response. The script prefers the top-level one but falls back to `dynamic_variables.userId`.
- **In-progress conversations skipped.** `status == "in-progress"` calls don't have a final `cost` yet, so they're filtered out.
- **`agent_id` filtering not exposed today — and the workspace now has six agents.** The Response-API migration split Coach and Quick Processing into an agent *pair* each, one per OpenAI API, because ElevenLabs selects the API on the agent's `custom_llm.api_type` rather than per session:

  | Agent | ID | API |
  | -- | -- | -- |
  | Coach — Response API | `agent_3401kyr8ry23fgmbgn8874jpfhzg` | `responses` |
  | Coach — Completion API | `agent_8701kn4rf9fcem6vj17mjrqx65sn` | `chat_completions` |
  | Quick Processing — Response API | `agent_4401kyr8qvreewq8ktr3e2e32mqk` | `responses` |
  | Quick Processing — Completion API | `agent_6901kn56759pf2rbzvvhb8cj9kfz` | `chat_completions` |
  | Onboarding | `agent_6101kea11zrgekytgpkpvex9qd2c` | `responses` |
  | Legacy Coach (no incognito) | `agent_4101k6nj9bbmeq9t648kvq1c4695` | `chat_completions` |

  Per-user **totals stay correct** — the script aggregates every conversation for the UID regardless of agent, which is what you want for "what does this user cost". What is *not* possible today is splitting a user's cost **by API arm**, because a `aiResponseAPI` treatment user's calls land on the Response-API agents and a control user's on the Completion-API ones, and the report can't tell them apart. Add a `--agent` filter (or group by `agent_id`, which the detail response already returns and the script already carries through) if you need per-arm cost.
- **Rate-limit safety.** Detail fetches retry on 429 with exponential backoff. List pagination is cursor-based and stable.

See [Tools/ElevenLabs in AI Knowledge](https://www.notion.so/34c75e23af20819abea7d64a826b72f3) for the rate table, retention configuration, and Dynamic Variables reference. See [Compliance/Privacy](https://www.notion.so/35f75e23af20813ea4e7f4165c9e0844) for the canonical UID redaction rule and the broader PII boundary that governs every external surface this skill writes to.
