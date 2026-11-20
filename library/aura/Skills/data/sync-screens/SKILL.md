---
name: sync-screens
description: >
  Projects an app's sim-test screen knowledge into its Notion Screens database —
  one row per screen, with the screenshot, how to reach it, its experiment gate
  and its known traps. The repo is the system of record; this makes Notion match
  it and reports anything it cannot reconcile. Idempotent: a second run writes
  nothing. Works for both apps: Aurie (AuraConsumerAppSimTests/knowledge) and Aura
  (AuraSimTests/knowledge, 133 screens). Trigger on "sync screens", "sync the screens to Notion", "update the
  Aurie Screens database", "are the screens in Notion up to date", "seed the
  screens database", or a question about whether Products › Aurie › Screens
  reflects the repo.
metadata:
  version: "1.5.0"
knowledge:
  reads:
    - Guide/Index
    - Products/Aurie
    - Products/Aurie/Screens
  writes:
    - Products/Aurie/Screens
---

# sync-screens

## What it does

Reads a sim-test knowledge folder and makes the matching Notion database say the
same thing:

| Repo | Notion |
|---|---|
| `knowledge/screens/*.md` | one row per screen — identifier, how to reach, experiment gate, traps |
| `knowledge/screenshots/*.png` | the `Screenshot` property **and** an image block in the row body — **three of each**, one per sky (see below) |
| `knowledge/screenshots/index.json` | `Capture sha`, `Last captured`, `Capture issues` |
| `sim_screenshots.py check` | `Status` — `captured` / `stale` / `blocked` |

**The repo is the system of record.** Nothing is ever written back to it. Edit the
markdown, then sync; do not edit rows by hand and expect them to survive.

## Usage

```bash
S=${CLAUDE_PLUGIN_ROOT}/skills/sync-screens/scripts/sync_screens.py

python3 $S check --app aurie              # read-only; exits 1 on drift, 0 in sync
python3 $S plan  --app aurie              # every row apply would create or update
python3 $S apply --app aurie --confirm    # write it
python3 $S apply --app aurie --only chatLimitReached_screen --confirm
python3 $S apply --app aurie --repo <worktree> --confirm   # read another checkout
```

`--repo` reads a different checkout or worktree than the app's configured one. The
weekly screens routine harvests in its own worktree and syncs from there, so it never
touches the main checkout.

`check` is the one to run in a cadence or a hook: it is read-only and its exit
code is the answer.

## Orphaned rows

A row whose screen has no doc in the checkout is an orphan. `apply --confirm`
archives it only when git proves the screen was removed: its `Source doc` path is
gone from `origin/main` **and** `origin/main` has the commit that deleted it. An
archived row goes to Notion's trash and can be restored. `plan` shows it as
`- <screen>  archive: doc deleted on main in <sha>`.

Every other orphan is left alone and printed as `FOLLOWUP orphan <screen>: <why>`.
That covers a doc still on main (the checkout is stale), a doc deleted and later
recreated, a row whose `Source doc` is not this app's repo, or git that can't be read.
**An unattended run raises one follow-up per line** with `shared:raise-followup`:
`--kind decision`, `--key sync-screens:<app>:orphan:<screen>`, the line's reason as
`--what`, and the ask "archive the row or restore the doc". It never ends the run with
only a comment. `--keep-orphans` turns the archiving off and reports every orphan
this way instead.

The script fetches `origin/main` before checking. A stale ref could hold a delete but
not a later recreate, and would archive a row whose doc is back. If the fetch fails,
nothing is archived and every orphan becomes a FOLLOWUP line. `--only` syncs one
screen and never archives or reports orphans.

## Stops

Any stop prints one line before the script exits: `FOLLOWUP stop <stage>: <why>`, where the
stage is `read-docs`, `read-notion`, `plan`, `write` or `archive` (a missing token, a Notion
error after its retries, an unknown screen, anything unexpected). Ids in it are cut to a prefix.
**An unattended run raises a follow-up for it** with `shared:raise-followup`: `--kind blocked`,
`--key sync-screens:<app>:stop:<stage>`, the line as `--what`, and the ask "fix the cause and
re-run the sync". A stop in `write` can leave some rows written: re-running `apply` is safe,
because rows that already match are left alone. `check` exiting 1 on drift is a result, not a
stop.

