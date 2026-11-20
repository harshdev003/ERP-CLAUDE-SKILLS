---
name: report-elevenlabs-costs
description: >
  One-pane consolidated daily ElevenLabs cost snapshot for the Aura workspace — defaults to
  yesterday Pacific, matching the dashboard view and staying inside the 10-day retention
  window, and produces a single condensed report combining the day headline (credits, actual
  plan cost, live overage), subscription and quota state, by-model credit breakdown,
  by-product-type breakdown, and top 5 Conversational AI users annotated with their current
  subscription state. Sits next to `eng:analyze-elevenlabs-costs` and
  `eng:analyze-elevenlabs-cost-per-user` but optimised for the morning-standup "what did we
  spend on voice yesterday?" question. UIDs truncated to 8 characters. Trigger on "summarize
  elevenlabs costs", "report elevenlabs costs", "report 11labs costs", "elevenlabs daily
  cost", "elevenlabs cost snapshot", "yesterday's elevenlabs report", "morning elevenlabs
  report", "voice cost snapshot", "11labs daily summary", or "how much did we spend on voice
  yesterday".

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index                 # baseline routing aid (universal)
    - Tools/ElevenLabs                # per-minute rates, retention, dynamic variables
    - Tools/Aura API              # subscription endpoint auth + shape
    - Tools/Slack                 # channel directory + Post format footer spec for #eng-budget post (--post-to-slack)
    - Compliance/Privacy          # UID redaction rule (first 8 chars + ellipsis)
---

# Report ElevenLabs Costs

Sibling to [`eng:analyze-elevenlabs-costs`](../analyze-elevenlabs-costs/SKILL.md) (workspace billing-period deep-dive) and [`eng:analyze-elevenlabs-cost-per-user`](../analyze-elevenlabs-cost-per-user/SKILL.md) (per-user attribution deep-dive). Same dollar / credit sources, condensed into a single screen for the morning-standup use case.

Six sections in order:

1. **Day headline** — credits burned + actual plan cost + live overage. Comparison to 7-day rolling average burn rate (flags ±20% anomalies).
2. **Subscription / quota** — plan tier, monthly cost, renewal date, quota used + expected pace.
3. **By model** — credit consumption split across models (Flash v2.5, Multilingual v2, Conversational AI agents).
4. **By product type** — Conversational AI vs TTS (vs others).
5. **Top 5 users (Conversational AI)** — Firebase UID + minutes + $ + **current Aurie/Aura subscription state** (via Aura PaymentService).
6. **Optional Slack payload** when invoked with `--post-to-slack` — the script only *emits* the payload; the agent posts it by invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md).

## Step 0 — Check prerequisites

```bash
# ElevenLabs key (admin preferred, personal key works too)
if [ -n "$ELEVENLABS_ADMIN_API_KEY" ]; then
  echo "OK: ELEVENLABS_ADMIN_API_KEY set"
elif [ -n "$ELEVENLABS_API_KEY" ]; then
  echo "OK: falling back to ELEVENLABS_API_KEY (workspace totals may be scoped to your personal key)"
else
  echo "MISSING: ElevenLabs key"
fi

# Aura PaymentService token for subscription-state annotation on top-5
[ -n "$AURA_API_TOKEN" ] && echo "OK: AURA_API_TOKEN set" || echo "MISSING: AURA_API_TOKEN (top-5 will render without state annotation)"
```

If the ElevenLabs key is missing, walk the user through adding `ELEVENLABS_ADMIN_API_KEY` via `update-config` and restarting Claude Code. Required scopes: **Workspace Analytics + User Read + ElevenAgents Read**.

If `AURA_API_TOKEN` is missing, the report still produces all six sections — the top-5 table just shows UIDs without state annotation. Document the soft-degrade in the report's stderr.

Other prereq: Python 3 with `requests`.

## Step 1 — Resolve the time window

**Yesterday Pacific** is the default — matches `eng:analyze-elevenlabs-cost-per-user`, aligns with the ElevenLabs dashboard's viewer-TZ default, and stays inside the 10-day Conversational AI retention window. Other forms:

- **Yesterday PT** (`--window yesterday`, default) — previous full Pacific calendar day
- **Today PT** (`--window today`) — partial; flags incompleteness
- **Last 7 days** (`--window last-7d`) — rolling
- **Custom** — `--since YYYY-MM-DD --until YYYY-MM-DD` (interpreted as Pacific midnights)

Windows wider than 10 days mean some Conversational AI calls have aged out of retention — `userId` is no longer fetchable for those calls. The skill prints a stderr warning and the top-5 section caveat reflects the gap.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-costs/scripts/report.py \
  --window yesterday \
  --json /tmp/elevenlabs_daily_snapshot.json
