# Schema — `Signals/Conversation Patterns`

**Kind:** `table` — an observational database, **one write-once row per rating event**, dedup on `Rating ID`. Rows are appended as the rating stream flows; window rollups (weekly / monthly) are computed at read time by `data:report-conversation-patterns`. Because rows are never updated (create-only, dedup-skip on re-run), it satisfies the Signals observational-layer contract.

**The grain is the rating, not the conversation.** A user can rate the same thread more than once — `chat` ratings in particular fire per message — so the rated object's id is *not* unique per rating, and deduping on it collapses those repeats into a single row. `Rating ID` is unique per rating *event*, which is what makes it the key. Measured cost of keying on the conversation instead, over one 7-day window: 43 repeated ids cost 53 rows, and `chat` alone lost ~21% of its ratings (141 rated → 112 rows), biasing every per-surface volume and polarity comparison against chat by exactly the ratings it dropped ([ENG-1115](https://linear.app/aura-health/issue/ENG-1115)).

**Status:** stable. Produced by [`data:harvest-conversation-patterns`](../../../../../data/skills/harvest-conversation-patterns/SKILL.md); consumed by [`data:report-conversation-patterns`](../../../../../data/skills/report-conversation-patterns/SKILL.md).

**Which conversation path?** Five AI Knowledge paths hold conversation- or feedback-shaped entries and they are **not** interchangeable — each has a different grain, and the same fact belongs in exactly one. Route by grain per [`Guide/Placement Rules` → *Conversation & feedback entries: route by grain*](https://www.notion.so/35a75e23af20812ca987c2bcedf27dd7). This path takes one **rating event**, as the rating stream produced it.

## Where it lives

- **Notion path:** `AI Knowledge → Signals → Conversation Patterns`
- **Database URL:** <https://app.notion.com/p/8315a912e4c94f82a246a43b1d3cba4f>
- **Data source ID:** `86ba6f79-e9dc-4ffb-9f05-2bd991b2ae8f`

## Producer & consumer

- **Producer:** `data:harvest-conversation-patterns` — sweeps ratings (all polarities) from Slack `#ai-data-intelligence` (C07MB0TMF5J), one row per rating.
- **Consumer:** `data:report-conversation-patterns` — queries a `Date` window and synthesises the narrative report.

## Properties

All [base-schema](../base-schema.md) fields apply. In addition:

| Field | Type | Required | Notes |
|---|---|---|---|
| `Signal` | title | yes | One-line summary of the conversation's pattern/signal, ≤120 chars. Human-readable row label. |
| `Rating ID` | rich_text | yes | **Dedup key** — the Slack `ts` of the k8bot `*[TYPE] RATED*` message this row came from, verbatim (e.g. `1783937040.769999`). One rating event, one ts, so it is unique per row where the rated object's id is not. Slack guarantees uniqueness per channel and the producer sweeps a single channel (`#ai-data-intelligence`), so the bare ts is the whole key. It is also recoverable from `Slack URL` — the permalink's `/p<digits>` is the same ts with the dot removed — which is the only way to re-derive the key for a row that has one written. Exact-match uniqueness check before create; a match → skip (write-once, never re-created or updated). |
| `Conversation ID` | rich_text | no | The id of whatever was rated, not always a conversation. Conversation shapes (`Conversation` / `Chat` / `Quick Processing` / `Coaching`) use the OpenAI/Aurie conversation id; `Journal` uses the journal entry id; `Profile` uses the `rendererRating` row id. All are UUIDs from different systems, so they can't collide. **Not unique and not a dedup key** — a thread rated N times has N rows sharing one `Conversation ID`, which is what makes it the right column for grouping a thread's ratings. Optional because it is a grouping column, not the key: every k8bot shape carries an id, so a blank one means the footer changed upstream. Warn on it, but write the row — the rating is the fact, and dropping a rating to enforce a grouping column is the loss this schema exists to avoid. |
| `Date` | date | yes | Date of the conversation / rating. Drives weekly & monthly rollups. |
| `Rating` | select | yes | `Positive` / `Neutral` / `Negative`. The user's rating polarity. **Not comparable across `Session Type`** — `Chat` and `Journal` are rated on a two-option widget with no neutral, so never pool them with the three-option surfaces into one polarity rate. See "`Rating` is not comparable across `Session Type`" below. |
| `Session Type` | select | yes | `Quick Processing` / `Coaching` / `Chat` / `Journal` / `Conversation` / `Profile`. |
| `Themes` | multi_select | no | Zero or more tags. Seeded with the technique/UX taxonomy (`voice_interruption`, `reflection_depth`, `session_length`, `therapeutic_honesty`, `somatic_efficacy`, `multi_session_arc`, `distress_containment`, `pacing_mismatch`, `coping_tools_gap`, `realism`, `ceiling_effect`, `rating_prompt_timing`, `journal_insight_framing`) **plus** topical/marketing tags for the positive stream (`relationship_attachment`, `boundaries`, `betrayal_recovery`, `self_trust`, `identity_acceptance`, `grief_loss`, `parenting_family`, `faith`, `somatic_relief`, `breakthrough_moment`). Unknown options are added on the fly — keep this list in sync with the skill. |
| `uid_prefix` | rich_text | no | A short non-identifying prefix — **8 characters taken from after the provider prefix**, with the provider kept as a readable label (`google:11248769`), so provider-prefixed ids exceed 8 total characters. Bare Firebase-28 ids (no colon) are the first 8 characters. Never a full UID. Derived by the writer, not by hand — see the canonical `uid_prefix()` in [`data:report-conversation-patterns`](../../../../../data/skills/report-conversation-patterns/scripts/sample.py). |
| `Feedback` | rich_text | no | The user's rating feedback, verbatim-short or paraphrased. Never contains a name/email. |
| `Renderer` | select | no | **`Profile` rows only.** The rated card — `core-quote-renderer`, `inner-tensions-renderer`, `tending-renderer`, … — or `whole-profile` when the rating targeted the entire profile rather than one card. Unset on every other shape. Options are added on the fly as new renderers ship. |
| `Prompt Version` | rich_text | no | Prompter version for the conversation, when known. |
| `Slack URL` | url | no | The `#ai-data-intelligence` rating thread this row came from. Effectively always set — it carries the `Rating ID` ts in its `/p<digits>` segment, so a row without it can't be re-derived. |

**Builder note.** `Themes` (multi_select), `Rating`/`Session Type` (select), and `url` build inline where the `make_*` helpers don't cover them: `{"multi_select": [{"name": t} ...]}`, `{"select": {"name": v}}`, `{"url": u}`.

## `Rating` is not comparable across `Session Type`

**Canonical for the repo.** Two `Session Type` values are rated on a **two-option** widget with no neutral; the rest are rated on a **three-option** widget. `Rating` therefore measures a different scale per row, and pooling the two into one polarity rate is invalid — a blended "% positive" or "% neutral" over mixed session types is an artifact of which surfaces can express a neutral at all, not a quality signal. Established in [ENG-1118](https://linear.app/aura-health/issue/ENG-1118).

| `Session Type` | k8bot header | Rate endpoint | Widget | Options presented | Sent |
|---|---|---|---|---|---|
| `Conversation` | `*CONVERSATION RATED*` | `POST /ai/conversation/:id/rate` | `ConversationFeedbackView` (emoji faces) | GREAT · OKAY · NOT HELPFUL | `1` / `0` / `-1` |
| `Quick Processing` | `*QUICK PROCESSING RATED*` | same endpoint (server picks the header from the prompter name) | same | GREAT · OKAY · NOT HELPFUL | `1` / `0` / `-1` |
| `Profile` | `*PROFILE RATED*` | `POST /ai/insight/renderers/rate` | same view, `labelOverrides` | GREAT · OKAY · NOT RIGHT | `1` / `0` / `-1` |
| `Chat` | `*CHAT MESSAGE RATED*` | `POST /ai/dialogue/:id/rate` | `AIRatingButtons` (iOS thumbs) · AIWeb `useFeedback` (`like`/`dislike`) | 👍 · 👎 | `1` / `-1` |
| `Journal` | `*JOURNAL RATED*` | `POST /ai/journal/:id/rate` | `JournalFeedbackButtons` (thumbs) | 👍 · 👎 | `1` / `-1` |
| `Coaching` | `*COACHING RATED*` | — | — | no traffic in the last 60 days | — |

**The constraint is client-side only.** Intelligence Service validates every one of these endpoints through the same `isValidRating` (`-1` / `0` / `1`) and renders the emoji through the same `ratingToEmoji`, so a neutral is accepted and posted correctly on *every* surface. Nothing server-side needs changing to widen a widget, and nothing server-side explains the gap — so don't read a missing neutral as a parser or backend fault.

**Two rules for any reader of this table:**

1. **Split by scale before quoting a rate.** Report the three-option group (`Conversation` / `Quick Processing` / `Profile`) separately from the two-option group (`Chat` / `Journal`). A per-`Session Type` breakdown satisfies this; a single pooled percentage does not.
2. **A missing `Neutral` on `Chat` / `Journal` is the widget, not the users.** Their positive share is mechanically higher than a three-option surface's because the users who would have picked OKAY had to pick 👍 or 👎.

**Observed distribution** (60 days to 2026-08-10, 4,614 rows, no internal-account filter) — the shape the rules above are calibrated on, not a target to compare against:

| `Session Type` | n | Positive | Neutral | Negative |
|---|---|---|---|---|
| `Conversation` | 2,081 | 1,337 | 624 | 120 |
| `Chat` | 1,375 | 1,285 | 8 | 82 |
| `Journal` | 698 | 694 | 0 | 4 |
| `Quick Processing` | 327 | 118 | 177 | 32 |
| `Profile` | 133 | 62 | 55 | 16 |

**Those `n` values are floors, not counts** — the window predates 2026-08-10, so see **Coverage** below. The under-count is not spread evenly: it scales with how often one thread gets rated more than once, which is worst on the per-message surfaces. `Chat` is the most understated row in the table, and it is also the row the two-option rule is calibrated on. The *ratio* columns are far less affected than `n` is, since a thread's repeat ratings are not systematically one polarity — so read the shape, not the volume, until the window is re-harvested.

Two entries in that table are load-bearing and easy to misread:

- **`Journal` is neutral-less, not positive-only.** Its widget offers 👎 and 4 users used it. A shorter window that happens to contain zero of them (the 7-day window in ENG-1118 showed 68 positive, 0 negative) invites the conclusion that journal cannot capture a negative at all, and it can. Don't drop `Journal` from a negative-rating sweep on that basis.
- **`Chat`'s 8 neutrals are all one test account** (`role: admin`, `test: true`) calling the endpoint directly — one `dialogue` id rated three times in eight seconds, every one with no feedback. From the widget, `Chat` produces exactly zero neutrals, so a run with the internal-account filter on will show `0`. A neutral appearing here from a **non**-internal UID means a client started sending `0`, which is worth chasing.

## Page body (optional, light)

Rows are primarily property-carried. Body may hold a short excerpt for context:

```markdown
## Signal
<one-paragraph description of the pattern in this conversation>

## Excerpt
<2–4 line paraphrased excerpt — PII-safe, no names/locations>

# Sources
- [Slack #ai-data-intelligence — rating thread](<thread URL>) (primary)
```

## Validation rules

1. **Required:** `Signal`, `Rating ID`, `Date`, `Rating`, `Session Type`. (`Conversation ID` is expected on every row but not enforced — see its note above.)
2. **Dedup on `Rating ID`** (exact match — a Slack ts has no case) before any create. A match → **skip**, returning the standard Recipe A2 dedup payload `{ "error": "duplicate", "existing_url": "...", "existing_id": "..." }` (the caller treats it as an idempotent skip, not a failure — the row already exists). Rows are **write-once**: never updated, never re-created. This is what keeps the table a valid Signal. **Never dedup on `Conversation ID`** — a repeated `Conversation ID` is expected data, not a duplicate.
3. **`Rating` enum:** `Positive` / `Neutral` / `Negative` (hard error otherwise).
4. **`Session Type` enum:** the six shapes above (warn, not error — shapes may grow).
5. **`Themes`:** unknown options added on the fly (no validation error).
6. **PII:** `uid_prefix` is a prefix, never a full UID — 8 characters after the provider prefix, so a provider-prefixed value is longer than 8 total characters (`google:11248769`) and a bare Firebase-28 id is exactly 8. A length check is **not** the validation; the check is that the value came from `uid_prefix()`. `Feedback`/body carry no full UID, email, name, or location.

## Idempotency

Pure append. Each harvest run creates rows only for ratings whose `Rating ID` isn't already present. Re-running the same window is a no-op (all dedup-skip). No updates, no upsert, no `Occurrences`/`Last Seen` bookkeeping — the row is a fact about one rating at one time, and window rollups are computed at read time by the report skill.

**On `record-knowledge`.** Although `Signals/Conversation Patterns` is a `table`-kind path (so the generic write-once guard doesn't refuse it outright), this schema's own idempotency rule is stricter: `record-knowledge` only ever `create`s a row here (with the `Rating ID` dedup-skip). It never issues `action: update` against this path. The per-rating row is immutable once written.

## Coverage

**Rows are complete per rating from 2026-08-10 onward. Before that date, per-surface volume in this table is a floor, not a count** — `chat` most of all, since a per-message rating surface loses the most to a per-thread key. Any comparison spanning that boundary has to say so.

**Filling the gap is a harvest, not a migration.** `data:harvest-conversation-patterns` over a past window writes the rows that window is missing and dedup-skips the ones already present, so the recoverable range is whatever Slack still retains on `#ai-data-intelligence`. There is no separate backfill mechanism and none is needed.

**Every row's `Rating ID` is derivable from its `Slack URL`**, which is what makes the key recoverable for a row that already exists (`/p1783937040769999` → `1783937040.769999`). Deriving it and writing it onto existing rows is the **only** sanctioned write to a row in this table; it is an operator migration, never something the producer does. Two rules follow, and the second is the load-bearing one:

- **A new required column must be added to the live data source before the producer starts writing it** — a create naming a property the table lacks is a 400.
- **Existing rows must carry the new key before dedup switches to it.** A key no existing row has matches nothing, so the next overlapping harvest re-creates a duplicate of every row already present. Changing the key without backfilling it is not an incomplete migration; it is a destructive one.
