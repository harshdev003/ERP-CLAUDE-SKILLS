---
name: investigate-user-rating
description: >
  Sweeps `*[TYPE] RATED*` k8bot messages in Slack #ai-data-intelligence over a window,
  analyses each rating one at a time — negative, neutral or positive —. Negative and neutral
  drive product signal; positive are framed for marketing, with sensitive sessions (minors,
  trauma or abuse) flagged NOT marketing-usable regardless of polarity. Delegates each entry to
  `data:profile-user` or `data:analyze-user-journal` by rating shape. Trigger on "investigate
  negative ratings", "review this week's negative ratings", "sweep the conversation ratings",
  "analyze recent thumbs-downs", "what ratings did we get this week", "analyze positive
  ratings", "sweep all ratings for marketing", "what resonated this week", or a pasted
  #ai-data-intelligence URL. Builds an eval dataset from rated replies too ("eval
  dataset from ratings", "turn ratings into test cases"). Do NOT use for a single rating URL (`data:profile-user`),
  cancellation feedback (`data:investigate-cancellation-feedback`), or PMF responses
  (`data:investigate-pmf-survey`).

metadata:
  version: "3.8.1"
knowledge:
  reads:
    - Guide/Index                              # baseline routing aid (universal)
    - Compliance/Privacy                       # PII rule — UID prefix + email domain only
    - Products/Aurie                      # brand voice for the digest's framing
    - Products/Aurie/Technology/Profile Insights & Card Renderers  # renderer-key enum + what each card is for — read for the profile branch's per-renderer digest breakdown
    - Team/Internal Accounts                   # employee / internal-account allowlist — sub-skills flag employee-originated ratings; digest counts them
    - Tools/Slack                              # canonical channel ID for #ai-data-intelligence (C07MB0TMF5J) — read in Step 0
    - Tools/Aura API/Dialogue Feedback Labels  # complete label taxonomy + per-surface interpretation (must-read for pattern synthesis)
    - Tools/Braintrust                                                 # Braintrust fallback path (BRAINTRUST_API_KEY)
    - Tools/OpenAI                                                     # OpenAI project keys + stored-completions access
  writes:
    - Learnings/Conversations                  # via profile-user (notion_record: on) — records each rated conversation (QP/Coaching/Conversation/Chat)
---

# investigate-user-rating

A thin **orchestrator**. It sweeps the `*[TYPE] RATED*` k8bot messages in #ai-data-intelligence, and for each real (non-test) rating in scope — negative, neutral, or positive — it calls the appropriate sub-skill to produce the per-entry picture, then rolls everything up into one digest. It does **not** re-implement behavioural / conversational / journal analysis — that all lives in `profile-user`, `analyze-user-journal`, and their sub-skills.

**Two lenses, one loop.** Negative and neutral ratings carry *product* signal — what missed, which prompter version, what to fix. Positive ratings carry *marketing* signal — what resonated, quotable moments, capability proof points (multilingual support, somatic techniques that worked, product testimonials). The same per-entry loop handles all three; only the framing of the per-entry write-up and the digest changes by polarity (see "Positive ratings — marketing framing" below).

What this skill owns:

- Parsing all six `*RATED*` message shapes and filtering test entries (a deterministic script, [`scripts/fetch_ratings.py`](scripts/fetch_ratings.py)).
- The **one-at-a-time, interactive** review loop.
- The opt-in threaded Slack reply per entry (it owns this — sub-skills run with `slack_reply: off`). Note: for the four conversation-rated shapes the sub-skill runs with `notion_record: on`, so each rated conversation is recorded to `Learnings/Conversations`; journal + profile ratings stay `notion_record: off`.
- The aggregate digest (shape mix, polarity split, prompter version distribution, cross-cutting patterns).

**Where the analysis stops.** A per-entry review produces an observation about one session, not a prompt change. For the four conversation-rated shapes the `Learnings/Conversations` row is the hand-off, and a prompt engineer makes the call off the accumulated corpus. Read [`references/signal-scope.md`](references/signal-scope.md) before writing any `Signal:` line — it sets what such a line may contain, why the evidentiary weight has to be stated, and why a **designed** behaviour (the sentinel ending a conversation being the worked example) must not be written up as a defect.

The card then closes with a `*Follow-up*` section — what someone could do next, and who: a **candidate** prompt rule paired with the data check that would justify it, a product/eng change, the corpus query, a CS action. It proposes; it doesn't decide, and it never creates a Linear card. Read [`references/follow-up.md`](references/follow-up.md) before writing one.