```

Other forms:

```bash
# Last 7 days, no top-users walk (fast — only workspace endpoints)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-costs/scripts/report.py --window last-7d --no-top-users

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-costs/scripts/report.py \
  --since 2026-05-15 --until 2026-05-17

# Emit a condensed Slack payload (the agent then posts it via shared:post-to-slack)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-elevenlabs-costs/scripts/report.py --post-to-slack
```

The script:

- Calls `/v1/user/subscription` for plan / overage / quota state
- Calls `/v1/usage/character-stats` with `breakdown_type=model` and again with `breakdown_type=product_type` for the window
- Walks `/v1/convai/conversations` for the window, then fetches `/v1/convai/conversations/{id}` per call for `duration_secs` + `metadata.charging.is_burst` + `dynamic_variables.userId` (this is the slow part — ~30–60s for a typical yesterday)
- For each of the top 5 users (ranked by minutes), calls Aura PaymentService `POST /subscriptions/list/:userId` to derive current state
- Renders all six sections plus, with `--post-to-slack`, *emits* an optional Slack payload (it does not post — the agent posts it via `shared:post-to-slack`) + JSON output

**Allocation math.** See [`references/methodology.md`](references/methodology.md). Summary: credits come straight from ElevenLabs (no derivation); per-user $ is `duration_secs × \$0.09/min in-plan, \$0.19/min burst` (actuals only — same as the per-user skill).

## Step 3 — Show the report inline

Print the script's stdout directly. Single screen — don't paginate, don't summarise, don't reformat. The headline carries the credits + plan/overage + 7d delta in one glance:

> **Yesterday (May 16 PT): 487K credits | Plan: \$500/mo Business | Overage: \$0.00 | -12% vs 7d avg ✅**

Below that the six sections render in order. Use the script's output verbatim.

## Step 4 — Ask what to do next

Use `AskUserQuestion`. Don't auto-route. Options:

1. **Drill into the per-user dimension** — chains to [`eng:analyze-elevenlabs-cost-per-user`](../analyze-elevenlabs-cost-per-user/SKILL.md) for the full distribution + heavy-hitter shifts.
2. **Drill into one user's behaviour** — hand the top-ranked UID to [`data:analyze-user-behavior`](../../../data/skills/analyze-user-behavior/SKILL.md) for the full behavioural profile.
3. **Post a Slack summary** — re-runs with `--post-to-slack` to **emit** a condensed payload (the script does not post — it just writes the payload out for the agent to pick up), then **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** to send it to `#eng-budget` (that skill owns the connection — MCP-first, `SLACK_BOT_TOKEN` script fallback — and the unfurl-off default; don't call Slack directly here). Skips top-5 from the Slack payload by default (PII-adjacent). Pass `--footer eng:report-elevenlabs-costs --footer-extra prod` to `shared:post-to-slack` so it stamps the versioned footer (`_Generated by `eng:report-elevenlabs-costs:<version>` · <YYYY-MM-DD> · prod_`); the emitted payload carries no footer, so don't hand-type one.
4. **Compare windows** — re-run for a different window (last-7d vs last-30d, etc.) so the user can see trend.
5. **Nothing, just show it** — stop here.

## When to use this vs the sibling skills

| Question | Skill |
|---|---|
| "What did we spend on voice yesterday?" | **`report-elevenlabs-costs`** (this) |
| "Are we on budget? Will we hit overage?" | `eng:analyze-elevenlabs-costs` |
| "Who's driving Aurie ConvAI cost?" | `eng:analyze-elevenlabs-cost-per-user` |
| "Day-over-day heavy-hitter shifts" | `eng:analyze-elevenlabs-cost-per-user --trend` |

The deep-dives default to billing-period or rolling-window views and produce multi-page reports. This skill defaults to yesterday PT and fits one screen.

## Subscription-state annotation on top 5

For each of the top 5 users (ranked by minutes), the script calls `POST https://services.aurahealth.io/subscriptions/list/:userId` with `Authorization: Bearer ${AURA_API_TOKEN}` and `{"includeDeleted": true}` body — `includeDeleted=true` is intentional so refund / revocation history surfaces alongside active, cancelled, and expired rows (otherwise a refunded trial looks indistinguishable from a never-premium user, which we found out the hard way on 2026-05-17). The response is an array of subscription rows; the skill derives a current state per `type` (`ai` for Aurie, `content` for Aura content):