**`apply` needs `--confirm`.** Without it, `apply` prints the same plan and writes
nothing. Notion writes are user-visible and not cheap to undo at scale, so the
two-step gate is deliberate — the same shape `product:record-ideas` uses. `plan`
prints *every* row it would touch, not a sample, so the preview and the write
cannot disagree.

## Apps are parameters, not branches

`APPS` at the top of the script holds each app's repo, knowledge folder, GitHub
base URL and database id. Both are filled in.

**One thing did need a second change: the Domain taxonomy is per-app.** Aurie's
twelve filed **67 of Aura's 133** screens under `other` — Aurie has no player,
content library, coaching, live or community surface, and Aura's conversational AI
is one embedded area rather than the whole product. `AURA_DOMAIN_RULES` brings that
to **4**. Nine names are spelled exactly as Aurie's so the boards stay comparable
where the surfaces correspond; seven are Aura's own. This matters beyond tidiness:
the loop's collision gate and its ideation priors both group by domain, so an app
filed under `other` makes them useless.

An app with **no screenshot store is a supported shape**, not an error. Aura has
139 screen documents and no `screenshots/` directory; every screen simply syncs
without an image.

## Four things that are easy to get wrong

Each of these was wrong in a first draft and is now handled:

1. **Doc names and store keys differ.** `welcome` vs `welcome_screen`. Fold with
   the same rule `sim_screenshots._norm()` uses — strip non-alphanumerics *and* a
   trailing `screen`. Without it, 8 of Aurie's 69 screenshots never find their
   document and 8 screens look uncaptured.
2. **Status comes from `sim_screenshots.py check`, never from inference.** The
   index's `issues` field holds capture-*quality* heuristics (`keyboard`,
   `dimmed`), not staleness. Inferring from it marked 32 screens stale when 16
   are.
3. **A `files` property renders as a chip.** To actually see the screenshot when
   you open a row, the image must also be a block in the page body. The script
   writes both, and replaces the blocks rather than appending to them.
4. **Three pictures per screen, one per sky.** Aurie's app is locked to light
   mode but reads the time-of-day sky, so dusk and night *are* its dark screens.
   The store keys them `<key>` (anytime), `<key>_light` (midday) and
   `<key>_dark` (night); `VARIANTS` at the top of the script is that list, and
   the row carries all three as files and as captioned body blocks.

   **The combined sha is the load-bearing part.** `Capture sha` is every
   variant's sha joined, not the anytime one. With the anytime sha alone a
   light- or dark-only re-capture left the row looking clean, so two thirds of
   the store could drift while `check` reported "in sync". `row_matches` also
   compares the file *count*, which is what catches a row still holding one
   picture from before this existed.

   Aura's variant axis is locale rather than sky, so its port changes
   `VARIANTS`, not the code.
5. **Comparing only the capture sha hides real drift.** A screen that goes stale,
   changes its gate or gains a trap keeps the same screenshot — and so the same
   sha — so a sha-only check leaves Notion on the old text forever while
   reporting "in sync". Every synced field is compared; the sha decides only
   whether the *image* needs re-uploading. Note that an empty sha must also equal
   an empty sha, or the blocked screens are rewritten on every run.

Also: Notion rejects any single rich-text node over 2000 characters, and several
`How to reach` and `Traps` sections exceed it. The script never truncates and chunks into
1900-char nodes, because the back half of a trap is usually the
useful half — and note that any earlier truncation in `clean` would defeat that
chunking entirely.

## Schema it writes to

`Screen` (title, and the join key — the repo knows no other id), `Identifier`
(the accessibility id, which is not always the name: `package_paywall_webview`
renders as `paywall_web_screen`), `Screen ID` (Notion `unique_id`, `SCREEN-n`),
`Domain` (the twelve options shared verbatim with Aurie Features, so the two
databases are joinable), `Category`, `Status`, `Screenshot`, `How to reach`,
`Experiment gate`, `Traps`, `Blocker`, `Capture sha`, `Capture issues`,
`Last captured`, `Source doc`, `Features` (relation).

## Related

- `data:sync-experiments` — the same shape for the experiment catalogue
- `product:record-ideas` — the other sim-test → Notion projection
- Aurie's `sim_screenshots.py` — the capture store this reads
