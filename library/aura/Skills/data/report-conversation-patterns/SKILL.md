---
name: report-conversation-patterns
description: >
  Generates a weekly or monthly narrative pattern report over a window of rated Aurie
  conversations — all session types, all polarities — then writes it as a dated snapshot to
  the Aurie Conversation patterns Notion page and optionally posts to Slack #ai-product. Reads
  its sampling frame from the Signals/Conversation Patterns table, falling back to a live
  Slack sweep, then stratified-samples transcripts for themes and emotional arcs. Trigger on
  "weekly conversation pattern report", "monthly conversation pattern report", "what patterns
  came up in Aurie conversations this week", "conversation pattern summary", "report
  conversation patterns", "post conversation patterns to Slack", "representative conversation
  sample", "are the same conversation issues recurring", or "recurring conversation patterns".
  Do NOT use to analyse one user's conversation (`data:analyze-user-conversations`), to
  populate the Signals table (`data:harvest-conversation-patterns`), or for BERTopic
  distributions (`data:fetch-conversation-topics`).

metadata:
  version: "3.7.1"
knowledge:
  reads:
    - Guide/Index                    # baseline routing aid (universal)
    - Signals/Conversation Patterns  # primary sampling frame — per-rating rows over a Date window
    - Products/Aurie            # brand/product framing for pattern descriptions
    - Compliance/Privacy             # PII redaction rules — uid prefix only
    - Team/Internal Accounts         # employee allowlist passed to the fallback frame's fetch_ratings.py --internal-accounts-file (catches employees on personal accounts the Aura-API ladder misses)
    - Tools/Slack                    # channel directory + post format (+ fallback frame)
    - Tools/OpenAI                   # stored completions access pattern
    - Tools/Notion                   # Aurie Conversation patterns page write
    - Tools/Braintrust               # Braintrust fallback path (BRAINTRUST_API_KEY)
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# report-conversation-patterns

