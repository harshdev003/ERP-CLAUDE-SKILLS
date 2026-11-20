---
name: analyze-user-journal
description: >
  Analyses one Aurie journal entry — or a user's recent journal history — by reading the
  rendered journal the user actually saw (title, summary, insights, affirmations) from the
  Aura API alongside the input context it was generated from in OpenAI stored transcripts. The
  two together answer the quality question: given what the user shared, did the journal
  capture the right emotional signal? With a `journal_id` it resolves by id; otherwise the
  pivot is Firebase UID over a window. Optional rating polarity and feedback text steer the
  synthesis. Offers three opt-in side effects: Slack reply, Learnings/Conversations record,
  Braintrust save. Trigger when the user pastes a `*JOURNAL RATED*` Slack URL, says "analyze
  this journal entry" or "why did this journal get a thumbs-down", or gives a UID and asks for
  their journal history. Do NOT use for conversation analysis (`analyze-user-conversations`)
  or topic reports (`fetch-conversation-topics`).

metadata:
  version: "3.2.3"
knowledge:
  reads:
    - Compliance/Privacy           # PII redaction rules — load-bearing: OpenAI logs are NOT PII-masked at write time
    - Guide/Index                  # baseline routing aid (universal)
    - Products/Aurie          # brand voice for report framing
    - Tools/OpenAI                 # auth + "Reading stored completions" access pattern (the migration target)
    - Tools/Aura API               # auth + base URL for /users/<id> context lookup
    - Tools/Aura API/Subscriptions # canonical subscription state for Context section
    - Tools/Slack                  # canonical Channel IDs (for skills) — Mode A URL parsing + reply offer
    - Learnings/Conversations      # check for prior analyses on the same journal_id
    - Tools/Braintrust               # Braintrust fallback path (BRAINTRUST_API_KEY)
  writes:
    - Learnings/Conversations      # opt-in: record the analysis for future retrieval
---

# analyze-user-journal

> ⚠️ **The `journal` prompter runs on the OpenAI Responses API, not Chat Completions.** Responses objects are **not enumerable** — `GET /v1/responses` rejects API keys ("must be made with a session key… only from the browser") — so the stored-completions walk returns **zero** for recent journals. The `llmCall` ledger (BigQuery `aura-main.llm.llm_call`, by `userId`) indexes them instead, then each object is fetched by id. Needs **`GCP_KEY`** alongside `OPENAI_JOURNAL_API_KEY`.
>
> Before this was wired, the walk returned 0 and the **legacy Braintrust fallback silently rescued the run** — so the skill looked healthy while depending on a source this repo migrated off in June. The Responses path now runs *before* that fallback. It is also more accurate: on a 7-day window the ledger showed 5 journal calls and the Responses path returned exactly 5, where Braintrust had been returning 10.
>
> Every result carries a `responses_source` block (ledger rows seen, objects fetched, objects that failed) so a partial read is distinguishable from an empty one.

> **Two sources, both required for a journal-rating analysis.** The k8bot `journal_id` (from the footer `[id: <uuid>, user: <uid>]`) is the `journal` table primary key. The footer's `user:` is the entry owner's full uid, so `fetch_journal.py` reads the entry from that user's journal list, exactly as the user read it (`GET /ai/journal/:journalId` answers only the owner). That rendered journal is the evidence the rating is *about*, and `scripts/fetch_journal.py` is the primary fetch. OpenAI's `Journal [Production]` project (`proj_qXZ9CZnjp4mL5PVsW43JyOpj`) supplies the other half — the prompt the journal was generated *from*, carrying the day's conversation excerpt — via `scripts/fetch_logs.py`. Analyse the rendered journal against its input; neither alone answers the quality question.
>
> **The k8bot fenced block is the user's rating comment, not journal content.** `journal.js#rate` fences `feedback || 'No feedback'`. It is passed as `feedback_text` and read as a reaction to the journal — never matched against journal text, which it does not contain. The generating call is located by timestamp instead (`--near-iso`), which is exact: the call is written within seconds of the row.

