---
name: analyze-openai-cost-per-subscription-state
description: >
  Break down OpenAI spend by subscription state for the Aura org over a chosen window —
  month-to-date (default), previous month, last-7d, last-30d, or custom. Pulls authoritative
  per-project dollars from the Costs API, then allocates them across subscription states,
  reading the IntelligenceService LLM-call ledger first with automatic failover to each
  project's stored chat completions. Two independent cuts in the same run: AI premium (Aurie,
  AI-prefixed labels) and content premium (Aura, unprefixed canonical labels). Surfaces top
  spenders per state so runaway-user candidates fall out, then offers a per-user drill-down
  within one state, chaining to `eng:analyze-openai-cost-per-user` with the same window.
  Trigger on "cost per subscription", "cost per subscription state", "spend per state", "spend
  per tier", "trial vs paid openai cost", "openai cost breakdown by aiPremiumState", "how much
  do paid users cost us", "openai cost per tier", or "who's costing us money on the free
  tier".

metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/OpenAI  # three-state semantic on premium booleans + per-model rates
    - Tools/Slack   # channel directory + Post format footer spec for #eng-budget post
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Analyze OpenAI Cost Per Subscription State

Sibling to [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md). Same authoritative dollar source — the OpenAI Costs API at `/v1/organization/costs` — split a different way: by subscription tier rather than by project.

