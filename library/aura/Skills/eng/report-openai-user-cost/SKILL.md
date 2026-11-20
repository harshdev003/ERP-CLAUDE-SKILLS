---
name: report-openai-user-cost
description: >
  Report OpenAI inference cost for ONE specific user (Firebase UID) over a chosen window —
  month-to-date (default), previous-month, last-7d, last-30d, week-to-date, today, or a custom
  range. Point-lookup counterpart to `eng:analyze-openai-cost-per-user`, far faster for one
  user. Auto-discovers Aurie production projects, pulls per-project dollars and tokens from
  the Costs and Usage APIs, and reads the user's token share from the LLM-call ledger, failing
  over to stored completions for older windows. Output is a markdown report with a per-project
  breakdown; `--json` for downstream use. Trigger on "what is user X costing OpenAI", "how
  much did this user cost in OpenAI", "openai cost for user {UID}", "report openai user cost",
  "user-specific openai spend", "cost report for user", or a pasted UID with a request for
  their OpenAI cost. Do NOT trigger for cohort or top-N ranking
  (`eng:analyze-openai-cost-per-user`), project-level totals (`eng:analyze-openai-costs`), or
  daily summaries (`eng:report-openai-costs`).

metadata:
  version: "3.3.1"
knowledge:
  reads:
    - Guide/Index             # baseline routing aid (universal)
    - Tools/OpenAI            # Costs + Usage + Chat Completions API recipes; admin-key auth; the "Cost accounting" rule this skill enforces
    - Products/Aurie     # brand-voice priors for report framing
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Report OpenAI Cost Per User

