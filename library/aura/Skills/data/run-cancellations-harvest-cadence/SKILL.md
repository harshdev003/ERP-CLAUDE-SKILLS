---
name: run-cancellations-harvest-cadence
description: >
  Run the recurring Group 2 (Cancellations) maintenance cadence end-to-end — the orchestrator
  that composes the three atomic cancellation skills in order:
  `data:harvest-cancellation-feedback` (sweep into Signals/Cancellations),
  `data:enrich-cancellation-feedback` (backfill Sessions/Turns/Signal/Vertical on new rows),
  then `data:update-cancellations-top10` (refresh the Learnings synthesis, only when the
  corpus grew enough to warrant it). Trigger on "run the cancellations cadence", "run the
  cancellations harvest", "do the weekly cancellations run", "run group 2 harvest", "harvest
  and enrich cancellations", "cancellations maintenance sweep", or "refresh the cancellations
  pipeline". Note "group 2" alone is ambiguous — the Feedback Analysis cadence has its own
  Group 2 (rating deep-analysis); this is the group 2 harvest. Do NOT use to run a single step
  — call the three skills directly — or for the per-user interactive churn digest
  (`data:investigate-cancellation-feedback`).

metadata:
  version: "3.0.2"
knowledge:
  reads:
    - Guide/Index
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
    - Tools/OpenAI                   # OpenAI project keys + stored-completions access
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Run Cancellations Harvest Cadence

## Goal

Execute the **Group 2 — Cancellations** maintenance cadence documented on the
**Knowledge & Conversation Data — Run Cadence** AI Knowledge page
(`https://app.notion.com/p/aurahealth/Knowledge-Conversation-Data-Run-Cadence-39d75e23af208187a5fef673ed15cdfe`)
in one guided pass: **harvest → enrich → (conditionally) refresh the Top 10 synthesis**.

This is a thin **orchestrator**. It owns the *sequence* and the *when-to-refresh* gate —
it does not re-implement any step. Each stage is a standalone sibling skill that carries
its own workflow, env, `knowledge` manifest, and PII rules; this skill just runs them in
order and decides whether the final synthesis step is warranted this run.

**One override the orchestrator MUST enforce.** Both `data:harvest-cancellation-feedback`
(its Step 3) and `data:enrich-cancellation-feedback` (its Step 4) end by telling the agent
to go run `data:update-cancellations-top10`. When you invoke them **as stages of this
cadence, ignore those trailing "now refresh the Top 10" instructions** — the Top 10 refresh
is owned solely by this skill's Step 3, which runs it exactly once, *after* enrich, and only
when the gate is met. Letting a sub-skill trigger it would refresh before enrich completes
and bypass the gate. Treat harvest and enrich as producing data only; the synthesis decision
is this orchestrator's alone.

## Scope — Group 2 only

- **In scope:** the three cancellation skills below, run in order.
- **Out of scope:** cadence Groups 1 (Conversation patterns & topics), 3 (Aurie cost),
  and 4 (Knowledge hygiene); `shared:daily-status`; any per-user Slack digest. If the
  user asks for those, stop and point them at the right skill — don't fold them in.
- **No "daily" item exists for Group 2.** It is a weekly (or ~every-20-new-rows) cadence.
  If asked "what's the daily cancellations task", the answer is: there isn't one.
- **"Group 2" is page-relative — disambiguate.** This is Group 2 on the *Knowledge &
  Conversation Data — Run Cadence* page (Cancellations). A *different* sibling page,
  *Feedback Analysis — Run Cadence*, has its own Group 2 (a rating deep-analysis sweep) —
  that is **not** this skill. When a bare "run group 2" could mean either, this skill is
  specifically the **"group 2 harvest"** (Cancellations); confirm the cancellations intent
  before running if it's genuinely unclear.

## Composed skills

| Stage | Skill | What it does | Cadence |
|---|---|---|---|
| 1 | [`data:harvest-cancellation-feedback`](../harvest-cancellation-feedback/SKILL.md) | Sweeps `#ai-feedback` k8bot cancellation posts into the Signals/Cancellations DB, dedup by Slack Thread URL | Weekly, or ~every 20 new cancellations |
| 2 | [`data:enrich-cancellation-feedback`](../enrich-cancellation-feedback/SKILL.md) | Backfills Archetype/Acquisition/Age Group/Sessions/Turns + the two agent-judgment fields (Signal, Vertical) on rows harvest left blank | After each harvest |
| 3 | [`data:update-cancellations-top10`](../update-cancellations-top10/SKILL.md) | Re-synthesizes the "Top 10 Learnings — Cancellations" section on the Learnings page | Weekly / ~every 20 rows |

## Required env

