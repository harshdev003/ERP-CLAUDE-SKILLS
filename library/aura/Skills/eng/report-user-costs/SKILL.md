---
name: report-user-costs
description: >
  Report ALL-source Aura cost for ONE specific user (Firebase UID) over a chosen window —
  OpenAI inference plus ElevenLabs Conversational AI plus LiveKit-path voice plus Aura
  content-listen coach-payout, summed into a single figure with a per-source breakdown table.
  Composes the four per-source `eng:report-*-user-cost` skills. Windows: month-to-date (default),
  previous-month, last-7d, last-30d, week-to-date, today, yesterday-pt, today-pt, or custom.
  Output carries the all-source total, a per-source table and each source's caveats. Trigger
  on "what is user X costing Aura", "total cost for user {UID}", "how much is this user
  costing us", "all-in cost for a user", "report user costs", "user cost report", or a pasted
  UID with a request for the full cost picture. Also triggers on "everything we're spending on
  this user". Do NOT trigger for single-source lookups (use the sub-skills directly), cohort
  or top-N ranking (the `analyze-*-cost-per-user` family), or project-level totals (the
  `analyze-*-cost` family).

metadata:
  version: "3.1.2"
knowledge:
  reads:
    - Guide/Index             # baseline routing aid (universal)
    - Products/Aurie     # brand-voice priors for the synthesis section
    - Tools/OpenAI                   # OpenAI project keys + stored-completions access
    - Tools/ElevenLabs                   # ElevenLabs API access (ELEVENLABS_API_KEY)
    - Tools/LiveKit                  # LiveKit-path voice vendors
    - Tools/BigQuery                 # per-user daily cost store (GCP_KEY)
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Report User Costs (all sources)

Single-user all-in cost across the four Aura cost sources that scale
with usage:

- **OpenAI** — LLM inference. **18** `[Production]` projects as of
  2026-08-16 (Conversation, Memory, Summary, Feed, Journal, Profile,
  Intent, Search, Suggestions, Insight, Onboarding, Notification,
  Guardrail, Translation, …). Auto-discovered, so the count grows on its
  own — and the walk is serial over it, so runtime grows with it.
- **ElevenLabs** — Conversational AI (the ConvAI agent voice path).
- **LiveKit** — the LiveKit agent voice path: LiveKit minutes, Cartesia /
  Fish / ElevenLabs TTS on LiveKit sessions, Deepgram STT. Read from the
  per-user daily cost store in BigQuery, so days not yet stored are
  `pending`.
- **Listens** — Aura content-listen coach payout. PaymentService's
  authoritative `/payouts/listens/aggregate` rates.

