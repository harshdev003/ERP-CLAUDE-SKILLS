---
name: query-user-export-data
description: >
  Reads one Aura user's stored Aurie data from the IntelligenceService Postgres for a data request
  (their conversations, journal, insights, affirmations, active memory and the profile Aurie
  builds), in one read-only SQL transaction, and writes one private JSON file per section for the
  export PDF skills to read. Run by the database owner on their own connection. When the database
  can't be reached, it prints the exact read-only SQL command for someone with access to run, and
  never tries another credential. Trigger on "query the user's export data", "pull the data-request
  rows for {uid}", "run the export SQL", "get the JSON for the user export", or as the first step of
  a data-request export. Do NOT use to change data (it is read-only), for more than one user per
  run, or for analysis of a user (`data:profile-user`).
metadata:
  version: "1.1.0"
knowledge:
  reads:
    - Guide/Index
    - Compliance/Privacy                    # a data-subject export: the user's own content
    - Tools/Postgres/IntelligenceService    # the tables and the owner's read-only access
---

# Query User Export Data

The first step of a data-request export: read everything the export covers for **one** user from
the IntelligenceService database, and leave it as private JSON files that the PDF skills render
(`eng:export-user-conversations`, `eng:export-user-journal`, `eng:export-user-insights`,
`eng:export-user-affirmations`, and the profile and memory exports). The approver chose SQL over
privileged API routes so that no route returns another user's data to an admin credential.

## Rules

- **Run by the database owner, on their own connection** (`INTELLIGENCE_DB_URL`). No other session
  connects, and the script never tries another credential.
- **Read-only, twice over:** one `REPEATABLE READ READ ONLY` transaction that ends in `ROLLBACK`, and
  psql runs with `default_transaction_read_only=on` and a statement timeout.
- **One user, by a trusted uid file.** The uid sits in a 0600 file; psql reads it at run time, so it is
  never in the SQL text, a command line, chat or this skill's output. A file others can read is
  refused. A uid with no `user` row stops the run before any file is written.
- **Named columns only,** each SELECT filtered by that uid. The SELECTs come from the schema owner
  (BackendIntelligence) and are ported verbatim, timestamps as ISO with a `Z`.
- **Private output:** one file per section, `{version: 1, entity, generatedAt, userIdPrefix, rows}`,
  0600 in a 0700 folder, named `<uid prefix>-<section>-<timestamp>.json`. An existing file is never
  overwritten. The terminal shows paths and row counts, never content.
- **Results never go through chat,** and the files never go to Slack, Notion, Linear or GitHub.
- **A hold on a request is a hold on running it.** Don't run it for a user whose request is on hold.

## What each section holds

| Section | Rows |
|---|---|
| `conversations` | one per conversation (not incognito, not deleted), with its user and assistant messages nested in order; `practice` is `morning`, `evening` or `walking` for a daily-practice conversation (`firstMessageContext` flags `isMorningCheckIn`, `isEveningCheckIn`, `isWalkingPractice`); `removed` flags one hidden from the chat list |
| `journal` | each entry (not deleted) with title, summary, notes, insights, affirmations, the local day, and `removed` |
| `insights` | each live insight: kind, title, message, mood, emotion, highlights (without their embedded transcripts, which carry the uid and repeat conversation excerpts), recommendations, conversation starters, timeline |
| `affirmations` | each live affirmation's text |
| `memory` | each active memory's text only (not deleted, not expired), per the approver |
| `profile` | the stored profile document, the compiled insights document, the Profile-tab cards, and the onboarding conversation |
| `profileRatings` | the user's ratings of the Profile tab (`rendererKey` null) and of its cards: key, rating, feedback, date. Never the rated snapshot |

The user's own feedback travels with each item: `rating` and `feedback` on conversations, replies, journal
entries, insights and affirmations (null when not rated). No in-app survey answers.

Nothing else: no embeddings, delivery flags, or ids beyond each row's own.

## Step 1: Run it

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/query-user-export-data/scripts/export_sql.py \
  --uid-file <0600 uid file> --out-dir <private folder> [--sections conversations,journal]
```

It writes the `.sql` file into the folder, runs it with psql, then reads the files back and prints
one line per section: the row count and the path.

## Step 2: When the database can't be reached

No `INTELLIGENCE_DB_URL`, a refused connection or a denied login: the script stops (exit 3, or 2
after a failed attempt), connects to nothing else, and prints the one command for someone with read
access to the IntelligenceService database:

```bash
umask 077; PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=180000' \
  psql -X -q -v ON_ERROR_STOP=1 -f <the printed .sql file>
```

The connection comes from libpq's environment (`PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE`, and
`PGPASSWORD` or `~/.pgpass`), never a URL on the command line: a password in argv shows in process
lists. When the script connects itself, it passes psql the URL without its password and the password
as `PGPASSWORD`.

Ask that person to run it on the same machine (the `.sql` reads the uid file and writes the JSON
files into the same folder), then check the result without reading any content:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/query-user-export-data/scripts/export_sql.py \
  --uid-file <same file> --out-dir <same folder> --check <timestamp printed with the command>
```

`--print-only` does the same on purpose, without trying to connect.

## Step 3: Hand the files to the PDF skills

Each export skill takes its section's file with `--from-file` and refuses a file of another
version, section or user. Report back with the counts and the folder path only.

## Related

- `eng:export-user-conversations`, `eng:export-user-journal`, `eng:export-user-insights`,
  `eng:export-user-affirmations`: render these files as the user-readable PDFs.
- `eng:monitor-coach-replies`, `data:compare-conversations`: the other skills that read the
  IntelligenceService Postgres, with the same read-only connection pattern.
