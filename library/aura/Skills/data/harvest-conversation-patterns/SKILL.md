---
name: harvest-conversation-patterns
description: >
  Sweeps a window of Aurie conversation ratings from Slack #ai-data-intelligence across ALL
  polarities and appends one write-once row per rating to the AI Knowledge
  Signals/Conversation Patterns table, dedup on Rating ID (the k8bot message ts). Each row is
  a lightweight immutable fact about one rating — session type, theme tags, short feedback —
  so window reports are a simple Date-range query. Pure-infra entries are dropped; prolific
  raters are annotated and reported, never dropped. Trigger on "harvest conversation
  patterns", "record conversation patterns", "update conversation patterns from ratings", "add
  patterns to AI Knowledge", "record patterns from the rating sweep", "log rated conversations
  to Signals", or "add to Conversation Patterns". Do NOT dedup on Conversation ID — a thread
  can be rated more than once, so that key drops every repeat. Do NOT use for the narrative
  window report (`data:report-conversation-patterns`).

metadata:
  version: "3.3.2"
knowledge:
  reads:
    - Guide/Index                    # baseline routing aid (universal)
    - Signals/Conversation Patterns  # dedup check — skip ratings already recorded
    - Tools/Slack                    # channel directory + bot UID reference
    - Products/Aurie            # brand/product framing for the Signal one-liner
    - Compliance/Privacy             # PII redaction rules — rows carry uid_prefix only
  writes:
    - Signals/Conversation Patterns  # one write-once row per rating
---

# harvest-conversation-patterns

Turns the rating sweep's Slack stream into a durable, per-rating
observational table. The sweep (`data:investigate-user-rating`) posts threaded
analyses to every rated entry but writes nothing to Notion — the signal dies in
Slack. This skill closes that loop: it reads the rated entries in a window and
appends **one write-once row per rating** to the
[`Signals/Conversation Patterns`](../../../shared/skills/record-knowledge/references/schemas/conversation-patterns.md)
table, across **all** rating polarities. Window rollups (weekly / monthly) are
computed at read time by `data:report-conversation-patterns` — this skill just
keeps the raw stream complete.

**One row per rating, write-once.** The row is a fact about one rating at one
time: polarity, session type, theme tags, a short feedback snippet, and the id of
what was rated. It is never updated or re-created — a re-run over an overlapping
window dedup-skips ratings that already have a row. Rows carry no clustering and
no lifecycle state — this is the raw observational stream; synthesis is left to
the report skill and to `Learnings/*` curation.

