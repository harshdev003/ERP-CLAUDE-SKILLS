---
name: export-user-journal
description: >
  Exports one Aura user's own Aurie journal to a readable PDF (and Markdown), so Support can send
  a user a copy of their journal for a data request. Verbatim: what the app shows in the journal
  (the user's own entries, the entries Aurie wrote, their insights and affirmations, saved
  affirmations), grouped by the user's local day; no internal fields, and no LLM reads the content.
  Read-only. Takes a Firebase UID. Same layout as `eng:export-user-conversations`, so the two read as
  one set. Trigger on "export this user's journal", "export user journal to PDF", "the user wants a
  copy of their journal", "download my Aurie journal", "data request for journal entries", or
  "export {uid} journal". Do NOT use for conversation history (`eng:export-user-conversations`),
  internal analysis of a user's journal (`data:analyze-user-journal`), or a full user profile
  (`data:profile-user`).
metadata:
  version: "1.2.1"
knowledge:
  reads:
    - Guide/Index
    - Compliance/Privacy    # a data-subject export: the user's own content, back to the user
    - Tools/Aura API        # IntelligenceService journal list; UserService user timezone
---

# Export User Journal

Produce a PDF of one user's Aurie journal: the document Support sends when someone asks for a copy
of their journal. The reader is the user, and sometimes someone they show it to, so the document
shows what the user saw in the app and nothing the product keeps about them.

## Privacy: read this before running

The same boundary as `eng:export-user-conversations` (read its Privacy section). This is a
**data-subject export**: the user's own content, returned to the user.

- **Never post the PDF, or anything from it, to Slack, Notion, Linear, GitHub or any other surface.**
  Deliver it only through the channel Support agreed for user data.
- **Only the requesting user's own UID.** There is no bulk mode.
- **Report with the UID prefix only** (`dNegS3YD…`). The script's summary line already truncates it.
- **The cover has no name, email or UID.**
- **No LLM sees the content.** The export is rendered from the API rows and nothing else.
- **The intermediate HTML is deleted** unless `--keep-intermediates` is passed.
- **A hold on a request is a hold on running it.** When a request is on hold, don't run the export for
  that user, not even as a dry run. Test on a test account only.

## What goes in the document

The journal rows the app shows, with only the fields it shows:

| Type | Label | Fields |
|---|---|---|
| `daily`, `onboarding` | Written by Aurie · Daily journal / First journal | title, summary, insights, affirmations |
| `user` | Written by you · Your entry | title, the entry text (`notes`), Aurie's insights on it |
| `affirmation` | Written by Aurie · Saved affirmation | the affirmation text (`title`, else the first affirmation) |

**Every entry says who wrote it.** The label is a chip at the top of the entry, and the cover has a
two-line legend for "Written by you" and "Written by Aurie". Aurie's insights and affirmations on an
entry the user wrote are headed "Insights · written by Aurie", so they are never read as the user's.
The conversations export uses the same chip colours (labelled "You" and "Aurie" there, since a
conversation can be spoken), and both share the layout, fonts, day grouping
and page-number footer, so the documents read as one set.

An entry with no title is shown as "Untitled Journal", as the app shows it; the export adds that
text and never stores it. An insight is shown as its quote and context (the `{quote, context}` shape the app renders), or as
plain text. **The user's own feedback** on an entry (`rating` as 👍/👎 or stars, and `feedback`) shows
inside that entry as "Your feedback", and nothing prints where there is none. Never rendered: `notification`, `emotions`, `impacts`, `mood`,
`readAt` and every id. **Any other type is left out and counted**, by type, in the summary line, so
a short export can be explained (insights and affirmations have their own export skills). Entries the user removed in the app are
left out and counted as "removed in the app" on the summary line; the cover says so.

## Step 1: Get the journal file

The rows come from **`eng:query-user-export-data`**, run by the IntelligenceService database owner
in one read-only SQL transaction. It writes `journal` as a JSON file (`{version, entity, generatedAt,
userIdPrefix, rows}`, 0600 in a 0700 folder). This skill reads that file and calls no journal route.
Never paste the file's content into chat.

## Step 2: Run the export

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/export-user-journal/scripts/export_journal.py \
  --uid "$(cat <trusted uid file>)" --from-file <the journal file> \
  --format both --out <private folder>/<ticket>-journal.pdf
```

| Flag | Purpose |
|---|---|
| `--uid` | Required. The user's Firebase UID, from a trusted source; the file's uid prefix must match it. |
| `--from-file` | Required. The `journal` file from `eng:query-user-export-data`. Another version, section or user is refused. |
| `--format` | `pdf` (default), `md`, or `both`. |
| `--out` | Output path. Default `./aurie-journal-<uid8>.pdf`; the extension is swapped per format. |
| `--timezone` | Override the user's timezone (IANA name). |
| `--html-only` / `--keep-intermediates` | Debugging only: the HTML holds the journal. |

**The input is a user id, never an email.** A person can have several accounts, and an email lookup
can resolve to one that is not the account they use: the export then reads an empty or wrong
journal and looks like a clean run. Take the uid from the request's verified source (Support's
record of the account, or the service that holds the rows), not from looking the email up.

**Sources, both read-only:**

- The `journal` file (above).
- `GET /users/:userId` (UserService), for `timezone` only. A profile with none gets `America/New_York`,
  the column's default. **If the timezone can't be read** (an error or an unknown name), the export stops and asks for `--timezone`, rather than putting entries on
  the wrong days. The summary says which timezone was used and why. `AURA_API_TOKEN` is needed for
  this one read, and not at all when `--timezone` is passed.

The script prints one summary line with the UID truncated, the days, the entries shown out of the
rows read, and what was left out by type. Quote that line when reporting back.

## Step 3: Check before sending

- **Do the counts add up?** Entries shown plus left out equals the rows read. Compare the entry count
  with what the user remembers.
- **Day boundaries** are the user's local days (`user.timezone`), as in the conversations export:
  a late-night entry belongs to the day the user wrote it.

## Related

- `eng:export-user-conversations`: the conversations export; this skill shares its layout and rules.
- `data:analyze-user-journal`: internal analysis of journal content (a different audience).
