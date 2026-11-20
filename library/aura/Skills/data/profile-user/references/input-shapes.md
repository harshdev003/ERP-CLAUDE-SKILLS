# k8bot input shapes — per-shape parsing detail

The full field-extraction rules for each of the seven k8bot Slack message shapes that `profile-user` Mode A recognises. `SKILL.md` carries the compact shape → route table; this file carries the verbatim per-shape specifics (fenced examples, which footer fields map to which flag, and the shape-specific edge notes).

### Shape 1 — `*New Cancellation Feedback*` → full profile flow

```
*New Cancellation Feedback*
*Reason:* <category>
*Feedback:*
```<free text>```
_User: <UID> | Subscription: <id> | Type: <type> | Product: <stripe_price_id> | Purchased: <date> | Provider: <provider>_
```

Extract:

- `User: <Firebase UID>` → `user_id`
- `Reason: <category>` → `context.category`
- `Feedback: <free text in fenced code block>` → `steering` and `context.stated`
- `Subscription` / `Type` / `Product` (Stripe price id) / `Purchased` / `Provider` → captured as additional subscription context (used by the **Feedback explanation** section even when not all fields are on the Mixpanel profile)

Sets `context.kind=cancellation` and routes to the **full picture** flow (Step 3a — all three sub-skills, Step 4 synthesis with a **Cancellation explanation** section).

### Shape 2 — `*QUICK PROCESSING RATED*` → conversation-only flow

```
*QUICK PROCESSING RATED*
:-1: Negative                                    (or :+1: Positive)

```<free text — fenced block, optional>```      (only present when the user typed feedback)

`[dialogue: <uuid>, conversation: <uuid>, user: <UID>, prompt:<name>:<version>]`
```

Extract from the metadata footer (single backticks, square brackets, comma-separated):

- `user: <UID>` → `user_id`
- `conversation: <UUID>` → `conversation_id` (required for the routing — passed to `analyze-user-conversations` via `--conversation-id`)
- `dialogue: <UUID>` → captured for provenance only
- `prompt:<name>:<version>` → captured for the report's Sources block (e.g. `quick-processing-prompter:0.235.1`)
- the `:+1:` / `:-1:` line → `rating_polarity` (`positive` or `negative`)
- the fenced feedback block (if present) → `rating_feedback`

Routes to the **conversation-only** flow (Step 3b — only `analyze-user-conversations` with conversation pivot, no synthesis).

### Shape 3 — `*COACHING RATED*` → conversation-only flow

Same metadata-footer shape as Shape 2; only the header differs (`*COACHING RATED*` instead of `*QUICK PROCESSING RATED*`). Same extraction, same routing. The `prompt:<name>:<version>` field will reference a coach-prompter version instead of `quick-processing-prompter`, which is useful context for the Sources block.

### Shape 4 — `*CONVERSATION RATED*` → conversation-only flow

```
*CONVERSATION RATED*
:neutral_face: Neutral                            (or :+1: Positive / :-1: Negative)

```<free text — fenced block>```                  (often the literal `No feedback` placeholder)

`[dialogue: <uuid>, conversation: <uuid>, user: <UID>, prompt:<name>:<version>]`
```

Same metadata-footer shape as Shapes 2 and 3. Two differences from Shape 3:

- **Polarity adds neutral.** Recognise three values: `:+1: Positive` / `:-1: Negative` / `:neutral_face: Neutral`. Map to `rating_polarity ∈ {positive, negative, neutral}`.
- **`No feedback` placeholder.** When the user didn't type anything, k8bot fills the fenced block with the literal string `No feedback` rather than omitting it. Treat any of `No feedback`, empty, or whitespace-only contents as no steering — pass `steering=""` to the sibling skill so it produces a generic friction + positives report rather than echoing the placeholder back as a steering hint.

The `prompt:<name>:<version>` field is typically `coach-prompter:<version>` — overlapping with Shape 3. Treat `*CONVERSATION RATED*` as the broader-emoji-set sibling of `*COACHING RATED*` (the team appears to be migrating coaching ratings to this header to capture neutral as a third polarity); don't try to disambiguate. Same extraction, same routing.

### Shape 5 — `*CHAT MESSAGE RATED*` → conversation-only flow

```
*CHAT MESSAGE RATED*
:-1: Negative                                    (or :+1: Positive)

```<free text — fenced block, optional>```      (only present when the user typed feedback)

`[dialogue: <uuid>, conversation: <uuid>, user: <UID>, prompt:<name>:<version>]`
```

Same metadata-footer shape as Shapes 2, 3, and 4. Same extraction, same conversation-pivot routing. Two notes:

