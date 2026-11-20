---
name: investigate-pmf-survey
description: >
  Sweeps `New PMF Survey Response` k8bot messages in Slack #ai-feedback over a window,
  profiles each respondent one at a time, and emits a Sean-Ellis aggregate that is segmented
  before it is judged: split by tenure bucket (Day 0-7 / 8-29 / 30+) and by content-habit,
  internal accounts excluded, and the 40% benchmark applied to the Aurie-anchored
  tenure-mature cohort rather than the blended population that under-reports fit. A thin
  orchestrator over `data:profile-user`. Input is a window (default 30d) or a Slack URL. A
  lighter cadence mode dedups per thread. Trigger on "investigate PMF survey responses",
  "review this month's PMF responses", "sweep the PMF feedback", "analyze PMF sentiment", "who
  gave not disappointed on PMF", "what's our PMF score this week", "show me the PMF results",
  or "run the PMF cadence sweep". Do NOT use for a single known UID (`data:profile-user`),
  cancellation feedback (`data:investigate-cancellation-feedback`), or rating triage
  (`data:profile-user` with the rating URL).

metadata:
  version: "3.9.0"
knowledge:
  reads:
    - Guide/Index               # baseline routing aid (universal)
    - Compliance/Privacy        # PII rule — UID prefix + email domain only on every Slack surface
    - Products/Aurie       # brand voice for the digest's framing
    - Team/Internal Accounts    # employee / internal-account allowlist — catches employees on personal accounts the Aura-API role/email rules miss (e.g. a founder's google:… login)
    - Tools/Slack               # Slack channel registry — #ai-feedback channel ID + bot access pattern
    - Tools/Aura API            # auth + base URL for the segmentation join (GET /users/<id>)
    - Tools/Aura API/Subscriptions  # canonical content-sub signal (POST /subscriptions/list/<id>)
    - Tools/Mixpanel            # cadence mode's lightweight per-entry engagement pull (bundled mixpanel_api.py)
    - Tools/OpenAI                   # OpenAI project keys + stored-completions access
---

# investigate-pmf-survey

A thin **orchestrator**. It sweeps the `New PMF Survey Response` k8bot messages in #ai-feedback and, for each real (non-test) respondent, calls [`data:profile-user`](../profile-user/SKILL.md) to produce the per-user picture, then rolls everything up into a **segmented** Sean-Ellis aggregate digest. It does **not** re-implement behavioural / conversational analysis — that all lives in `profile-user` and its sub-skills.

**Why segment before reporting (PROD-2665).** A single blended "Very disappointed %" systematically *under-reports* Aurie's fit, because two confounds depress the answer independently of how well Aurie fits the user:

1. **Tenure** — Day 0-7 respondents haven't formed a habit; the survey often fires that early, so their answer is a timing artifact, not a fit signal.
2. **Content-habit** — users with an established Aura *content* listening habit answer the Aurie-specific question lower because content is a fallback. The discriminator is content-habit, **not** account age (two 5-yr accounts split: a heavy content listener said "Somewhat"; a near-zero listener said "Very").

So the digest splits scores by tenure bucket **and** content-habit, excludes internal/AI-reviewer/admin accounts, and applies the 40% benchmark to the **Aurie-anchored, tenure-mature** cohort.

What this skill owns:

- Parsing the `New PMF Survey Response` message shape and filtering test entries (deterministic script: [`scripts/fetch_pmf_responses.py`](scripts/fetch_pmf_responses.py)).
- The deterministic **segmentation join** — for every real respondent the script joins the UID against the Aura API (`GET /users/<id>` + `POST /subscriptions/list/<id>`) to derive tenure bucket, content-habit, and internal-account flags over the full Sean-Ellis denominator (not just the deep-profiled subset).
- The **one-at-a-time, interactive** review loop.
- The opt-in threaded Slack reply per profile (it owns this; `profile-user` runs with `side_effects: off`).
- The segmented aggregate digest (Sean-Ellis distribution split by tenure + content-habit, internal-exclusion, benchmark applied to the mature Aurie-anchored cohort, + cross-cutting patterns).

**Why one at a time, not a parallel fan-out.** Fanning out ~20 `profile-user` agents simultaneously hits OpenAI / ElevenLabs rate limits and loses in-flight agents to interrupts. This skill instead walks entries sequentially: profile → show the user → optionally post a threaded reply → advance. The user stays in the loop and can skip, stop, or re-steer at any entry.

## When to use

- "investigate PMF survey responses" / "review this month's PMF responses"
- "sweep the PMF feedback" / "analyze PMF sentiment"
- "who gave not disappointed on PMF" / "what's our PMF score"
- pastes an #ai-feedback channel or thread URL and asks to review PMF responses

**Do NOT use for:**

- A single user when you already have a UID → [`data:profile-user`](../profile-user/SKILL.md) (Mode B).
- Cancellation feedback → `data:investigate-cancellation-feedback`.
- A single conversation rating (QP / coaching / chat) → `data:profile-user` with the rating URL.

## Inputs

- **`mode`** (optional, default `interactive`) — `interactive` runs the Steps 0–5 workflow below (a human invoking the skill directly, or an on-demand aggregate/segmented digest); `cadence` runs the lightweight per-entry loop described in "Cadence mode" further down, deduped on Slack thread state — built for an external recurring job, not for interactive use.
- **`slack_url`** (optional) — a Slack permalink. Three shapes:
  - a **channel** URL → sweep that channel over `window`;
  - a **thread** URL → sweep, and offer to land the aggregate digest as a reply on that thread;
  - a **single PMF-message** permalink → profile just that one respondent.
- **`window`** (optional, default `30d`) — `7d` / `14d` / `30d` / `Nd` / `YYYY-MM-DD..YYYY-MM-DD` (inclusive). Ignored when `slack_url` is a single message.
- **`channel_id`** (optional, default `C0AENS8D48N` = `#ai-feedback`). Canonical mapping in AI Knowledge → `Tools/Slack` → Channel IDs (for skills); the default here mirrors that table. Override when sweeping a different channel for a one-off run.
- **`analysis_depth`** (optional, default `meaningful_text_only`):
  - `aggregate_only` — Sean-Ellis stats + digest, no per-user `profile-user` runs;
  - `meaningful_text_only` — deep-profile only respondents whose feedback is non-empty and not a canned survey answer;
  - `all` — deep-profile every non-test respondent (including canned-answer / empty-feedback ones).

## Prerequisites