**Attribution source: Intelligence Service LLM-call ledger first, stored chat completions as failover.** For the token-weight attribution, the skill reads the IntelligenceService per-call LLM ledger (BigQuery `aura-main.llm.llm_call`, auth via `GCP_KEY` — docs: [LLM Calls](https://app.notion.com/p/aurahealth/LLM-Calls-3a675e23af208127b217e53af47524e8)) as the **primary** source. The ledger has one row per OpenAI call — both Chat Completions **and** Responses APIs — with `aiPremium` / `aiPremiumState` / `premium` / `premiumState` as top-level columns (the equivalents of the `metadata.*` fields). The `aiPremium` / `premium` booleans were added 2026-07 (ENGBACK-1237), so a churned user (`aiPremium=false`) stays "Non AI Premium" rather than collapsing into "Never AI Premium"; rows predating that column carry a null boolean and fall back to inferring the tier from the `*State` field alone. If the ledger request fails (non-200, timeout, parse error, missing token), the script warns on stderr and falls back to the stored-chat-completions walk below — note the fallback is chat-API-only and cannot see Responses-API traffic.

**Ledger epoch.** The ledger went live at **2026-07-23T03:25Z** (Intelligence Service Release 0.285.0); there are no ledger rows before that moment. Any part of the requested window before the epoch always uses the stored-completions walk; windows straddling the epoch mix both sources (the report's `Attribution source:` line says which). Results from the two slices are merged and deduped on the OpenAI object id.

In the fallback (and pre-epoch) path, for each project with non-zero spend the skill iterates that project's stored chat completions (`GET /v1/chat/completions` with an `OpenAI-Project` header) and reads four metadata fields off each one:

- `metadata.aiPremium` — `"true"` / `"false"`
- `metadata.aiPremiumState` — `paid` / `trial` / `free` / `paymentPending`
- `metadata.premium` — `"true"` / `"false"`
- `metadata.premiumState` — `paid` / `trial` / `free` / `paymentPending`

These were added by [ENG-498](https://linear.app/aura-health/issue/ENG-498) (AI premium) and [ENG-500](https://linear.app/aura-health/issue/ENG-500) (content premium).

**Three-state semantic on the booleans.** Each subscription boolean carries three meaningful states (see [`Tools/OpenAI`](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e) → "Three-state semantic on the booleans" for the canonical write-up):

- `null` / field absent → user has **never had** this subscription type. No subscription event has ever fired on the user row.
- `false` → user **had** this subscription type **and lost it** (cancelled / expired).
- `true` → user **currently has** it (refer to the `*State` field for paid/trial/free/paymentPending detail).

The two dimensions don't get this equally today. The `"aiPremium"` column in the IntelligenceService `"user"` table is `BOOLEAN` nullable with no default, so the three-state semantic is preserved — users who have never had AI premium carry NULL forever and the metadata-stamping path skips the field. The `"premium"` column is declared `BOOLEAN DEFAULT FALSE`, so the never-vs-lost distinction is flattened upstream before metadata is written. Tracked in [ENGBACK-1083](https://linear.app/aura-health/issue/ENGBACK-1083/premium-columns-should-not-default-to-false-collapses-never-premium).

**Bucket layout per cut:**

- **AI dimension — Aurie** (six buckets): AI Premium Paid, AI Premium Trial, AI Premium Free, AI Premium Payment Pending, Non AI Premium, **Never AI Premium**, plus `(untagged)`. `Never AI Premium` is for completions that arrive with no `aiPremium` / `aiPremiumState` fields but **do** carry `metadata.user` — the user row exists but the column is NULL. `(untagged)` is reserved for completions with no user at all (pre-account / system / batch traffic).
- **Content dimension — Aura** (five buckets): Premium Paid, Premium Trial, Premium Free, Premium Payment Pending, Non Premium, plus `(untagged)`. No "Never Premium" bucket — the `DEFAULT FALSE` flattens never-vs-lost, so we can't surface it until ENGBACK-1083 ships.

**Label convention.** AI-dimension rows carry an "AI " prefix; content-dimension rows don't. The two cuts share the same column shape so they're visually similar — the prefix is what tells a reader at a glance which subscription a row refers to. Aurie's AI premium and Aura's content premium are distinct subscriptions with different price points and different cohorts; conflating them at a glance led readers (including the team) to misread Trial-heavy weeks. Plain "Premium Paid" is reserved for the content (Aura) dimension because that matches what the rest of the product calls it.

Pre-account / system / batch traffic legitimately omits all four metadata fields — that traffic surfaces as `(untagged)` in both cuts and is distinct from the `Never AI Premium` user-row case.

Each completion contributes `(prompt_tokens + completion_tokens)` (or the Responses-API equivalent `input_tokens + output_tokens`) as a weight. The project's $ from the Costs API gets allocated across tiers in proportion to those weights — see [`references/allocation.md`](references/allocation.md) for the math and the caveats. **No model-price table is maintained.**

## Step 0 — Check prerequisites

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "OK: admin key set" || echo "MISSING"
```

If missing, walk the user through the same setup as `eng:analyze-openai-costs` — Admin API key (`sk-admin-...`), Owner role on the OpenAI org, store in `~/.claude/settings.json` under the `env` block, restart Claude Code. Hand off to `update-config` to add the key without clobbering existing entries.

```bash
test -n "$GCP_KEY" && echo "OK: GCP_KEY set" || echo "MISSING"
```

`GCP_KEY` authenticates the ledger read (BigQuery `llm.llm_call`). If it's missing the script does **not** fail — post-epoch slices fall back to the stored-completions walk with a stderr warning — but the fallback is chat-API-only and cannot see Responses-API traffic, so set it for full coverage.

Other prereqs: `python3` with `requests` (`pip3 install requests` if missing).

**Window of usable data.** OpenAI's documented retention for `store: true` chat completions is ~30 days. If the user asks for `previous-month` and we're already past day 30, the older end of the window may have been swept. The script doesn't fail — it just sees fewer completions in that range and a higher share of the Costs API's $ flows to `(untagged)`. Surface this to the user when results look thin. This retention caveat applies to the stored-completions path only — the ledger has no such retention limit, so post-epoch (on/after 2026-07-23T03:25Z) slices served by the ledger stay fully attributable going forward.

## Step 1 — Resolve the time window

Same presets as `eng:analyze-openai-costs`:

- **Month-to-date** (default) — 1st of current calendar month UTC → now
- **Previous month** — full prior calendar month (UTC)
- **Last 7 days** — rolling 7d ending now
- **Last 30 days** — rolling 30d ending now
- **Custom range** — explicit `--since YYYY-MM-DD --until YYYY-MM-DD` (UTC, half-open)

If the user names a month directly ("April", "2026-04"), translate to `--since` / `--until`.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-subscription-state/scripts/report.py \
  --window month-to-date \
  --json /tmp/openai_cost_per_tier.json
```

Other forms:

```bash
# Previous calendar month
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-subscription-state/scripts/report.py --window previous-month

# Only the AI-premium cut (shorter output)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-subscription-state/scripts/report.py --dimension ai

# Suppress the per-tier top-users ranking — Slack-friendly
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-subscription-state/scripts/report.py --no-top-users

# Custom range
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-subscription-state/scripts/report.py \
  --since 2026-04-01 --until 2026-05-01
```

By default the script:

- Renders **both** cuts (AI premium and content premium). Pass `--dimension ai` or `--dimension content` to render only one.
- Includes the per-tier **top-5 spenders** below each cut. Pass `--no-top-users` to suppress (and drop the "Top user" column).
- Prints a `## ⚠️ Coverage warnings` section listing any project where >5% of tokens have no tag — signals a regression in ENG-498 / ENG-500 *or* pre-account / system traffic the project owners haven't accounted for.

The script prints the report on stdout and (with `--json`) writes structured data. JSON shape:

```json
{
  "window": { "label": "...", "start_unix": ..., "end_unix": ..., "start_iso": "...", "end_iso": "..." },
  "currency": "usd",
  "total": 1832.41,
  "untagged_amount": 92.07,
  "by_ai_premium_state": [
    { "tier": "AI Premium Paid", "amount": 1240.18, "active_users": 320, "top_users": [{ "uid": "...", "amount": 42.11 }] },
    ...
  ],
  "by_premium_state": [
    { "tier": "Premium Paid", "amount": 980.55, "active_users": 410, "top_users": [{ "uid": "...", "amount": 38.20 }] },
    ...
  ],
  "coverage": { "<project_id>": { "total_tokens": ..., "ai_untagged_pct": ..., "content_untagged_pct": ..., "completion_count": ... } }
}
```

## Step 3 — Show the report inline

Show stdout directly. Lead with a one-line headline: total spend, tagged share, and the standout tier. Example:

> _Aurie spent \$1,832 on OpenAI this month — 67% from AI Premium Paid users (\$1,228), 12% from AI Premium Trial (\$220), and 5% untagged (\$92). AI Premium Free (comped / redemption-code users) accounted for \$48 across 11 users — paying zero, costing like AI Premium Paid. Worth a look at the top spender._

Use the AI-prefixed labels when talking about the AI dimension and the unprefixed labels for the content dimension — never mix them in the same sentence, since they refer to different subscriptions.

If the report's `## ⚠️ Coverage warnings` section fired, surface it to the user clearly — it usually means a code path missed the metadata tagging.

## Step 4 — Offer a per-user drill-down

The natural follow-up from a per-state view is *"who's costing us the most within state X?"* Offer it as a one-line menu after the report lands:

> _Want to see top users within a state? Name one (e.g. **"AI Premium Free"** for the Aurie cut, or **"Premium Paid"** for the Aura cut) or **skip**._

If the user picks a state, chain to [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md) with `--tier` set to that state and the same window already resolved in Step 1. Pass the exact label as rendered in the table (AI-prefixed for the AI cut, unprefixed for the content cut):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-user/scripts/report.py \
  --window <same as Step 1> \
  --tier "AI Premium Free"
```

Surface its stdout inline below the per-state report. The per-user output already restricts the distribution stats to the chosen state, so the headline reads cleanly ("of the 11 AI Premium Free users, the top one cost \$40 — ~80× the state's median").

If the user says "skip" / "no" / falls quiet, proceed to Step 5 with just the per-state view.

Common triggers Claude should interpret as a state pick without re-asking: "AI Premium Free users", "Aurie trial users", "show me AI Paid", "the Aura Premium Paid users", "Non AI Premium ones". When the user names a state without specifying which dimension, ask which one they mean — never assume.

## Step 5 — Ask where to route it

Same routing options as the parent skill:

1. **Post a summary to Slack** *(optional)* — post to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`). Use the compact format below. Re-run the script with `--no-top-users` for the Slack version so the message stays readable. Post via `shared:post-to-slack` with `--footer eng:analyze-openai-cost-per-subscription-state --footer-extra prod` so it stamps the versioned footer — don't hand-type one in the body.
2. **Save a Markdown summary** to a user-provided path.
3. **Nothing, just show it** — stop here.

If the user drilled down in Step 4, ask whether the Slack/file summary should include the per-user view or stay state-only. Default to state-only for Slack (per-user output has UID rows; same privacy posture as `eng:analyze-openai-cost-per-user`); include both for file saves.

Do NOT route automatically; always wait for the user's pick.

## Slack message format (Step 5 option 1)

Compact shape — designed to fit one Slack message without scrolling:

```markdown
**OpenAI Spend by Subscription — <window label>**

- **Total:** $<total> (<tagged_pct>% tagged, <untagged_pct>% untagged)
- **AI premium top tier (Aurie):** <AI Premium Paid / AI Premium Trial / …> — $<amount> (<share>%)
- **Content premium top tier (Aura):** <Premium Paid / Premium Trial / …> — $<amount> (<share>%)

**By AI premium tier (Aurie)**

| Tier | Spend | Share | Users |
|---|---:|---:|---:|
| AI Premium Paid | $X | X.X% | N |
| AI Premium Trial | … | … | … |
| AI Premium Free | … | … | … |
| AI Premium Payment Pending | … | … | … |
| Non AI Premium | … | … | … |
| Never AI Premium | … | … | … |
| (untagged) | … | … | — |

**By content premium tier (Aura)**

| Tier | Spend | Share | Users |
|---|---:|---:|---:|
| Premium Paid | $X | X.X% | N |
| Premium Trial | … | … | … |
| Premium Free | … | … | … |
| Premium Payment Pending | … | … | … |
| Non Premium | … | … | … |
| (untagged) | … | … | — |

**Headline**

<one paragraph: which tier is the largest, whether top users in a tier are a leak (e.g. AI Premium Free user costing \$40/mo), whether coverage looks healthy. Always name the dimension when citing a tier — "AI Premium Free" not "Premium Free" when referring to the Aurie cut>
```

Drop the `--no-top-users` ranking section from Slack — it's too long. The summary table's "Active users" + "$/user" columns already let people spot the obvious outliers; the inline chat report has the full top-5 ranking for deeper digging.

## Notes & edge cases

**Allocation is service-tier aware** ([ENG-1106](https://linear.app/aura-health/issue/ENG-1106)).
Each project's dollars are split by token weight scaled by the ledger's `serviceTier`:
OpenAI prices Flex at exactly **50%** of Standard and Fast (renamed from `priority` on
2026-07-30) at **2×**, so `flex`/`batch` weigh ×0.5, `default`/`auto`/`scale` ×1.0, and
`fast`/`priority` ×2.0. Per-tier list prices live in `Tools/OpenAI` → "Per-tier list prices".

A tier-homogeneous project cancels the factor out; the skew appears where tiers mix inside
one project, which happens both because different prompters run different tiers and because
a flex request falls back to `auto` under capacity pressure. A null `serviceTier` (rows
before the [ENGBACK-1315](https://linear.app/aura-health/issue/ENGBACK-1315) deploy) weighs
1.0. The `coverage.total_tokens` figure stays a **raw token count** — it measures tagging
coverage, not cost, so it is deliberately not scaled.

- **The stored-completions fallback resolves nothing for most projects (measured 2026-08-04).** What matters is whether a project's completions are **stored and listable**, not which API it calls — `Feed` still spends daily but has had no stored completion since **2026-07-23**. On a post-2026-07-23 window the ledger is effectively the only attribution source: every production project except `Conversation` has nothing stored, and `Conversation` itself is only **partially** covered — the `aiResponseAPI` A/B (since 2026-07-31) serves its treatment arm on the Responses API, so one conversation can span both surfaces. If the ledger is unreachable (or `GCP_KEY` is unset) this run does not degrade gracefully: dollars still come from the Costs API, so the report looks plausible while attribution collapses into the untagged bucket. **Treat a post-2026-07-23 run whose ledger call failed as unusable, not degraded** — re-run it rather than reading the numbers. Canonical per-project detail: `Tools/OpenAI` → "Responses API migration — reading stored data"; don't restate it here. [ENG-1049](https://linear.app/aura-health/issue/ENG-1049)
- **A post-2026-07-23 ledger outage now fails closed (exit 2) — [ENG-1054](https://linear.app/aura-health/issue/ENG-1054).** The caveat above says such a run is unusable rather than degraded; this is the code that enforces it, rather than a stderr warning that scrolls past. Pass `--allow-degraded-attribution` (or set `AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1`) to accept the degraded attribution deliberately. **Unaffected:** pre-epoch slices (the ledger has no rows before 2026-07-23T03:25Z, so stored completions are the correct source), projects the ledger doesn't serve, and the roll-out-ramp union — none of those is a ledger *failure*.

- **Rollout-window caveat.** The `aiPremium*` / `premium*` metadata tagging ([ENG-498](https://linear.app/aura-health/issue/ENG-498) / [ENG-500](https://linear.app/aura-health/issue/ENG-500)) shipped on **2026-05-12**. Calls stored before that date carry none of the four subscription-state fields — they pre-date the code that writes them. When the requested window predates or straddles that boundary, the script prints a `:warning:` banner at the top of the rendered report and a stderr line, and sets `rollout.window_status` in the JSON to `pre` / `mixed` / `post`. Don't read the `(untagged)` slice in a `pre` or `mixed` window as a tagging regression — it's the rollout window. For clean tier breakdowns, wait until the requested window is entirely on or after 2026-05-12. Full caveat lives on [`Tools/OpenAI`](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e) → "Rollout date and the data gap".
- **Ledger epoch + failover.** The Intelligence Service LLM-call ledger (BigQuery `llm.llm_call`) is the primary attribution source for slices on/after **2026-07-23T03:25Z** (Intelligence Service Release 0.285.0 — no ledger rows exist before then). Pre-epoch slices always use the stored-completions walk. On any ledger failure (BigQuery error, unset `GCP_KEY`) the post-epoch slice falls back to stored completions with a stderr warning — that fallback is **chat-API-only** and cannot see Responses-API traffic, so ledger outages understate Responses-API-heavy prompters in the tier weights (the $ totals from the Costs API stay correct). The rendered report's `Attribution source:` line states which source(s) served the window.
- **Numbers may lag the dashboard by up to 24h** — same caveat as the parent skill; the Costs API's most-recent daily bucket isn't always closed.
- **Retention.** Stored chat completions are kept ~30 days by OpenAI (not officially documented as a hard guarantee — verify on first run if asked). Requests for `previous-month` near the end of the next month may see partial coverage. The Costs API itself is not retention-bounded that tightly — so the $ totals are still correct; only the per-tier allocation degrades. The ledger has **no** such retention limit — post-epoch windows keep full attribution indefinitely.
- **Tier composition.** A completion's tier is `(<premium-bool>, <state-string>)`, with labels keyed to the dimension:
  - AI dimension: `aiPremium == false` → "Non AI Premium"; `aiPremium == true, state == paid|trial|free|paymentPending` → "AI Premium <State>" (e.g. "AI Premium Paid")
  - Content dimension: `premium == false` → "Non Premium"; `premium == true, state == paid|trial|free|paymentPending` → "Premium <State>" (e.g. "Premium Paid")
  - Both fields missing → "(untagged)" in either dimension (AI dimension also yields "Never AI Premium" when a `metadata.user` is present)
  - `aiPremium`/`premium` are stringified booleans on the wire (`"true"` / `"false"`) per OpenAI's metadata-string-only rule. The script coerces.
- **What "AI Premium Free" / "Premium Free" actually means.** A *Free* user is a user who has the subscription access **without paying for it** — typically via a redemption code, gift, comp, partnership, or other granted-access path. They get the same product experience as Paid (same prompters, same model routing, same per-call cost) but contribute zero revenue. That's why they get their own tier rather than being lumped into Non: the *cost* side looks like a paid user, the *revenue* side does not. A Free row that's large relative to Paid is a unit-economics signal — surface it; don't bury it in the rendered headline. The dimension matters when reporting: "AI Premium Free" is a comped Aurie subscription, "Premium Free" is a comped Aura content subscription, and the two cohorts may not overlap.
- **Active user count.** Computed off `metadata.user` (Firebase UID), which Aura's instrumented prompters set. Completions without a UID land under "(unknown)" and are *excluded* from the active-user count and the top-user ranking — they still contribute to the tier's dollar share.
- **Untagged ≠ leak.** ~5% of org spend is legitimately pre-account / system / batch (per ENG-498's audit). The coverage warning fires at >5% **per project** to catch regressions, not the org-wide baseline. If a *specific instrumented project* (e.g. Conversation, Memory) climbs above 5%, that's the signal.
- **`OpenAI-Project` header + admin key.** This skill assumes that combination works against `GET /v1/chat/completions`, per ENG-498's acceptance probe. If OpenAI changes the auth model, the skill will fail loudly (4xx surfaced via `_request`) — at which point you'd swap to project-scoped keys.
- **Rate limits.** Org-level endpoints share 60 req/min. The script paginates serially and retries 429 with backoff (max 5).
- **Cowork sandboxes** strip host env (PROD-1305), so `OPENAI_ADMIN_API_KEY` won't reach `mcp__workspace__bash` automatically. Same paste-export workaround as the parent skill. No MCP fallback exists.

## Acceptance criteria mapping

| AC | Where it's implemented |
|---|---|
| Skill exists with `SKILL.md`, `scripts/report.py`, `references/allocation.md` | This file + sibling files |
| `--window month-to-date` produces tier-level tables (both cuts) | `render()` with `dimension="both"` |
| `--window previous-month` works | `resolve_window("previous-month", ...)` |
| `--json out.json` writes structured payload | `main()` → `--json-out` branch |
| `--no-top-users` suppresses ranking | `_render_cut(show_top_users=False)` |
| `--dimension ai\|content\|both` | argparse choice + `render()` filter |
| Coverage warning when >5% untagged per project | `render_coverage_warnings()` |
| PR follows existing pattern | Same shape as `analyze-openai-costs` |