Each sub-skill returns its own focused report; this orchestrator
synthesises them into one all-source picture with a single top-line $
figure and a per-source breakdown. Designed for the CX-inquiry,
save-attempt, and `data:profile-user` Cost-line use cases that
previously surfaced unreliable Braintrust `estimated_cost` numbers (see
[PROD-2240](https://linear.app/aura-health/issue/PROD-2240) for the
deprecation context; AI Knowledge `Tools/Braintrust` →
"Cost accounting" for the canonical rule).

## When to use

Trigger when the user asks any of:

- "what is user `<UID>` costing Aura"
- "total cost for user `<UID>`"
- "how much is this user costing us"
- "all-in cost for a user"
- "report user costs"
- "user cost report"
- pastes a Firebase UID and asks for "the full cost picture", "everything
  we're spending on this user", or "all-source cost"

**Do NOT trigger for:**

- Single-source lookups — use the sub-skill directly:
  - `eng:report-openai-user-cost` for LLM only
  - `eng:report-elevenlabs-user-cost` for ElevenLabs ConvAI voice only
  - `eng:report-livekit-user-cost` for LiveKit-path voice only
  - `eng:report-listens-user-cost` for content-listen payout only
- Cohort or top-N ranking — use the per-source `analyze-*-cost-per-user`
  rank-N family (`eng:analyze-openai-cost-per-user`, etc.).
- Project-level / workspace-level totals — use the per-source `analyze-*-cost`
  family (`eng:analyze-openai-costs`, `eng:analyze-aura-cost`,
  `eng:analyze-elevenlabs-costs`).
- All-user combined cross-source rankings — `eng:report-top-conversation-users`
  covers that pattern at the cohort level.

## Inputs

- `user_id` (required) — Firebase UID. 28 alphanumeric chars typical (e.g.
  `e8XFD61aBaYp5qNryghIHPuij0S2`).
- `--window` (optional, default `mtd`) — one of `today`, `wtd`, `mtd`,
  `prev-month`, `last-7d`, `last-30d`, `yesterday-pt`, `today-pt`.
  Custom range via `--from YYYY-MM-DD --to YYYY-MM-DD`.
- `--sources` (optional) — comma-separated subset of `openai`, `elevenlabs`,
  `livekit`, `listens` (default: all four). Useful when one source's prerequisites
  are missing or when the caller only needs a subset.

## Step 0 — Check prerequisites

Each sub-skill has its own prereq. This orchestrator checks all up
front and degrades gracefully when a source isn't available:

```bash
test -n "$OPENAI_ADMIN_API_KEY" && echo "OK openai" || echo "MISSING openai"
test -n "$AURA_API_TOKEN"       && echo "OK listens" || echo "MISSING listens"
test -n "$ELEVENLABS_ADMIN_API_KEY" || test -n "$ELEVENLABS_API_KEY" \
  && echo "OK elevenlabs" || echo "MISSING elevenlabs"
test -n "$GCP_KEY"              && echo "OK cost store" || echo "MISSING cost store"
```

`GCP_KEY` serves two legs, both reading the per-user daily cost store:
all of LiveKit, and the ElevenLabs days past its 10-day retention. Both
legs also need an interpreter with `google-cloud-bigquery`, which Step 1a
picks. Without the key or the library they still run. LiveKit reports
itself `unavailable`. ElevenLabs reports its store half `unavailable`
and keeps its live half. Both exit `2`. The table below renders those as
`unavailable`, never `\$0.00`.

`OPENAI_ADMIN_API_KEY` covers only the org-level `/v1/organization/*`
half of the OpenAI walk. Per-user attribution also needs the
project-scoped `OPENAI_<PROJECT>_API_KEY` family (PROD-3572); a project
whose key is absent is skipped with a warning and the run still
completes, so treat the check above as necessary but not sufficient.

Behaviour when a source's key is missing:

- **Only one or two sources reachable.** Continue with what's available;
  the rendered report's per-source table shows `unavailable` for the
  missing source(s) and the all-source total is computed from the
  reachable subset, with a clear caveat in the report header.
- **No source reachable.** Stop with a clear error pointing to
  `update-config`.

**Cowork caveat.** All four sub-skills' env vars are blocked from
`mcp__workspace__bash` (PROD-1305). Use the per-session paste-export
workaround in CLAUDE.md → "Cowork constraints", or run from the host
harness.

## Step 1 — Plan, then collect the four sources

**Plan-before-action cue.** Before collecting, lay out a one-paragraph
plan in chat:

- The UID (prefix only — `e8XFD61a…`) and window (e.g. "month-to-date,
  May 1 → May 28 = 28 days").
- Which sources are in scope (default all four; subset if `--sources`
  was passed or a key is missing).
- What the synthesised report will surface (top-line $ + per-source
  breakdown + per-day rate + caveats from each sub-skill).

### Step 1a — Default: run the four sub-skill scripts directly (deterministic)

Each sub-skill's work is done by a `report.py` that already emits the
structured JSON this orchestrator consumes. **Run those scripts with
Bash — do not invoke the sub-skills via the Skill tool.** The four
`report.py` calls are independent; issue them in one message so they run
concurrently.

```bash
R=${CLAUDE_PLUGIN_ROOT}/skills
python3 $R/report-openai-user-cost/scripts/report.py     <UID> --window <window> --no-by-project --json /tmp/user_cost_openai_<prefix>.json
python3 $R/report-listens-user-cost/scripts/report.py     <UID> --window <window> --no-by-tier    --json /tmp/user_cost_listens_<prefix>.json

# LiveKit and ElevenLabs read BigQuery: use the first interpreter that exists AND has
# google-cloud-bigquery (plain python3 often lacks it; the framework python on the team Macs has it).
PYBQ=python3
for c in python3 /Library/Frameworks/Python.framework/Versions/3.12/bin/python3; do
  command -v "$c" >/dev/null 2>&1 && "$c" -c 'import google.cloud.bigquery' 2>/dev/null && { PYBQ=$c; break; }
done
"$PYBQ" $R/report-elevenlabs-user-cost/scripts/report.py  <UID> --window <window> --no-by-bucket  --json /tmp/user_cost_elevenlabs_<prefix>.json
"$PYBQ" $R/report-livekit-user-cost/scripts/report.py     <UID> --window <window> --no-by-vendor  --json /tmp/user_cost_livekit_<prefix>.json
```

If no candidate has the library, `PYBQ` stays `python3`, so both scripts always run. Each then
reports its BigQuery part `unavailable`, writes its JSON and names what is missing, as Step 0
describes. Never skip either call because the library looks missing: a leg with no JSON cannot
be rendered at all.

Substitute `--from / --to` for `--window` on a custom range. Both the
OpenAI and ElevenLabs walks take minutes — that is expected work, not a
hang. **Which one is slowest depends on the window**; see
[Runtime](#runtime) before you set a timeout around this step.

**This calls four sibling skills' scripts by path, which is a deliberate
exception to skill self-containment (architecture principle 1)** — same
shape as `data:run-rating-feedback-analysis-cadence` calling
`investigate-user-rating`'s `fetch_ratings.py`. The alternative is copying
four `report.py` files plus `resolve_window.py` (already a tracked
canonical family) into this folder, which trades one composition
dependency for four drifting copies of the code that produces the numbers.
All four siblings live in this same plugin, so `CLAUDE_PLUGIN_ROOT`
resolves them and they ship together — a partial install cannot leave this
skill pointing at something absent.

**Why the scripts and not the Skill tool.** This orchestrator is almost
always reached from inside an agent: `data:profile-user` composes it, and
[`data:run-cancellation-feedback-analysis-cadence`](../../../data/skills/run-cancellation-feedback-analysis-cadence/SKILL.md)
composes *that*. Each Skill-tool invocation spawns another agent that fans
out further, so a Skill-tool collection sits three or more levels of nesting
deep before the first API call — deep enough that runs report themselves as
in progress and never return a figure, leaving the caller to time out and
record cost as pending. The scripts do the same work with none of that, so
the caller gets a number. Deterministic-first is authoring principle 7.

### Step 1b — Skill-tool path (interactive only, opt-in)

Invoking `eng:report-openai-user-cost` / `eng:report-elevenlabs-user-cost` /
`eng:report-livekit-user-cost` / `eng:report-listens-user-cost` via the Skill tool remains valid **when a
human asked for one source's full narrative sub-report** and this
orchestrator is running at the top level, not underneath another skill.
It buys the sub-skill's own prose and caveat framing; it costs the nesting
above. Never take this path from `data:profile-user`, from any
`run-*-analysis-cadence`, or from any other agent-invoked context.

If one source errors (rate limit, network blip, missing key), surface the
error, note the source as `error` in the per-source table, and continue with
the others — don't bail the whole report for one failure.

## Step 2 — Read the JSON outputs and synthesise

Read each `/tmp/user_cost_<source>_<UID-prefix>.json` file. The shape
is consistent enough across sub-skills that the orchestrator can pull:

- `totals.cost_usd` (OpenAI — note the plural key), `total.cost_usd`
  (ElevenLabs, LiveKit) or `total.cost_cents` (listens — divide by 100)
- LiveKit only: `status` (`ok` / `partial` / `pending` / `unavailable` /
  `error`) and `pending_dates`. `total.cost_usd` is `null` when the leg
  measured nothing (all pending, or unavailable)
- `window.label`, `window.days_in`
- `notes` (caveats list) — propagate verbatim into the unified report

Compose the unified report in this exact shape:

```
## All-source cost — `<UID-prefix>`  ($TOTAL over Nd, $X.XX/day)

| Source | Cost | % of total |
|---|---:|---:|
| OpenAI inference | $X.XX | XX% |
| ElevenLabs (ConvAI voice) | $X.XX | XX% |
| LiveKit (voice) | $X.XX | XX% |
| Listens (Aura content payout) | $X.XX | XX% |
| **Total** | **$X.XX** | **100%** |

**Per-day rate:** $X.XX/day over Nd window.

**Caveats:**
- <verbatim caveats from each sub-skill, prefixed with the source name>
- <orchestrator-level caveats: missing source, sub-skill errors, etc.>

---

### OpenAI inference (detail)
<full markdown from the openai sub-skill — its headline + table>

### ElevenLabs (detail)
<full markdown from the elevenlabs sub-skill — its headline + table>

### LiveKit (detail)
<full markdown from the livekit sub-skill — its headline + table + pending notes>

### Listens (detail)
<full markdown from the listens sub-skill — its headline + table>
```

For sub-skills that returned zero cost / no qualifying events, render
the per-source row with `\$0.00 (0%)` and skip the detail section
entirely (just note "No matching activity in this window" inline in
the breakdown table row's notes column).

**A leg that measured nothing is not a zero.** When the LiveKit JSON has
`total.cost_usd: null`, render its row as `pending` (status `pending`)
or `unavailable` / `error` with the note's reason. Leave it out of the
total, and add a header caveat naming it. Examples: "LiveKit pending —
2026-09-25 not yet in the cost store", or "LiveKit unavailable — GCP_KEY
not set". For `partial`, show the stored-days figure and carry the
pending-dates note verbatim into Caveats, because same-day LiveKit use
is not in the total.

**ElevenLabs `partial` means part of the window was not measured.** When
its JSON `status` is `partial`, `unavailable` or `error`, the figure
covers only the measured part. That happens when its store half (the
days past the 10-day retention) is `pending` or `unavailable`. Render
the ElevenLabs row with its figure, mark it partial, and carry the
store-half caveat verbatim. When `total.cost_usd` is `null` (nothing
measured at all), render the row like a LiveKit `null`.

**LiveKit and ElevenLabs never overlap.** `voice_11labs_livekit` (ElevenLabs TTS on LiveKit
sessions) is counted by the LiveKit leg. The ElevenLabs leg walks ConvAI only. Adding the two
rows counts nothing twice. The rule and the test that pins it are in
[`eng:report-livekit-user-cost`](../report-livekit-user-cost/SKILL.md) → *What this leg counts*.

## Step 3 — Show the report inline

Return the unified report as the skill's result. Don't post it
anywhere, don't write it to disk. The caller decides what to do with
it.

Lead with the one-line headline (UID prefix, total $, window length,
per-day rate) so it can be grabbed for a Slack summary without
re-reading the whole document.

## Step 4 — Optional composition use cases

Other skills can invoke this orchestrator instead of rolling their own
multi-source aggregation. Today's known consumers (post-PROD-2240
migration):

- `data:profile-user` — its Cost line in the synthesis invokes this
  skill (replacing the unreliable Braintrust `estimated_cost` aggregation
  it used pre-migration).
- `data:analyze-user-behavior` — same, for the Engagement section's
  cost line.
- `data:analyze-user-conversations` — invokes the OpenAI sub-skill
  directly (not this orchestrator) since it only needs per-conversation
  OpenAI cost.

## Runtime

`--window last-30d`, one real user per leg, issued in parallel:

| Leg | Measured | Scales with |
|---|---|---|
| `report-listens-user-cost` | **0.6 s** | nothing — one POST |
| `report-livekit-user-cost` | **3.5 s** | nothing — two partition-pruned BigQuery reads |
| `report-elevenlabs-user-cost` | **0.9 s** dormant, **22.9 s** at 58 calls | that user's own call count |
| `report-openai-user-cost` | **8.8 min** | the `[Production]` project count (**18** today) |

**The OpenAI leg is the slow one, and it is the one to size a timeout
against.** It walks every `[Production]` project serially, so it grows
whenever a project is added to the OpenAI org — with nothing here to
notice. Quote the window with any figure above and re-measure before
sizing a cap off one: a timeout that fires on an ordinary run is
indistinguishable from a hang.

**The other legs are user-scoped.** ElevenLabs filters its list call
server-side by `user_id`, so it costs one detail fetch per *that user's*
conversations rather than per workspace conversation; see
[`eng:report-elevenlabs-user-cost`](../report-elevenlabs-user-cost/SKILL.md)
→ Notes for the degradation signal that fires if the filter ever stops
working. Listens is a single POST. LiveKit reads two small date ranges
from the cost store.

`--sources` narrows the run when a caller wants fewer legs, and
`data:profile-user` exposes it as `cost_sources`. **Whatever is dropped
has to be named in the output** — a narrowed total rendered as a whole one
understates the user, and on a cost-driven question that inverts the
conclusion.

## Notes & edge cases

- **Runtime — quote a window or the number is meaningless.** See
  [Runtime](#runtime) below. Issue the four Step 1a `report.py` calls in
  one message so the wall-clock is bounded by the slowest, not the sum.
- **User has no activity in any source.** Render `\$0.00 total` with a
  caveat noting "No matching activity across any source in this window."
  Useful signal — they might be a new signup or churned dormant user.
- **Window touches the OpenAI 2026-05-12 metadata-user rollout cutoff.**
  The OpenAI sub-skill handles this by extrapolating the pre-rollout
  user share uniformly across all projects from the post-rollout
  sub-window's OpenAI Costs + Usage data (the old Braintrust fallback
  was removed in PROD-2466). Caveats propagate to this orchestrator's
  report header.
- **Window reaches past the ElevenLabs 10-day retention cutoff.** The
  ElevenLabs sub-skill reads those days from the per-user cost store
  and the rest from its live walk. Each day comes from one source. Its
  Source table and caveats say which days came from where.
- **Window straddles a PaymentService payout-cycle boundary.** Listens
  cost is unaffected (per-listen cost doesn't depend on monthly
  boundaries); only the rendered $ may not match a specific monthly
  invoice. Same caveat as the sub-skill.
- **`--sources <subset>` flag.** Useful for the
  `data:analyze-user-conversations` composition path where only OpenAI
  is needed. Sub-skills not in scope are skipped entirely (not even
  prereq-checked).

## Out of scope (v1)

- **Per-conversation $ breakdown.** Sub-skills don't expose stable
  conversation IDs across sources.
- **Trend lines / day-over-day.** Call the orchestrator twice with
  different windows and diff.
- **Cohort / top-N ranking.** Per-source rank-N family handles that
  (`eng:analyze-*-cost-per-user`); a future
  `eng:rank-user-costs` could span all four sources but is out of
  scope here.
- **Cost forecasting / LTV / unit economics.** Descriptive only.
- **Per-active-day metric.** Would require querying activity (OpenAI
  stored-completion conversation count, Mixpanel app-open count) to define
  "active." Use the simple per-window-day rate instead —
  `total / window.days_in`.

## References

- Sub-skills:
  [`eng:report-openai-user-cost`](../report-openai-user-cost/SKILL.md),
  [`eng:report-elevenlabs-user-cost`](../report-elevenlabs-user-cost/SKILL.md),
  [`eng:report-listens-user-cost`](../report-listens-user-cost/SKILL.md),
  [`eng:report-livekit-user-cost`](../report-livekit-user-cost/SKILL.md).
- Related per-source rank-N siblings:
  [`eng:analyze-openai-cost-per-user`](../analyze-openai-cost-per-user/SKILL.md),
  [`eng:analyze-elevenlabs-cost-per-user`](../analyze-elevenlabs-cost-per-user/SKILL.md),
  [`eng:analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md).
- AI Knowledge: [`Tools/Braintrust` →
  "Cost accounting"](https://www.notion.so/34c75e23af2081968f7bf7ee43e1442a)
  — the rule that motivated this family.
- Linear: [PROD-2242](https://linear.app/aura-health/issue/PROD-2242) (this
  card), [PROD-2243](https://linear.app/aura-health/issue/PROD-2243)
  (OpenAI sub-skill), [PROD-2244](https://linear.app/aura-health/issue/PROD-2244)
  (ElevenLabs sub-skill), [PROD-2245](https://linear.app/aura-health/issue/PROD-2245)
  (Listens sub-skill), [PROD-2240](https://linear.app/aura-health/issue/PROD-2240)
  (consumer migration off Braintrust `estimated_cost`, blocked by this card).
- ENG-498: `metadata.user` tagging rollout for OpenAI calls (2026-05-12).
- PROD-1961: PaymentService `POST /payouts/listens/aggregate` endpoint spec.
