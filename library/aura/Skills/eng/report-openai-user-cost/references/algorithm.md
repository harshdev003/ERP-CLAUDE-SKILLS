# Algorithm reference — `eng:report-openai-user-cost`

Short technical note on how the per-user cost is computed and the assumptions
each step rests on. Loaded by the SKILL.md on demand.

## The pattern in one line

```
user_$_in_project = (user_tokens / project_tokens) × project_$        (per-project)
user_$_total      = Σ user_$_in_project                               (sum across all production projects)
```

`project_$` and `project_tokens` come from the OpenAI Costs API and Usage API
(authoritative; the Usage API covers Responses-API calls too, so the denominator never reads the ledger). `user_tokens` comes from the IntelligenceService per-call LLM
ledger (BigQuery `llm.llm_call`: one `userId`-filtered walk per run, split by `project` key locally) for the
post-2026-07-23T03:25Z slice, and from filtering OpenAI's stored chat
completions by `metadata[user]=<UID>` for the pre-epoch slice (and as failover
when the ledger is unavailable). The proportion is a per-project
allocation: as long as users in the same project go through the same prompter
chain (and therefore the same model mix), token weights are a faithful proxy
for $ weight.

## The 2026-07-23 ledger epoch (ENGBACK-1237)

Intelligence Service 0.285.0 went live at **2026-07-23T03:25:00Z** (`LEDGER_EPOCH_UNIX =
1784777100`) persisting one row per OpenAI call — both Chat Completions and
Responses APIs — in the `llmCall` table, exposed via authenticated
BigQuery `aura-main.llm.llm_call` (`GCP_KEY`). For any window `[start, end)`:

- `slice_before = [start, min(end, epoch))` — always the stored-completions
  walk; the ledger has NO rows before the epoch.
- `slice_after = [max(start, epoch), end)` — ledger primary
  (`inputTokens`/`outputTokens` map to `prompt_tokens`/`completion_tokens`).
  On ledger failure (non-200, timeout, parse error, missing token) the script
  warns on stderr and falls back to the stored-completions walk for that
  slice — a chat-API-only view that cannot see Responses-API traffic.
- Merged results dedupe defensively on the OpenAI object id (ledger
  `openaiId` == stored completion `id`).
- Non-Intelligence Service `[Production]` projects (Content, Search, Support, …) have no
  ledger key and stay on the stored-completions walk throughout.

