# Aurie design system — what a mockup must get right

**App:** Aurie (iOS, `AuraConsumerApp`). This file is **per app**: Aura has its own
visual language and will get `design-system-aura.md`. Never mix them — an Aurie
mockup in Aura's type or colour reads as wrong to the team instantly.

The single rule behind everything here: **a mockup shows the real screen with one real
change.** Every pixel that already exists is cropped from a capture of the app; only the
new element is rendered — with the app's own fonts and glyphs. A redrawn screen in an
approximated typeface is a sticker, and the first pilot's sticker was pulled before review.

> **Values live next door.** Every token — type, colour, spacing, radius, gradient,
> shadow — with its current value and the `File.swift:line` that defines it, is in
> **`design-tokens-aurie.generated.md`**, regenerated from the app by
> `product:sync-design-system`. This file explains *how to use* them and carries what
> cannot be derived: renderer mechanics, the component inventory, the real copy and its
> sources. Where the two disagree on a value, the generated file is right and this one
> is stale.

## Typography (`Services/Theme/Global/Fonts.swift`)

| Role | Face | File (`AuraConsumerApp/Resources/Fonts/`) | Where used |
|---|---|---|---|
| Display / headings | **ABC Otto** Regular | `ABCOtto-Regular-Trial.otf` | `Font.display(size:)` — welcome title 32pt, tagline 22pt |
| Display italic | ABC Otto Light Italic | `ABCOtto-LightItalic-Trial.otf` | the *Aurie* in "Hi, I'm *Aurie*" |
| Body / UI | **SF Pro** — the system font, *not* in the checkout | `/Library/Fonts/SF-Pro-Text-*.otf` if installed, else `/System/Library/Fonts/SFNS.ttf` | buttons (`Fonts.subheadlineSemibold` = 15 semibold), badge (`Fonts.captionRegular` = 12 regular) |

ABC Otto is a licensed trial — **not vendored** into this skill; the renderer reads it
from the app checkout (`--app-root` / `AURA_APP_ROOT`). SF Pro is not in the checkout
either, and never will be: Apple's licence forbids embedding it, so the app reaches it
through `Font.text(_ style:weight:)` — a Dynamic Type **text style**, never a point size —
and the renderer reads it from the system. Never call `.system(...)` or a literal font
name in the app either: `Fonts.swift` is the only place a face is named, and its tokens
are named for the style they draw (`calloutRegular`, `subheadlineSemibold`).

The full display and UI scales (every `displayLight*` / `font*Default*` token and its
point size) are in `design-tokens-aurie.generated.md` — use a token name, not a size.

Captures from the iPhone 17 Pro simulator are **3x**: multiply point sizes by 3.

## Colour (`Services/Theme/Global/Colors.swift`)

- `Colors.blackSecondary` — the welcome screen's foreground tint for title, tagline and
  buttons' text. The paywall badge uses it at **0.9 opacity**. (Value in the generated
  token file; quote the token, not the hex.)
- Background is an image, not a colour: **`chatBackground`**
  (`Resources/Assets.xcassets/chatBackground.imageset/Aurie_Background*.jpg`), aspect-filled
  and bleeding under the safe areas. Its gradient shifts vertically, which is why a band
  moved on the canvas must be feathered on its leading edge and the exposed strip refilled
  from a sampled background range, not a flat colour.

## Components that already exist — reuse, never redraw

