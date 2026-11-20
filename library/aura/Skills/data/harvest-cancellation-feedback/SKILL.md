---
name: harvest-cancellation-feedback
description: >
  Harvest recent #ai-cancellation-feedback entries into the
  Signals/Cancellations Notion database. Sweeps the last N days of
  k8bot cancellation posts, deduplicates against existing DB rows,
  fetches each new user's Aura profile, and writes one structured row
  per cancellation. Trigger when the user says "harvest cancellations",
  "sync cancellation feedback to Notion", "batch record cancellations",
  "populate the cancellations DB", "backfill cancellations", or asks to
  add recent cancellation entries to the Signals/Cancellations database.
  Sister skill: data:update-cancellations-top10 refreshes the Top 10
  synthesis after rows are added.
metadata:
  version: "3.3.0"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal)
    - Signals/Cancellations      # dedup check — existing rows
    - Tools/Slack                  # #ai-cancellation-feedback read mechanics
    - Tools/Notion                 # Cancellations DB write mechanics
    - Tools/Aura API               # per-user profile lookup
    - Tools/OpenAI                   # OpenAI project keys + stored-completions access
  writes:
    - Signals/Cancellations      # adds new rows per cancellation event
---

# harvest-cancellation-feedback

Batch-populate the **Signals/Cancellations** Notion database from recent `#ai-cancellation-feedback` k8bot cancellation posts. The counterpart to `data:investigate-cancellation-feedback` (which drives interactive per-user analysis and posts to Slack). This skill is the automated write-path: it reads from Slack, deduplicates, profiles users, and writes structured rows — no Slack posting.

Typical cadence: weekly, covering the last 7d (or longer for catch-up runs). Once rows are in the DB, run `data:update-cancellations-top10` to refresh the Top 10 synthesis on the Learnings page.

**Column contract:** [`Signals/Cancellations` schema](../../../shared/skills/record-knowledge/references/schemas/cancellations.md) is canonical for the table's properties, their types, and which phase owns each one — this skill owns the create-time columns, `data:enrich-cancellation-feedback` owns the rest. A doc reference, not a runtime read (the skill stays self-contained). `tests/test_cancellations_schema.py` holds this script and that schema to each other, so a column added here without a schema row fails CI.

## Inputs

Optional flags:

- `--window Nd` — look-back window (default `7d`; supports `YYYY-MM-DD..YYYY-MM-DD` range).
- `--dry-run` — preview rows without writing to Notion.

## Required env

| Var | Purpose |
|---|---|
| `SLACK_BOT_TOKEN` | Reads `#ai-cancellation-feedback` history (and `#ai-feedback` for pre-split entries) |
| `AURA_API_TOKEN` | Profiles each user (archetype, acquisition campaign, age group, email domain). **Degrades gracefully if unset** — profile fields are left blank, row is still written. Support Style isn't exposed by the current Aura API and always stays blank. |
| `NOTION_TOKEN` | Writes rows to the Cancellations database |

## Workflow

### Step 1 — Fetch cancellation entries

Run the bundled `fetch_cancellations.py` against the cancellation feed. `harvest.py` passes `--channel cancellations`, which sweeps both `#ai-cancellation-feedback` (`C0BR3UTNL83`) and `#ai-feedback` (`C0AENS8D48N`, cancellations predating the 2026-08 split) and merges the entries — dedup downstream is by Slack permalink, so the overlap is harmless:

```bash
PYBIN="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
$PYBIN "${CLAUDE_PLUGIN_ROOT}/skills/harvest-cancellation-feedback/scripts/harvest.py" \
    --window 7d
```

Or invoke the full workflow via the harvest script (handles fetch, dedup, profile, write in one call):

```bash
PYBIN="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
$PYBIN "${CLAUDE_PLUGIN_ROOT}/skills/harvest-cancellation-feedback/scripts/harvest.py" \
    [--window Nd] [--dry-run]
```

The script:
1. Calls `fetch_cancellations.py` (self-contained copy in `scripts/`) to get real cancellation entries from the specified window.
2. Queries the Cancellations DB for existing Slack Thread URLs to dedup — entries already recorded are skipped.
3. For each new entry: fetches the Aura user profile (archetype, acquisition campaign, age group, email domain — Support Style is not exposed by the current API and stays blank).
4. Writes one Notion row per new entry with all structured fields.
5. Prints a summary of rows added.

