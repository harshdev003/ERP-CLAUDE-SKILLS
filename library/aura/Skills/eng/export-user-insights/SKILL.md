---
name: export-user-insights
description: >
  Exports one Aura user's own Aurie insights to a readable PDF (and Markdown), so Support can send a
  user a copy for a data request. Verbatim: each insight Aurie wrote for them with its title, message, emotion, highlights, recommendations, timeline and conversation starters, grouped by the user's local day; no ids or
  internal fields, and no LLM reads the content. Read-only. Takes a Firebase UID from a trusted
  source, never an email lookup. Same layout as `eng:export-user-journal`, so the exports read as
  one set. Trigger on "export this user's insights", "export user insights to PDF", "the user wants a copy of their insights", "data request for insights", or "export {uid} insights". Do NOT use for the journal (`eng:export-user-journal`), conversations (`eng:export-user-conversations`) or affirmations (`eng:export-user-affirmations`).
metadata:
  version: "1.0.2"
knowledge:
  reads:
    - Guide/Index
    - Compliance/Privacy    # a data-subject export: the user's own content, back to the user
    - Tools/Aura API        # IntelligenceService insight list; UserService user timezone
---

# Export User Insights

Produce a PDF of one user's Aurie insights: the document Support sends when someone asks for
a copy of their data. The reader is the user, so it shows the insights as the user can read them and
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
| `kind` | the entry's label, as words (`session-insight` → "Session insight") |
| `title`, `message` | the title and the text |
| `emotion`, `mood` | one line, "Emotion: calm · Mood: good" |
| `highlights`, `recommendations`, `chatStarters` | a list each, headed Highlights, Recommendations, Conversation starters |
| `createdAt` | the time, in the user's timezone, under its local day |

Never rendered: `timeline` (the retired emotion-detection series; the approver's decision), `id`, `sessionId`, `userId`, `service`, `notify`, session and meeting times, `day` / `week` /
`year`. The user's own feedback on an insight (`rating` as 👍/👎 or stars, and its text) shows inside it
as "Your feedback", and nothing prints where there is none. Every entry is labelled "Written by Aurie". An insight with no title, message or
list has nothing to show: it is counted on the summary line, never silently dropped. Removed insights, and
insights replaced by a newer daily or weekly version, are not returned by the API.

**Read-only:** the script reads a file; its only network call is the user's timezone.

## Step 1: Get the section file

The rows come from **`eng:query-user-export-data`**, run by the IntelligenceService database owner:
one read-only SQL transaction that writes `insights` as a JSON file (`{version, entity, generatedAt,
userIdPrefix, rows}`, 0600 in a 0700 folder). There is deliberately no API route that returns another
user's insights. Never paste the file's content into chat.

## Step 2: Run the export

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/export-user-insights/scripts/export_ai_items.py \
  --entity insights --uid "$(cat <trusted uid file>)" --from-file <the insights file> \
  --format both --out <private folder>/insights.pdf
```

| Flag | Purpose |
|---|---|
| `--entity` | `insights` for this skill. |
| `--uid` | Required. The user's Firebase UID, from a trusted source; the file's uid prefix must match it. |
| `--from-file` | Required. The `insights` file from `eng:query-user-export-data`. Another version, section or user is refused. |
| `--format` | `pdf` (default), `md`, or `both`. |
| `--out` | Output path. Default `./aurie-insights-<uid prefix>.pdf`; the extension is swapped per format. |
| `--timezone` | Override the user's timezone (IANA name). |
| `--html-only` / `--keep-intermediates` | Debugging only: the HTML holds the export. |

`AURA_API_TOKEN` is needed for one read, the user's timezone.

**Sources, both read-only:**

- The `insights` file (above).
- `GET /users/:userId` (UserService), for `timezone` only. With no timezone on the profile the column
  default `America/New_York` applies. **If it can't be read**, the export stops and asks for
  `--timezone` rather than putting insights on the wrong days.

The script prints one summary line: the UID prefix, the days, the insights shown out of the rows read,
and how many had nothing to show. Quote that line when reporting back.

## Step 3: Check before sending

- **Do the counts add up?** Shown plus "nothing to show" equals the rows read.
- **Day boundaries** are the user's local days, as in the journal export.

## Related

- `eng:export-user-journal`, `eng:export-user-conversations`: the other parts of a data request.
- `eng:export-user-affirmations`: the same script with `--entity affirmations`.
