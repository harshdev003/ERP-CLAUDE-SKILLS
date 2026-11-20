---
name: profile-user
description: >
  The reusable user-profiling engine for Aura. Given a Firebase UID, an Aurie conversation ID,
  or a Slack-pasted k8bot rating URL, it composes up to four sibling skills —
  `data:analyze-user-behavior`, `data:analyze-user-conversations`, `eng:report-user-costs` and
  `eng:build-aurie-user-insights` — into one unified report. Flag-driven: `sources` selects
  which sub-analyses run, `steering` focuses the synthesis, `context` shapes a per-kind
  Feedback explanation, and `side_effects` suppresses persistence. Trigger on "profile user
  {UID}", "build a full profile for {UID}", "give me everything we know about {UID}", "why did
  {UID} cancel and what's their profile", "360 on {UID}", "what happened with this user", or a
  pasted k8bot rating Slack URL asking for analysis or the full picture. Do NOT use for
  behavioural-only readouts (`analyze-user-behavior`), conversation-only readouts when you
  already hold a UID or conversation ID (`analyze-user-conversations`), or aggregate topic
  reports (`fetch-conversation-topics`).

metadata:
  version: "3.3.0"
knowledge:
  reads:
    - Compliance/Privacy      # PII redaction rules — UID prefix + email domain only; never full email/name in Slack/Notion
    - Guide/Index             # baseline routing aid (universal)
    - Tools/Aura API          # canonical endpoint doc — Usage → Engagement (`GET /ai/usage/engagement/:userId`), Step 3d tenure + volume
    - Products/Aurie     # brand voice for the unified synthesis
    - Products/Aurie/Technology/Profile Insights & Card Renderers  # what each renderer card is for — the `insights` source needs it to judge the rated artifact, not just describe it
    - Team/Internal Accounts  # employee / internal-account allowlist (Firebase UID / auth-provider ID / email) — drives the Step 3.5 employee-flag check
    - Tools/Braintrust        # cost-accounting rule — why per-user cost routes through eng:report-user-costs, not estimated_cost
    - Tools/Slack             # canonical Channel IDs (for skills) — Mode A URL parsing + Step 0.5 pre-check + Step 6 reply offer
    - Learnings/Conversations # check for prior analyses on the same conversation_id
    - Tools/Mixpanel                                                   # Mixpanel service-account / MCP access
    - Tools/OpenAI                                                     # OpenAI project keys + stored-completions access
  writes:
    - Learnings/Conversations # Step 7: record a rated conversation (notion_record on for conversation_only, off for full_profile)
---

# profile-user

The reusable user-profiling engine: it composes up to four sibling skills for one user and synthesises a single unified report. Callers tune it with flags — `sources` (which sub-analyses run), `steering` (a free-text focus string), `context` (`{kind, stated, category}`, which shapes the per-kind **Feedback explanation** section), and `side_effects` (which persistence steps fire, and how). Two classes of caller use it:

- **Orchestrators** — `data:investigate-cancellation-feedback` and `data:investigate-pmf-survey` sweep a whole channel of feedback and call this skill once per user with `side_effects: off`, owning the Slack/Notion persistence themselves. The cancellation flow is `context.kind=cancellation`; the PMF flow is `context.kind=pmf` with `sources=[behavior, conversations]`.
- **Direct human use** — paste a Slack URL of a k8bot rating message and the skill detects the shape and routes:
  - **Cancellation feedback** (`*New Cancellation Feedback*`) → full picture: composes `data:analyze-user-behavior` + `data:analyze-user-conversations` + `eng:report-user-costs` and synthesises a unified report whose **Feedback explanation** section (heading "Cancellation explanation") cross-references the stated reason against behavioural, conversational, and cost evidence.
  - **Quick-processing / Coaching / Conversation / Chat-message rated** → focused conversation analysis: invokes only `data:analyze-user-conversations` against the conversation ID embedded in the rating message, with the rating's feedback used as steering. The behavioural and cost sub-skills and the synthesis layer are skipped — for a single rated reply, the user-level profile is overkill.
  - **Journal rated** → focused journal analysis: invokes only `data:analyze-user-journal` against the journal ID embedded in the rating message. No steering is passed (the fenced block is journal entry text, not feedback about the AI output). Same lightweight-wrapper output pattern as conversation-only.

Direct (non-Slack) invocation against a UID also works — that path runs the full-picture flow, since "profile a user" is the natural ask outside Slack.

**Employee / internal flag.** On every route the skill runs a lightweight check (Step 3.5) against the `Team/Internal Accounts` allowlist plus the Aura-API `@aurahealth.io` / `role` / `isAIReviewer` / `test` signals. When the entry is from an Aura employee or an internal/test account (including a founder testing on a personal `google:…` account), the report opens with a clear "⚠️ Employee / internal account" banner. It's a **flag, not a filter** — the entry is still analysed and still counts in any caller's aggregate.

The skill **returns the report as its result**. On the `conversation_only` route (a single rated conversation) it records that analysis to `Learnings/Conversations` by default (Step 7; `notion_record: off` to skip); the `full_profile` route does **not** bulk-record a user's history. The Slack reply (Step 6) and Braintrust save (Step 8) are opt-in. The caller decides what else to do with the returned report.

## When to use

Trigger when the user asks for a user-level picture or pastes any k8bot rating URL:

- "profile user `<UID>`" / "build a full profile for `<UID>`"
- "give me everything we know about `<UID>`"
- "why did `<UID>` cancel and what's their profile"
- "explain this cancellation reason for `<UID>`"
- "360 on `<UID>`"
- pastes a Firebase UID + a cancellation reason and asks for the full picture
- pastes a Slack URL pointing at a k8bot rating message — any of: `*New Cancellation Feedback*`, `*QUICK PROCESSING RATED*`, `*COACHING RATED*`, `*CONVERSATION RATED*`, `*CHAT MESSAGE RATED*`, `*JOURNAL RATED*`, or `*PROFILE RATED*` — and asks for analysis / "the full picture" / a profile / "what happened with this user"

**Do NOT use for:**

- Behavioural-only profile → use `data:analyze-user-behavior` directly.
- Conversation-only friction analysis when you already have a UID or conversation ID → use `data:analyze-user-conversations` directly. This orchestrator is the front door for Slack URLs; if the caller already has the inputs in hand, the atomic sibling is one less hop.
- Aggregate topic reports across users → `fetch-conversation-topics`.
- Experiment-level analysis → `analyze-experiment`.

## Inputs

The skill accepts two input modes plus a set of tuning flags. Pick the input mode that matches what the caller has; the flags (documented under **Flags (all modes)** below) control which sub-analyses run and how the report is framed.

