---
name: screenshot-harvest
description: Capture, review and store the app's screenshots in every variant (anytime, light and dark sky) from the per-screen recipes in the repo, then open the PR and post the images to Linear. By default it captures only the screens the current branch touches; it can also do one section of the app (onboarding, paywall, home/feed, …) or every screen. Use when the user says "harvest screenshots", "update the screenshots", "capture the screens for this PR", "refresh the screenshot store", "get screenshots for <feature>", "which screenshots are stale", or after merging a UI change that should be pictured.
---

# Screenshot harvest

## What it is

`AuraConsumerAppSimTests/knowledge/screenshots/` holds one picture per screen per
variant: `<screen>.png` (anytime: the clock's sky), `<screen>_light.png` and
`<screen>_dark.png`. The Notion Screens board, design renders and reviewers read
them. This skill keeps them current.

Beside each picture are the two things `audit` runs on, under the same key:
`screenshots/measurements/<key>.json` — the contrast rows, measured when the capture
was taken — and `screenshots/hierarchy/<key>.txt`, the XCTest accessibility dump the
same observation produced. That is what lets the audit cover the whole store rather
than the screens a live run happened to reach.

**The measurement is not made from the stored picture, and does not have to match
it.** The two have different lifetimes on purpose: the picture changes when the *UI*
changes, the rows on every capture. Tying them together meant re-storing a 1 MB PNG
whenever a journal entry or a chat reply moved — churn on exactly the screens that
change most often. Now a content-only capture refreshes the rows and the dump and
leaves the picture alone, so the harvest diff shows design work and nothing else.

The price: changing the maths no longer re-scores what is already stored — it takes
effect on screens captured since. The weekly harvest re-captures everything, so that
lag is a week at most.

It is built **on the sim-test harness**, not beside it: the same runner
(`AdHocSimTest`), the same `sim_start.sh` / `sim_install.sh` / `sim_test.sh`, the
same `state.json` / `action.json` protocol. The difference is who decides each
step: a **recipe** replays a fixed list of ops instead of an agent choosing, so a
capture comes out the same every time. Setup, simulator ownership and safety rules
are the `sim-test` skill's — follow them.

| File | Role |
|---|---|
| `knowledge/screenshots/recipes.json` | Flows: account, launch args, experiments, ops; per-screen tolerance; `watch` paths for screens whose key is not an identifier |
| `knowledge/screenshots/variants.json` | The variant axis (Aurie: `-SIM_SKY`; Aura will use locale) |
| `Scripts/sim_drive.py` | Runs one flow on the harness; `shot:<key>` writes `labels.json` into the session |
| `Scripts/sim_capture.py` | Picks flows for a scope, runs them per variant, switches accounts, writes a contact sheet; `store` pins and harvests |
| `Scripts/sim_screenshots.py` | The store: `check`, `pin`, `harvest`, `ack`, `review`, `remove`, `list` |

## Scope — ask only if the request is ambiguous

| User says | Scope flag |
|---|---|
| nothing specific, inside a branch with changes | `--pr` (default): screens whose watched paths the branch changes |
| a feature or area ("the paywall", "onboarding") | `--section <name>` — `onboarding`, `home/feed`, `conversation`, `paywall`, `grounding`, `journal`, `affirmations`, `profile`, `settings`, `growth/referral` (the Notion Domain names) |
| one or a few screens | `--screen <key>` (repeatable, knowledge-file stem) |
| one flow by name (e.g. a one-shot surface on an account you just made) | `--flow "<name>"` (repeatable; with a scope it narrows it), often with `--member <email>` |
| "all", "everything", "refresh the store" | `--all` (2–3 hours) |

The full `--all` harvest, plus the Notion and Claude Design syncs, runs every Monday
at 13:00 as the `aurie-screens-weekly` routine (its own worktree and simulators).

**Always all variants** (`anytime,light,dark`) unless the user narrows it. A PR
that changes a screen changes it in every sky.

## Check the disk first

A full harvest writes tens of GB across three simulators. On 2026-09-29 one
exhausted 44 GB of headroom and took the machine to 235 MB free — which does not
merely fail the run: the Bash tool writes its output file before it executes, so
the agent could no longer run any command at all, including a cleanup.

```bash
# The eng plugin's install cache — $CLAUDE_PLUGIN_ROOT is only set while Claude Code
# runs a plugin's own script, so it reads empty in an ordinary shell call.
RD=$(ls -d ~/.claude/plugins/cache/aura-tools/eng/*/skills/reclaim-disk/scripts/reclaim_disk.py \
     2>/dev/null | sort -V | tail -1)
python3 "$RD" check-space --need-gb 60 --repo "$(git rev-parse --show-toplevel)"
```

Exit 0 and carry on. Exit 2 and it prints what is reclaimable — read that, get a
yes, and run the same script with `reclaim --confirm`. Do not start a full harvest
under the floor; the run will die somewhere in the middle and take the simulators
with it. (`eng:reclaim-disk`.)

## Workflow

1. **Plan** — nothing starts:
   ```bash
   python3 AuraConsumerAppSimTests/Scripts/sim_capture.py plan [scope]
   ```
   It lists the screens in scope, the flows that cover them, and any screen with
   **no flow** — add a flow to `recipes.json` first (see "Adding a screen"). Read
   the `needs:` notes: account state a flow cannot create itself.
2. **Simulator and build** — per the `sim-test` skill: this session's own
   simulator (`sim_start.sh`), then `sim_install.sh --session "$SID"` from the
   branch being pictured. The capture uses whatever is installed.
3. **Run** — in the background (flows take 1–3 min each, onboarding ~8):
   ```bash
   python3 AuraConsumerAppSimTests/Scripts/sim_capture.py run [scope] --udid "$SID"
   ```
   Ends with a run file and a **contact sheet** (`Sessions/capture-sheet-*.png`)
   of only the frames that **look different from the store** (or are new), each
   titled with its pixel difference. Frames that match the stored picture need no
   review: `check` counts them as verified, and `store` leaves their picture alone.
   `sim_capture.py sheet [--run <file>] [--all]` rebuilds the sheet (`--all`: every
   frame).
4. **Review the contact sheet — every frame on it.** Reject captures with a system
   prompt on top (Save Password, microphone), a sheet mid-animation, a spinner, or
   the wrong screen. For each reject, fix the recipe (a `sleep`, a `?tap:Not Now`,
   a dismiss) and re-run that screen (`--screen <key>`). Do not store a bad frame.
   Raise real app bugs you see (legibility, overlap, clipped text) with the user
   and ask whether to prioritise them before carrying on.
5. **Store** the reviewed run:
   ```bash
   python3 AuraConsumerAppSimTests/Scripts/sim_capture.py store [--only <key>]... [--skip <key>]... [--all]
   python3 AuraConsumerAppSimTests/Scripts/sim_screenshots.py check
   python3 AuraConsumerAppSimTests/Scripts/sim_screenshots.py ack --verified
   ```
   `--only` and `--skip` take a recipe key (every variant) or the store key shown on
   the contact sheet (e.g. `home_screen_light`). `--skip <key>` rejects a frame you turned down in review: it is not stored, and it
   is marked in its session so `check` never reads it as a newer capture. `store`
   only writes frames that differ from the stored picture; `--all` writes every frame.
   A frame that matches the store still gives up its measurement and its dump: both
   are written from *this* capture, which is the frame actually on screen now, while
   the picture stays as it is. This run's session is the only place they exist —
   `Sessions/` is gitignored and cleaned, so by the next harvest they are gone.
6. **Audit** the stored pictures for contrast:
   ```bash
   python3 AuraConsumerAppSimTests/Scripts/sim_screenshots.py audit [--sky dark] [--verbose]
   ```
   Reads the rows stored at capture time, which the `ui` analyzer's own maths produced
   (`analyzers/contrast.py`) — there is no second implementation, and nothing is
   re-measured here. It reports elements below the bar per screen and sky, and exits 0:
   it is a report, not a gate (`--strict` to exit 1). `--verbose` prints each
   measurement's capture date. File confirmed violations as Bugs with the crop and the
   full frame attached.
7. **PR** — commit `knowledge/screenshots/` (images, `index.json`, `pins.json`)
   plus any recipe changes; PR as a draft, `bugbot run`, ready when clean (repo PR
   rules). If the harvest belongs to a Linear card, post the images on the card:
   upload each PNG with Linear's `fileUpload` mutation and add one comment with a
   table per surface (Anytime | Light | Dark).

## What the audit can and cannot decide

`audit` measures; it does not judge. What it hands back to a person, and what filing
a bug without settling it looks like:

* **`confidence: low` rows are not findings** and are dropped before you see them. A
  crop is `low` when it has neither a flat background nor a solid-coloured foreground —
  a gradient orb or a photo — or when nothing in it stands clear of the background (an
  empty field, a crop of the wrong frame). Either way the ratio is arithmetic on two
  colours that were never a foreground and a background.
* **Large text is on 3:1, not 4.5:1** — ≥18 pt, or ≥14 pt bold. The XCTest tree carries
  no font size, so everything textual is held to 4.5:1 and a heading at 3.2:1 is
  reported without being a violation. Check the size before filing.
* **A glyph-only control is non-text**, also 3:1, but reaches the tree as a `Button`
  like any other.
* **Elements nothing can see are filtered out**, and they used to dominate. XCTest
  lists the screen under a presented sheet and rows scrolled out of a list, with
  frames that still land inside the picture — `home_journalButton` was measured at
  1.08:1 on `onboarding_choosePractices_screen`, where those pixels are a reminder
  time. `parse_hierarchy_frames(..., visible_only=True)` now drops them using the
  dump's own indentation: a sibling covering the whole window hides everything listed
  before it, and a node must lie inside the intersection of its ancestors' frames.
  Store-wide that took **651 rows over 142 screen-sky pairs down to 201 over 90**.
* **Anti-aliased text is not mistaken for a gradient any more.** The split used to be
  the widest gap in the crop's sorted luminances, and `confidence` was `low` when that
  gap was under 0.02. Anti-aliased text ramps smoothly from background to ink, and the
  lower its contrast the smaller every step — so the text most likely to fail was the
  most likely to be dropped: "Crisis support" at 3.10:1 had a gap of 0.0075, and failing
  `StaticText` rows were 4 reported to 257 dropped. Now the background is the crop's
  dominant luminance (`backgroundShare`), and a crop is kept when either that is a flat
  fill or its ink is one solid colour (`solidInkShare`) — text on a sky gradient has
  the second. The prayer orb has neither and is dropped in all three skies. Store-wide
  that took **53 rows over 36 screen-sky pairs to 442 over 142**, `StaticText` 4 → 223.
* **What is still dropped that might be real:** display type drawn over a gradient with
  soft, glowing edges — "Beautiful work today, Mara" at 2.84:1 on `liveSessionDoneNew_screen`
  and `appstorerating` has neither a flat background nor solid ink. Check those screens
  by eye when the sky or the heading style changes.
* **After changing the maths, run `remeasure`.** `audit` reads rows measured at capture
  time; `sim_screenshots.py remeasure` re-scores every screen whose stored picture is the
  measured capture, and skips (and lists) the few that are not, until they are captured
  again.

**So still open the crop before filing.** `audit --json` carries each row's `box` and
the two colours it compared, so the arithmetic is checkable on its own. The rectangle
is a crop of the *capture that was measured*, which is usually — not always — the
stored picture: on a screen whose content moves, the rows are newer than the PNG. When
the two disagree, capture that screen again and look at the fresh frame. The rows point
at the right element; whether that element's number means anything is still your call.

And the standing limit, unchanged by this: **the comparison still cannot see a font
substitution.** The dump names no typeface and no point size, so an audit that comes
back clean says the colours clear the bar — it says nothing about which font drew them.

## Staleness — a picture is stale only when it looks different

`check` reports, per stored picture:

| Heading | Meaning | Action |
|---|---|---|
| `STALE` | a capture newer than the change **looks different** (pixel diff above the screen's tolerance) | re-store it (`store`) |
| `UNVERIFIED` | the screen's **view code** or its knowledge file changed since the capture, no newer capture yet | capture it (`--screen` / `--pr`) |
| `VERIFIED BY CAPTURE` | code changed, a newer capture looks the same | `ack --verified` records it |
| `NO … CAPTURE` / `HARVESTABLE` | missing variant / a capture exists but is not stored | capture / `store` |
| `DUPLICATES` | an older copy of a screen under another spelling (`signIn_screen` next to `signin_screen`) | `remove --duplicates` — the file-stem copy stays |
| `NO AUDIT INPUT` | stored, but no measurement (or no dump) beside it, so `audit` has nothing to report | capture that screen again — both only exist while their session does |

**What a screen watches.** Only code that draws it: the view files (`Views/`,
`Components/`, `Modals/`, `*View.swift`, asset catalogs) of the feature folder that
declares its identifier (`onboarding_quiz_screen` → `Onboarding/OnboardingQuiz/`),
falling back to the whole feature's view files. View models, coordinators and
services are never watched: they move behaviour, not pixels, and watching them once
flagged ~60 pictures over a two-line fix. A visual change made outside the views is
caught where it is made — that PR's own `--pr` capture re-takes the screen. A recipe
`watch` entry that names a folder gets the same view-only rule.

Only `STALE`, `NO CAPTURE ANYWHERE` and `HARVESTABLE` reach the Notion sync. Keep
`DUPLICATES` at zero: the sync matches screens by folded name, so a leftover copy can
be the picture it shows. `remove <key>...` deletes any entry outright (image, index,
pin) — for pictures that should not be in the store, not for old ones (re-store those). A
commit alone never makes a picture stale. The comparison crops the status bar and
home indicator and tolerates 3% of pixels by default; live content (chat text,
journals, shuffled options) sets its own `tolerance` in `recipes.json`. An anytime
picture in question usually reads as changed (a different clock sky) and is re-stored;
that is expected.

### What the comparison cannot see

`visual_diff` downsamples 8x, blurs, and counts a pixel as different only when its grey
level moves by more than 40 of 255. That is deliberate — it keeps anti-aliasing, a caret
blink and a one-pixel shift from flooding the sheet — but it means **a change carried
entirely in thin strokes is invisible to it**. A whole-app font substitution is exactly
that shape.

Measured on the 2026-09-28 harvest, the day Inter was replaced by SF Pro across every
screen: sign-up on the night sky reported **0.02%** against a picture taken three days
before the swap, and home on light reported **1.01%** with a visibly larger subtitle and
different prompt copy. Both sat under tolerance, so neither reached the contact sheet.

So when a change is typographic — a face, a weight, a text style, a size floor — do not
read a small contact sheet as "nothing moved". The sheet is evidence about **layout
breakage and gross contrast**, which do shift enough pixels to register. It is not
evidence about glyph shapes or contrast ratios. Review those against the code that
changed, or open the before and after side by side for the handful of screens the change
actually touched.

## Adding a screen (or a feature PR that adds one)

A PR that adds a screen must add, in the same PR:
1. `knowledge/screens/<key>.md` (the store definition — `sim-testing.md`).
2. A flow in `recipes.json` that reaches it, with `shot:<key>`. If its key is not
   an identifier in code, add `watch` paths for it.
3. The three captures (`sim_capture.py run --screen <key>` → review → `store`).

Surfaces that need state a test account cannot create get a DEBUG launch argument
(`-SIM_PRESENT_SHEET=…`, `-SIM_PAYMENT_PENDING`, `-SIM_FAIL_PROMPT_RESULTS` — see
`knowledge/rules.md` § Forcing a server state), never a real server change.

## Accounts

| `account` in a flow | What the run does |
|---|---|
| `member` | signs in the recipes' member account (premium via QA code) if needed |
| `new` | signs out, onboards a fresh account; credentials go into `users.json` at once |
| `prelogin` | signs out; the flow never signs up |

One-shot surfaces (QP intro, captions tooltip, practices setup, and the Aurie Care
flows, whose join is kept on the account) show once per account. Flows marked
`oneShot` in `recipes.json` are left out of `--all`; run them by name on a fresh
account, a separate one for each Care flow. When onboarding misses one, re-run just that flow on the account the run
created instead of onboarding again: `run --flow "captions tooltip" --member <new
email>`; for the QP intro, grant premium first (`sim_grant_premium.py --email`), then
`run --screen quickProcessing_intro_screen --member <new email>`.
`knowledge/workarounds.md` § "Capturing one-shot surfaces" says which account
reaches each. Premium grants go through `sim_grant_premium.py` and are
recorded in `users.json` immediately.

## Gotchas

- `swipe:down` does not dismiss sheets; tap the sheet's own button.
- A free account gets the package paywall on every cold launch — `?tap:xmark` first.
- Never tap "Update Payment Method" or anything that reaches a real payment, portal
  or purchase.
- A `shot` that lands on the wrong frame is a recipe problem, not a store problem —
  fix the recipe and re-run; never pin by hand around it.
- After an optional op, guard the shot: `?shot:<key>@<id>`. The guard can also be a
  visible text (`@~Session complete`, prefix match) for a surface no id reaches, or
  alternatives joined with `|` when arms identify it differently.
- A full `--all` run is 2–3 hours on one simulator. For speed, run one variant per
  simulator, each with **its own member account** (`--member`), installing with
  `sim_install.sh --no-build` — `workarounds.md` → "Parallel screenshot capture".
- The app allows two custom practices with the same name, so a practice setup flow
  that runs more than once leaves duplicate rows on the member account. The
  custom-practice setup flow ends with `macro:dedupe-practice:<name>`, and
  `sim_capture.py dedupe-practices --udid <id> [--member <email>] [--name <name>]`
  cleans an account that already has duplicates (it signs in first, then opens Edit
  practices with `dailyPractices3=a`; `b` is control and has no Edit practices).
- Aura: the same tools with its own `variants.json` (locale) and `recipes.json`.
