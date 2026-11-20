---
name: backfill-learnings-conversations
description: >
  Fills the historical gap in the AI Knowledge `Learnings/Conversations` database by
  analysing rated Aurie conversations that never got a deep-dive row. A resumable, chunked
  driver over `data:analyze-user-conversations`: a bundled script computes the gap (rated
  conversations in `Signals/Conversation Patterns` minus those already recorded), filters
  by polarity and window, and hands one conversation at a time to that skill with recording
  on. Defaults to negative ratings. Unfetchable conversations are counted, never silently
  skipped. Trigger on "backfill Learnings Conversations", "fill the conversations backlog",
  "backfill the negative rated conversations", "catch up Learnings/Conversations", "how big
  is the conversations backfill gap", or "record the historical rated conversations". Do NOT
  use to analyse one conversation (`data:analyze-user-conversations`), to write the
  per-rating stream (`data:harvest-conversation-patterns`), or to refresh the Top 10
  synthesis (`data:update-conversations-top10`).
metadata:
  version: "1.0.2"
knowledge:
  reads:
    - Guide/Index                     # baseline routing aid (universal)
    - Compliance/Privacy              # transcripts + UIDs pass through this driver
    - Tools/Notion                    # NOTION_TOKEN, REST access for the gap query
    - Tools/OpenAI                    # the composed skill reads stored completions
    - Signals/Conversation Patterns   # the rating stream the gap is computed from
    - Learnings/Conversations         # the target table, and the dedup side of the gap
  writes:
    - Learnings/Conversations         # one row per backfilled conversation, via the composed skill
---

# backfill-learnings-conversations

`Learnings/Conversations` is filled going forward by the unattended rating cadences, which
record every negative and neutral conversation rating they review, and by interactive deep
dives. What neither reaches is the backlog: rated conversations from before the cadences
recorded, positive ratings, and any cadence create that failed (`create_failed`). This skill
closes that gap.

It is a **driver, not an analyser**: every row is written by
[`data:analyze-user-conversations`](../analyze-user-conversations/SKILL.md), one
conversation per invocation, exactly as an interactive run would. What this skill adds is
the set to work through, the chunking, and the accounting.

## The override, stated as this skill's own decision

`data:analyze-user-conversations` documents a deliberate position: a **user-pivot** run
does *not* bulk-record, because that would flood the table with unrated backlog, and "the
feed is meant to be rated conversations only."

**This skill records in bulk, and that is a widening this skill owns — not something that
skill's rules already permit.** The justification, so a reader can judge it rather than
take it: that rule's objection is to *unrated* backlog, and every conversation here carries
a user rating. The widening is of the **pivot** (many conversations per run instead of
one), never of the **eligibility rule** (still rated conversations only). A conversation
with no rating is not in the gap set and cannot be backfilled by this skill.

If `data:analyze-user-conversations` ever changes what it considers eligible, this
paragraph is wrong and must be re-decided — not carried forward because it is written down.

## Prerequisites

- **`NOTION_TOKEN`** — required. The gap is a join across two Notion data sources, which
  the Notion MCP cannot do on our plan, so the bundled script goes to REST. Without it the
  skill cannot compute what to work on and stops.
- Whatever `data:analyze-user-conversations` needs for transcripts
  (`OPENAI_CONVERSATION_API_KEY`, with the Braintrust fallback). A conversation whose
  transcript cannot be fetched is reported, not skipped silently — see *Accounting*.

## Step 1 — compute the gap

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/backfill-learnings-conversations/scripts/gap.py \
  --polarity Negative --limit 25 --json /tmp/backfill-gap.json