**The grain is the rating, and the dedup key matches it.** `Rating ID` — the k8bot
message's Slack `ts` — is unique per rating event. The rated object's id is not: a
user can rate the same thread repeatedly, and `chat` ratings fire per message, so
deduping on `Conversation ID` keeps one rating per thread and discards the rest as
false duplicates. **Repeats are data.** `Conversation ID` is written on every row the
footer gives an id for — it is how you group a thread's ratings — but it is never the
uniqueness constraint. Measured cost of getting this wrong, on one 7-day window: 53
rows lost to 43 repeated ids, `chat` 141 rated → 112 rows
([ENG-1115](https://linear.app/aura-health/issue/ENG-1115)).

**This skill writes the automated stream: one lightweight row per rating in the
window, every polarity, positive included.** Which of the five conversation /
feedback paths a given entry belongs in is decided by grain, and that rule has
one canonical home — [`Guide/Placement Rules` → *Conversation & feedback entries:
route by grain*](https://www.notion.so/35a75e23af20812ca987c2bcedf27dd7). Read the
rule there; all five paths are named in one table, and a copy of it here would
drift out of agreement with the four that are not this skill's.

Sister skills: [`data:report-conversation-patterns`](../report-conversation-patterns/SKILL.md) (all-polarity narrative report over a window — the primary consumer of this table),
[`data:analyze-user-conversations`](../analyze-user-conversations/SKILL.md) (per-conversation deep dive; records a Learnings/Conversations row),
[`data:investigate-user-rating`](../investigate-user-rating/SKILL.md) (the sweep that produces the Slack rating stream this skill reads).

## When to use

After any rating sweep (manual or cron), or on a standing weekly cadence, when
the user wants the Conversation Patterns table kept current:

- "harvest conversation patterns"
- "record the last 7 days of ratings to Conversation Patterns"
- "add rated conversations to Signals"
- "update conversation patterns from the sweep"

Because rows are write-once and dedup on `Rating ID`, running it twice over
overlapping windows is safe — the second run only adds ratings the first didn't
cover. A past window works the same way, which is how a gap in the table is filled:
re-run the harvest over it and the missing rows land, bounded only by Slack's
retention on the channel.

## What gets a row — scope

Run `fetch_sweep_analyses.py` first (Step 1). The script drops the one non-signal
class, so **every entry it returns with a classified rating gets a row** — every
shape qualifies: conversations, journal entries, and profiles alike:

**Recorded (one row each):**
- Every rating the script returns, **all polarities** — positive, neutral, and
  negative alike. Positive ratings are first-class here: they are
  the marketing / ad-winner / "what worked" signal the report leans on.
- **Every rating on a thread, not just the first.** N ratings on one conversation
  are N rows. This is the whole point of keying on `Rating ID`.
- Rows with short or empty feedback are still recorded — the rating + session
  type + conversation id are themselves signal. `Feedback` is just optional.
- Rows with **no `source_id`** — the `Rating ID` still identifies them, so they are
  recordable; they just land with a blank `Conversation ID`. Every k8bot shape carries
  an id in its footer (`conversation:` on the conversation shapes, `id:` on journal and
  profile), so the count should be 0; a non-zero one means the footer shape changed
  upstream and the parser needs updating. Report it, don't drop the rating for it.

**Returned by the script but NOT recordable** (counted, not written — see Step 2/3):
- **Unknown polarity** — `fetch_sweep_analyses.py` emits `polarity: "unknown"`
  when no rating emoji was detected. `Rating` is required and enum-restricted
  (`Positive`/`Neutral`/`Negative`), so an unknown-polarity entry can't be a row.

**Already dropped by the fetch script (never reach you):**
- **Pure-infra entries** — voice issue / audio issue / not connecting; these are
  device/transport failures, not conversation patterns.

**Annotated, never dropped — prolific raters.** Rating volume is not a test signal:
it tracks engagement, so the heaviest raters in any window are the most engaged real
users, and a volume threshold removes them in proportion to how much they use the
product ([ENG-1048](https://linear.app/aura-health/issue/ENG-1048)). The script tags an
entry `high_volume: true` when its UID posted ≥20 ratings inside any **rolling 7-day
span** (`high_volume_threshold` / `high_volume_span_days` in the output) and reports the
peak per UID prefix in `high_volume_uids`. **Every one of those entries still gets a
row.** When a UID genuinely is a QA loop, exclude it by naming the account — the
`Team/Internal Accounts` allowlist — never by inferring it from volume.

The span is **7 days because the rule says so, not because the pass is 7 days.** A
count taken over the harvest window instead would make the pass's width decide who
counts as a test loop: the same UID rating at the same rate gets one answer from a
weekly sweep and another from a wide re-harvest. Keep any future volume rule anchored to
a fixed span for that reason.

**Coverage caveat for rows dated before 2026-08-10.** The table under-represents
ratings in that period — prolific users are absent from it entirely. To fill it, run
the harvest over the affected window: rows are write-once and dedup on `Rating ID`, so
a re-run adds only what is missing, and the pass width does not change what lands.
Scope and approval for that backfill live on
[ENG-1132](https://linear.app/aura-health/issue/ENG-1132).

Do not re-filter for "generic" positives — in the per-rating model a generic
"Helpful response" positive is a valid data point (it counts toward the positive
rate for the window). The only judgment call is theme tagging (below), which is
optional per row.

## Theme taxonomy (per-row tags)

Each row may carry zero or more `Themes` tags. Two families — keep the seed list
in sync with the
[`Signals/Conversation Patterns` schema](../../../shared/skills/record-knowledge/references/schemas/conversation-patterns.md)
(update both in the same PR when you add a tag).

**Technique / UX tags** (how Aurie performed — mostly from neutral/negative + the
bot analysis):

| Tag | Signal it captures |
|---|---|
| `voice_interruption` | VAD end-of-utterance fires mid-thought; Aurie speaks before user finishes |
| `reflection_depth` | Response mirrors user back without adding a reframe or new frame |
| `session_length` | User wanted more time; session ended before they were ready |
| `therapeutic_honesty` | Aurie held appropriate tension rather than colluding with avoidance |
| `somatic_efficacy` | Body-based technique produced measurable relief (yawn, breath change) |
| `multi_session_arc` | User returned in a later session to close a loop from an earlier one |
| `distress_containment` | How acute distress (activated nervous system, crisis-adjacent content) was held |
| `pacing_mismatch` | Aurie behind or ahead of where the user actually was |
| `coping_tools_gap` | User wanted strategies/techniques, session provided only reflection |
| `realism` | User surprised by human-like attunement ("creepily realistic") |
| `ceiling_effect` | Good support but content prevents positive rating; user names the constraint |
| `rating_prompt_timing` | Negative rating is a timing artifact — prompt fired before a real session |
| `journal_insight_framing` | A generated journal-insight title/framing amplified a painful pattern rather than steadying |

**Topical / marketing tags** (what the user brought — the positive-stream signal
marketing pulls from):

| Tag | Signal it captures |
|---|---|
| `relationship_attachment` | Attachment, connection, dependency in a relationship |
| `boundaries` | Setting/holding boundaries with others |
| `betrayal_recovery` | Working through infidelity, broken trust |
| `self_trust` | Rebuilding trust in one's own judgment |
| `identity_acceptance` | Self-acceptance, identity, self-worth |
| `grief_loss` | Grief, bereavement, loss |
| `parenting_family` | Parenting, family-of-origin dynamics |
| `faith` | Faith, spirituality, meaning |
| `somatic_relief` | Felt bodily relief / regulation in-session |
| `breakthrough_moment` | A clear insight / turning point the user names |

**Eight of the topical tags are assigned deterministically at write time by
[`scripts/tag_themes.py`](scripts/tag_themes.py); the rest are left empty.** The
script is the policy — it keyword-matches `relationship_attachment`,
`parenting_family`, `grief_loss`, `boundaries`, `identity_acceptance`,
`betrayal_recovery`, `self_trust` and `faith` against each entry's bot-analysis
text, and its module docstring carries the per-tag precision and recall. Do not
hand-pick tags in place of it and do not block a row on picking one: a row with
no theme is a valid row, and one tagger applied uniformly is the property that
makes this column comparable across a window.

**The technique/UX tags are not auto-derivable, and their absence is not
evidence.** `reflection_depth`, `therapeutic_honesty`, `distress_containment`,
`pacing_mismatch`, `coping_tools_gap`, `session_length`, `realism` and the others
are judgements about how Aurie performed; fitting keyword rules for them against
a hand-tagged corpus returns artifacts of the analysis template rather than
signal. They stay empty here and are added, if at all, by a later human or
analysis pass. Anyone aggregating `Themes` needs that stated alongside the
counts, because a low technique-tag count reads identically to a real absence.
`somatic_relief` and `breakthrough_moment` are topical but measured at 0.31 and
0.25 precision, so they are excluded for the same reason.

**Why this is deterministic rather than best-effort.** When tagging was a
judgement call the column accumulated several different policies — stretches
tagged technique-first, stretches tagged topical-first, and long stretches not
tagged at all — which makes any cross-window theme comparison measure the
tagger rather than the conversations. A single mechanical rule applied on every
harvest is what keeps the axis meaningful; changing the rules is therefore a
change to what the column means, so bump the skill and say so in the PR.

Unknown tags are added on the fly; add them to both tables above and the schema
seed list. Adding a tag to `RULES` means re-measuring it — a rule below roughly
0.6 precision costs more than it adds.

## Workflow

### Step 1 — Fetch rated entries

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/harvest-conversation-patterns/scripts/fetch_sweep_analyses.py \
  --window 7d \
  --json /tmp/sweep_analyses.json
```

Default window is `7d`. Pass `--window 14d` or `--window 30d` for broader
sweeps — the width changes *how much* you get, never *which* entries qualify. The
script sweeps `#ai-data-intelligence`, parses every `*[TYPE] RATED*` message across all
polarities, drops infra entries, annotates prolific raters, and (for entries with a
`conversation_id`) reads the bot-analysis thread. Output:

```json
{
  "window": "7d",
  "from_ts": 1234567890.0,
  "to_ts": 1234567890.0,
  "total_rated": 94,
  "recorded": 93,
  "skipped_infra": 1,
  "skipped_volume": 0,
  "high_volume_entries": 24,
  "high_volume_uids": { "KmDxHalP": 24 },
  "high_volume_threshold": 20,
  "high_volume_span_days": 7,
  "with_feedback": 62,
  "with_analysis": 55,
  "distinct_source_ids": 84,
  "repeated_source_ids": 5,
  "rows_beyond_first_per_source": 6,
  "entries": [
    {
      "ts": "1783937040.769999",
      "rating_ts": "1783937040.769999",
      "permalink": "https://auramobile.slack.com/archives/C07MB0TMF5J/p1783937040769999",
      "uid_prefix": "KmDxHalP",
      "polarity": "neutral",
      "shape": "conversation",
      "prompter_version": "68CA0387",
      "feedback": "I still need to process more.",
      "conversation_id": "68CA0387-...",
      "source_id": "68CA0387-...",
      "renderer_key": null,
      "bot_analysis": ":mag: *Rating review — neutral · uid `KmDxHalP` …"
    }
  ]
}
```

`rows_beyond_first_per_source` is how many rows a per-thread key discards from this
batch. Report it: a non-zero value is normal and expected, and watching it is what keeps
a regression to a per-thread key from being silent.

`skipped_volume` is always `0` and is in the output on purpose — a reader can see that
no volume filter ran, rather than inferring it from a missing key. Read it alongside
`high_volume_entries`, which is how many rows a volume filter would cost if one were
ever reintroduced.

`uid_prefix` is a short non-identifying prefix of the user id — 8 characters,
taken from after the provider on provider-prefixed ids (`google:11223344`) so
distinct users don't collapse onto one value. PII rule enforced
in the script. Full emails, given names, and family names never appear.

### Step 1b — Assign themes

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/harvest-conversation-patterns/scripts/tag_themes.py \
  --input /tmp/sweep_analyses.json \
  --json /tmp/sweep_themes.json
```

Writes `{rating_ts: [themes]}` for every entry that earned at least one tag, and
prints the per-tag counts plus how many entries were left untagged for want of a
bot analysis. Step 2 reads that file; entries absent from it get no `Themes`.

Run it on every harvest. It is the only thing writing this column, which is what
lets a window report compare theme counts without also comparing taggers — see
*Theme taxonomy* above for the policy and its measured accuracy.

### Step 2 — Map each entry to a row + dedup

There is no clustering. **Each entry becomes one candidate row.** For each entry:

- **Signal** (title, ≤120 chars): a one-line summary of what this conversation
  shows. For neutral/negative, distill the bot analysis / feedback ("User wanted
  concrete coping tools, got reflection"). For positive, name what landed
  ("Felt understood processing a breakup — asked to keep going"). If feedback is
  empty, use the shape + rating ("Neutral quick-processing session, no feedback").
- **Rating ID** (dedup key): the entry's `rating_ts` — the k8bot message ts, verbatim
  (keep the dot; don't reformat it). The field is `rating_ts` and the column is
  `Rating ID`; the mismatch is deliberate, see the script's comment. Always present, so no entry is unrecordable for
  want of a key.
- **Conversation ID** (not a dedup key): the entry's `source_id` — the id of whatever was
  rated. For the conversation shapes that's the `conversation_id`; for `journal` it's
  the journal entry id and for `profile` it's the `rendererRating` row id, both read
  from the footer's `id:`. Two entries sharing a `source_id` are two ratings of one
  thread: **both get a row.** An entry with **no `source_id` at all** is still
  recordable (it has a `Rating ID`) but loses its grouping column — write it, and count
  it as "no source id" in Step 3. In practice every k8bot shape carries one, so this
  should be 0; a non-zero count means the footer shape changed upstream.
- **Date**: the entry `ts` → `YYYY-MM-DD`.
- **Rating**: `polarity` → `Positive` / `Neutral` / `Negative`. If `polarity` is
  `unknown` (no rating emoji detected), **skip** the entry and count it as
  "unrecordable (unknown rating)" in Step 3 — `Rating` is required and can't be
  synthesised.
- **Session Type**: `shape` → `Quick Processing` (`qp`) / `Coaching` / `Conversation` / `Chat` / `Journal` / `Profile`.
- **Themes**: whatever `tag_themes.py` returned for this entry in Step 1b — zero
  or more tags. Take them as given; do not add or remove tags by hand, or the
  column stops meaning one thing across the window.
- **uid_prefix**: the entry `uid_prefix` (a short prefix — never a full UID).
  Not a dedup key; `Rating ID` is. Repeats are normal (one user, many ratings).
- **Feedback**: the entry `feedback`, trimmed/paraphrased if it carries any PII
  (never a name, email, or location).
- **Renderer** (profile shape only): the entry's `renderer_key` — the rated card
  (`core-quote-renderer`, …). A profile rating with no `renderer_key` rated the whole
  profile: write `whole-profile`, don't leave it empty, so the two cases are
  distinguishable in a query. Leave the field unset on every other shape.
- **Prompt Version**: `prompter_version` when present.
- **Slack URL**: the entry's `permalink` (the script builds it from channel + `ts`).

**Dedup — on `Rating ID`, in two places.**

1. *Against the table.* Fetch the `Rating ID`s already in `Signals/Conversation
   Patterns` (via `retrieve-knowledge`, or query the table's data source). Any
   candidate whose `Rating ID` is already present is a **skip** (write-once — the row
   is immutable and already correct).
2. *Within the batch.* Two entries can only share a `Rating ID` if the same Slack
   message was parsed twice, so intra-batch collisions should be 0 — assert it rather
   than assume it, and never collapse on any other column. Entries sharing a
   `source_id`, a `uid_prefix`, a `Date`, or identical feedback text are **distinct
   ratings and all get rows.** The batch's `rows_beyond_first_per_source` is the
   count a per-thread key discards here; it is expected to be non-zero.

Do not fetch `Conversation ID`s for dedup purposes at all. The temptation is real —
it is the column that looks like an identity — and acting on it is
[ENG-1115](https://linear.app/aura-health/issue/ENG-1115).

### Step 3 — Present the plan (plan-before-side-effects)

Rows are write-once creates (idempotent, safe), so confirm **once** with a
summary — not per row:

```
Window 7d: 94 rated (total_rated); dropped by fetch: 1 infra (skipped_infra) → 93 returned (recorded).
  Prolific raters: 24 entries from 1 UID ≥20/7d — annotated, all recorded
                   (high_volume_entries / high_volume_uids; skipped_volume 0)
  Positive P · Neutral U · Negative G
  By type: conversation X, qp Y, coaching Z, journal J, chat C, profile Pr
  Threads: 84 distinct (distinct_source_ids); 5 rated more than once
           (repeated_source_ids), 6 rows a per-thread key drops
           (rows_beyond_first_per_source)

Unrecordable (skipped in Step 2): K2 unknown rating = K   [no source id: K1 — recorded, not skipped]
Candidate rows (recorded − K): 93 − K
  Already in Signals/Conversation Patterns (dedup-skip on Rating ID): S
  → New rows to create: R

Create R write-once rows in Signals/Conversation Patterns? (yes / show N / stop)
```

`show N` prints the first N drafted rows in full for a spot-check before writing.
`stop` exits without writing. Default on `yes`: create all R.

### Step 4 — Create rows in Signals/Conversation Patterns

Use `shared:record-knowledge` with `path: Signals/Conversation Patterns`,
`action: create` (Recipe A2 — dedup on `Rating ID`, then create).
**Never `action: update`** — the schema is create-only / write-once; a duplicate
`Rating ID` returns `{ "error": "duplicate", ... }` and is treated as an
idempotent skip, not a failure.

For each new row, pass `fields`:

- `Signal` (title) · `Rating ID` (rich_text — the `rating_ts`, dot intact) ·
  `Conversation ID` (rich_text — the `source_id`) · `Date` (date) ·
  `Rating` (select) · `Session Type` (select) · `Themes` (multi_select) ·
  `uid_prefix` (rich_text) · `Feedback` (rich_text) · `Renderer` (select — profile
  rows only) · `Prompt Version` (rich_text) ·
  `Slack URL` (url — the rating-thread permalink built in Step 2).
- `sources`: the same Slack rating thread as a `{ label, url }` entry (→ `# Sources`).
- Optional light body: a `## Signal` paragraph + a short PII-safe `## Excerpt`
  (2–4 paraphrased lines) when the conversation is worth a snippet; most rows are
  property-only.

Batch through the R new rows. On any per-row `duplicate` result, count it as a
skip and continue (belt-and-suspenders against a mid-run re-rate). Print a
running count; don't stop the batch on a single skip.

### Step 5 — Done

Report the counts:

```
Signals/Conversation Patterns: R rows created, S dedup-skipped on Rating ID
(window 7d, P positive · U neutral · G negative). K unrecordable (unknown rating).
Threads rated more than once: 5 → 6 rows a per-thread key drops.
Prolific raters: 24 rows from 1 high-volume UID — recorded, not dropped.
K1 rows written with no Conversation ID (expected 0).
```

The report over this table is `data:report-conversation-patterns` (weekly /
monthly window query) — there is no separate Top 10 synthesiser for this stream.