End-to-end quality analysis for one user's journal entry or recent journal history. Connects the rendered journal from the Aura API, the OpenAI journal-prompter call that produced it, and the user's Aura profile, then synthesises a report focused on whether the AI-generated output (title, insights, summary, affirmations) faithfully reflected the emotional content of the input conversations.

Unlike `analyze-user-conversations`, there is no multi-turn dialogue to analyse — each journal is a single LLM completion. The analysis asks: given what the user shared in their Aurie sessions that day, did the journal output capture the right emotional signal? Or did it miss, minimise, or reframe something important?

## When to use

Trigger when the user asks any of:

- "analyze this journal entry" / "analyze journal `<UUID>`"
- "why did this journal get a thumbs-down"
- "analyze journal history for user `<UID>`"
- pastes a `*JOURNAL RATED*` k8bot Slack URL (via `data:profile-user` routing)
- provides a `journal_id` extracted from a k8bot footer (sufficient on its own — the fetched row carries the `user_id`)

**Do NOT trigger for:**

- Aurie conversation analysis (multi-turn chat) → use `analyze-user-conversations`.
- Aggregate topic reports across all users → use `fetch-conversation-topics`.
- Experiment-level analysis → use `analyze-experiment`.

## Inputs

### Mode A — from *JOURNAL RATED* k8bot (via profile-user or investigate-user-rating)

Fields passed in by the caller:

- `journal_id` — the UUID from the k8bot footer `[id: <uuid>, user: <uid>]`. **Load-bearing.** It is the `journal` table primary key, so, with the footer's `user:` (the owner), it resolves the rendered journal from that user's journal list. This is the entry the rating is about.
- `user_id` — Firebase UID from the footer `user:` field. Used to walk the OpenAI side for the generating call, and for the Aura profile lookup.
- `feedback_text` — the fenced block content from the k8bot message. This is the **user's typed rating comment** (`journal.js#rate` fences `feedback || 'No feedback'`), not journal content. Read it as the user's reaction; never use it to identify the journal.
- `rating_polarity` — `positive` or `negative` (from the thumbs-up/thumbs-down emoji in the k8bot message).

### Mode B — Direct

- `journal_id` (optional) — analyse one specific journal. Sufficient on its own: the fetched row carries `userId` and `createdAt`, which drive the rest of the workflow.
- `user_id` (optional) — Firebase UID. Returns all journals in the time window. Required when `journal_id` is absent.
- `feedback_text` (optional) — the user's rating comment, when one exists. Steering only.
- `rating_polarity` (optional) — `positive` | `negative`. When absent, produces a generic report.
- `time_window` (optional, default `30d`) — `30d`, `7d`, `NNd`, `last_journal`, `last_n=<int>`. Ignored when `journal_id` scopes the run to one entry.

At least one of `journal_id` or `user_id` is required. If neither can be resolved, ask. Don't guess.

## Workflow

### Step 0 — Resolve inputs and check prerequisites

**If Mode A (k8bot inputs).** The caller has already extracted `journal_id`, `user_id`, `feedback_text`, and `rating_polarity` from the k8bot message. Persist all four through the workflow. `channel_id` + `message_ts` from the source Slack URL are passed for Step 6's reply offer.

**If Mode B (direct).** Capture `journal_id` and/or `user_id` (and optional fields) verbatim.

**Prerequisites.**

```bash
test -n "$AURA_API_TOKEN"        && echo "OK: AURA_API_TOKEN set"        || echo "MISSING AURA_API_TOKEN"
test -n "$OPENAI_JOURNAL_API_KEY" && echo "OK: OPENAI_JOURNAL_API_KEY set" || echo "MISSING OPENAI_JOURNAL_API_KEY"
```

