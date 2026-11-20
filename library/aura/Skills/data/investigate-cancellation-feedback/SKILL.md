---
name: investigate-cancellation-feedback
description: >
  Sweeps Slack churn-signal channels over a window — `*New Cancellation Feedback*` k8bot
  messages in #ai-cancellation-feedback and free-text posts in #aurie-feedback — profiles each
  canceller one at a time, and emits an aggregate digest. A thin orchestrator over
  `data:profile-user`, owning the message parse, the test/noise filter, the interactive loop
  and the digest. Sequential and interactive, NOT a parallel fan-out, so the user can skip,
  stop or steer mid-sweep. Input is a window (default 7d) or a Slack URL. Threaded replies are
  opt-in. UID prefix and email domain only. Trigger on "investigate cancellation feedback",
  "review this week's cancellations", "sweep the cancellation feedback", "analyze recent
  cancellations", "who cancelled this week and why", "review aurie feedback", "sweep
  #aurie-feedback", or a pasted cancellation channel / thread URL. Do NOT use for a known UID
  (`data:profile-user`) or PMF responses.

metadata:
  version: "3.4.0"
knowledge:
  reads:
    - Guide/Index          # baseline routing aid (universal)
    - Compliance/Privacy   # PII rule — UID prefix + email domain only on every Slack surface
    - Products/Aurie  # brand voice for the digest's framing
    - Tools/Slack          # canonical Channel IDs (for skills) — resolves #ai-cancellation-feedback → C0BR3UTNL83, #aurie-feedback → C0AEK511BT9, #ai-feedback → C0AENS8D48N (pre-split cancellations)
    - Tools/Mixpanel                 # Mixpanel service-account / MCP access
    - Tools/OpenAI                   # OpenAI project keys + stored-completions access
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# investigate-cancellation-feedback

A thin **orchestrator**. It sweeps Slack churn-signal channels — `*New Cancellation Feedback*` k8bot posts in **#ai-cancellation-feedback** (`C0BR3UTNL83`, plus **#ai-feedback** `C0AENS8D48N` for posts predating the 2026-08 split) and free-text human-team posts in **#aurie-feedback** (`C0AEK511BT9`) — and for each real (non-test) canceller it calls [`data:profile-user`](../profile-user/SKILL.md) to produce the per-user picture, then rolls everything up into one digest. It does **not** re-implement behavioural / conversational / cost analysis — that all lives in `profile-user` and its sub-skills.

What this skill owns:

