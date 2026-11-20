---
name: analyze-user-conversations
description: >
  Pulls a single user's recent Aurie conversations from OpenAI stored transcripts
  (Conversation [Production], both surfaces merged) and synthesises a friction-focused report.
  Two pivots: by Firebase UID (every conversation in the window) or by conversation ID. An
  optional feedback string steers the synthesis toward evidence for that signal. Accepts a
  pasted Slack k8bot rating URL — or `user_id` / `conversation_id` directly. Returns an inline
  report, then offers a draft Slack reply, a Learnings/Conversations record, and a Braintrust
  eval-case save. Trigger on "analyze user conversations", "analyze the last conversation for
  user X", "why did user X cancel", "pull cancellation analysis for user X", "what went wrong
  for user X", "analyze this rated conversation", "why did this conversation get a
  thumbs-down", "analyze this neutral-rated session", or a pasted UID / conversation UUID /
  k8bot URL. Do NOT use for aggregate topic reports (`fetch-conversation-topics`) or
  experiments.

metadata:
  version: "3.3.1"
knowledge:
  reads:
    - Compliance/Privacy                # PII redaction rules — load-bearing: OpenAI logs are NOT PII-masked at write time
    - Guide/Index                       # baseline routing aid (universal)
    - Products/Aurie               # brand voice for the report framing
    - Tools/OpenAI                      # auth + "Reading stored completions" access pattern (the migration target)
    - Tools/Mixpanel/Events             # event taxonomy when pulling behavioural context
    - Tools/Aura API                               # auth + base URL for /users/<id> context lookup
    - Tools/Aura API/Subscriptions                 # canonical subscription state for the Context section
    - Tools/Aura API/Dialogue Feedback Labels      # complete label taxonomy + per-surface interpretation (must-read when feedback text is supplied)
    - Tools/Slack                       # canonical Channel IDs (for skills) — Mode A URL parsing + Step 7 reply offer
    - Learnings/Conversations           # check for prior analyses on the same conversation_id
    - Tools/Notion                            # Notion REST / MCP access pattern (NOTION_TOKEN)
    - Tools/Braintrust                        # Braintrust fallback path (BRAINTRUST_API_KEY)
  writes:
    - Learnings/Conversations           # Step 8: record a single (rated) conversation by default; user-pivot backlog off
---

# analyze-user-conversations

End-to-end friction analysis for one user's recent Aurie conversations. Connects OpenAI stored transcripts (**both** the chat-completions and Responses surfaces — see Step 2), the Aura API (user profile), and Mixpanel (behavioural context), then synthesises a report. Designed for cancellation-feedback debugging but works for any user-level investigation.