- **AI Lifetime** — `type=ai, lifetime=true, deletedAt=null`
- **AI Paid** — `type=ai, autoRenew=true, trial=false, expirationAt > now`
- **AI Trial** — `type=ai, autoRenew=true, trial=true, expirationAt > now`
- **AI Trial (cancelled)** — `type=ai, autoRenew=false, trial=true, expirationAt > now`
- **AI Cancelled (active)** — `type=ai, autoRenew=false, expirationAt > now`
- **AI Expired** — `type=ai, expirationAt < now` and no active row + no deletions
- **AI Trial Expired** — `type=ai` row with `deletedAt` within 5 min of `expirationAt`, `autoRenew=false`, `trial=true`. **Standard end-of-trial flow** — user let trial run out naturally after disabling auto-renew. *Not* a refund. (ENG-540.)
- **AI Refunded** — `type=ai` row with `deletedAt` materially before `expirationAt` (>5 min). Implies Apple/Google/Aura refund processed before the period would have ended; includes the "refund-then-keep-using" / chargeback cohort.
- **AI Revoked** — `type=ai` row with `deletedAt` but neither of the above (admin/CS action, payment-failure cleanup). Rare fallback.
- **Content-only (Aura)** — only `type=content` rows present
- **Content Expired (Aura)** — only `type=content` rows, all expired
- **Never premium** — empty array

Top-5 with both AI and Content rows gets the AI state annotation (since they're using Aurie ConvAI). When multiple deleted AI rows exist (e.g. a refunded trial followed by a re-purchase that also got refunded), the most-recently-deleted row wins.

**Full subscription timeline in `--json`.** Every top-5 user's `--json` entry includes a `subscription_timeline` array with one entry per row, sorted by `createdAt` ascending. Each entry carries `type`, `provider`, `trial`, `lifetime`, `created_at`, `purchase_at`, `converted_at` (trial→paid transition), `expiration_at`, `deleted_at`, `auto_renew`, and a derived `status` (one of `active` / `cancelled-active` / `expired` / `trial-expired` / `refunded` / `revoked` — the three deletion statuses come from the same `_classify_deletion` heuristic as the single-label state above). Suitable for downstream agents that want the full subscription story rather than a single label — `data:analyze-user-behavior`, post-hoc churn analysis, etc.

If `AURA_API_TOKEN` is unset or the endpoint returns non-200, the column reads "(no state — `AURA_API_TOKEN` missing)" and the report continues. Don't fail the whole run on missing state annotation.

**State is current, not at-call-time.** The annotation reflects the user's subscription state **as of report render**, not as of the call. A user who was in a trial yesterday but whose trial has since expired will show as "AI Expired" here even though their yesterday calls were technically trial-time. This intentionally differs from `eng:report-openai-costs`, which derives state from `metadata.aiPremium*` stamped on each call (= state at call time). If you want at-call-time state on ElevenLabs, you'd need to join against a historical subscription snapshot — out of scope for this skill.

## Privacy posture

Per [`Compliance/Privacy`](https://www.notion.so/35f75e23af20813ea4e7f4165c9e0844):

- **Firebase UIDs are truncated to the first 8 characters + `…`** in every rendered markdown surface (top-5 table, headline if any UID surfaces, Slack payload). Full 28-char UIDs appear only in `--json` output, which is the documented exception.
- **`givenName` is never read or surfaced.** The `dynamic_variables` include it; the script doesn't pull it.
- **`cancelReason` / `cancelFeedback`** from the subscription response — these are PII-adjacent (user free-text). The state annotation doesn't surface them; only the derived state label.

## Edge cases

- **Window straddling ConvAI retention.** Walks fail (404 on detail fetch) for calls older than 10 days. The skill counts these as "scrubbed" in stderr and the top-5 only includes detail-fetchable calls. The credit / quota numbers come from `character-stats` which has longer retention, so the headline is still complete.
- **No ConvAI traffic in the window.** The top-5 section renders as "no Conversational AI traffic in this window — Aurie may be paused or this is a TTS-only day." The other sections still render.
- **Workspace key without Workspace Analytics scope.** `character-stats` returns 401/403. The skill exits with a hint to fix the scope (same behaviour as the sibling skills).
- **Subscription endpoint hits a stale-cancelReason row.** Per [ENGBACK-1081](https://linear.app/aura-health/issue/ENGBACK-1081/subscription-rows-surface-stale-cancelreason-cancelfeedback-while), `cancelReason` can be populated on a row that's still active. The state derivation here looks at `expirationAt` + `autoRenew` + `lifetime` first, not `cancelReason`, so this doesn't bias the annotation.
- **Cowork sandbox.** `mcp__workspace__bash` strips host env (PROD-1305) — `ELEVENLABS_ADMIN_API_KEY` + `AURA_API_TOKEN` won't reach the sandbox. Use the per-session paste-export workaround in AINative `CLAUDE.md`.

## Comparison baselines

The day-headline includes a 7-day rolling-average credit-burn comparison. If yesterday is more than ±20% from the 7-day average, the headline flags it (e.g. "+35% vs 7d avg ⚠️" or "-42% vs 7d avg ✅"). The 7-day window is fetched in the same `character-stats` call (`aggregation_interval=day`, then sum the day buckets) so there's no extra request.
