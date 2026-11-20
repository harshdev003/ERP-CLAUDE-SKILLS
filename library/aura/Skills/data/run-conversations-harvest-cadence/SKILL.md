---
name: run-conversations-harvest-cadence
description: >
  One-shot orchestrator for the weekly Group 1 "Conversation patterns & topics" cadence. Runs
  the five Group 1 skills in the correct order with the right defaults, then reports counts
  and snapshot links; it composes them and never reimplements their logic. Order:
  `data:harvest-conversation-patterns` then `data:report-conversation-patterns` (so the report
  reads a fresh window), plus `data:report-aurie-conversations` and
  `data:fetch-conversation-topics`, then `data:update-conversations-top10`. Defaults: Notion
  snapshots every run, no Slack unless asked, UID-prefix PII only, 7d window. Trigger on "run
  the weekly conversation cadence", "run the conversation harvest cadence", "harvest
  conversations cadence", "weekly conversation harvest and reports", or "run the conversation
  data cadence". Do NOT rely on a bare group number — they aren't globally unique — and do NOT
  use for a single step.

metadata:
  version: "3.1.2"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal). Sub-skills own their own
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
    - Tools/Braintrust               # Braintrust fallback path (BRAINTRUST_API_KEY)
                    # knowledge manifests; this orchestrator delegates all
                    # Notion / Slack / OpenAI / Braintrust / 11Labs I/O to them.
---

# Run Conversations Harvest Cadence