> **Migrated off Braintrust 2026-06-05** ([PROD-2457](https://linear.app/aura-health/issue/PROD-2457), umbrella [PROD-2456](https://linear.app/aura-health/issue/PROD-2456)). Reads OpenAI's `Conversation [Production]` project — the chat-completions endpoints for Completions traffic, and the `llmCall` ledger plus `GET /v1/responses/{id}` for Responses traffic (which is not enumerable; see Step 2). Access pattern in AI Knowledge `Tools/OpenAI` → "Reading stored completions". The BT enrichment that's gone: auto-generated conversation `title`, `deliberation_seconds`, per-row Topics `classifications`. Polarity in Mode A still flows from the k8bot Slack message; Mode B without a URL has no rating context unless the caller supplies it.

The skill **returns the report as its result**. For a **conversation-pivot** (a single specified/rated conversation) it also records a `Learnings/Conversations` row by default (Step 8; `--no-record` to skip); a **user-pivot** (a whole history) does **not** bulk-record. The caller decides what else to do with the returned report (paste in Slack, feed to another skill).

## When to use

Trigger when the user asks any of:

- "analyze user conversations for `<UID>`"
- "analyze the last conversation for user `<UID>`"
- "why did `<UID>` cancel"
- "pull cancellation analysis for `<UID>`"
- "what went wrong for user `<UID>`"
- "cancellation feedback report for `<UID>`"
- pastes a Firebase UID + a feedback string and asks for analysis
- pastes a **Slack URL** (k8bot rating message in `#ai-conversation-ratings` / `#cancellation-feedback`, or any thread where the analysis should land) and asks for the analysis — Mode A; the skill optionally offers to post a summary back as a thread reply when the run finishes.

Also trigger when the user pastes a `*New Cancellation Feedback*` Slack message from `k8bot` and asks for follow-up analysis — the UID + reason + feedback string are all in that message.

**Do NOT trigger for:**

- Aggregate topic reports across all users → use `fetch-conversation-topics`.
- Experiment-level analysis (per-variant) → use `analyze-experiment`.
- Generic "summarise this conversation" with the transcript already in chat → just summarise it directly; this skill is for fetching transcripts.

## Inputs

Two input modes — pick the one that matches what the caller pasted.

### Mode A — Slack URL

Paste a Slack permalink (e.g. `https://auramobile.slack.com/archives/C07MB0TMF5J/p1778228018407599`). The skill resolves the URL into `channel_id` + `message_ts`, fetches the message by **invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** (it owns the connection — MCP-first, `SLACK_BOT_TOKEN` script fallback), then **detects the message shape** and parses it accordingly. Five k8bot rating shapes are recognised — same set as `data:profile-user`'s Mode A:

- **`*New Cancellation Feedback*`** → extracts `User: <UID>` → `user_id`, `Reason: <category>` (captured for context), and the fenced `Feedback:` block → `cancellation_feedback`. Routes through the **user pivot** (every conversation in the window).
- **`*QUICK PROCESSING RATED*`** → extracts `user: <UID>` → `user_id`, `conversation: <UUID>` → `conversation_id`, the `:+1:` / `:-1:` line → `rating_polarity` (`positive` / `negative`), the optional fenced feedback block → `cancellation_feedback` (overloaded as a generic steering string), and `prompt:<name>:<version>` → captured for the Sources block. Routes through the **conversation pivot**. **Feedback label note:** `"Recommendation not helpful"` was removed from the active dialogue option set in Intelligence Service PR #298 (2026-06-24). If it appears in a historical entry, on QP it means Aurie's conversational output didn't resonate (QP has no track recommendations); on Coaching/Conversation it may refer to a content track suggestion. See `Tools/Aura API/Dialogue Feedback Labels` in AI Knowledge for the current active labels and the full removal context.
- **`*COACHING RATED*`** → same metadata-footer shape as quick-processing; only the header differs. Same extraction, same conversation-pivot routing.
- **`*CONVERSATION RATED*`** → same metadata-footer shape as quick-processing / coaching, but adds a third polarity: `:neutral_face: Neutral` (alongside `:+1: Positive` / `:-1: Negative`). The footer's `prompt:<name>:<version>` is typically `coach-prompter:<version>` — so this header is effectively the broader-emoji-set sibling of `*COACHING RATED*` (the team may be migrating coaching ratings to this header; treat both as conversation-pivot rated messages without trying to disambiguate). Same extraction, same conversation-pivot routing. Polarity is one of `positive` / `negative` / `neutral`. The fenced feedback block often contains the literal string `No feedback` when the user didn't type anything — treat any of `No feedback`, empty, or whitespace-only as no steering (`cancellation_feedback=""`) rather than passing the placeholder through verbatim.
- **`*CHAT MESSAGE RATED*`** → same metadata-footer shape as quick-processing / coaching / conversation-rated. The footer's `prompt:<name>:<version>` is typically `guardrails-handler-prompter:<version>` — this header appears to fire when the rated Aurie reply came from the **guardrails handler layer** (a reply produced via `handoffToGuardrails` for safety / crisis / out-of-scope deflection). Polarity is binary (`:+1: Positive` / `:-1: Negative`) — no `:neutral_face: Neutral` observed on this header to date. Same extraction, same conversation-pivot routing. Useful steering context for the synthesis: the friction often centres on *how* the deflection was phrased (templated vs. mirrored, single-shot vs. sticky across turns).

If the message starts with `*<Header>*` but isn't one of the five shapes above — *don't guess*. Surface to the caller: "Got a k8bot-style message but the header `*<header>*` isn't one of the five I know how to route. Want me to fall back to Mode B (give me a UID or conversation_id directly)?"

If the URL points at an arbitrary (non-k8bot) thread — that's also valid Mode A, but the caller must additionally provide a `user_id` or `conversation_id` for the analysis itself. The URL is then used **only** to anchor Step 7's reply offer.

`channel_id` + `message_ts` are persisted through the workflow regardless of shape — Step 7's reply offer applies uniformly to all Mode A runs.

### Mode B — Direct

Two mutually-exclusive pivots; pick the one that matches what the caller has.

- **`user_id`** (one of two required) — Firebase UID. 28 alphanumeric chars typical (e.g. `0VwbDpKVEva6FvKOSjYWDzb07yi2`). Returns every conversation in the time window.
- **`conversation_id`** (one of two required) — Aurie conversation UUID (e.g. `BF1A3648-73F1-4B96-BD3C-110B8054A602`). Stored as `metadata.conversationId` on every OpenAI completion in the conversation. Returns one specific conversation. Strictly better than the user pivot when the caller already has the ID — see Step 2 Option B for why. Used by `data:profile-user` when routing a k8bot `*QUICK PROCESSING RATED*`, `*COACHING RATED*`, or `*CONVERSATION RATED*` Slack URL.

Mode B skips Step 7 entirely — there's no source thread to reply to.

### Both modes share

- **`cancellation_feedback`** (optional) — free text. The parameter name is a v1 leftover from the cancellation-feedback flow; semantically it's a **generic steering string** for the synthesis. Pass any feedback that should focus the analysis: a cancellation reason, a k8bot rating's typed feedback, or any other signal. When absent, the skill produces a generic friction + positives report. (In Mode A, this is auto-extracted from recognised k8bot shapes — caller can still override.)
- **`time_window`** (optional, default `60d`, ignored when `conversation_id` is set) — one of:
  - `60d` *(default)* — covers two months; catches users who cancel weeks after their last conversation (OpenAI storage bug means 0 completions is not reliable evidence of zero usage — Braintrust fallback runs automatically on 0 matches, so a wider window is essential).
  - `30d` — narrower window when only recent activity is needed.
  - `7d`
  - `last_conversation` — only the most recent conversation by `created` timestamp.
  - `last_n=<int>` — the most recent N distinct conversations.
- **`record`** (optional) — controls the Step 8 write to `Learnings/Conversations`. **Default depends on the pivot: `true` for a conversation-pivot (a single, specified/rated conversation), `false` for a user-pivot** (analysing a user's whole history — we don't bulk-record a backlog; recording is scoped to individual rated conversations). Explicit values override: `true` writes and surfaces the recorded row; `false` / `--no-record` analyses only (used by orchestrators that own persistence, e.g. `data:profile-user`, and by "just analyse, don't record"); `ask` shows the draft and waits for an explicit yes before writing.

If neither `user_id` nor `conversation_id` can be resolved (Mode A parse failure with no caller fallback; Mode B missing both), ask. Don't guess.

## Workflow

### Step 0 — Resolve inputs and check prerequisites

**If Mode A (Slack URL).** Parse the URL — `https://<workspace>.slack.com/archives/<channel_id>/p<ts_no_dot>` (the `p` form) or `/p<ts_no_dot>?thread_ts=<...>`. Convert: `channel_id` is the path segment after `/archives/`; `message_ts` is the `p<digits>` slug with a decimal point inserted before the last 6 digits (e.g. `p1778228018407599` → `1778228018.407599`). Fetch the parent message by **invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** for that `channel_id` + `message_ts` (it owns the connection — MCP-first, `SLACK_BOT_TOKEN` script fallback); only the parent message is needed for parsing.

Detect the shape by reading the first non-blank line of the parent body:

| First line | Shape | Pivot |
|---|---|---|
| `*New Cancellation Feedback*` | Cancellation feedback | user pivot (extract `user_id` + `cancellation_feedback`) |
| `*QUICK PROCESSING RATED*` | Quick-processing rated | conversation pivot (extract `conversation_id`, `rating_polarity` ∈ {positive, negative}, optional feedback) |
| `*COACHING RATED*` | Coaching rated | conversation pivot (same shape as quick-processing) |
| `*CONVERSATION RATED*` | Conversation rated | conversation pivot (same metadata footer as coaching; polarity ∈ {positive, negative, neutral}; "No feedback" placeholder → empty steering string) |
| `*CHAT MESSAGE RATED*` | Chat message rated | conversation pivot (same metadata footer; polarity ∈ {positive, negative}; prompter typically `guardrails-handler-prompter:<version>`) |
| anything else starting with `*` | unknown k8bot shape | stop and ask (see **Inputs → Mode A**) |
| no `*` header / arbitrary thread | not a k8bot rating | the URL is reply-anchor only; require the caller to provide `user_id` or `conversation_id` directly before continuing |

Persist `channel_id` + `message_ts` regardless of shape — Step 7's reply offer needs them.

**If Mode B (direct).** Capture `user_id` *or* `conversation_id` (and optional `cancellation_feedback`) verbatim. No URL fetch.

**Prerequisites.**

```bash
test -n "$OPENAI_CONVERSATION_API_KEY" && echo "OK: OPENAI_CONVERSATION_API_KEY set" || echo "MISSING OPENAI_CONVERSATION_API_KEY"
test -n "$AURA_API_TOKEN"       && echo "OK: AURA_API_TOKEN set"       || echo "MISSING AURA_API_TOKEN (best-effort, will skip user profile lookup)"
```

`OPENAI_CONVERSATION_API_KEY` is hard-required — a service-account key scoped to the `Conversation [Production]` project. **Not** `OPENAI_ADMIN_API_KEY`: OpenAI enforces a `model.request` scope on `GET /v1/chat/completions` that Admin keys cannot hold, by OpenAI design — Admin keys no longer work for stored-completions reads ([PROD-3572](https://linear.app/aura-health/issue/PROD-3572)). `AURA_API_TOKEN` is best-effort — the report can be produced without it, the **Context** section just won't include plan/vertical/signup info.

If `OPENAI_CONVERSATION_API_KEY` is missing: hand off to `update-config` to add it under `~/.claude/settings.json` → `env` block (per [`/CLAUDE.md`](../../../../CLAUDE.md) → "Secrets and environment"). Create the key at <https://platform.openai.com/api-keys> → Create new secret key → Owned by: Service account → Project: `Conversation [Production]`. Restart Claude Code after adding.

**Cowork caveat.** `mcp__workspace__bash` strips host env (PROD-1305), so neither key is reachable inside Cowork sandboxes. See [`/CLAUDE.md`](../../../../CLAUDE.md) → "Known Cowork constraints" for the per-session paste-export workaround.

### Step 1 — Pull AI Knowledge priors

Invoke `shared:retrieve-knowledge` to pre-load the paths declared in `knowledge.reads`:

- `Products/Aurie` — brand-voice priors for how to phrase the report (especially when `cancellation_feedback` mentions things like "not conversational" — the response should mirror Aurie's actual voice expectations, not project a generic chatbot frame).
- `Tools/OpenAI` — auth + the "Reading stored completions (BT migration access pattern)" section: completion shape, /messages endpoint, metadata schema (`metadata.user` for UID, `metadata.conversationId` for grouping), the metadata field reference (server-side `metadata[...]` filtering works; the gotchas are no native **time** filter, retention TBD, and `metadata.name` not `metadata.prompterName`).
- `Tools/Mixpanel/Events` — only when Step 4 runs.
- `Tools/Aura API` — base URL + auth conventions for the `/users/<id>` lookup.
- `Tools/Aura API/Subscriptions` — request/response shape for `POST /subscriptions/list/:userId` (Step 3's second call). Authoritative across Aurie + Aura.

Keep the fetched content in working memory through Steps 2–6. Cite paths and URLs in the **Sources** block of the final report.

If a path fails to resolve (taxonomy drift), note the miss and proceed. Don't block.

### Step 2 — Fetch OpenAI conversation transcripts

The fetch script reads **both** OpenAI API surfaces from the `Conversation [Production]` project and merges them, because Aurie conversations are split across the two by the `aiResponseAPI` A/B. Two entry points — pick by what the caller passed in.

> **Why two surfaces.** Chat-Completions conversations are found by *walking* `GET /v1/chat/completions` with metadata filters. That is impossible for the Responses API: the list form (`GET /v1/responses`) rejects API keys outright — *"must be made with a session key (that is, it can only be made from the browser)"* — so stored responses are **readable but not enumerable**. The `llmCall` ledger (BigQuery `aura-main.llm.llm_call`, read by the bundled `llm_ledger.py`) is the per-user index instead, then each object is fetched by id. Requires `GCP_KEY` in addition to the OpenAI project key.
>
> **If the ledger read fails**, the script warns loudly on stderr and returns Chat-Completions-only results. Treat that as **incomplete**, not as the answer — any user on the treatment arm will look quieter than they are.

Every result carries an `api_breakdown` (how many conversations came from each surface) and a `responses_source` block (ledger rows seen, objects fetched, objects that failed). A conversation can also come back as `openai_api: "mixed"` when the user changed arm mid-conversation — the arm resolves per app run, so a relaunch mid-conversation can switch it.

**Zero results are not all the same.** The `hint` distinguishes three cases: genuinely inactive window; ledger unreadable so only half the sources were checked; and ledger rows exist but their stored objects failed to fetch. Only the first means "this user had no conversations".

**Option A — User pivot (default):** start from a Firebase UID and pull every conversation in the time window.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-user-conversations/scripts/fetch_logs.py \
  --user-id <UID> \
  --window 60d \
  --json /tmp/aurie_user_logs.json
```

Window flag accepts: `60d` (default), `30d`, `7d`, `NNd`, `last_conversation`, `last_n=<int>`.

**Option B — Conversation pivot:** when the caller already has a specific `conversationId` (k8bot rating message in `#ai-conversation-ratings`, Slack thread link, Braintrust UI URL — the conversationId is the same across surfaces), query it directly.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-user-conversations/scripts/fetch_logs.py \
  --conversation-id <UUID> \
  --json /tmp/aurie_conversation.json
```

Strictly better than the user pivot when you have the conversationId:

- Near-instant — a server-side `metadata[conversationId]` filter returns only this conversation's completions (a handful), so the walk exhausts in one or two pages regardless of how busy the project is.
- No need to know the user's Firebase UID up front.
- No noise from unrelated conversations in the window.

`--window` is ignored (with a stderr note) when `--conversation-id` is set — a conversationId query already scopes to one conversation.

The script:

1. Lists `/v1/chat/completions` with `OpenAI-Project: proj_esFyd4eq2VIAjKUn69X1ZFfk` (Conversation [Production]) and a **server-side** metadata filter — `metadata[user]=<UID>` for the user pivot, `metadata[conversationId]=<UUID>` for the conversation pivot — so OpenAI returns only matching rows. The `since_unix` lower bound is still applied client-side (no native time filter). See `Tools/OpenAI` → "Metadata field reference" for the verified filter syntax.
2. Filters:
   - `--user-id` mode: keeps items where `metadata.user == <UID>` OR top-level `input_user == <UID>` (defensive against the two write paths in IntelligenceService).
   - `--conversation-id` mode: keeps items where `metadata.conversationId` matches, accepting both case variants and the snake_case form for forward-compat.
3. Groups matching completions by `metadata.conversationId`.
4. For each conversation, fetches `/v1/chat/completions/{latest_id}/messages` **once** to get the full transcript (system + every user/assistant turn). Parses the messages into user→assistant turn pairs.
5. Returns JSON with shape:
   ```json
   {
     "user_id": "...",
     "window": { "kind": "days", "since_unix": 1780000000, "days": 30 },
     "conversation_count": 4,
     "turn_count": 27,
     "conversations": [
       {
         "conversation_id": "...",
         "started_at": "2026-06-05T19:47:10+00:00",
         "ended_at": "2026-06-05T19:54:52+00:00",
         "completion_count": 24,
         "turn_count": 10,
         "latest_completion_id": "chatcmpl-...",
         "turns": [
           {
             "created": "<latest completion's timestamp>",
             "input": {"role": "user", "content": "..."},
             "output": {"role": "assistant", "content": "...", "tool_calls": null},
             "metadata": {"user": "...", "conversationId": "...", "name": "coach-prompter", ...}
           }
         ]
       }
     ]
   }
   ```
   In `--conversation-id` mode the top-level key is `conversation_id` instead of `user_id`.

**`output.content` is the model's raw output, not what the user saw.** A null one is not an empty turn — a tool call and an unanswered user turn both look that way, so read `tool_calls` too. A turn with `interrupted: true` is a **voice barge-in**: the user spoke again before the reply was spoken, so LiveKit dropped it from history. Its `output` is the reply the model generated (recovered from that call's stored response, id in `reply_source`) or `[reply not in history — likely voice barge-in]` — either way the model did answer, and the user likely did not hear it. And a **sentinel** marker glued to the end of a final assistant turn (`…a good place to stop.**ENDCONVERSATION**`) is a completion signal the service strips from the stream and from `dialogue.message` but cannot strip from OpenAI's stored copy: don't read it as text Aurie showed, as a defect, or as a prompt leak.
Both shapes, plus the sentinel registry to check when a new marker appears: [`references/turn-shapes.md`](references/turn-shapes.md).

**What's gone vs the BT version:** no `rating` field (BT's `review` span has no OpenAI equivalent), no per-row `classifications` (BT's Topics autotag), no `span_id` / `span_attributes`, no per-turn timestamps (every turn carries the conversation's `latest_completion_id` timestamp — exact per-turn timing isn't recoverable without a per-completion `/messages` fan-out, which we don't do for cost reasons).

**Performance characteristic.** Both pivots use a **server-side** metadata filter (`metadata[conversationId]` / `metadata[user]`), so OpenAI returns only matching rows — the walk no longer scans the whole project. `--conversation-id` returns one conversation's handful of completions in one or two pages (near-instant). `--user-id` returns just that user's completions (tens-to-hundreds over 30d, a few pages), then the `since_unix` lower bound is applied client-side (OpenAI has no native time filter). This is a large speed-up over the original client-side-walk approach. See `Tools/OpenAI` → "Metadata field reference" for the verified filter syntax + the "no native time filter" caveat.

If the script reports zero conversations, **stop here** and surface the gap to the user — don't fabricate analysis from no data. The script's `hint` field explains the common causes (pre-2026-05-12 traffic, wrong UID, prompter living in a different OpenAI project).

### Step 3 — Best-effort: pull user profile from Aura API

Two calls. The first gives identity / origin; the second is the canonical subscription state.

```bash
# Identity / origin (vertical, signup, platform, locale, onboarding, acquisition)
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/data:analyze-user-conversations (session ${AINATIVE_SESSION_NAME:-adhoc})" -fsS -H "Authorization: Bearer $AURA_API_TOKEN" \
  https://services.aurahealth.io/users/<UID> \
  | jq '{ id, email, givenName, vertical, locale, sourcePlatform, attributionData, deviceOS, createdAt, loggedInAt, onboarding }'

# Canonical subscription state (Aurie + Aura content app, both products)
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/data:analyze-user-conversations (session ${AINATIVE_SESSION_NAME:-adhoc})" -fsS -X POST -H "Authorization: Bearer $AURA_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"includeDeleted": true}' \
  https://services.aurahealth.io/subscriptions/list/<UID>
```

Capture from `/users/<id>`: `vertical`, `sourcePlatform`, `attributionData`, `createdAt`, `locale`, plus the `onboarding.conversation` quiz answers and `onboarding.highlight` insight when relevant for the Context paragraph (a quiz-vs-highlight mismatch is real signal, especially on cancellation reports).

`attributionData` is an **opaque, schema-flexible JSON blob** carrying acquisition signals. The shape varies across SDK generations and acquisition surfaces — may be newer AppsFlyer-style `af_*` keys (`af_ad_type`, `af_channel`, `af_keywords`, etc.), legacy keys (`installSource`, `campaignName`, `adsetName`, `appsflyerAdName`, `fbAdName`, `keywords`, `agency`, `affiliateId`, `promocode`, `referralCode`, `referrerId`, `siteSourceName`, and many more), referral/promo fields, or organic-only payloads with just `sourcePlatform`. **Never assume a specific shape.** For the **Context** paragraph, render a one-line summary by picking the most informative non-empty values across four buckets — *source/channel*, *campaign*, *creative/ad*, *free-text signal (keywords/referral/promo)* — in that priority. Walk known keys per bucket (source: `media_source` → `installSource` → `source` → `siteSourceName` → `agency` → `affiliateId` → `medium` → `sourcePlatform`; campaign: `campaign` → `campaignName` → `campaignId` → `fbCampaignId`; creative: `af_ad_type` → `appsflyerAdName` → `fbAdName` → `appsflyerAdSet` → `adsetName` → `adId`; free-text: `keywords` → `af_keywords` → `content` → `term` → `referralCode` → `promocode` → `referrerId`) and take the first non-empty value per bucket. If no priority keys match but the payload is non-empty, fall back to *"unrecognised attribution shape — keys: `<comma-separated non-empty keys, max 6>`"*. Format examples: *"Acquired via fb · Aurie_WO_Scale_04-17-26 - Purchase · creative `ChatGPT Relationships -F- 04-15-26`"* for AppsFlyer-paid; *"Organic install (sourcePlatform=AIiOS)"* for organic; *"Referral via promocode `WELCOME10`"* for referral; *"attribution unavailable"* for null/empty. Whichever creative/campaign/referral field actually resolves becomes the load-bearing context when a rated reply hints at expectations mismatch — quote the actual key + value verbatim so the reader can audit the interpretation. Treat the payload as non-PII campaign metadata — safe to render verbatim in the report, Slack reply, and Notion row.

Capture from `/subscriptions/list/:userId`: each row's `type` (`ai` = Aurie; `content` / `coaching` / `business` / `community` / `coach` = Aura), `provider`, `purchaseAt`, `expirationAt`, `autoRenew`, `trial`, `cancelReason`, `cancelFeedback`, `subscriptionCampaign`. **This is source of truth** for whether the user is paying — the `/users` row's `premium*` fields mirror only the `type:"content"` row and are `null` for users whose paid products are any of the other five types, so don't read them as the canonical answer. Full doc: [List Subscriptions By User Id](https://www.notion.so/35975e23af20818088b4e59dd028bbdd); type taxonomy on [Subscriptions](https://www.notion.so/35975e23af20817ab2ded91f7ca8cbbc).

If `/users/<id>` fails (401/403, network error, Cowork sandbox), record "Aura profile lookup unavailable" in **Sources** and continue. Same for `/subscriptions/list/:userId` — record the gap and continue with the transcripts alone; the Context section just won't include subscription state.

### Step 4 — Best-effort: pull Mixpanel behavioural context

Invoke the Mixpanel MCP (`mcp__3330833b-...`) for a lightweight session count + last-active timestamp. Keep this small — the conversation transcripts are the primary signal; Mixpanel is just for surfacing whether the user was actively engaging the rest of the app or only Aurie.

Suggested query (insights, breakdown by event):

- Filter: `distinct_id = <UID>` over the same window as Step 2.
- Metrics: `AI Sign Up Login` (sessions proxy), `AI Conversation Session Started` (Aurie engagement), `Subscription Cancelled` (cancellation event if present).

If Mixpanel returns nothing or errors, record the miss in **Sources** and continue.

### Step 5 — Build the analysis plan, then synthesise

**Plan-before-synthesis cue.** Before drafting the report, lay out a one-paragraph plan describing:

- The N conversations being analysed (count + date range).
- Whether `cancellation_feedback` is shaping the prompt (and what specifically you'll be looking for — e.g. for "not conversational", scan for: short Aurie replies, generic question lists, ignoring user's emotional bid, scripted-feeling intros).
- Which sources came back empty (Aura profile? Mixpanel? Subscription state?).

This is the moment to slow down — without it, models tend to skim long transcripts and produce generic readouts. Show the plan in chat, then synthesise.

Synthesis sections (always in this order):

1. **Context** — who (UID prefix only — never paste full UIDs without need), plan, vertical, signup date, cancellation reason if any. One paragraph. **PII rule (hard):** UID prefix only (first 8 chars), email **domain** only, never the full email / `givenName` / `familyName` from `/users/<id>`. Applies to this Context paragraph AND to every downstream surface — the Step 7 Slack draft, the Step 8 Notion row, any forwarded report. See `Compliance/Privacy` for the canonical rule.
2. **Friction moments** — per conversation, with quoted transcript excerpts and timestamps. **PII rule (hard) when quoting:** OpenAI stored completions are NOT PII-masked at write time. The transcript may contain the user's real first name, last name, and email as they typed it. Apply Compliance/Privacy redaction before any excerpt lands in the report: first name OK in body if Aurie naturally addressed the user that way, but never quote full email / full last name / phone / payment info. When a friction moment turns on a piece of literal user-typed PII, paraphrase ("the user mentioned their email") instead of quoting. **Sentinel rule (hard) when quoting:** strip any sentinel marker (Step 2) from the excerpt first — it never reached the user, so quoting it back invents a line Aurie didn't say; describe what the turn signalled if that's the point. Applies to every downstream surface too — the Step 7 Slack draft and the Step 8 Notion row. Format:
   ```
   ### Conversation N — <YYYY-MM-DD HH:MM> (<turn_count> turns)
   - **What happened:** <one-line summary>
   - **Friction:** <specific moment(s); quote the exact Aurie line + the user's response>
   - **Why it maps to <feedback>** (only when `cancellation_feedback` is set): <link the moment to the specific complaint>
   ```
   Quote sparingly — 1–2 short lines per moment, not full paragraphs.
3. **Positive interactions** — moments where Aurie did connect or the user expressed satisfaction. Same per-conversation format. **Always include this section** even on cancellation reports — it counterbalances confirmation bias and surfaces what's worth preserving.
4. **Patterns / hypotheses** — 2–4 bullets calling out recurring shapes across conversations (e.g. "Aurie defaults to listing 3 options when the user is venting"). Each bullet should be falsifiable — i.e. checkable against another user's logs.
5. **Recommended follow-up** — 2–4 concrete next steps. Examples: "review the system prompt's instruction to offer multiple-choice replies"; "add an eval case for short-emotional-bid → empathic-mirror in the Feedback Conversation dataset"; "tag this user for a follow-up survey before the cancellation finalises".
6. **Sources** — every URL and path used. Required entries:
   - OpenAI Conversation [Production] project (`proj_esFyd4eq2VIAjKUn69X1ZFfk`) — the source of the transcripts. Cite the conversationId(s) you read, not a UI URL (OpenAI doesn't expose a per-conversation deep link).
   - AI Knowledge paths consulted (e.g. `Products/Aurie`, `Tools/OpenAI`, `Tools/Aura API/Subscriptions`)
   - Aura API user profile (`/users/<id>`) (or "unavailable")
   - Aura API subscriptions (`POST /subscriptions/list/<id>`) (or "unavailable")
   - Mixpanel query (or "skipped/unavailable")

### Step 6 — Return inline

Return the report as the skill's result and don't write it to disk. The Slack reply (Step 7) is opt-in; the Notion record (Step 8) is on by default.

In **Mode A** runs continue to Step 7 (Slack-thread reply offer) then Step 8 (Notion record offer).

In **Mode B** runs Step 7 is skipped (no source thread) but Step 8 still runs — recording an analysis in `Learnings/Conversations` is valuable regardless of how the run was triggered.

### Step 7 — (Mode A only) Offer to reply on the source thread

When the run started from a Slack URL (Mode A) and Step 0 captured `channel_id` + `message_ts`, finish by **asking** whether the caller wants a draft summary posted as a reply on that thread. Do not auto-post — sending a message on someone's behalf requires explicit per-turn confirmation. This step is purely opt-in; declines or non-responses leave the report inline and post nothing.

**Step 7a — Ask.** Phrase it tightly:

> *"Want me to reply on the original Slack thread with a summary? I'll show you the draft first — nothing posts without your approval."*

If the caller declines (or says nothing actionable), stop. The inline report from Step 6 already covers the ask.

**Step 7b — Draft.** On approval, compose a tight summary in this exact shape — never the full per-section report (too long for a thread reply, and the per-conversation friction quotes bloat the thread):

```
*Headline:* <one line — UID prefix or conversation-id prefix + the dominant signal across the analysis>

*What worked:* <1–3 bullets pulled from the Positive interactions section — each bullet is one short clause, no quotes>

*What didn't:* <1–3 bullets pulled from Friction moments — each bullet is the friction shape (not the full quote); cite turn timestamps inline if compact>

*Hypothesis to validate:* <1–2 bullets pulled from Patterns / hypotheses — each is falsifiable; this is what the team should check next>

_Full analysis available on request._
```

Show the draft in chat and require **explicit approval** before posting — "Looks good, post it" / "Yes, send" / equivalent. A simple "go ahead" is sufficient; ambiguous responses ("hmm" / "maybe") are not. If the caller wants edits, apply them and re-show the draft. Iterate until they approve or decline.

**Step 7c — Post.** On explicit approval, **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** to post the approved draft as a **threaded reply** — pass `channel=<channel_id>`, `text=<approved draft body>`, `thread_ts=<message_ts>`, **and `--footer data:analyze-user-conversations`** so the helper stamps the versioned footer, and ask it to return the permalink. The draft body carries no footer — `shared:post-to-slack` appends the versioned one.

`thread_ts=<message_ts>` is critical — it makes the message land as a thread reply, not a top-level message in the channel. Verify both `channel_id` and `message_ts` are set before invoking. The slack skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and the unfurl-off default — don't call Slack directly here.

**Step 7d — Confirm.** Surface the permalink the slack skill returns so the caller can verify the reply landed where expected:

> *Posted: <permalink>*

If the post fails (no Slack connection, channel access revoked, expired token), the slack skill surfaces the error — relay it verbatim and offer to retry; don't silently swallow it. The caller may need to invite the bot to the channel or connect the workspace.

**Skip conditions.** Skip Step 7 entirely when:

- The run is **Mode B** (no source thread exists).
- The Mode A URL parse failed in Step 0 and the caller fell back to providing UID / conversation_id directly without a usable URL anchor.
- The caller said up-front in the same turn "don't reply, just analyse."

### Step 8 — Record the analysis in AI Knowledge → Learnings → Conversations (default on)

Whether Step 7 fired (Mode A) or was skipped (Mode B), finish the run by **persisting the analysis** as a row in `Learnings/Conversations`. This is the durable counterpart to the Slack reply: Slack is good for the next 24h, the Notion row is what's still searchable in six months when someone asks "have we seen question-saturation kill ratings before?".

**Recording is on by default for a single (rated) conversation — not for a user's backlog.** We record a conversation *because* it carried a rating/feedback signal worth keeping, so a **conversation-pivot** run (one specified conversation, the rated case) records by default. A **user-pivot** run (a `user_id` sweep over a whole history) does **not** bulk-record — that would flood `Learnings/Conversations` with unrated backlog; the feed is meant to be rated conversations only (driven by `data:investigate-user-rating` and single-conversation analyses). `record` modes:

- **`true`** (default for conversation-pivot) — compose the row (applying `Compliance/Privacy` masking), write it, and present the recorded row inline so the caller sees exactly what landed. No pre-write prompt.
- **`false` / `--no-record`** (default for user-pivot) — analyse only, write nothing. Also passed by orchestrators that own persistence (`data:profile-user`).
- **`ask`** — show the drafted row and wait for an explicit yes before writing (for a caller who wants to review first, or to record a picked subset of a user-pivot).

Step 7 (Slack reply) and Step 9 (Braintrust dataset) stay ask-first regardless.

**Step 8a — Pre-check (create vs update).**

Query the Conversations data source (`2895c7d1-a921-49ac-ad16-91b3ac70951f`) for a row with the analysed `conversation_id`. Use `retrieve-knowledge` or a direct Notion REST query — both paths are documented in `retrieve-knowledge/scripts/notion_rest.py`.

- **Row exists** → the write is an **update**: replace its `## Headline` / `## What worked` / `## What didn't` / `## Hypotheses to validate` / `## Recommended follow-up` sections with the fresh content and update `Rating` / `Prompt Version` if they changed.
- **No row** → it's a **create**.
- **User-pivot runs** (multiple conversations): **not recorded by default** (`record` defaults `false` for this pivot — we don't backfill a user's unrated history into the DB). If the caller explicitly wants rows, `record: ask` lets them pick which conversations, or `record: true` records all in the window.

**Step 8b — Compose the row.** Build the row payload per the [`Learnings/Conversations` schema](../../../shared/skills/record-knowledge/references/schemas/conversations.md), applying `Compliance/Privacy` masking (OpenAI logs aren't masked at write time — do the masking here). The full row payload (fields + page-body markdown template) is in [`references/record-format.md`](references/record-format.md).

**Step 8c — Write (or gate on `record: ask`).** The `shared:record-knowledge` invocation shape is in [`references/record-format.md`](references/record-format.md).

- **`record: true` (default)** — write immediately, no pre-write prompt. Invoke `shared:record-knowledge`.
- **`record: ask`** — first show the composed `Title` + `Themes` + body bullet count and wait for an explicit yes (the caller can request edits — add/remove a theme, sharpen the headline — before approving). On the yes, invoke `shared:record-knowledge`.

The Recipe A2 dedup happens inside `record-knowledge`. If the pre-check missed an existing row (rare — race between checks), the write refuses with the existing row URL — switch to an update against that URL.

**Step 8d — Confirm.** After `record-knowledge` returns, announce what landed with the **real** URL it returned (never before the write):

> *"Recorded this analysis to AI Knowledge → Learnings → Conversations — <returned URL>."*  (create)
> *"Updated the existing row with the fresh analysis — <returned URL>."*  (update)
> *"Recorded N conversations (C created, U updated) — URLs below."*  (user-pivot)

If the write fails (schema violation, Notion downtime), surface the error and offer to retry. Don't silently swallow. Common fixes: drop a Theme that doesn't exist yet (or accept that Notion will add it as a new option — `Themes` is multi_select with on-the-fly extension), or shorten an overly long `Title`.

**Skip conditions.** For a conversation-pivot, recording is on by default; skip Step 8 when:

- The run is a **user-pivot** *and* the caller did **not** explicitly pass `record: true` / `record: ask` (`record` defaults `false` for a user-pivot — a backlog isn't bulk-recorded; but an explicit `record: true` records all in the window and `record: ask` picks a subset, so those override the skip).
- The caller passed `record: false` / `--no-record` (e.g. an orchestrator that owns persistence — see `data:profile-user`).

- The caller said up-front in the same turn "don't record, just analyse."
- Step 2 returned zero conversations (nothing to record).
- The run was a generic, no-`conversation_id` user-pivot exploration where the user explicitly said they don't want individual conversation rows.

Step 7 (Slack reply, ask-first) and Step 8 (Notion record, default-on) are independent: declining the Slack reply does not suppress the record, and skipping the record leaves the Slack reply (Mode A) standing.

### Step 9 — Offer to save the conversation to a Braintrust dataset

Whether Step 7 (Slack reply) and Step 8 (Notion record) fired or were skipped, finish the run by offering to **save the conversation as a Braintrust eval case** in a dataset within the `Conversation` project. Default is the `Feedback Conversation` dataset; the caller can override via `--dataset <name-or-slug>` on the sub-skill if a different dataset (e.g. a brand-voice-focused sibling) is more appropriate.

This closes the loop on the analysis: the Notion row (Step 8) is the durable institutional-memory archive; the Braintrust dataset row makes the same conversation a runnable check that future prompter changes get evaluated against. The two are complementary — Notion is for humans reading *"have we seen this pattern before?"*; the dataset is for evals running on PRs / prompter version bumps.

Like Step 7 (and unlike Step 8, which defaults on), this is **opt-in and requires explicit per-turn approval**. The dataset is shared infrastructure that drives evals — the caller has to say yes.

**Step 9a — Pre-check for an existing row.**

Run the sub-skill's dedup check (`save_row.py --check-dupe --conversation-id <UUID>` — full command in [`references/braintrust-save.md`](references/braintrust-save.md)). If `existing_count == 0` → frame the offer as **create**. If `existing_count >= 1` → frame as **update** / **skip**, surfacing the existing row(s) `case_id` + `pattern`.

**Step 9b — Ask.** Phrase it tightly. Offer once even if Steps 7 and 8 were both declined — the caller may want the durable eval case without the public Slack reply or the Notion archive.

> *"Want me to save this conversation to the Braintrust Feedback Conversation dataset? It'd become a runnable eval case that future prompter changes get checked against."*

If a row already exists, replace the close with:

> *"…this conversation already has a row at `<URL>` (`case_id=<id>`, `pattern=<pattern>`). Update it with the fresh criteria, leave it, or save anyway as a sibling row?"*

**Skip Step 9 entirely when:**

- The caller said up-front in the same turn "don't save to braintrust" / "skip the dataset" / equivalent.
- Mode B with `user_id` pivot returned multiple conversations (the dataset row needs one specific conversation — ask the caller to pick which one to save, or skip the offer).
- Step 2 returned zero conversations.
- The analysis is **not anchored to a specific conversation_id** — e.g. a free-form steering exploration that didn't surface a single load-bearing conversation. The dataset row needs a conversation to mirror.

A declined Step 7 or a skipped Step 8 does **not** automatically skip Step 9 — they're independent decisions.

**Step 9c — Compose `data:save-to-braintrust-dataset`.**

On approval, invoke that skill with pre-filled flags from the materials this analysis already has in scope. The composition strips duplicate prompts (conversationId, observed_rating, source) and seeds reasonable defaults for the rest — the sub-skill still shows its own preview and asks the caller to confirm or edit every field before writing. The `save_row.py --build` command with its pre-filled flags, and the `--pattern` default heuristics (with the example pattern slugs seen so far), are in [`references/braintrust-save.md`](references/braintrust-save.md).

Then hand off to the sub-skill's Step 3 (prompt the user for any missing or unconfirmed fields) and Step 5 (insert / update). See [`data:save-to-braintrust-dataset` SKILL.md](../save-to-braintrust-dataset/SKILL.md) for the row contract, validation rules, `--dataset <name-or-slug>` override (default `feedback-conversation`), and `--update <row-id>` / `--allow-duplicate` mechanics.

**Step 9d — Confirm.** After the sub-skill returns, surface the new (or updated) row URL inline:

> *Saved to dataset: <URL>*

If the sub-skill fails (validation error, Braintrust API down, dedup refusal), surface the error verbatim — don't silently swallow.

## Output format

The skill's primary return value is a single Markdown document organised exactly as listed in Step 5 (Context → Friction → Positives → Patterns → Follow-up → Sources), led by a one-line headline for a Slack TL;DR. Length by `time_window`, and where the optional Step 7/8/9 outputs land, are in [`references/output-format.md`](references/output-format.md).

## Edge cases

Zero-match handling, the PII-not-masked discipline, re-run idempotency, Mode A URL failures, and the Cowork sandbox are catalogued in [`references/edge-cases.md`](references/edge-cases.md). The core PII rules are also stated inline in Step 5.

## Out of scope (v1)

- **Auto-trigger on k8bot cancellation messages.** Hooking this skill to fire automatically when k8bot posts in `#cancellation-feedback` is a v2 idea — see Steve's [self-improving loop with human approval](https://claude.ai/share/c1c9ee9c-d172-4acb-87f1-c1cefce342a0) sketch. v1 is on-demand only.
- **Automated prompt changes.** Per Alyssa's note in the originating thread: "I wouldn't automate any prompt changes beyond that." This skill produces analysis; humans iterate the prompt.
- **Auto-saving to the Braintrust Feedback Conversation dataset.** Step 9 always requires explicit per-turn approval, even if Steps 7 and 8 both ran. The dataset is shared infrastructure that drives evals on prompter PRs — silently growing it on every analysis would erode the signal. No flag, env var, or caller-side override bypasses the confirm loop.
- **File-system persistence.** Writing the report to a local file or Drive is not in scope. If the caller wants that, they can copy the Step 6 output manually.
- **Auto-posting the Slack reply.** Step 7 always requires explicit per-turn approval. There is no flag, env var, or caller-side override that bypasses the draft-and-confirm loop. Precedent: `data:profile-user` Step 6 + Claude Code's "actions visible to others" guidance in [`/CLAUDE.md`](../../../../CLAUDE.md).
- **Multi-user batch analysis.** One user per invocation. For aggregate views, use `fetch-conversation-topics`.
- **Quoting any `$` figure derived from Braintrust `metrics.estimated_cost`.** Hard prohibited. If the analysis needs to surface a cost number — per-user, per-conversation, or per-window — invoke `eng:report-openai-user-cost` (single-source LLM cost) or the parent `eng:report-user-costs` (all sources combined). For a per-conversation cost proxy, divide the per-user OpenAI cost by the user's conversation count over the same window. Braintrust's `estimated_cost` has pricing-table gaps, wrong rate tiers, and a single-project framing problem — see AI Knowledge [`Tools/Braintrust` → "Cost accounting"](https://www.notion.so/34c75e23af2081968f7bf7ee43e1442a) for the rule and the 17× under-count case that motivated it. Token counts on the same `metrics` object (`prompt_tokens`, `prompt_cached_tokens`, `completion_tokens`) ARE trusted and may be quoted — it's only the dollar field that's unreliable.

## Key IDs and references

Key IDs (OpenAI project, Aura API base URL), originating Slack thread, and the full Linear ticket history are in [`references/key-ids.md`](references/key-ids.md).
