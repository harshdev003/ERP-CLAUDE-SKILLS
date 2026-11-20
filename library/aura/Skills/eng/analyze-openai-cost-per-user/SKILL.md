---
name: analyze-openai-cost-per-user
description: >
  Rank individual Aura users by OpenAI cost — a "who's costing us the most" view,
  complementary to `eng:analyze-openai-costs` (by project) and
  `eng:analyze-openai-cost-per-subscription-state` (by state). Pulls authoritative per-project
  dollars from the Costs API, then allocates them across users by token weight, reading the
  IntelligenceService LLM-call ledger first (it covers both Chat Completions and Responses)
  with automatic failover to stored chat completions for older windows. Each top user is
  annotated with their AI premium and content premium tier. Output includes a
  distribution-stats block (mean / median / p90 / p95 / p99 / max / stddev) so the headline
  frames top spenders against the long tail. `--top N` sets the cutoff; `--tier {Label}`
  filters to one tier. Trigger on "cost per user", "spend per user", "openai top spenders",
  "who's costing us the most on openai", "openai runaway users", "expensive users", "openai
  cost outliers", "highest openai spend by uid", or "openai user cost distribution".

metadata:
  version: "3.4.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/OpenAI  # three-state semantic on premium booleans + per-model rates
    - Tools/Slack   # channel directory + Post format footer spec for #eng-budget post
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Analyze OpenAI Cost Per User

Sibling to [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md) (by project) and [`eng:analyze-openai-cost-per-subscription-state`](../analyze-openai-cost-per-subscription-state/SKILL.md) (by subscription state). Same authoritative dollar source — the OpenAI Costs API at `/v1/organization/costs` — split a third way: by individual user.

For each project with non-zero spend, the skill collects that project's per-call records and groups by user (Firebase UID). **Attribution reads the IntelligenceService LLM-call ledger first** (BigQuery `aura-main.llm.llm_call` — one row per OpenAI call, covering both the Chat Completions and Responses APIs; docs: [LLM Calls](https://app.notion.com/p/aurahealth/LLM-Calls-3a675e23af208127b217e53af47524e8)), **with automatic failover to the OpenAI stored chat completions walk** (`GET /v1/chat/completions` with an `OpenAI-Project` header, `metadata.user` tagged by [ENG-498](https://linear.app/aura-health/issue/ENG-498)). The ledger went live at **2026-07-23T03:25Z** (Intelligence Service 0.285.0): windows (or window slices) before that moment always use stored completions — there are no ledger rows before the epoch — and post-epoch slices use the ledger unless it's unreachable, in which case the script warns on stderr and falls back (note: the fallback is chat-API-only and cannot see Responses-API traffic). The rendered report annotates which source(s) were used via an `Attribution source:` line. The project's $ from the Costs API is allocated across its users proportional to their token weights. Across-project totals are summed per UID, then ranked.

Each top row is annotated with the user's **last-seen** AI premium tier (`metadata.aiPremium` + `metadata.aiPremiumState`) and content premium tier (`metadata.premium` + `metadata.premiumState`) — current state, not a per-call average. See [`references/allocation.md`](references/allocation.md) for why and the other caveats.

**Tier values** are dimension-specific, same set as `eng:analyze-openai-cost-per-subscription-state`:

- **AI dimension (Aurie):** `AI Premium Paid` / `AI Premium Trial` / `AI Premium Free` / `AI Premium Payment Pending` / `Non AI Premium` / **`Never AI Premium`** / `(untagged)`. `Never AI Premium` is the bucket for users whose `aiPremium` column is NULL in the IntelligenceService `"user"` table (declared `BOOLEAN` nullable, no default — never had an AI subscription event), distinct from `(untagged)` which means no `metadata.user` was stamped at all (pre-account / system / batch traffic).
- **Content dimension (Aura):** `Premium Paid` / `Premium Trial` / `Premium Free` / `Premium Payment Pending` / `Non Premium` / `(untagged)`. No `Never Premium` bucket — the `premium` column is `BOOLEAN DEFAULT FALSE` in the DB, which flattens never-vs-lost upstream.

