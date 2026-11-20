---
name: enrich-cancellation-feedback
description: >
  Backfill Archetype, Acquisition, Age Group, Sessions, Turns, Signal, and
  Vertical onto Signals/Cancellations rows that were already harvested by
  data:harvest-cancellation-feedback but never had these fields populated.
  Trigger when the user says "enrich cancellation feedback", "backfill
  cancellation rows", "fill in sessions and signal for cancellations", "add
  archetype and turns to the cancellations DB", "infer verticals for
  cancellations", or asks to complete the profile/engagement fields on
  already-harvested cancellation entries.
  Sister skill: data:harvest-cancellation-feedback creates the rows in the
  first place; this skill only enriches existing ones.
metadata:
  version: "3.3.1"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Signals/Cancellations      # rows to enrich
    - Tools/Slack                  # re-fetch source message by ts
    - Tools/Notion                 # Cancellations DB read + write mechanics
    - Tools/Aura API               # per-user profile lookup
    - Tools/OpenAI                 # stored-completions session/turn counts
    - Compliance/Privacy           # PII rule: never persist a full Firebase UID
    - Tools/Braintrust               # Braintrust fallback path (BRAINTRUST_API_KEY)
  writes:
    - Signals/Cancellations      # enriches existing rows in place
---

# enrich-cancellation-feedback