- **Prompter is typically `guardrails-handler-prompter:<version>`.** This header appears to fire when the rated Aurie reply came from the **guardrails handler layer** rather than the main chat / quick-processing / coaching prompters — i.e. a reply produced when the model routed the turn through `handoffToGuardrails` (safety / crisis / out-of-scope deflection). The prompter version on the footer is the guardrails handler revision, useful context for the Sources block since the friction analysis often centres on *how* the deflection was phrased (templated vs. mirrored, single-shot vs. sticky across turns).
- **Polarity is binary** (`:+1: Positive` / `:-1: Negative`) — no `:neutral_face: Neutral` observed on this header to date. Treat the same as Shapes 2/3 polarity-wise.

### Shape 6 — `*JOURNAL RATED*` → journal-only flow

```
*JOURNAL RATED*
👎                                               (or 👍)

`<journal entry text>`                           (fenced block — the user's journal
                                                  text / day's conversation excerpt
                                                  that fed into the journal-prompter)

[id: <journal-entry-uuid>, user: <Firebase UID>]
```

Extract:

- `id: <UUID>` → `journal_id` (the legacy Braintrust `journal` span ID — it has **no OpenAI equivalent** post-migration; `analyze-user-journal` now routes by `user_id` + `journal_text`, not by this id, so it's captured for provenance only)
- `user: <UID>` → `user_id`
- the fenced block content → `journal_text` (the day's conversation excerpt — passed to `analyze-user-journal` as context, NOT as `steering`)
- the emoji on the second non-blank line → `rating_polarity`:
  - 👎 (`:thumbsdown:` / `:-1:`) → `negative`
  - 👍 (`:thumbsup:` / `:+1:`) → `positive`
  - Match the literal emoji character; do not expect a text suffix like "Positive" or "Negative".

**Key difference from Shapes 2–5:** the footer uses `id:` (not `conversation:`), and there is no `dialogue:` or `prompt:` field. The fenced block is the user's own journal entry text, not a rating comment about the AI output. Do not pass it as `steering` — pass it as `journal_text` to `analyze-user-journal`.

Routes to the **journal-only** flow (Step 3c — only `analyze-user-journal`, no behavioural sub-skill, no synthesis layer).

### Shape 7 — `*PROFILE RATED*` → full-profile flow (profile pivot)

```
*PROFILE RATED*
:thumbsup:                                       (or :thumbsdown:)

```<free text — fenced block>```                  (often the literal `No feedback` placeholder)

`[id: <rendererRating-row-uuid>, user: <Firebase UID>]`
```

When a single card is rated rather than the whole profile, the footer gains a `renderer:` field: `[id: <uuid>, user: <UID>, renderer: <prompter-name-key>]` (e.g. `renderer: core-quote-renderer`).

Extract from the metadata footer (single backticks, square brackets, comma-separated):

- `user: <UID>` → `user_id`
- `id: <UUID>` → `rating_id` (the `rendererRating` row id — captured for provenance / the Sources block)
- `renderer: <key>` (optional) → `renderer_key` — the single renderer that was rated; absent means the whole profile
- the emoji line → `rating_polarity`. **All three polarities occur**: the service validates a
  rating as `1` / `0` / `-1` and maps `0` to `:neutral_face:`, so a neutral profile rating is
  real, not a parse error
- the fenced feedback block (if present, and not the `No feedback` placeholder) → `steering` and `context.stated`

**Key difference from Shapes 2–5:** the rating is about the user's **generated profile** (the compiled insights + card renderers on the profile screen), not about a conversation or a single AI reply. So it routes to the **full-profile** flow (Step 3a — behavioural + conversational + cost sub-skills, Step 4 synthesis) with `context.kind=rating`, not the conversation-only flow.

**This shape adds the `insights` source.** Pass `rating_id` to `eng:build-aurie-user-insights` `mode=read` so the analysis sees the rated artifact itself — the exact snapshot, not a fresh compile. Without it the run can only describe the user's behaviour, never what they actually reacted to; and with the `No feedback` placeholder (the common case) that leaves no evidence at all. See SKILL.md Step 3a. When `renderer_key` is present, fold it into the steering so the synthesis focuses on that card (e.g. "user rated the `core-quote-renderer` card negatively — <feedback>"). The fenced block, when non-placeholder, is genuine feedback about the profile — pass it as `steering` (unlike `*JOURNAL RATED*`, where the block is the user's own entry text).

### Unrecognised k8bot shape

If the message starts with `*` followed by capitalised text (looks like a k8bot status header) but doesn't match any of the seven shapes above — *don't guess*. Surface to the caller: "Got a k8bot-style message but the header `*<header>*` isn't one of the seven I know how to route. Want me to fall back to Mode B (give me a UID) or treat this as a generic conversation analysis (give me a `conversation_id`)?" New k8bot shapes are added upstream over time; flagging them lets us update this skill rather than misroute.

If the message isn't a k8bot rating at all (no recognisable header, no metadata footer), tell the user what shape was found and ask them to either re-paste or fall back to Mode B.