Weekly/monthly narrative Aurie conversation pattern report for marketing and
product. Reads the window's ratings from the `Signals/Conversation
Patterns` table (one write-once row per rating), stratified-samples up to
30 transcripts (one per user, one per thread, diverse session types and polarity
mix), synthesises
a narrative report, and writes it as a dated snapshot to the Aurie Conversation
patterns Notion page — the primary, always-written deliverable — with an optional
follow-on Slack post to #ai-product. When the table has no rows for the window it
falls back to a live Slack sweep.

Sister skills:
- [`data:harvest-conversation-patterns`](../harvest-conversation-patterns/SKILL.md) — populates `Signals/Conversation Patterns`, one write-once row per rating (this report's source)
- [`data:fetch-conversation-topics`](../fetch-conversation-topics/SKILL.md) — BERTopic distribution from Braintrust
- [`data:update-conversations-top10`](../update-conversations-top10/SKILL.md) — refreshes Top 10 synthesis from Learnings rows

## When to use

Weekly cadence (or on-demand) to surface what Aurie conversations look like
across the rating spectrum. Primarily useful for marketing and product stakeholders
who want a narrative view — not just the signal from negative/neutral ratings.

## PII rules (mandatory)

- UID prefix: first 8 characters only — never the full Firebase UID
- Email: domain only — never the full address
- Never include `givenName`, `familyName`, or any full email
- Conversation excerpts must be paraphrased, not verbatim if they contain
  names, locations, or other PII

## Polarity is per-surface, never pooled (mandatory)

**`chat` and `journal` are rated on a two-option 👍/👎 widget; `conversation`, `qp` and
`profile` get a three-option GREAT/OKAY/NOT-HELPFUL widget.** So a single blended "% positive"
across the window measures which surfaces can express a neutral, not how good the sessions
were — the two-option surfaces mechanically report a higher positive share, because a user
who would have chosen OKAY had to choose 👍 or 👎. The full option-set table, the observed
distribution, and the reason the constraint is client-side lives in the
[`Signals/Conversation Patterns` schema](../../../shared/skills/record-knowledge/references/schemas/conversation-patterns.md)
→ "`Rating` is not comparable across `Session Type`" (canonical — don't restate it here).

What this skill must do:

- **Report polarity split by scale group**, per the Step 4 template — never one pooled figure.
- **Never state a single headline positive rate for the window.** If a stakeholder needs one
  number, give the three-option group's rate and say which surfaces it covers.
- **`journal` is neutral-less, not positive-only** — it does capture 👎, just rarely. A window
  with zero journal negatives is a small-sample result, not evidence the surface can't express one.
- **This rule governs what the report claims, not how the sample is drawn.** Step 2's stratified
  sample preserves the window's polarity proportions so the sample represents the frame — a separate
  concern, and correct as specified there.

## Data sources

- **Sampling frame (primary)** — `query_patterns.py --window 7d` reads the
  window's rows from the `Signals/Conversation Patterns` table (one write-once
  row per **rating**, all polarities — a thread rated N times contributes N rows,
  so `counts.total` is a rating count and `counts.distinct_threads` is the thread
  count; say which one a headline number is). The script targets the live
  table's data source by default; `--data-source` or `$CONVERSATION_PATTERNS_DS`
  override it. Requires `NOTION_TOKEN`.
- **Sampling frame (fallback)** — `fetch_ratings.py --polarity all --window 7d`
  reads the k8bot `*[TYPE] RATED*` messages in #ai-data-intelligence
  (C07MB0TMF5J) directly. Used only when the table query returns no rows (harvest
  hasn't run for the window, or the table isn't created yet). Requires
  `SLACK_BOT_TOKEN`.
- **Transcripts** — `fetch_logs.py --conversation-id <id>` pulls turn-level
  content from OpenAI stored completions (Conversation [Production] project).
  Requires `OPENAI_CONVERSATION_API_KEY` — a service-account key scoped to that
  project (**not** `OPENAI_ADMIN_API_KEY`: OpenAI enforces a `model.request`
  scope on stored-completions reads that Admin keys can't hold, by OpenAI
  design — [PROD-3572](https://linear.app/aura-health/issue/PROD-3572)).

> **Reads both OpenAI surfaces.** `fetch_logs.py` merges Chat-Completions conversations (walked via `GET /v1/chat/completions`) with Responses-API conversations, which are **not enumerable** on OpenAI — the `llmCall` ledger (BigQuery `aura-main.llm.llm_call`) indexes those and each object is then fetched by id. This needs **`GCP_KEY`** in addition to the OpenAI project key. If the ledger read fails the script warns on stderr and returns Chat-Completions-only results — treat that as incomplete, not as the answer. Results carry `api_breakdown` and `responses_source` so you can tell which surfaces were actually read. Full write-up: [`data:analyze-user-conversations`](../analyze-user-conversations/SKILL.md) Step 2.
- **Sampling** — `sample.py` applies stratified sampling (polarity + session type,
  one entry per user, **one entry per rated thread**, prefer entries with
  conversation_id and feedback text). Both frames are per-rating, so the thread
  collapse is what keeps a repeatedly-rated thread from being read and quoted twice;
  it reports `summary.thread_collapsed`. The counts are untouched by it —
  restricting the qualitative sample is not the same as discounting the ratings.
  Both frames emit an `entries` list of the same shape, so `sample.py` consumes
  either without change.

**Source every headline number from `sample.py`'s `summary`, not from a frame's
`counts`.** The two frames share only the `entries` key — the table frame emits
`total` / `positive` / `neutral` / `negative` / `by_type` / `by_theme`, the Slack
fallback emits `parsed` / `real` / `test_dropped` / `infra_skipped` / `by_shape`, and
no name means anything in both. A `counts.*` path in the report template therefore
resolves to nothing on one of the two paths, and it is the fallback path — the one
taken exactly when the table is empty and nobody is checking. `sample.py` derives
`ratings_in_window`, `distinct_threads_in_window` and `by_polarity_by_shape_in_window`
from the entries themselves, so they are present and mean the same thing on both frames.
The polarity key is **per shape and there is no pooled equivalent** — a blended rate
across shapes is invalid (see the `Rating` is-not-comparable section in the schema), and
the surest way not to quote one is for it not to exist. Sum the shapes within a scale
family yourself. Use a
frame's own `counts` only for something specific to it (`test_dropped` and
`enrichment` on the fallback; `by_theme` on the table).

Auth required: `NOTION_TOKEN` (table frame **and** the Notion snapshot write in Step 5) +
`OPENAI_CONVERSATION_API_KEY` (transcripts). `SLACK_BOT_TOKEN` is needed only for the
Slack fallback frame and the optional Step 6 post.

## Workflow

### Step 1 — Load the window's ratings

**Primary — the Signals/Conversation Patterns table** (weekly/monthly is just a
Date-window query):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-conversation-patterns/scripts/query_patterns.py \
  --window 7d \
  --json /tmp/rated_all.json
```

