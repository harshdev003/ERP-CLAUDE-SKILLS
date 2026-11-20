# Record the rated conversation to `Learnings/Conversations`

`Learnings/Conversations` is the corpus the prompts are improved from: the Conversations
loop's digest (`product:digest-conversation-signals`) reads its rows and turns recurring
defects into prompt candidates. A rating the sweep analyses and replies to, but never
records, is invisible to that loop. So every eligible entry gets a row, written from the
analysis this tick already produced. No second analysis is run for it.

The recording step runs **after** the reply has posted, and it never changes the reply:
the reply is the dedup watermark, so a failed write leaves the entry answered and
unrecorded, never the other way round.

## Which entries get a row

Record an entry when **all** hold:

- **Polarity is negative or neutral.** Positives stay Slack-only.
- **Shape is one of the four conversation-rated shapes:** `qp`, `coaching`, `conversation`,
  `chat`. Journal and profile ratings are never recorded here (`Source Type` has no value
  for either; the rated object is not a conversation).
- **The reply posted was the normal review.** Never record the content-safety placeholder
  (the withheld entry was deliberately not analysed) or the error reply (there was nothing
  to analyse).
- **The entry has a `conversation_id`.**

## Pre-check: create or update

Query the Conversations data source (`2895c7d1-a921-49ac-ad16-91b3ac70951f`) for a row whose
`Conversation ID` matches, case-insensitively.

- **No row** → `action: create`.
- **Row exists** → `action: update`: replace the five body sections with this tick's
  analysis, and update `Rating` / `Prompt Version` if they changed. A re-rated conversation
  updates its row; it never mints a second one.

## Row fields

| Field | Value |
|---|---|
| `Title` | `<8-char UID prefix>… — <the reply's one-line headline>` |
| `Conversation ID` | the entry's `conversation_id` |
| `User ID` | the full Firebase UID (the schema's accepted exception; never in body copy) |
| `Conversation Date` | the transcript's latest completion date, else the rating date |
| `Rating` | `Negative` / `Neutral` (Title-case, never the lowercase polarity) |
| `Source Type` | `qp` → `Quick Processing Rated` · `coaching` → `Coaching Rated` · `conversation` → `Conversation Rated` · `chat` → `Chat Message Rated` |
| `Conversation Type` | `Quick Processing Rated` → `Quick Processing`; `Coaching Rated` / `Conversation Rated` → `Coaching`; `Chat Message Rated` → read it off the transcript's prompter (`quick-processing*` → `Quick Processing`, otherwise `Coaching`) |
| `Prompt Version` | the k8bot footer value, full `<prompter>:<version>` form |
| `Vertical` | the canonical `Identity/Audience` value the analysis supports, else `unknown` |
| `Themes` | the recurring-defect tags the analysis supports (reuse existing options such as `closure_misfire`, `question_saturation`, `reframe_misfire`; add a new one only for a genuinely new pattern) |
| `Day in Trial` | days since Aura sign-up when the tenure read resolved it; omit otherwise |
| `Slack Thread URL` | the entry's `permalink` from `fetch_ratings.py` — never hand-built, since the workspace domain is not derivable from the channel id |
| `Steering Feedback` | the typed feedback; empty when the entry carried only `No feedback` |

## Page body

Distil the posted reply into the schema's five sections. One short clause per bullet, no
full quotes, and the same `Compliance/Privacy` masking as the reply (UID prefix only, no
given names, sentinel markers stripped):

```markdown
## Headline
<the reply's headline>

## What worked
<what the session did well, from the reply's account; "Nothing notable" when none>

## What didn't
<the why-polarity read: the friction the user hit>

## Hypotheses to validate
<the Signal line's falsifiable hypothesis, with its evidentiary weight>

## Recommended follow-up
<the eval case or the check the Signal line names>
```

Sources: the OpenAI `Conversation [Production]` project filtered by the conversation id
(primary), and the rating's Slack thread.

## Write

```yaml
shared:record-knowledge
  caller: <this skill's plugin:skill id>
  path: Learnings/Conversations
  action: create        # or update
  page_id: <existing row URL, update only>
  fields: <above>
  sources: <above>
  body_replace_sections: <update only: the five sections above>
```

## Failure handling

A write that fails (Notion down, schema rejection, missing `NOTION_TOKEN`) is logged with
the entry's UID prefix and `conversation_id` and the tick moves on. It is never retried inside
the tick and never blocks a later post. The two actions recover differently, so count them
apart:

- **Failed create** (`create_failed`) — the conversation still has no row, so
  `data:backfill-learnings-conversations` picks it up on its next run.
- **Failed update** (`update_failed`) — the row exists and keeps its previous analysis. The
  backfill skips it (it only fills conversations with no row) and the next tick skips the
  answered thread, so nothing retries it. Name each one in the readout with its row URL, so
  an operator can re-record it with `data:analyze-user-conversations` on that
  `conversation_id`.

Report the counts in the tick readout: `recorded C created · U updated · create_failed ·
update_failed (row URLs) · S not eligible`.