```

Flags: `--polarity` (`Negative` default, comma-separated, or `all`), `--since` / `--until`
/ `--days` for a window, `--limit` for the chunk size, `--json` for the payload.

The script prints the full counts — distinct rated conversations, already recorded, gap,
gap after filters, and how many this chunk returns — then the entries. **Report the counts
to the user before analysing anything**, so the size of what remains is visible at the
start rather than inferred from how long the run takes.

### What the default `--session-type` excludes, and why it is not a drop

`Conversation ID` in the rating stream is **the id of whatever was rated, not always a
conversation**: the `Conversation` / `Chat` / `Quick Processing` / `Coaching` shapes carry an
Aurie conversation id, `Journal` carries a journal entry id, and `Profile` carries a
`rendererRating` row id. All are UUIDs from different systems.

So the default `--session-type` is the four conversation-bearing shapes. The other two are
not merely unlikely to resolve — they *cannot*, and because this skill keeps no state, the
same ids would come back in every chunk forever. On the negative gap that is 36 entries
(25 `Profile`, 11 `Journal`) out of 415.

They are **counted in the run summary**, not quietly omitted — an absent row is
indistinguishable from one that was never rated. `--session-type all` includes them if you
want to see them fail.

**Journal ratings are backfillable, just not by this skill.**
[`data:analyze-user-journal`](../analyze-user-journal/SKILL.md) resolves a `journal_id` and
writes the same `Learnings/Conversations` table. A journal backfill is a separate run with a
different driver; it is out of scope here rather than impossible. `Profile` renderer ratings
have no conversation behind them at all and do not belong in this table.

### What `--polarity Negative` means

A conversation can be rated more than once and the ratings can disagree (207 of 7,231 carry
more than one polarity). The representative rating is picked **worst-first**, so
`--polarity Negative` selects every conversation that received *at least one* negative
rating — a later thumbs-up does not retract the earlier complaint. The script's docstring
has the full rule and why an arrival-order pick produces a number that is not reproducible.

## Step 2 — plan before writing anything

Show the user the chunk before running it: the count, the polarity/window filters, and the
first few entries. Recording rows is a side effect on a durable table, so the plan comes
first and the user can cut the chunk down or change the filters.

For a run larger than ~25 conversations, say plainly that it will take a while and offer to
do it in chunks. Do not start an unbounded sweep because the gap is large.

## Step 3 — analyse and record, one conversation at a time

For each entry, invoke `data:analyze-user-conversations` with:

- **`conversation_id`** — the entry's `conversation_id`.
- **`record: true`** — the row is the point of the run.
- **`cancellation_feedback`** — the entry's `feedback`, when non-empty. It is that skill's
  generic steering string, and a backfilled conversation is months old, so the rating's own
  words are the best signal for what the analysis should look for.
- **"skip the dataset"** — say it in the same turn. That skill's Step 9 offers to save the
  conversation to a Braintrust dataset and stays ask-first even when recording is on, so a
  25-conversation chunk would otherwise stop 25 times for a confirmation, or write 25 eval
  cases the plan never listed. Its documented skip condition is the caller saying so up
  front, which is what this line is. A Braintrust case is a deliberate per-conversation
  decision, not a side effect of a backfill.

Do not batch several conversations into one invocation. The composed skill is
one-conversation-per-run by design, and its `Learnings/Conversations` idempotency check
works per conversation.

**Between conversations, keep going on a failure.** One unfetchable transcript must not end
the chunk — record it (Step 4) and move to the next entry.

## Step 4 — accounting

Every entry ends in exactly one bucket, and **every bucket is counted in the summary**:

| Outcome | Meaning |
|---|---|
| `recorded` | A `Learnings/Conversations` row was written. |
| `already_present` | The composed skill's idempotency check found an existing row — a concurrent run or a stale gap read. Not an error. |
| `unfetchable` | The transcript could not be retrieved from OpenAI or Braintrust. |
| `failed` | Anything else, with the error. |

**An unfetchable conversation is reported, never dropped.** A conversation id that no longer
resolves looks exactly like a conversation that was never in the gap, and a backfill that
quietly skips them reports a clean run over a smaller corpus than it claims. List the
unfetchable ids so a later run can retry them.

Report at the end:

```
Backfill chunk — Negative, 25 requested
  recorded         : 21
  already_present  :  1
  unfetchable      :  3   (ids listed below)
  failed           :  0
  gap remaining    : 394
```

`gap remaining` comes from re-running Step 1's script, not from arithmetic on this chunk —
the table may have moved underneath the run.

## Resumability

There is **no state file**. The gap is recomputed from the live tables on every run, so a
row written by a previous chunk is in `Learnings/Conversations` and is not offered again.
Nothing to cap, prune, or keep in sync between sessions, and an interrupted run costs
nothing but the conversations it had not reached. This is the same reasoning the analysis
cadences use — dedup on observable state rather than a watermark.

The corollary: a conversation that ends `unfetchable` **will** be offered again next run,
because nothing records the attempt. That is deliberate — a transient OpenAI failure should
be retried — but it means a permanently unfetchable id reappears in every chunk. When that
becomes noise, narrow the window with `--since` rather than inventing a skip list.

## Privacy

Rows carry a full Firebase UID in `User ID`, which is the accepted exception documented in
`Compliance/Privacy`; narrative copy uses a prefix. Nothing from this skill goes to Slack or
any other external surface — the output is Notion rows and a run summary for the caller.
Never paste a transcript into the summary.

## When NOT to use this

- **One conversation** — call `data:analyze-user-conversations` directly.
- **The per-rating stream** — that is `data:harvest-conversation-patterns`, a different
  table at a different grain. Which table an entry belongs in is decided by
  [`Guide/Placement Rules` → *Conversation & feedback entries: route by grain*](https://www.notion.so/35a75e23af20812ca987c2bcedf27dd7).
- **Positives at scale.** The schema asks for friction moments, what worked, hypotheses and
  follow-up; 5,040 positive conversations would make this table a conversation-grain mirror
  of the Signals stream, which is what rule 7 exists to prevent. `--polarity Positive`
  works, but decide it deliberately rather than reaching for `all`.