Pass `--window 14d` / `--window 30d` / `--window month` for broader windows.
The script targets the live `Signals/Conversation Patterns` data source by
default (hardcoded; override with `--data-source <id>` or
`$CONVERSATION_PATTERNS_DS`), pulls every row whose `Date` is in the window, and
writes `{source, window, from_date, to_date, counts, entries}` — `counts` has
`total` / `positive` / `neutral` / `negative` / `by_type` / `by_theme` /
`distinct_threads` / `ratings_beyond_first_per_thread`.

**`total` is a rating count, `distinct_threads` is a thread count, and they differ.**
A thread rated more than once contributes a row per rating (correct — they are
distinct signals with their own feedback and sometimes opposite polarity). Any
headline number in the report has to say which of the two it is; "N conversations"
sourced from `total` overstates reach by exactly `ratings_beyond_first_per_thread`.
These two keys exist on this frame only — the report template reads the equivalent
frame-independent pair off `sample.py`'s `summary` (see **Sampling** above).

**Fallback — live Slack sweep.** `query_patterns.py` exits `3` when it can't
resolve the table, the table is empty for the window, or `NOTION_TOKEN` is
unset. On exit `3`, fall back to the legacy frame — **pass the same window**,
mapped to `fetch_ratings.py`'s accepted form (it takes `Nd` only, not `week` /
`month`): `week` → `7d`, `month` → `30d`, and `Nd` unchanged.

```bash
# window token -> fetch_ratings form: week=7d, month=30d, Nd=Nd
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-conversation-patterns/scripts/fetch_ratings.py \
  --polarity all --window 30d \
  --internal-accounts-file ${AI_KNOWLEDGE_CACHE_DIR:-~/.cache/ai-knowledge}/team__internal-accounts.md > /tmp/rated_all.json
```

Both write the same `{counts, entries}` shape (and, after the
`SESSION_TYPE_TO_SHAPE` normalisation, the same `shape` keys), so Step 2 is
identical either way.