| Component | Where | What it is |
|---|---|---|
| **Social-proof laurel badge** | `PackagePaywallView.swift` → `socialProofBadge` | Ships today as SF Symbols `laurel.leading` / `laurel.trailing` (28pt, light) flanking three centred lines in `Fonts.captionRegular`, tint `titleTextColor` @ 0.9, 6pt spacing. **Copy is fixed brand credential.** Glyphs exported to `references/assets/laurel_*.png`. **The badge stays; the glyph does not** — design has ruled it needs a custom laurel from the Figma Icon System, not SF Symbols. Render what ships, and do not treat the SF Symbol as the settled look. |
| `PrimaryButton` / `GlassContinueButton` | `Shared/Components/PrimaryButton.swift`, `GlassContinueButton.swift` | 56pt capsule; primary carries the animated pink→peach gradient with a glass edge, label `TextStyle.headline` in `Colors.Black`; secondary is frosted glass. **Renderer: `element=pill;clone=welcome:cta`** — the welcome Get Started pill cloned and re-lettered, never redrawn |
| `CircularIconButton` | `Shared/Components/CircularIconButton.swift` | 40pt glass disc (`glassBackgroundCircle`), one SF Symbol at `iconSize` 16 in the `figurePrimary` role — the back chrome, the value-props nav, the affirmation hold. **Renderer: `element=circle_glyph;clone=<region>;symbol=…`** — a disc already on the screen given another glyph |
| `SettingsRowView` | `Shared/Components/SettingsRowView.swift`, laid out by `Features/Settings/Sidebar/Views/SidebarView.swift` | glass group card (`borderRadius18`), rows of 24pt icon · 16pt · title (`font375DefaultSemibold` overridden to `.medium`, `Colors.grayMain`) · trailing `chevron.right` (a screen) or `arrow.up.right` (leaves the app), 16pt padding, hairline between rows. **Renderer: `element=settings_row`** clones a row band or a one-row group and re-fills it |
| Legal disclaimer | `Features/Welcome/Views/WelcomeScreenContent.swift` | `Font.text(.caption2, weight: .medium)` (11pt), `blackSecondary` at **0.5** opacity, ALL CAPS, the two links `.underline()`. **Renderer: `element=text_block;role=disclaimer|secondary;underline=Privacy Policy|Terms of Use`** — same words, another case, role or place |
| Bottom sheet | `customPracticeBottomSheet` in the store (`DailyPractices`) | half-screen white sheet (`borderRadius` XL top, grabber), an icon disc (`dpSheetIconBg`), a display-face title, a subtitle, a hairline, controls, a full-width primary button; the screen behind is dimmed. **Renderer: `element=sheet`** — cloned and re-filled; `cta=a|b|c` stacks a primary pill over secondary glass pills |
| Wordmark | `AurieWordmark.swift` — the `aurieLogo` template asset, 23pt in the voice and chat headers, tinted `skyState.textPrimary` | the *Aurie* wordmark. **Renderer: `element=signature`** (the asset, tinted per sky), or `element=crop;clone=home:logo;key=ink` to lift it off a capture |
| Signature lockup | composed — `figureQuaternary` hairline · wordmark · caption at the sky's `secondaryOpacity` | how anything the app exports signs itself. **Renderer: `element=signature;sky=<name>;text=aurie.ai`** |
| Story card | composed, 9:16 | the sky redrawn from `SkyState.stops` + `haloColor`, the affirmation lifted off the capture (`crop;key=ink`), the signature above the bottom 13 % a story app covers with its own controls. **Nothing in it is a crop of a screenshot's background** — see the SKILL's "drawn from the tokens" rule |
| `CharacterView` | welcome hero | the orb — 187pt on iPhone, with a glow halo that blends into the background (crop it with margin) |

**System sheets are captured, never drawn.** The app presents the iOS share sheet; it does
not draw it, and neither does the renderer. Capture it on the session's simulator (Safari for
a link, Photos for an image — `idb` taps, `simctl` screenshots) and `cutout` it into an
overlay with the header re-lettered for the payload. Messages and Mail are not on the
simulator: what they open with is shown as a **text panel** in the flow, with the copy and
its source, not as a look-alike. Aura's Guest Pass flow (Send Text · Send Email · Share Link
→ a prefilled message with a referral link) is the sister app's precedent for a share
surface; port the pattern, cite it, and note that Aurie has no referral link or guest pass —
the link is the App Store page until a referral programme exists.

