---
name: design-idea
description: >
  Turn an idea from the `Learnings/Loop/Aurie Features Ideas` database into design screenshots: a
  Current capture of the screen plus one or more Proposed variants (by placement and by copy),
  rendered onto the real capture with the app's own fonts and components, composed as one
  Current-vs-Proposed sheet, and attached to the row's Screenshot property so share-ideas puts
  it in the Gate 1 thread. Drives the `product:designer` agent; called by features-loop
  between record and share, and again when a thread asks for a redesign. Use when the user
  says "design this idea", "mock up IDEA-12", "show me what it would look like", "make the
  Current vs Proposed", or "redesign IDEA-12 — they liked the idea but not the design".

metadata:
  version: "1.15.0"
knowledge:
  reads:
    - Guide/Index
    - Learnings/Loop/Aurie Features Ideas
    - Tools/Notion
  writes:
    - Learnings/Loop/Aurie Features Ideas
---

# design-idea

The **Design** leg of the Aurie Features loop. An idea in; screenshots out. It sits
between **record** and **share**: a Gate 1 thread should open with a picture of the
change, and a reviewer should be able to say *"I like the idea but not the design"* and
get a revised picture in the same thread.

Two halves, kept apart on purpose:

| Half | Owner | Does |
|---|---|---|
| **Judgment** | `product:designer` agent (Fable) | Reads the idea and the design system, decides *what* to show — which element, which real copy, which placement(s), how many variants — and cites the source of every claim |
| **Mechanics** | `scripts/design_idea.py` | Deterministically renders those decisions: crops the real capture, composes the new element with the real fonts, emits the sheet, attaches to Notion |

The agent never draws; the script never decides. That is what keeps a mockup honest
and repeatable.

## The design system is per app

Two files per app, with different jobs and different lifecycles:

| File | Holds | Kept by |
|---|---|---|
| `references/design-system-{app}.md` | renderer mechanics, the components that already exist, the **real copy inventory with sources**, placement guidance | by hand — none of it is derivable |
| `references/design-tokens-{app}.generated.md` | every type / colour / spacing / radius / gradient / shadow token with its value and `File.swift:line`, plus the brand rules that bind copy | `product:sync-design-system`, from the app and AI Knowledge |

Read the pair for the app you are designing for and no other. Where they overlap, the
generated file is authoritative on **values** and the hand-kept file on **how to use
them**. The hand-kept file therefore quotes token *names*, never hexes or point sizes:
a value written there is a second copy that goes stale silently.

## Commands

Resolve the script from the plugin root — `${CLAUDE_PLUGIN_ROOT}/skills/design-idea/scripts/design_idea.py`.

### `render` — compose variants onto a real capture

```bash
python3 design_idea.py render --idea IDEA-1 --screen welcome \
  --asis /path/to/welcome_capture.png \
  --variant "name=a;slot=top;element=laurel;label=a · Trust badge, above the orb" \
  --variant "name=c;slot=mid;element=line;text=Move from overwhelmed to calm in 1–2 minutes;style=display;source=App Store listing id6748324270" \
  --variant "name=e;slot=mid;element=review;text=Honestly, it understands me better than ChatGPT.;source=App Store review (US)" \
  --variant "name=f;slot=mid;element=review;text=…;source=…;plus=laurel@bottom;label=f · Review below the tagline, badge above the CTA" \
  --out ./out
```

- **Pre-consent screens.** `welcome` is drawn before the age-gate consent, where no experiment
  can bucket or expose; `render` prints a warning for any model with `pre_consent=True`. The idea
  needs a bucketing path in `testPlan.instrumentation` or a post-consent screen
  (`brainstorm-ideas` → `references/pre-consent-screens.md` is the canonical list).
- `--asis` is a **3x capture of the real screen** — the screenshot store
  (`AuraConsumerAppSimTests/knowledge/screenshots/<key>[_light|_dark].png`) or a sim-test
  session. Never a redraw. Render from the store when an element clones chrome from another
  screen: the same-sky donor capture is found beside `--asis` (or name it, `--donor welcome=<png>`).
