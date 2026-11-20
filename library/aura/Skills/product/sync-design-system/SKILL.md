---
name: sync-design-system
description: >
  Generates an app's Claude Design system from the app itself — design tokens
  parsed out of its theme files (Swift for Aurie, React Native JS for Aura),
  screens from its sim-test capture store, and brand/voice from AI Knowledge. The
  codebase is the system of record; the design system is a projection of it,
  rebuilt rather than maintained. Produces the bundle locally; the agent uploads it
  with the DesignSync tool. Trigger on "rebuild the design system", "sync the
  design system", "update Aurie in Claude Design", "update Aura in Claude Design",
  "the tokens changed", "regenerate the design tokens", or a question about
  whether the Claude Design project still matches the app.
metadata:
  version: "1.22.1"
knowledge:
  reads:
    - Guide/Index
    - Identity/Brand
    - Identity/Apps/Aurie
---

# sync-design-system

## What it does

Reads an app and makes its Claude Design project say the same thing:

| Source | Becomes |
|---|---|
| `Services/Theme/Global/*.swift` | `tokens.css` (CSS custom properties) + `tokens.json` (typed manifest, each token citing `File.swift:line`) |
| the same files | `_adherence.oxlintrc.json` — the token registry lint checks raw hex/px against |
| every `.swift` in the app, minus the theme files | the **token adherence card** — token uses against hard-coded literals, per kind, with the values the app actually writes |
| `Resources/Fonts/*` | embedded `@font-face` declarations, so cards render in the app's real letterforms |
| `knowledge/screenshots/*.png` | a screens gallery, downscaled and grouped by domain — **one tile per screen showing every sky it was captured in** |
| AI Knowledge — Identity › Brand, Identity › Apps › *app* | brand cards: promise, voice, naming, copy rules, experience modes |
| every card the builder emits | `_ds_manifest.json` — the index the Design System pane actually renders from |
| the token manifest + brand facts | `design-tokens-<app>.generated.md` — what `product:designer` reads for values (via `--grounding-out`) |

**The codebase is the system of record.** Nothing is written back to it. Change
the app or the knowledge base and re-run; do not hand-edit the project and
expect it to survive the next rebuild.

### The accessibility cards are computed, not written

`accessibility_cards()` emits two cards under an **Accessibility** group: the bar every
screen must clear, and Aurie's sky rule.

**The bar is read from Notion, not held in the script.** `apps.py` gives each app an
`a11yBaseline` page id; the generator reads the JSON code block on
`Design/Accessibility Baseline` at build time and **fails loudly** if it is missing,
unparseable, or short a key. A hardcoded fallback is precisely the drift this removes —
the cards would keep rendering, quietly citing a bar nobody agreed to. An app with no
`a11yBaseline` gets no accessibility cards, which is right for one whose bar is unsettled.

The same bar is written into the `--grounding-out` file the `product:designer` agent
   reads, under *The bar every design must clear* — so the agent picks tokens that clear
   it rather than having a proposal corrected afterwards.

   That page is also what humans read and what `.claude/rules/accessibility.md` points at,
so the numbers have exactly one home. The platform floors behind them — Apple 44 pt,
Material 48 dp, WCAG AA 24 px, 11 pt minimum text — are sourced under `Design/Platforms`.

**The violation row is derived from the token manifest, not typed in.** The card lists
every type token below the 11 pt floor by reading `tokens.json`, so raising the small
type makes the row disappear on the next build rather than leaving a stale claim behind.
On Aurie today that is three tokens at 10 pt.

### One tile per screen, not one per picture

Aurie's app is locked to light mode but reads the time-of-day sky, so dusk and
night *are* its dark screens. The capture store keys each sky separately —
`<key>` (anytime), `<key>_light` (midday), `<key>_dark` (night) — so a store of
80 screens holds 240 pictures.

The gallery groups them back by base name and renders the skies **side by side
in one tile**, captioned. Iterating the index directly instead would emit 240
alphabetical tiles, which interleaves every screen's dark sky between its
anytime and light one — the comparison the variants exist for becomes the one
thing you cannot do.

