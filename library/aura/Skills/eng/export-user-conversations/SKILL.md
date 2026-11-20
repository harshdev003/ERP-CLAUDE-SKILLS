---
name: export-user-conversations
description: >
  Exports one Aura user's own Aurie conversation history to a readable PDF, so Support can
  send a user (and their counselor) a copy of what they said and what Aurie replied. Verbatim
  only — user turns and Aurie turns, grouped by the user's local day; no titles, summaries or
  metadata, and no LLM reads the content. Takes a Firebase UID and an optional date range.
  Trigger on
  "export this user's conversations", "export user conversations to PDF", "send the user their
  chat history", "the user wants a copy of their Aurie chats", "download all my chats with
  Aurie", "GDPR/data request for conversation history", "export {uid} conversations", or a
  Support thread asking how a user can get their transcripts. Do NOT use for internal analysis
  of a user's conversations (`data:analyze-user-conversations`), a full user profile
  (`data:profile-user`), journal content (`data:analyze-user-journal`), or aggregate
  conversation reporting (`data:report-aurie-conversations`).
metadata:
  version: "1.5.2"
knowledge:
  reads:
    - Guide/Index
    - Compliance/Privacy
---

# Export User Conversations

Produce a PDF of one user's Aurie conversation history — the document Support hands back when
someone asks "can I download my chats?". The reader is the user themselves, and often a
counselor or therapist they are showing it to, so the output is plain, chronological and free
of anything the product generated about them.

## Privacy — read this before running

This skill exists in tension with the default rule in `Compliance/Privacy`, so the boundary is
stated explicitly rather than left to judgement. That page says: **never dump full transcripts
into Slack or a Notion row**, and never paste `email`, `givenName`, `familyName` or a full
Firebase UID onto an external surface.

The PDF is a **data-subject export** — the user's own content, returned to the user. That is a
different act from publishing transcript content to an internal analytical surface, and the
default rule does not forbid it. Everything else still applies:

- **Never post the PDF, or any content from it, to Slack, Notion, Linear, GitHub or any other
  external surface.** Deliver it through the channel Support has agreed for user data.
- **Never run this for a UID that is not the requesting user's own.** There is no bulk mode and
  there should not be one.
- **When you report back in chat, on a ticket, or in a thread, use the UID prefix only**
  (`dNegS3YD…`), never the full UID, the email, or any name. The script's own summary line
  already truncates the UID for this reason.
- **The cover carries no name, email or UID** — deliberately. The user knows who they are, and
  a document that may be forwarded should not widen the identifiers it exposes.
- **No LLM sees the content.** The export is verbatim; nothing is summarised, rewritten or
  classified, so the transcript never leaves the machine it was rendered on.
- **The intermediate HTML is deleted after rendering** unless `--keep-intermediates` is passed.
  The PDF is then the only copy on disk. Agree retention with Support; do not leave it lying in
  a shared directory.

## What goes in the document

Only two things: **what the user wrote** and **what Aurie replied**. `role IN ('user',
'assistant')`, `message` only. Conversation `title` and `summary` are not selected at all — they
are written by the summary prompter, not by either party, so they have no place in a verbatim
record. System prompts, tool calls, reasoning, usage, metrics and metadata are never touched.

**Daily check-ins and practices have their own document.** Conversations flagged as a daily practice
(`firstMessageContext` `isMorningCheckIn`, `isEveningCheckIn` or `isWalkingPractice`, the only three
in prod) are left out of the main file and rendered with `--part checkins` as "Your daily check-ins
with Aurie". That cover counts each practice, and each conversation heading names its practice. The
main cover says the check-ins are in a separate file, so nothing appears twice.

Two kinds of conversation are left out. **Onboarding**: the conversation flagged
`isOnboarding`, whose turns are the onboarding quiz answers written in one batch rather than a
chat; `--include-onboarding` keeps it. **Incognito**: these are never stored, so they cannot be
exported. The cover says so, because that is the first thing a user missing a chat will ask. **Removed**: a conversation the user removed in the app is left out too, as the app shows it.


**The user's feedback** on a reply (`rating` as 👍/👎 or stars, and its text) shows under that reply, and
feedback on a whole conversation at its end; nothing prints where there is none.

**Every run of messages says who it's from**: a "You" or "Aurie" chip (a conversation can be spoken,
not only typed, so the chip names the speaker), explained once on
the cover. The chips have the same colours, and the document the same layout and page footer, as
`eng:export-user-journal`, so the two read as one set.

## Step 1: Get the conversations file

The rows come from **`eng:query-user-export-data`**, run by the IntelligenceService database owner
in one read-only SQL transaction. It writes `conversations` as a JSON file (`{version, entity,
generatedAt, userIdPrefix, rows}`, 0600 in a 0700 folder). This skill reads that file and calls no
conversation route.
Never paste the file's content into chat.

## Step 2: Run the export, once per document

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/export-user-conversations/scripts/export_conversations.py \
  --uid "$(cat <trusted uid file>)" --from-file <the conversations file> \
  --format both --out <private folder>/<ticket>-conversations.pdf
python3 ${CLAUDE_PLUGIN_ROOT}/skills/export-user-conversations/scripts/export_conversations.py \
  --uid "$(cat <trusted uid file>)" --from-file <the conversations file> --part checkins \
  --format both --out <private folder>/<ticket>-checkins.pdf