> **Migrated off Braintrust 2026-06-05** ([PROD-2466](https://linear.app/aura-health/issue/PROD-2466), umbrella [PROD-2456](https://linear.app/aura-health/issue/PROD-2456)). The Braintrust BTQL fallback for pre-2026-05-12 Conversation-project token counts is removed; pre-rollout user share is now extrapolated uniformly across all projects using OpenAI Costs + Usage data only. The skill no longer reads from Braintrust at all.

> **Ledger-first attribution since 2026-07-23** (ENGBACK-1237). Per-user token
> attribution reads the IntelligenceService per-call LLM ledger first
> (BigQuery `aura-main.llm.llm_call` — docs:
> [LLM Calls](https://app.notion.com/p/aurahealth/LLM-Calls-3a675e23af208127b217e53af47524e8)),
> filtered by `userId` + `project` key, with automatic failover to the OpenAI
> stored-chat-completions walk when the ledger is unavailable. The ledger went
> live in Intelligence Service 0.285.0 at **2026-07-23T03:25Z** — window slices before that epoch
> ALWAYS use stored completions (the only pre-epoch source). The ledger covers
> both Chat Completions and Responses API calls; the completions fallback is
> chat-API-only and cannot see Responses-API traffic. Unlike stored completions
> (~30-day retention), the ledger has no retention limit going forward. Dollars
> still come exclusively from the OpenAI Costs API — the ledger only replaces
> the token-weight/attribution walk.

Single-user counterpart of `eng:analyze-openai-cost-per-user`. Use this when
you have **one** Firebase UID in hand and want to know what they cost OpenAI
over a window — not when you're hunting for the top-N spenders across the
whole user base.

Dollars come from OpenAI's Costs API + Usage API exclusively. The skill never
reads Braintrust's `metrics.estimated_cost` (pricing-table gaps + wrong rate
tiers — see AI Knowledge `Tools/Braintrust` → "Cost accounting" for the
historical rationale that motivated this skill's design).

## When to use

Trigger when the user asks any of:

- "what is user `<UID>` costing OpenAI"
- "how much did `<UID>` cost in OpenAI"
- "openai cost for user `<UID>`"
- "report openai user cost"
- "user-specific openai spend"
- "report cost for user `<UID>`"
- pastes a Firebase UID and asks for their OpenAI cost specifically

**Do NOT trigger for:**

- Cohort or top-N ranking → `eng:analyze-openai-cost-per-user`
- Project-level totals (no per-user dimension) → `eng:analyze-openai-costs`
- Daily / weekly summaries → `eng:report-openai-costs` / `eng:analyze-openai-tokens`
- ElevenLabs TTS or content-listen costs → siblings `eng:report-elevenlabs-user-cost` /
  `eng:report-listens-user-cost`
- All-source per-user cost (OpenAI + ElevenLabs + LiveKit + listens combined) → parent
  `eng:report-user-costs`

## Inputs

- `user_id` (required) — Firebase UID. 28 alphanumeric chars typical (e.g.
  `e8XFD61aBaYp5qNryghIHPuij0S2`).
- `--window` (optional, default `mtd`) — one of `today`, `wtd`, `mtd`,
  `prev-month`, `last-7d`, `last-30d`, `yesterday-pt`, `today-pt`. Custom
  range via `--from YYYY-MM-DD --to YYYY-MM-DD`.
- `--by-project` (default on) — show per-project breakdown table. Pass
  `--no-by-project` for total only.
- `--json <path>` (optional) — write structured report (full UID retained)
  to disk for downstream consumers.

## Step 0 — Check prerequisites

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "OK" || echo "MISSING OPENAI_ADMIN_API_KEY"
test -n "$GCP_KEY" && echo "OK" || echo "MISSING GCP_KEY (ledger attribution falls back to stored completions)"
```

`OPENAI_ADMIN_API_KEY` (admin key, `sk-admin-...`, Owner role) is the only
hard-required env var. Same setup as `eng:analyze-openai-costs`; see CLAUDE.md →
"Token inventory".

`GCP_KEY` (the skills service account, as for every BigQuery read) authenticates the
Intelligence Service LLM-call ledger reads (BigQuery `llm.llm_call`) for
post-2026-07-23 attribution. If it's missing the script does NOT crash — it
warns on stderr and falls back to the stored-chat-completions walk, which is
chat-API-only and cannot see Responses-API traffic, so set it for accurate
numbers.

If `OPENAI_ADMIN_API_KEY` is missing, hand off to `update-config`.

**Cowork caveat.** `mcp__workspace__bash` strips host env (PROD-1305). Use the
per-session paste-export workaround in CLAUDE.md → "Cowork constraints", or
run from the host harness.

## Step 1 — Pull AI Knowledge priors

Invoke `shared:retrieve-knowledge` for the paths declared in `knowledge.reads`:

- `Tools/OpenAI` — Costs API + Usage API recipes, admin-key auth, the
  `metadata.user` rollout date (2026-05-12), the canonical "Cost accounting"
  rationale (why we read $ from OpenAI's Costs API, not derived sources).

Keep this in working memory through Steps 2-4 so the rendered report's
**Caveats** block references the right pages.

## Step 2 — Run the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-user-cost/scripts/report.py <UID>
```

Common variants:

```bash
# Default: month-to-date with per-project breakdown
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-user-cost/scripts/report.py <UID>

# Previous calendar month
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-user-cost/scripts/report.py <UID> --window prev-month

# Custom range — the 47-day Apr 11 → May 27 window from the originating session
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-user-cost/scripts/report.py <UID> \
  --from 2026-04-11 --to 2026-05-27

# Total only, no per-project table (compact for orchestrator composition)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-user-cost/scripts/report.py <UID> --no-by-project

# Structured JSON for downstream callers (parent `eng:report-user-costs`)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-openai-user-cost/scripts/report.py <UID> \
  --window last-30d --json /tmp/openai_user.json
```

The script handles: window resolution (via the canonical sibling
`resolve_window.py`), project auto-discovery (`[Production]`-suffixed),
per-project Costs/Usage fetches with pagination + retries, the
ledger-first per-user attribution (Intelligence Service BigQuery `llm.llm_call` for the
post-2026-07-23 slice, with failover to the stored-completions walk;
pre-epoch slices always use stored completions), boundary dedupe on the
OpenAI object id, and the pre-2026-05-12 extrapolation. See
[`references/algorithm.md`](references/algorithm.md)
for the math, assumption surface, and the explicit "what this skill does
NOT do" list.

## Step 3 — Show the report inline

The script prints a single markdown document to stdout. Render it verbatim;
don't paraphrase the numbers. Lead with the one-line headline (total $ over
N days, $/day) and follow with the per-project table when `--by-project`
is on.

Example shape:

```
## OpenAI cost — `e8XFD61a…`  (\$16.62 over 47 days, \$0.35/day)

| Project       | Project $ | User share | User $ |
|---|---:|---:|---:|
| Conversation ¹|  \$3,198.08 |     0.290% |  \$9.28 |
| Memory ¹      |  \$1,527.08 |     0.310% |  \$4.73 |
| Summary ¹     |    \$369.98 |     0.222% |  \$0.82 |
| Journal ¹     |    \$202.56 |     0.355% |  \$0.72 |
| Feed ¹        |    \$526.33 |     0.133% |  \$0.70 |
| Profile ¹     |    \$237.78 |     0.155% |  \$0.37 |
| **Total**     |            |            | **\$16.62** |

**Caveats:**
- Window touches pre-2026-05-12. OpenAI's `metadata.user` tagging shipped that day, so the
  pre-rollout portion lacks per-user attribution. Per-project user share is extrapolated
  from the post-rollout sub-window by `full_window_$ / post_rollout_window_$` (applied
  uniformly to every project including Conversation).
- Attribution source: mixed (window straddles ledger epoch 2026-07-23T03:25Z).

¹ Pre-2026-05-12 user share extrapolated by scaling the post-rollout share by
  `full_window_$ / post_rollout_window_$`.
```

## Step 4 — Compose, route, or return

This skill is **inert by default** — it just returns the report. The caller
decides what to do with it:

- `data:profile-user` and `data:analyze-user-behavior` invoke this for their
  Cost line (replacing the unreliable Braintrust-based number per
  [PROD-2240](https://linear.app/aura-health/issue/PROD-2240)).
- `eng:report-user-costs` (parent, [PROD-2242](https://linear.app/aura-health/issue/PROD-2242))
  composes this with `eng:report-elevenlabs-user-cost`,
  `eng:report-livekit-user-cost` and `eng:report-listens-user-cost` for the
  full all-source per-user cost.
- Direct CLI use for CX inquiries — paste the markdown into a Slack thread
  with `--no-by-project` if you want a compact one-liner.

## Notes & edge cases

**Allocation is service-tier aware, per source** ([ENG-1107](https://linear.app/aura-health/issue/ENG-1107)).
OpenAI prices Flex at exactly **50%** of Standard and Fast (renamed from `priority` on
2026-07-30) at **2×**, so `flex`/`batch` weigh ×0.5, `default`/`auto`/`scale` ×1.0, and
`fast`/`priority` ×2.0. Per-tier list prices live in `Tools/OpenAI` → "Per-tier list prices".

**Weighting is per-source, because only some sources record a tier.** The numerator and
denominator are assembled from several sources:

| Source | Used for | Carries tier? |
|---|---|---|
| LLM ledger rows | numerator, post-epoch | yes (`serviceTier`) |
| Stored completions walk | numerator | yes (`service_tier`) |
| `GET /v1/organization/usage/completions` | denominator, always | yes, when grouped by `service_tier` |

**The denominator never walks the ledger.** The Usage endpoint covers Chat Completions and Responses API calls: measured 2026-10-27, it was within 0.3% of the ledger's input tokens on every IntelligenceService project. Walking every project's ledger rows for the whole window, for every profiled user, is what scaled IntelligenceService to 8 pods in [PROD-7898](https://linear.app/aura-health/issue/PROD-7898). The numerator reads the ledger once per user: one `userId`-filtered walk for the window, capped at `USER_LEDGER_MAX_PAGES`, split by project locally and cached for the run.

The rule: **weight the ledger contribution, and count every non-ledger contribution at face value on both sides.** The denominator's post-epoch slice on IntelligenceService projects is grouped by tier and weighted; its pre-epoch and non-IS slices stay at face value. That keeps each slice
on one basis across numerator and denominator, which is what a ratio requires; weighting a
numerator whose denominator cannot be weighted would under-charge flex users by the size of
the discount.

Two consequences worth knowing. On a window with no ledger coverage the weights equal the raw
counts, so the split is a plain token split. And a null `serviceTier` weighs 1.0 — it means
"not recorded", so it is priced as Standard rather than guessed at.

**The `User share` column is a share of dollars, not of tokens** — it is computed from the
same weights as `User $`, so `share × Project $ = User $` on every row. Raw token counts are
still emitted in `--json` (`user_tokens` / `project_tokens`) for consumers that want volumes.

- **The stored-completions fallback resolves nothing for most projects (measured 2026-08-04).** What matters is whether a project's completions are **stored and listable**, not which API it calls — `Feed` still spends daily but has had no stored completion since **2026-07-23**. On a post-2026-07-23 window the ledger is effectively the only attribution source: every production project except `Conversation` has nothing stored, and `Conversation` itself is only **partially** covered — the `aiResponseAPI` A/B (since 2026-07-31) serves its treatment arm on the Responses API, so one conversation can span both surfaces. If the ledger is unreachable (or `GCP_KEY` is unset) this run does not degrade gracefully: dollars still come from the Costs API, so the report looks plausible while attribution collapses into the untagged bucket. **Treat a post-2026-07-23 run whose ledger call failed as unusable, not degraded** — re-run it rather than reading the numbers. Canonical per-project detail: `Tools/OpenAI` → "Responses API migration — reading stored data"; don't restate it here. [ENG-1049](https://linear.app/aura-health/issue/ENG-1049)
- **A post-2026-07-23 ledger outage now fails closed (exit 2) — [ENG-1054](https://linear.app/aura-health/issue/ENG-1054).** The caveat above says such a run is unusable rather than degraded; this is the code that enforces it, rather than a stderr warning that scrolls past. Pass `--allow-degraded-attribution` (or set `AINATIVE_ALLOW_DEGRADED_ATTRIBUTION=1`) to accept the degraded attribution deliberately. **Unaffected:** pre-epoch slices (the ledger has no rows before 2026-07-23T03:25Z, so stored completions are the correct source), projects the ledger doesn't serve, and the roll-out-ramp union — none of those is a ledger *failure*.

- **Ledger vs stored completions.** Post-2026-07-23T03:25Z attribution reads
  the Intelligence Service LLM ledger first; every report annotates its data source with an
  `Attribution source:` caveat line (`LLM ledger (...)`, `stored chat
  completions (...)`, or `mixed (window straddles ledger epoch
  2026-07-23T03:25Z)`). Windows straddling the epoch merge the two slices with
  defensive dedupe on the OpenAI object id (ledger `openaiId` == stored
  completion `id`).
- **Ledger unavailable (non-200, timeout, missing `GCP_KEY`).** The
  script warns on stderr and falls back to the stored-completions walk for the
  post-epoch slice. The fallback is chat-API-only — it cannot see
  Responses-API traffic, so ledger-era Responses calls are undercounted until
  the ledger is reachable again.
- **Retention.** OpenAI stored completions age out (~30 days); the ledger has
  no retention limit going forward, so post-epoch windows stay attributable
  indefinitely.
- **Non-Intelligence Service projects (Content, Search, Support, …).** They have no ledger
  key, so their attribution stays on the stored-completions walk for the
  whole window.
- **Window entirely before 2026-05-12.** With the Braintrust fallback gone
  (PROD-2466), there's no post-rollout sub-window to extrapolate from — the
  user's $ in every project is reported as `—` and a caveat explains the
  gap. Practical impact today (2026-06-05): only custom `--from`/`--to`
  ranges where both ends precede 2026-05-12 trigger this. Presets like
  `prev-month` always reach into the post-rollout window.
- **User has no completions in any project over the window.** Render
  `\$0.00` with a caveat noting the gap. Distinct from "user doesn't exist
  in OpenAI's records at all" — both render `\$0.00` here. Cross-check
  against the Aura API user record if it matters.
- **Project has $ but no Usage tokens.** The project's spend is reported in
  the row but `User share` is `—` (can't divide by zero). User cost
  contribution from that project is \$0; flagged as an open question in
  the caveats so the reader can investigate (usually a fine-tuning job or
  batch run unrelated to this user).
- **Rate-limit hits.** The script retries with exponential backoff on 429 /
  5xx. If it exhausts retries, it errors out — re-run after a few minutes.
- **Lifetime preset.** Not in v1. Use `--from <createdAt>` with the user's
  creation date from the Aura API. v2 could add the preset with an
  `AURA_API_TOKEN` dependency.

## Out of scope (v1)

- **Per-conversation $ breakdown within a project.** OpenAI's stored chat
  completions metadata doesn't expose a stable conversation ID. Divide
  total by Aurie conversation count if you need a rough per-conversation
  proxy.
- **Cost forecasting, LTV, break-even-on-day-N.** Descriptive only.
- **ElevenLabs / content-listen cost.** Siblings — see PROD-2244 / PROD-2245.
- **Cohort / top-N ranking.** Use `eng:analyze-openai-cost-per-user`.

## References

- [`references/algorithm.md`](references/algorithm.md) — math, the
  2026-05-12 rollout boundary handling, and what this skill explicitly
  doesn't do.
- Existing siblings:
  [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md)
  (rank-N, slower), [`eng:analyze-openai-costs`](../analyze-openai-costs/SKILL.md)
  (project-level), [`eng:report-openai-costs`](../report-openai-costs/SKILL.md)
  (daily snapshot).
- AI Knowledge: [`Tools/OpenAI`](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e) —
  Costs + Usage + Chat Completions API recipes; the canonical
  cost-accounting rule this skill enforces (no `estimated_cost`).
- Linear: [PROD-2466](https://linear.app/aura-health/issue/PROD-2466) (the
  Braintrust-fallback removal under umbrella [PROD-2456](https://linear.app/aura-health/issue/PROD-2456)),
  [PROD-2243](https://linear.app/aura-health/issue/PROD-2243) (this skill's
  original build), [PROD-2242](https://linear.app/aura-health/issue/PROD-2242)
  (parent family), [PROD-2240](https://linear.app/aura-health/issue/PROD-2240)
  (consumer migration off `estimated_cost`).
- ENG-498: `metadata.user` tagging rollout (2026-05-12).