- Parsing the two message shapes and filtering test/noise entries (a deterministic script, [`scripts/fetch_cancellations.py`](scripts/fetch_cancellations.py)). Format selection is by `--channel` (see [Channel formats](#channel-formats) below).
- The **one-at-a-time, interactive** review loop.
- The opt-in threaded Slack reply per profile (it owns this; `profile-user` runs with `side_effects: off`).
- The aggregate digest (reason mix, provider split, time-to-cancel, save-economics, cross-cutting patterns).

**Why one at a time, not a parallel fan-out.** The originating 2026-06-04 sweep fanned out ~20 `profile-user` agents at once and hit OpenAI / ElevenLabs rate limits and lost in-flight agents to interrupts. This skill instead walks entries sequentially: profile → show the user → optionally post a threaded reply → advance. The user stays in the loop and can skip, stop, or re-steer at any entry.

## When to use

- "investigate cancellation feedback" / "review this week's cancellations"
- "sweep the cancellation feedback" / "analyze recent cancellations"
- "who cancelled this week and why"
- "review aurie feedback" / "sweep #aurie-feedback" / "profile the aurie-feedback posts"
- pastes an #ai-cancellation-feedback (or pre-split #ai-feedback) **or** #aurie-feedback channel / thread URL and asks to review cancellations

**Do NOT use for:**

- A single user when you already have a UID → [`data:profile-user`](../profile-user/SKILL.md) (Mode B).
- PMF survey responses → `data:investigate-pmf-survey`.
- A single conversation rating (QP / coaching / chat) → `data:profile-user` with the rating URL.
- Aggregate Mixpanel cohort analysis → the Mixpanel UI.

## Channel formats

The skill supports two Slack-message shapes, dispatched by channel. Same downstream wiring into `data:profile-user` — different upstream parsers.

| Aspect | `cancellation_feedback` (default) | `aurie_feedback` |
|---|---|---|
| Channel | `C0BR3UTNL83` = **#ai-cancellation-feedback** (and `C0AENS8D48N` = **#ai-feedback** for posts before the 2026-08 split) | `C0AEK511BT9` = **#aurie-feedback** |
| Author | k8bot (every in-app cancellation that flows through the cancel modal) | human support-team members (currently `U01FWJNMR9R`, `U019LLC8S5C`) |
| Shape | `*New Cancellation Feedback*` header + `Reason:` + fenced `Feedback:` block + `_User \| Subscription \| Type \| Product \| Purchased \| Provider_` metadata line | free-text quote + ` - <UID>` suffix + optional trailing back-tick-fenced support annotation |
| UID extraction | `_User: <UID>` field in the metadata line | 28-char alphanumeric token at the end of the message after `- ` |
| Reason field | Structured dropdown (`Too expensive`, `Other`, `Didn't help me the way I hoped`, …) — fed to `data:profile-user` as `context.category` | None — `context.category` is set to `"free-text aurie-feedback channel post"` and the synthesis layer derives its read from the verbatim text |
| Test / noise filter | Feedback text matches `test` / `testing` / `test dev` OR same UID fired ≥ 3 in-window (QA blast) OR same UID fired exactly twice within 10 minutes (the AIiOS cancel-modal double-fire bug — see PROD-2554; the later message is dropped as `cancel-modal-double-fire`, the earlier one stays as canonical) OR the Stripe Product ID is a known DEV-environment price (`price_1SaaoIIS90bgNudEFtMDb6Xl`, `price_1TRtfBIS90bgNudEYQsIa7gU` — full inventory in AI Knowledge `Tools/Stripe` → "Price IDs") | Bot posts skipped (weekly digests in this channel use `bot_id`); `channel_join` / `channel_leave` / `bot_message` subtypes skipped; verbatim posts from humans treated as real |
| `support_annotation` | Not applicable | Captured separately when the support team paste ends with a single-back-tick-fenced line (e.g. `` `Asked to book a call.` ``) — surfaced to the caller, NOT folded into the steering string |
| Coverage | In-app cancels that went through the cancel modal | Out-of-band cancels (App Store / Stripe direct toggle) — the only signal for users who never hit the modal, including high-conviction save targets, bug ragequits, and zero-engagement trial bounces |

**Why both exist.** k8bot covers users who cancel through Aurie's in-app flow and fill out the modal. But many users cancel auto-renew directly via App Store or Stripe — `cancelReason` / `cancelFeedback` end up null and k8bot never fires. The support team's #aurie-feedback channel is the only place those churn signals surface (see [PROD-2523](https://linear.app/aura-health/issue/PROD-2523) for the originating finding: 4 users on 2026-06-08 who were entirely invisible to the existing sweep). Wiring both channels into the same skill means one Tuesday-morning sweep covers all known churn surfaces.

**Default selection.** Running the skill without an explicit channel sweeps `cancellation_feedback` on `#ai-cancellation-feedback` (the historical default, repointed at the post-split channel). To sweep `#aurie-feedback`, pass `channel_id=C0AEK511BT9` (or its Slack URL). To sweep the whole cancellation feed across the split, pass `channel_id="cancellations"`. To sweep every churn channel in one session, pass `channel_id="all"` — the recommended path for any generic "sweep all churn" ask (and the one a future cron / scheduled-task should use). The wrapped output keeps per-channel stats legible (each sub-object under `channels[]` is byte-for-byte identical to the standalone single-channel output), so the test/noise filter breakdown still surfaces per format. Running the skill twice with different `channel_id`s also still works for callers that need to interleave the two roster reads with other steps.

## Inputs

- **`slack_url`** (optional) — a Slack permalink. Three shapes:
  - a **channel** URL → sweep that channel over `window`;
  - a **thread** URL → sweep, and offer to land the aggregate digest as a reply on that thread;
  - a **single cancellation-message** permalink → profile just that one canceller (skip the window sweep).
- **`window`** (optional, default `7d`) — `7d` / `14d` / `30d` / `Nd` / `YYYY-MM-DD..YYYY-MM-DD` (inclusive). Ignored when `slack_url` is a single message.
- **`channel_id`** (optional, default `C0BR3UTNL83` = `#ai-cancellation-feedback`). Five accepted values: `C0BR3UTNL83` → `cancellation_feedback`, `C0AENS8D48N` (`#ai-feedback`, cancellations predating the 2026-08 split) → `cancellation_feedback`, `C0AEK511BT9` (`#aurie-feedback`) → `aurie_feedback`, or one of two sentinels — `cancellations` → both sides of the split, `all` → every churn channel (output wraps each channel under `channels[]`, see [Channel formats](#channel-formats) → Default selection). The parser format is derived from the channel ID for single-channel runs; canonical mapping in AI Knowledge → `Tools/Slack` → Channel IDs (for skills); the defaults here mirror that table. Pass any other channel ID for a one-off (the script falls back to `cancellation_feedback`); to force a specific parser on an unknown channel, run the script directly with `--format aurie_feedback` or `--format cancellation_feedback`. Note: `--single-ts` is single-channel only — pass an explicit `channel_id` (not a sentinel) when using it.
- **`analysis_depth`** (optional, default `meaningful_text_only`):
  - `aggregate_only` — stats + digest, no per-user `profile-user` runs;
  - `meaningful_text_only` — deep-profile only entries whose feedback text is non-empty and non-test;
  - `all` — deep-profile every non-test entry (including the empty-feedback ones).
- **`skip_prior_check`** (optional, default `false`) — when `true`, bypasses the Step 2.5 prior-analysis pre-check and proceeds straight to per-entry profiling. Use for re-runs where the caller has already decided to refresh, or for sweep modes where the per-entry pre-check noise outweighs the value (e.g. a 30d sweep where most threads will have priors).

## Prerequisites

- **Slack is MCP-first** (best-practices principle 11). The channel **fetch** (`fetch_cancellations.py`, `conversations.history`) is API-first by necessity — the script paginates + filters the window. The opt-in threaded **reply** (Steps 3–4) is agent-driven: prefer the Slack MCP connector to post it, falling back to `shared:post-to-slack`'s `slack_post.py` with **`SLACK_BOT_TOKEN`**. The bot must be a member of the channel (the fetch + the Step 2 `auth.test`/`conversations.replies` pre-check use the token directly).
- **`profile-user` chain env** — `profile-user` runs `analyze-user-behavior` (Mixpanel: `MIXPANEL_SERVICE_ACCOUNT_USERNAME` / `_SECRET` + **`MIXPANEL_PROJECT_ID=963821`**), `analyze-user-conversations` (`OPENAI_CONVERSATION_API_KEY` — a service-account key scoped to `Conversation [Production]`; reads OpenAI stored completions. **Not** `OPENAI_ADMIN_API_KEY` — Admin keys can't hold the `model.request` scope this read now requires, [PROD-3572](https://linear.app/aura-health/issue/PROD-3572)), and `eng:report-user-costs` (`AURA_API_TOKEN`, `OPENAI_ADMIN_API_KEY`, `ELEVENLABS_ADMIN_API_KEY`, and `GCP_KEY` for the cost-store reads: all of LiveKit, and ElevenLabs days past retention). This skill doesn't check those directly — `profile-user` and its sub-skills run their own prerequisite checks and degrade per their own docs.

**TLS gotcha.** Direct `urllib` / `curl` HTTPS calls fail in some sandboxes with `SSL: CERTIFICATE_VERIFY_FAILED`. The fetch script and any Slack post use the Python `requests` library (it bundles certifi). Don't reach for `curl`.

**Cowork caveat.** `mcp__workspace__bash` strips host env (PROD-1305), so `SLACK_BOT_TOKEN` and the `profile-user` chain's tokens won't reach a Cowork sandbox. Run from the host harness, or use the per-session paste-export workaround in [`/CLAUDE.md`](../../../../CLAUDE.md) → "Known Cowork constraints".

## Workflow

### Step 0 — Resolve inputs

Resolve `channel_id`, `window`, and `analysis_depth` from the inputs. If `slack_url` was given, decode it:

- `https://<ws>.slack.com/archives/<channel_id>/p<ts_no_dot>` → `channel_id` is the path segment after `/archives/`; the `p<digits>` slug becomes a ts by inserting a `.` before the last 6 digits (`p1780465597965429` → `1780465597.965429`).
- If the decoded message is a `*New Cancellation Feedback*` k8bot post (channel `C0BR3UTNL83`, or `C0AENS8D48N` for a pre-split post) **or** a free-text support-team feedback post in `C0AEK511BT9` (i.e. a non-bot message ending in `- <28-char UID>`) → **single-message mode** (pass `--single-ts <ts>` to the script; skip the window sweep).
- If it's a channel root or any other thread → sweep mode, and remember `channel_id` + `thread_ts` for the optional Step 4 digest reply.

### Step 1 — Fetch + parse + filter (deterministic)

Run the script and capture its JSON:

```bash
# single-channel (back-compat default and explicit-channel forms):
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-cancellation-feedback/scripts/fetch_cancellations.py \
  --channel <channel_id> --window <window>
# single-message mode (single-channel only — `--single-ts` is incompatible with a sentinel):
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-cancellation-feedback/scripts/fetch_cancellations.py \
  --channel <channel_id> --single-ts <ts>
# sweep the cancellation feed across the 2026-08 split (both k8bot channels, no #aurie-feedback):
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-cancellation-feedback/scripts/fetch_cancellations.py \
  --channel cancellations --window <window>
# sweep every feedback channel in one call (recommended for generic 'all churn' sweeps):
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-cancellation-feedback/scripts/fetch_cancellations.py \
  --channel all --window <window>
```

The script reads the channel ID and routes to the right parser (`--format auto`, the default — see [Channel formats](#channel-formats)). The output JSON has a stable shared shape across formats — `channel`, `format`, `window`, `counts`, `entries[]` — so the orchestrator code that walks the roster doesn't need a per-format branch. With a sentinel `--channel` (`cancellations` or `all`) the output instead wraps the per-channel results: `{"format": "<sentinel>", "window": ..., "channels": [<single-channel sub-object>, …], "totals": {"real_entries_across_channels": N, "by_channel": {...}}}`. Each sub-object under `channels[]` is byte-for-byte identical to a standalone single-channel invocation, so existing per-format synthesis logic walks the rosters with one extra `for ch in result["channels"]:` outer loop and no inner change. Under `all` the order is fixed — `[0]` the current k8bot channel, `[1]` the human-team channel, `[2]` the pre-split k8bot channel — so the historical `[0]`/`[1]` contract survives the split. Every entry also carries its own **`channel_id`** (the Slack channel it came from — `C0BR3UTNL83`, `C0AEK511BT9`, or `C0AENS8D48N`); a consumer that flattens `channels[]` into a single entry list **must** read the channel from `entry["channel_id"]` for dedup + posting rather than defaulting it, or an `#aurie-feedback` entry gets checked/posted against the wrong channel.

**`cancellation_feedback` (default, channels `C0BR3UTNL83` + pre-split `C0AENS8D48N`).** Parses the `*New Cancellation Feedback*` shape (`Reason`, fenced `Feedback`, and the `_User | Subscription | Type | Product | Purchased | Provider_` line), computes `days_to_cancel` from `Purchased`, and applies the **test filter**:

**The two timestamps in a post are on different clocks.** `Purchased` is a **US Pacific wall clock**; the cancellation instant is the Slack `ts`, a UTC epoch. The script localizes `Purchased` to `PURCHASED_TZ_NAME` before subtracting — subtracting them raw adds the entire UTC offset to every gap, which turns a same-minute in-flow cancel into a ~7-hour one and so moves it out of the same-day bucket. Each entry carries the resolved instant as **`purchased_utc`** alongside the verbatim `purchased`, so `days_to_cancel` can be re-derived against `when` without knowing which clock either side was rendered in. Covered by [`tests/test_days_to_cancel.py`](../../../../tests/test_days_to_cancel.py).

- feedback text (lowercased/stripped) is `test` / `testing` / `test dev` or starts with `test`; **and/or**
- the UID fired ≥3 cancellations in the window (a QA blast loop); **and/or**
- the UID fired exactly twice within 10 minutes (the AIiOS cancel-modal double-fire bug). One user-initiated cancel currently produces two k8bot Slack messages ~4–10 min apart because the iOS cancel modal re-runs the cancel flow ([PROD-2554](https://linear.app/aura-health/issue/PROD-2554)). The **later** of the two entries is dropped as `test_reason=cancel-modal-double-fire`; the earlier one stays as the canonical cancel record. When the upstream bug is fixed this rule becomes redundant but stays as a belt-and-suspenders defence. The window-guarded rule (`DOUBLE_MODAL_WINDOW_SECONDS=600`) won't suppress real signal because a legitimate re-cancellation after a resubscribe takes far longer than 10 minutes — Stripe billing cycles operate on days, not minutes; **and/or**
- the entry's Stripe Product ID matches a known **DEV-environment** Stripe price (today: `price_1SaaoIIS90bgNudEFtMDb6Xl` annual + `price_1TRtfBIS90bgNudEYQsIa7gU` monthly). The cancellation k8bot webhook routes both dev and prod Stripe-side cancellations into the PROD channel; entries on dev-only price IDs are dropped as `test_reason=dev-environment-stripe-price` to keep them out of the real-user cohort. Full dev↔prod price-ID inventory lives in AI Knowledge `Tools/Stripe` → "Price IDs (dev vs prod, plus the dev-leak gotcha)" — keep `DEV_STRIPE_PRICES` in the script in sync with that page when AIWeb adds a new dev variant.

Each dropped entry carries a `test_reason`; the script reports the counts so the filter is auditable. (The 2026-06-04 7d window: 24 parsed → 8 real + 16 test dropped.) Output adds `reason_mix`, `provider_mix`, `test_reasons`, `test_entries[]`.

**Post-API-lookup test filter (email pattern).** After fetching the Aura API user record in Step 3, if the returned email contains `.for.test+` (e.g. `<redacted-email>`), treat the entry as a QA/researcher account and exclude it — do not profile further, do not post a Slack reply, exclude from the digest's reason mix and patterns. Log it as `test_reason=test-email-pattern`. The `.for.test+` substring is the canonical Aura QA/research email convention; the `+` tag suffix is arbitrary and varies per run.

**Ghost subscriber — don't label until both checks pass.** Only flag a user as a ghost subscriber (no account, no usage) after confirming (a) the Aura API call succeeded (HTTP 200, valid user object) AND (b) the conversations fetch returned zero results. A silent API failure — e.g. a shell readonly-variable collision (`UID` is reserved in bash; use `USR` or another variable name) — will produce empty results that look like a missing account but aren't. Verify the HTTP status and response shape before concluding the account doesn't exist.

**`aurie_feedback` (channel `C0AEK511BT9`).** Parses the free-text support-team paste shape, extracts the 28-char Firebase UID from the `… - <UID>` suffix at end of message, strips outer ASCII or smart quotes from the verbatim text, and captures any trailing back-tick-fenced line as `support_annotation` (kept separate from the steering text). The **noise filter** here is upstream — bot posts (this channel also receives a weekly digest) are dropped via `bot_id`, and `channel_join` / `channel_leave` / `bot_message` subtypes are dropped via `subtype`. No `reason` / `provider` / `purchased` / `days_to_cancel` fields apply — those keys are present with empty values for schema parity. Output adds `skipped` count, `skip_reasons`, `with_support_annotation` count, and `skipped_messages[]`.

### Step 2 — Show the roster, then act (plan-before-side-effects)

Before profiling anyone, show the user the parsed roster. The exact stats depend on the format:

- **`cancellation_feedback`** — real-entry count, test-dropped count **with the `test_reason` breakdown**, the reason mix, and the provider split.
- **`aurie_feedback`** — real-entry count, skipped count **with the `skip_reason` breakdown** (bot-post / subtype-channel_join / no-uid-match), and the with-support-annotation count. No reason mix or provider split — that's not structured in this format.

State the plan: which entries will be deep-profiled given `analysis_depth`, that each runs `profile-user` sequentially, that each will first be pre-checked for a prior analysis (Step 2.5), and that threaded Slack replies (if any) are opt-in. This is the moment to slow down — the user can narrow the window, switch depth, or pick specific entries before any sub-skill runs.

### Step 2.5 — Prior-analysis pre-check (per entry)

Before invoking `profile-user` on an entry, walk that entry's source thread for any prior reply posted by our own analysis bot user. When a prior reply is found, surface it and ask the caller how to proceed — skipping costly sub-skill runs on threads that were already analyzed avoids both the OpenAI / ElevenLabs / Mixpanel round-trips and the noise of double-posting in the source channel.

Skip this step when `skip_prior_check=true`, when `analysis_depth=aggregate_only` (Step 3 doesn't run anyway), or when the bot's own user_id can't be resolved (see **Bot identity** below).

**Bot identity.** Resolve once at the start of the run by calling Slack `auth.test` on `SLACK_BOT_TOKEN`. The returned `user_id` (e.g. `U05PB6H1234`) is our analysis bot's identity — every reply on a thread whose `user` field equals that id counts as a prior analysis. If `auth.test` fails (401, missing token, network error), skip the pre-check silently and note *"prior-analysis pre-check skipped — Slack auth.test unavailable"* in the digest's **Sources** block. The run still proceeds to per-entry profiling.

**Per-entry check.** For each in-scope entry, call Slack `conversations.replies` with `channel=<channel_id>, ts=<entry thread_ts>`. Walk the returned messages, **skip the thread parent** (it's the source feedback post — k8bot for `cancellation_feedback`, a support team member for `aurie_feedback` — not our reply), and pick the most recent message whose `user` equals the bot user_id. If none, continue silently to Step 3 for that entry. The 2026-06-08 manual `data:profile-user` replies on the four `#aurie-feedback` threads were posted under the bot identity (`aura_ai`, `U0ATMPESR37`), so this pre-check finds them on the next sweep without modification.

Use the Python `requests` library for both Slack calls — the TLS gotcha in **Prerequisites** above applies here too; don't use `curl`. Pace pre-checks at ~0.5s between entries on sweeps to stay well under Slack's `conversations.replies` rate limit (Tier 3, 50/min/app).

**When a prior analysis is found**, surface it inline before profiling:

```
Prior analysis found on this entry's thread:
- posted: 2026-05-22 14:31 UTC
- first lines:
  > <first 3 non-blank lines of the prior reply, each truncated to ~200 chars>
- permalink: https://auramobile.slack.com/archives/<channel_id>/p<reply_ts_no_dot>?thread_ts=<entry thread_ts>
```

Then ask **three explicit choices** — no defaults, ambiguous responses ("hmm" / "maybe") are not enough to advance:

- **Re-run** — proceed to Step 3 for this entry. Step 5's per-entry post offer will mention the prior reply ("a prior analysis was already posted at <ts> — post this fresher one as a new reply, or skip the post?"), so the caller can decide between a new reply and skipping the post.
- **Skip** — don't run `profile-user` for this entry. The aggregate digest substitutes the prior-analysis permalink for this entry's per-user card; no sub-skills run, no new post happens.
- **Show only** — fetch the prior reply's full body via `conversations.replies`, render it inline as the entry's "analysis" (no synthesis layer), and exit for this entry without running sub-skills or posting anything new.

**Single-message mode.** The pre-check still runs — there's just one entry, so one ask. A "skip" or "show only" returns immediately with the prior analysis's permalink + (for "show only") its body, and the run ends without a digest.

### Step 3 — Per-user loop (one at a time)

Skip this step entirely when `analysis_depth=aggregate_only`. Also skip entries where Step 2.5's per-entry pre-check returned **skip** or **show only** — the digest substitutes the prior-analysis link for those entries.

Pick the in-scope entries: `meaningful_text_only` → entries with non-empty feedback; `all` → every real entry. (In `aurie_feedback` every real entry has feedback text by construction — the parser drops UID-only posts.) Then loop **sequentially** (do not fan out). For each entry:

1. Invoke [`data:profile-user`](../profile-user/SKILL.md) via the `Skill` tool in Mode B with:
   - `user_id=<uid>`
   - `context = { kind: "cancellation", stated: "<feedback text>", category: <see below> }`
     - For `cancellation_feedback`: `category = "<dropdown reason>"` (the parsed `Reason:` field — e.g. `"Too expensive"`, `"Didn't help me the way I hoped"`).
     - For `aurie_feedback`: `category = "free-text aurie-feedback channel post"` (no structured reason exists; the synthesis layer derives its read from the verbatim text). When the entry carries a `support_annotation`, append it to `stated` as a final line — e.g. `"<verbatim quote>\n\n[Support annotation: Asked to book a call.]"` — so the profile factors in the support team's annotation without losing the user's own words.
   - `steering="<feedback text>"` (empty when the entry had no free text — the profile then leans on the categorical reason + behaviour). For `aurie_feedback`, `steering` is always the verbatim quote and is never empty.
   - `sources=[behavior, conversations, cost]`
   - `side_effects={ slack_reply: off, notion_record: off, braintrust_dataset: off }` — **the orchestrator owns persistence**
   - `time_window=30d` (the cancellation window is about *which* users; the per-user lens is 30d)
   - `skip_prior_check=true` — Step 2.5 above already ran the per-entry pre-check; pass through to suppress `profile-user`'s own Step 0.5 and avoid duplicate asks
2. Present the returned profile to the user (lead with the UID prefix + reason + the **Cancellation explanation**).

   **Close every per-entry card with a `*Follow-up*` section** after `Signal` — up to four bullets (🧠 prompt candidate · 🛠 product/eng · 📊 data check · 💬 CS), only the ones that apply, and none at all when nothing is actionable. A prompt line is a *candidate* rule for the prompt engineer, drafted only after reading the current prompt text and checking what the model actually received; link an existing Linear card, never create one. Format, rules and worked examples: [`references/follow-up.md`](references/follow-up.md).
3. **If** a `slack_url` / channel was supplied, offer (opt-in, draft-and-confirm) to post the profile as a **threaded reply** under that entry's source message (`thread_ts` from the script). See Step 5 for the posting mechanics + PII rule. Never auto-post.
4. Advance to the next entry. The user may say "skip", "stop", or re-steer at any point.

Hold each returned profile in memory for the Step 4 digest — don't reprint them all.

### Step 4 — Aggregate digest (always)

Compose one digest from the roster + the per-user profiles:

- **Reason distribution** (count + %) and **provider split** (from the script). *Skip both for `aurie_feedback` — neither is structured in that format. Replace with a one-line note describing the channel coverage gap the format fills (out-of-band cancels not visible to k8bot).*
- **Time-to-cancel histogram** from `days_to_cancel`: same-day (<1) / <7d / 7–30d / >30d. *Skip for `aurie_feedback` — `Purchased` isn't structured in the post, so `days_to_cancel` is null. The per-user `data:profile-user` runs surface this from Mixpanel + Aura API per user.*
- **Per-reason feedback snippets**, deduped (drop empties). *For `aurie_feedback`, group by the synthesised failure-mode tag from the per-user analysis (e.g. "Import Memory bug ragequit", "champion / price-timing cancel", "save-call volunteer") rather than the absent dropdown reason — same shape, derived rather than structured.*
- **Cross-cutting patterns** — 3–5 falsifiable, evidence-cited bullets. Seed with the patterns the 2026-06-04 sweep established, and confirm or revise them against this window's data:
  - "Too expensive" is a poor signal on its own — it splits into genuine **price-shock** (paid then bailed in seconds, zero usage), **creative-mismatch FIT** (came in on an ad promising a different product, e.g. a "ChatGPT" creative, then judged the reflective product "not helpful"), **temporal affordability** ("cost *currently*"), and **annual-commit balk** (deep trial engagement, balked at the \$69.99/yr commit).
  - **Annual-only pricing** is the recurring churn driver for high-engagement trial users — a monthly fallback at the cancel modal is the highest-leverage intervention.
  - **Save-economics are strong** — most cancellers cost \$0–9 over 30d against \$69.99/yr; flag the per-user cost-vs-price ratio from the profiles.
  - **Guardrail — don't read `deletedAt` as deletion intent.** A `deletedAt` timestamp on a `/subscriptions/list/:userId` row is a backend artifact (Stripe webhook completion / PaymentService async cleanup), **not** evidence the user asked to delete their data. The real signal for user intent is the Mixpanel event chain — `Open Delete Account Modal` → `Delete Account Confirmation Open` → `Delete User` for accounts, `AI Manage My Data Tapped` → `AI Conversation Deleted` / `AI Journal Deleted` for data. Cross-check Mixpanel before any per-user card claims "requested data deletion" — a `deletedAt` 10–30 min after a cancel is normal cleanup. Origin: 2026-06-09 misread documented in [PROD-2546](https://linear.app/aura-health/issue/PROD-2546).
- **Recommended follow-ups** — concrete, each naming an owner artifact (a paywall variant, an onboarding step, a creative). Roll up the per-entry `*Follow-up*` lines: group prompt candidates by prompter and count how many entries each one would have caught, so the prompt engineer sees recurrence rather than single cases.

### Step 5 — Output + optional digest reply

Return one Markdown digest: aggregate → per-user collapsed cards (one block each, UID-prefix only — substituted with the prior-analysis permalink for any entry where Step 2.5 returned "skip" or "show only") → patterns → recommended follow-ups → **Sources** (every source permalink + the `profile-user` invocations + the script invocation). When Step 2.5 detected priors, include the bullet *"N of M entries had prior analyses — linked, not re-run"* in the **Sources** block, alongside the per-entry prior-analysis permalinks.

If `slack_url` was a **thread** URL, offer (opt-in, draft-and-confirm) to post the digest's headline + patterns as a reply on that thread.

**Slack posting mechanics (Steps 3 + 5).** Sending on someone's behalf needs explicit per-turn approval — show the draft, require a clear yes, never auto-post. **PII rule (hard, per `Compliance/Privacy`):** UID prefix (first 8 chars) only, email **domain** only — never `givenName` / `familyName` / full email. Post each per-entry reply and the Step 5 digest by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with `channel=<id>`, `thread_ts=<entry thread_ts>` (so it lands as a reply), and **`--footer data:investigate-cancellation-feedback`** — the helper stamps the canonical versioned footer (`_Generated by `data:investigate-cancellation-feedback:<version>` · <YYYY-MM-DD>_`) from `metadata.version` deterministically. **Don't** hand-type a footer, put one in the message body, or post via a raw `chat.postMessage` (`slack_post.py` uses `requests` internally — same certifi/TLS path). Pace ~0.5s between posts and confirm each post's permalink. When Step 2.5 found a prior analysis on this entry's thread and the caller chose "re-run", the per-entry post offer explicitly mentions the prior reply timestamp + permalink so the caller can decide between posting a fresh reply or skipping the post; always post as a new reply (no edits to the prior message).

**Card guards and scratch files (mandatory in cadence mode).** On every post pass `--expect "<uid8>…"` (this entry's redacted UID), `--max-chars 6000` and `--max-chunks 3`: the helper refuses, exit 2 and nothing sent, a body that is not this entry's card. When the caller gives an **output folder** (its worker folder, e.g. `g4_main/`), every file this skill writes goes there under a typed name (`g4_card_<uid8>.txt`), never the scratchpad root or a generic name like `c1.txt`; otherwise pipe the card on stdin (`--text -`). See `data:run-all-analysis-cadence` → *Scratch files and posting*.

## Output format

A single Markdown digest. Length scales with `analysis_depth`: `aggregate_only` ≈ 30–50 lines; `meaningful_text_only` ≈ 60–120 lines; `all` ≈ 150–250 lines for a busy week. Per-user cards are the compressed `profile-user` output (UID-prefix only), not the full per-section reports.

## Edge cases

- **Zero real entries (all test / all skipped).** Report the test-dropped count + `test_reason` breakdown (`cancellation_feedback`) **or** the skipped count + `skip_reason` breakdown (`aurie_feedback`, e.g. only weekly digests + `channel_join` events were in-window) and stop — there's nothing to profile. (Common on quiet days where only QA fired the flow / no support paste landed in #aurie-feedback.)
- **`profile-user` returns a partial picture** (no Mixpanel profile, or zero OpenAI completions). It already degrades gracefully and flags the gap in its Headline; carry that note into the digest card rather than dropping the entry.
- **Single-message mode.** Behaves like a one-entry sweep: one Step 2.5 pre-check + one `profile-user` run (if not skipped) + a one-line "aggregate" noting it was a single message. Still offers the threaded reply when sub-skills ran. `--single-ts` is single-channel only — pass an explicit `channel_id` (not `all`) when using it; the script errors loudly otherwise.
- **`channel_id="all"` with an explicit `--format`.** The script errors loudly — `--format` only applies when `--channel` is a single channel ID. The dispatcher uses `resolve_format("auto")` per channel under `all`, so each channel gets its native parser automatically. Drop the explicit `--format` (or run two single-channel invocations) if you need to override the parser.
- **Window spans the OpenAI metadata-user cutoff, or has cost-store days that are pending or unavailable (LiveKit, and ElevenLabs past its 10-day retention).** `eng:report-user-costs` surfaces those caveats per user; the digest's save-economics bullets should repeat any caveat verbatim rather than quoting a cost as exact.
- **Re-running on the same window.** Idempotent — no persistence beyond the opt-in Slack replies. Step 2.5 surfaces prior replies on each entry's thread so the caller can choose "skip" or "show only" to avoid re-posting; pass `skip_prior_check=true` to bypass the per-entry asks when the intent is deliberately to refresh everything.
- **Pre-check finds prior analysis, caller picks "re-run".** Step 3 runs as normal for that entry; Step 5's per-entry post offer mentions the prior reply explicitly so the caller can decide between posting a fresh reply and skipping the post. New replies always post as new messages — the prior reply is never edited.
- **`skip_prior_check=true`.** Step 2.5 is bypassed entirely; the sweep proceeds straight to per-entry profiling for every in-scope entry as if no priors existed. Use this for intentional re-profiling.
- **Slack `auth.test` fails (missing / expired `SLACK_BOT_TOKEN`).** Step 2.5 is skipped silently; the run proceeds to per-entry profiling. The digest's **Sources** block notes *"prior-analysis pre-check skipped — Slack auth.test unavailable"*.

## Key references

- **Per-user engine:** [`data:profile-user`](../profile-user/SKILL.md) — called once per canceller with `context.kind=cancellation`, `side_effects: off`.
- **Sibling sweep:** `data:investigate-pmf-survey` — same orchestrator shape for PMF responses.
- **Channels:** `#ai-cancellation-feedback` = `C0BR3UTNL83` (k8bot cancellation posts), `#aurie-feedback` = `C0AEK511BT9` (free-text support-team posts), `#ai-feedback` = `C0AENS8D48N` (k8bot cancellation posts predating the 2026-08 split; read-only history, nothing new lands there). Canonical mapping lives in AI Knowledge → `Tools/Slack` → Channel IDs (for skills); keep this skill's defaults in sync with that table whenever the channels change.
- **Linear:** [PROD-2398](https://linear.app/aura-health/issue/PROD-2398) (this skill) · [PROD-2523](https://linear.app/aura-health/issue/PROD-2523) (added `#aurie-feedback` channel + `aurie_feedback` parser) · blocked-by [PROD-2409](https://linear.app/aura-health/issue/PROD-2409) (profile-user generalization) · parent [PROD-2154](https://linear.app/aura-health/issue/PROD-2154).
- **Origin:** the 2026-06-04 manual cancellation sweep — this skill automates it, sequentially. The 2026-06-08 manual #aurie-feedback sweep (4 users invisible to the existing k8bot sweep) motivated the second channel.
