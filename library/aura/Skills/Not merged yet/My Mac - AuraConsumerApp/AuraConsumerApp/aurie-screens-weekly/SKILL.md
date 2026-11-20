---
name: aurie-screens-weekly
description: The weekly Aurie screens routine, run unattended every Monday at 13:00 by the `aurie-screens-weekly` scheduled task. In its own worktree and simulators it harvests every Aurie screen in all three skies (anytime, light, dark), reviews and stores the pictures, opens the PR, then syncs the Notion Screens database (`data:sync-screens`) and the Aurie Claude Design project (`product:sync-design-system`) from that worktree, and reports on Linear. Use when the scheduled task fires, or when the user says "run the weekly screens routine", "run aurie-screens-weekly", "do the Monday screens sync now", or "harvest and sync all Aurie screens".
---

# Aurie screens — weekly routine

One run keeps three things in step, with nobody driving:

| Step | Writes |
|---|---|
| Harvest all screens, 3 skies | `AuraConsumerAppSimTests/knowledge/screenshots/` on a branch → PR |
| `data:sync-screens` | Notion AI Knowledge › Products › Aurie › Screens |
| `product:sync-design-system` | the **Aurie** Claude Design project (`963e5951-4785-4308-adfa-2ec8bef9b69d`) |

**Standing decisions (Jerome, 2026-09-26):** run in its own worktree, never the main
checkout; sync Notion and Design **right after** the harvest, from the harvest branch,
without waiting for the PR to merge; Mondays 13:00. Aura gets its own routine later
(its variant axis is locale, and it has no Design project yet).

**This skill is the approval for its own writes.** `sync-screens apply --confirm` and
the Design upload normally wait for a human yes; the user approved this weekly publish,
so the run goes ahead — **inside the limits in each step**. Outside them (wrong project,
counts that shrank, a failed gate) it stops that step and reports instead.

Follow `screenshot-harvest` and `sim-test` for everything not restated here
(simulator ownership, safety rules, never tap a real payment).

## 0. Setup (about 15 minutes, most of it the first build)

```bash
MAIN=/Users/jeromecurlier/Development/Aura/Mobile/AuraConsumerApp
WT=$MAIN/.claude/worktrees/aurie-screens-weekly
AIN=/Users/jeromecurlier/Development/Aura/AI/AINative
AINWT=$AIN/.claude/worktrees/screens-routine
SIM=$WT/AuraConsumerAppSimTests            # every sim-test script and the store live here
DATE=$(date +%Y-%m-%d)
```

**Always use these absolute paths.** Every shell (and every agent) starts in the main
checkout, and a `cd` does not carry over to the next command. A relative
`Scripts/…` or `knowledge/…` either fails or, worse, runs against the main checkout,
which this routine must never touch, and the `--repo $WT` syncs then publish last
week's pictures. Commands that must run inside the worktree start with
`cd $SIM && …` in the same command.

0. **Disk.** A three-sky harvest exhausted 44 GB of headroom once and took the
   machine to 235 MB free, which stops the agent running any command at all — the
   Bash tool writes its output file before it executes. Check before anything else:

   ```bash
   RD=$(ls -d ~/.claude/plugins/cache/aura-tools/eng/*/skills/reclaim-disk/scripts/reclaim_disk.py \
        2>/dev/null | sort -V | tail -1)     # $CLAUDE_PLUGIN_ROOT is empty in a plain shell call
   python3 "$RD" check-space --need-gb 80 --repo $MAIN
   ```

   Exit 0, carry on. Exit 2, it prints what is reclaimable: report it, reclaim the
   safe sources with `reclaim --confirm`, and only then start. If it is still under
   the floor after that, stop and report rather than starting a run that will die
   in the middle. (`eng:reclaim-disk`.)
1. **Worktree.** Create it once (`git -C $MAIN worktree add $WT -b screens-weekly/$DATE origin/main`),
   then each week: `git -C $WT fetch origin && git -C $WT switch -C screens-weekly/$DATE origin/main`.
   It is this routine's own; if it has uncommitted changes, they are last week's
   leftovers — report them and discard only files under
   `$SIM/knowledge/screenshots/`, `$SIM/users.json` and `$SIM/Sessions/`. Copy the gitignored config per
   `.claude/rules/config.md` (real copy of `GoogleService-Info.plist`, symlinked
   `Secrets*.xcconfig`), then `make proj`.