Why the ledger is primary post-epoch: it sees Responses-API traffic the
stored-completions endpoint can't, it has no ~30-day retention limit, and it
filters server-side by user + project + time (no client-side paging of the
whole project's completions). The report's Caveats always carry an
`Attribution source:` line naming which source(s) produced the numbers.

## Why $ comes from the Costs API, not derived sources

Reading dollars from the OpenAI **Costs API** (the actual billing source of
truth) sidesteps three failure modes of derived approaches like
Braintrust's `metrics.estimated_cost`:

1. **Pricing-table gaps** for new model versions — e.g. `$0.00` observed
   for `gpt-5.2-chat-latest` from 2026-04-11 through 2026-05-18 on live
   Braintrust spans. The Costs API has no such gap.
2. **Wrong rate tier** — Braintrust attributed gpt-5-mini rates on what was
   actually a more expensive tier in the same case. The Costs API ÷ Usage
   API ratio is empirical.
3. **Per-span sums miss the multi-project picture** — IntelligenceService
   writes to 18 production projects per Aurie interaction (Conversation,
   Memory, Summary, Feed, Journal, Profile, …). Summing only Conversation
   spans misses ~45% of an active user's true cost.

The canonical write-up of this rule lives on AI Knowledge `Tools/Braintrust`
→ "Cost accounting (don't trust estimated_cost)" — kept there as historical
record of why we don't read from Braintrust here, even though this skill
itself no longer touches Braintrust at all (PROD-2466, 2026-06-05).

## The 2026-05-12 rollout boundary

[ENG-498](https://linear.app/aura-health/issue/ENG-498) shipped on
2026-05-12 to start writing the Firebase UID as `metadata.user` on every
OpenAI Chat Completions request. Before that date, OpenAI's stored
completions don't carry the user tag — so the `metadata[user]=<UID>` filter
returns zero rows for those spans even when the user was active.

Two cases this skill handles:

### Window entirely on or after 2026-05-12

Straightforward. No fallback needed. The metadata filter captures everything;
the Costs API and Usage API give project denominators over the same window.

### Window straddles or precedes 2026-05-12

The pre-rollout portion has no per-user OpenAI-side attribution. The skill
extrapolates **uniformly across all projects** (Conversation included) by
scaling the user's post-rollout share to the full window:

```
share_post_rollout = user_tokens_post / project_tokens_post
user_$_extrapolated_full = share_post_rollout × project_$_post × (project_$_full / project_$_post)
                         = share_post_rollout × project_$_full
```

I.e. assume the user's share in the post-rollout subset is representative
of their share in the full window, and apply that share to the full-window
project $.

This is flagged in the report's **Caveats** so a reader can sanity-check
whether the assumption holds for the user's activity pattern.

**Why uniform extrapolation across all projects** (history). The previous
version of this skill applied a Braintrust BTQL fallback to the Conversation
project — reading per-user token sums directly via `metadata.userId` on BT
spans, which predate the OpenAI rollout. As of PROD-2466 (2026-06-05) that
fallback is removed: the skill no longer reads Braintrust at all, and the
Conversation project gets the same proportional extrapolation as the rest.
For windows that span only a few pre-rollout days, the difference is
negligible; for windows that reach deep into pre-rollout, expect a wider
band of uncertainty on the Conversation row specifically (the BT-backed
number was closer to ground truth there).

## What the skill explicitly does NOT do

- **Per-conversation $ breakdown within a project.** OpenAI's stored chat
  completions metadata doesn't expose a stable conversation ID, so there's
  no way to roll completions up to one Aurie conversation. If you need
  per-conversation cost, divide the OpenAI project's total $ for that
  conversation's window by the user's conversation count (rough proxy).
- **Tier resolution.** The sibling `eng:analyze-openai-cost-per-user`
  annotates each top row with last-seen `aiPremium` / `premiumState` tier.
  This skill omits that — for a single-user lookup, the consumer (e.g.
  `data:profile-user`) already has the user's tier from the Aura API
  `/users/<UID>` call.
- **Cost forecasting / unit economics.** Descriptive only.
- **Across-org allocation.** This is OpenAI cost only. ElevenLabs TTS is
  handled by `eng:report-elevenlabs-user-cost`; content-listen coach
  payout by `eng:report-listens-user-cost`; LiveKit-path voice by
  `eng:report-livekit-user-cost`. The parent `eng:report-user-costs`
  composes all four.

## Production projects this skill iterates

Auto-discovered via `/v1/organization/projects` — every project whose name
ends in `[Production]`. Today's set (**18**, as of 2026-08-16):

```
Content, Conversation, Evaluations, Feed, Guardrail, Insight, Insights,
Intent, Journal, Memory, Notification, Onboarding, Profile, Search,
Suggestions, Summary, Support, Translation
```

`Insight` (dead singular) and `Insights` are both present and both walked.

New `[Production]`-suffixed projects added in the future are picked up
automatically. `[Development]` projects are excluded (no real-user traffic);
non-suffixed projects (Default, Front, Lovable, etc.) are also excluded.

## Rate limits and runtime

OpenAI's org-level endpoints share a 60 req/min limit. The skill paginates
serially with exponential backoff on 429/5xx. Typical wall-clock:

- **18** production projects (2026-08-16) × ~3 API calls each (costs +
  usage + completions) = ~54 calls
- Plus one `project_totals` call per project (costs + usage over the
  post-rollout sub-window) when the window straddles 2026-05-12, for
  per-project extrapolation
- **~9 minutes** end-to-end for a 30-day window (measured 8.8 min,
  2026-08-16); faster for shorter windows because the user has fewer
  completions to page through.

**This figure tracks the project count, so re-measure rather than trust
it.** The loop is serial over auto-discovered `[Production]` projects, so
every project added to the OpenAI org lengthens the walk by its full
share, with nothing here to notice. Any timeout sized off the number above
should be re-derived when the project count changes — a timeout that fires
on an ordinary run is indistinguishable from a hang.

Compare to `eng:analyze-openai-cost-per-user`'s ~30 min — the metadata-user
filter is the load-bearing optimization. Don't reach for the ranking
skill when you only need one user's number.
