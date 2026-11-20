Aura is the meditation, sleep and emotional-health app: a personalized library people open to **listen**, not to talk. This system is the 4.0 redesign as the app ships it — every token below is read from the app's own theme files, so name tokens, never values.

## Content fundamentals

**Voice: a caring mother.** Empowering, peaceful, comforting, concise. Aura reassures without fussing and invites without pushing.

- **Second person, never first.** Write "you" and "your sleep". Aura does not speak as "I" or "we" in the product; that is Aurie's voice, not Aura's.
- **Specific over abstract.** "The 3 a.m. stare" beats "sleep difficulties". Name the moment, the body, the time of day.
- **Outcome over feature.** Say what the person will feel, not what the app does.
- **Low commitment.** "Try it for 3 minutes" beats "Start your wellness journey".
- **Honest over hype.** No miracle or cure language, no before/after promises, no therapy claims. Aura supports; it does not treat or diagnose.
- **Short, short, longer.** Vary rhythm. Sentence case for titles and buttons.
- **Numbers carry their reason.** "7 million people use Aura — and the top reason they keep coming back is sleep", never "Join 7 million users today".

**Never write:** journey (wellness or healing), unlock, transform / transformation, self-care, holistic, "in today's busy world", "we all struggle with", "therapist in your pocket".

**Calls to action** are direct and low-pressure: "Try it free", "Just listen", "Start a 3-minute session". Never "Change your life" or "Begin your Aura journey today".

**Naming.** *Aura* is this app (or the company, as *Aura Health*). *Aurie* is the AI companion, always spelled *Aurie*, never "Aura AI". Both together: "Aura and Aurie", in that order.

## Visual foundations

### Theme

Aura has two themes, **dark** (the default) and **light**. Onboarding, the tab bar and the players always render dark. Every colour in a screen is a **role** that resolves per theme — `textPrimary`, `canvas`, `surfaceGlass` — never a `palette-*` literal; the palette is what the roles point at. A role that exists on one theme must exist on the other.

### Colour

- **Ground.** Pages sit on `canvas` (near-black #131313 dark, #F8F8F8 light). Cards are `surfaceCard`; glass panels and pills over imagery are `surfaceGlass`; sheets use `surfaceSheet` over `surfaceScrimSheet`; the floating tab bar is `surfaceNav` over `navScrim`.
- **Text ladder.** `textPrimary` for titles and body; `textSecondary` and `textSubdued` for supporting copy; `textEmphasis` for a highlighted figure. All four clear 4.5:1 on `canvas` in both themes.
  - `textTertiary` and `textMuted` fall below 4.5:1 on the light canvas (and `textMuted` on dark too): use them for large text, metadata a person can do without, or disabled states, never for body copy.
  - `textPlaceholder` is a field placeholder only.
  - `textAccent` reads on dark only (2.2:1 on light).
- **On other grounds.** Text on a coloured fill uses `textOnColorEmphasis` / `textOnColorMuted`; on `surfaceInverse` (the white pill) it is `textOnInverse`; on imagery, `onMediaPrimary`.
- **Accents** mark meaning, not decoration: `accentLive` for live, `accentDestructive` for delete, `accentLink` for inline links, `accentRating` for stars. `accentMint` and `accentPeach` deepen on light (`palette-mintDeep`, `palette-peachDeep`) so they stay legible.
- **The primary CTA** is the rose pill: `g-cta-primary`, with `g-cta-sheen` along its top edge and `ctaShadow` beneath; the benefit check chip beside it is `accentRose`. Its fill is the same in both themes.
- **Borders** are hairlines: `borderSubtle` by default, `borderDefault` to separate, `borderSelected` / `borderSelectedRow` for the chosen option, `borderStrong` sparingly.

### Gradients and glows

Gradients are scrims and washes, not decoration. Scrims (`g-onboarding-screen-scrim`, `g-bottom-cta-scrim`) fade content into the canvas; the media-detail hero is washed from `heroWashPrimary` to `heroWashSecondary`. Each onboarding topic card has its own glow, a core fading out through an edge colour: `topic-dark-<topic>` on dark, `topic-light-<topic>` on light. The paywall's aurora is eight stops sampled every 45° around a ring (`auroraNorth` … `auroraNorthWest`).

### Type

Two families, both bundled:

- **Charter** (serif) for titles and display: `titleSerifRegular30`, `displaySerifRegular40`, `displaySerifRegular25`, `displaySerifRegular22`, `displaySerifRegular20`, `bodySerifRegular16`. The italic cut (`titleSerifItalic30`) sets the emphasised words inside a title, never a whole line.
- **Nunito Sans** for the interface: `bodySansRegular16` and `copySansRegular14` for reading; `labelSans*` for controls; `buttonSansSemiBold18` / `ctaSansSemiBold16` for buttons; `captionSans*` and `eyebrowSansRegular12` for small print; `tabLabelSansSemiBold11` only in the tab bar.

No style sets a separate weight: the face carries it. Sizes are authored at the 402pt frame and scale gently with the screen.

### Space, radius, shadow

- **Spacing** is the `space-*` ladder (`space-1` … `space-128`), in points at the 402pt frame; use a step, never a number between them.
- **Radius** is `radius-12`, `radius-14`, `radius-16`, `radius-24` and `radius-32`, the authored values exactly.
- **Shadows** are named for the surface they lift (`shadow-offer-card`, `shadow-sheet-panel`, `shadow-entry-menu`…); some come in a light and a dark variant (`shadow-offer-plan-card-light` / `-dark`). Use the one named for your surface rather than borrowing another's.

## Iconography

Aura's icons are not in this system yet. Until they are, use the app's own icon set from its codebase; never substitute emoji or a generic icon font. Icons take `textPrimary` / `iconMuted`; `iconStat` is the paywall's App Store and downloads glyphs.

## Screens

The **Screens** assets are live captures of the current app in both themes (`_v4_dark`, `_v4_light`), regenerated from the simulator capture store. Use them to see a token in place and to match real layouts; never copy their copy as approved marketing lines.