**Mode A — Slack URL.** Paste a Slack permalink to any k8bot rating message (e.g. `https://auramobile.slack.com/archives/C0AENS8D48N/p1778072519577519` or `https://auramobile.slack.com/archives/C07MB0TMF5J/p1778207250134199`). The skill resolves the URL into `channel_id` + `message_ts`, fetches the message by **invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** for that `channel_id` + `message_ts` (that skill owns the connection — MCP-first, `SLACK_BOT_TOKEN` script fallback — and unfurl-off; don't call Slack directly here), then **detects the message shape** and parses it accordingly. Seven shapes are recognised:

| # | Header (first line) | Route | `context.kind` | Key extracted fields |
|---|---|---|---|---|
| 1 | `*New Cancellation Feedback*` | `full_profile` | `cancellation` | `user_id`, `context.category` (Reason), `steering`/`context.stated` (Feedback), subscription block (Subscription/Type/Product/Purchased/Provider) |
| 2 | `*QUICK PROCESSING RATED*` | `conversation_only` | `rating` | `user_id`, `conversation_id`, `dialogue` (provenance), `prompt:<name>:<version>`, `rating_polarity`, `rating_feedback` |
| 3 | `*COACHING RATED*` | `conversation_only` | `rating` | same footer as Shape 2 (`prompt` is a coach-prompter version) |
| 4 | `*CONVERSATION RATED*` | `conversation_only` | `rating` | same footer as Shape 2 + neutral polarity (`:neutral_face:`) and the literal `No feedback` placeholder → `steering=""` |
| 5 | `*CHAT MESSAGE RATED*` | `conversation_only` | `rating` | same footer as Shape 2 (`prompt` typically `guardrails-handler-prompter:<version>`; polarity binary) |
| 6 | `*JOURNAL RATED*` | `journal_only` | `rating` | `user_id`, `journal_id` (footer `id:`), `journal_text` (fenced block — NOT steering), `rating_polarity` from raw 👎/👍 |
| 7 | `*PROFILE RATED*` | `full_profile` + `insights` | `rating` | `user_id`, `rating_id` (footer `id:`), optional `renderer_key`, `rating_polarity`, `steering`/`context.stated` (Feedback) |

The routing decision is the `Route` + `context.kind` columns above; the verbatim per-shape fenced examples, the exact field-to-flag mapping, and the shape-specific edge notes (neutral polarity, `No feedback` placeholder, journal `id:` vs `conversation:`, `renderer:` sub-field) live in [`references/input-shapes.md`](references/input-shapes.md).

**Unrecognised k8bot shape.** If the first line starts with `*` but matches none of the seven headers, *don't guess* — surface the unknown header and offer Mode B or a generic `conversation_id`. If it isn't a k8bot rating at all, say what shape was found and ask the caller to re-paste or fall back to Mode B. Full wording in [`references/input-shapes.md`](references/input-shapes.md).

**Mode B — Direct.** Provide a pivot plus optional flags. **`conversation_id` decides the route whenever it is present, even alongside a `user_id`** — the two are not competing pivots. A conversation-pivot caller supplies both: `conversation_id` selects the conversation and `user_id` only feeds Step 3d's user-scoped engagement call. Routing on `user_id` when both are given would turn a single rated conversation into a windowed whole-user analysis, which is the wrong artifact and far more expensive.

- **`user_id`** — Firebase UID (28 alphanumeric chars, e.g. `0VwbDpKVEva6FvKOSjYWDzb07yi2`). Runs the full-picture flow.
- **`conversation_id`** — Aurie conversation UUID (`metadata.conversationId` on the OpenAI stored completions). Runs the conversation-only flow (one specific conversation by ID), and wins the route over a co-supplied `user_id`.
- **`rating_id`** (optional) — a `rendererRating` row id, from a `*PROFILE RATED*` footer `[id: <uuid>, user: <UID>]`. Accepted in **both** modes: Shape 7 sets it automatically, and a Mode B caller can pass it alongside `user_id`. It is what makes the `insights` source return the **exact rated snapshot** instead of the current profile — without it the source silently degrades to a live read.

Direct UID invocation runs the full-picture flow by default. Orchestrators (`investigate-cancellation-feedback`, `investigate-pmf-survey`) call this skill in Mode B, passing `user_id` plus the flags below.

If neither `user_id` nor `conversation_id` can be resolved (Mode A parse failure with no fallback; Mode B missing both), ask. Don't guess.

### Flags (all modes)

These tune what runs and how the report is framed. In Mode A the skill auto-derives `steering` / `context` from the recognised k8bot shape; a caller can still override any flag.

- **`sources`** (optional) — which sub-analyses to run, a subset of `{behavior, conversations, cost, insights}`. Default: `behavior, conversations, cost` for the full-picture / user pivot, **plus `insights` whenever a `rating_id` is present** (Shape 7 always supplies one; a Mode B caller may too — one rule, so the artifact is never skipped depending on entry path); `conversations` only for the conversation pivot. Omitting a source drops both its sub-skill invocation (Step 3) and its synthesis section (Step 4) — e.g. `sources=[behavior, conversations]` skips `eng:report-user-costs` and omits the **Cost over window** section. `cost` requires a `user_id` (the cost skill is user-scoped) and is ignored on the conversation pivot. `insights` fetches the rated profile artifact — see Step 3a. A plain UID profile carries no `rating_id`, so it doesn't run there: the source answers "what did the user rate", which a generic profile run isn't asking.
- **`cost_sources`** (optional, default all four) — subset of `{openai, elevenlabs, livekit, listens}`, forwarded verbatim to `eng:report-user-costs` as its `--sources` flag. Only meaningful when `cost` is in `sources`. All four legs run by default and that is normally right — every leg is user-scoped except OpenAI, which walks the `[Production]` projects serially and is the slow one (~9 min; ElevenLabs, LiveKit and listens are seconds — see [`eng:report-user-costs`](../../../eng/skills/report-user-costs/SKILL.md) → Runtime). Narrow it only for a real constraint, such as a caller that lacks one provider's key. **Whatever is dropped must be named in the Cost section** — see Step 4a item 4; a narrowed cost total that reads as complete understates the user, and voice in particular is what explains a cost complaint.
- **`steering`** (optional) — free-text string that focuses the synthesis. Passed verbatim to whichever sub-skills run (they accept it as their `cancellation_feedback` arg, documented on both as a generic steering string). **Alias:** `cancellation_feedback` is still accepted for back-compat; if both are passed, `steering` wins.
- **`context`** (optional) — `{ kind, stated, category }`, shapes the **Feedback explanation** synthesis section:
  - **`kind`** — one of `cancellation` | `pmf` | `rating` | `generic` (default `generic`). Selects the section heading + framing (see Step 4a item 6). `generic` omits the section.
  - **`stated`** — the verbatim user quote (cancellation feedback text, PMF free-text answer, or rating feedback). Rendered as the section's blockquote. The section only renders when `stated` is non-empty.
  - **`category`** — optional categorical label: cancellation dropdown reason, PMF sentiment bucket (`Very` / `Somewhat` / `Not disappointed`), or rating polarity.
- **`time_window`** (optional, default `30d`) — `30d` or `90d`. See **Window mapping** below. (Ignored on the conversation pivot — scoped to one conversation by ID.)
- **`side_effects`** (optional) — `{ slack_reply, notion_record, braintrust_dataset }`, each `on` | `off` | `ask`. Defaults for direct human use: `slack_reply` and `braintrust_dataset` default `ask`; **`notion_record` defaults by route — `on` for `conversation_only` (a single rated conversation, which is what we want in `Learnings/Conversations`), `off` for `full_profile`** (a whole-user profile — do NOT bulk-record a user's backlog) and `journal_only`. Semantics: `off` skips the step silently; `ask` shows the draft and waits for an explicit yes before acting; `on` acts by default — for `notion_record`, compose the masked row, write it, and surface the result (no pre-write prompt), while `slack_reply`/`braintrust_dataset` on `on` still require a human yes before the outward send (per Claude Code's actions-visible-to-others rule) and just pre-select the offer. An orchestrator that owns persistence passes the relevant ones `off`, and the matching Steps 6/7/8 are skipped silently.
- **`record_meta`** (optional) — `{ slack_thread_url, source_type, prompt_version, rating }` for the `conversation_only` record when the run is Mode B (a direct `conversation_id`, e.g. from the `investigate-user-rating` sweep) and Step 0 didn't parse a Slack URL. Populates the recorded row's `Slack Thread URL` / `Source Type` / `Prompt Version` (full `prompter_name:prompter_version` form) / `Rating` (Title-case enum) so it isn't written missing or malforming required fields. Ignored when `notion_record` is off.
- **`skip_prior_check`** (optional, default `false`) — when `true`, bypasses the Step 0.5 prior-analysis pre-check (Mode A only — Mode B has no source thread to check). Use for re-runs where the caller has already decided to refresh, or when an orchestrator owns the pre-check itself (e.g. `data:investigate-cancellation-feedback` runs Step 2.5 before invoking this skill and passes `skip_prior_check=true` to avoid duplicating the ask).

**Feedback propagation.** Whenever `steering` is set (Mode A extraction in any shape, or Mode B flag), it is passed verbatim to the relevant sub-skill(s):

- **Full-picture flow:** passed to the behavioural + conversational sub-skills — `analyze-user-behavior` re-ranks **Top features** and tightens **Notable signals**; `analyze-user-conversations` scans transcripts for patterns matching it. `eng:report-user-costs` takes no steering (same cost number regardless). The orchestrator then composes the **Feedback explanation** (per `context.kind`) in Step 4.
- **Conversation-only flow:** passed only to `analyze-user-conversations` (via its `cancellation_feedback` arg, which is overloaded as a generic steering string — see the sibling skill's docs). The rating's feedback text steers the friction analysis even though the rating isn't a cancellation.

### Window mapping

The two sub-skills support different window vocabularies. This skill normalises:

| `profile-user` | `analyze-user-behavior` | `analyze-user-conversations` |
|---|---|---|
| `30d` *(default)* | `30d` | `60d` |
| `90d` | `90d` | `60d` |

`60d` is the conversations window regardless of the profile-user window: there is a known OpenAI storage bug where completions are not stored, and `fetch_logs.py` automatically retries against Braintrust on zero OpenAI matches. A 60-day window ensures the Braintrust fallback covers users who cancel weeks after their last conversation — the case that motivated this (user `4YfC0Ynm…` had conversations from 45–56 days prior that OpenAI missed but Braintrust held).

Anything else (`7d`, `last_conversation`, `last_n=...`) — route to the atomic sibling that supports it. This orchestrator only handles the two windows in the table above.

## Workflow

### Step 0 — Resolve inputs, detect shape, set the routing flag

**Mode A (Slack URL).** Detect the URL shape first — `https://<workspace>.slack.com/archives/<channel_id>/p<ts_no_dot>` (the `p` form) or `/p<ts_no_dot>?thread_ts=<...>`. Convert: `channel_id` is the path segment after `/archives/`; `message_ts` is the `p<digits>` slug with a decimal point inserted before the last 6 digits (e.g. `p1778072519577519` → `1778072519.577519`). Fetch the parent message by **invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** for that `channel_id` + `message_ts` — only the parent message is needed for parsing; thread replies are not. That skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and unfurl-off — don't call Slack directly here.

**Detect the message shape** by reading the first non-blank line of the parent body:

| First line | Shape | Routing flag | `context.kind` |
|---|---|---|---|
| `*New Cancellation Feedback*` | Cancellation feedback | `route = "full_profile"` | `cancellation` |
| `*QUICK PROCESSING RATED*` | Quick-processing rated | `route = "conversation_only"` | `rating` |
| `*COACHING RATED*` | Coaching rated | `route = "conversation_only"` | `rating` |
| `*CONVERSATION RATED*` | Conversation rated | `route = "conversation_only"` | `rating` |
| `*CHAT MESSAGE RATED*` | Chat message rated (typically a guardrails-handler reply) | `route = "conversation_only"` | `rating` |
| `*JOURNAL RATED*` | Journal rated | `route = "journal_only"` | `rating` |
| `*PROFILE RATED*` | Profile rated (the generated insights/renderers) | `route = "full_profile"` | `rating` |
| anything else starting with `*` | unknown k8bot shape | stop and ask (see Inputs → "Unrecognised k8bot shape") | — |
| no `*` header | not a k8bot rating | stop and ask the caller to re-paste or fall back to Mode B | — |

**Parse per shape** using the field rules in the **Inputs** section above, setting `context.kind` per the table and `context.stated` / `steering` from the feedback text. Persist `channel_id` + `message_ts` regardless of shape — Step 6's Slack-reply offer applies uniformly across the rating types. For `*JOURNAL RATED*`: extract `journal_id` from the `id:` footer field (not `conversation:`), `journal_text` from the fenced block, and `rating_polarity` from the raw 👎/👍 emoji (no text suffix expected).

**Mode B (direct).** Route on `conversation_id` first: if it is present, `route = "conversation_only"` — **regardless of whether a `user_id` came with it** (that uid is for Step 3d's engagement call, not a route signal). Only when `conversation_id` is absent does a `user_id` set `route = "full_profile"`. If neither is present, ask. **Capture `rating_id` when the caller passes one** — an orchestrator forwarding a profile rating supplies it alongside `user_id`, and dropping it silently downgrades the `insights` source from the rated snapshot to a live read. Capture any `steering` (or its `cancellation_feedback` alias) and `context` flags verbatim — quotes and emoji included; the sub-skills need the raw string to steer their syntheses. When a caller passes `sources`, honour it (Step 3); otherwise apply the per-route default. When a caller passes `side_effects`, honour it in Steps 6–8.

**Both modes — sub-skill prerequisites.** This skill does not check API keys directly — the sub-skills run their own Step 0 prerequisite checks (Mixpanel access via MCP **or** the `MIXPANEL_SERVICE_ACCOUNT_*` API creds, `OPENAI_CONVERSATION_API_KEY` / `OPENAI_JOURNAL_API_KEY` — project-scoped service-account keys, **not** `OPENAI_ADMIN_API_KEY` (PROD-3572) — for the conversation/journal sub-skills, `AURA_API_TOKEN`). Mixpanel access is the only hard requirement for the behavioural sub-skill, and either transport satisfies it — **don't stop the run as "Mixpanel unavailable" when the API creds are present but MCP isn't loaded**; `analyze-user-behavior` will fall through to the API path in its own Step 2. If a sub-skill stops at its Step 0, this orchestrator surfaces that gap and stops too. Don't synthesise from a partial picture. (For `route = "conversation_only"`, only `analyze-user-conversations`'s prerequisites apply: `OPENAI_CONVERSATION_API_KEY` is required, `AURA_API_TOKEN` is best-effort.)

**Cowork caveat.** Sub-skills need host env (`OPENAI_CONVERSATION_API_KEY`, `OPENAI_JOURNAL_API_KEY`, `AURA_API_TOKEN`, and the three `MIXPANEL_*` vars) that `mcp__workspace__bash` strips (PROD-1305). See [`/CLAUDE.md`](../../../../CLAUDE.md) → "Known Cowork constraints" for the per-session paste-export workaround. This orchestrator inherits the constraint from the sub-skills.

### Step 0.5 — Prior-analysis pre-check (Mode A only)

After `channel_id` + `message_ts` are persisted and the message shape has been parsed, walk the source thread for a prior reply from our own analysis bot user. The goal: surface any prior `profile-user` analysis already posted on this thread so the caller can decide whether to re-run, skip, or just review what's there — before paying for sub-skill runs.

Skip this step when:

- `skip_prior_check=true` (a parent orchestrator has already run the per-entry pre-check and is suppressing the duplicate ask).
- Mode B (no source thread exists).
- Slack `auth.test` fails (401, missing `SLACK_BOT_TOKEN`, network error) — note *"prior-analysis pre-check skipped — Slack auth.test unavailable"* in the report's final **Sources** block; the run still proceeds to Step 1.

**Mechanics.**

1. Resolve the bot's own user_id once via Slack `auth.test` on `SLACK_BOT_TOKEN`. The returned `user_id` (e.g. `U05PB6H1234`) is the identity used to detect prior analyses on the thread.
2. Fetch the thread replies by **invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** for that `channel_id` + `message_ts` (its `replies` mode — `conversations.replies` on the thread). That skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and unfurl-off — don't call Slack directly here.
3. **Skip the thread parent** (it's the k8bot rating / cancellation message, not our reply). Find the most recent reply whose `user` field equals the bot user_id. If none, continue silently to Step 1.

**When a prior analysis is found**, surface it inline before any sub-skill runs:

```
Prior analysis found on this thread:
- posted: 2026-05-22 14:31 UTC
- first lines:
  > <first 3 non-blank lines of the prior reply, each truncated to ~200 chars>
- permalink: https://auramobile.slack.com/archives/<channel_id>/p<reply_ts_no_dot>?thread_ts=<message_ts>
```

Then ask **three explicit choices** — no defaults, ambiguous responses ("hmm" / "maybe") are not enough to advance:

- **Re-run** — proceed to Step 1 and beyond. Step 6's reply offer will mention the prior reply so the caller can decide between posting a fresh reply or skipping the post.
- **Skip** — return immediately with the prior-analysis permalink as the answer. The report's **Sources** block cites the permalink + the `auth.test` verification that resolved the bot user_id; no sub-skills run.
- **Show only** — fetch the prior reply's full body by **invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** for that `channel_id` + `message_ts` (its `replies` mode), render it inline as the report (no synthesis layer, no new framing), and return without running any sub-skills or posting anything new. That skill owns the connection — don't call Slack directly here. **Sources** cites the permalink and the `auth.test` verification.

### Step 1 — Pull AI Knowledge priors

Invoke `shared:retrieve-knowledge` to pre-load the paths declared in `knowledge.reads`:

- **`Products/Aurie`** — brand-voice priors for the unified synthesis. The sub-skills already pull this for their own framing; we re-fetch (cache-served, see `/CLAUDE.md` → "Knowledge manifest" → "Local cache") so the **Headline**, **Notable signals**, and **Feedback explanation** sections speak the team's actual vocabulary about Aurie users rather than a generic chatbot frame.
- **`Team/Internal Accounts`** — the employee / internal-account allowlist (Name → Firebase UID(s) / auth-provider ID(s) / personal email + the `@aurahealth.io` / role / `isAIReviewer` / `test` rules). Loaded here so the Step 3.5 employee check is a local lookup, not a per-entry Notion round-trip. Cache-served (24h TTL) — cheap even on a full orchestrator sweep.

The sub-skills will pull the rest of their knowledge (Mixpanel taxonomy, OpenAI stored-completions schema, Aura API conventions) when invoked in Step 3 — don't duplicate those reads here.

If the path fails to resolve, note the miss and proceed.

### Step 2 — Build the orchestration plan, show it, then act

**Plan-before-side-effects cue.** Before invoking any sub-skill, lay out a one-paragraph plan in chat:

- The detected `route` (`full_profile` vs `conversation_only`) and the message shape that produced it.
- For `full_profile`: the UID (prefix only), the window flag, and the `sources` that will run (default all three — note any the caller dropped), plus the always-on tenure + engagement call (Step 3d); the `context.kind` and whether `steering` is present, plus what specifically the synthesis will look for (e.g. for a "too expensive" cancellation: paywall views, trial-limit hits, payment friction in transcripts; for a PMF "Not disappointed": tenure / days-since-signup and habit-formation signals; for "not conversational": short Aurie replies, scripted multiple-choice on emotional bids); the window mapping passed to each sub-skill (call out the `90d → 30d` cap if applicable).
- For `conversation_only`: the conversation ID (full UUID — it's the entire payload), the rating polarity (positive/negative), the prompter version (e.g. `quick-processing-prompter:0.235.1`), and what the rating's feedback string is steering toward.

This is the moment to slow down — for `full_profile` it keeps the synthesis cross-referencing two reports rather than concatenating them; for `conversation_only` it makes the steering explicit before the conversation analysis runs.

### Step 3 — Invoke sub-skills

Branch on the `route` flag set in Step 0.

#### Step 3a — `route = "full_profile"`: invoke the selected sub-skills in parallel

Compose; do not re-implement. Invoke the sub-skills named in `sources` via the `Skill` tool in a **single message with parallel calls** so they run concurrently. `sources` resolves to `{behavior, conversations, cost}`, plus **`insights` whenever a `rating_id` is present** (see Inputs). **`steering`, when set, is passed to both the behavioural and conversational sub-skills** (as their `cancellation_feedback` arg) — each steers its own analysis (behaviour: re-ranks Top features, weights Notable signals, tightens Subscription history narrative; conversations: scans transcripts for patterns matching it). The cost sub-skill takes no steering — it returns the same authoritative number regardless. The orchestrator's **Feedback explanation** in Step 4 then layers on top of already-steered reports rather than re-deriving the steering itself.

Invoke only the requested sources — skip the bullet for any source the caller omitted, and omit its synthesis section in Step 4:

- `behavior` → `data:analyze-user-behavior` with `user_id=<UID>`, `time_window=<mapped behavior window>`, `cancellation_feedback=<steering verbatim when present>`.
- `conversations` → `data:analyze-user-conversations` with `user_id=<UID>`, `time_window=<mapped conversations window>`, `cancellation_feedback=<steering verbatim when present>`, **`record=false`** (this orchestrator owns the Notion record in Step 7 — suppress the sibling's default-on Step 8 so a conversation isn't recorded twice).
- `cost` → `eng:report-user-costs` with `user_id=<UID>`, `--window <mapped behavior window>` (mirror the behavior window — listens + ElevenLabs are best read on the same window as behaviour, not the narrower conversations cap), and `--sources <cost_sources>` when the caller supplied one (default: all four). Pass `--json /tmp/profile_user_cost_<UID-prefix>.json` so the structured payload is available for the synthesis section. Record which legs ran — Step 4a item 4 has to name any that didn't.
- `insights` → `eng:build-aurie-user-insights` with **`mode=read`**, `user_id=<UID>`, **and `rating_id=<rating_id>`** — its presence is what selected this source in the first place. Pass **both**, not one or the other: the sub-skill's live fallback is keyed by user, and a 404 on the snapshot returns no user, so a rating-id-only call stops empty. Returns the compiled insight + card renderers — the thing a `*PROFILE RATED*` rating is *about*. **`mode=read` is mandatory here**: the skill's default `build` mode POSTs a fresh compile, which overwrites the very artifact under investigation. Never invoke it without the flag from this step.

  A `rating_id` returns the **exact snapshot** the user rated; a bare UID returns the **current** profile, which a later recompile may have replaced. The sub-skill labels which one it returned — carry that label into **Sources** verbatim and never upgrade a live read to "what the user rated".

**Plus the tenure + engagement call.** [Step 3d](#step-3d--all-routes-pull-tenure--engagement-volume) issues one `GET /ai/usage/engagement/:userId` regardless of `sources` — fire it in the same parallel message as the sub-skills above.

Each sub-skill returns its own Markdown report. Hold the returned reports in working memory for Step 4 — do **not** paste them verbatim into the final output (that would just duplicate reports the caller could have run separately). The job here is synthesis.

**Cost source-of-truth rule.** The cost number for this user MUST come from `eng:report-user-costs` (which routes OpenAI cost via `/v1/organization/costs`, ElevenLabs via duration × rate, LiveKit via the per-user daily cost store in BigQuery, listens via the PaymentService payout endpoint). **Do not compute a cost from Braintrust `metrics.estimated_cost`** — see AI Knowledge [`Tools/Braintrust` → "Cost accounting"](https://www.notion.so/34c75e23af2081968f7bf7ee43e1442a) for the reason (pricing-table gaps, wrong tier, single-project framing).

**If one sub-skill returns an empty/blocked result** (no engage profile from `analyze-user-behavior`; zero OpenAI completions from `analyze-user-conversations`; one of the four cost sources unavailable or pending in `report-user-costs`):

- Surface the gap in the **Sources** block.
- Continue with the available report — but note in the **Headline** that the picture is partial.
- A profile from one source alone is still useful (e.g. user signed up but never talked to Aurie); just don't pretend it's 360°.

**If both behavioural sub-skills return empty/blocked**, check the Step 3d engagement response before stopping. Both sub-skills are **window-bounded** (Mixpanel window, OpenAI retention), so "empty" from the pair means "nothing in that window" — not "no user". The engagement call is lifetime and unwindowed, so it can still return real tenure and volume for a user who was simply inactive over the window. **If it succeeded at all — even with every count at zero — continue**: render section 2b plus whatever else is available and flag in the **Headline** that the picture is lifetime-only. An all-zero response is not an empty one — it still carries `tenure`, and "signed up `<tenure line>`, never engaged with Aurie" is a finding in its own right, often *the* finding for a churn read. Resolve that tenure line through the table in [`references/engagement.md`](references/engagement.md) like any other: a stamped `createdAt` renders hedged here too. Gate on whether the call **succeeded**, never on whether counts are non-zero; stop only when it actually failed (non-2xx, network, missing token). Then tell the user the UID has no usable footprint in any source and ask them to confirm it. The cost sub-skill can fail independently and the profile run continues — render the **Cost over window** section with an "unavailable" note rather than blocking on it.

#### Step 3b — `route = "conversation_only"`: invoke `analyze-user-conversations` directly

One sub-skill call plus the [Step 3d](#step-3d--all-routes-pull-tenure--engagement-volume) engagement `GET` (which runs on this route too, and feeds the preface's `Tenure & engagement` line) — no synthesis layer. Invoke `data:analyze-user-conversations` via the `Skill` tool with:

- `conversation_id=<UUID extracted from the rating message footer>` — drives the sibling skill's conversation-pivot mode (its Step 2 Option B), which fetches the full span tree including the rating span itself.
- `cancellation_feedback=<steering verbatim, when present>` — this is the sibling skill's own param name (a v1 leftover); semantically it's a generic steering string and the sibling applies it the same way regardless of whether it came from a cancellation or a thumbs-down. If steering is empty (positive rating with no comment, or negative rating with no typed feedback), pass `cancellation_feedback=""` and let the sibling produce its generic friction + positives report.
- `record=false` — this orchestrator owns the Notion record (Step 7). The sibling's Step 8 records a conversation-pivot by default, so suppress it here to avoid a double write; profile-user's Step 7 is the single recorder for this route.

**Assemble `record_meta` for Step 7 (both modes).** Step 7 needs the row's `Rating` / `Source Type` / `Prompt Version` / `Slack Thread URL`, which the OpenAI completion alone doesn't carry — build `record_meta` here so the write is complete and schema-valid:
- **Mode A (Slack URL parsed in Step 0):** `slack_thread_url` = the pasted permalink; `source_type` from the shape (QP→`Quick Processing Rated`, Coaching→`Coaching Rated`, Conversation→`Conversation Rated`, Chat→`Chat Message Rated`); `prompt_version` = the full `prompt:<name>:<version>` footer value; `rating` = Title-cased `rating_polarity` (`positive`→`Positive`, …). Also set `context.category = rating_polarity`.
- **Mode B (`investigate-user-rating` sweep):** `record_meta` arrives from the caller — use it verbatim.

The sibling skill's report is the orchestrator's output for this route — Step 4 below collapses to "return as-is, with a thin wrapper noting the route and the rating polarity/prompter at the top". No behavioural or cost sub-skill, no unified synthesis: the rating is about a single conversation, not a churn signal, and a 360 framing would be noise. (`context.kind=rating` here; the thin wrapper's preface plays the role of the Feedback explanation section.)

**If `analyze-user-conversations` returns no logs for the conversation ID**, stop. Tell the user the conversation ID from the rating message can't be found in the OpenAI Conversation [Production] logs and ask whether the message might be older than the logs retention window (or predate the 2026-05-12 `metadata.user` tagging rollout).

#### Step 3c — `route = "journal_only"`: invoke `analyze-user-journal` directly

One sub-skill call plus the [Step 3d](#step-3d--all-routes-pull-tenure--engagement-volume) engagement `GET` — `analyze-user-journal` carries no engagement data, so that call is the only source for the preface's `Tenure & engagement` line. Invoke `data:analyze-user-journal` via the `Skill` tool with:

- `journal_id=<UUID extracted from the [id: ...] footer field>`
- `user_id=<UID extracted from the [user: ...] footer field>`
- `journal_text=<fenced block content from the k8bot message>` — passes the journal entry text as context for the analysis
- `rating_polarity=<positive|negative>` — from the 👍/👎 emoji
- `side_effects=<pass-through from caller>` — the orchestrator owns persistence

No `steering` is passed — the fenced block is the journal entry, not feedback about the AI output. `analyze-user-journal` steers from `rating_polarity` alone (negative = something went wrong with the journal-prompter output for that entry).

The sibling skill's report is the orchestrator's output for this route — Step 4 collapses to "return as-is, with a thin wrapper noting the route and the rating polarity at the top" (same pattern as Step 4b for conversation_only).

**If `analyze-user-journal` returns no journal for the user** (it routes by `user_id` + `journal_text`, not by `journal_id` — the k8bot `journal_id` is the legacy Braintrust span ID with no OpenAI equivalent), stop. Tell the user the journal can't be found in the OpenAI Journal [Production] logs for that UID — the user may not have used the journal feature in the window, or the window may predate the 2026-05-12 `metadata.user` tagging rollout.

#### Step 3d — all routes: pull tenure + engagement volume

One authenticated `GET /ai/usage/engagement/:userId`, **not** gated by `sources` — so an orchestrator that drops `behavior` or `cost` still gets tenure and volume. On `full_profile`, fire it in the same parallel message as the Step 3a sub-skills; on the two thin-wrapper routes it is the only user-level call. **It runs on `conversation_only` and `journal_only` too.** A user-level block may look like noise in an artifact-scoped wrapper, but tenure and volume are what tell a reader *how much weight that one artifact carries* — the same thumbs-down means different things from a two-session account and a several-hundred-conversation one, and nothing else in either wrapper distinguishes them. It is also the sole source for the `Tenure & engagement` line that `data:investigate-user-rating` and both `run-*-analysis-cadence` skills copy out of section 2b, and the conversation + journal shapes are the common ones. One GET does not reintroduce the per-entry cost the wrappers exist to avoid. **The call is user-scoped, so a `conversation_only` run needs a `user_id` in addition to its `conversation_id` pivot** — callers pass both (`data:investigate-user-rating` takes the uid off the parsed rating entry). When only a `conversation_id` was supplied, skip the call and render the line as `unavailable — no user_id supplied`; never derive a UID from the sibling report, which carries only the 8-char prefix. **A present-but-blank `user_id` counts as absent** — strip it and apply the same rule, because interpolating an empty value builds a bare `…/engagement/` that resolves to the self-serve route rather than erroring (precondition in [`references/engagement.md`](references/engagement.md)).

**[`references/engagement.md`](references/engagement.md) has the call, the field catalog, and three load-bearing rules — read it before rendering anything from this response.** In short: `aiCreatedAt` is frequently a bulk stamp rather than a real join date (two guards; render a lower bound when either fires, and never chase the exact date through Mixpanel or BigQuery), `conversations.total` counts *completed* conversations only, and a `0` means zero rather than missing.

### Step 3.5 — Employee / internal-account check (all routes)

Decide whether this entry came from an **Aura employee or an internal / test account** rather than a genuine user, so the report can say so plainly. This runs on **every** route (full_profile, conversation_only, journal_only) — a rated conversation or a cancellation from a founder testing the app is exactly the case this exists to catch. It is a **flag, not a filter**: the entry is still analysed and still counts in any caller's aggregate (see the orchestrators' digests) — we just label it.

**Inputs to match.** Collect every identifier already in hand for this entry (don't fetch anything new solely for this check):

- `user_id` — the Firebase UID (Mode A footer `user: <UID>`, or Mode B `user_id`).
- Any **auth-provider ID** carried by the source (PMF entries arrive as `<name> - google:<...>` / `apple:<...>`; when an orchestrator passes one through, match it verbatim).
- The account **email** and **role / isAIReviewer / test** flags — only when a sub-skill already surfaced them. On `full_profile`, `analyze-user-behavior` reports the email domain and (per its Identity section) an **Internal / employee account** note derived from the `/users/<id>` `email` / `role` / `isAIReviewer` / `test` fields — reuse that; don't re-call the API. On `conversation_only` / `journal_only`, the behavioural sub-skill didn't run, so only the allowlist + any display name are available.
- The **display name**, when the source carries one (PMF).

**Match order (first hit wins; all deterministic except the last):**

1. **Allowlist** — the `user_id`, auth-provider ID, or email matches a row in `Team/Internal Accounts` (pre-loaded in Step 1). This is the path that catches employees on **personal accounts** (e.g. a founder signed in with a personal `google:…` — no `@aurahealth.io` email, no admin role), so it must run even when no behavioural report exists. Record the matched name + which identifier hit.
2. **Email domain** — the account email ends in `@aurahealth.io` (from the behavioural report's Identity section, when present).
3. **Account role / flags** — the `/users/<id>` record has `isAIReviewer = true`, `test = true`, or `role ∈ {admin, agent, service, system, creator, audio-engineer}` (from the behavioural report, when present).
4. **Display-name heuristic (soft — never on its own)** — the display name matches a known employee first name from `Team/Internal Accounts` **and** at least one other weak signal points internal. Mark as **possible** employee (not confirmed) and say why; never hard-label on a shared first name alone.

**Result.** Set `internal = { is_internal: bool, confidence: confirmed|possible, who: "<name or —>", reason: "<allowlist:google:… | email_domain=aurahealth.io | role=admin | isAIReviewer | test | name+signal>" }`. When nothing matches, `is_internal=false` and no banner renders. Carry `internal` into Step 4 (every route renders it) and hand it back to the caller so an orchestrator can count it in its digest.

**PII note.** The banner names the employee's **first name only** (as listed in `Team/Internal Accounts`) — never the full email or full name. The UID-prefix / email-domain redaction rules in `Compliance/Privacy` still apply to everything else in the report.

### Step 4 — Compose the report

Branch on `route`. The full section-by-section formatting — what each `full_profile` synthesis section must contain and its PII rules, the Feedback-explanation skeleton + per-kind headings, the cost table and thin-wrapper preface templates — lives in [`references/synthesis-sections.md`](references/synthesis-sections.md). The routing instruction stays here.

#### Step 4a — `route = "full_profile"`: synthesise the unified report

Don't concatenate — cross-reference. Each section pulls from one or both sub-reports and adds a layer of judgment the sub-skills can't (they don't see each other's output). Render these sections **in this exact order** (omitting the guarded ones when their condition isn't met):

0. **Employee / internal banner** *(only when `internal.is_internal` from Step 3.5)* — first line of the report, above the Headline.
1. **Headline** — one-line TL;DR (UID prefix only), combining plan/vertical + the dominant cross-source signal + (if applicable) the cancellation reason.
2. **Identity & subscription state** — from the behaviour report; UID prefix + email domain only (hard PII rule), acquisition block verbatim, subscription state from `/subscriptions/list/:userId` (source of truth across all six sub types).
2b. **Tenure & engagement volume** *(always — not gated by `sources`, and rendered on every route including the two thin wrappers)* — from Step 3d: Aura sign-up, Aurie join (sentinel-guarded), lifetime completed conversations / messages / active days / streaks. Renders even when every count is zero, and renders with an "unavailable" note when the call failed. Downstream reply bodies copy this section, so it must exist on every route — see Step 3d.
3. **Behavioural profile** — distilled from `analyze-user-behavior` (engagement, journey events, subscription history, top features).
3b. **Rated profile** *(only when `insights` in `sources`)* — the artifact the rating is about: what was rated, the source label (rated snapshot vs live profile), and the per-card read connecting the content to the rating. Paraphrase renderer text — it is generated *about* the user.
4. **Cost over window** *(only when `cost` in `sources`)* — distilled from `eng:report-user-costs`; cost is from that skill only, **never** Braintrust `estimated_cost`. **Name any leg that did not run** — whether dropped via `cost_sources` or unavailable — e.g. "LLM + listens only; voice cost not included". A narrowed total rendered as a whole one understates a heavy voice user, which inverts the conclusion on a cost-driven signal. A leg that is pending or omitted is never written as `\$0.00`: "we did not measure" and "this cost nothing" are opposite claims.
5. **Conversational profile** — distilled from `analyze-user-conversations`; redact raw transcript PII before rendering.
6. **Feedback explanation** *(only when `context.stated` non-empty and `context.kind≠generic`)* — the cross-referencing synthesis layer; heading + framing follow `context.kind`.
7. **Notable signals** — 2–4 falsifiable bullets that only emerge from the combination.
8. **Recommended follow-up** — 2–4 concrete next steps, each naming an owner artifact.
9. **Sources** — every URL/path used (each sub-skill entry only when it ran) + the window-mapping note.

Section contents, PII rules per section, the Feedback-explanation skeleton, and the per-kind (`cancellation`/`pmf`/`rating`/`generic`) headings: [`references/synthesis-sections.md`](references/synthesis-sections.md) → "Full-profile synthesis sections".

#### Step 4b — `route = "conversation_only"`: thin wrapper over the conversation analysis

The sibling skill's report is already the right shape. Add a one-paragraph context preface above its `Headline` (employee banner when `internal.is_internal`, then rating / conversation ID / steering-feedback lines), paste the `analyze-user-conversations` report **verbatim** — do not rewrite or re-synthesise — and append the Slack permalink + prompter version to its Sources block. Exact preface template + Sources additions: [`references/synthesis-sections.md`](references/synthesis-sections.md) → "conversation_only wrapper". **The preface carries the section 2b line** (from Step 3d, which runs on this route) — both tenure clauses + lifetime volume, so a reader can weigh one rated reply against how much the user has actually used Aurie. It is what `data:investigate-user-rating` and the cadence skills copy into their threaded reply.

#### Step 4c — `route = "journal_only"`: thin wrapper over the journal analysis

Same pattern as Step 4b, over `analyze-user-journal`: employee-banner-aware preface (rating / journal ID / journal-text excerpt), sibling report pasted verbatim, and the Slack permalink + journal ID appended to its Sources block. Exact preface template + Sources additions: [`references/synthesis-sections.md`](references/synthesis-sections.md) → "journal_only wrapper". The preface carries the section 2b line too — `analyze-user-journal` has no engagement data of its own, so Step 3d's call on this route is the only source for it.

### Step 5 — Return inline

Return the report as the skill's result. Don't write it to disk. The Slack reply (Step 6) and Braintrust save (Step 8) are opt-in; the Notion record (Step 7) is on by default on the `conversation_only` route and off on `full_profile`.

**`side_effects` gating (applies to Steps 6–8).** Each step checks its `side_effects` key first: `off` → skip the step silently (no offer, no prompt); `ask` → show the draft and wait for an explicit yes before acting; `on` → act by default (for `notion_record`, compose + write + surface the result with no pre-write prompt; for `slack_reply`/`braintrust_dataset`, still require the human yes before the outward send). Defaults for direct human use: `slack_reply=ask`, `braintrust_dataset=ask`, and **`notion_record` by route — `on` for `conversation_only`, `off` for `full_profile` and `journal_only`** (we record single rated conversations, not a user's whole history). Orchestrators (`investigate-*`) pass `slack_reply: off` (they own the threaded reply), `notion_record: off`, and `braintrust_dataset: off` so a per-user run produces no side effects of its own.

When no `side_effects` are passed (direct human use): in **Mode A** runs continue to Step 6 (Slack-thread reply offer) then Step 7 (Notion record — default on for `conversation_only`, off for `full_profile`). In **Mode B** runs (direct UID, no Slack URL) Step 6 is skipped (no source thread); Step 7 follows the same route rule — a `conversation_only` (rated) run records by default, a `full_profile` run does not.

### Step 6 — (Mode A only) Offer to reply on the source thread

Skip this step entirely when `side_effects.slack_reply=off` — an orchestrator owns the threaded reply and a per-user run must stay silent.

When the run started from a Slack URL (Mode A) and Step 0 captured `channel_id` + `message_ts`, finish by **asking** whether the caller wants the analysis posted as a reply on that thread. Do not auto-post — sending a message on someone's behalf requires explicit per-turn confirmation.

The default body to offer depends on the route:

- **`route = "full_profile"`**: *"Want me to reply on the original Slack thread with this analysis? If yes, I'll post a thread reply with the Headline + **Tenure & engagement** + **Feedback explanation** (the full report can be linked or pasted on request)."*
- **`route = "conversation_only"`** (QP / coaching / conversation rated): *"Want me to reply on the original Slack thread with this analysis? If yes, I'll post a thread reply with a TL;DR + **Tenure & engagement** + the **Patterns / hypotheses** section from the conversation analysis (the full report can be linked or pasted on request)."*
- **`route = "journal_only"`** (journal rated): *"Want me to reply on the original Slack thread with this analysis? If yes, I'll post a thread reply with a TL;DR + **Tenure & engagement** + the **Patterns / hypotheses** section from the journal analysis."*

If the caller approves:

- **Before drafting the post, scrub PII.** UID prefix (first 8 chars) only, email **domain** only, never `givenName` / `familyName` / full email. The draft is going to Slack — once posted it can't be silently retracted. See `Compliance/Privacy` for the canonical rule.
- Show the draft in chat and require **explicit approval** before posting — ambiguous responses ("hmm" / "maybe") are not enough.
- Post the approved draft as a **threaded reply** by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with the message + `channel=<channel_id>` + `thread_ts=<message_ts>` (so it lands as a reply, not a new top-level message) + `--footer data:profile-user` (the helper stamps the versioned footer), and ask it for the permalink. That skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and unfurl-off — don't call Slack directly here.
- Don't hand-type a footer in the reply body — `shared:post-to-slack` stamps the versioned `Tools/Slack` footer (`_Generated by `data:profile-user:<version>` · <YYYY-MM-DD>_`) from `metadata.version` via `--footer data:profile-user` above. Spec on the `Tools/Slack` page (pre-fetched via `knowledge.reads`).
- **Default body — full_profile:** Headline + **Tenure & engagement volume** (section 2b, condensed to one line) + the **Feedback explanation** section (Stated <reason/answer> → What the data shows → Most plausible explanation). The full per-section report is too long for a thread reply; offer to paste it on request. **2b is not optional in the reply** — it is the one section telling a reader how much weight the feedback deserves, and it can't be reconstructed from the rest of the body. Condense to one line: both Tenure clauses, then the lifetime volume. **Copy the Tenure clauses verbatim from 2b**, which resolved them through [`references/engagement.md`](references/engagement.md) → "Tenure rendering" — never re-derive them and never substitute a bare date for a hedged clause; that file's wording is required on every surface it reaches, and the reply is one.
- **Default body — conversation_only:** a TL;DR line (one sentence — the dominant friction or pattern) + the **Tenure & engagement** line from the Step 4b preface + the **Patterns / hypotheses** bullets from the sibling's report. Skip the full friction-moment quotes (they bloat the thread); offer to paste on request.
- **Default body — journal_only:** same shape as conversation_only — TL;DR + the **Tenure & engagement** line from the Step 4c preface + the **Patterns / hypotheses** bullets. **That line is required on every route**, not just `full_profile`: it is one line, it is the only thing in the reply saying how much weight the rating carries, and it is already resolved in the report or preface — so no route justifies dropping it. Copy it verbatim rather than recomputing either tenure clause.
- After posting, confirm with the message permalink so the caller can verify it landed where expected.

**When Step 0.5 found a prior analysis and the caller chose "re-run":** the offer explicitly mentions the prior reply timestamp + permalink — *"A prior analysis was already posted on this thread at <ts> ([permalink](...)). Post this fresher one as a new reply, or skip the post?"* — so the caller can decide between posting a new reply or skipping the post even after running the full analysis fresh. Always post as a NEW reply in the thread; never edit the prior message.

If the caller declines or doesn't respond, do nothing — the report is already inline above. This step is purely opt-in.

Skip this step entirely for Mode B runs (no source thread to reply to) and for Mode A runs where parsing succeeded but the caller said up-front "don't reply, just analyse".

### Step 7 — Record the analysis in AI Knowledge → Learnings → Conversations (default on for a rated conversation)

Independent of whether Step 6 fired or was skipped, record the analysis as a row in `Learnings/Conversations` — **scoped to the rated conversation, not a user's backlog**. On the **`conversation_only`** route (a single rated conversation) `notion_record` defaults **`on`**: compose the `Compliance/Privacy`-masked row, write it via `shared:record-knowledge`, and surface the returned URL — no pre-write prompt. On the **`full_profile`** route it defaults **`off`** (see the route note below — we don't bulk-record a whole history). `notion_record: off` skips it; `notion_record: ask` shows the drafted row and waits for a yes before writing. The mechanics mirror `analyze-user-conversations` Step 8 — read its [Step 8 description](../analyze-user-conversations/SKILL.md) for the compose → write → confirm flow. (Because Step 3a/3b invoke that sibling with `record=false`, this Step 7 is the single recorder — no double write.)

Route-specific notes:

- **`route = "conversation_only"`** (QP / coaching / conversation rated): the sibling produced the analysis verbatim — pass it through to `record-knowledge` with the conversation ID. The row's `Rating`, `Source Type`, `Prompt Version`, and `Slack Thread URL` come from **`record_meta`**, assembled in Step 3b (from the Step 0 parse on a Mode A Slack-URL run, or from the caller on the `investigate-user-rating` sweep). `Rating` must be the Title-case enum (`Positive`/`Negative`/`Neutral`/`N/A`) — Title-case `record_meta.rating` / `context.category` before writing, never the lowercase polarity. `Source Type` is required — if neither `record_meta.source_type` nor a Step-0 shape is available, infer it from the conversation and never write the row without it. This is the typical path.
- **`route = "journal_only"`**: **not recorded to `Learnings/Conversations`** — `notion_record` defaults `off` here, and `Journal Rated` isn't a valid `Source Type` in that schema. A journal rating's analysis stays in the inline report (and the Slack reply if Step 6 ran); there's no journal row in the Conversations DB.
- **`route = "full_profile"`**: **`notion_record` defaults `off` here — do NOT bulk-record a user's conversation history.** We only want *rated* conversations in `Learnings/Conversations`, not every conversation a profiled user happens to have had. If the caller explicitly wants rows (`notion_record: on` or `ask`), record one row per analysed conversation (create/update, dedup on `conversation_id`) and report the count. The orchestrator's `Feedback explanation` section is never recorded — it's user-level synthesis, not conversation-level; it lives only in the inline report (and, when Step 6 ran, in the Slack reply).
- **Mode B (direct UID, full_profile route)**: same as `full_profile` above — **not recorded by default** (`notion_record` off; don't bulk-record an unrated backlog). Only if the caller explicitly sets `notion_record: on`/`ask` do rows get written, with `Source Type: Manual` and an empty `Slack Thread URL`.

Pre-check for existing rows on each `conversation_id` (Recipe A2 dedup) — record as a create or an update accordingly, and report the create/update counts.

On `conversation_only` recording is on by default; skip Step 7 when:

- The route is **`journal_only`** — journals aren't recorded to `Learnings/Conversations` at all (no valid `Source Type`; `notion_record` defaults `off`).
- The route is **`full_profile`** — `notion_record` defaults `off` there (we don't bulk-record a user's history). Record only if the caller explicitly sets `notion_record: on` / `ask`.
- `side_effects.notion_record=off` (an orchestrator owns persistence).
- The caller said `skip` / "don't record, just analyse" (equivalent to `notion_record=off` for this run).
- The sub-skill returned zero conversations (no Aurie history to record).

A declined Step 6 does **not** automatically skip Step 7 — they're independent decisions. The user may want the analysis preserved without posting it publicly, or vice versa.

### Step 8 — Offer to save the conversation to the Braintrust Feedback Conversation dataset

Independent of whether Steps 6 and 7 fired or were skipped, finish by offering to **save the conversation as a Braintrust eval case** in the `Feedback Conversation` dataset. Closes the loop the same way `analyze-user-conversations` Step 9 does: the Notion row (Step 7) is the durable institutional archive; the dataset row is the runnable check future prompter changes get evaluated against. Same opt-in discipline — explicit per-turn approval, never auto-fired.

The mechanics mirror `analyze-user-conversations` Step 9 — read its [Step 9 description](../analyze-user-conversations/SKILL.md#step-9--offer-to-save-the-conversation-to-a-braintrust-dataset) for the pre-check, ask, compose, and confirm sub-steps.

Route-specific notes:

- **`route = "conversation_only"`** (QP / coaching / conversation rated / chat message rated): this is the typical path. The sibling already has the conversation_id, observed_rating, Slack permalink, and a per-conversation analysis with a sharp **Patterns / hypotheses** section — all four are exactly what the sub-skill needs as `--build` flag defaults. Pass them through.
- **`route = "journal_only"`**: same as `conversation_only` — the sibling already has `journal_id`, observed_rating, Slack permalink. Pass through.
- **`route = "full_profile"`** (cancellation feedback): the user-pivot report typically contains 1–4 conversations. The dataset row needs one specific conversation. Ask the caller which one — surface a one-line headline per conversation and let them pick, or **skip** Step 8 entirely if no single conversation is load-bearing. Don't bulk-create dataset rows from a full_profile run — that dilutes the dataset signal.
- **Mode B (direct UID, full_profile route)**: same as above — ask which conversation to save, or skip. The dataset row's `metadata.source` is empty when no Slack URL is in scope; the sub-skill will prompt for a Braintrust UI URL or other anchor.

Skip Step 8 entirely when:

- `side_effects.braintrust_dataset=off` (an orchestrator owns persistence).
- The caller said up-front in the same turn "don't save to braintrust" / "skip the dataset" / equivalent.
- The sub-skill returned zero conversations.
- The route is `full_profile` and the caller doesn't want to single out one conversation.

A declined Step 6 or Step 7 does **not** automatically skip Step 8 — they're independent decisions. The dataset row is the most durable artifact of the three (it actively gates prompter changes for as long as the dataset is wired to evals), so the caller may want it even when they don't want a public reply or a Notion archive.

## Output format

The skill's primary return value is a single Markdown document whose shape depends on the route (`full_profile` section order and length bands; `conversation_only` / `journal_only` preface + verbatim sibling report + appended Sources entries). The per-route shapes, plus which side-effect step each route triggers, are in [`references/synthesis-sections.md`](references/synthesis-sections.md) → "Output format (per-route shapes)". The **Headline** (full-profile) or the sibling's existing `Headline` line (conversation/journal-only) is always a single line, so the caller can grab a TL;DR for Slack without re-reading the whole report.

## Edge cases

**Only one sub-skill returns data (full_profile route).** Continue with the partial picture. Note in the **Headline** that the profile is from one source only (e.g. *"behaviour-only — no Aurie conversations in window"*). The **Feedback explanation** section, if rendered, should explicitly say "no <missing-source> evidence available" rather than synthesising from one side.

**Both sub-skills return empty (full_profile route).** Check Step 3d's engagement response first — it is lifetime and unwindowed, so it can carry real tenure and volume for a user who was merely inactive over the sub-skills' windows. **A successful response means continue**, even if every count is zero: it still carries `tenure`, and "signed up `<tenure line>`, never engaged with Aurie" is a finding — with that line resolved through the tenure table in [`references/engagement.md`](references/engagement.md), stamps included. Flag the lifetime-only picture in the **Headline**. Only when the engagement call itself *failed*: stop, don't fabricate, and ask the user to confirm the UID (typo? wrong app? user signed up but never authenticated on a tracked surface?).

**Conversation ID returns no logs (conversation_only route).** Stop. Tell the user the conversation ID from the rating message can't be found in the OpenAI Conversation [Production] logs — the message may be older than the logs retention window, predate the 2026-05-12 `metadata.user` tagging rollout, or the rating may have been routed from a non-prod environment. Ask them to confirm before re-running.

**Stated feedback contradicts the data.** State the contradiction in **Feedback explanation** → **Most plausible explanation**. Don't smooth it over. The team needs to see when the stated reason or answer is a proxy for something else — that's exactly the value of running this skill versus reading the feedback string in isolation.

**Rating feedback is meta-commentary, not a literal complaint about Aurie's words.** Common on `*QUICK PROCESSING RATED*` messages: the user types feedback that reflects on themselves (e.g. *"I'm aware that I can do minimum"*) rather than describing what Aurie said. The conversation analysis will surface this if you let the steering work — Aurie may not have said the literal phrase the user objects to. This is a feature of the conversation-only flow: don't treat the rating-message feedback string as direct UX feedback without the transcript context.

**`90d` window passed (full_profile route only).** Map to `90d` for behaviour and `60d` for conversations. Surface in **Sources**. Don't silently downgrade. (Window doesn't apply to the conversation-only route — it's scoped by ID.)

**Re-running on the same input.** Idempotent across both routes. The sub-skills are themselves idempotent (no persistence) and this orchestrator inherits that — re-running just regenerates the report with potentially newer data. No state to corrupt.

**Cowork sandbox.** Sub-skills' env requirements (`OPENAI_CONVERSATION_API_KEY`, `OPENAI_JOURNAL_API_KEY`, `AURA_API_TOKEN`) are blocked from `mcp__workspace__bash` (PROD-1305). Either use the per-session paste-export workaround in [`/CLAUDE.md`](../../../../CLAUDE.md) → "Known Cowork constraints", or run from the host harness.

**Mode A: Slack URL points at a non-k8bot or differently-formatted message.** Stop. Surface what was found (e.g. "first line is `*Daily Status*` — not a k8bot rating header I recognise") and ask the caller to either (a) re-paste a k8bot rating URL of one of the six known shapes, or (b) fall back to Mode B by providing the UID directly. Do not guess UIDs from Slack user mentions — k8bot's `_User: …_` (cancellation-feedback) and `[user: …]` (rated) lines are the canonical Firebase UID; `@mentions` are typically the engineer who triaged, not the user.

**Mode A: k8bot shape header exists but the body is missing fields.** Treat as a parsing failure for *that shape only*. Tell the caller which fields couldn't be extracted (e.g. "found `*QUICK PROCESSING RATED*` header but no `[conversation: <UUID>]` line in the metadata footer") and ask whether to fall back to Mode B. Don't attempt to re-route to a different shape — the header is the source of truth for which shape to expect.

**Mode A: thread already contains a prior `profile-user` analysis.** Step 0.5 detects this and asks the caller how to proceed before any sub-skill runs (re-run / skip / show only) — no expensive Mixpanel / OpenAI / cost round-trips happen until the caller explicitly chooses re-run. When the caller picks "re-run", Step 6's post offer mentions the prior reply timestamp + permalink so the caller can decide between posting a fresh reply and skipping the post. New replies always post as new messages in the thread; the prior reply is never edited. Pass `skip_prior_check=true` to bypass Step 0.5 entirely (used by parent orchestrators that own the pre-check, or by callers deliberately refreshing).

## Out of scope (v1)

- **Auto-trigger on k8bot rating messages.** Hooking this skill to fire when k8bot posts in `#cancellation-feedback` or `#ai-conversation-ratings` is a v2 idea (parallel to the same v2 noted in `analyze-user-conversations`). v1 is on-demand only.
- **Slack reply persistence.** Step 6 (Slack reply) always requires explicit per-turn approval — no flag or override bypasses the confirm loop, since it's an outward post. `side_effects.slack_reply: off` suppresses the step entirely so a calling orchestrator can own the reply under its own approval.
- **Notion record defaults on for a rated conversation only.** On the `conversation_only` route Step 7 records the analysis to `Learnings/Conversations` by default — compose the masked row, write it, surface what landed (no separate yes). On `full_profile` it defaults `off`: we record *rated* conversations, never a profiled user's whole backlog. A caller sets `notion_record: off` to suppress or `notion_record: ask`/`on` to review or force a write.
- **Auto-saving to the Braintrust Feedback Conversation dataset.** Step 8 always requires explicit per-turn approval. The dataset is shared infrastructure that drives evals on prompter PRs — silently growing it on every run would erode the signal. No override bypasses the confirm loop, even when route is `conversation_only` and all three side-effect steps would otherwise be safe defaults.
- **File-system persistence.** Writing the report to a local file or Drive is not in scope.
- **Multi-user batch profiles.** One user (or one rated conversation) per invocation. For aggregate views, build a separate skill — the per-item report is too long to fan out responsibly.
- **Predictive scoring (LTV, churn risk, save-attempt success probability).** Profile + explanation only — no model inference. The report is descriptive, not prescriptive.
- **Time windows other than `30d` / `90d`.** If a caller needs `7d` or `last_conversation`, route them to the atomic sibling that supports it. Adding more windows here would multiply the mapping table without unlocking real synthesis value. (Conversation-only route is window-free by design — it's scoped by ID.)
- **Catalogue of k8bot message shapes in AI Knowledge.** The seven shapes are documented in the table above (full detail in [`references/input-shapes.md`](references/input-shapes.md)) per the self-contained-skills principle. A `Tools/Slack/k8bot` page that catalogues every k8bot message format would be useful eventually but is a separate concern.

## Key references

Sibling skills, endpoints, canonical Slack threads, and the full Linear-ticket history are collected in [`references/key-references.md`](references/key-references.md). The four sibling skills composed here: [`data:analyze-user-behavior`](../analyze-user-behavior/SKILL.md), [`data:analyze-user-conversations`](../analyze-user-conversations/SKILL.md), [`data:analyze-user-journal`](../analyze-user-journal/SKILL.md), and [`eng:report-user-costs`](../../../eng/skills/report-user-costs/SKILL.md); the two orchestrators that call it: `data:investigate-cancellation-feedback` and `data:investigate-pmf-survey`.