The weekly **Group 1 — Conversation patterns & topics** cadence in one invocation.
This skill is a thin orchestrator: it runs the five Group 1 skills in the right
order, applies the standing defaults, and prints a consolidated summary. All the
real work — Notion writes, Slack, OpenAI/Braintrust/11Labs reads, dedup,
idempotency — lives in the composed sub-skills. Mirrors the Group 1 section of the
[Knowledge & Conversation Data — Run Cadence](https://www.notion.so/39d75e23af208187a5fef673ed15cdfe)
runbook.

## What it runs

| # | Skill | Depends on | Writes (default) |
|---|---|---|---|
| 1 | `data:harvest-conversation-patterns` | — | `Signals/Conversation Patterns` rows (write-once, dedup on Rating ID) |
| 2 | `data:report-conversation-patterns` | **after** step 1 (reads a fresh window) | dated snapshot → **Aurie Conversation patterns** page (primary, every run) |
| 3 | `data:report-aurie-conversations` | independent | opt-in snapshot → `Signals/Aurie Conversations` |
| 4 | `data:fetch-conversation-topics` | independent | opt-in snapshot → `Signals/Conversation Topics` |
| 5 | `data:update-conversations-top10` | independent | rewrites `Top 10 Learnings — Conversations` on the `Learnings` page |

Only step 2 depends on step 1. Steps 3, 4 and 5 read their own sources directly
(OpenAI/11Labs; Braintrust; the `Learnings/Conversations` rows) and can run in any
order — but keep them after the harvest so a single run leaves every layer current.

**Step 5 is the group's only `Learnings/*` write** — steps 1–4 all land in
`Signals/*`. It is the counterpart to `data:run-cancellations-harvest-cadence`'s
own top-10 step, and it exists because a synthesis with no scheduled producer
drifts from the corpus it claims to summarise without anything reporting it: the
section sat at a 2026-05-11 synthesis over 3 rows while the database grew to 482.
Nothing was broken — no producer had ever been scheduled.

## Standing defaults (apply to every sub-skill)

- **Notion snapshots are the deliverable and are written every run.** Step 2's
  snapshot is always-written by design. Steps 3 and 4 leave their Notion snapshot
  *opt-in* when run standalone (each asks for approval / a routing pick first) — but
  filing that snapshot **is the whole point of running them inside this cadence**.
  So: invoking this orchestrator is the user's standing approval for those two
  opt-in snapshot writes — take the "file to Signals/…" option on the user's behalf
  and don't re-prompt per step. Step 5's approval prompt is covered the same way
  (see Step 5). (This pre-authorization covers only the Notion writes named here —
  nothing else.)
- **Do NOT post to Slack unless the user explicitly asks.** The pre-authorization
  above is Notion-only. Slack stays opt-in — the cadence lands in Notion for review
  first.
- **Harvest's plan gate:** `data:harvest-conversation-patterns` presents a
  create-plan before writing rows. Its writes are idempotent write-once creates, so
  running the cadence pre-approves that plan too — proceed without pausing unless the
  plan surfaces something unexpected (e.g. a wildly off row count), which you should
  flag rather than blindly confirm.
- **PII:** UID-prefix (first 8 chars) only, never a full UID; email domain only;
  paraphrase excerpts; and keep sensitive / safety-flagged content (abuse, minors,
  active crisis, medication) off any marketing-facing surface.
- **Run autonomously** — no per-skill confirmation between steps, per the
  pre-authorization above. Only pause if a sub-skill hits a hard blocker (a missing
  token it can't route around) or its preview shows something clearly wrong.
- **Idempotent.** The harvest dedups on Rating ID; the report snapshots
  title-suffix a same-period re-run rather than overwriting. Safe to re-run.

## Step 0 — Resolve the period

Default to a **weekly** run over the last 7 days. Only ask the user if the request
is ambiguous — otherwise proceed weekly. Monthly is supported on request.

**The sub-skills take DIFFERENT window arguments — do not reuse one flag
across all of them.** Pick each step's own flag for the agreed period; passing the
wrong token fails argparse or silently selects a different window:

| Step | Skill | Weekly flag | Monthly flag |
|---|---|---|---|
| 1 | harvest-conversation-patterns | `--window 7d` | `--window 30d` |
| 2 | report-conversation-patterns | `--window 7d` | `--window month` (or `30d`) |
| 3 | report-aurie-conversations | `--window last-7d` | `--window month-to-date` (or `previous-month` for a closed month) |
| 4 | fetch-conversation-topics | `--days 7` | `--days 30` (or a `--since/--until` calendar month) |
| 5 | update-conversations-top10 | *(no window flag)* | *(no window flag)* |

Step 5 takes **no window argument** — it re-synthesises from the whole
`Learnings/Conversations` corpus, not a window, so there is no weekly/monthly
variant to pass.

Confirm the exact flags against each sub-skill's own SKILL.md before running — the
table above is the current mapping, not a contract; if a sub-skill's flags change,
follow the sub-skill.

**Monthly definitions are not identical across sources.** A rolling "last 30 days"
(harvest / topics) and a "month-to-date" or "previous-month" calendar window
(report-aurie-conversations) cover different spans, especially mid-month. For a
monthly run, prefer each sub-skill's **calendar-month** option where it has one, and
have Step 6 state the exact window each step actually used rather than implying one
shared span.

## Step 1 — Harvest (`data:harvest-conversation-patterns`)

Invoke `data:harvest-conversation-patterns` for the window. It sweeps the rated
conversations, drops infra entries, dedups on Rating ID, and appends
one write-once row per rating to `Signals/Conversation Patterns`. Capture its counts
(rated / recorded / new / dedup-skipped) for the final summary.

Prolific raters are annotated and recorded, never dropped — the harvest skill owns that
rule ([ENG-1132](https://linear.app/aura-health/issue/ENG-1132)); don't restate a
threshold here. Do carry its `high_volume_entries` count into the summary, so a run
where one account dominates the window is visible rather than silently blended in.

Run this **first** — step 2 reads the table it populates.

## Step 2 — Pattern report (`data:report-conversation-patterns`)

Invoke `data:report-conversation-patterns` for the **same** window, after the
harvest. It stratified-samples the window, synthesises the narrative, adds the
**Recurring vs new** longitudinal callout (vs prior same-cadence snapshots), and
writes the dated snapshot to the Aurie Conversation patterns page. Do not post to
Slack (default). Capture the snapshot URL.

## Step 3 — Engagement + cost report (`data:report-aurie-conversations`)

Invoke `data:report-aurie-conversations` for the window (independent of the
harvest). File its dated snapshot to `Signals/Aurie Conversations`. Capture the
headline (conversations, voice %, cost-to-serve) + snapshot URL.

## Step 4 — Topics (`data:fetch-conversation-topics`)

Invoke `data:fetch-conversation-topics` for the window (independent; Braintrust
BERTopic Task/Sentiment/Issues). File its dated snapshot to
`Signals/Conversation Topics`. Capture the top Task topic + snapshot URL.

If the skill isn't in the registry yet (freshly reinstalled), `/reload-plugins` (or
restart) to surface it, then invoke it normally. Do **not** substitute its bundled
`scripts/report.py` as a fallback — that script only prints the report; the Notion
snapshot is filed by the skill's own steps, so running the bare script would skip
the deliverable (and violates the composition rule above). If it genuinely can't be
surfaced this session, skip step 4 and note the gap in the summary rather than
running the script.

## Step 5 — Conversations top-10 (`data:update-conversations-top10`)

Invoke `data:update-conversations-top10` (no window argument — it re-synthesises
from the whole `Learnings/Conversations` corpus). It rewrites the
`Top 10 Learnings — Conversations` section in place on the `Learnings` page.
Capture the row count it synthesised from and the bullet count.

That skill shows its diff and asks for approval before writing (its Step 6).
**Answer yes on a cadence run** — keeping the synthesis level with the corpus is
this step's whole purpose, and this cadence takes the offer as its own decision.
Pause only if its diff looks wrong (a collapse in bullet count, or a corpus count
far off the table total).

## Step 6 — Summary

Print a single consolidated readout:

- **Harvest:** rated / recorded / new rows created / dedup-skipped; table total.
- **Pattern report:** window, sample size + polarity split, snapshot URL, and the
  headline Recurring-vs-new signal (which patterns are recurring / new).
- **Aurie conversations:** conversations, voice %, full cost-to-serve, snapshot URL.
- **Topics:** top Task topic + share, snapshot URL.
- **Conversations top-10:** rows synthesised from (and the table total), bullet
  count, and the byline date now on the page.
- **Slack:** note it was NOT posted (opt-in); offer to post the pattern report to
  #ai-product if the user wants.

## Notes & edge cases

- **Composition, not reimplementation.** Always invoke the sub-skills; never inline
  their scripts or duplicate their logic here. If a sub-skill's behaviour changes,
  this orchestrator inherits it for free. Same pattern as `data:profile-user`.
- **Partial degradation.** If one independent step (3, 4 or 5) can't run — e.g. a
  missing `BRAINTRUST_API_KEY` for topics, or transcripts aged out of retention —
  complete the rest and note the gap in the summary rather than aborting the cadence.
- **Framework Python.** If a bundled sub-skill script errors on `import requests`,
  run it with the framework interpreter
  (`/Library/Frameworks/Python.framework/Versions/Current/bin/python3`) — the
  Notion REST / dedup steps need it.
- **Scope.** Group 1 only. The Cancellations (Group 2), Aurie cost (Group 3),
  Experiments (Group 4) and Knowledge-hygiene (Group 5) cadences are out of scope —
  run their own skills.