- `--screen` names a measured model — `welcome`, `onboarding_valueprops`, `affirmation`,
  `settings`, `package_paywall`, `home`, `conversation`, `profile`, plus
  `custompracticebottomsheet` as a donor (the bottom sheet); the row's `Screen` identifier is accepted as-is. Every model has a
  `screen` region (the whole frame) for overlays. Each model is a set of named
  **regions** measured off the capture's hierarchy dump; `welcome` is also a **stack** with the
  three movable-band slots, plus `orb_badge` (a 40pt control by the orb) and `chrome_trailing`
  (the empty top-right chrome slot). `package_paywall` is fixed: `headline`, `bullet1` /
  `bullet1_text`, `bullets`, the plan cards and the header pieces. A two-line headline pushes
  its content down, so render it with `--crop 0,0,1206,2522` — otherwise the fixed home zone
  hides a label the app would simply scroll to. `home` (orb · greeting · voice row · Quick
  Processing card · FOR YOU · feed) and `conversation` (opener · user bubble · reply · rating ·
  input) sit on a sky gradient, so their fills read each row from a content-free column and
  an inserted strip takes its insertion row's colour — no flat patch, no seam. `profile`
  (identity · the Core card and its parts · reflection) has a frosted bottom fade: what an
  insert pushes down dissolves under it rather than being cut flat.