**Internal accounts in the fallback frame.** Pass `--internal-accounts-file` with the
`Team/Internal Accounts` page as `retrieve-knowledge` caches it (a `knowledge.reads` path);
the script reads every Firebase UID and `google:` id off it, so never hand-build
`--internal-ids` ([ENG-1436](https://linear.app/aura-health/issue/ENG-1436)). It is the
only signal that catches employees on personal accounts. Beyond it the script joins each
distinct UID against the Aura API (`role`, `isAIReviewer`, `@aurahealth.io`, `test=true`,
Aurie lifetime-free creator sub), roughly two calls per distinct UID, so a 30-day frame
takes a few minutes. `--no-enrich` skips that join when speed matters; the allowlist still
applies, but the frame then includes internal accounts the API would have caught, so say so
in the report. Rating volume is never a drop signal — prolific raters are the most engaged
real users and belong in the sampling frame. Report `counts.test_dropped` +
`enrichment.reason` alongside the frame.

**Window semantics differ by design** — say which frame was used in the report.
The table path (`query_patterns.py`) uses **inclusive calendar days** (the
table's `Date` is day-grained): `today − (N−1)` through `today`. The Slack path
(`fetch_ratings.py`) uses a **rolling 168-hour** span ending at the current
instant. So a rating right at the window boundary can land in one frame and not
the other — expected, not a bug. Note `source: signals_table` vs the Slack
fallback in the final report, since the fallback also re-derives everything
from Slack and won't reflect any manual curation in the table.

### Step 2 — Stratified sample

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-conversation-patterns/scripts/sample.py \
  --input /tmp/rated_all.json \
  --n 30 \
  --output /tmp/sample.json
```

The script applies:
- One entry per user — dedup on the first 8 chars of the Firebase UID. The full
  UID is dropped; the output carries only `uid_prefix` (PII rule).
- Preserve polarity proportions from the window (positive : neutral : negative),
  via largest-remainder allocation that never exceeds `--n`
- Diverse session types (conversation, qp, coaching, journal, chat, profile)
- Prefer entries with `conversation_id` (transcript fetchable) and text signal —
  `feedback`, or `journal_text` on journal rows. **Profile rows have no transcript**
  — the rated object is the generated profile, not a conversation, so they're
  text-only by nature; don't let the transcript preference squeeze them out of the
  sample entirely.
- Profile rows carry `renderer` (the rated card, or `whole-profile`). When several
  land on the same renderer, that's the finding — say so rather than treating them
  as unrelated profile ratings.
- Output: the N sampled entries with their metadata (`uid_prefix`, not full UID)

Note the sample summary: how many positive / neutral / negative, what session
types, and how many carry a fetchable transcript vs text-only.

### Step 3 — Fetch transcripts

For each entry in the sample that has a `conversation_id`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-conversation-patterns/scripts/fetch_logs.py \
  --conversation-id <conversation_id> \
  --json /tmp/transcript_<n>.json
```

Collect the transcripts. For entries without a `conversation_id`, use the `feedback`
text alone as the signal (note which entries lacked a fetchable transcript).

Cap at 25 transcript fetches even if the sample has 30 entries — some entries may
lack a conversation_id or have unavailable transcripts.

### Step 3b — Read the prior snapshot(s) for recurrence

Before synthesizing, pull the **1–3 most recent prior snapshots** already on the
Aurie Conversation patterns page so this run can tell recurring patterns from new
ones. Use the bundled `notion_rest.py` (already used for the Step 5 write):

```python
import os, sys
sys.path.insert(0, f"{os.environ['CLAUDE_PLUGIN_ROOT']}/skills/report-conversation-patterns/scripts")
from notion_rest import notion

PARENT = "34a75e23-af20-801c-bfc8-ed81815c38fc"  # Aurie Conversation patterns
priors = [b for b in notion.get_block_children(PARENT) if b.get("type") == "child_page"]
# child_page titles are "Patterns — Week of <label>" / "Patterns — <Month YYYY>";
# sort by the date in the title, then take the most recent 1–3 that belong to an
# EARLIER reporting period than this run — then
# notion.get_block_children(<child_page_id>, recursive=True) to read each prior
# report body (its What-came-up / What-worked / What-didn't text). Pass
# recursive=True: the section bullets are nested content, and a shallow read drops
# them, making the recurrence check miss patterns. (The PARENT listing above stays
# shallow on purpose — it only needs the top-level child_page blocks.)
```

**Match cadence, and exclude this run's own period.** Compare like with like:
a **weekly** run (`Patterns — Week of …`) considers only earlier **weekly**
snapshots, and a **monthly** run (`Patterns — <Month YYYY>`) only earlier
**monthly** snapshots — never mix the two title shapes as priors, or the
recurrence counts blend overlapping windows (a week inside a month it's compared
against). Within that same-cadence set, filter out any child page for the period
you're reporting on now — the base title *and* any `(re-run …)` / `#N` suffixed
variant (per the Step 5 idempotency scheme). A prior must be a **strictly earlier**
week or month; otherwise a same-period re-run would count this window's own first
run as a longitudinal prior and mislabel repeated patterns as "N weeks running."

**Compare against substantive sections only — not the prior's own recurrence
bullets.** Prior snapshots now themselves carry a `### Recurring vs new` section.
Re-derive recurrence from each prior's *substantive* pattern text (What came up /
What worked / What didn't) and **ignore its `### Recurring vs new` section** — do
not chain a prior's trend bullets forward, or a mislabel compounds run-over-run
and the "do NOT invent recurrence" guard is violated.

Skip this step gracefully when there is no prior snapshot (first run, or the token
is unavailable in a Cowork sandbox) — just omit the Step 4 "Recurring vs new"
section rather than blocking the report. Keep the comparison lightweight: you're
matching this window's patterns against the prior ones by meaning, not by an exact
string — a rough "seen last period too" / "new this period" / "not seen since
<date>" is the goal (phrase it in the run's own cadence — weeks for a weekly run,
months for a monthly run).

### Step 4 — Synthesize pattern report

Read all transcripts + feedback texts together (plus the prior snapshots from
Step 3b). Write a narrative report covering:

**Report structure:**
```
## Aurie Conversation Patterns — <week label, e.g. "Week of 2026-07-07">

**Sample:** <summary.sampled> transcripts read, drawn from
<summary.ratings_in_window> ratings on <summary.distinct_threads_in_window> threads
across <window>
(<summary.by_shape>)
**Polarity** — three-option surfaces (conversation · qp · profile), summed from
`summary.by_polarity_by_shape_in_window`:
<positive_n> positive · <neutral_n> neutral · <negative_n> negative
**Polarity** — two-option surfaces (chat · journal, 👍/👎 only), same source:
<positive_n> positive · <negative_n> negative

### What came up most

<3–5 themes that appeared across multiple sessions, with brief description.
Focus on what users actually brought to Aurie — topics, emotional states,
life situations — not on product quality signals (those go in "What worked /
didn't").>

### Emotional arcs

<How sessions tended to move: how users arrived vs. how they left. Note any
notable patterns in how quickly rapport built, how distress was held, whether
sessions ended with resolution or mid-thought.>

### What worked

<2–4 specific observations about what Aurie did well across the sample.
Name the technique or dynamic, not just "good response".>

### What didn't

<2–4 observations about friction, misses, or truncated arcs. Be specific.>

### Recurring vs new

<Only when Step 3b found a prior snapshot — otherwise omit this section entirely.
A few bullets comparing this window's patterns against the prior snapshot(s):
which are recurring and roughly how many periods running, which are new this
window, and which appeared before but are absent now. Phrase the recurrence in the
run's OWN cadence — "weeks" for a weekly run, "months" for a monthly run.
Weekly-run examples:
- Directness/pacing friction — 3rd week running, still open
- Grief-register over-mirroring — new this week
- Voice-interruption complaints — not seen since <date>
(A monthly run would instead say "3rd month running" / "new this month".)
Keep it to what the priors actually support; do NOT invent recurrence. This is the
trend layer that turns one-window observations into a tracked signal.>

### Marketing signals

<What would resonate in external communications — authentic moments, capability
demonstrations, user need patterns that marketing could reference.
Keep user examples anonymised (uid prefix only, paraphrase content).>

### One thing to act on

<Single most actionable signal from this sample for the team — product, prompt,
or QA follow-up.>
```

Keep the report under 650 words (the Recurring-vs-new section adds a little).
Prioritise specificity over completeness — a sharp observation about 3 sessions
is more valuable than a generic summary of 30.

### Step 5 — Write the Notion snapshot (primary deliverable, every run)

The report's primary output is a dated snapshot under the **Aurie Conversation
patterns** page (`34a75e23-af20-801c-bfc8-ed81815c38fc`). **Write it on every
run** — marketing relies on this doc staying current, so a scheduled/automated
run must never skip it. This mirrors how `eng:report-aurie-weekly-costs` always
writes its `Signals/Aurie Weekly Cost` snapshot.

Title the page `Patterns — Week of <week label>` (weekly) or
`Patterns — <Month YYYY>` (monthly). The hierarchy is: Aurie Conversation
patterns → dated snapshot child pages, one per report run.

Build the body with the bundled `notion_rest.py` block builders and create the
page:

```python
import os, sys
sys.path.insert(0, f"{os.environ['CLAUDE_PLUGIN_ROOT']}/skills/report-conversation-patterns/scripts")
from notion_rest import notion, block_heading, block_paragraph, block_bullet, block_callout, block_divider

PARENT = "34a75e23-af20-801c-bfc8-ed81815c38fc"  # Aurie Conversation patterns
title = f"Patterns — Week of {week_label}"
blocks = [
    block_callout(f"Source: {frame} · window {window} · generated {run_date}. "
                  "Representative sample, not all logs.", "🗓️"),
    # then: Sample line → What came up most → Emotional arcs → What worked →
    # What didn't → Recurring vs new (when a prior snapshot exists) →
    # Marketing signals → One thing to act on → Sources
]
notion.create_page(PARENT, title, blocks)
```

Lead the body with a provenance callout (which frame — `signals_table` vs the
Slack fallback — and the window) and close with a `# Sources` section. This
narrative snapshot is a **separate artifact** from the `Signals/Conversation
Patterns` table (the per-rating rows Step 1 reads): the table is the raw
stream, this page is the rolled-up narrative. Do not write the narrative into
the table, and do not use `shared:record-knowledge` for per-rating rows
here — that's `data:harvest-conversation-patterns`' job.

**Idempotency.** List the parent's child pages before creating, and pick a title
that isn't already in that list — matching against the **full** child-title set,
not just the base period title. If the base title exists, append a run-timestamp
suffix (e.g. `Patterns — Week of 2026-07-08 (re-run 2026-07-15T18:20Z)`); if that
suffixed title also already exists (a second re-run in the same minute), add a
trailing counter (`… #2`, `… #3`) until the title is unique. Never overwrite a
prior snapshot.

**Auth.** The write uses the bundled `notion_rest.py` (`NOTION_TOKEN`). If the
token is unavailable (e.g. a Cowork sandbox), fall back to the `notion-create-pages`
MCP with the same parent + title + blocks.

### Step 6 — Post to Slack (optional secondary channel)

The Notion snapshot is the durable home; a Slack post is an optional extra for
in-channel visibility. Post only when the user asks (or a caller passes a
post-to-slack flag) — do not post by default.

Save the report to `/tmp/conv_patterns_report.txt`, then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-conversation-patterns/scripts/slack_post.py \
  post \
  --channel C07EQNYRU6B \
  --text-file /tmp/conv_patterns_report.txt \
  --footer data:report-conversation-patterns \
  --footer-extra "sample of <N> from <window>"
```

Default channel: `C07EQNYRU6B` (#ai-product). If the user specifies a different
channel, use the name or ID they provide.

Footer: the `--footer data:report-conversation-patterns --footer-extra "sample of <N> from <window>"` flags on the `post` command above make `slack_post.py` stamp the versioned footer (`_Generated by `data:report-conversation-patterns:<version>` · <YYYY-MM-DD> · sample of <N> from <window>_`) from `metadata.version`. Don't append a footer to the report text.

## Automation

To run this weekly, schedule it via `anthropic-skills:schedule` with a Monday morning
trigger. The Notion snapshot (Step 5) writes on every run, so a scheduled run keeps
the Aurie Conversation patterns doc current without manual intervention; the Slack
post (Step 6) stays opt-in. The skill is idempotent — a second run in the same period
is title-suffixed with the run date rather than overwriting the prior snapshot.