- **Slack is MCP-first** (best-practices principle 11). The channel **fetch** (`fetch_pmf_responses.py`, `conversations.history`) is API-first by necessity — the script paginates + filters the window. The opt-in threaded **reply** (Steps 4–5) is agent-driven: prefer the Slack MCP connector to post it, falling back to `shared:post-to-slack`'s `slack_post.py` with **`SLACK_BOT_TOKEN`**. The bot must be a member of the channel.
- **`AURA_API_TOKEN`** — consumed **directly by the fetch script** for the segmentation join (`GET /users/<id>` + `POST /subscriptions/list/<id>`). Graceful degrade: if it's missing (or you pass `--no-enrich`), the script still emits the raw sentiment distribution but **omits** the `segments` / `internal_excluded` / `benchmark` blocks, and the digest reports segmentation as "unavailable — enrichment skipped." A 401 on `/subscriptions/list/<id>` specifically is the role-allowlist gap (system role added in PaymentService 2.185.2); the join falls back to the user-row `premium` mirror and continues. Same token the `profile-user` chain uses.
- **`profile-user` chain env** (`mode=interactive` only) — `profile-user` runs `analyze-user-behavior` (Mixpanel: `MIXPANEL_SERVICE_ACCOUNT_USERNAME` / `_SECRET` + **`MIXPANEL_PROJECT_ID=963821`**; Aura API: `AURA_API_TOKEN`) and `analyze-user-conversations` (`OPENAI_CONVERSATION_API_KEY` — a service-account key scoped to `Conversation [Production]`; reads OpenAI stored completions. **Not** `OPENAI_ADMIN_API_KEY` — Admin keys can't hold the `model.request` scope this read now requires, [PROD-3572](https://linear.app/aura-health/issue/PROD-3572)). Note `cost` is **not** in sources for PMF — `eng:report-user-costs` is not called. This skill doesn't check those directly — `profile-user` and its sub-skills run their own prerequisite checks and degrade per their own docs.
- **Mixpanel creds** (`mode=cadence` only) — `MIXPANEL_SERVICE_ACCOUNT_USERNAME` / `_SECRET` / `_PROJECT_ID`, consumed directly by the bundled [`scripts/mixpanel_api.py`](scripts/mixpanel_api.py) for the per-entry engagement snapshot (cadence mode never calls `data:profile-user` or `analyze-user-behavior` — see "Cadence mode" below for why). Same Aura API token as above for the email-domain + subscription-state lookup.

**TLS gotcha.** Direct `urllib` / `curl` HTTPS calls fail in some sandboxes with `SSL: CERTIFICATE_VERIFY_FAILED`. The fetch script and any Slack post use the Python `requests` library (it bundles certifi). Don't reach for `curl`.

**Cowork caveat.** `mcp__workspace__bash` strips host env (PROD-1305), so `SLACK_BOT_TOKEN` and the `profile-user` chain's tokens won't reach a Cowork sandbox. Run from the host harness, or use the per-session paste-export workaround in [`/CLAUDE.md`](../../../../CLAUDE.md) → "Known Cowork constraints".

## Workflow

### Step 0 — Resolve inputs

Resolve `channel_id`, `window`, and `analysis_depth` from the inputs. If `slack_url` was given, decode it:

- `https://<ws>.slack.com/archives/<channel_id>/p<ts_no_dot>` → `channel_id` is the path segment after `/archives/`; the `p<digits>` slug becomes a ts by inserting a `.` before the last 6 digits.
- Fetch the decoded message and check if it is a `New PMF Survey Response` message → **single-message mode** (pass `--single-ts <ts>` to the script; skip the window sweep).
- If it is a channel root or any other thread → sweep mode; remember `channel_id` + `thread_ts` for the optional Step 5 digest reply.

### Step 1 — Fetch + parse + filter (deterministic)

**First, pass the employee allowlist to the script.** Pass `--internal-accounts-file ${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/team__internal-accounts.md` — the `Team/Internal Accounts` page as `retrieve-knowledge` caches it (a `knowledge.reads` path). The bundled [`internal_accounts.py`](scripts/internal_accounts.py) reads every Firebase UID, provider id (`google:…` / `apple:…`) and email row off it (email rows, including wildcard test-alias patterns, match the footer address); never hand-assemble `--internal-ids` ([ENG-1436](https://linear.app/aura-health/issue/ENG-1436)). Pass the employee first names as `--internal-names`. The script matches ids as a **hard** internal flag (`internal_reason=allowlist:<uid-prefix>` — prefixed, never the raw id, since the digest prints the reason) independent of the Aura API join — this is what catches employees on personal accounts (e.g. `google:…` logins) the `@aurahealth.io` / role rules miss. Names are a **soft** signal only (reported under `internal_excluded.possible_by_name`, never auto-excluded — real users share first names). Both are optional; omit them and the script behaves exactly as before.

Run the script with `--json` to write output atomically to a file, then read from that file: (`$OUT` is your worker folder, e.g. `<scratchpad>/g3_main/`, never the scratchpad root or a fixed `/tmp` name: see `data:run-all-analysis-cadence` → *Scratch files and posting*)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-pmf-survey/scripts/fetch_pmf_responses.py \
  --channel <channel_id> --window <window> \
  --internal-accounts-file ${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/team__internal-accounts.md --internal-names "daniel,steve" \
  --json "$OUT/g3_pmf_responses.json"
# single-message mode:
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-pmf-survey/scripts/fetch_pmf_responses.py \
  --channel <channel_id> --single-ts <ts> \
  --internal-accounts-file ${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/team__internal-accounts.md --internal-names "daniel,steve" \
  --json "$OUT/g3_pmf_responses.json"
```

Then load the results from the file — never pipe stdout through inline Python transformations:

```python
import json, os
data = json.load(open(os.path.join(os.environ["OUT"], "g3_pmf_responses.json")))
```

**⚠️ Never pipe stdout through inline Python.** Piping `fetch_pmf_responses.py` output through a shell `| python3 -c '...'` one-liner can corrupt field values — UIDs and feedback text have been observed to mangle (correct first 8 chars, garbled remainder) when stdout is transformed inline. Always write to a file with `--json` and read from it.

The script parses the `New PMF Survey Response` shape (`*<Sentiment> if could no longer use Aurie*`, one free-text line, and a `<First Name>[ - Day <N>] - <UID>` footer — where `<UID>` is a Firebase UID **or** a provider-prefixed auth id like `google:…` / `apple:…`, so SSO-account entries aren't dropped), applies the **test filter** (feedback text lowercased/stripped is `test` / `testing` / `test dev` or starts with `test`), runs the **segmentation join** (unless `--no-enrich`), overlays the `--internal-ids` / `--internal-names` allowlist, and writes structured JSON.

**Two body shapes.** k8bot emits the body either plain or as a Slack **blockquote**, in which case `conversations.history` delivers the sentiment + free-text lines HTML-escaped and marker-prefixed (`&gt; *Very disappointed…*` / `&gt; No Feedback`). Both are live in any window longer than a day. The parser handles both and stores the **normalised** free text in `feedback` — one leading marker stripped, then `html.unescape` — so the card's quoted line, the `steering` / `context.stated` strings, the digest snippets and the test filter all read the same clean value and none of them strips anything itself. The strip is gated on the sentiment line also carrying the marker: that is what distinguishes k8bot's rendering from a respondent who typed `>`.

**User-id shapes, and unparsed messages.** The footer's trailing id is a 28-char Firebase uid, a provider-prefixed auth id (`google:…`), or a **legacy UUID** from an account predating Firebase — all three parse. The pattern stays validating rather than permissive because the id is the last field on the line: anything loose turns a malformed footer into a fabricated user id. A message carrying the PMF header that does **not** parse is counted in `counts.unparsed` and listed in `unparsed_messages` (with permalink), and the script warns on stderr — a parse failure must never be a silent skip, which is what let both a dropped respondent and a sender format change go unnoticed. **A non-zero `unparsed` means the sender's format changed; read the listed messages before trusting the run's counts.**

**`Day N` footer segment.** The footer is `[<First Name> - ][Day <N> - ]<UID>`: the sender joins the non-empty parts, so the name and the day are **independently optional** — a user with no display name yields `Day 5 - <UID>` or a bare `<UID>`, and neither may be read as a name or drop the respondent. `N` lands in **`day_n`** (`null` when absent) and never in `first_name`. It is **captured, not consumed**: `tenure_bucket` stays derived from the Aura API's `aiCreatedAt`, so `day_n` is an independent signal and a free cross-check on that join — don't wire it into segmentation.

**Segmentation join (PROD-2665).** When `AURA_API_TOKEN` is set, the script enriches every real entry from the Aura API and adds these fields per entry plus rolled-up aggregates:

- Per entry: `tenure_days`, `tenure_anchor` (`aiCreatedAt`, fallback `createdAt`), `tenure_bucket` (`0-7` / `8-29` / `30+` / `unknown`), `content_anchored` (bool/null) + `content_signal` (e.g. `content_sub`, `listens=804`, `streakMax=124`, `none`), `is_internal` (bool) + `internal_reason` (e.g. `role=admin`, `isAIReviewer`, `email_domain=aurahealth.io`, `email_test_alias`, `test`, `ai_lifetime_free`), `is_coach` (flag only — not an exclusion), `aura_found`, `email_domain` (domain only — the script never retains the address).
- `enrichment` — whether it ran + the thresholds/role-set used (so the digest can cite the exact rule).
- `internal_excluded` — `{count, entries:[{uid_prefix, sentiment, email_domain, internal_reason}]}`. `uid_prefix` keeps the auth provider as a readable label and takes its 8 characters from *after* it (`google:111555896239269076429` → `google:11155589`); a bare `uid[:8]` would render every `google:…` account as the string `google:1`, and provider-prefixed ids are exactly the allowlist accounts this block exists to surface. Firebase-28 ids are unchanged. Shared derivation — see `/CLAUDE.md` → "Canonical source for the `uid_prefix()` derivation".
- `counts.internal_excluded` / `counts.real_non_internal`, and **two** distributions: `sentiment_distribution` (raw, every real entry) and `sentiment_distribution_non_internal`. **Read the non-internal one for anything Sean-Ellis** — the raw one exists to keep the full observed record, not to be scored.
- `segments` — `by_tenure`, `by_content_habit`, and the `by_tenure_x_content` cross-tab, each a `{Very, Somewhat, Not, total}` counter, **internal-excluded denominator**.
- `benchmark` — `blended_very_pct` vs `aurie_mature_very_pct` (cohort = Aurie-anchored **and** tenure `30+`, internal excluded), `n_aurie_mature`, `meets_40`, `small_n` (cohort `< 10`), plus the unresolved split `blended_resolved_very_pct` / `n_blended_resolved` / `n_blended_unresolved` (see "Unresolved respondents" below).

**Footer-email rule (ENG-1141) — fires without the Aura API.** The footer's `<First Name>` slot is free text, so what the survey captured lands there verbatim, and for QA accounts that is an address, Slack-wrapped as `<mailto:a@b|a@b>`. The script therefore parses the slot into a display name + an address **before** any API call and classifies on it: `@aurahealth.io` → `internal_reason=email_domain=aurahealth.io`; a disposable test alias → `email_test_alias`, matched per local-part token (split on `. + _ -`, token matching `test`/`testing`/`test<N>`) so `contest@…` is not swept in. This is the [`Team/Internal Accounts`](https://www.notion.so/39175e23af208198a437ce2c198b36ef) "match the email pattern, not just the listed UIDs" rule (the Yawar `<redacted-email>` precedent), applied to any domain.

Independence from the API is the whole point: `GET /users/<id>` **404s on QA and deleted accounts** — the very accounts the rule exists to catch — so a domain rule that reads only the user record cannot fire on them. The Aura-API join is therefore **additive**: neither its 404 branch nor its found branch may clear a flag the footer set. On the 2026-08-11 window, two `@aurahealth.io` QA submissions rode a 404 into the score at 45.2% blended against 41.4% without them.

Two consequences for output: the display name never carries an address forward (`first_name` is empty when the slot held one — otherwise a card that simply prints the name violates the PII rule below), and only the **domain** is retained anywhere on the entry.

**Allowlist overlay (employee accounts the Aura-API join misses).** The script's Aura-API `is_internal` is derived from the user record (`role` / `isAIReviewer` / `@aurahealth.io` email / `test` / `ai_lifetime_free`). That misses employees signed in on **personal accounts** — a founder on a personal `google:…` login has none of those flags. The script applies the `Team/Internal Accounts` allowlist directly (via `--internal-accounts-file` + `--internal-names`, see the run step above): it matches each entry's `authId` (Firebase UID **or** `google:…` / `apple:…` provider id) as a **hard** flag → `is_internal=true`, `internal_reason=allowlist:<uid-prefix>`; a display-name match is a **soft** signal → reported under `internal_excluded.possible_by_name`, `is_internal` left untouched (never auto-excluded — real users share first names). This runs even when `--no-enrich` / the Aura token is missing, so the flag survives without the API. It's what catches the exact case that prompted the change (`Daniel - google:111555896239269076429`).

**Content-habit rule:** content-anchored if a `type:content` subscription row exists, OR `listenedCount ≥ 25` (lifetime *count* of listens — not `listenedTotal`, which is cumulative minutes), OR `streakMax ≥ 7`; else Aurie-anchored. Thresholds are tunable constants at the top of the script.

**Answers that aren't free text** are **not** test entries — they are real respondents who skipped the free-text field. Two sources, tracked separately because they come from different places: the **five canned options** the app offers on the "Not disappointed" path (`did_not_help`, `did_not_need`, `did_not_understand`, `only_tried_briefly`, `other` — source of truth is `AuraConsumerApp`'s `PMFNotDisappointedOption` enum, *not* the channel, which only shows options someone has picked so far), and the backend's **blank-field placeholder** `No Feedback`, which unlike the five appears on all three sentiment paths. The script marks them as `feedback_is_default: true` so the orchestrator can handle them with `meaningful_text_only` depth. Matching is exact against the normalised text (see "Two body shapes" above) — a default embedded in a longer sentence is real free text, not a default.

**Duplicate UIDs.** Unlike cancellations, the PMF survey can fire at multiple lifecycle points per user — the script flags repeat UIDs in `duplicate_uids` but does **not** drop them. Report them in the roster.

**A repeat *count* is a lifecycle artifact; a repeat *burst* is a different finding.** Two submissions weeks apart are the intended multi-lifecycle-point case. A double-digit count inside one window, minutes apart, cycling between sentiments, is a QA loop — the same account cannot have three different answers to "how would you feel losing Aurie" in an hour. Both land in `duplicate_uids` identically, so the count alone does not separate them: read it against the entries' timestamps and whether the sentiment varies. Never drop either (see "Unresolved respondents stay in the blend") — surface the burst so a human can allowlist the account, which is the only durable fix when no API-derived rule can reach it.

**Unresolved respondents stay in the blend.** An entry whose UID doesn't resolve (`aura_found=false`) is **kept** in `benchmark.blended_n` and excluded only from the mature cohort, which needs a tenure it doesn't have. Dropping it would bias the score rather than clean it: a 404 is most often a *deleted* account — a churned real user, and churned users skew "Not disappointed". The residue is made visible instead of absorbed: compare `blended_very_pct` against `blended_resolved_very_pct` (resolved subset only) and report `n_blended_unresolved` whenever it's non-zero. Internal/QA accounts among the unresolved are already handled upstream by the footer-email rule, which needs no API call — so an unresolved entry that reaches the blend is one we have no reason to think isn't a real user.

### Step 2 — Show the roster, then act (plan-before-side-effects)

Before profiling anyone, show the user:

- Parsed / test-dropped / real entry counts, plus the `internal_excluded` count (and reasons).
- **Sentiment distribution**: Very / Somewhat / Not disappointed (count + %) from `sentiment_distribution_non_internal`. Show the raw `sentiment_distribution` beside it only when `counts.internal_excluded > 0`, so the size of the exclusion is legible.
- **Sean-Ellis headline (segmented)**: lead with `benchmark.aurie_mature_very_pct` (Aurie-anchored, tenure-mature, internal-excluded) vs the 40% benchmark, and show `benchmark.blended_very_pct` beside it as the *under-reporting* contrast. Flag `small_n` when the mature cohort is `< 10`.
- **Segment preview**: the `by_tenure` and `by_content_habit` "Very %" per bucket, so the user sees the split before any deep profiling.
- Duplicate UID count if non-zero (same user submitted more than once in the window).
- Which entries will be deep-profiled given `analysis_depth`, and that each runs `profile-user` sequentially.
- Threaded Slack replies (if applicable) are opt-in.
- If enrichment did **not** run (`enrichment.ran=false`), say so here and note the segments/benchmark are unavailable for this run.

This is the moment to slow down — the user can narrow the window, switch depth, or pick specific entries before any sub-skill runs.

### Step 3 — Per-user loop (one at a time)

Skip this step entirely when `analysis_depth=aggregate_only`.

Pick the in-scope entries: `meaningful_text_only` → entries with non-default feedback; `all` → every real entry. Then loop **sequentially** (do not fan out). For each entry:

1. Invoke [`data:profile-user`](../profile-user/SKILL.md) via the `Skill` tool in Mode B with:
   - `user_id=<uid>`
   - `context = { kind: "pmf", stated: "<feedback text>", category: "<sentiment>" }` (use `stated=""` when `feedback_is_default` — the profile then leans on the categorical sentiment + behaviour)
   - `steering="<sentiment> — <feedback text>"` (omit the feedback portion when `feedback_is_default`)
   - `sources=[behavior, conversations]` — **cost is omitted for PMF**
   - `side_effects={ slack_reply: off, notion_record: off, braintrust_dataset: off }` — **the orchestrator owns persistence**
   - `time_window=30d`
2. Present the returned profile (lead with UID prefix + sentiment + **PMF explanation** and its tenure check).
3. **Carry the segmentation flags forward** (already computed deterministically by the script in Step 1 — the profile *confirms/enriches* them, it does not re-derive them):
   - **Internal/non-customer**: the entry's `is_internal` + `internal_reason` (`role=admin`, `isAIReviewer`, `email_domain=aurahealth.io`, `test`, `ai_lifetime_free`). These are dropped from every aggregate. If the `profile-user` behavior section surfaces a creator/internal signal the script missed (e.g. an Aurie lifetime-free row the subs call didn't see), note the discrepancy and prefer excluding.
   - **Tenure**: the entry's `tenure_bucket` + `tenure_days` (anchored on `aiCreatedAt`). Cross-check against the behavior section's onboarding/journey timeline.
   - **Content-habit**: the entry's `content_anchored` + `content_signal`. Cross-check against the behavior section's listen counts / content-app subscription — this is the sharpest confound, so confirm it per profiled user.
4. **If** a `slack_url` / channel was supplied, offer (opt-in, draft-and-confirm) to post the profile as a **threaded reply** under that entry's source message (`thread_ts` from the script). See Step 5 for posting mechanics + PII rule. Never auto-post.
5. Advance to the next entry. The user may say "skip", "stop", or re-steer at any point.

Hold each returned profile + flags in memory for the Step 4 digest.

### Step 4 — Aggregate digest (always)

Compose one digest from the roster + the script's `segments` / `benchmark` / `internal_excluded` blocks + per-user profiles. The segmentation drives the digest — lead with the cohort-corrected number, not the blend.

- **Headline — benchmark applied to the right cohort (AC #4):** report `benchmark.aurie_mature_very_pct` (Aurie-anchored **and** tenure `30+`, internal excluded) against the **40%** Sean-Ellis threshold. Show `benchmark.blended_very_pct` beside it explicitly labelled as the *under-reporting* blend, with the delta. If `benchmark.small_n` (`n_aurie_mature < 10`), state the cohort is too thin to call against 40% and report it as directional only. When `benchmark.n_blended_unresolved > 0`, give `blended_resolved_very_pct` alongside the blend so the unresolved residue's effect is stated rather than silently folded in.
- **Internal / non-customer exclusion** (the broadened AURIE-CREATOR rule): list `internal_excluded` (UID-prefix + reason) and report raw "Very %" (all real) vs adjusted "Very %" (internal removed). The rule excludes `role ∈ {admin, agent, service, system, creator, audio-engineer}`, `isAIReviewer=true`, `@aurahealth.io` email — from the Aura user record **or from the PMF footer's own address**, plus a `email_test_alias` local part on any domain (see "Footer-email rule" in Step 1) — `test=true`, `ai_lifetime_free` creator subs, **and any entry matched to `Team/Internal Accounts`** via the allowlist overlay (`internal_reason=allowlist:<uid-prefix>`) — the path that catches employees on personal accounts. Label each excluded entry with the rule that fired, and separate confirmed matches from soft name-only (`allowlist-name:*`) ones. (`isCoach` is reported as a note, not auto-excluded.) In `aggregate_only` mode the join still runs in the script, so this is computed — not "requires per-user profile to confirm."
- **Tenure split (`segments.by_tenure`):** "Very / Somewhat / Not" count + % for Day 0-7 / 8-29 / 30+ / unknown. Call out the Day 0-7 share — a low score there is a survey-timing artifact, not non-fit. Recommend a tenure-gated re-survey (Day 14 / Day 30) as the structural fix.
  - **Count sub-24h respondents with `tenure_hours < 24`** — the per-entry field from Step 1, not a re-derivation.
  - **Report the sub-24h count separately, inside the Day 0-7 line.** Day 0-7 is too coarse to carry this: a respondent who answered 24 minutes after signup and one who answered on day 6 are different kinds of evidence, and averaging them hides the first behind the second. State it as `Day 0-7: N (of which M answered <24h after signup)`.
  - **Say so in the headline when M is a material share of the window.** These responses stay in the blended score (see the banner rule in cadence mode — they are qualified, not excluded), so the score itself cannot carry the caveat; the prose has to. On the 2026-08-04 window, 11 of 14 respondents sat in Day 0-7 — a blended score reported without that sentence reads as a fit measurement when it is substantially a trigger-timing measurement.
- **Content-habit split (`segments.by_content_habit`):** "Very %" for content-anchored vs Aurie-anchored. This is the sharpest confound: content-anchored respondents answer lower because content is a fallback. Anchor the narrative on the worked example — two 5-yr accounts split on content-habit, not age (heavy listener → "Somewhat"; near-zero listener → "Very"); a new account with heavy Aurie use and no content → "Very."
- **Cross-tab (`segments.by_tenure_x_content`):** show the 30+ × Aurie-anchored cell (the benchmark cohort) against the other cells so the confound's direction is visible at a glance.
- **Per-sentiment feedback snippets**: deduped free-text, excluding canned answers and the blank placeholder. Max 3 per sentiment bucket.
- **Cross-cutting patterns** — 3–5 falsifiable, evidence-cited bullets. Seed with the patterns prior sweeps established; confirm or revise against this window's data:
  - **Content-habit is the dominant blended-score distortion (PROD-2665).** Respondents with an established Aura *content* habit answer the Aurie question lower because content is a fallback — independent of account age. Aggregating them with Aurie-anchored respondents under-reports Aurie's true fit; the content-habit-split score is the meaningful one. *(Falsifiable: if content-anchored and Aurie-anchored "Very %" converge this window, the confound didn't hold here.)*
  - **PMF timing bias is the other denominator distortion.** The survey fires Day 0–7 for many users; a low score from a user who hasn't formed a habit is a survey-design artifact. The tenure-mature (30+) score is more meaningful.
  - **"Very disappointed" cohort signals genuine emotional attachment**, not just satisfaction — language like "only one who understands me", "pocket therapist" differentiates Aurie from generic AI.
  - **"Somewhat disappointed" cohort is the highest-leverage re-engagement target** — they see value but have a specific friction (price, feature gap, engagement cadence). Each stated reason maps to a product lever.
- **Recommended follow-ups** — concrete, each naming an owner artifact (e.g. a tenure-gated survey trigger, a content-habit-aware survey segment, a feature response to a recurring request, a re-engagement flow for the Somewhat cohort).

### Step 5 — Output + optional digest reply

Return one Markdown digest: aggregate → per-user collapsed cards (UID-prefix only) → patterns → recommended follow-ups → **Sources** (every source permalink + the `profile-user` invocations + the script invocation).

If `slack_url` was a **thread** URL, offer (opt-in, draft-and-confirm) to post the digest's headline + patterns as a reply on that thread.

**Slack posting mechanics (Steps 3 + 5).** Sending on someone's behalf needs explicit per-turn approval — show the draft, require a clear yes, never auto-post. **PII rule (hard, per `Compliance/Privacy`):** UID prefix (first 8 chars) only, email **domain** only — never `givenName` / `familyName` / full email. Post each per-entry reply and the Step 5 digest by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with `channel=<id>`, `thread_ts=<entry thread_ts>` (so it lands as a reply), and **`--footer data:investigate-pmf-survey`** — the helper stamps the canonical versioned footer (`_Generated by `data:investigate-pmf-survey:<version>` · <YYYY-MM-DD>_`) from `metadata.version` deterministically. **Don't** hand-type a footer (the model improvises a non-canonical, version-less one), put one in the message body, or post via a raw `chat.postMessage` (`slack_post.py` uses `requests` internally — same certifi/TLS path). Pace ~0.5s between posts and confirm each post's permalink back to the user.

**Card guards and scratch files (mandatory in cadence mode).** On every post pass `--expect "<uid8>…"` (this entry's redacted UID), `--max-chars 6000` and `--max-chunks 3`: the helper refuses, exit 2 and nothing sent, a body that is not this entry's card. When the caller gives an **output folder** (its worker folder, e.g. `g3_main/`), every file this skill writes goes there under a typed name (`g3_card_<uid8>.txt`), never the scratchpad root or a generic name like `c1.txt`; otherwise pipe the card on stdin (`--text -`). See `data:run-all-analysis-cadence` → *Scratch files and posting*.

## Cadence mode (`mode=cadence`)

A second, lighter-weight workflow for an external recurring job (e.g. the 4-hourly analysis cadence) to keep #ai-feedback replies current without running the full interactive workflow (Steps 0–5) on every tick. It differs from `mode=interactive` in three ways: it never calls `data:profile-user` (too slow per-entry for a tight cadence over a busy channel), it never produces the Step 4 aggregate Sean-Ellis digest (that stays an on-demand, `interactive`-mode product), and it tracks progress by **asking Slack which threads already carry a card**, rather than walking a `window` in an interactive loop.

**Inputs specific to this mode:**

- `window` (default `7d`) — trailing fetch window.
- `channel_id` (default `C0AENS8D48N`), same as interactive mode.
- **A fixed trailing width is correct here, and only because dedup is per-thread.** There is no cursor to outrun: re-fetching an already-carded entry costs one `conversations.replies` call and it is skipped, so the window only has to be wide enough to cover a plausible outage. Widen it for a longer gap rather than reaching for a cursor — a cursor plus a fixed width silently orphans entries between the two on any outage longer than the width (too old for the window, too new to be marked processed, and the cursor advances past them regardless).

**No bootstrap.** There is no cursor file, so there is nothing to seed and no cold-start question to ask. A first run against a channel with uncarded entries cards them — which is the intended behaviour, not a backlog accident.

**Per-tick procedure:**

1. Run [`scripts/fetch_pmf_responses.py`](scripts/fetch_pmf_responses.py) with `--window <window>` — same script as interactive mode, `--internal-accounts-file` / `--internal-names` from `Team/Internal Accounts` as usual.
2. Resolve the bot id once via `auth.test` (expect `U0ATMPESR37`). **If it fails, post nothing for the tick and report it** — a blind dedup check reads every thread as uncarded. Then call `conversations.replies` per entry (pace ~0.35s, Tier-3) and keep only entries whose thread has no message with `user == <bot id>`, excluding the thread parent. Process those **oldest-first**. For each:
   - **`is_internal`** → post the **internal-tester note** (shape below), then move on to the next entry. The note is this entry's entire treatment: no Mixpanel pull, no engagement snapshot, no card, no synthesis — the account is not a respondent, so there is nothing to analyse. It ends work on *this entry*, never the tick; the loop continues through every remaining unanswered entry.
   - **`feedback_is_default`** (canned answer / blank free-text — see Step 1's "Answers that aren't free text" note above) → post a **behavior-only card** (shape below). There is no free-text to quote, so the card omits the quoted-feedback line entirely; the header, engagement block, and Read/Flag synthesis are built from behaviour alone.
   - **otherwise** (real free-text) → post the **full card**: same shape, plus the quoted feedback + a one-line gloss under the header.
3. Nothing to persist — the posted card **is** the record, and the next tick's reply check will skip it. Re-run the reply check immediately before posting (the pre-post race check) so two concurrent sweeps can't double-card the same entry.

**Internal-tester note (replaces the analysis, does not accompany it).**

An `is_internal` entry gets a reply saying exactly that, and nothing more:

```
:wrench: *Internal / test account — not a respondent. Ignore this entry.*
uid `<uid_prefix>` matched `<internal_reason>`. No analysis run: excluded from the Sean-Ellis
denominator and from every segment.
```

Name the **signal**, not the person — `internal_reason` already carries it (`allowlist:<uid-prefix>`, `email_domain=aurahealth.io`, `role=admin`, `test`, …), and the PII rule below is UID-prefix + domain only. Whoever needs the identity can read it off `Team/Internal Accounts`.

**Every internal entry gets this note — never silence.** A bare k8bot message with no reply is indistinguishable from one the sweep has not reached yet, so leaving an internal entry unanswered makes *excluded* unreadable as anything but *pending* to a human scrolling the channel. That is the same asymmetry as *Tag, don't drop* (`/CLAUDE.md`), applied to the Slack surface rather than to a table: the exclusion is correct, so state it rather than leaving it to be inferred from an absence. Claiming the thread — so the next tick's reply check skips the entry instead of re-examining it forever — is the secondary benefit.

**And it is a note, never a card.** Analysing a tester's submission would produce a confident-looking read of a fit signal that does not exist, which is a worse outcome than either the silence or the one line of channel noise. One short line per internal entry is the whole cost, and PMF volume is low (~14 entries per two days).

**Answered-too-early banner (renders above the card).**

Read **`tenure_hours`** off the entry — `fetch_pmf_responses.py` computes it in Step 1 (fractional hours from the tenure anchor to the survey answer). When it is **under 24**, render a banner as the card's **first line**, above the header:

**Do not try to derive this yourself.** `tenure_anchor` is the *field name* used (`"aiCreatedAt"` / `"createdAt"`), never a timestamp, and `tenure_days` floors — so there is nothing on the entry to subtract and nothing that separates 24 minutes from 20 hours. `tenure_hours` exists precisely because that arithmetic has no resolvable input outside the script.

```
:warning: *Answered <N minutes|N hours> after signup — before there was usage to reflect on.*
The engagement snapshot below covers a single sitting, not a usage pattern. Sean-Ellis asks
how you'd feel *losing* the product; a user this new has no counterfactual to answer from.
Read the sentiment as low-evidence — in particular, do **not** read a low score as dissatisfaction.
```

**The response still counts.** It stays in the denominator and in every aggregate — the banner qualifies it, it does not remove it. Never drop a respondent for answering early: withholding a data point looks identical to never having collected it, so the reader cannot tell a filtered population from a thin one. A visibly-caveated response keeps them able to judge.

**Why 24h and not 1h.** The trailing-7d Mixpanel snapshot on a same-day respondent covers one session block, so the "7d" label overstates what is behind the numbers regardless of whether the answer came at 24 minutes or 20 hours. The banner names the actual elapsed time, so the reader can weigh the two cases differently without the threshold having to.

**This is not the `tenure-bias flag`.** That flag fires at `tenure_days <= 14` and renders as a header suffix, so a 24-minute answer and a Day-13 answer look identical under it. The two measure different things and both can render on one card; the banner is deliberately louder because it marks an answer with no usage behind it, not merely a young one.

Worked case — `CYxe45iI`, 2026-08-04: account created 07:40:20, three conversations rated 👍 at 07:58 / 08:02 / 08:04, PMF answered **"Somewhat disappointed" at 08:04:33** — 12 seconds after the third 👍, 24 minutes after signup, against 6 completed conversations, 62 messages, 26.6 minutes of voice and a paid subscription in that same window. The sentiment contradicts the same user's explicit ratings *and* their behaviour within the same minute.

**Card shape (both variants):**

The header carries `sentiment` + `Day <tenure_days>` plus a **flags suffix** — zero, one, or two space-separated flag phrases, appended independently (they are not mutually exclusive):

- `tenure-bias flag` — present whenever `tenure_days <= 14`, on **either** card variant (full or behavior-only).
- `behavior-only (no free-text)` — present whenever the entry is `feedback_is_default` (behavior-only cards only; never on a full card, since a full card by definition has real free-text).
- `submission N of M in window` — present whenever this entry's UID appears in `duplicate_uids`. `M` is that UID's count; `N` is this entry's position among them, oldest-first.

So a Day-8 default entry gets **both** of the first two flags; a Day-30 default entry gets only `behavior-only (no free-text)`; a Day-3 full-text entry gets only `tenure-bias flag`; a Day-40 full-text entry gets neither (a bare `*PMF · <sentiment> · Day <tenure_days>*`). The repeat flag composes with any of those.

**The repeat flag is what makes a burst legible on its first card.** Cadence mode has no roster, so the flag is the only place Step 1's `duplicate_uids` surfaces — without it a reader has to scroll the channel and notice a UID prefix recurring. So when `M` is large and the entries are minutes apart, say so on the `*Flag:*` line and treat the entry per the repeat-burst rule in Step 1 rather than reading its sentiment as a respondent's view. (Sized by a real burst: one QA account, 17 submissions in 111 minutes, 2026-08-24.)

```
[:warning: *Answered <N> after signup — before there was usage to reflect on.* …]   ← banner, when elapsed < 24h
*PMF · <sentiment> · Day <tenure_days>[ · tenure-bias flag][ · behavior-only (no free-text)][ · submission N of M in window]*
_"<quoted feedback>"_ — <one-line gloss>          ← full card only; omitted entirely on behavior-only

*Mixpanel (last 7d)*
• <sessions> sessions · <sent> msgs sent · <received> received · <total> total events
• <cap hits> cap hits · <modals> subscription modals · <purchase taps> purchase taps · <mic uses> mic uses

*Read:* <1–3 sentence synthesis — for behavior-only, lean entirely on the engagement numbers + tenure_days + subscription state, and flag a behavior-vs-sentiment gap when engagement is heavy despite a middling/low sentiment score; for full cards, cross-reference the quoted feedback against the engagement numbers>
*Flag:* <tenure-bias / contradiction / product-signal notes, as applicable>
```

Both variants pull the same lightweight per-entry engagement snapshot. This mode does **not** invoke `analyze-user-behavior` or `data:profile-user` — it calls Mixpanel directly via the bundled [`scripts/mixpanel_api.py`](scripts/mixpanel_api.py) (a copy of the canonical helper in `data:analyze-user-behavior` — see `/CLAUDE.md` → "Canonical source for `mixpanel_api.py`"). Resolve the `distinct_id` via the `UserID` event-property fallback chain (same recipe as the canonical skill's Step 2), pull the trailing-7d event stream **capped `to_date` to yesterday on the Mixpanel project calendar** (today is always partial — see "Whose clock" below), and count: `sessions` = `AI Conversation Session Started`, `sent`/`received` = `AI Conversation Message Sent`/`Received`, `cap hits` = `AI Message Limit Reached` + `AI Conversation Limit Reached`, `modals` = `AI Subscription Modal Seen`, `purchase taps` = `AI Subscription Purchase Button Tapped`, `mic uses` = `AI Microphone Unmuted`. Cross-reference with a direct `GET /users/<id>` + `POST /subscriptions/list/<id>` call for email domain (PII rule below) and current subscription state (provider, autoRenew, expiration) — `tenure_days` / `tenure_bucket` are already present on the parsed entry from Step 1's segmentation join, no second call needed for those.

**Whose clock: the Mixpanel project calendar, not UTC.** Both the cap and the exception below are stated relative to the **Mixpanel project timezone** — the value, and the `/api/app/me` read that resolves it, are canonical on `Tools/Mixpanel` → Project timezone (declared in `knowledge.reads` above, so it is pre-fetched). Mixpanel validates `from_date` / `to_date` against *that* calendar, so a UTC-derived date is a full day ahead of the project for the whole US evening and the export rejects the request outright:

```
400 to_date cannot be later than today; to_date=2026-08-05; today=2026-08-04
```

Derive every date from `mixpanel.project_today()` — never `datetime.now(timezone.utc).date()` or `date.today()`. `export_events` / `find_events_by_uid` clamp **both** ends of the window to the project's current date as a backstop, but a backstop is not a licence to compute against the wrong clock: it corrects a date that is too *late*, and a date that is too *early* passes through it silently, returning a narrower window with no error. The clock has to be right at the call site.

**Same-day-signup exception to the yesterday cap.** When the entry's `tenure_days=0`, capping `to_date` to yesterday returns an all-zero snapshot every time — exactly for the users where the behavior-vs-sentiment gap is most likely (a same-day purchase or heavy first-session usage right before a low/middling PMF answer). For these entries set `to_date = mixpanel.project_today()`, i.e. include today, accepting that today's count is partial/still-accumulating. Every other `tenure_days` value keeps the yesterday cap unchanged.

**`tenure_days=0` does not mean the user joined today.** `tenure_days` floors, so the anchor can fall on the *previous* calendar day and still round to 0 — anchor `2026-08-04T02:33Z`, survey `2026-08-05T01:03Z` is 22.5h, so `tenure_days=0` spanning two dates. A Day-0 entry's activity therefore covers up to two calendar days, not one. That costs nothing in practice: the trailing-7d `from_date` already reaches back well past the anchor either way, which is why the exception only ever has to move `to_date`. Do **not** narrow `from_date` to the anchor's date on the assumption that a Day-0 user has nowhere else to have been active.

**Why behavior-only instead of skip.** In a typical window, the majority of PMF responses arrive with the free-text field left blank (the `No Feedback` placeholder, or one of the five canned options) — every one of them is still a real respondent with real Mixpanel history behind it, including power users whose blank free-text would otherwise mask a strong engagement signal. A behavior-only card still surfaces the tenure-bias flag, the engagement snapshot, and a read on fit; it just can't quote words the respondent never typed.

**Posting mechanics.** Same as interactive mode's Step 5: post via [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md) with `channel=<channel_id>`, `thread_ts=<entry ts>`, and `--footer data:investigate-pmf-survey` for the versioned footer — never hand-type one. **PII (hard):** UID prefix (first 8 chars) + email domain only, never full name or full email, per `Compliance/Privacy`. Pace ~0.5–0.6s between posts.

**No digest.** Cadence mode never produces the Step 4 aggregate — it's a pure per-entry reply loop, meant to run unattended between the periodic interactive sweeps that want the full segmented Sean-Ellis picture. Run `mode=interactive` (any `window`, `analysis_depth=aggregate_only` for speed) whenever the aggregate view is actually needed.

## Output format

A single Markdown digest (`mode=interactive`). Length scales with `analysis_depth`: `aggregate_only` ≈ 30–50 lines; `meaningful_text_only` ≈ 60–120 lines; `all` ≈ 150–250 lines for a busy month. Per-user cards are the compressed `profile-user` output (UID-prefix only), not the full per-section reports. `mode=cadence` has no digest output — see "Cadence mode" above for its per-entry card shape.

## Edge cases

- **Zero real entries (all test).** Report the test-dropped count + `test_reason` breakdown and stop.
- **`profile-user` returns a partial picture** (no Mixpanel profile, or zero OpenAI completions). It already degrades gracefully; carry that note into the digest card.
- **Single-message mode.** One `profile-user` run + a one-line "aggregate" noting it was a single message. Still offers the threaded reply.
- **`aggregate_only` still segments.** The segmentation join runs inside the script (Step 1) over the full real population, independent of `analysis_depth` — so `segments` / `internal_excluded` / `benchmark` are present even in `aggregate_only`. (Contrast with the old design, where the creator check needed a per-user profile.)
- **Enrichment unavailable** (`AURA_API_TOKEN` missing, or `--no-enrich`). The script omits `segments` / `benchmark`. The allowlist overlay still runs, so `internal_excluded` is still present for any `--internal-ids` / `--internal-names` matches (employees are still flagged) — only the Aura-API-derived internal signals and the tenure/content segmentation are missing. Report the raw sentiment distribution and state plainly that the tenure / content-habit split and the cohort benchmark are unavailable for this run — do **not** present the blended number as if it were the corrected one.
- **Small mature cohort (`benchmark.small_n`).** When the Aurie-anchored, tenure-mature cohort is `< 10`, report its "Very %" as directional only and do not declare pass/fail against 40%. Widen the window to grow the cohort.
- **Enrichment partially resolved.** Some UIDs may not resolve to an Aura record (`aura_found=false`, surfaced as `segments.n_enrichment_unresolved` and `benchmark.n_blended_unresolved`). They fall into the `unknown` tenure / content buckets and are **kept** in the blended denominator (rationale under "Unresolved respondents stay in the blend", Step 1) but excluded from the mature-cohort benchmark. Report the count and, when non-zero, `blended_resolved_very_pct` beside `blended_very_pct`. A 404 here is **not** a reason to treat the entry as fake — the internal/QA check already ran on the footer address without the API.
- **Duplicate UIDs.** Report all submissions per UID (newest first); note the tenure trend across submissions if the sentiment changed (e.g. "Not → Very" as user matured — this is signal about survey-timing bias, not data quality).
- **Window spans partial days.** The inclusive `YYYY-MM-DD..YYYY-MM-DD` range ends at `23:59:59` on the end date.
- **Re-running on the same window.** Idempotent — no persistence beyond opt-in Slack replies. Re-running regenerates the digest; if replies were already posted, the per-entry offer notes the prior reply so the user can skip re-posting.

## Key references

- **Per-user engine:** [`data:profile-user`](../profile-user/SKILL.md) — called once per respondent with `context.kind=pmf`, `sources=[behavior, conversations]`, `side_effects: off`.
- **Sibling sweep:** `data:investigate-cancellation-feedback` — same orchestrator shape for cancellation feedback. The broadened internal/non-customer exclusion here (role/`isAIReviewer`/email-domain/test) is a candidate to port to that sibling — follow-up, not done in PROD-2665.
- **Sean-Ellis benchmark:** 40% "Very disappointed" threshold for product–market fit. Applied to the **Aurie-anchored, tenure-mature** cohort (not the blend) per PROD-2665.
- **Segmentation signals:** tenure measured at survey time (`aiCreatedAt`/`createdAt`), content-habit (`listenedCount` / `streakMax` + `type:content` sub), internal flags (`role` / `isAIReviewer` / `email` / `test`) — all from the Aura API user record, **except** the email rules, which also run against the PMF footer's own address so they survive a 404 (ENG-1141). Role taxonomy mirrors UserService `config.userRolesList`.
- **Channel:** `#ai-feedback` = `C0AENS8D48N`. Canonical mapping lives in AI Knowledge → `Tools/Slack` → Channel IDs (for skills); keep this skill's default in sync with that table whenever the channel changes.
- **Linear:** [PROD-2399](https://linear.app/aura-health/issue/PROD-2399) (this skill) · [PROD-2665](https://linear.app/aura-health/issue/PROD-2665) (tenure + content-habit segmentation) · [ENG-1141](https://linear.app/aura-health/issue/ENG-1141) (footer-email internal rule + unresolved-residue split) · parent [PROD-2154](https://linear.app/aura-health/issue/PROD-2154) · engine [PROD-2409](https://linear.app/aura-health/issue/PROD-2409) · sibling [PROD-2398](https://linear.app/aura-health/issue/PROD-2398).
- **Tests:** [`tests/test_pmf_footer_identity.py`](../../../../tests/test_pmf_footer_identity.py) pins the footer parse, the two email rules, the additive-enrichment property, and the unresolved-residue split. Each failure mode here is silent — the entry parses, the run succeeds, the account just lands in the denominator — so the suite asserts the negatives too (no address survives into `first_name`; the 404 branch does not clear the footer verdict; `contest@…` is not a test alias).
- **Origin:** the 2026-06-04 manual PMF sweep — this skill automates it, sequentially. Segmentation by tenure + content-habit added from the 2026-06-10 → 2026-06-12 sweeps (PROD-2665).
- **Cadence mode:** serves the recurring "Group 3" #ai-feedback cadence — an external 4-hourly job that keeps PMF replies current via `mode=cadence` (deduped on Slack thread state, see "Cadence mode" above; [ENG-1055](https://linear.app/aura-health/issue/ENG-1055)). Every non-internal respondent gets a threaded card, including canned-answer entries (behavior-only), so a heavily-engaged user who leaves the free-text blank is never silently dropped from the record.