2. **Last week's PR.** `gh pr list --search "head:screens-weekly/ is:open"`. If one is
   still open it is superseded by this run: comment "Superseded by this week's run" and
   close it (only PRs from `screens-weekly/*` branches — never anyone else's).
3. **AINative scripts** from a detached worktree at `origin/main`, not the shared
   checkout (it can be on any branch): create once with
   `git -C $AIN worktree add --detach $AINWT origin/main`, then each week
   `git -C $AINWT fetch origin && git -C $AINWT checkout --detach origin/main`.
   Both scripts need `--repo` (AINative ≥ data 3.59.0 / product 3.52.0); if
   `grep -- --repo` finds it missing, stop the sync steps and report.
4. **Simulators**, one per sky, all this routine's own:
   ```bash
   cd $WT && A=$(bash $SIM/Scripts/sim_start.sh | tail -1)                                  # anytime
   cd $WT && L=$(SIM_SESSION_NAME=aurie-screens-weekly-light bash $SIM/Scripts/sim_start.sh | tail -1)
   cd $WT && D=$(SIM_SESSION_NAME=aurie-screens-weekly-dark  bash $SIM/Scripts/sim_start.sh | tail -1)
   cd $WT && bash $SIM/Scripts/sim_install.sh --session $A                                  # builds
   cd $WT && bash $SIM/Scripts/sim_install.sh --session $L --no-build
   cd $WT && bash $SIM/Scripts/sim_install.sh --session $D --no-build
   ```
   A build failure stops the run: report it, touch nothing else.
5. **Member accounts**, one per sky (flows write to the account, so skies never share):
   anytime `<redacted-email>` (the recipes' member), light
   `<redacted-email>`, dark `<redacted-email>`. The QA premium
   grant lasts a month: `python3 $SIM/Scripts/sim_grant_premium.py --email <each> --refresh-only`,
   and `--force` a new grant for any that lapsed (it records `$SIM/users.json` at once).
   The `journal` flow builds each member's journal dated today before it runs
   (`Scripts/sim_daily_journal.py`, IntelligenceService `POST /ai/journal/daily/:userId`).
   That needs `AURA_USER_TOKEN` (admin) in `~/.claude/settings.json` `env`; without it the
   flow falls back to the newest previous card and `journal_screen` has no hero.

## 1. Harvest (about 1–1.5 hours)

Spawn **one agent per simulator** (`model: sonnet`, background), **about two minutes
apart**: each sky's onboarding flow creates an account and writes `users.json`, and
three writes at once can lose one. Each prompt inlines:
its UDID and sky, the absolute `$SIM` path, "do not build, install, erase, boot or
shut down any simulator, never touch another UDID, do not run `store`, do not commit,
never tap a real payment", and its commands, each run with `run_in_background` and
waited on:

```bash
# Practices first: Prayer and Journaling on, the custom practice created. The practice
# screens need them, `--all` leaves setup flows out, and the flow is safe to repeat
# (it removes a duplicate practice it would have made).
cd $SIM && python3 -u Scripts/sim_capture.py run --section setup --variants <sky> --udid <UDID> --member <member>
cd $SIM && python3 -u Scripts/sim_capture.py run --all --variants <sky> --udid <UDID> --member <member>
```

then the **one-shot surfaces** on fresh premium accounts (QP intro, rating sheet and
captions tooltip show once per account; an account from `sim_provision_user.py` has none
of them used). The Aurie Care flow is one-shot too: it joins the waitlist, and the join
is kept on the account (`oneShot` in `recipes.json` keeps it out of `--all`). Provision
**one account per sky**, three in all, **one at a time** first (they write `users.json`),
grant premium, confirm with `--refresh-only`, then give each agent:

```bash
cd $SIM && python3 -u Scripts/sim_capture.py run --flow "quick processing" --flow "captions tooltip" \
  --flow "aurie care" --variants <sky> --udid <UDID> --member <fresh account>
```

The Care upsell (`aurie_care_upsell_intro`, `aurie_care_upsell_on_the_list`) has no flow: it
only shows on a new account as its first-time paywall closes, and "You're on the list" needs a
card saved on a dev build. Capture those two by hand.

(`cd $SIM && python3 Scripts/sim_provision_user.py --note "weekly one-shot (<sky>)" --json`
makes each account.)

Each agent reports: pass/fail, its run files and contact sheets, every `shot` / `skip` /
`ERR` line, and — on a failure — the error lines and what the last screenshot shows.
A flow that fails on a new first-run cover or an app bug is retried **once**; after
that, carry on and report it.

### Give each agent one command, and watch the work rather than the agents

**An agent stalls silently between commands.** Told to run several long commands in
order — "each with `run_in_background`, wait for it to finish before the next" — it
reliably runs the first, reports "I'll wait for the notification", and never fires the
rest. It does not know it is stuck, so it never reports a problem. This happened twice
in the 2026-09-28 run, with two agents each sitting on unrun commands while nothing at
all was happening on their simulators.

So:

- **One long command per agent.** When a sky needs a setup pass and then `--all`, that is
  two agents in sequence, or two `run` calls driven from here — not one agent holding a
  numbered list.
- **Never read silence as progress.** An agent that has said nothing for ten minutes is
  as likely stalled as busy. Check the work, not the worker:
  ```bash
  ps aux | grep -c "[s]im_capture.py"     # 0 with agents "waiting" means stalled
  ```
  A `Monitor` on that count reports when the real work ends, which an idle agent never
  will. Beware the gap between one agent's commands, though: a momentary zero is not
  necessarily the end. Confirm against the captured-frame count before concluding.
- **When an agent does stall, stop it and run its remaining commands from here.** That is
  what unblocked both stalls in the 2026-09-28 run, and it costs one command each.

## 2. Review and store

Read **every contact sheet** yourself. For each changed frame:

- **Store** it when the UI really changed (layout, copy, a new element, a new screen).
- **Skip** it (`store --skip <key>`) when only these differ: the clock's sky (anytime),
  live text (chat, QP replies, summaries), a system overlay (Save Password, the
  "slide to type" keyboard tip, a permission alert), or the wrong screen.

```bash
cd $SIM && python3 Scripts/sim_capture.py store --run <run file> [--only <key>]... [--skip <key>]...
cd $SIM && python3 Scripts/sim_screenshots.py check
cd $SIM && python3 Scripts/sim_screenshots.py ack --verified
```

`check` must end with nothing STALE, UNVERIFIED, missing or duplicated, except items
you name in the report with the reason, and `auditable:` must read the full store —
a picture with no measured rows beside it is one `audit` cannot report on, and those
rows only exist while the session that made them does. **App bugs seen while reviewing** (legibility,
overlap, clipped text, a screen that will not load) are filed as their own Linear bug
under the current cycle's Bugs parent, with the screenshot attached (crop plus the full
frame), then listed in the report.

## 2b. Audit the store for contrast

```bash
cd $SIM && python3 Scripts/sim_screenshots.py audit --verbose
```

Reports the rows measured at capture time, by the `ui` analyzer's own maths, over
every screen in all three skies. The numbers are measured; the call is not. Before filing anything, settle the two
judgements the dump cannot make — large text (≥18 pt, or ≥14 pt bold) is on 3:1, and
a glyph-only button is non-text, also 3:1 — and drop anything the report already
marked low confidence. Confirmed violations are filed as Bugs under the current
cycle's Bugs parent with the crop and the full frame attached, the same as any visual
bug found in review.

Compare the element count against last week's on ENG-1426. A jump after a colour
change is the signal this step exists for; a jump with no colour change in the week
is more likely a capture problem than 30 new violations — check a couple by eye
before filing.
A change to `analyzers/contrast.py` in the week moves the count too — the one that
stopped dropping anti-aliased text took it from 53 to 442 — so read the git log for
that file before blaming the capture.

## 3. PR

Commit in the worktree only:
`git -C $WT add AuraConsumerAppSimTests/knowledge/screenshots AuraConsumerAppSimTests/users.json`
(images, `index.json`, `pins.json`, accounts), check `git -C $WT diff --cached --stat`,
then `git -C $WT commit` with a message naming the counts (stored / verified / skipped)
and `git -C $WT push -u origin HEAD`. Push, open a **draft**
PR titled `chore(sim-tests): weekly Aurie screens <DATE> [ENG-1426]` whose body lists
what changed per sky and what was skipped, and post `bugbot run`. Fix and re-run BugBot
until clean (at most 5 rounds); mark ready once CI passes. Never merge it — Jerome does.

If nothing changed (every frame matched), skip the PR and say so; the syncs still run.

## 4. Sync Notion

```bash
S=$AINWT/plugins/data/skills/sync-screens/scripts/sync_screens.py
python3 $S plan  --app aurie --repo $WT
python3 $S apply --app aurie --repo $WT --confirm
python3 $S check --app aurie --repo $WT        # must print "in sync", exit 0
```

**Limits:** `plan` must show 80 or more documents and no orphaned rows. If it would
create or update more than 20 rows while the harvest stored fewer than 10 pictures,
something other than this week's pictures moved — stop and report the plan instead of
applying.

## 5. Sync Claude Design

```bash
B=$AINWT/plugins/product/skills/sync-design-system/scripts/build_bundle.py
OUT=$(mktemp -d)/aurie-ds
python3 $B --app aurie --repo $WT --out $OUT     # prints tokens / screens / cards
```

Then upload with the **DesignSync** tool, per `product:sync-design-system`:

1. `get_project` on `963e5951-4785-4308-adfa-2ec8bef9b69d`: it must be the project named
   **Aurie** with `type: PROJECT_TYPE_DESIGN_SYSTEM`. Anything else: stop and report.
2. **Limits:** the build exited 0 (the token completeness gate passed), `screens` equals
   the store's screen count, and neither `tokens` nor `cards` dropped by more than 5
   from last week's report on ENG-1426. Otherwise stop and report the counts.
3. `finalize_plan` with `localDir: $OUT` and the writes list from
   `product:sync-design-system` (`README.md`, `tokens.css`, `tokens.json`,
   `_adherence.oxlintrc.json`, `_ds_manifest.json`, `preview/*.html`, `fonts/*.otf`,
   `uploads/*.png`), `deletes: []`.
4. `write_files`, one `localPath` entry per file, in batches of at most 256.

## 6. Report and clean up

Comment on [ENG-1426](https://linear.app/aura-health/issue/ENG-1426) with:
date, PR link (or "no changes"), stored / verified / skipped counts per sky with a
one-line reason per skip, the contrast audit (elements below the bar, screens
affected, how many pictures were auditable), bugs filed, the Notion sync result, and
the Design build counts (tokens, screens, cards) — next week's run compares against
these. Attach the
contact sheets of stored frames. Then shut down the three simulators
(`sim_shutdown.sh --session <UDID>`). Keep both worktrees; next week reuses them.

A run that stopped early still reports: which step, why, and what was left untouched.