These are the tokens the **composed** skills need — this orchestrator sets none of its own.
Each sub-skill degrades per its own rules if a token is missing; see its SKILL.md.

| Var | Needed by | Notes |
|---|---|---|
| `SLACK_BOT_TOKEN` | harvest, enrich | Reads `#ai-feedback`; enrich re-fetches the source message to recover the full UID |
| `AURA_API_TOKEN` | harvest, enrich | Profiles each user (archetype, acquisition, age group). Degrades gracefully if unset |
| `OPENAI_CONVERSATION_API_KEY` | enrich | Sessions/Turns + conversation excerpt via stored completions. **Project-scoped `Conversation [Production]` key — NOT `OPENAI_ADMIN_API_KEY`**, which cannot hold the `model.request` scope stored-completions reads require ([PROD-3572](https://linear.app/aura-health/issue/PROD-3572)). Degrades to blank if unset |
| `NOTION_TOKEN` | all three | Reads/writes the Signals/Cancellations DB and the Learnings page |

## Workflow

Run the stages in order. **Each stage is a real skill invocation** — invoke the sibling
skill (which loads its own SKILL.md), don't reimplement it here.

### Step 0 — Establish the refresh baseline

The Top 10 gate in Step 3 measures growth **since the last synthesis**, not since this run —
so anchor on the Learnings page, not the harvest delta. Record, from the byline of the
"Top 10 Learnings — Cancellations" section on the Learnings page
(`*Synthesized <date> by agent from N rows…*`):

- **`last_synthesis_count`** — the `N` in that byline.
- **`last_synthesis_date`** — the date in that byline.
- **`current_total`** — the current Signals/Cancellations row count (the harvest dry-run in
  Step 1 prints "Existing DB rows: N"; after harvest, add the new rows).

Two caveats to carry into Step 3:

- **Placeholder / missing byline.** `data:update-cancellations-top10` allows a placeholder
  line with no `N` and no date (a never-synthesized or reset page). If you can't read a
  numeric `N` or a date, treat `last_synthesis_count = 0` and `last_synthesis_date =
  unknown` — i.e. **a refresh is warranted** (there's no prior synthesis to protect).
- **The delta is approximate.** `last_synthesis_count` counts rows the *synthesis*
  considered (it skips stub rows), while `current_total` is the full DB count — so
  `current_total − last_synthesis_count` runs slightly high. Treat the ~20-row threshold as
  a heuristic, not a precise trigger, and lean on the time-since-last-synthesis signal when
  the delta is near the boundary.

### Step 1 — Harvest (`data:harvest-cancellation-feedback`)

1. Run the harvest **dry-run first** (`--window 7d --dry-run`) — the sub-skill's own
   plan-before-write step. Review the printed summary: fetched vs already-in-DB vs new,
   and any profile-fetch warnings.
2. If the rows look right, run it **for real** (`--window 7d`).
3. Record how many new rows were added. **Zero new rows does not end the run** — always
   proceed to Step 2's backlog check, and always evaluate the Step 3 gate afterward
   (the weekly time-trigger and growth accumulated in *prior* runs can warrant a refresh
   even on a zero-harvest run). Never stop before Step 3.
4. Ignore the harvest skill's own trailing Step 3 ("run `data:update-cancellations-top10`") —
   that decision is this orchestrator's Step 3.

Use a longer `--window` (e.g. `30d`, or a `YYYY-MM-DD..YYYY-MM-DD` range) for a catch-up run.

### Step 2 — Enrich (`data:enrich-cancellation-feedback`)

**Always run the backlog check**, even when Step 1 added zero rows — enrich only ever picks
up rows still missing `Sessions`, so a run with no un-enriched rows is a cheap no-op, and a
run with a stranded prior backlog gets it drained.

**Precondition — the drain loop needs `OPENAI_CONVERSATION_API_KEY`.** With that key set,
enrich writes `Sessions`/`Turns` (even `0` for a genuine no-conversation user), so enriched
rows drop out of the `fetch` filter and the loop terminates. **With the key unset, enrich
deliberately leaves `Sessions` blank** so a later keyed run can still pick the row up — which
means those rows never leave the `fetch` filter and a "loop until 0" would run forever. So:

- **Key set (normal):** loop as below until `fetch` returns 0.
- **Key unset:** do **not** loop. Run a **single** pass so the deterministic profile fields
  (Archetype/Acquisition/Age Group) still get written, then stop and flag in Step 4 that
  Sessions/Turns/Signal/Vertical are pending a keyed re-run. Do not treat the still-nonempty
  `fetch` as an incomplete drain.

Loop (key set):

1. `fetch` a batch (`--limit 20`). If it returns **0 rows**, the backlog is already clear —
   go to Step 3.
2. Otherwise read each row's `existing_feedback` + `conversation_excerpt` and write the two
   agent-judgment fields (`signal`, `vertical`) per the sub-skill's Step 2.
3. `write --dry-run`, confirm, then `write` for real.
4. **Repeat** `fetch` → fill → `write` until `fetch` reports **0 rows** — the backlog is
   clear. A large harvest is several batches; don't assume one pass drains it.
5. Ignore the enrich skill's own trailing Step 4 ("consider running
   `data:update-cancellations-top10`") — that decision is this orchestrator's Step 3.

Watch the difference between the two "blank Sessions" causes: a **401 on every OpenAI call**
means the wrong key *type* is set (`OPENAI_ADMIN_API_KEY` instead of the project-scoped
`OPENAI_CONVERSATION_API_KEY` — see the PROD-3572 note), not a real zero-engagement signal
and not the key-unset degrade path above. Fix the key and re-run rather than looping.

### Step 3 — Conditionally refresh the Top 10 (`data:update-cancellations-top10`)

Always reach this step (even on a zero-harvest, empty-backlog run). The synthesis refresh is
**gated**, not automatic. Compute `delta = current_total − last_synthesis_count` (see Step 0's
two caveats — the delta is approximate, and a placeholder byline means refresh) and apply the
gate as an ordered decision so Refresh and Hold can't both match:

1. **Refresh** if **either** the byline was placeholder/missing (no prior synthesis) **or**
   `delta` is **~20+ rows** **or** it's been **about a week or more** since
   `last_synthesis_date`.
2. **Otherwise Hold** — i.e. `delta` is only a handful of rows **and** it's been less than
   ~a week. Say so explicitly ("held the Top 10 refresh — only +N rows since the last
   synthesis on <date>, below the ~20-row trigger and under a week"). The week-elapsed check
   is part of the Refresh test above, so a quiet week with small growth still refreshes
   rather than holding.

When refreshing, the sub-skill produces the diff and **requires explicit approval before
it writes** — surface that approval request to the user; never auto-approve on their behalf.

### Step 4 — Report

Summarize the run:
- Rows harvested (new vs already-in-DB), and the new total.
- Rows enriched, and whether the backlog is clear — or, if `OPENAI_CONVERSATION_API_KEY`
  was unset, that Sessions/Turns/Signal/Vertical are pending a keyed re-run.
- Whether the Top 10 was refreshed or held, with the reason (delta and/or weeks since the
  last synthesis).
- Any warnings worth flagging (profile-fetch misses, degraded tokens, OpenAI 401s).

## Idempotency

Safe to re-run. Harvest dedups by Slack Thread URL (a second run adds nothing new).
Enrich only picks up rows still missing `Sessions` (already-enriched rows are skipped).
The Top 10 refresh is a full-section replace, not an append, and is gated (Step 3), so a
back-to-back re-run with no new Slack activity harvests nothing, drains no backlog, and —
unless a week has elapsed — holds the refresh: effectively a no-op.

## Edge cases

- **Zero new rows at harvest** → don't stop; still run Step 2's backlog check *and* Step 3's
  gate (a week may have elapsed, or prior runs' growth may have crossed the threshold). A run
  that harvests nothing can still legitimately refresh the Top 10.
- **Enrich backlog spans multiple batches** → keep looping until `fetch` returns 0; don't
  stop after the first batch. *Exception:* if `OPENAI_CONVERSATION_API_KEY` is unset, do a
  single pass and stop — the backlog can't drain without the key, so looping would never end
  (see Step 2).
- **Corpus barely grew, under a week** → hold the Top 10 refresh and say why (see Step 3).
  **A week or more elapsed** → refresh even on small growth.
- **Placeholder / never-synthesized Learnings byline** → treat as refresh-warranted (Step 0).
- **User asks for a different cadence group** → out of scope; point them at the right skill.
- **Token missing** → each sub-skill degrades on its own terms; note the miss in the
  Step 4 report rather than aborting the whole cadence.

## References

- Cadence source of truth: **Knowledge & Conversation Data — Run Cadence** (AI Knowledge),
  Group 2 section.
- Stage 1: [`data:harvest-cancellation-feedback`](../harvest-cancellation-feedback/SKILL.md)
- Stage 2: [`data:enrich-cancellation-feedback`](../enrich-cancellation-feedback/SKILL.md)
- Stage 3: [`data:update-cancellations-top10`](../update-cancellations-top10/SKILL.md)
- Per-user interactive churn digest (NOT this skill): [`data:investigate-cancellation-feedback`](../investigate-cancellation-feedback/SKILL.md)
- Target DB: [Signals/Cancellations](https://app.notion.com/p/fcc2e9da2a1147b99eb5342f1977becd)
- Learnings page: `https://app.notion.com/p/34975e23af208164950cc24797c2c625`