```

| Flag | Purpose |
|---|---|
| `--uid` | Required. The user's Firebase UID, from a trusted source; the file's uid prefix must match it. |
| `--from-file` | Required. The `conversations` file from `eng:query-user-export-data`. Another version, section or user is refused. |
| `--part` | `conversations` (default; daily check-ins and practices left out) or `checkins` (only those). |
| `--format` | `pdf` (default), `md`, or `both`. |
| `--out` | Output path. Default `./aurie-<part>-<uid8>.pdf`. The extension is swapped per format. Files are written 0600, and a folder the script creates is 0700. |
| `--include-onboarding` | Keep the onboarding conversation. Excluded by default. |
| `--timezone` | Override the user's timezone (IANA name). Otherwise it is read from UserService with `AURA_API_TOKEN`; if that read fails, the script stops rather than guess. |
| `--oversize-limit` | Chars above which a message moves to the Appendix (default 15000). |
| `--html-only` | Write the HTML and skip PDF rendering. |
| `--keep-intermediates` | Keep the HTML. It contains the transcript — only for debugging. |

The script prints a one-line summary with the UID already truncated. Quote that line when
reporting back; do not restate the full UID.

## Step 3: Sanity-check before sending

- **Does the day count look right?** Compare against what the user says they remember. A user
  who reports "missing" conversations is usually describing the grouping problem below, not
  data loss.
- **Did anything land in the Appendix?** Users paste transcripts from other assistants into
  Aurie. One real export contained a 260,594-character ChatGPT export in a single message —
  ~43 pages. It belongs in the Appendix, not in the middle of a day.
- **Page count.** A heavy user produces 100+ pages. That is expected; ~700 characters a turn
  over a few hundred turns adds up.

## Why grouped by day, and by the user's local day

Two decisions that look cosmetic and are not. Both came out of a real support case
([PROD-5786](https://linear.app/aura-health/issue/PROD-5786)).

**Group by day, not by conversation start.** A conversation stays open until it is ended, and
the idle cron can leave one running for days. Grouping by `conversation.createdAt` files every
turn under the day the conversation *began*, so a day the user was active can show nothing at
all. For the reporting user, **2026-08-24 held 27 user turns and zero conversation entries** —
all of them inside a conversation dated 08-23. That blank day is what "my conversations are
missing" actually looked like.

**Use the user's timezone, not UTC.** The `user.timezone` column is authoritative and defaults
to `America/New_York`. Grouping the same export in UTC instead of `America/Chicago` collapsed a
late-night session into the following day and **hid an entire active day (08-29)**. Day
boundaries in a document the user will scan by date have to be *their* day boundaries.

**A conversation spanning days is broken at the day boundary.** It appears under each day it
was active, and under each day it shows **only that day's turns** — never the whole thread
repeated. A `continued from Aug 23` / `continues Aug 30` chip marks the seam, so a day never
opens mid-thread with no explanation, but the chip is a label and not duplicated content.

Every turn therefore appears exactly once. That is worth asserting when changing the grouping:
for the reference export the per-day turn counts (16 + 131 + 131 + 55 + 17 + 63) sum to 413,
which is exactly the number of dialogue rows for that user. A grouping change that duplicates a
multi-day conversation, or drops the tail of one, breaks that identity.

## User ids come in more shapes than you expect

Do not add a charset allowlist for `--uid`. A survey of the prod `user` table found bare
Firebase ids, `-`-led push ids, `google:`-prefixed ids, and legacy ids containing `=`, `{` and
`}` — and provider-prefixed Apple ids can carry `.`. An enumerated allowlist locked **13 real
users** out of their own data-subject export, which is the one request you cannot fail.

The id travels as a URL path segment, so the `--uid` check is a typo guard only: non-blank, no
whitespace, under 256 characters. Anything that could plausibly be an id reaches the API, which
answers `USER_NOT_FOUND` if it is not one.

## Known shapes in the data

- **Aurie-opener-only conversations.** A conversation can hold exactly one `assistant` turn with
  no `prompt` and no `usage` — an opener the user never answered. These carry no user content.
  They appear in the export as a one-message block; that is correct and honest.
- **Oversized pasted messages.** See Appendix handling above.
- **Markdown.** Aurie emits `**bold**` and `*italic*`; both are rendered. Everything is
  HTML-escaped first, and arithmetic asterisks (`5 * 4`) are left alone.

## Output formats

**PDF** (default) is the one to send: a chat-bubble transcript, headless Chrome prints it
(macOS and Linux paths are both probed — no LaTeX, no weasyprint, no poppler). If no Chrome is
present the script says so, and `--html-only` still gets you the document.

**Markdown** (`--format md`) is the same document, same grouping, same rules — for when the
recipient wants something greppable, diffable, or pasteable into another tool.

One transformation is applied to Markdown that is not applied to the PDF, and it is worth
knowing about. Users paste whole documents into the chat, and a pasted `# Heading` or `---`
rule would otherwise be emitted as real structure — **hijacking the day hierarchy the export is
built on**. In a real export, a pasted document's `# Personal Context & Timeline` landed between
two day headings and its `##` sections took over the outline. So `md_safe()` escapes
*line-leading* `#` and horizontal rules inside message bodies: every character survives, it just
stops acting as structure. Inline marks (bold, italic, links) are left alone — they cannot break
the outline. The PDF never needed this because it HTML-escapes message bodies first.

## Related

- `data:analyze-user-conversations` — internal friction analysis from OpenAI stored transcripts.
  Different source, different audience, produces judgement rather than a record.
- `data:profile-user` — the full multi-source profile of a user.
- `data:analyze-user-journal` — journal entries, which this export does **not** cover.