`AURA_API_TOKEN` is hard-required whenever a `journal_id` is in play — it is the only way to read the rendered journal, which is the evidence a journal rating is about. Without it, stop and say so; a rating-only write-up is not an analysis of the journal.

`OPENAI_JOURNAL_API_KEY` is hard-required for the input-context half — a service-account key scoped to the `Journal [Production]` project. **Not** `OPENAI_ADMIN_API_KEY`: OpenAI enforces a `model.request` scope on `GET /v1/chat/completions` that Admin keys cannot hold, by OpenAI design ([PROD-3572](https://linear.app/aura-health/issue/PROD-3572)). When it is missing but `AURA_API_TOKEN` is present, the rendered journal still supports a reduced report — say which half is missing rather than presenting it as complete.

If `OPENAI_JOURNAL_API_KEY` is missing: hand off to `update-config` to add it under `~/.claude/settings.json` → `env` block (per [`/CLAUDE.md`](../../../../CLAUDE.md) → "Secrets and environment"). Create the key at <https://platform.openai.com/api-keys> → Create new secret key → Owned by: Service account → Project: `Journal [Production]`. Restart Claude Code after adding.

**Cowork caveat.** `mcp__workspace__bash` strips host env (PROD-1305), so neither key is reachable inside Cowork sandboxes. See [`/CLAUDE.md`](../../../../CLAUDE.md) → "Known Cowork constraints" for the per-session paste-export workaround.

### Step 1 — Pull AI Knowledge priors

Invoke `shared:retrieve-knowledge` for the paths declared in `knowledge.reads`:

- `Products/Aurie` — brand voice, especially how Aurie is meant to reflect emotional bids back.
- `Tools/OpenAI` — auth + the "Reading stored completions (BT migration access pattern)" section: completion shape, `/messages` endpoint, metadata schema (`metadata.user` for UID; journal calls don't carry `metadata.conversationId` since they're single-shot, but they do carry `metadata.name = journal-prompter`), the metadata field reference (server-side `metadata[...]` filtering works; the gotchas are no native **time** filter + retention TBD).
- `Tools/Aura API` — base URL + auth conventions for the `/users/<id>` lookup.
- `Tools/Aura API/Subscriptions` — request/response shape for `POST /subscriptions/list/:userId`.
- `Compliance/Privacy` — PII redaction rules (UID prefix + email domain only). **Load-bearing**: OpenAI logs are NOT PII-masked at write time (unlike the legacy Braintrust integration's `setMaskingFunction(maskPII)`), so the masking discipline lives entirely in the synthesis layer now.
- `Learnings/Conversations` — check for prior analyses on the same `journal_id`, which is the dedup key.

Keep fetched content in working memory through Steps 2–5. Cite paths in the **Sources** block of the final report.

If a path fails to resolve (taxonomy drift), note the miss and proceed. Don't block.

### Step 2a — Fetch the rendered journal (primary evidence)

Whenever a `journal_id` is available, start here. This is the entry the user read and rated.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-user-journal/scripts/fetch_journal.py \
  --footer '[id: <UUID>, user: <UID>]' \
  --json /tmp/aurie_journal_row.json
# or: --journal-id <UUID> --uid <UID>
```

With `AURA_API_TOKEN` (admin or agent role): `POST /ai/journals/list/:userId` for the footer's user, newest first and removed entries included, until the row with this id. The summary line shows a uid prefix only (the canonical `uid_prefix()`). Returns:

```json
{
  "journal_id": "<uuid>", "user_id": "<UID>", "type": "daily",
  "created_at": "2026-08-05T02:05:40.628Z", "read_at": "...",
  "title": "...", "summary": "...",
  "insights": [{ "quote": "...", "context": "..." }],
  "affirmations": ["..."],
  "notes": null, "mood": null, "emotions": [], "impacts": [],
  "rating": 1, "feedback": "So comforting."
}
```

Three fields drive the rest of the workflow: `user_id` (the OpenAI walk and the profile lookup — so a `journal_id` alone is a complete Mode B input), `created_at` (the `--near-iso` probe in Step 2b), and the content fields (the analysis itself).

**The script exits non-zero on 404, 401/403, and network failure, and warns when the row resolves but carries no content.** Any of those means the journal is unresolved: say so in the Headline and stop. Do not fall back to writing the report from the rating and the engagement numbers alone — that produces a write-up that reads finished while never having seen the thing being rated.

`rating` and `feedback` on the row are the authoritative rating, and agree with the k8bot message. When they disagree, trust the row and note the discrepancy.

### Step 2b — Fetch the input context from OpenAI

The generating call carries the prompt the journal was made from — the day's conversation excerpt. Pivot by `user_id`; pass the rendered journal's `created_at` to resolve to the one call behind it.

**Mode A — k8bot-routed (most common):**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-user-journal/scripts/fetch_logs.py \
  --user-id <UID> \
  --near-iso <created_at from Step 2a> \
  --window 7d \
  --json /tmp/aurie_journal.json
```

The call is written within seconds of the journal row, so proximity in time identifies it exactly — `match_strategy` reports `user_id + createdAt proximity` and `journal_count: 1`. When no call lands within 6h of the row, the script returns the full window with a hint; the rendered journal from Step 2a still stands, so report the missing input context rather than dropping the analysis.

The script lists Journal [Production] with a server-side `metadata[user]=<UID>` filter (newest-first), so OpenAI returns only this user's journals; it then applies the `since_unix` window bound client-side and, when `--near-iso` is set, keeps the single call closest to that timestamp.

**Mode B — User-only pivot:** pull all journals in a window.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-user-journal/scripts/fetch_logs.py \
  --user-id <UID> \
  --window 30d \
  --json /tmp/aurie_user_journals.json
```

Returns all journal completions in the window, sorted most-recent-first. The `--window` flag accepts: `30d`, `7d`, `NNd`, `last_journal`, `last_n=<int>`.

This pivot returns the OpenAI calls, not the rendered rows — there is no rendered-journal fetch for a whole window, because `GET /ai/journal/` is scoped to the calling user. A privileged `POST /ai/journal/list/:userId` (roles `admin`/`agent`/`service`/`system`) is the proper backend for it; adopt it here once it reaches production ([ENG-1109](https://linear.app/aura-health/issue/ENG-1109)). Until then, deepen any single entry of interest by re-running with its `journal_id` through Step 2a.

The script:

1. Lists `/v1/chat/completions` with `OpenAI-Project: proj_qXZ9CZnjp4mL5PVsW43JyOpj` (Journal [Production]) and a **server-side** `metadata[user]=<UID>` filter, so OpenAI returns only this user's journal completions. The `since_unix` lower bound is applied client-side (no native time filter). See `Tools/OpenAI` → "Metadata field reference" for the verified syntax.
2. Keeps `metadata.user == <UID>` (with `input_user == <UID>` as a defensive client-side fallback).
3. For each matching completion, fetches `/v1/chat/completions/{id}/messages` once to get the system-prompt input (journal-prompter is single-shot — the system message carries the input context up to Intelligence Service 1.35, a developer message after it from 1.36; the 1.40 `---` delimiter pair is skipped).
4. Parses the assistant output from `choices[0].message.content` — a markdown-fenced JSON object with `title` / `insights` / `summary` / `affirmations`.
5. Returns JSON with shape:
   ```json
   {
     "user_id": "<UID>",
     "window": { "kind": "days", "days": 30, "since_unix": 1782... },
     "match_strategy": "user_id only" | "user_id + createdAt proximity" | "user_id only (no call near the journal timestamp)",
     "journal_count": <int>,
     "journals": [
       {
         "completion_id": "chatcmpl-...",
         "created_at": "2026-06-04T15:30:00+00:00",
         "model": "gpt-5.2-chat-latest",
         "input": "<system prompt with day's conversation excerpt embedded>",
         "output": { "title": "...", "insights": "...", "summary": "...", "affirmations": "..." },
         "metadata": { "user": "<UID>", "name": "journal-prompter", "model": "...", "version": "...", ... }
       },
       ...
     ]
   }
   ```

If the script reports zero journals after probing, **stop here** and surface the gap to the user — don't fabricate analysis from no data. Likely causes: wrong UID, the user never used the journal feature in the window, or the window predates the OpenAI metadata rollout.

### Step 3 — Best-effort: pull user profile from Aura API

Two calls, keyed on `user_id` — from the caller, or from the `user_id` on the Step 2a row when the run started from a `journal_id` alone. Both are best-effort — the report can be produced without them.

```bash
# Identity / origin
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/data:analyze-user-journal (session ${AINATIVE_SESSION_NAME:-adhoc})" -fsS -H "Authorization: Bearer $AURA_API_TOKEN" \
  https://services.aurahealth.io/users/<UID> \
  | jq '{ id, email, givenName, vertical, locale, sourcePlatform, attributionData, deviceOS, createdAt, loggedInAt, onboarding }'

# Canonical subscription state
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/data:analyze-user-journal (session ${AINATIVE_SESSION_NAME:-adhoc})" -fsS -X POST -H "Authorization: Bearer $AURA_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"includeDeleted": true}' \
  https://services.aurahealth.io/subscriptions/list/<UID>
```

Capture `vertical`, `sourcePlatform`, `attributionData`, `createdAt`, and subscription state. Follow the same `attributionData` rendering rules as `analyze-user-conversations` Step 3 (source/channel/campaign/creative buckets, fallback to key listing for unrecognised shapes).

If either call fails (401/403, network error, Cowork sandbox), record "Aura profile lookup unavailable" in **Sources** and continue.

### Step 4 — Build the analysis plan, then synthesise

**Plan-before-synthesis cue.** Before drafting the report, lay out:

- The journal(s) being analysed (count + date range, or the single journal's date).
- Whether the rendered journal resolved (Step 2a) and which of `title`, `summary`, `insights`, `affirmations`, `notes` are non-empty.
- Whether the generating call resolved (Step 2b), and by which `match_strategy`.
- Whether `rating_polarity` is shaping the prompt:
  - `negative` → look for where the output missed or minimised the emotional content of the input.
  - `positive` → confirm what the output got right and why.
  - absent → generic quality + strengths report.
- What `feedback_text` says, read as the user's reaction to the journal — a short comment ("So comforting.") is a verdict on the content, so name which part of the journal it is plausibly a verdict *on*.
- Which sources came back empty.

Show the plan in chat, then synthesise.

**Synthesis sections (always in this order):**

1. **Context** — UID prefix (first 8 chars only, never full UID), plan, vertical, signup date. One paragraph. **PII rule (hard):** UID prefix only, email domain only, never full email/`givenName`/`familyName`. Applies to this paragraph and every downstream surface (Slack draft, Notion row). See `Compliance/Privacy` for the canonical rule.

2. **Journal output quality** — per journal (or the single journal), structured as:
   ```
   ### Journal — <YYYY-MM-DD HH:MM> (`<journal_id>`, completion `<chatcmpl-... | resp_...>`)
   - **Input context (excerpt):** <1-2 sentences from the system-prompt input, if available>
   - **The journal as the user read it:**
     - Title: `<title>`
     - Summary: <quote the summary, or the load-bearing sentences of it>
     - Insights: <quote each insight's `quote` + `context`>
     - Affirmations: <quote each>
   - **Quality assessment:** <did the output faithfully reflect the emotional content? quote specific mismatches or strong alignments, naming both sides — what the user said in the input, what the journal made of it>
   - **Rating:** <positive/negative/unknown> — <what the user's `feedback_text` is a verdict on, tied to specific journal content; if negative, what specifically they likely objected to>
   ```

   **Quote the journal, don't summarise it away.** A reader of this report should be able to see what the user saw. A rating with a two-word comment is not thin evidence when the journal behind it carries a title, a summary and an insight — the content is what makes the rating legible, and it is the whole reason Step 2a runs first.

   When Step 2a did not resolve, say so here in place of the content — **"journal content unavailable — `<reason>`"** — and carry that caveat into the Headline. Never write this section from the rating alone.

3. **Strengths** — moments where the output did connect or resonate. Always include this section even on negative-rating reports — it counterbalances confirmation bias and surfaces what's worth preserving.

4. **Patterns / hypotheses** — 2–4 falsifiable bullets across journal(s). For single-journal analysis, this is weaker signal — note that explicitly. Each bullet should be checkable against another user's logs.

5. **Recommended follow-up** — 2–4 concrete next steps.

6. **Sources** — every URL and path used. Required:
   - The journal id and how it was read — owner lookup + `POST /ai/journals/list/<uid prefix>…` (or "unresolved — `<reason>`")
   - OpenAI Logs URL for the generating call — `https://platform.openai.com/logs/<completion_id>` (one per journal analysed)
   - AI Knowledge paths consulted
   - Aura API profile calls (or "unavailable")

### Step 5 — Return inline

Return the report as the skill's result. Don't post it anywhere yet. The caller asked for the analysis first; persistence is opt-in via Steps 6–8.

In **Mode A** runs continue to Step 6 (Slack-thread reply offer).
In **Mode B** runs Step 6 is skipped (no source thread) but Steps 7–8 still run.

### Step 6 — (Mode A only) Offer to reply on the source Slack thread

When the run started from a `*JOURNAL RATED*` k8bot URL (Mode A) and Step 0 captured `channel_id` + `message_ts`, finish by **asking** whether the caller wants a draft summary posted as a reply on that thread. Do not auto-post — this step requires explicit per-turn confirmation.

**Step 6a — Ask.**

> *"Want me to reply on the original Slack thread with a summary? I'll show you the draft first — nothing posts without your approval."*

If the caller declines, stop. The inline report from Step 5 already covers the ask.

**Step 6b — Draft.** On approval, compose a tight summary:

```
*Headline:* <one line — UID prefix + the dominant quality signal>

*The journal:* <title in quotes> — <one clause naming what it actually said, e.g. the insight or the summary's core move>

*What worked:* <1-3 bullets from Strengths — one short clause per bullet>

*What didn't:* <1-3 bullets from Journal output quality mismatches — one clause per bullet>

*Hypothesis to validate:* <1-2 bullets from Patterns / hypotheses — falsifiable>

_Full analysis available on request._
```

The `*The journal:*` line is required whenever Step 2a resolved. It is what makes the rest of the reply checkable by someone reading the thread — without it the summary asserts a verdict about content the reader can't see. When Step 2a did not resolve, replace the line with `*The journal:* unavailable — <reason>`.

Show the draft in chat and require **explicit approval** before posting. Iterate until they approve or decline.

**Step 6c — Post.** On explicit approval, **invoke [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** to post the approved draft as a **threaded reply** — pass `channel=<channel_id>`, `text=<approved draft body>`, `thread_ts=<message_ts>`, **and `--footer data:analyze-user-journal`** so the helper stamps the versioned footer, and ask it to return the permalink. The draft body carries no footer — `shared:post-to-slack` appends the versioned one.

`thread_ts=<message_ts>` is critical — it makes the message land as a thread reply, not a top-level message. Verify both `channel_id` and `message_ts` are set before invoking. That skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) — don't call Slack directly here.

**Step 6d — Confirm.** Surface the permalink the slack skill returns so the caller can verify the reply landed where expected. If the post fails (no Slack connection, channel access revoked, expired token), the slack skill surfaces the error — relay it verbatim and offer to retry; don't silently swallow it.

**Skip conditions.** Skip Step 6 entirely when:

- The run is Mode B (no source thread exists).
- The caller said up-front "don't reply, just analyse."

### Step 7 — Offer to record the analysis in AI Knowledge → Learnings → Conversations

Whether Step 6 fired or was skipped, offer to **persist the analysis** as a row in `Learnings/Conversations`. Requires explicit per-turn approval.

**Step 7a — Pre-check for a duplicate row.** Query the Conversations data source for a row with the analysed `journal_id` in the `Conversation ID` field. If a row exists, frame as **update** (surface existing URL + created_at). If no row, frame as **create**.

**Step 7b — Ask.**

> *"Want me to record this analysis in AI Knowledge → Learnings → Conversations? Future runs on the same journal theme will find it via `retrieve-knowledge`."*

If the caller declines, stop.

**Step 7c — Draft + approve.** On approval, compose the row payload:

```yaml
path: Learnings/Conversations
action: create  # or update
fields:
  Title: "<UID-prefix> — <one-line headline>"
  Conversation ID: "<journal_id UUID>"
  User ID: "<full Firebase UID>"
  Conversation Date: <ISO date from the OpenAI completion's created_at>
  Rating: <Positive | Negative | N/A>
  Source Type: Journal Rated
  Conversation Type: Journal
  Prompt Version: "journal-prompter:<version if known>"
  Vertical: <canonical Audience value | unknown>
  Themes: [<topic tags>]
  Slack Thread URL: "<Mode A permalink, or omitted in Mode B>"
  Steering Feedback: "<k8bot feedback text, or empty>"
body: |
  ## Headline
  <one-line summary>

  ## What worked
  <bullets from Strengths>

  ## What didn't
  <bullets from Journal output quality mismatches>

  ## Hypotheses to validate
  <bullets from Patterns / hypotheses>

  ## Recommended follow-up
  <bullets>
```

Show Title + Themes + body bullet count in chat before writing. Iterate until explicit approval.

**Step 7d — Write.** On explicit approval, invoke `shared:record-knowledge`.

**Step 7e — Confirm.** Surface the row URL after the write returns.

**Skip conditions.** Skip Step 7 entirely when:

- The caller said up-front "don't record, just analyse."
- Steps 2a and 2b both returned nothing.

A declined Step 6 does **not** automatically skip Step 7.

### Step 8 — Offer to save to Braintrust dataset

Whether Steps 6 and 7 fired or were skipped, finish by offering to **save the journal as a Braintrust eval case** via `data:save-to-braintrust-dataset`. (The eval-dataset target stays on Braintrust per the PROD-2456 scope — only the read source migrated to OpenAI.) Requires explicit per-turn approval.

**Skip Step 8 entirely when:**

- The caller said up-front "skip the dataset."
- Mode B with user-id pivot returned multiple journals (the dataset row needs one specific journal — ask the caller to pick which one, or skip the offer).
- Steps 2a and 2b both returned nothing.

## Output format

The skill's primary return value is a single Markdown document organised as: Headline → Context → Journal output quality → Strengths → Patterns / hypotheses → Recommended follow-up → Sources.

Always lead with a one-line headline above the **Context** section:

> *Headline:* `<UID-prefix>` — journal rated negative on <date>, title missed the shift from anxiety to relief the user described across the day's sessions.

Length depends on `time_window`:

- `last_journal` → 1 quality section, ~25 lines total.
- `7d` → typically 1–3 journals, ~40–70 lines.
- `30d` → typically 3–8 journals, ~60–150 lines.

## Edge cases

- **The rendered journal doesn't resolve** (404, 401/403, network). `fetch_journal.py` exits non-zero with the reason. Lead the Headline with it and produce no quality assessment — there is nothing to assess. A rating plus engagement numbers is not a journal analysis, however complete the write-up looks.
- **The journal row resolves but is empty** (no title/summary/insights/affirmations/notes). The script warns on stderr. Report it as an unresolved journal, not as an entry that was empty by design — an empty row is a bug worth naming, not a finding about the user.
- **Zero journal completions match** in Step 2b. The fetch script returns a `hint` field listing the most likely causes (wrong UID, user never used journal in the window, window predates the OpenAI metadata rollout) — surface that verbatim. When Step 2a resolved, still produce the report: assess the journal on its own terms and note that the input context was unavailable, so faithfulness to the day's conversation could not be checked.
- **Output fields are partially missing** (e.g. `affirmations` is null/empty). Note the gap in the analysis; don't treat a missing field as a quality failure — it may be a feature toggle.
- **No `journal_id`** (Mode B user pivot). The analysis uses the OpenAI completion's `input` and `output` fields as the sole context — the calls, not the rendered rows. Note this in the plan, and re-run through Step 2a on any entry worth a closer look.
- **No call within 6h of the journal's `createdAt`.** The script returns the full window with a hint — the caller can browse and pick. Don't silently widen scope without surfacing the fallback.
- **`feedback_text` is `No feedback`.** That is k8bot's placeholder for a rating with no typed comment, not user content. Treat the rating as unexplained and lean on the journal content plus `rating_polarity`.
- **Only one journal but window is 30d.** Fine — produce the report for one journal. Note that patterns are single-entry signal.
- **No PII masking at write time.** Unlike the legacy Braintrust integration (`setMaskingFunction(maskPII)` injecting `[USER_NAME]` / `[USER_EMAIL]` / `[USER_LAST_NAME]` placeholders), OpenAI stored completions carry the raw input as Aurie received it — including any name / email / phone the user pasted. The masking discipline now lives entirely in the synthesis layer: UID prefix only, email domain only, never full names from the input. See `Compliance/Privacy` for the canonical rule.
- **`journal_id` passed alone.** Sufficient. Step 2a resolves it, and the returned row carries the `user_id` and `created_at` that Step 2b and Step 3 need.
- **Cowork sandbox.** Both `OPENAI_JOURNAL_API_KEY` and `AURA_API_TOKEN` are blocked (PROD-1305). Use the per-session paste-export workaround from CLAUDE.md, or run from the host harness.

## Key IDs and references

- **OpenAI project:** `Journal [Production]` — `proj_qXZ9CZnjp4mL5PVsW43JyOpj`
- **OpenAI Logs UI:** `https://platform.openai.com/logs/<chatcmpl-...>` (per-completion deep link; no project-level filtered URL — switch projects in the dashboard sidebar to see the journal log stream)
- **Aura API base URL:** `https://services.aurahealth.io`
- **Linear tickets:** [PROD-2458](https://linear.app/aura-health/issue/PROD-2458) (this migration); [PROD-2456](https://linear.app/aura-health/issue/PROD-2456) (umbrella); [PROD-2411](https://linear.app/aura-health/issue/PROD-2411) (original skill build)
- **Sibling skill:** `data:analyze-user-conversations` — same pattern, OpenAI `Conversation [Production]` project. Both migrated off Braintrust under PROD-2456.
- **Rendered journal endpoint:** `POST /ai/journals/list/:userId` for the footer's `user:` (the owner's full uid), then the row with the footer's `id:` — the `journal` row primary key, set by `src/models/journal.js#rate`. `GET /ai/journal/:journalId` answers only the owner, and no route looks up a row's owner
- **IntelligenceService source:** `src/models/journal.js` (`rate()` builds the k8bot footer and fences the user's `feedback`), `src/prompters/journal.js`, `src/config.js` (project `journal`, openaiAPI: `chatCompletions`, `store: true`)
- **Tools/OpenAI access pattern:** AI Knowledge → `Tools/OpenAI` → "Reading stored completions (BT migration access pattern)" — endpoints, completion shape, querying patterns, constraints.