**Cloning is the rule for any control that already ships.** A new pill, disc or row is cut out of
a capture that shows one, its old content erased along the axis the surface is uniform on (down a
column of the pill's horizontal gradient; across a row of the disc's vertical one; from a
content-free column of a row's glass), and only the new label or glyph is drawn. A look-alike
drawn from tokens is the sticker the first pilot got pulled for. Glyphs are SF Symbols exported
once by AppKit into `references/assets/symbols/` and sized by the point size the app gives them —
the export's own height per point, since a chevron and a share arrow at the same `iconSize` are
not the same height.

## Real copy inventory — the only claims a mockup may carry

Every claim must come from copy we already publish, **attributed the way we publish it**.
Sources are cited so a reviewer can check them in the thread.

| Claim (verbatim) | Attribution | Source | Notes |
|---|---|---|---|
| **"Created by Aura, / Best of Apps Winner / & 8M Members"** | **Aura's** credentials | aurie.ai/subscribe headline (verbatim); `PackagePaywallView.swift` `socialProofBadge` ships the same three lines as *"8M+ Members"* without the ampersand | The one badge we publish. **Use the aurie.ai wording** for the welcome badge (Jerome, 2026-09-17). |
| "Built by the team behind Aura (the mindfulness app trusted by 8M+ people)" | Aura's | App Store listing, id 6748324270 (CA), 2026-09 | Longer prose form of the same credential |
| "Developed with licensed therapists and clinicians" | Aurie | App Store listing | Credibility, relevant for a therapy companion |
| "Move from overwhelmed to calm in 1–2 minutes" | Aurie | App Store listing | The only **outcome** claim we publish — the natural "benefit line" variant |
| "Clinically trained emotional support" · "trained in 20+ therapeutic modalities" · "Created by therapists and coaches" | Aurie | aurie.ai/subscribe | |
| "30K+ 5-star ratings" · "8M+ downloads" | **Aura's** | aurie.ai/subscribe stats row | Aura ecosystem numbers — attribute to Aura |
| 4.8 ★ | Aurie | App Store (CA) | **10 ratings** as of 2026-09 — do not show a count, and avoid leading with stars until volume grows |
| *"It understands me better than ChatGPT."* — D7smith · *"way more empathetic while also being challenging"* — Trinidad456 | Aurie users | App Store reviews | Real quotes; the ChatGPT one is a competitor comparison — tone/legal caution |

**Correction on record (2026-09-17):** an early mockup read *"8M+ Members · Apple 'Best of
Apps' Winner"* as if they were Aurie's. Both are **Aura's**. Attribute them as
*Created by Aura …* — exactly as the paywall and aurie.ai do. This is why every variant
carries a `source=`, and why a free-text `line` without one is refused.

### Review quotes — verbatim, attributed as App Store reviews

Pulled by Viktor from the Aurie **US App Store** most-recent feed (app 6748324270) on
2026-09-17 — source: [#support-bot thread](https://auramobile.slack.com/archives/C0AV0U4HN6P/p1789696694350479?thread_ts=1789696599.176149&cid=C0AV0U4HN6P).
Use **verbatim**, attributed *"— App Store review"*; a quote is social proof only while it
is a quote.

| Angle | Quote |
|---|---|
| Emotional shift / relief (core spine) | *"I've had the app about a week and I think I've cried no less than three times from various breakthroughs. Like having a deep conversation with a close friend."* |
| | *"A wonderful and insightful tool to help organize thoughts and literally feel lighter in minutes. A calm workbench for the mind available 24/7."* |
| | *"Aurie has become my go-to space to process emotions… helps guide me to clarity every time."* |
| ChatGPT-alternative (strong; a CPP exists for it) | *"Honestly, it understands me better than ChatGPT."* |
| | HSP/overthinker: *"I get dysregulated so easily because I feel very deeply and am an overthinker… really helped me during overwhelming moments when I need a sounding board to make sense of my jumbled thoughts."* |
| | *"I have tried many different AI emotional companion apps and this is definitely number one for me."* |
| Therapy-gap | *"I can discuss problems with Aurie anytime instead of waiting 2 weeks until my next therapy appointment."* |
| | *"Idc if you're looking for a therapist, scared to start therapy, or about to crash out… start here with this app! Use it until you're able to go to therapy."* |
| Trial → convert | *"I did the trial and realized it was making a tremendous difference very quickly so I signed up for a year. I have gained so much insight… It's absolutely unreal."* |
| | *"Truly on another level. Like having the person I need with me anytime."* |
| Voice mode | *"I love this app so much, my favorite is the voice mode. I love how it comforts me and it always makes me feel so much better."* |

**Legal flags (Viktor):** never run *"Better than a therapist!"* as a headline — implied
clinical substitution; the *"instead of waiting 2 weeks"* body copy is fine. Never
*"fix all my problems"* — an outcome guarantee. Keep quotes verbatim and attributed.

**Viktor's ranking for ad / web use** (same thread, 2026-09-17): 1 *"understands me better
than ChatGPT"* (strongest differentiator; a ChatGPT-alternative CPP exists) · 2 the "calm
workbench … feel lighter in minutes" review (closest to the brand spine, *"a shift you can
actually feel"*) · 3 the "cried no less than three times" review (emotional proof, high
scroll-stop) · 4 the "instead of waiting 2 weeks" review (therapy-gap; body copy only) ·
5 the trial → year review (conversion proof — **right above the CTA**). Viktor's rank lines
are condensed; **render the verbatim row above, never the condensed line.** Pairing rule:
the laurel badge is the institutional trust signal — pair it with **one** human quote
(#1 or #2), not a wall. **In marketing creative only** — see the review-card note below.

**Brand constraints that override the ranking** (Viktor, same thread, relaying Steve):
- **No therapy-wait or price comparisons.** The *"instead of waiting 2 weeks until my next
  therapy appointment"* quote is **out** for creative, however true.
- **No trial language for Aurie mobile** — it is direct paid. The *"I did the trial …"*
  quote is **out** on the app's own screens and in mobile ads.
- Both replaced in Viktor's set by the **voice-mode** and **HSP / overthinker** reviews.

**Review-card template (Viktor's RC1–RC5) — marketing creative, not an app component.**
Design has ruled the review card **out for in-app surfaces**; a proper component set is being
designed. It stays here as the reference for ads and the web, where it is what we publish, and
an in-app mockup does not reach for it. The pattern: light / pastel ground →
a *question hook* in italics-emphasis → a verbatim ★5 review card → a `Try Aurie…`
mechanism line → Aurie wordmark → disclaimer. Hooks that passed copy QA: *"Still using
ChatGPT to process your feelings?"* · *"Want to feel lighter after you talk it out?"* ·
*"Ever had a real breakthrough talking to an app?"* · *"What if you could just say it out
loud?"* · *"Do your thoughts get jumbled when you are on edge?"* Assets exist in 4:5 and
9:16 — [contact sheet](https://auramobile.slack.com/files/U0ATMPESR37/F0C2LPUJ5RT/contact_sheet.png).
The welcome screen is an in-app surface, so it gets the **badge alone**.

**Do not screenshot a live rating wall.** The recent-review stream is heavy on
freezing / connection / slow-voice complaints and the daily usage cap; any "see our
reviews" treatment uses curated quotes, not a live App Store capture.

### The aurie.ai/subscribe social-proof block (screenshot on record, 2026-09-17)

- **Laurel badge** — same component as the paywall, but the text *and* the laurels are set
  in the brand's **warm gradient** (pink → orange → yellow, the Get Started button's
  gradient) on the pale ground, not the flat `blackSecondary` tint the app uses. Either
  treatment is ours; on the welcome screen's gradient background the flat tint reads
  cleaner, the gradient text is the web's look. The web copy reads *"Create by Aura"* — a
  typo; the app's *"Created by Aura"* is correct, so mockups say **Created**.
- **Two stat cards** — Apple logo + ★★★★★ **30K+** *5 Stars ratings*, and a people glyph
  **8M+** *Downloads*. Both are **Aura's** numbers (see inventory); if reused they carry the
  "Created by Aura" framing, never Aurie's own rating (10 ratings, 4.8★ on the CA store).
- **30-day money back guarantee** — *"If you are not happy with Aurie in 30 days, please
  contact us and we will provide a full refund immediately."* A real published promise
  and a legitimate reassurance element for a paywall or CTA-adjacent slot. Reuse verbatim.

**Web assets are reusable.** aurie.ai publishes the laurel badge, hero imagery and the
same gradient language; when an idea's source or comparison is the web, take the asset
rather than redrawing it.

## Placement — a design decision, not a slot default

Where a new element goes changes what it does. Reason about it per idea, and when in
doubt **propose more than one placement as separate variants** — they make clean
experiment arms.

**Letter variants as the arms they become — `a`, `c`, `d` …, never `b`.** In every Aurie
and Aura experiment **`b` (and nil) is control**, i.e. the existing screen. On a sheet that
is the *Current* panel, labelled `control (b)`; the renderer refuses `name=b`. Keeping the
letters identical here and on the experiment server is what lets a reviewer's "ship c" mean
the same thing to the build.

| Slot | What it communicates | When it fits |
|---|---|---|
| **top** (above the hero) | First thing read — frames everything below | Trust/provenance before the pitch; there is empty background here on `welcome_screen` so nothing moves |
| **mid** (below the tagline) | Supports the tagline; read as part of the message | Outcome/benefit lines that complete the promise; the hero slides up to make room |
| **bottom** (above the CTA) | Last nudge before commitment (proximity to the button) | Reassurance at the moment of decision; the disclaimer slides up — watch crowding |

Hierarchy on `welcome_screen`, top → bottom: orb (visual anchor) → "Hi, I'm Aurie" →
tagline → legal disclaimer → **Get Started** (primary) → Login. Nothing may push the CTA
off its safe-area anchor; the top `Spacer()` is what absorbs new content.

## Screen models used by the renderer (`design_idea.py` → `SCREENS`)

3x pixels on a 1206×2622 capture, read off the XCTest hierarchy dump the screenshot store keeps
beside each picture (`knowledge/screenshots/hierarchy/<key>.txt`, points × 3). Re-measure when a
screen's layout changes.

| Model (`--screen`) | Kind | What it knows |
|---|---|---|
| `welcome` | stack | hero `430–1562`, disclaimer `1702–1935` (three lines in SF Pro), CTA top `1985`, top zone `190–430`, background `1562–1690`; regions `disclaimer`, `cta` (Get Started), `login`, `footer` (empty, under Login) |
| `onboarding_valueprops` | fixed | the last slide: `body`, and the nav row — `prev` (← disc), `dots`, `next` (✓ disc, *Complete onboarding*), `nav` (the row) |
| `affirmation` | fixed | background `1500–1560`; before the hold: `back` (disc, top-left), `chrome_trailing` (its empty mirror, top-right), `text`, `below_text` (empty), `hold` |
| `settings` | list | `header`, `group1`…`group4` (the glass cards), `logout`, `help`; clones `row` (a row band with its hairline) and `group` (a one-row card with its 24pt spacing) |

**Which op fits which change.** A stack slot moves bands the way the top Spacer would. On a fixed
screen a change *replaces* a control (`replace:next` — the ✓ becomes a labelled pill) or
*overlays* empty chrome (`at:chrome_trailing` — a share disc where the app leaves a slot). On a
list screen a change *inserts* (`after:group2` a new group, `row:group3` a row in a group) and
everything below moves down; what slides under the home indicator is what the app would scroll.
The renderer refuses the wrong pairing rather than improvise.

## Brand rules that bind any copy a design proposes

The brand's canonical codification is the team's **Aurie Design System** (owned by Steve;
`colors_and_type.css` extracted from the app's Theme files plus the **Figma May-2026
styleguide**). Its voice rules apply to every line the designer puts on a screen:

- Aurie speaks as **"I"**, never **"we"**. The product almost never speaks in its own voice.
- **No exclamation marks. No emoji** in product copy — the orb carries the warmth.
- **Curly apostrophes.** Legal copy is plain, tracked **ALL CAPS**.
- One brand gradient (magenta → pink → orange under a white wash); one near-black
  `#1C1B1F`. Colour does almost no work; a new element earns colour only if it is a control.

**Display face — an open question.** The brand system names **Test Martina Plantijn** (Light /
Italic, −5 % tracking) as the display serif; the app's `Fonts.swift` ships **ABC Otto**. The
faithful renderer follows the app, because it composites what ships. When type is the point
of an idea, say so in the Gate 1 thread rather than let a reviewer assume either face is
settled; the owners of the brand system and of `Fonts.swift` decide which is current.

The laurel social-proof badge and this copy inventory are the loop's own components — the brand
system's "subscription badge" is the Premium pill, not the laurel credential. They live here and
in `design_idea.py`. The review card is marketing creative, not one of them.

**This inventory is provisional.** Design is building a proper component set; when it lands, the
components below are replaced by it rather than extended. Until then this file is a faithful
report of what the app ships, which is what the renderer can composite — not a statement that
any of it is the final form.