- `--variant` is repeatable; `name`, `slot` and `element` are required. Pairs are split on
  `;`, so no value — `source=` included — can contain one. Entries that share a
  `name=` are **one arm with several placements** (erase the disclaimer *and* write it again
  below the buttons). **Letter variants as the experiment arms they will become: `a`, `c`, `d` …**
  In every one of our experiments **`b` (and nil) is control** — the existing screen, the
  *Current* panel — so `name=b` is refused. The sheet labels Current as `control (b)`.
  - **Slots.** `top` | `mid` | `bottom` on a stack screen; on any screen a region op —
    `replace:<region>` repaints the region from the background sample and places the element
    there (`align=left|center|right`, `valign=top|center|bottom` — `bottom` for a chat
    thread, which is anchored to the input); `at:<region>` overlays it on what is there; `after:<region>`
    inserts a full-width strip below the region and pushes everything under it down (`gap=<pt>`),
    the way a list grows — what slides under the home indicator is what the app would scroll;
    `row:<group>` grows a settings group by one row, before its bottom padding.
    `at:` and `replace:` take `dx=` / `dy=` in points (positive right / down), so
    `at:gear;dx=-48` is a disc 48pt leading of the Profile gear; `profile` also names it
    `gear_leading` (a 40pt disc, 8pt leading of the gear).
    On `settings`, the Log out + help-link footer is pinned under a flexible Spacer, so an
    `after:` / `row:` insert uses the Spacer up first and pushes the footer only by what is left
    (`Screen.footer`).
  - `element=laurel` — the paywall's social-proof badge, copy fixed and pre-cited.
  - `element=line` — one centred line in `style=display` (ABC Otto) or `inter`, or the paywall
    header's calibrated styles: `headline` (`displayLight28`, `.kerning(-0.56)`; `\n` breaks
    where the app's copy breaks) and `bullet` (`subheadlineRegular`, `.kerning(-0.18)` plus SF
    Pro's tracking). Only `headline` and `bullet` are calibrated against a capture; `display`
    and `inter` render without system tracking, so a long body line can read a few percent
    wide. **Requires `text=` and `source=`** — a claim with no provenance is refused, not
    rendered.
  - `element=review` — one **verbatim** ★5 review on a soft white card (stars drawn, quote in
    Inter Medium, attribution `— App Store review`; override with `attribution=`). Requires
    `text=` (the exact quote) and `source=`.
  - `element=badge_review` — the laurel badge over one review card: institutional trust plus
    **one** human voice, the pairing aurie.ai uses instead of a wall of quotes. Same
    requirements as `review`. Tall (~280pt at 3x) — `mid` or `bottom`, never `top`.
  - **Cloned chrome** — a new control built from one already on a capture, so its glass,
    gradient, hairline and padding are the app's own pixels and only the content is drawn again:
    - `element=pill;clone=welcome:cta;text=…;source=…[;width=<pt>]` — `PrimaryButton`, the
      welcome Get Started pill re-lettered (`TextStyle.headline`, `Colors.Black`); `width=`
      narrows it between its end caps.
    - `element=circle_glyph;clone=<region>;symbol=<sf symbol>[;points=16;weight=regular]` —
      `CircularIconButton`, a glass disc on the screen given another SF Symbol in the
      `figurePrimary` role the sky resolves to (measured off the ground, so a dark sky gets white).
    - `element=chip;clone=<disc region>;text=…;source=…[;trailing=xmark;points=15;weight=semibold]`
      — a `CircularIconButton` disc stretched to a capsule around a `subheadlineSemibold` label
      with an optional trailing SF Symbol, in the ink the sky resolves to. Same glass as the
      disc it clones; the height is the disc's.
    - `element=offer_card;text=<heading>;pill=<label>;source=…[;symbol=gift;dismiss=0]` (Profile) —
      the Core card's own glass and wash re-filled: its icon chip with `symbol=`, a
      `displayLight22` heading, the forming card's compact gradient pill and the Download Aura
      card's dismiss mark. Carries its 16pt stack spacing, so place it with `after:`.
    - `element=settings_row;symbol=…;text=…;source=…[;trailing=chevron.right|arrow.up.right;clone=row|group]`
      — `SettingsRowView` as `SidebarView` lays it out; `clone=row` (default) for `row:<group>`,
      `clone=group` (a one-row card with its spacing) for `after:<group>`.
  - `element=text_block;text=…;source=…[;points=11;weight=medium;role=primary|secondary|tertiary|disclaimer;underline=a|b;style=display;align=left;width=<pt>]`
    — a paragraph in the app's UI face, or the display face with `style=display` (a chat
    message is `style=display;points=18;align=left;width=290`); `\n` breaks a line, a blank line is a gap;
    `underline=` rules phrases (the disclaimer's two links); `role=` is the foreground opacity
    Colors.swift gives that role, on the tint the ground calls for.
  - `element=erase` — repaint the region, place nothing (`replace:` only).
  - `plus=laurel@<slot>` or `plus=erase@<slot>` — a **second placement** in the same entry
    (e.g. `slot=mid;element=review;plus=laurel@bottom`). Anything richer is a second `--variant`
    with the same `name=`. Stack slots are laid out bottom → mid → top so the bands move once.
- Writes `{idea}_current.png`, one `{idea}_{name}.png` per variant, `{idea}_compare.png`
  (Current · control (b) | a | c | …) and `{idea}_design.json` (what was rendered, with
  sources and labels — `attach` reads it to name the uploads).
- `--app-root` (or `AURA_APP_ROOT`) points at the `AuraConsumerApp` checkout — the display
  face lives there and is not vendored (ABC Otto is a licensed trial). SF Pro is read from the
  Mac; the glyphs are SF Symbols exported once into `references/assets/symbols/`.

### Flows — every screen the change opens

A control is not a design until the screens it opens are drawn: an engineer builds the
sheet after the tap, the system UI it hands off to, and the payload that leaves the app,
not the row. When an idea adds a control, render the path and lay it out as one flow
sheet per arm whose path differs (Jerome, 2026-09-30):

```bash
# the screens, as renders of the real captures
python3 design_idea.py render --idea IDEA-13 --screen settings --asis settings_screen_light.png --out out \
  --variant "name=a1;slot=row:group3;element=settings_row;symbol=square.and.arrow.up;trailing=arrow.up.right;text=Share Aurie;source=…;label=1 · Tap Share Aurie" \
  --variant "name=c2;slot=row:group3;element=settings_row;…" \
  --variant "name=c2;slot=at:screen;element=sheet;clone=custompracticebottomsheet:sheet;title=Share Aurie;subtitle=…;text=<message>;cta=Share;symbol=square.and.arrow.up;source=…"
# then the flow: images in order, and a text panel for what a system app opens with
python3 design_idea.py flow --idea IDEA-13 --name c --title "tap → Share Aurie sheet → iOS share sheet" --out out \
  --panel "label=1 · Tap Share Aurie;image=out/idea13_a1.png" \
  --panel "label=2 · Share Aurie sheet;image=out/idea13_c2.png" \
  --panel "label=3 · iOS share sheet;image=out/idea13_a2.png" \
  --panel "label=4 · What the chosen app opens with;text=<message>"
```

- `flow` writes `{idea}_flow_{name}.png` and records it in `{idea}_design.json`, so `attach`
  names it `Flow <name> · <title>.png` and share-ideas posts it as its own reply. Panels of
  different shapes (a 9:16 card beside a screen) are fitted by aspect, never stretched.
### `compare` — the Current-vs-Proposed sheet for an arm that spans screens

`render` composes one screen. An arm that changes Profile and Settings is rendered once per
screen, each into its own `--out`, then laid out once:

```bash
python3 design_idea.py compare --idea IDEA-41 --out out \
  --panel "label=Current · Profile;image=prof/idea41_current.png;control=1" \
  --panel "label=Current · Settings;image=set/idea41_current.png;control=1" \
  --panel "label=a · Profile;image=prof/idea41_a.png;arm=a" \
  --panel "label=a · Settings;image=set/idea41_a.png"
```

`control=1` greys a panel's label (every other panel is a treatment, in green); `arm=<letter>`
registers the image as that arm's file with its label. The sheet is recorded as the manifest's
`compare`, so `attach` names it `Current vs Proposed.png`, the name share-ideas keys on. At least
one panel must be `control=1`, and `arm=b` is refused.

- **`element=sheet`** — the app's own bottom sheet (`customPracticeBottomSheet` in the store),
  cloned and re-filled: `title=` (display face), `subtitle=`, `text=` (the body, drawn as the
  sheet's own white field card — a preview, cut with an ellipsis above the buttons),
  `cta=` (one label, or `Send Text|Send Email|Share Link` for a primary pill over secondary
  glass pills — Aura's Guest Pass pattern), `symbol=` (the icon disc's glyph). Placed with
  `at:screen`; the host shows through the scrim the app draws behind a sheet.
- **`element=overlay;image=<png>`** — a prepared full-frame RGBA placed with `at:screen`:
  a system sheet from `cutout`, or a card.
- **`element=crop;clone=<screen>:<region>[;key=ink;height=<pt>]`** — a region lifted off a
  capture (the `home:logo` wordmark); `key=ink` keeps only the strokes, keyed against the
  crop's own ground, so a mark drawn on one sky sits on another.
- **`element=sky;sky=<dawn|morning|midday|afternoon|dusk|night>[;halo=0]`** — the environment
  painted from `SkyState.stops` and `haloColor`, full frame, placed with `at:screen`.
- **`element=signature[;sky=<name>;points=<pt>;rule=0;text=<caption>;source=…]`** — how a
  shared card signs itself: hairline, the *Aurie* wordmark tinted with that sky's
  `textPrimary`, and an optional caption at its secondary opacity. A caption is copy, so it
  needs `source=`.
- **`render --crop x0,y0,x1,y1`** cuts every output to a frame — the 9:16 story card is the
  affirmation's sky redrawn, its text lifted with `crop;key=ink`, and the signature, cropped.

### Something the app exports is **drawn from the tokens, not cropped from a capture**

A screen is composed onto its real screenshot, because the screenshot *is* the app. An
artefact the app **generates** — a story card, a share image, an OG image — is not: it has
no capture, and cropping the nearest screen gives you that screen's blur, its chrome's
ground, and a flat grey where a gradient should be. The first IDEA-19 card was exactly
that, and Jerome's word for it was "not very polished" (2026-09-30).

So build an exported artefact the way the app would: `sky` for the ground, `crop;key=ink`
for type already set on a capture, `signature` for the mark, `text_block` for anything new.
Every value then traces to `Colors.swift` / `AurieEnvironment.swift` rather than to
whatever pixels happened to be under the crop.

### `wordmark` — the app's own mark, once

```bash
python3 design_idea.py wordmark            # macOS; rasterizes aurieLogo.pdf into references/assets/
```

The `aurieLogo` asset is a **template PDF**, so it carries shape and no colour — the export
keeps its alpha and `signature` tints it per sky, the same thing `AurieWordmark` does with
`.renderingMode(.template)`. Committed, so it is a one-off; re-run it only when the asset
changes.
- **A text panel** (`--panel "label=…;text=…"`) is how the payload is shown: what Messages or
  Mail opens with. Neither is on the simulator, and a drawn look-alike of a system app is
  the sticker this leg exists to avoid.

### `cutout` — a captured system sheet as an overlay

The iOS share sheet, a permission alert, a system picker: the system draws them, the app
only presents them, and the store has no capture of them. Capture one on **this session's
simulator** — drive Safari (a link) or Photos (an image) with `idb` (`brew install
facebook/fb/idb-companion`, `pip install fb-idb`; `idb ui tap x y --udid <UDID>` in points;
`xcrun simctl io <UDID> screenshot`) — then cut it into an overlay:

```bash
python3 design_idea.py cutout --image safari_share.png --top 1388 --corners 40 --key 239 --scrim 0.4 \
  --relabel "338,1505,1000,1556=Aurie: Process Your Feelings|17|semibold" \
  --relabel "338,1566,1000,1612=apps.apple.com|15|regular" \
  --paste "106,1508,286,1688=AppIcon.jpg" --paste-radius 14 --out share_link_overlay.png
```

Rows from `--top` down are kept (the first `--corners` rows keyed on luminance so the
rounded corners let the host through); everything above becomes the scrim. `--relabel`
re-letters a header line in the system font for the payload the idea proposes; `--paste`
swaps the thumbnail or preview for the thing shared (the app icon for a link, the story
card for an image). What a sheet shows below its header is the capturing app's — Safari's
bookmark actions, Photos' album actions — and the caption says so.

### `symbols` — export an SF Symbol the elements do not have yet

```bash
python3 design_idea.py symbols --name square.and.arrow.up gift --weight regular medium semibold
```

macOS only (it compiles `scripts/sfsymbol.swift` against AppKit). Renders never need it:
they read the PNGs, and `sf_symbol()` refuses a glyph that is not exported with this command
in the message. Commit the PNGs with the change that needs them.

### `attach` — put the files on the row

```bash
python3 design_idea.py attach --idea IDEA-1 --files out/idea1_compare.png out/idea1_a.png out/idea1_c.png
```

Replaces the row's **Screenshot** files (so a redesign does not pile stale variants on the
row). Uploads are **named by label** from the `{idea}_design.json` found beside the files
(or `--manifest`): `Current vs Proposed.png`, `a · Trust badge, above the orb.png`, … —
`share-ideas` posts the **combined sheet** and every **`Flow …`** file, one reply each, with
that name as the caption; the per-arm images and the other skies stay on the row for the
Linear card. Order matters: sheet first, then the rest.

**This skill never touches Slack.** One owner posts pictures into a thread, and it
is `share-ideas`: `post-pending` uploads the row's Screenshot into a new Gate 1 thread;
`post-redesign --idea IDEA-N --confirm` posts the replaced files as a **reply** in the
existing thread after a redesign — that bot reply is also what clears `needs_redesign` on
the next harvest, so a redesign that is attached but never posted would be re-rendered
every tick.

## Refusals — and why

- **A `line` without `source=`** → refused. The first pilot mis-attributed Aura's
  credentials to Aurie; the citation is what a reviewer checks.
- **Unknown `--screen`** → refused. A screen model is measured from a real capture; there is
  no generic layout to fall back to. Add the screen to `SCREENS` with regions read off its
  hierarchy dump (`knowledge/screenshots/hierarchy/<key>.txt`, points × 3).
- **A stack slot on a fixed or list screen, `row:` outside a list, an unknown region or an
  un-exported symbol** → refused, each naming what the screen or the assets do have.
- **Capture size ≠ model size** → warned, not refused. Regions may be off; re-measure.

## Where it sits in the loop

```
ideate → record → **design** ⇄ (notes back to ideation) → share → GATE 1
                                          ↑
                    "like the idea, not the design" → redesign → reply in thread
```

**The designer weighs treatments before it commits.** If the brief names a *kind* of change
("add social proof") rather than an element, the `product:designer` agent lists the treatments
the system already affords (badge, one review card, a benefit line, badge + card …) against
what the screen is for, keeps the two or three that are materially different, and only then
renders them faithfully here. The agent chooses; this skill renders what it chose.

`features-loop` calls `design-idea` once per newly recorded idea (`design` leg) and once
per `needs_redesign` verdict from `harvest-idea-decisions`. The back-and-forth with ideation
is **bounded** — two rounds, then it ships to share with what it has.

## Knowledge

Reads the idea row from `Learnings/Loop/Aurie Features Ideas` (Notion REST via `NOTION_TOKEN`;
falls back to the MCP recipe in `Tools/Notion`). Writes only the row's Screenshot property.
