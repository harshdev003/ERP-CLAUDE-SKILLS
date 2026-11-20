---
name: screenshot-harvest
description: Capture, review and store Aura's screenshots in every variant (the 4.0 design in light and dark) from the per-screen recipes in the repo, then open the PR. By default it captures only the screens the current branch touches; it can also do one section of the app or every screen with a recipe. Use when the user says "harvest screenshots", "update the screenshots", "capture the screens for this PR", "refresh the screenshot store", "get screenshots for <feature>", "which screenshots are stale", or after merging a UI change that should be pictured.
---

# Screenshot harvest

Ported from AuraConsumerApp's skill of the same name (ENG-1410/1417, ENG-1462). Same
tools and protocol; Aura's variants are **design × theme**, not Aurie's time-of-day sky.

## What it is

`AuraSimTests/knowledge/screenshots/` holds two pictures per screen, one per variant:

| Key | Design | Theme | Captured with |
|---|---|---|---|
| `<screen>_v4_light` | 4.0 | light | `--experiment auraDesignChange3=a` `-AuraTheme=light` |
| `<screen>_v4_dark` | 4.0 | dark | `--experiment auraDesignChange3=a` `-AuraTheme=dark` |

The app has one design, 4.0; the legacy design and its pictures were removed
(PROD-7943). The design stays an axis with a single value, so store keys keep their
`_v4_` part. `auraDesignChange3=a` is still forced on every capture: nothing in the app
branches on it, but UserWeb's WebView paywall and the Aurie upsell read the arm to pick
their own design, so a capture that left it to the account could picture the wrong
paywall. The theme is forced through `-AuraTheme`, read by `ios/Aura/AuraTestOverrides.m`
behind the `-AuraUITestOverrides` sentinel and applied **on read** in `ThemeStore` (the
account's stored preference is never written). The axes are declared in
`knowledge/screenshots/variants.json`.

The theme picker offers Auto, Light and Dark only, and Auto resolves to one of those
two, so these two cover everything a user can see. An unsuffixed `<screen>.png` is a
retired "anytime" picture from before both axes were forced — remove it once its screen
has its two replacements (`sim_screenshots.py remove <key>`).

Beside each picture are two audit inputs, both from the capture that was measured:
`screenshots/hierarchy/<key>.txt`, the XCTest accessibility dump the runner took **at
the same instant** (one `app.debugDescription` in `observeAndWriteState`), and
`screenshots/measurements/<key>.json`, the contrast rows measured from that capture's
own picture and dump. The dump is what lets the store be audited for Aura's most common
failure, a11y collapse — an `_screen` id with nothing under it — without re-running the
screen; the rows are what `audit` reports.

A screen whose **content** moved but whose UI did not (a new recommendation row, a
different live event) keeps its stored picture; `store` refreshes only its dump and
measurement from the new capture. So the dump can describe a later capture than the
picture — by design, since nothing reads the two together any more.

It is built **on the sim-test harness**: the same runner, the same `sim_start.sh` /
`sim_install.sh` / `sim_test.sh`, the same `state.json` / `action.json` protocol. A
**recipe** replays a fixed list of ops instead of an agent choosing, so a capture comes out
the same every time. Setup, simulator ownership and safety rules are the `sim-test`
skill's — follow them.

| File | Role |
|---|---|
| `knowledge/screenshots/recipes.json` | Flows: account, launch args, experiments, ops; per-screen tolerance; `watch` paths |
| `knowledge/screenshots/variants.json` | The variant axis (`-AuraTheme`) |
| `Scripts/sim_drive.py` | Runs one flow; `shot:<key>` writes `labels.json` into the session |
| `Scripts/sim_capture.py` | Picks flows for a scope, runs them per variant, switches accounts, writes a contact sheet; `store` pins and harvests |
| `Scripts/sim_screenshots.py` | The store: `check`, `pin`, `harvest`, `ack`, `review`, `remove`, `retire`, `list` |

## Scope — ask only if the request is ambiguous

| User says | Scope flag |
|---|---|
| nothing specific, inside a branch with changes | `--pr` (default): screens whose watched files the branch changes |
| an area ("settings", "the tabs") | `--section <name>` — the `section` of each flow in `recipes.json` |
| one or a few screens | `--screen <key>` (repeatable, knowledge-file stem) |
| one flow by name | `--flow "<name>"` (repeatable; with a scope it narrows it), often with `--member <email>` |
| "all", "everything", "refresh the store" | `--all` |

**Always both variants** unless the user narrows it (`--variants v4_dark`). A PR that
changes a screen changes it in both themes.

Don't put `auraDesignChange3` in a flow's `experiments` — the variant forces it.

## Workflow

1. **Disk** — `knowledge/screenshots/README.md` § "Check the disk before a harvest".
2. **Plan** — nothing starts:
   ```bash
   python3 AuraSimTests/Scripts/sim_capture.py plan [scope]
   ```
   Lists the screens in scope, the flows that cover them, and any screen with **no flow**
   — add one first (see "Adding a screen"). Read the `needs:` notes.
3. **Simulator and build** — per the `sim-test` skill: this session's own simulator, then
   `sim_install.sh --session "$SID"` from the branch being pictured. The capture uses
   whatever is installed. A second simulator takes `sim_install.sh --no-build`.
4. **Run** — in the background (each flow is a fresh session, 1–3 min):
   ```bash
   python3 AuraSimTests/Scripts/sim_capture.py run [scope] --udid "$SID"
   ```
   Ends with a run file and a **contact sheet** (`Sessions/capture-sheet-*.png`) of only
   the frames that look different from the store (or are new), each titled with its pixel
   difference. `sim_capture.py sheet [--run <file>] [--all]` rebuilds it.
5. **Review every frame on the sheet.** Reject a frame with a system prompt on top, a
   launch card, a sheet mid-animation, a spinner, or the wrong screen. Fix the recipe (a
   `sleep`, a `?tap:Not Now`, a dismiss) and re-run that screen (`--screen <key>`). Do not
   store a bad frame. Raise real app bugs you see (contrast, overlap, clipped text) with
   the user.
6. **Store** the reviewed run:
   ```bash
   python3 AuraSimTests/Scripts/sim_capture.py store [--only <key>]... [--skip <key>]... [--all]
   python3 AuraSimTests/Scripts/sim_screenshots.py check
   python3 AuraSimTests/Scripts/sim_screenshots.py ack --verified
   ```
   `--only` / `--skip` take a recipe key (every variant) or a store key from the sheet
   (`dashboard_home_v4_dark`). `--skip` marks the frame rejected in its session, so `check`
   never reads it as a newer capture. `store` writes only frames that differ from the
   stored picture (`--all` writes every one); a matching frame still refreshes the dump
   and contrast rows beside the kept picture.
7. **Audit** the store for contrast:
   ```bash
   python3 AuraSimTests/Scripts/sim_screenshots.py audit [--variant dark] [--verbose] [--json]
   ```
   Reads the stored rows — the `ui` analyzer's own maths (`analyzers/contrast.py`), no
   second implementation. A report, not a gate (`--strict` exits 1). After a change to
   `contrast.py`, `remeasure` re-scores every screen whose picture is the measured
   capture. File confirmed violations with the crop and the full frame.
8. **PR** — commit `knowledge/screenshots/` (images, `hierarchy/`, `measurements/`,
   `index.json`, `pins.json`) plus any recipe changes. AuraSimTests changes always go
   through a PR.

## Parallel capture — one variant per simulator

A section in two variants on one simulator is two passes in a row. Run them side by
side instead: one simulator per variant, each with its own name, so the per-session
rules (`sim-test` skill) keep them apart.

```bash
# One-time: one more simulator next to your own, and the app on it — no rebuild
# (two xcodebuilds on one DerivedData lock its build database).
SIM_SESSION_NAME=harvest-2 bash AuraSimTests/Scripts/sim_start.sh
bash AuraSimTests/Scripts/sim_install.sh --session <UDID> --no-build   # the new UDID

# Then one variant per simulator, in the background:
python3 AuraSimTests/Scripts/sim_capture.py run --section settings --variants v4_light --udid <UDID-1>
python3 AuraSimTests/Scripts/sim_capture.py run --section settings --variants v4_dark  --udid <UDID-2>
```

Each run writes its own run file and contact sheet (the simulator is in the name);
`store --run <file>` each one after review.

- **Check headroom first.** A booted simulator with Aura running takes about 1.5–2 GB;
  two of them want ~4 GB free (`vm_stat`), on top of the disk check above. Other
  sessions' simulators count — `xcrun simctl list devices booted`.
- **Accounts.** Navigation-only flows (settings, tabs, details) can share the member
  account across simulators: they read, they do not write. A flow that **writes** —
  creating a playlist, a journal entry, a reflection, a reservation — needs its own
  member account per simulator (`run --member <email>`), or two runs race on the same
  data and one captures the other's half-finished state.
- **Shut them down when the harvest is done** — `SIM_SESSION_NAME=harvest-2 bash
  AuraSimTests/Scripts/sim_shutdown.sh` — and delete them if they will not be reused.

Measured 2026-10-06: a flow takes about a minute per variant once runners exit promptly
(`-collect-test-diagnostics never`, `workarounds.md`), so four simulators took a section
from ~4 minutes per flow to ~1 when there were four variants; with two, two simulators
halve it.

## What the audit can and cannot decide

`audit` measures; it does not judge. What it hands back to a person:

- **`confidence: low` rows are not findings** and are dropped: a gradient, a photo or a
  flat fill has no meaningful foreground/background split.
- **Large text is on 3:1, not 4.5:1** — ≥18 pt, or ≥14 pt bold. The tree carries no font
  size, so everything textual is held to 4.5:1 and a heading at 3.2:1 is reported
  without being a violation. Check the size before filing.
- **A glyph-only control is non-text**, also 3:1, but reaches the tree as a `Button`.
  Aura's icon-only header buttons (`profile_searchButton`, `sidebar_backButton`) cluster
  just under 4.5:1 for exactly this reason — check whether they clear 3:1 before filing.
- **Elements nothing can see are filtered out** — the screen under a sheet, rows
  scrolled out of a list — using the dump's own indentation (`visible_only=True`).
- **It cannot see a font substitution.** The dump names no typeface and no size; a clean
  audit says the colours clear the bar, nothing about which font drew them.

## Staleness — a picture is stale only when it looks different

| Heading | Meaning | Action |
|---|---|---|
| `STALE` | a capture newer than the change **looks different** (above tolerance) | re-store it |
| `UNVERIFIED` | the screen's view file or knowledge file changed since the capture landed | capture it |
| `VERIFIED BY CAPTURE` | code changed, a newer capture looks the same | `ack --verified` |
| `NO LIGHT/DARK CAPTURE`, `HARVESTABLE` | missing variant / captured but not stored | capture / `store` |
| `DUPLICATES` | the same screen under two spellings (`dashboard_screen` and `dashboard_home`) | `remove --duplicates` — the knowledge-file stem stays |
| `NO AUDIT INPUT` | stored without a dump or measured rows beside it | capture it again (or `harvest` while its session is on disk) |

Measured **from the commit that landed the picture**, not its date — so a capture PR that
also edits the screen does not mark its own screens stale on merge.

**What a screen watches.** The file carrying its root testID (`git grep`, because Aura's
screen files are not named after their identifiers), plus the view files of its sub-folder
when it lives in one (`src/screens/familyPlan/`). Only `src/screens`, `src/components`,
`src/reuseableUI` and `src/img` count as view code. Stores, logic, services and requests
never do: they move behaviour, not pixels. A `watch` entry in `recipes.json` overrides
this for a key that is not a testID.

**What the comparison cannot see.** `visual_diff` downsamples 8×, blurs, and ignores grey
changes under 40/255, so a change carried in thin strokes — a font, a weight, a size — can
read as nothing. Review typographic changes against the code, not the sheet.

## Adding a screen

A PR that adds a screen must add, in the same PR:

1. `knowledge/screens/<key>.md`, declaring its root: ``Root `testID`: **`<id>_screen`**``.
2. A flow in `recipes.json` that reaches it, ending in `shot:<key>@<id>` — the `@<id>`
   guard stops a missed tap from storing the wrong picture.
3. The two captures (`run --screen <key>` → review → `store`).

**Overlay-mounted screens** (rendered through `Dashboard.renderSecondaryView`) never
report their own `state.screen`. A recipe does not need it: guard the shot on a label or
element id the overlay does expose, or on nothing, and confirm on the sheet. Screens with
no in-app entry point open by universal link: `open:<destination>[/<objectId>]`
(`workarounds.md` § universal links).

## Accounts

| `account` | What the run does |
|---|---|
| `member` | signs in the recipes' member account (premium via `QA_AURA_SIM_TEST_1MONTH`) |
| `new` | signs out, makes a fresh account; credentials go into `users.json` at once |
| `prelogin` | signs out; the flow never signs up |

Premium lapses monthly — `sim_grant_premium.py --email <member>` re-grants it and records
it in `users.json`. Two simulators in parallel each need **their own** member account
(`--member`): flows write to the account.

## Gotchas

- Never tap anything that reaches a real payment, a Stripe sheet, or a purchase.
- `swipe:down` does not dismiss sheets; tap the sheet's own button.
- The podcast launch card and the attribution survey can open over Home after a sign-in;
  `macro:covers` (run automatically at the start of every flow) clears them.
- A universal link fired twice with the identical URL is dropped the second time, and one
  fired mid-transition is lost. `open:` uses a fresh objectId and settles first.
- The capture's variant comes from its session's launch argument. A frame cannot be filed
  under the wrong theme by hand — `pin` refuses it.
