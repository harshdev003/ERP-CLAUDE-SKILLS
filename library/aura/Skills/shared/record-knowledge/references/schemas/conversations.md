# Schema — `Learnings/Conversations`

**Status:** stable. Introduced in [PROD-1701](https://linear.app/aura-health/issue/PROD-1701) — recorded by `data:analyze-user-conversations` and `data:profile-user` at the end of a run. Recording is **on by default for a single rated conversation** (a conversation-pivot / the `conversation_only` route); a **user-pivot / `full_profile`** run does **not** bulk-record a user's history. A caller passes `record: false` / `notion_record: off` to skip, or `record: ask` / `notion_record: ask` to review the row before it's written.

Rows are write-once by default. Each row distills one analysed Aurie conversation: friction moments, what worked, hypotheses to validate, recommended follow-up. The unique identity of a row is the Braintrust `conversation_id`. Re-running the analysis on the same conversation should either skip (idempotent) or be a deliberate update by the caller — see "Idempotency" below.

**Which conversation path?** Five AI Knowledge paths hold conversation- or feedback-shaped entries and they are **not** interchangeable — each has a different grain, and the same fact belongs in exactly one. Route by grain per [`Guide/Placement Rules` → *Conversation & feedback entries: route by grain*](https://www.notion.so/35a75e23af20812ca987c2bcedf27dd7). This path takes a **human's judgement about one conversation** — friction moments, what worked, hypotheses to validate, recommended follow-up.

**Who writes rows.** The unattended rating cadences (`data:run-feedback-analysis-cadence`,
`data:run-rating-feedback-analysis-cadence`) record every **negative and neutral**
conversation-rated entry they review, distilled from the analysis they post in Slack — one row
per conversation, updated in place on a re-rating. Positive ratings, journal and profile ratings
are not recorded by the cadences. Interactive deep dives (`data:analyze-user-conversations`,
`data:profile-user`) write richer rows for the conversations someone looked at by hand, and
`data:backfill-learnings-conversations` fills the rated conversations that have no row: history
from before the cadences recorded, and any cadence write that failed. The table is the corpus
the Conversations loop (`product:digest-conversation-signals`) improves the prompts from. The
automated per-rating stream with every polarity is `Signals/Conversation Patterns`.

## Where it lives

- **Notion path:** `AI Knowledge → Learnings → Conversations`
- **Database URL:** <https://www.notion.so/977a74fcb9c24057af622ddab7bdd663>
- **Data source ID:** `2895c7d1-a921-49ac-ad16-91b3ac70951f`

## Properties

All [base-schema](../base-schema.md) fields apply. In addition:

| Field | Type | Required | Notes |
|---|---|---|---|
| `Title` | title | yes | Short headline — e.g. `tWL0ZSuz… — Neutral rating after 35m affair conversation`. Lead with the UID/conversation prefix + dominant signal. |
| `Conversation ID` | text | yes | Braintrust conversation UUID. **Dedup key** — case-insensitive uniqueness check before writing. |
| `User ID` | text | yes | Firebase UID (**full string** — an accepted exception to the `Compliance/Privacy` UID-truncation recipe; see that page's "Accepted exceptions to the Agent rule"). Display prefixes in the page body (e.g. `tWL0ZSuz…`) — never the full UID in narrative copy. |
| `Conversation Date` | date | no | When the conversation actually happened (from Braintrust span timestamps). Defaults to blank if Braintrust didn't return a `created` timestamp. |
| `Rating` | select | yes | `Positive` / `Negative` / `Neutral` / `N/A`. `N/A` when no rating span exists (Mode B without rating, manual analysis). |
| `Source Type` | select | yes | What surfaced the conversation for analysis: `Cancellation Feedback`, `Quick Processing Rated`, `Coaching Rated`, `Conversation Rated`, `Chat Message Rated`, `Manual`. |
| `Conversation Type` | select | no | `Quick Processing` / `Coaching` / `Other`. Inferred from `Source Type` when possible (`Quick Processing Rated` → `Quick Processing`; `Coaching Rated` / `Conversation Rated` → `Coaching`; cancellation/manual → `Other`). `Chat Message Rated` is not auto-inferred — a guardrails-handler reply can fire inside either a Quick Processing or Coaching conversation, so infer from the conversation itself. |
| `Prompt Version` | text | no | Captured from k8bot footer (`prompt:coach-prompter:0.235.4`). Blank when not available (Mode B). |
| `Vertical` | select | no | Canonical vertical from `Identity/Audience` — `grief`, `anxiety`, `sleep`, `relationships`, `loneliness`, `stress`, `depression`, `self_worth`, `purpose`, `anger`, `trauma`, or `unknown`. Inferred from user profile + conversation content. |
| `Themes` | multi_select | no | Topic tags. Seeded set: `affair`, `abandonment`, `grief`, `anxiety`, `self_blame`, `self_worth`, `onboarding_friction`, `somatic_anchoring`, `question_saturation`, `reframe_misfire`, `closure_misfire`, `first_conversation`, `recommendation_misfire`. Extend as new themes recur — add the option in Notion directly (no schema change needed). |
| `Day in Trial` | number | no | Days since signup, when known (`0` = day of signup). Pulled from Aura API user profile + conversation timestamp. |
| `Slack Thread URL` | url | no | Source Slack thread the analysis was triggered from (Mode A only). |
| `Steering Feedback` | text | no | Typed cancellation reason / k8bot feedback text used to steer the analysis. Blank when the placeholder `No feedback` was the only content. |

## Page body

Mirrors the structure of the Slack draft reply produced by `analyze-user-conversations` Step 7, with two additional sections for the longer-form follow-ups:

```markdown
## Headline
<one-line summary — UID/conversation prefix + dominant signal>

## What worked
- <bullet 1>
- <bullet 2>

## What didn't
- <bullet 1>
- <bullet 2>

## Hypotheses to validate
- <falsifiable hypothesis 1>
- <falsifiable hypothesis 2>

## Recommended follow-up
- <concrete next step 1>
- <concrete next step 2>

# Sources
- [Braintrust — conversation logs](<URL>) (primary)
- [Slack — source thread](<URL>)   (Mode A only)
- [Aura API — user profile](<URL>) (or "unavailable")
- [Mixpanel — behavioural context](<URL>) (or "unavailable")
```

The synthesis sections map 1:1 to the analyse-user-conversations report (Headline / Friction → "What didn't" / Positives → "What worked" / Patterns → "Hypotheses to validate" / Follow-up → "Recommended follow-up"). The skill compresses the long-form report into bullets when recording — full per-conversation friction quotes stay out of Notion to keep rows scannable.

## Example row

```yaml
Title: "tWL0ZSuz… — Neutral after 35m affair conversation"
Conversation ID: "3F0CA47A-A4C1-40D7-BED1-40CEAF59717B"
User ID: "tWL0ZSuzArUoLTK3LuKnPhOmr2B3"
Conversation Date: 2026-05-08
Rating: Neutral
Source Type: Conversation Rated
Conversation Type: Coaching
Prompt Version: "coach-prompter:0.235.4"
Vertical: relationships
Themes: [affair, abandonment, question_saturation, closure_misfire, first_conversation]
Day in Trial: 0
Slack Thread URL: "https://auramobile.slack.com/archives/C07MB0TMF5J/p1778525408643079"
Steering Feedback: ""

# Body
## Headline
tWL0ZSuz… — brand-new user (sign-up → trial → first chat all within 4 min) went 35 min deep on an affair / abandonment topic, then rated Neutral with no feedback after a 35s deliberation. In-session care looks high-quality; the rating most plausibly reflects exit shape, not content.

## What worked
- Somatic anchoring early (hand on chest, slow breath) — user reports "a tad bit lighter" by exchange 4
- Sharp validating reframes — "who is fighting for you right now?" lands the emotional core
- Closes somatic (hand on heart, breath, "I'm here") — clean grounding ritual

## What didn't
- ~30 of 35 Aurie turns end with a question — for a new user in acute grief that reads as excavation
- Direct reframes on the affair ("competing with a structure that already existed") are honest but may land as judgment from a brand-new chatbot
- User signals closure ("Thank you") and Aurie returns warmth but no forward handle — no takeaway, no tool, no invitation to return

## Hypotheses to validate
- Question-saturation correlates with Neutral ratings on coach-prompter — falsifiable on other rated logs
- First-conversation-of-trial users need a "we did something" artefact at exit, even more than returning users
- Aurie introducing diagnostic vocabulary the user didn't use first (e.g. "predatory") can tip support → clinical

## Recommended follow-up
- Add a Braintrust eval case for question-saturation on first-conversation rated logs
- Prototype a "we did something" exit artefact for first-trial users (takeaway card / breathwork loop / scheduled check-in)
- Review the coach-prompter system message for diagnostic vocabulary that escalates emotional register

# Sources
- [Braintrust — Conversation 3F0CA47A-A4C1…](https://www.braintrust.dev/app/Aura%20Health%201/p/Conversation/logs?filter=metadata.conversationId%3D%273F0CA47A-A4C1-40D7-BED1-40CEAF59717B%27) (primary)
- [Slack — source thread](https://auramobile.slack.com/archives/C07MB0TMF5J/p1778525408643079)
- Aura API user profile — unavailable
- Mixpanel — skipped (focused conversation-pivot run)
```

## Validation rules

1. **Required:** `Title`, `Conversation ID`, `User ID`, `Rating`, `Source Type`.
2. **`Conversation ID` is the dedup key.** Case-insensitive uniqueness check before any create. If a row with the same `Conversation ID` exists, `record-knowledge` refuses the write with a pointer to the existing row URL — caller decides whether to update or skip.
3. **`Rating` enum:** must be one of `Positive` / `Negative` / `Neutral` / `N/A` (hard error otherwise).
4. **`Source Type` enum:** must be one of the six canonical values (hard error otherwise).
5. **`Vertical` enum:** warn (not error) if outside the canonical Audience list — list is source of truth and may drift.
6. **`Themes`:** unknown options are added to the Notion DB on the fly (no validation error). Keeps the taxonomy free to grow without schema PRs.
7. **`Slack Thread URL`** when set must be a Slack permalink (`https://*.slack.com/archives/...`); warn on any other host.
8. **`sources` (in page body):** at minimum the Braintrust logs URL. Slack thread URL is required if `Source Type ≠ Manual`. Other sources (Aura API / Mixpanel) annotated as "unavailable" when skipped — never silently omit.

## Idempotency

- **Conversation ID is the dedup key.** Before any create, `record-knowledge` looks up existing rows with the same `Conversation ID`. If found, the write is refused; the caller is told the existing row URL.
- **Re-analysis on the same conversation.** The expected response is to invoke `record-knowledge` with `action: update` and `page_id: <existing row>`. Updates follow the standard `Learnings/*` rules: property merge, named-section body replace, sources append-only. See "Update semantics" below.

## Update semantics

`Learnings/Conversations` rows accept `action: update` from `analyze-user-conversations` and `profile-user` when a conversation is re-analysed (e.g. after a new prompt version ships, after the user follows up). The allowed body sections for `body_replace_sections`:

- `## Headline`
- `## What worked`
- `## What didn't`
- `## Hypotheses to validate`
- `## Recommended follow-up`

`# Sources` is append-only (per base-schema). `Conversation ID` and `User ID` are never updatable — they identify the row.

When a re-analysis arrives with a newer `Prompt Version`, also update the `Prompt Version` property (the rating context shifted, so the property should track the most recent analysis). Same for `Rating` if the rating changed.

## Open questions

- **"Top 10 Learnings — Conversations"** synthesis on the Learnings landing page — out of scope until the DB has 15+ rows. Track as a follow-up.
- **Conversation-to-experiment links** — should `Themes: reframe_misfire` rows be linkable to a future experiment row testing a fix? Add a `Related Experiments` relation property in a v2 if the use case emerges.
- **PII shape in `Steering Feedback`** — cancellation feedback occasionally contains the user's name or email. The current rule is to record verbatim (Notion is internal and already access-controlled). Revisit if a healthcheck flags it.