The suffix list lives in `screen_cards()` in `build_bundle.py`. An app whose
store has no variants is unaffected: every picture is its own single-sky tile.

## Usage

```bash
S=${CLAUDE_PLUGIN_ROOT}/skills/sync-design-system/scripts

# Tokens only — fast, and the honest check that the parser still covers the app
python3 $S/extract_tokens.py --app aurie --out /tmp/ds

# The whole bundle (runs extract_tokens first)
python3 $S/build_bundle.py --app aurie --out /tmp/ds

# ...from another checkout or worktree (theme, fonts and screenshots all read there)
python3 $S/build_bundle.py --app aurie --repo <worktree> --out /tmp/ds

# ...and refresh the designer agent's token reference in the same pass
python3 $S/build_bundle.py --app aurie --out /tmp/ds \
  --grounding-out ${CLAUDE_PLUGIN_ROOT}/skills/design-idea/references/design-tokens-aurie.generated.md
```

`--grounding-out` must be **absolute** (or plugin-root-anchored). A repo-relative
path resolves against the current directory, so from anywhere but the AINative root
it would write a plausible file into a new nested tree while `product:designer` kept
reading the stale committed one — a miss with no symptom. The generator now refuses a
path whose parent directory does not already exist, which turns that into an error.

`--grounding-out` writes the file `product:designer` reads for **values**. Commit it:
it is generated, but it is read by an agent that has no way to run the generator
mid-task. Regenerate it in the same commit as any theme change, or the agent designs
against a stale palette and nothing flags it.

Then upload with the **DesignSync tool** — a script cannot do this half:

1. `list_projects` / `get_project` — confirm the target is
   `type: PROJECT_TYPE_DESIGN_SYSTEM`. That type is immutable at creation, so
   pushing to a regular project never makes it a design system.
2. **Show the user the plan and wait for a clear yes.** Name the project (by
   title *and* id), the bundle directory, and the file / token / screen / card
   counts the build just printed (`files` is what step 4 uploads). This is the gate — everything after it
   is a remote write that a team can see.
3. `finalize_plan` with `localDir` set to the bundle dir and
   `writes: ["README.md", "tokens.css", "tokens.json", "_adherence.oxlintrc.json",
   "_ds_manifest.json", "preview/*.html", "fonts/*.otf", "uploads/*.png"]`,
   `deletes: []`.
4. `write_files` with one entry per file, each using `localPath` — contents then
   never enter the model's context. **Send it in batches of at most 128 files** —
   the bundle is several hundred files, too many for one call. If a batch times
   out, resend it as two halves rather than whole. Report the files written per
   batch, and stop if the total doesn't reach the build's `files` count.

**DesignSync logs in only from an interactive terminal.** `/design-login` works in
a `claude` session in a terminal, nowhere else. A scheduled or desktop run can
build the bundle but cannot upload it: stop after the build, report the bundle
directory and counts, and say the upload needs a terminal session.

**Why step 2 is not optional.** `finalize_plan` + `write_files` publishes
immediately, to whichever project id is in hand, from whichever `--out` directory
is in hand. A stale bundle dir or a mistyped project id both publish silently and
the result is visible to everyone the project is shared with — which, in practice,
is the whole organisation. The bundle is cheap to rebuild; a wrong publish is not
cheap to explain. Confirm before writing, the same shape as `data:sync-screens`'
`apply --confirm`.

## Publishing to a Design System artifact (Aura and Aurie)

Standalone Claude Design (claude.ai/design, the DesignSync path above) closes by
2026-12-14. A design system now lives in Claude as an artifact made from the **Design
System** type, its content in files under `project/`. An app whose `apps.py` entry has
`designSystemArtifact` is published there, never through DesignSync:

```bash
S=${CLAUDE_PLUGIN_ROOT}/skills/sync-design-system/scripts
python3 $S/extract_tokens.py --app aura --out "$OUT/m" --strict
python3 $S/to_design_system.py build --app aura --tokens "$OUT/m/tokens.json" --out "$OUT"
```