### Step 2 — Review the output

Check the printed summary for:
- How many entries were fetched vs already in DB vs newly added.
- Any profile-fetch warnings (AURA_API_TOKEN missing or user not found).

If the run was `--dry-run`, confirm the rows look correct, then re-run without `--dry-run`.

### Step 3 — Trigger top-10 refresh

After a successful harvest that added new rows, run `data:update-cancellations-top10` to refresh the synthesis on the Learnings page.

## Database schema

**Notion DB:** [Signals/Cancellations](https://app.notion.com/p/fcc2e9da2a1147b99eb5342f1977becd)
**Data source:** `collection://edce288d-5d89-4b4d-8083-e40d0ed7c1ee`

| Field | Type | Source |
|---|---|---|
| Title | title | `{uid[:8]} — {reason} — {YYYY-MM-DD}` |
| Cancellation Date | date | Slack message timestamp |
| Reason | select | k8bot reason field |
| Feedback Text | text | k8bot verbatim feedback |
| Days to Cancel | number | `(cancel_ts − purchased_ts) / 86400`, both in UTC — `Purchased` is a US Pacific wall clock and is localized first (see `PURCHASED_TZ_NAME`) |
| Acquisition | select | Aura `aiAppstackCampaign` (falls back to `campaign`/`subscriptionCampaign`), matched against `CreativeTest`/`Scale`, else `other`, else `organic` when absent |
| Archetype | select | Aura `onboarding.highlight.id`, stripped of its `archetype_` prefix |
| Support Style | select | Not exposed by the current Aura API — left blank |
| Age Group | select | Aura top-level `ageGroup` (already the single-letter band code) |
| Email Domain | text | Aura `email` (domain only — PII rule) |
| User ID | text | Firebase UID first 8 chars (PII rule — never full UID) |
| Signal | text | Leave blank at harvest time; populated later by investigation |
| Slack Thread URL | url | The entry's own `permalink` — `https://auramobile.slack.com/archives/{channel_id}/p{ts}`, so pre-split rows keep their `C0AENS8D48N` link and new ones point at `C0BR3UTNL83` |
| Sessions | number | Leave blank at harvest time (no OpenAI call in harvest) |
| Turns | number | Leave blank at harvest time |
| Vertical | select | Leave blank at harvest time (inferred during investigation) |
| Creator | select | `Agent` |

**PII rules (hard constraints — never relax):**
- `User ID`: first 8 chars of Firebase UID only.
- `Email Domain`: domain portion only (`@gmail.com` → `gmail.com`). Never full address.
- No `givenName`, `familyName`, or other PII in any field.

## Dedup strategy

Dedup key: **Slack Thread URL** (encodes the exact Slack `ts` for the cancellation message, which is unique per event). A user who cancels, re-subscribes, and cancels again gets two rows with different URLs — both are valid.

## Catch-up runs

To backfill a longer period:

```bash
$PYBIN harvest.py --window 30d
```

Or a specific date range:

```bash
$PYBIN harvest.py --window 2026-06-01..2026-07-13
```

## Out of scope

- **Per-user conversation analysis and Slack posting** → `data:investigate-cancellation-feedback`
- **Top 10 synthesis** → `data:update-cancellations-top10`
- **Sessions / Turns fields** — populated separately via investigation; harvest leaves them blank to avoid requiring `OPENAI_CONVERSATION_API_KEY` (the Conversation [Production] stored-completions read)

## Related

- Source script: [`scripts/fetch_cancellations.py`](scripts/fetch_cancellations.py) — copy of `data:investigate-cancellation-feedback`'s script (canonical: `plugins/data/skills/investigate-cancellation-feedback/scripts/fetch_cancellations.py`)
- Notion helper: [`scripts/notion_rest.py`](scripts/notion_rest.py) — copy of `shared:retrieve-knowledge`'s script (canonical: `plugins/shared/skills/retrieve-knowledge/scripts/notion_rest.py`)
- Slack read helper: [`scripts/slack_read.py`](scripts/slack_read.py) — copy of `shared:read-slack`'s script (canonical: `plugins/shared/skills/read-slack/scripts/slack_read.py`)
- Target DB: [Signals/Cancellations](https://app.notion.com/p/fcc2e9da2a1147b99eb5342f1977becd)
- Sister synthesis skill: [`data:update-cancellations-top10`](../update-cancellations-top10/SKILL.md)
