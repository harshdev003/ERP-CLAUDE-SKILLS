---
name: export-user-memory
description: >
  Exports what Aurie remembers about one Aura user (their active memories) to a readable PDF (and
  Markdown), so Support can send a user a copy for a data request. Verbatim: each active memory's
  text only (not deleted, not expired), grouped by the user's local day, each labelled "Written by
  Aurie"; no ids or internal fields, and no LLM reads the content. Read-only: it renders the memory
  file from `eng:query-user-export-data`. Same layout as the other data-request exports. Trigger on
  "export this user's memories", "export what Aurie remembers", "data request for memory", or
  "export {uid} memory". Do NOT use for the profile, journal, conversations, insights or
  affirmations exports.
metadata:
  version: "1.0.1"
knowledge:
  reads:
    - Guide/Index
    - Compliance/Privacy    # a data-subject export: the user's own content, back to the user
    - Tools/Aura API        # UserService user timezone
---

# Export User Memory

Produce a PDF of one user's Aurie memory: the document Support sends when someone asks for
a copy of their data. The reader is the user, so it shows the memory as the user can read them and
nothing the product keeps for itself.

## Privacy: read this before running

The same boundary as `eng:export-user-journal` (read its Privacy section).

- **Never post the PDF, or anything from it, to Slack, Notion, Linear, GitHub or any other surface.**
  Deliver it only through the channel Support agreed for user data.
- **Only the requesting user's own UID, from a trusted source.** A person can have several
  accounts, and an email lookup can resolve to one they don't use: the export then reads an empty
  or wrong history that looks like a clean run. There is no bulk mode.
- **Report with the UID prefix only.** The script's summary line already shortens it.
- **No name, email or UID on the cover**, and no LLM sees the content.
- **Files are private:** the script writes them 0600 and creates a missing folder 0700. The
  intermediate HTML is deleted unless `--keep-intermediates` is passed.
- **A hold on a request is a hold on running it.** While a request is on hold, don't run the export
  for that user, not even as a dry run. Test on your own or a test account only.

## What goes in the document

| Field | Shown as |
|---|---|
| `memory` | the memory's text |
| `createdAt` | the time, in the user's timezone, under its local day |

Only active memories (not deleted, not expired), and only their text, per the approver's decision:
no recall question, category, type, depth, tags or other dates. Every entry is labelled "Written by
Aurie": Aurie notes memories from the user's conversations, and the app does not show them, so the
cover says this document is the only copy the user can read. A row with no text is counted on the
summary line, never silently dropped.

## Step 1: Get the section file

The rows come from **`eng:query-user-export-data`**, run by the IntelligenceService database owner:
one read-only SQL transaction that writes `memory` as a JSON file (`{version, entity, generatedAt,
userIdPrefix, rows}`, 0600 in a 0700 folder). There is deliberately no API route that returns another
user's memory. Never paste the file's content into chat.

## Step 2: Run the export

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/export-user-memory/scripts/export_ai_items.py \
  --entity memory --uid "$(cat <trusted uid file>)" --from-file <the memory file> \
  --format both --out <private folder>/memory.pdf
```

| Flag | Purpose |
|---|---|
| `--entity` | `memory` for this skill (canonical copy of the script in `eng:export-user-insights`). |
| `--uid` | Required. The user's Firebase UID, from a trusted source; the file's uid prefix must match it. |
| `--from-file` | Required. The `memory` file from `eng:query-user-export-data`. Another version, section or user is refused. |
| `--format` | `pdf` (default), `md`, or `both`. |
| `--out` | Output path. Default `./aurie-memory-<uid prefix>.pdf`; the extension is swapped per format. |
| `--timezone` | Override the user's timezone (IANA name). |
| `--html-only` / `--keep-intermediates` | Debugging only: the HTML holds the export. |

`AURA_API_TOKEN` is needed for one read, the user's timezone.

**Sources, both read-only:**

- The `memory` file (above).
- `GET /users/:userId` (UserService), for `timezone` only. With no timezone on the profile the column
  default `America/New_York` applies. **If it can't be read**, the export stops and asks for
  `--timezone` rather than putting memory on the wrong days.

The script prints one summary line: the UID prefix, the days, the memory shown out of the rows read,
and how many had nothing to show. Quote that line when reporting back.

## Step 3: Check before sending

- **Do the counts add up?** Shown plus "nothing to show" equals the rows read.
- **Day boundaries** are the user's local days, as in the journal export.

## Related

- `eng:export-user-journal`, `eng:export-user-conversations`: the other parts of a data request.
- `eng:export-user-insights`: the canonical copy of the script, with `--entity insights`.