`build` writes `project/tokens.json` in the type's grammar (every family a list of
`{name, value, usage}`; Aura: colours per theme, dark first, roles as aliases of `palette-*`;
Aurie: one light theme, because its skies resolve in code through the sky roles, a usage note on every token from the source line's own comment,
contrast on canvas measured for text roles), the first `project/tokens.css`, the README
brand book and cover from `references/design-system-<app>.md` / `-cover.html`, the font
files, and `screens.json`: the current captures (`screenVariants`), downscaled. It refuses a
manifest with parser gaps and any token the page would drop (bad or duplicate name, no usage).

Then, with the Artifact tool, on the app's artifact url:

0. **Show the plan and wait for a clear yes**, as for DesignSync: the artifact (title and url),
   the account the session is signed in as (it owns what it creates), and the counts `build`
   printed (tokens per family, type styles, fonts, screens). Everything after is a write others
   can see once the artifact is shared. An unattended routine runs only under its own standing
   approval for this exact artifact.
1. **Upload the screens** as assets (`asset: true`, `file_paths` ≤25 a call, from inside the
   working directory) and write `{file: {"blob", "size"}}` from the results.
2. `to_design_system.py index --uploads … --out "$OUT"` (`--existing` the artifact's current
   `project/design-system.json`, read first, when it has one: its keys and marker are kept).
3. **One publish** with `root` = `$OUT`, `file_path` = the index, `files` = every other file
   under `project/`.

The artifact belongs to the claude.ai account the session is signed in as, and is private
until that account shares it from the page's Share menu. Publish from the account that should
own it.

## `_ds_manifest.json` is not optional

The Design System pane renders from `_ds_manifest.json`, not from the `@dsCard`
comments directly. The app compiles that file itself — but only when its own
self-check runs, which does **not** happen on a freshly uploaded project. Upload
26 correctly-marked cards without it and the pane says **"No cards yet"** while
every file sits there perfectly valid.

So the builder writes it, from the same card definitions that produce the HTML,
which is why the two cannot drift. Schema: `namespace`, `cards`
(path/group/name/subtitle/viewport), `globalCssPaths`, `tokens`
(name/value/kind/definedIn), `fonts`, `brandFonts`, and empty `components` /
`startingPoints` / `templates` / `themes`.

## Why the split

The deterministic half is a script; the upload is a tool only the agent can
call. Keeping them apart means the bundle can be rebuilt, diffed and reviewed
without touching the remote project — and a bad parse is caught before anything
is published.

## The completeness gate

`extract_tokens.py` re-reads every `static let` in the theme files and checks that
each one was parsed or recorded as `unparsed`. A name that is neither is a **parser
gap**. It is recorded too, never dropped:

- it goes into `unparsed` with `"gap": "no parser reads this declaration"`;
- the manifest says `"complete": false` and counts it under `parser_gaps`;
- it prints `FOLLOWUP token <file>:<name>: …`.

The bundle is built without that one token, so a single unreadable declaration
never holds back every other card. **An unattended run raises one follow-up per
FOLLOWUP line** with `shared:raise-followup`: `--kind blocked`,
`--key sync-design-system:<app>:gap:<name>`, the line as `--what`, and the ask
"teach the parser this declaration". It never ends the run with only a comment.

`--strict` (on either script) stops instead: exit 1, no manifest and no cards. Use
it when a gap must block the publish.

## Stops

Any stop in `extract_tokens.py` or `build_bundle.py` prints one line before the exit:
`FOLLOWUP stop <script>: <why>` (`extract-tokens` or `build-bundle`; a `--strict` gap, a
missing or broken accessibility baseline, a missing theme directory or repo, a font file
`apps.py` names that the repo no longer has, anything unexpected). A failed token extraction is reported once, by `extract-tokens`. Ids are cut to
a prefix. The upload has two stops of its own, in steps 4 and below: a written-file total short
of the build's `files` count, and a run that cannot log in to DesignSync. **An unattended run
raises one follow-up per stop** with `shared:raise-followup`: `--kind blocked`,
`--key sync-design-system:<app>:stop:<script>` (`upload` for the two upload stops), the line or
the shortfall as `--what`, and the ask "fix the cause and re-run the sync" (for the login stop:
"upload the built bundle from a terminal session"). It never ends the run with only a comment.