Backfills the fields `data:harvest-cancellation-feedback` deliberately leaves blank — Archetype, Acquisition, Age Group (until [PROD-3441](https://linear.app/aura-health/issue/PROD-3441)'s profile-field fix, these were silently blank for every harvested row), plus Sessions, Turns, Signal, and Vertical (blank by harvest design — they need conversation data harvest doesn't fetch). Signal and Vertical are the two agent-judgment fields (see Step 2); the rest are deterministic pulls. Support Style stays blank: the Aura `/users/{uid}` response carries no such field and no other source provides it, so the column is left empty rather than fabricated.

This is the enrich-existing-row counterpart to `data:harvest-cancellation-feedback` (create-new-row). Neither `data:investigate-cancellation-feedback` (Slack-digest orchestrator, runs with `side_effects.notion_record: off`) nor `data:update-cancellations-top10` (only touches the Top 10 synthesis text) write these fields — this is the one skill that does.

**Column contract:** [`Signals/Cancellations` schema](../../../shared/skills/record-knowledge/references/schemas/cancellations.md) is canonical for the table's properties and which phase owns each one. It matters more here than for the harvester: this skill *reads* property names the harvester wrote, so a rename there resolves to an empty field here and writes a blank column without raising. A doc reference, not a runtime read. `tests/test_cancellations_schema.py` pins the agreement.

## PII constraint — read before touching this skill

The Cancellations DB stores only the first 8 chars of each user's Firebase UID (hard PII rule, never relax) plus a Slack Thread URL — never the full UID. But the Aura API profile lookup and the OpenAI stored-completions lookup both require the full UID. So enriching a row means:

1. Parse the row's Slack Thread URL to recover the Slack channel + `ts`.
2. Re-fetch that exact source k8bot message from Slack to recover the full UID from its `_User: <UID>_` metadata line — the same parsing shape `fetch_cancellations.py` uses for a live sweep.
3. Use the full UID **only in memory**, for the two API calls below.
4. Discard it. It is never written to Notion, never printed, never saved to any output file — only the existing 8-char prefix is used for matching/display.

`scripts/enrich.py`'s `fetch` command enforces this: the full UID exists only inside `recover_full_uid()`'s return value and the two calls that consume it; it never enters the JSON batch the rest of the workflow operates on.

## Cost and pacing

Each row needs 1 Slack fetch + 1 Aura API call + 1 OpenAI stored-completions walk. For a large backlog (100+ rows) this is real API traffic and real wall-clock time — expect single-digit seconds per row, not instant. `--limit` (default 20) chunks a big backfill into batches; run `fetch` → fill in `signal` + `vertical` → `write` repeatedly rather than assuming one invocation clears the whole backlog.

## Workflow

### Step 1 — Fetch a batch of un-enriched rows

```bash
PYBIN="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
$PYBIN "${CLAUDE_PLUGIN_ROOT}/skills/enrich-cancellation-feedback/scripts/enrich.py" \
    fetch --limit 20 --out /tmp/enrich_batch.json
```

This queries `Signals/Cancellations` for rows where `Sessions` is empty (the enrichment-needed signal — a row that's been through this skill always has `Sessions` set, even if it's `0`), then for each of up to `--limit` rows:

- Recovers the full UID via Slack (Step "PII constraint" above).
- Calls the Aura API for Archetype / Acquisition / Age Group (same field mapping as harvest's fixed `fetch_profile()` — `onboarding.highlight.id` stripped of its `archetype_` prefix, `aiAppstackCampaign` matched against `CreativeTest`/`Scale`/else `other`/else `organic`, top-level `ageGroup` letter code).
- Calls OpenAI stored completions (`fetch_logs.py`'s `run_user_id()`, default `--window 90d`) for `conversation_count` (Sessions) and `total_turn_count` (Turns) — both are plain counts, no LLM involved.
- Builds a short conversation excerpt (most recent 1-2 conversations, first+last couple of turns each, trimmed) for the next step to read.

Writes one JSON object per row to `--out` (or stdout). `"signal"` and `"vertical"` are each pre-populated from Notion's existing cell if a prior run already wrote one for this row (e.g. `Signal` was written but `Sessions`/`Turns` were held back that time because `OPENAI_CONVERSATION_API_KEY` was unset) — otherwise they're empty and need filling in.

**`sessions: 0, turns: 0` covers two cases, both valid.** Either the OpenAI (and Braintrust-fallback) lookup genuinely found nothing for that user in the window — often a real non-engagement cancellation — or the lookup itself failed for that user and is recorded as 0 rather than left blank, so the row doesn't stay stuck matching the "un-enriched" filter forever. An empty `conversation_excerpt` with `sessions: 0` is the signal either way; write a Signal that reflects "no engagement found" rather than assuming failure. (The one case that *doesn't* get a 0: `OPENAI_CONVERSATION_API_KEY` unset entirely for the whole run — `sessions`/`turns` stay blank so a later run with the token set can still pick the row up.)

### Step 2 — Write the Signal and Vertical for each row (agent judgment, not a script)

This step is deliberately **not** deterministic-first — it's the intentional exception in this skill, and it covers the two agent-judgment fields. Sessions/Turns/Archetype/Acquisition/Age Group are all plain data pulls a script can do correctly; **Signal** and **Vertical** both require reading what the user actually said (their `existing_feedback`, their `conversation_excerpt`) — a script can't infer either.

For each entry in the batch JSON, read `existing_reason`, `existing_feedback`, `sessions`, `turns`, `archetype`, `acquisition`, and `conversation_excerpt`, then fill in:

**`"signal"`** — a short, specific, falsifiable sentence or two that names the engagement pattern, the theme, and the key insight — the same bar `data:update-cancellations-top10` uses when reading a populated Signal field. Two worked examples from real rows:

- Zero engagement: *"Zero conversations found in a 90-day OpenAI/Braintrust lookup — this reads as true non-engagement (never returned after subscribing) rather than a habit that formed and then broke."*
- High engagement: *"High-engagement carryingALot user (31 sessions, 176 turns) working through prolonged uncertainty around a possible divorce; Aurie's validate-then-open-question pattern holds steady across sessions. 'Other' as the cancellation reason likely reflects the situation resolving or changing rather than product dissatisfaction, given the depth of sustained use."*

**`"vertical"`** — the dominant emotional theme of the user's conversations, chosen from exactly one of: `grief`, `anxiety`, `relationships`, `stress`, `trauma`, `loneliness`, `self_worth`, `parenting`, `work`, `health`, `unknown`. Pick from the `conversation_excerpt` + `existing_feedback`; use `unknown` when there's no conversation content to judge from (e.g. a zero-session row with no excerpt). A value outside this set is skipped with a warning at write time, so stay within the enum.

Save the edited JSON back to the same path.

### Step 3 — Write the batch back to Notion

Preview first:

```bash
$PYBIN "${CLAUDE_PLUGIN_ROOT}/skills/enrich-cancellation-feedback/scripts/enrich.py" \
    write --input /tmp/enrich_batch.json --dry-run
```

Then for real:

```bash
$PYBIN "${CLAUDE_PLUGIN_ROOT}/skills/enrich-cancellation-feedback/scripts/enrich.py" \
    write --input /tmp/enrich_batch.json
```

Each row's non-empty fields (`Archetype`, `Acquisition`, `Age Group`, `Vertical`, `Sessions`, `Turns`, `Signal`) are written via `update_page_props` on the row's own `page_id` — no second Notion lookup needed, the `fetch` step already captured it. A row missing a field (e.g. no Aura profile because `AURA_API_TOKEN` was unset) just skips that property; already-written fields on a row are never blanked out by a later partial run. A `vertical` outside the enum is warned about and dropped rather than written.

**`Sessions`/`Turns` are held back until both `Signal` and `Vertical` are filled in.** `Sessions` is the field `fetch`'s query filters on to find un-enriched rows (`Sessions is_empty`) — writing it before both agent-judgment fields exist would drop the row out of every future `fetch` scan with one of them permanently blank. If you run `write` on a batch where a row's `"signal"` is still `""` or its `"vertical"` is blank/invalid, that row's `Archetype`/`Acquisition`/`Age Group` (and a valid `Vertical`) still get written, but `Sessions`/`Turns` are held back and a warning naming the missing field(s) is printed — go back and fill them in, then re-run `write`.

### Step 4 — Repeat until the backlog clears

Re-run Step 1 — rows already enriched (non-empty `Sessions`) won't be picked up again. Once `fetch` reports 0 rows found, the backlog is clear. Consider running `data:update-cancellations-top10` afterward if the newly-populated Archetype/Sessions/Signal/Vertical fields should feed into a refreshed synthesis.

## Required env

| Var | Purpose |
|---|---|
| `SLACK_BOT_TOKEN` | Required for `fetch` — re-fetches the source message to recover the full UID. Without it, nothing can be enriched. |
| `AURA_API_TOKEN` | Optional for `fetch` — profiles each user. Degrades gracefully: Archetype/Acquisition/Age Group stay blank if unset. |
| `OPENAI_CONVERSATION_API_KEY` | Optional for `fetch` — Sessions/Turns/conversation excerpt. A service-account key scoped to `Conversation [Production]`; **not** `OPENAI_ADMIN_API_KEY` (Admin keys can't hold the `model.request` scope this stored-completions read now requires, PROD-3572). Degrades gracefully: stay blank if unset. |
| `NOTION_TOKEN` | Required for both `fetch` (reads target rows) and `write` (writes enrichment). |

## Out of scope

- **Creating new Cancellations rows** → `data:harvest-cancellation-feedback`
- **Top 10 synthesis** → `data:update-cancellations-top10`
- **Per-user Slack digest / threaded replies** → `data:investigate-cancellation-feedback`
- **Support Style** — the Aura API carries no such field, so it's left blank

## Related

- Sister skill (creates rows this one enriches): [`data:harvest-cancellation-feedback`](../harvest-cancellation-feedback/SKILL.md)
- Sister synthesis skill: [`data:update-cancellations-top10`](../update-cancellations-top10/SKILL.md)
- Notion helper: [`scripts/notion_rest.py`](scripts/notion_rest.py) — copy of `shared:retrieve-knowledge`'s script (canonical: `plugins/shared/skills/retrieve-knowledge/scripts/notion_rest.py`)
- Slack read helper: [`scripts/slack_read.py`](scripts/slack_read.py) — copy of `shared:read-slack`'s script (canonical: `plugins/shared/skills/read-slack/scripts/slack_read.py`)
- OpenAI stored-completions helper: [`scripts/fetch_logs.py`](scripts/fetch_logs.py) — copy of `data:analyze-user-conversations`'s script (canonical: `plugins/data/skills/analyze-user-conversations/scripts/fetch_logs.py`); this skill only uses `run_user_id()` and `CONVERSATION_PROJECT_ID` from it, not its report-synthesis or CLI paths

> **Reads both OpenAI surfaces.** `fetch_logs.py` merges Chat-Completions conversations (walked via `GET /v1/chat/completions`) with Responses-API conversations, which are **not enumerable** on OpenAI — the `llmCall` ledger (BigQuery `aura-main.llm.llm_call`) indexes those and each object is then fetched by id. This needs **`GCP_KEY`** in addition to the OpenAI project key. If the ledger read fails the script warns on stderr and returns Chat-Completions-only results — treat that as incomplete, not as the answer. Results carry `api_breakdown` and `responses_source` so you can tell which surfaces were actually read. Full write-up: [`data:analyze-user-conversations`](../analyze-user-conversations/SKILL.md) Step 2.
- Target DB: [Signals/Cancellations](https://app.notion.com/p/fcc2e9da2a1147b99eb5342f1977becd)