**Why one at a time, not a parallel fan-out.** Sequential processing keeps the user in the loop at every step — they can skip, stop, or re-steer — and avoids rate-limit collisions when multiple sub-skill invocations hit the same OpenAI / Aura API endpoints concurrently.

## When to use

- "investigate negative ratings" / "review this week's negative ratings"
- "sweep the conversation ratings" / "analyze recent thumbs-downs"
- "what ratings did we get this week" / "look at negative ratings in #ai-data-intelligence"
- "analyze positive ratings" / "sweep all ratings for marketing" / "what resonated this week"
- pastes an #ai-data-intelligence channel / thread / single-rating URL and asks to review

**Do NOT use for:**

- A single rating URL when you already want a full user profile → [`data:profile-user`](../profile-user/SKILL.md) (Mode B, conversation pivot).
- Cancellation feedback → `data:investigate-cancellation-feedback`.
- PMF survey responses → `data:investigate-pmf-survey`.
- Aggregate Mixpanel cohort analysis → the Mixpanel UI.

## Inputs

- **`slack_url`** (optional) — a Slack permalink. Three shapes:
  - a **channel** URL → sweep that channel over `window`;
  - a **thread** URL → sweep, and offer to land the aggregate digest as a reply on that thread;
  - a **single rating-message** permalink → analyse just that one entry (skip the window sweep).