This exists because of a specific bug. `gGray0` is a one-line `GradientStyle(...)`
where every sibling spans several lines; the block parser matched only the
multi-line form and dropped it **silently** — not into `unparsed`, just gone. The
manifest was a plausible-looking 222 tokens instead of 223, and nothing said so.
A design system that quietly shrinks is worse than one that fails loudly.

So the parser's own blind spots are not trusted to report themselves: the gate
re-reads every source file and asserts the names match.

A `[Gradient.Stop]` array in `Colors.swift` (`.init(color:location:)` or
`Gradient.Stop(color:location:)`) is a gradient token with its stop locations. It has no
direction of its own (the call site sets it), so it is drawn leading → trailing, `90deg`.

## Adding an app

Add an entry to `scripts/apps.py`. Two token readers exist:

- **Swift** (Aurie, the default): `Colors.swift`, `Fonts.swift`… in `themeDir`.
- **React Native** (`"format": "rn"`, Aura): [`scripts/extract_rn.py`](scripts/extract_rn.py)
  reads the JS object literals of Aura 4.0's `src/styles/redesign` layer as text.
  It reads a literal `palette`, semantic **roles** with a dark and a light value
  (`redesignDark` / `redesignLight`), spacing, layout, radius, gradients (angled,
  start-to-end vector and radial), shadows, topic glows and the Charter / Nunito Sans
  font ladder. Same manifest schema; `tokens.css` puts the dark roles in `:root` (the
  app's default theme) and the light ones under `[data-theme="light"]`. A `.js` file
  in those folders that no reader opens is a gap too; `themeSkip` lists the ones that
  hold no tokens.

Two things need real checking, not just filling in:

- **The declaration forms**: run `extract_tokens.py --strict` first and read what
  lands in `unparsed`; the gate fails rather than let a new form pass quietly.
- **`BRAND_BY_APP`** in `build_bundle.py` — keyed by app deliberately. An app
  with no entry gets a warning and no brand cards, because inheriting another
  product's voice is worse than having none. Aura and Aurie have different
  voices (Aura does not speak in first person; Aurie does).

## What it deliberately does not do

**It does not invent a semantic layer.** Aurie's tokens are named for their value
(`blackPrimary`, `white50`), never their role (`--fg-primary`, `--bg-canvas`),
because that is how the app names them. Generating a role layer here would mean
inventing one the codebase does not have, and mapping role to value is a design
decision that needs a designer. The gap ships as its own card rather than as a
guess.

**It does not change sharing.** DesignSync has no share method — read, plan,
write, delete only. In practice a project created this way already came out
shared *Anyone in <org> with the link — Can edit*, so do not tell the user they
must share it before checking: open the project and read the Share dialog.

**Two UI-only things worth knowing**, because neither is reachable from the tool
and both cost real time to rediscover:

- The project URL is `claude.ai/design/p/<projectId>`. The `/design/<projectId>`
  form silently redirects to the Design home, which reads as "the project is
  missing".
- *Set / Remove as org default* opens a **confirmation dialog**. A click alone
  changes nothing, and if the dialog is dismissed the button looks broken.

## Fonts

Aurie's display face is ABC Otto: the app ships the licensed `ABCOtto-Light.otf`,
`ABCOtto-LightItalic.otf` and `ABCOtto-Regular.otf`, and `apps.py` lists them. They are
embedded so cards render in the real letterforms; the body face is SF Pro, the system
font, which Apple's licence forbids embedding. A file `apps.py` names that the repo no
longer has stops the build (`FOLLOWUP stop build-bundle:`): shipping without it makes
every display card fall back to Georgia while the bundle still looks complete.