The AI-dimension labels carry an "AI " prefix to disambiguate from the content (Aura) labels: plain "Premium Paid" refers to the Aura content subscription, "AI Premium Paid" to the Aurie AI-features subscription. The two are distinct subscriptions with different price points and different cohorts. See [`Tools/OpenAI`](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e) → "Three-state semantic on the booleans" for the canonical write-up, and [ENGBACK-1083](https://linear.app/aura-health/issue/ENGBACK-1083/premium-columns-should-not-default-to-false-collapses-never-premium) for the upstream schema fix that will give the content dimension the same three-state semantic.

The report leads with a distribution-stats block (count, mean, median, p90, p95, p99, max, stddev) so the reader can judge whether the top spenders are anomalous or just the long tail of a normal SaaS power-law.

## Step 0 — Check prerequisites

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "OK: admin key set" || echo "MISSING admin key"
test -n "$GCP_KEY" && echo "OK: GCP_KEY set" || echo "MISSING GCP_KEY (ledger path will fall back to stored completions)"
```

Same admin-key setup as the sibling skills — `sk-admin-...`, Owner role, stored in `~/.claude/settings.json` `env` block, restart Claude Code. Hand off to `update-config` if it's missing.

`GCP_KEY` (the skills service account) is needed for the primary attribution path — the Intelligence Service LLM-call ledger in BigQuery. If it's unset the script doesn't crash: it warns on stderr and falls back to the stored-chat-completions walk for the post-epoch slice (which cannot see Responses-API traffic, so attribution coverage degrades).

Other prereqs: `python3` with `requests` (`pip3 install requests` if missing). No numpy / pandas — distribution stats use the Python stdlib.

**Retention.** OpenAI keeps `store: true` chat completions ~30 days — this applies to pre-epoch windows and to the fallback path. Requests for `previous-month` near the end of the next month may see partial coverage; the Costs API totals are still correct, only per-user attribution degrades (more $ falls to `untagged_amount`). The LLM ledger has no such retention limit going forward — post-2026-07-23 attribution stays queryable indefinitely.

## Step 1 — Resolve the time window

Same presets as the siblings:

- **Month-to-date** (default) — 1st of current calendar month UTC → now
- **Previous month** — full prior calendar month (UTC)
- **Last 7 days** — rolling 7d ending now
- **Last 30 days** — rolling 30d ending now
- **Custom range** — explicit `--since YYYY-MM-DD --until YYYY-MM-DD` (UTC, half-open)

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-user/scripts/report.py \
  --window month-to-date \
  --json /tmp/openai_cost_per_user.json
```

Common variants:

```bash
# Default: top 5 + stats + headline
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-user/scripts/report.py

# Deeper rank cutoff
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-user/scripts/report.py --top 25

# Filter to one tier — AI dimension (Aurie). The "AI " prefix tells the
# script to match against the user's last-seen aiPremiumState.
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-user/scripts/report.py --tier "AI Premium Free"

# Filter to one tier — content dimension (Aura). No prefix = content side.
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-user/scripts/report.py --tier "Premium Free"

# Just the ranking, no stats / no headline (compact for follow-up)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-user/scripts/report.py --no-stats --no-headline

# Previous calendar month
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-user/scripts/report.py --window previous-month

# Custom window
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-openai-cost-per-user/scripts/report.py \
  --since 2026-04-01 --until 2026-05-01
```

Flags:

| Flag | Default | Effect |
|---|---|---|
| `--top N` | 5 | Rank cutoff on the table. Stats are computed over the full population (or the filtered tier if `--tier` is set), not just the top N. |
| `--tier "<Label>"` | none | Restrict ranking + stats to one tier. Dimension is inferred from the label: AI-prefixed labels (`AI Premium Paid`, `AI Premium Trial`, `AI Premium Free`, `AI Premium Payment Pending`, `Non AI Premium`, `Never AI Premium`) filter on the user's last-seen AI tier; unprefixed labels (`Premium Paid`, `Premium Trial`, `Premium Free`, `Premium Payment Pending`, `Non Premium`) filter on the content tier; `(untagged)` matches users whose AI or content tier is untagged. |
| `--no-stats` | off | Suppress the distribution block. |
| `--no-headline` | off | Suppress the "Headline outliers" paragraph. |
| `--json out.json` | n/a | Write the full structured payload (window, total, stats, `top_users`, `users_all`) to disk. `users_all` is the complete ranked list — see "JSON shape" below. |

JSON shape:

```json
{
  "window": { "label": "...", "start_unix": ..., "end_unix": ..., "start_iso": "...", "end_iso": "..." },
  "total": 1832.41,
  "tagged_amount": 1740.34,
  "untagged_amount": 92.07,
  "tier_filter": null,
  "stats": {
    "count": 4200, "mean": 0.41, "median": 0.08,
    "p90": 1.20, "p95": 2.85, "p99": 12.40, "max": 40.18, "stddev": 1.92
  },
  "top_users": [
    {
      "uid": "Za0DlgYucXRZduFOOIHKvjPbymV2",
      "amount": 40.18,
      "share": 2.3,
      "ai_tier": "AI Premium Free",
      "content_tier": "Premium Free",
      "top_project": "Conversation",
      "completion_count": 412
    }
  ],
  "users_all": [
    "... same row shape as top_users, but the full ranked list (all tagged users) regardless of --top ..."
  ]
}
```

UIDs are shortened (`first8…last4`) in the rendered markdown table only. The JSON output keeps the full UID so it's grep-able and join-able.

## Step 3 — Show the report inline

Lead with a one-line headline that names the top spender, their tier, and the multiple over the median. Example:

> _Top OpenAI spender this month: an AI Premium Free user costing \$40.18 — ~490× the median user, 2.3% of org spend. 2 of the top 5 are AI Premium Free (comped Aurie subscription, paying \$0 for AI). Distribution otherwise looks like a normal SaaS power-law._

Then show the script's stdout verbatim. The "Headline outliers" section already calls out the actionable patterns (AI Premium Free in the top set, Non AI Premium in the top set, single-project dominance, top user as Nx the median) — don't paraphrase them, just frame them up. When citing a tier in the headline, name the dimension explicitly: "AI Premium Free" for Aurie, "Premium Free" for Aura.

## Step 4 — Ask where to route it

Same routing options as the sibling skills. **One difference: the top-user data is user-identifiable** (Firebase UIDs are PII-adjacent). Default routing is:

1. **Post a summary to Slack** *(optional, private channels only)* — post to `#eng-budget` (`C05PC18APLH`, private — canonical ID in `Tools/Slack`). **Do NOT default to general / public channels** — the report contains UIDs that haven't been agreed for public sharing. Use `--no-stats` for the Slack version so the message stays under one screen; the stats block reads better inline in chat. Post via `shared:post-to-slack` with `--footer eng:analyze-openai-cost-per-user --footer-extra prod` so it stamps the versioned footer — don't hand-type one in the body.
2. **Save a Markdown summary** to a user-provided path (local file is fine).
3. **Nothing, just show it** — stop here.

Always wait for the user's pick.

## Slack message format

Compact — designed to fit one Slack message:

```markdown
**OpenAI Spend by User — <window label>**

- **Total tagged:** $<tagged> across <N> users ($<mean>/user mean, $<median>/user median)
- **Top user:** $<amount> (~<Nx>× median, <pct>% of org spend) — <ai_tier> / <content_tier>, mostly in <top_project>

**Top 5**

| User | Spend | Tier | Top project |
|---|---:|---|---|
| `<short_uid>` | $X | <ai_tier> | <project> |
| ... |

**Headline**

<one paragraph: which tier the top users are in (name the dimension — "AI Premium Free" vs "Premium Free"), whether any are AI Premium Free (comped Aurie access, paying \$0 for AI), whether the distribution is anomalously wide vs. the team's calibration, follow-up suggestions if a single user is concentrated in one project>
```

Drop the stats block for Slack — the "mean / median / top is Nx median" in the headline already conveys the shape; the full percentile table reads better in chat where vertical space is cheap.

## Notes & edge cases

**Allocation is service-tier aware** ([ENG-1106](https://linear.app/aura-health/issue/ENG-1106)).
Each project's dollars are split by token weight scaled by the ledger's `serviceTier`:
OpenAI prices Flex at exactly **50%** of Standard and Fast (renamed from `priority` on
2026-07-30) at **2×**, so `flex`/`batch` weigh ×0.5, `default`/`auto`/`scale` ×1.0, and
`fast`/`priority` ×2.0. Per-tier list prices live in `Tools/OpenAI` → "Per-tier list prices".

Without this, a user whose calls ran on flex absorbs twice the dollars they actually cost.
The skew needs tiers to mix inside one project — which happens both because different
prompters run different tiers and because a flex request falls back to `auto` under
capacity pressure. A null `serviceTier` (rows before the
[ENGBACK-1315](https://linear.app/aura-health/issue/ENGBACK-1315) deploy) weighs 1.0, so a
window straddling the deploy degrades toward the old numbers rather than skewing.

- **The stored-completions fallback resolves nothing for most projects (measured 2026-08-04).** What matters is whether a project's completions are **stored and listable**, not which API it calls — `Feed` still spends daily but has had no stored completion since **2026-07-23**. On a post-2026-07-23 window the ledger is effectively the only attribution source: every production project except `Conversation` has nothing stored, and `Conversation` itself is only **partially** covered — the `aiResponseAPI` A/B (since 2026-07-31) serves its treatment arm on the Responses API, so one conversation can span both surfaces. If the ledger is unreachable (or `GCP_KEY` is unset) this run does not degrade gracefully: dollars still come from the Costs API, so the report looks plausible while attribution collapses into the untagged bucket. **Treat a post-2026-07-23 run whose ledger call failed as unusable, not degraded** — re-run it rather than reading the numbers. Canonical per-project detail: `Tools/OpenAI` → "Responses API migration — reading stored data"; don't restate it here. [ENG-1049](https://linear.app/aura-health/issue/ENG-1049)
- **A post-2026-07-23 ledger outage now fails closed (exit 2) — [ENG-1054](https://linear.app/aura-health/issue/ENG-1054).** The caveat above says such a run is unusable rather than degraded; this is the code that enforces it, rather than a stderr warning that scrolls past. Pass `--allow-degraded-attribution` (or set `AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1`) to accept the degraded attribution deliberately. **Unaffected:** pre-epoch slices (the ledger has no rows before 2026-07-23T03:25Z, so stored completions are the correct source), projects the ledger doesn't serve, and the roll-out-ramp union — none of those is a ledger *failure*.

- **Numbers may lag the dashboard by up to 24h** — same caveat as the sibling skills.
- **Read `attribution.materiality`, never `attribution.sources`, to judge whether a run is usable ([ENG-1114](https://linear.app/aura-health/issue/ENG-1114)).** `sources` is a set of markers, so the only test it supports is "is this string present" — which cannot tell one cent of non-ledger spend from two hundred dollars. The `--json` block now also carries `materiality`: `dollars` and `shares_pct` per attribution class, plus the two numbers worth gating on — `degraded_share_pct` (spend split by a stored-completions **fallback**, i.e. after a ledger read failed) and `non_ledger_share_pct` (spend in projects the ledger never serves). Gate on `degraded_share_pct`. The rendered note reports both figures inline.
  - **The two non-ledger conditions are not interchangeable, and the note no longer implies they are.** `completions-fallback` is an outage; `completions-non-ledger` is a permanent property of the project (the *Unaffected* clause above). The post-epoch branch of `build_attribution_note` used to emit the "ledger unavailable" wording for either one, so a run whose only non-ledger spend was `Front` at **\$0.01** announced an outage — and on 2026-08-09 a perfectly good day (Conversation \$154.92, Insights \$102.80, Memory \$54.81, Profile \$20.33, … — \$350.11 of \$350.12 ledger-attributed) was withheld from `aurie_per_user_daily_cost` on the strength of that sentence. Behaviour pinned by [`tests/test_attribution_materiality.py`](../../../../tests/test_attribution_materiality.py).
- **`materiality` answers "was this the right source"; `attribution.coverage` answers "did it actually attribute" ([ENG-1120](https://linear.app/aura-health/issue/ENG-1120)).** They fail independently, so read both. A walk can use exactly the right source for the window and still resolve nothing — and when it does, `degraded_share_pct` stays **0.00%**: a pre-epoch window legitimately uses stored completions (no ledger existed), so there is no `completions-fallback` marker, and if the walk resolves zero users there is no degraded *share* either. `coverage` reports `coverage_pct` (the share of **walkable** spend that landed on a real UID), `reached`/`unreached`/`eligible_dollars`, `users_with_cost`, and `skipped_dollars` (dev + internal projects, excluded from the ratio by design — counting them would make a healthy day look broken). Gate on `coverage_pct`. The rendered note carries the figure on **every** branch, including pre-epoch, and says `ATTRIBUTION COLLAPSE` below 90%.
  - **This is not hypothetical, and it is silent by construction.** On 2026-06-24 and 2026-06-25 the walk resolved **0** users, \$144.88 and \$151.00 of LLM spend landed entirely on the store's `Other` reconciling row, voice attribution was intact, and nothing errored — so no total was wrong and only the *distribution* was empty, which is precisely what the per-user readers consume. Both days were persisted. Behaviour pinned by [`tests/test_attribution_coverage.py`](../../../../tests/test_attribution_coverage.py), whose load-bearing case asserts the ENG-1114 guard **accepts** a collapsed day.

- **Internal-tooling projects are excluded from per-user attribution ([ENG-1044](https://linear.app/aura-health/issue/ENG-1044)).** `Evaluations` is the prompt engineer's workspace, so its completions carry whatever UID the eval fixture used — attributing them would book prompt-engineering spend onto a real user's Aurie cost. It is skipped the same way a `[Development]` project is: **its dollars still count in the org day total** (which must reconcile to the real OpenAI bill) and fold into the untagged bucket, they just never reach a user. The exclusion list is `INTERNAL_PROJECT_BASE_NAMES` in `scripts/report.py`, matched on the qualifier-stripped base name so both env twins are covered. Add a project there when its spend is internal tooling rather than cost-to-serve — and note the effect is invisible in a day total, so a wrongly-included project shows up only as an unexplained spike on one UID.
- **Ledger roll-out ramp (2026-07-23T03:25Z → 2026-07-24T00:00Z).** Within this window the ledger is only **partially populated** (~3× thin across every project — 701 distinct users vs 2,549 the following day), so it is treated as a *supplement* to stored chat completions rather than a replacement: the script fetches both and unions them, deduped on `id`. Stored completions cover this window because the Responses-API migration (0.287.x) shipped after the ledger release, so the traffic was still overwhelmingly Chat Completions. The union matters because per-user cost is an allocation of the authoritative day total by token share — a thin user set doesn't lose dollars, it re-spreads the full day across the users that survive, overstating each. The union is scoped to the ramp, so steady-state runs keep a single source and don't pay for a second walk per project per day. `ledger-ramp-union` appears in `attribution.sources` when it applies; `ledger-ramp-union-partial` marks a project whose completions walk failed, leaving it ledger-only and likely overstated.

  **2026-07-23 cannot be fully reconstructed even with the union.** That day resolves ~1,737 users against ~2,550 on adjacent days (mean \$0.149/user vs ~\$0.10), leaving per-user figures there roughly 1.5× overstated. The cause is structural: a prompter that had already moved to the Responses API *before* the ledger finished ramping is invisible to both sources — stored completions never see Responses traffic, and the ledger had not yet recorded it. `Notification [Production]` is the clear case (4,143 completions / 2,106 users on 07-22; 70 pre-epoch on 07-23, then zero, against only 562 ledger users). Those calls exist in no queryable source and are permanently unrecoverable. **Treat 2026-07-23 per-user cost as a floor, not a measurement**, and prefer excluding that date from cohort/concentration analyses rather than reading it at face value. Day totals for it are unaffected and remain authoritative.
- **Ledger epoch + failover.** The Intelligence Service LLM-call ledger went live at 2026-07-23T03:25Z (Intelligence Service 0.285.0). The script splits the requested window at that epoch: the pre-epoch slice always walks stored chat completions; the post-epoch slice reads the ledger (project key derived from the OpenAI project name — `Memory [Production]` → `memory`; `[Development]` projects are skipped) and falls back to stored completions with a stderr warning on any ledger failure. Merged results are deduped on the OpenAI object id (`openaiId` == stored completion `id`). The report header and the JSON `attribution` block state which source(s) were used. Ledger tier labels come from the `aiPremium` / `aiPremiumState` (and `premium` / `premiumState`) columns — the booleans were added 2026-07 (ENGBACK-1237) so a churned user (`aiPremium=false`) stays "Non AI Premium" instead of collapsing into "Never AI Premium"; rows predating that column carry a null boolean and fall back to state-only inference, and a null state gets the same untagged/absent handling as missing completion metadata. Dollar methodology is unchanged — the Costs API stays the $ authority; the ledger only replaces the token-weight attribution walk.
- **`metadata.user`** is the Firebase UID, set on every user-bearing completion by Aura's instrumented prompters (ENG-498's audit); on the ledger it's the top-level `userId` column. Pre-account / system / batch traffic legitimately omits it and lands in `untagged_amount`. The skill never calls the Aura user DB to resolve UID → email / name; that join is the consumer's job.
- **Tier resolution is last-seen.** For each user, the most recent completion's `aiPremiumState` / `premiumState` wins. This matches the unit-economics framing (current state of the user) rather than averaging across mid-window upgrades. Documented in [`references/allocation.md`](references/allocation.md).
- **Rollout-window caveat.** The `aiPremium*` / `premium*` metadata tagging ([ENG-498](https://linear.app/aura-health/issue/ENG-498) / [ENG-500](https://linear.app/aura-health/issue/ENG-500)) shipped on **2026-05-12**. Calls stored before that date carry none of the four subscription-state fields — they pre-date the code that writes them. When the requested window predates or straddles that boundary, the script prints a `:warning:` banner at the top of the rendered report and a stderr line, and sets `rollout.window_status` in the JSON to `pre` / `mixed` / `post`. Don't read the `(untagged)` slice in a `pre` or `mixed` window as a tagging regression — it's the rollout window. For clean tier breakdowns, wait until the requested window is entirely on or after 2026-05-12. Full caveat lives on [`Tools/OpenAI`](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e) → "Rollout date and the data gap".
- **Distribution method.** Percentiles use `statistics.quantiles(..., method="inclusive")` because we have the entire population of users in the window — not a sample. This also guarantees p99 ≤ max, avoiding "p95 > max" rendering artifacts on small populations. Stddev is `statistics.stdev` (sample, n-1).
- **Stats are over post-allocation dollars**, not raw token counts. Population is tagged users only — the `untagged_amount` is reported as a separate line in the header but doesn't enter the distribution.
- **`--tier` filter scopes everything**, including the stats. "Top 5 AI Premium Free spenders + their cost distribution" is one flag. The dimension is inferred from the label prefix: AI-prefixed labels match `ai_tier`, unprefixed labels match `content_tier`, `(untagged)` matches either. The non-filtered tier shows up only as an annotation on the top rows.
- **JSON carries the full ranked list (`users_all`), not just `top_users`.** Markdown rendering respects `--top` (default 5); JSON always writes every tagged user in rank order so deeper cuts ("top 20", "top 50", "everyone above \$5") can be extracted with `jq` or a small Python read instead of re-running the script (the per-completion iteration takes ~30 min on month-to-date and is the entire cost; rank cutoff is a render-time decision only). Example: `jq '.users_all[:20]' < out.json` gives the top 20 without a re-run.
- **No Gini coefficient or Lorenz curve in v1** — concentration is the interesting story but those need a chart to be useful, and we're markdown-only. Mean / median / p99 / max + "Nx the median" cover most of the signal. Revisit when chart output is on the table.
- **Privacy posture.** UIDs are not PII on their own (they're Firebase identifiers, not emails) but they are user-identifiable in the Aura DB. Treat the report as confidential — Slack-post to private channels only, don't paste full UIDs into public docs without scoping.
- **`OpenAI-Project` header + admin key.** Same auth model as the sibling skills.
- **Rate limits.** Org-level endpoints share 60 req/min; the script paginates serially and retries 429 with backoff.
- **Cowork sandboxes** strip host env (PROD-1305) — no MCP fallback; user must paste-export `OPENAI_ADMIN_API_KEY` per session.

## Acceptance criteria mapping (from ENG-503)

| AC | Where it's implemented |
|---|---|
| Skill exists with `SKILL.md`, `scripts/report.py`, `references/allocation.md` | This file + sibling files |
| `--window month-to-date` produces stats + top-N | `render()` |
| `--window previous-month` works | `resolve_window("previous-month", ...)` |
| `--top N` flag, default 5 | argparse + `DEFAULT_TOP_N` |
| `--tier <Label>` filters ranking + stats | `render()` filters; `compute_stats` runs over filtered population |
| Stats: count, mean, median, p90, p95, p99, max, stddev | `compute_stats()` |
| `--json out.json` writes structured payload | `main()` → JSON branch |
| `--no-stats` suppresses distribution block | `render(show_stats=False)` |
| `--no-headline` suppresses outliers paragraph | `render(show_headline=False)` |
| UID shortening `first8…last4` in markdown, full in JSON | `_short_uid()` + JSON keeps raw `uid` |
| Skill does NOT call the Aura user DB | The script calls only OpenAI APIs plus the authenticated Intelligence Service `llmCall` ledger (BigQuery `llm.llm_call`) endpoint — no direct DB access, no UID → email resolution |
