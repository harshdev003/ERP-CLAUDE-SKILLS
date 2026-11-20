---
name: aurie-screens-weekly
description: The weekly Aurie screens routine, run unattended every Monday at 13:00 by the `aurie-screens-weekly` scheduled task. In its own worktree and simulators it harvests every Aurie screen in all three skies (anytime, light, dark), reviews and stores the pictures, opens the PR, then syncs the Notion Screens database (`data:sync-screens`) and publishes the Aurie Design System artifact (`product:sync-design-system`) from that worktree, and reports on Linear. Use when the scheduled task fires, or when the user says "run the weekly screens routine", "run aurie-screens-weekly", "do the Monday screens sync now", or "harvest and sync all Aurie screens".
---

# Aurie screens — weekly routine

One run keeps three things in step, with nobody driving:

| Step | Writes |
|---|---|
| Harvest all screens, 3 skies | `AuraConsumerAppSimTests/knowledge/screenshots/` on a branch → PR |
| `data:sync-screens` | Notion AI Knowledge › Products › Aurie › Screens |
| `product:sync-design-system` | the **Aurie** Design System artifact (<https://claude.ai/artifact/U7eLisVGjZm4JSN989MZrD>), via the Artifact tool |

**Standing decisions (Jerome, 2026-09-26):** run in its own worktree, never the main
checkout; sync Notion and Design **right after** the harvest, from the harvest branch,
without waiting for the PR to merge; Mondays 13:00. Aura gets its own routine later
(its variant axis is locale).

**This skill is the approval for its own writes.** `sync-screens apply --confirm` and
the design system publish normally wait for a human yes; the user approved this weekly publish,
so the run goes ahead — **inside the limits in each step**. Outside them (wrong artifact,
counts that shrank, a failed gate) it stops that step and reports instead.

**Every stop goes through step 6.** "Stop and report", anywhere below, means: run
[step 6](#6-report-and-clean-up--on-every-exit) — stop the harvests, shut down the
simulators, comment on ENG-1426 and raise a follow-up for the stopped step. That is the
only way a run ends, finished or not. Nobody watches this routine, so a run that ends any
other way is a run nobody hears about: the 2026-11-16 run left its harvests and
simulators running and ended with no PR, no syncs, no report and no follow-up.

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
captions tooltip show once per account; an account the onboarding flow just signed up has
none of them used). The Aurie Care flow is one-shot too: it joins the waitlist, and the join
is kept on the account (`oneShot` in `recipes.json` keeps it out of `--all`). Use **one
account per sky**, three in all: the account that sky's onboarding run created this week
(`users.json`, notes "screenshot harvest account (onboarding, <sky>)"). Grant premium to
each **one at a time** (they write `users.json`), confirm with `--refresh-only`, then give
each agent:

```bash
cd $SIM && python3 -u Scripts/sim_capture.py run --flow "quick processing" --flow "captions tooltip" \
  --flow "aurie care" --variants <sky> --udid <UDID> --member <fresh account>
```

The Care upsell (`aurie_care_upsell_intro`, `aurie_care_upsell_on_the_list`) has no flow: it
only shows on a new account as its first-time paywall closes, and "You're on the list" needs a
card saved on a dev build. Capture those two by hand.

Accounts can't be made with a script any more: prod refuses a scripted sign-up, and
`sim_provision_user.py` is retired (`knowledge/rules.md § Test accounts`). If a sky's
onboarding run failed before signing up, run `sim_capture.py run --flow onboarding
--variants <sky>` for that sky first; it records the new account.

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
- **An expired wait is never the end of the run.** Never end your turn while harvests run
  in the background unless a wait is armed. When a wait expires with no event, check the
  work the same way: count the `sim_capture.py` processes and the frames captured so far.
  If processes are still running and frames have grown since the last check, re-arm the
  wait. If no process is running and the harvest has not reported, or the frame count has
  not moved since the last expiry, the harvest has stopped. Treat that as a stop at step 1
  and go to step 6. The 2026-11-16 run ended about 35 minutes after its 30-minute wait
  expired, with nothing reported.

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

## 5. Publish the Aurie design system artifact

The design system lives in one **Design System artifact**,
<https://claude.ai/artifact/U7eLisVGjZm4JSN989MZrD>, published with the **Artifact** tool.
Never use DesignSync or the old Claude Design project (`963e5951-…`) for this step:
standalone Claude Design is closing, and the artifact replaced it (Jerome, 2026-11-18).

```bash
S=$AINWT/plugins/product/skills/sync-design-system/scripts
mkdir -p "$WT/TestResults"   # gitignored; inside the worktree because asset uploads read from the working directory
OUT=$(mktemp -d "$WT/TestResults/aurie-ds.XXXX")
# `&&`: a failed --strict gate must stop here, not fall through to a build that exits 0
python3 $S/extract_tokens.py --app aurie --repo $WT --out "$OUT/m" --strict && \
  python3 $S/to_design_system.py build --app aurie --repo $WT --tokens "$OUT/m/tokens.json" --out "$OUT"
```

`build` prints the counts (tokens per family, type styles, fonts, screens). Then follow
`product:sync-design-system` § "Publishing to a Design System artifact", on this artifact only:

1. `read` the artifact. It must be the Aurie design system the session's account can edit
   (`writer`). Anything else: stop.
2. **Limits:** both scripts exited 0, `screens` equals the store's screen count, and the
   token total did not drop by more than 5 from last week's report on ENG-1426. Otherwise
   stop and report the counts.
3. Upload the screens as assets (`asset: true`, `file_paths` at most 25 a call), then
   `to_design_system.py index --app aurie --repo $WT --uploads … --out "$OUT"`, with
   `--existing` set to the artifact's current `project/design-system.json` (read it first).
4. **One publish** to that artifact's `url`, with `root` = `$OUT`, `file_path` = the index,
   and `files` = every other file under `project/`.

**What this step may never do:** publish to any other artifact, create a new one (a publish
without `url`), share it, change who owns it, pin it, or delete anything from it. The standing
approval above covers this one artifact's content and nothing else.

**If the publish fails, stop this step.** That includes a refused publish, a conflict with
a newer version, the session not being signed in as an account that can edit the artifact,
or a short asset upload. Do not retry with `force`, and do not fall back to DesignSync.
Report it in step 6 and raise the `design` follow-up there, with the error as `--what` and
the ask "publish the built design system from an interactive session".

## 6. Report and clean up — on every exit

Run this on **every** exit: a finished run, a stop at any step, an expired wait you cannot
re-arm, or an error you cannot get past. Do the parts in this order. That way, if the run
dies partway through the report, the simulators have already been freed.

**1. Stop and shut down.** For each of this routine's three UDIDs (`$A`, `$L`, `$D`; never
any other), stop the capture loop first, because it would start the next flow. Then stop
the run it left behind, and shut the simulator down:

```bash
cd $WT && bash $SIM/Scripts/sim_kill.sh --session <UDID> capture
cd $WT && bash $SIM/Scripts/sim_kill.sh --session <UDID> all
cd $WT && bash $SIM/Scripts/sim_shutdown.sh --session <UDID>
```

`capture` matches only Python processes running `sim_capture.py` with `--udid <UDID>`. It
never matches a shell whose command line merely mentions the script, such as your own
`bash -c`. Don't use `pgrep -f` / `pkill -f` on that pattern: it matches the agent's own
shell and kills the cleanup partway through. It would also match other sessions on this
Mac, which run the same scripts.

Five booted simulators (about 19 GB of RAM) were found the day after the 2026-11-16 run.
If the UDIDs were lost, find this routine's simulators by name (`aurie-screens-weekly`,
`-light`, `-dark`) with `xcrun simctl list devices booted`. Never shut one down by the
shared model name.

**2. Report.** Comment on [ENG-1426](https://linear.app/aura-health/issue/ENG-1426) with:
date, PR link (or "no changes"), stored / verified / skipped counts per sky with a
one-line reason per skip, the contrast audit (elements below the bar, screens
affected, how many pictures were auditable), bugs filed, the Notion sync result, and
the design system build counts (tokens per family, type styles, fonts, screens) and the publish result — next week's run compares against
these. Attach the
contact sheets of stored frames. Keep both worktrees; next week reuses them.

A run that stopped early still reports: which step, why, and what was left untouched.

**3. Raise a follow-up for every stopped step.** Do this whether the whole run stopped, or
only one step stopped and the run carried on (for example, Design over its limits). The
ENG-1426 comment alone is not enough, because nobody reads it until the next week. Use
`shared:raise-followup`, kind `blocked`, one key per stopped step:
`aurie-screens-weekly:<DATE>:<step>`, where `<step>` is `setup`, `harvest`, `review`,
`audit`, `pr`, `notion`, `design` or `report`.

```bash
RF=$(ls -d ~/.claude/plugins/cache/aura-tools/shared/*/skills/raise-followup/scripts/followup.py \
     2>/dev/null | sort -V | tail -1)    # $CLAUDE_PLUGIN_ROOT is empty in a plain shell call
python3 "$RF" raise --dry-run --kind blocked \
  --title "Weekly Aurie screens $DATE stopped at <step>" \
  --what "<what ran and what did not: harvest per sky, PR, Notion, Design>" \
  --why "<why it stopped: the failed limit, gate or error, in one line>" \
  --ask "<the exact thing needed to finish or re-run>" \
  --card ENG-1426 --key "aurie-screens-weekly:$DATE:<step>" --session aurie-screens-weekly
```

Read the dry run, then run the same command without `--dry-run`.
- **Exit 1:** a field broke a rule (a person's name, an email, "as discussed"). Fix the
  field and run it again.
- **Exit 2:** a Slack or Linear call failed. Re-running with the same key is safe.
- **No `followup.py` found:** say so at the top of the ENG-1426 comment and in the final
  summary. The run still must not end in silence.

A run that finished every step raises nothing.