- **`window`** (optional, default `7d`) — `7d` / `14d` / `30d` / `Nd` / `YYYY-MM-DD..YYYY-MM-DD` (inclusive). Ignored when `slack_url` is a single message.
- **`channel_id`** (optional) — read from `Tools/Slack` in AI Knowledge (Step 0 pre-fetches it via `retrieve-knowledge`). The canonical value is `C07MB0TMF5J` (#ai-data-intelligence); callers may override for testing.
- **`polarity_filter`** (optional, default `negative`) — `negative` | `negative_neutral` | `all`. Use `all` for the marketing sweep (includes positive ratings); the standing recurring sweep runs `all` so both the product and marketing lenses stay current. `negative` / `negative_neutral` stay the right choice for a pure product-signal review.
- **`shape_filter`** (optional, default `all`) — `all` | `qp` | `coaching` | `conversation` | `chat` | `journal` | `profile`.
- **`analysis_depth`** (optional, default `meaningful_text_only`):
  - `aggregate_only` — stats + digest, no per-entry sub-skill runs;
  - `meaningful_text_only` — deep-analyse only entries where `feedback` is non-empty (for conversation shapes), or always (for journal and profile shapes — a journal rating resolves its journal by id and so always has content to assess, and a profile rating routes to a full-profile analysis that stands on its own regardless of typed feedback);
  - `all` — deep-analyse every non-test entry (including conversation-shape entries with no feedback text).

## Prerequisites

- **Slack is MCP-first** (best-practices principle 11). The channel **fetch** (`fetch_ratings.py`, `conversations.history`) is API-first by necessity — the script paginates + filters the window. The opt-in threaded **reply** (Steps 3 + 5) is agent-driven: prefer the Slack MCP connector to post it, falling back to `shared:post-to-slack`'s `slack_post.py` with **`SLACK_BOT_TOKEN`**. The bot must be a member of the channel.
  **Note:** invite the `aura-ai` bot to `#ai-data-intelligence` (channel `···` → Integrations → Add apps → `aura-ai`) before first use.
- **Sub-skill chain env:**
  - `data:profile-user` path: `OPENAI_CONVERSATION_API_KEY` (required — a service-account key scoped to `Conversation [Production]`; `analyze-user-conversations` reads OpenAI stored completions via `GET /v1/chat/completions` list with `metadata[conversationId]` filter. **Not** `OPENAI_ADMIN_API_KEY` — Admin keys can't hold the `model.request` scope this endpoint now requires, PROD-3572), `BRAINTRUST_API_KEY` (fallback — when OpenAI returns 0 completions, `fetch_logs.py` automatically retries via Braintrust BTQL against the `Conversation` project; recovers spans with `input` messages inline), `AURA_API_TOKEN` (best-effort)
  - `data:analyze-user-journal` path: `OPENAI_JOURNAL_API_KEY` (required — a service-account key scoped to the OpenAI `Journal [Production]` project; not `OPENAI_ADMIN_API_KEY`, same PROD-3572 reason), `AURA_API_TOKEN` (best-effort)
  - No Mixpanel or OpenAI cost keys needed — `sources=[conversations]` only
  - **Direct transcript fetch (bypass sub-skill chain):** `scripts/fetch_logs.py --conversation-id <UUID>` — uses `OPENAI_CONVERSATION_API_KEY`, falls back to Braintrust BTQL automatically. Use this when the sub-skill chain is unavailable or for ad-hoc sweep analysis. Note: QP spans are stored in Braintrust with `input` as a list, and a real transcript is often trailed by side-effect spans (`quick-processing-done-prompter`, spans with no `prompterName`) whose `input` carries no messages. `_bt_build_conversation()` reads the latest **message-bearing** span, so a trailing side-effect span does not blank the transcript. Read a 0-turn result as evidence the spans genuinely hold no messages.

> **Stored transcripts keep sentinel markers.** A prompt can close a reply with a literal marker glued to the end of the message (`…a good place to stop.**ENDCONVERSATION**`, today only on `quickProcessing` — so `shape_filter=qp` entries see it first). The IntelligenceService strips it from the stream and from the persisted `dialogue.message` but cannot strip OpenAI's stored copy, so a fetched transcript still carries it on the closing turn. Treat it as a completion signal, not as text the user saw and not as a defect — strip it from any turn quoted into a reply or a marketing highlight. Registry: `conversation.sentinels` in the IS `src/config.js`; more markers will follow the same path.

> **Reads both OpenAI surfaces.** `fetch_logs.py` merges Chat-Completions conversations (walked via `GET /v1/chat/completions`) with Responses-API conversations, which are **not enumerable** on OpenAI — the `llmCall` ledger (BigQuery `aura-main.llm.llm_call`) indexes those and each object is then fetched by id. This needs **`GCP_KEY`** in addition to the OpenAI project key. If the ledger read fails the script warns on stderr and returns Chat-Completions-only results — treat that as incomplete, not as the answer. Results carry `api_breakdown` and `responses_source` so you can tell which surfaces were actually read. Full write-up: [`data:analyze-user-conversations`](../analyze-user-conversations/SKILL.md) Step 2.

**TLS gotcha.** Direct `urllib` / `curl` HTTPS calls fail in some sandboxes with `SSL: CERTIFICATE_VERIFY_FAILED`. The fetch script and any Slack post use the Python `requests` library (it bundles certifi). Don't reach for `curl`.

**Cowork caveat.** `mcp__workspace__bash` strips host env (PROD-1305), so `SLACK_BOT_TOKEN` and the sub-skill chain's tokens won't reach a Cowork sandbox. Run from the host harness, or use the per-session paste-export workaround in [`/CLAUDE.md`](../../../../CLAUDE.md) → "Known Cowork constraints".

## Workflow

### Step 0 — Resolve inputs

**Read `Tools/Slack` from AI Knowledge** (declared in `knowledge.reads`) to get the canonical channel ID for `#ai-data-intelligence`. The entry under `Channel Directory → Engineering and Product` lists the channel ID (`C07MB0TMF5J`) and confirms the `aura-ai` bot membership requirement. Use this as the default `channel_id` unless the caller explicitly overrides it.

Resolve `channel_id`, `window`, `polarity_filter`, `shape_filter`, and `analysis_depth` from the inputs.

**Map the request phrasing to `polarity_filter`** (when the caller didn't pass one explicitly) — the default is `negative`, so a marketing/positive request must bump it up or the sweep will silently skip positives:

- "positive ratings", "what resonated", "for marketing", "all ratings", "marketing sweep" → `all`
- "neutral", "lukewarm", "negative and neutral" → `negative_neutral`
- "negative ratings", "thumbs-downs", or no polarity cue → `negative` (default)

`fetch_ratings.py` has no positive-only filter; positives are reached via `--polarity all`, and the digest/loop separate them by polarity from there. When you resolve to `all` off a marketing phrasing, say so in the Step 2 roster ("sweeping all polarities for the marketing lens") so the choice is visible.

**Marketing lens also bumps `analysis_depth`.** When the phrasing resolves `polarity_filter` to `all` for marketing (and the caller didn't set `analysis_depth` explicitly), default `analysis_depth=all`, not the usual `meaningful_text_only`. Most thumbs-up ratings carry no typed `feedback` — the quotable moment or capability proof point lives in the *transcript* (a somatic technique that worked, a multilingual session, an inner-child arc). `meaningful_text_only` would skip exactly those positive conversation entries. Flag the cost in the roster: `all` depth deep-analyses every non-test entry, so a busy day is many sub-skill runs — offer `meaningful_text_only` as the lighter option if the user only wants the text-bearing highlights.

If `slack_url` was given, decode it:

- `https://<ws>.slack.com/archives/<channel_id>/p<ts_no_dot>` → `channel_id` is the path segment after `/archives/`; the `p<digits>` slug becomes a ts by inserting a `.` before the last 6 digits (`p1780465597965429` → `1780465597.965429`).
- If the decoded message is itself a `*[TYPE] RATED*` message → **single-message mode** (pass `--single-ts <ts>` to the script; skip the window sweep).
- If it's a channel root or any other thread → sweep mode, and remember `channel_id` + `thread_ts` for the optional Step 5 digest reply.

### Step 1 — Fetch + parse + filter (deterministic)

Run the script and capture its JSON:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-user-rating/scripts/fetch_ratings.py \
  --channel <channel_id> --window <window> --polarity <polarity_filter> --shape <shape_filter>
# single-message mode:
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-user-rating/scripts/fetch_ratings.py \
  --channel <channel_id> --single-ts <ts>
```

The script parses all six `*RATED*` shapes, applies polarity + shape filters, drops **only** internal accounts, and emits structured JSON.

> **Rating volume is not a test signal — never filter on it.** Volume tracks engagement, so a per-UID rate threshold silences exactly the users worth hearing from: the most prolific raters in any window are long-tenured paying accounts. Prolific UIDs are annotated `high_volume` and reported under `high_volume_uids` so a genuine QA loop stays visible, but they are never dropped. Rationale and measurements: [ENG-1048](https://linear.app/aura-health/issue/ENG-1048).

**Two signals drop an entry, both of them statements about the account itself:**

1. **`--internal-accounts-file ${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/team__internal-accounts.md`** — the `Team/Internal Accounts` page as `retrieve-knowledge` caches it (a `knowledge.reads` path; if the cache file is missing, save the pre-loaded page body to a file and pass that). The bundled [`internal_accounts.py`](scripts/internal_accounts.py) reads every Firebase UID **and** provider id (`google:…`) off it — **never hand-assemble `--internal-ids` from the page**: on 2026-09-28 that dropped both `google:` ids and two founders' ratings reached the sweep ([ENG-1436](https://linear.app/aura-health/issue/ENG-1436)). Rating footers carry the provider id verbatim for accounts created on one, so exact match catches them. A **hard** flag that needs no Aura token, and the only one that catches employees on personal accounts. Check `allowlist.provider_ids` in the output is non-zero; `--internal-ids` still merges in extra ids.
2. **The Aura API ladder** — `role ∈ {admin, agent, service, system, creator, audio-engineer}`, `isAIReviewer`, an `@aurahealth.io` email, `test=true`, or an Aurie lifetime-free creator sub. Same ladder [`data:investigate-pmf-survey`](../investigate-pmf-survey/SKILL.md) uses. `isCoach` is **not** a signal — a coach can be a genuine Aurie user.

The ladder is what catches an internal account nobody has added to the allowlist yet. On the 7-day window at 2026-08-04 it caught 7 UIDs / 10 entries the allowlist alone kept (`role=creator` ×4, `ai_lifetime_free` ×3, `email_domain=aurahealth.io` ×3).

**The join degrades, it never blocks.** It needs `AURA_API_TOKEN` (and `requests`); without either, `enrichment.reason` says why and only the allowlist applies — nothing else is dropped. Keep that direction when editing: analysing one extra rating costs a sweep step, dropping one loses a paying customer's feedback outright. It runs one `GET /users/:uid` + one `POST /subscriptions/list/:uid` per distinct **surviving** UID (~40 UIDs / ~14s on a 7-day negative sweep); `--no-enrich` skips it.

### Step 2 — Show the roster (plan-before-side-effects)

Before analysing anyone, show the user:
- Counts: total parsed → after polarity filter → real (non-test) → test-dropped with `test_reason` breakdown → `skipped (unfetchable)` (entries on the [skip-list](references/unfetchable-ratings.md))
- **State the drop line even at zero** — `0 filtered`, or e.g. `3 filtered (role=creator ×2, allowlist ×1)`. A silent drop is indistinguishable from "nothing happened", so a filter defect can run for months unnoticed. When `enrichment.ran` is false, say so and name `enrichment.reason`: the roster is allowlist-only in that case, so an unlisted internal account is in it.
- Distribution by shape + polarity + prompter (from the script output)
- The plan: which entries will be deep-analysed given `analysis_depth`, that each runs sequentially, and that threaded Slack replies are opt-in

This is the moment to slow down — the user can narrow the window, switch depth, change polarity or shape filters, or pick specific entries before any sub-skill runs.

### Step 3 — Per-entry loop (one at a time)

Skip this step entirely when `analysis_depth=aggregate_only`.

Pick the in-scope entries:
- `meaningful_text_only` → journal and profile entries always (a journal rating resolves its journal by id, so there is always content to assess; a profile rating routes to a full-profile analysis that stands on its own regardless of typed feedback) + conversation-shape entries where `feedback != ""`;
- `all` → every real (non-test) entry.

**Skip known-unfetchable items.** Exclude any entry listed in
[`references/unfetchable-ratings.md`](references/unfetchable-ratings.md) — items
whose conversation/journal can't be fetched from stored completions, so they'd
dead-end every sweep. This exclusion is already surfaced in the Step 2 roster as
`skipped (unfetchable)`. If a lookup below fails as genuinely unfetchable (0
conversations found from a stored-completions miss — **not** a transient 401/429,
which now raises a catchable `FetchLogsError` and is retried per PROD-3653),
append it to that file so the next sweep skips it.

Loop **sequentially** (do not fan out). For each entry, branch on `shape`:

**Conversation-pivot entries (qp / coaching / conversation / chat):**

Invoke [`data:profile-user`](../profile-user/SKILL.md) via the `Skill` tool with:
- `conversation_id=<conversation_id>` (Mode B — conversation pivot; faster than user-id pivot)
- `context = { kind: "rating", stated: "<feedback text or empty>", category: "<polarity>" }`
- `steering="<feedback text>"` (empty string when no feedback typed)
- `sources=[conversations]` — behaviour + cost sub-skills not needed for a single rated reply
- `side_effects={ slack_reply: off, notion_record: on, braintrust_dataset: off }` — **record the rated conversation to `Learnings/Conversations`** (this sweep is the feed for rated conversations); the orchestrator still owns the threaded Slack reply, so `slack_reply: off`. `notion_record: on` records the one rated conversation (dedup on `conversation_id`, so re-sweeps update in place, not duplicate).
- **`record_meta`** — the row metadata this Mode B run can't derive from the OpenAI completion alone; pass it through so Step 7 writes a *complete*, schema-valid row. From the parsed k8bot entry:
  - `slack_thread_url=<permalink>`
  - `prompt_version=<prompter_name>:<prompter_version>` — the **full k8bot footer form** (e.g. `coach-prompter:0.235.4`), not the bare version, so the row records which prompter produced the rating.
  - `source_type` mapped from `shape` — `qp`→`Quick Processing Rated`, `coaching`→`Coaching Rated`, `conversation`→`Conversation Rated`, `chat`→`Chat Message Rated`.
  - `rating` = the **Title-cased** polarity — `positive`→`Positive`, `negative`→`Negative`, `neutral`→`Neutral` (the schema `Rating` enum is Title-case; `context.category` carries the lowercase form and must not be written verbatim).

  `Conversation ID` comes from `conversation_id`. Without `record_meta` the row would be missing `Source Type` (a **required** field) or carry a lowercase `Rating`, and the write would hard-fail validation.
- `user_id=<uid>` **as well as** `conversation_id` — take the uid verbatim from the parsed entry's `uid` field, not the 8-char prefix used in write-ups. **Check it is non-empty first:** a k8bot message over Slack's 4,000-character limit loses the footer that carries it, so the parsed `uid` is blank; pass no `user_id` at all rather than a blank one, and expect the `Tenure & engagement` line to render `unavailable`. `conversation_id` stays the pivot (faster, no user-id field probing), but Step 3d's `GET /ai/usage/engagement/:userId` is user-scoped and the `Tenure & engagement` line this reply requires has no other source. Omit the uid and that call cannot be formed, so the line degrades to `unavailable` on the most common shape.

**Journal entries (shape = journal):**

Invoke [`data:analyze-user-journal`](../analyze-user-journal/SKILL.md) via the `Skill` tool with:
- `journal_id=<journal_id>` — the footer id is the `journal` row primary key, so it resolves the rendered journal directly. Load-bearing: it is what lets the analysis read the entry the rating is about.
- `user_id=<uid>`
- `feedback_text=<fenced block>` — the user's typed rating comment, for steering only
- `rating_polarity=<polarity>`
- `side_effects={ slack_reply: off, notion_record: off, braintrust_dataset: off }`

The sub-skill's write-up quotes the journal's title and content. Carry that through into the reply — a verdict that never names what the journal said isn't checkable by anyone reading the thread.

**Profile entries (shape = profile):**

The rating is about the user's generated profile, so this uses the **full-profile** pivot, not the conversation pivot. Invoke [`data:profile-user`](../profile-user/SKILL.md) via the `Skill` tool with:
- `user_id=<uid>` (Mode B — full-profile pivot; the profile rating has no conversation to pivot on)
- `rating_id=<rating_id>` (the footer `id:`) — **pass it whenever the entry has one.** It's what lets the `insights` source return the exact snapshot the user rated instead of the current profile, which a later recompile may have replaced.
- `sources=[behavior, conversations, cost, insights]` — **request `insights` explicitly.** Without it the run describes the user but never the thing they rated, which on a `No feedback` entry (the common case) leaves no evidence at all.
- `context = { kind: "rating", stated: "<feedback text or empty>", category: "<polarity>" }`
- `steering="<feedback text>"` — when `renderer_key` is set, prefix it so the synthesis focuses on that card (e.g. `"rated the core-quote-renderer card: <feedback>"`)
- `side_effects={ slack_reply: off, notion_record: off, braintrust_dataset: off }` — **the orchestrator owns persistence**

Carry `profile-user`'s artifact label (rated snapshot vs live profile) into the per-entry presentation and the digest — a live read is never reported as what the user rated.

After each analysis:
1. Present the returned report (lead with UID prefix + shape + polarity + one-line headline).
2. If a `slack_url` / channel was supplied, offer (opt-in, draft-and-confirm) to post as a **threaded reply** under that entry's source message (`thread_ts` from the script). Never auto-post.
3. Advance. The user may say "skip", "stop", or re-steer at any point.

Hold each returned report in memory for the Step 4 digest — don't reprint them all.

#### Positive ratings — marketing framing

Positive entries go through the same sub-skill routing, but the per-entry write-up is framed for **marketing**, not product-fix. For each positive entry:

- **Middle section header is `Why positive makes sense`** (parallel to `Why negative/neutral makes sense`) — name *what* landed: the technique, the reframe, the moment the user's state shifted.
- **Call out the quotable moment.** The most marketing-useful positives carry an unprompted endorsement in the `feedback` text (e.g. "That needs to be my mantra for the next 90 days", "I might finally be free", "if I hadn't found this app I might not be in this spot") or a capability proof point (native-quality non-English support, a somatic technique the user confirmed worked, an inner-child/self-worth arc with a felt shift). Surface these verbatim (they contain no PII) in the `Signal:` section, tagged **Marketing-usable**.
- **Content-safety exclusions (hard).** Flag as `⚠️ Do NOT use for marketing` — and route to trust/content-safety review — any positive session that involves a **minor** (references to a parent/guardian nearby, school, age-appropriate cues) or **trauma/abuse-adjacent content** (dissociation, sexual-assault aftermath, self-harm). These may still be excellent internal examples (e.g. trauma-informed consent handling), but never go into external material regardless of the positive rating.
- **Low-signal positives.** A journal-shape rating whose comment is only a generic praise tag ("Helpful insight", "Accurate information", "Clear and concise") is thin as *testimonial* — there is nothing attributable to quote from the user. It is not thin as *evidence*: the journal it rates is fetched by id and carries a title, summary, insights and affirmations. Assess that content, and note the rating as aggregate positive-volume rather than marketing material. Never write up a journal rating as having no content behind it.
- **Duplicate ratings.** The same underlying session is often rated on more than one surface (a `conversation` and a `chat` entry sharing one `conversation_id`, sometimes 2–3×). Reply per thread, but label the repeats as duplicates and **count the session once** in the digest.

**Volume note.** Positives typically outnumber negative/neutral several-fold (~50/day vs. ~15–20/day observed). A per-thread reply on every positive is a real jump in channel volume — expected when running `polarity_filter=all`; flag it to the user if they didn't anticipate it.

### Step 4 — Aggregate digest (always)

Compose one digest from the roster + the per-entry analyses:

- **Employee / internal-originated ratings** — count + UID-prefix list of entries flagged as employee/internal by the per-entry sub-skill (`data:profile-user` Step 3.5 for the conversation-shape ratings; `data:analyze-user-journal` for `*JOURNAL RATED*`). The rating message footers carry `user: <UID>`, so the match runs off the `Team/Internal Accounts` allowlist even on the conversation-only route where no behavioural report exists. Kept **counted** in the shape/polarity/prompter stats (a flag, not a filter) — but surfaced up front so a founder's own thumbs-down isn't read as a genuine user signal.
- **Distribution by shape** (count + %).
- **Distribution by polarity** (negative / neutral / positive — reflects the `polarity_filter` applied).
- **Profile ratings by renderer** *(only when profile entries are in scope)* — count + polarity split per `renderer_key`, with entries whose footer carries no `renderer:` grouped as `whole-profile`. **This is the actionable cut for the shape**: several thumbs-downs landing on one card is a finding about that card, and collapsing them into a single "profile" row hides it. Name the renderer in each per-entry digest line too, for the same reason.
- **Top prompters by negative-rating count** (from `prompter_mix` in script output) — this is the key signal: which prompter version is getting the most thumbs-downs?
- **Per-shape feedback snippets**, deduped, non-empty only.
- **Cross-cutting patterns** — 3–5 falsifiable, evidence-cited bullets. Seed from any per-entry analyses that ran. **Shape guard for removed labels:** do not merge `recommendation_not_helpful` across QP and Coaching/Conversation into a single track-recommender failure — on QP it signals content quality (no track recs); on Coaching/Conversation it may refer to a track suggestion. Treat `invalid_intent` as infra noise and `notification_not_good` as push-notification signal, separate from session-quality patterns.
- **Marketing highlights** (only when the sweep included positives) — the 3–8 most quotable/resonant positive moments, each as `uid-prefix — quote or capability — one-line why it lands`, with any content-safety-excluded entries listed separately under a `⚠️ Not marketing-usable` line. **Dedupe by `conversation_id` first** — the same session is often rated on multiple surfaces, so collapse those to one highlight (don't let the same quote appear 2–3×). This is the hand-off surface for marketing; keep every quote PII-clean.
- **Recommended follow-ups** — concrete, each naming an owner artifact. A Braintrust eval case is in scope: it is cheap and carries no production risk. A **prompter-version or config-param change is not** — that is the prompt engineer's call off the corpus, so surface the pattern and the measurement that would settle it instead. Per [`references/signal-scope.md`](references/signal-scope.md).

### Step 5 — Output + optional digest reply

Return one Markdown digest: aggregate → per-entry collapsed cards (one block each, UID-prefix only) → patterns → recommended follow-ups → **Sources** (every source permalink + the sub-skill invocations + the script invocation).

If `slack_url` was a **thread** URL, offer (opt-in, draft-and-confirm) to post the digest's headline + patterns as a reply on that thread.

**Every negative or neutral per-entry reply ends with a `*Follow-up*` section** when anything is actionable, per [`references/follow-up.md`](references/follow-up.md); the digest's cross-cutting patterns roll up its prompt candidates by prompter.

**Every per-entry reply carries a `*Tenure & engagement:*` line.** Both tenure clauses + lifetime volume (completed conversations · messages · distinct active days) — and nothing else: **subscription state is not part of it**, because section 2b doesn't carry it and the conversation-pivot route never calls `subscriptions/list`. Sourced from `data:profile-user` section 2b — populated by its Step 3d `GET /ai/usage/engagement/:userId` call, which is gated by neither `sources` nor route, so 2b is present on the conversation-pivot, profile-pivot **and** journal routes alike. (Journal entries route to `data:analyze-user-journal`, which has no engagement data of its own; 2b reaches them via `profile-user`'s Step 3d on the `journal_only` route.) **Copy both tenure clauses verbatim; never re-derive them and never substitute a bare date for a hedged clause** — the sub-skill already resolved them through its `references/engagement.md` → "Tenure rendering" rules, and that wording is required on every downstream surface including this reply. A failed engagement call renders `unavailable — <reason>`; zeroes render as zeroes. **Never omit the line** — it is what tells a reader how much weight one rating carries, and its absence is indistinguishable from a dormant account. Use it in the analysis too, not just as a stat line: the same rated artifact supports opposite readings depending on how much material the compiler had.

**Slack posting mechanics (Steps 3 + 5).** Sending on someone's behalf needs explicit per-turn approval — show the draft, require a clear yes, never auto-post. **PII rule (hard, per `Compliance/Privacy`):** UID prefix (first 8 chars) only, email **domain** only — never `givenName` / `familyName` / full email. Post each per-entry reply and the Step 5 digest by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with `channel=<id>`, `thread_ts=<entry thread_ts>` (so it lands as a reply), and **`--footer data:investigate-user-rating`** — the helper stamps the canonical versioned footer (`_Generated by `data:investigate-user-rating:<version>` · <YYYY-MM-DD>_`) from `metadata.version` deterministically. **Don't** hand-type a footer, put one in the message body, or post via a raw `chat.postMessage` (`slack_post.py` uses `requests` internally — same certifi/TLS path). Pace ~0.5s between posts and confirm each post's permalink back to the user.

**Card guards and scratch files (mandatory in cadence mode).** On every post pass `--expect "<uid8>…"` (this entry's redacted UID), `--max-chars 6000` and `--max-chunks 3`: the helper refuses, exit 2 and nothing sent, a body that is not this entry's card. When the caller gives an **output folder** (its worker folder, e.g. `g1_main/`), every file this skill writes goes there under a typed name (`g1_card_<uid8>.txt`), never the scratchpad root or a generic name like `c1.txt`; otherwise pipe the card on stdin (`--text -`). See `data:run-all-analysis-cadence` → *Scratch files and posting*.

## Output format

A single Markdown digest. Length scales with `analysis_depth`: `aggregate_only` ~30-50 lines; `meaningful_text_only` ~60-120 lines; `all` ~150-250 lines for a busy week. Per-entry cards are the compressed sub-skill output (UID-prefix only), not the full per-section reports.

## Active feedback labels (as of Intelligence Service PR #298, 2026-06-24)

**Current dialogue negative options:** `harmful_unsafe`, `not_true`, `not_helpful`, `voice_issue`.

Three options were removed in Intelligence Service PR #298 because they were ambiguous or not meaningful from a user perspective. They may still appear in historical ratings (before 2026-06-24):

- **`recommendation_not_helpful` ("Recommendation not helpful")** — removed because QP has no track recommendations and users adopted it as a generic "didn't help" signal, making it ambiguous across surfaces. On historical QP entries treat it as a content-quality signal (not a track recommender failure); on historical Coaching/Conversation entries it may refer to a track suggestion (verify via dialogue logs).
- **`invalid_intent`** — infrastructure signal (Intelligence Service could not map intent to a valid flow). Treat as infra noise, not a content-quality signal.
- **`notification_not_good`** — about a push notification or in-app prompt, not session content. Treat separately from session-quality signals.

## Edge cases

- **Zero entries after polarity filter.** Report the shape + polarity distribution and stop — there's nothing to analyse.
- **`profile-user` or `analyze-user-journal` returns no OpenAI completions.** Sub-skills already degrade gracefully and flag the gap in their Headline; carry that note into the digest card rather than dropping the entry.
- **Single-message mode.** Behaves like a one-entry sweep: one sub-skill run + a one-line "aggregate" noting it was a single message. Still offers the threaded reply.
- **Bot not in channel.** `channel_not_found` error from Slack API → surface clearly: "The bot isn't in #ai-data-intelligence. Invite it via channel `···` → Integrations → Add apps → `aura-ai`, then retry."
- **Re-running on the same window.** Idempotent — no persistence beyond the opt-in Slack replies. Re-running regenerates the digest; if replies were already posted, the per-entry offer notes the prior reply so the user can skip re-posting.

## Follow-up: an eval dataset from ratings

To test a prompt change against what users complained about, follow [`references/conversation-dataset.md`](references/conversation-dataset.md): rated replies → the exact production call behind each ([`scripts/feedback_dataset.py`](scripts/feedback_dataset.py) `gather`) → an issue tag and judge rubric per row → de-identified rows in Braintrust → `eng:compare-prompt-versions` with an LLM judge, plus the crisis safety net. It reuses Step 1's `fetch_ratings.py` output and runs no sub-skill.

## Key references

- **Routing sub-skills:**
  - [`data:profile-user`](../profile-user/SKILL.md) — conversation-pivot shapes (QP / Coaching / Conversation / Chat rated), `sources=[conversations]`, `slack_reply: off`, `notion_record: on` (records the rated conversation), `braintrust_dataset: off`; also the full-profile pivot for `*PROFILE RATED*` (`user_id` + `rating_id`, `sources=[behavior, conversations, cost, insights]` — `insights` fetches the rated artifact via `eng:build-aurie-user-insights` `mode=read`, `side_effects: off` — no Notion record)
  - [`data:analyze-user-journal`](../analyze-user-journal/SKILL.md) — journal-pivot shape (`*JOURNAL RATED*`), `side_effects: off`
- **Sibling sweeps:** `data:investigate-cancellation-feedback`, `data:investigate-pmf-survey`
- **Channel:** #ai-data-intelligence = `C07MB0TMF5J`. Canonical mapping lives in AI Knowledge → `Tools/Slack`; keep this in sync whenever the channel changes.
- **Linear:** [PROD-2413](https://linear.app/aura-health/issue/PROD-2413/build-datainvestigate-user-rating-orchestrator) · blocked-by [PROD-2411](https://linear.app/aura-health/issue/PROD-2411), [PROD-2412](https://linear.app/aura-health/issue/PROD-2412) · parent [PROD-2154](https://linear.app/aura-health/issue/PROD-2154)
