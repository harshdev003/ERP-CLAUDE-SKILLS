---
name: export-user-affirmations
description: >
  Exports one Aura user's own Aurie affirmations to a readable PDF (and Markdown), so Support can send a
  user a copy for a data request. Verbatim: each affirmation's text, grouped by the user's local day; no ids or
  internal fields, and no LLM reads the content. Read-only. Takes a Firebase UID from a trusted
  source, never an email lookup. Same layout as `eng:export-user-journal`, so the exports read as
  one set. Trigger on "export this user's affirmations", "export user affirmations to PDF", "the user wants a copy of their affirmations", "data request for affirmations", or "export {uid} affirmations". Do NOT use for the journal (`eng:export-user-journal`), conversations (`eng:export-user-conversations`) or insights (`eng:export-user-insights`).
metadata:
  version: "1.0.2"
knowledge:
  reads:
    - Guide/Index
    - Compliance/Privacy    # a data-subject export: the user's own content, back to the user
    - Tools/Aura API        # IntelligenceService affirmation list; UserService user timezone
---

# Export User Affirmations

Produce a PDF of one user's Aurie affirmations: the document Support sends when someone asks for
a copy of their data. The reader is the user, so it shows the affirmations as the user can read them and
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
| `text` | the affirmation |
| `createdAt` | the time, in the user's timezone, under its local day |

The user's own feedback on an affirmation shows inside it as "Your feedback"; nothing prints where there is
none. Never rendered: `id`, `userId`, `source`. Every entry is labelled "Written by Aurie". An
affirmation with no text is counted on the summary line, never silently dropped. Removed affirmations are
not returned by the API.

## Step 1: Get the section file

The rows come from **`eng:query-user-export-data`**, run by the IntelligenceService database owner:
one read-only SQL transaction that writes `affirmations` as a JSON file (`{version, entity, generatedAt,
userIdPrefix, rows}`, 0600 in a 0700 folder). There is deliberately no API route that returns another
user's affirmations. Never paste the file's content into chat.

## Step 2: Run the export

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/export-user-affirmations/scripts/export_ai_items.py \
  --entity affirmations --uid "$(cat <trusted uid file>)" --from-file <the affirmations file> \
  --format both --out <private folder>/affirmations.pdf
```

| Flag | Purpose |
|---|---|
| `--entity` | `affirmations` for this skill (canonical copy of the script in `eng:export-user-insights`). |
| `--uid` | Required. The user's Firebase UID, from a trusted source; the file's uid prefix must match it. |
| `--from-file` | Required. The `affirmations` file from `eng:query-user-export-data`. Another version, section or user is refused. |
| `--format` | `pdf` (default), `md`, or `both`. |
| `--out` | Output path. Default `./aurie-affirmations-<uid prefix>.pdf`; the extension is swapped per format. |
| `--timezone` | Override the user's timezone (IANA name). |
| `--html-only` / `--keep-intermediates` | Debugging only: the HTML holds the export. |

`AURA_API_TOKEN` is needed for one read, the user's timezone.

**Sources, both read-only:**

- The `affirmations` file (above).
- `GET /users/:userId` (UserService), for `timezone` only. With no timezone on the profile the column
  default `America/New_York` applies. **If it can't be read**, the export stops and asks for
  `--timezone` rather than putting affirmations on the wrong days.

The script prints one summary line: the UID prefix, the days, the affirmations shown out of the rows read,
and how many had nothing to show. Quote that line when reporting back.

## Step 3: Check before sending

- **Do the counts add up?** Shown plus "nothing to show" equals the rows read.
- **Day boundaries** are the user's local days, as in the journal export.

## Related

- `eng:export-user-journal`, `eng:export-user-conversations`: the other parts of a data request.
- `eng:export-user-insights`: the canonical copy of the script, with `--entity insights`.
