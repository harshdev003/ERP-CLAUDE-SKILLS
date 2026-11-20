Aurie is an emotional processing guide: emotional attunement with structured guidance, creating a real internal shift and then stepping back. **Feel lighter today.** Every token below is read from the app's own Swift theme, so name tokens, never values.

## Content fundamentals

**Voice.** Aurie speaks in the **first person** as a warm, emotionally attuned companion: gentle yet grounded, never clinical, never preachy, with light metaphors and invitational language.

- Warm and empathetic: "I'm here to help you rediscover your light".
- Non-judgmental: accept where the person is, without pushing.
- Grounded: rooted in therapeutic expertise without being clinical.
- Invitational: "Your light is worth tending. Shall we begin?"
- Supportive: "Let me send gentle support right when you need it".

**Approved lines.** Hero: "Feel lighter today". Subscription: "Feel understood. Feel supported. Feel lighter." Welcome: "I'm here to help you rediscover your light". Closing: "Your light is worth tending. Shall we begin?"

**What Aurie is not:** a therapist or a replacement for therapy; a companion designed for attachment or reliance; a productivity coach; a chatbot optimized for endless conversation; generic self-care or journaling.

**Shared principles** (with Aura): specific over abstract; outcome over feature; low commitment over aspirational ("Try it for 3 minutes"); honest over hype (no miracle or cure language); invitational over imperative ("If you'd like…").

**Never write:** journey, unlock, transform / transformation, self-care, holistic, wellness journey, "in today's busy world", "we all struggle with", "therapist in your pocket". **CTAs:** "Try it free", "Just listen", "Start a 3-minute session"; never "Change your life".

**Naming.** Always *Aurie*, never "Aura AI", "Aura Companion" or "Aura+". *Aura* is the meditation app (or the company, *Aura Health*). Both together: "Aura and Aurie", in that order.

## Experience modes

Every surface serves one mode and should say which:

- **Quick Relief** (10–90 seconds): anxiety spikes, late-night vulnerability, near-zero effort. Felt result: "I'm steadier. I can continue."
- **Quick Processing** (2–5 minutes): at most three user turns; name what's present, locate it in the body, soften, identify the need, close with a deliverable. "I'm less stuck."
- **Go Deeper** (8–15 minutes): invited, earned, explicitly consented, with a strong ending and cooldown. "That shifted something real."

The core loop: trigger, downshift, small clarity, integration, return. A screen that raises activation loses people silently.

## Visual foundations

### The sky, and why colour is a role

Aurie is locked to light mode but reads the **time-of-day sky**: dawn, morning, midday, afternoon, dusk and night. Dusk and night are its dark mode. A colour is only legible if it is legible in **every** sky, so on a sky surface name the sky role (`skyState.textPrimary`, `textSecondary`, `figurePrimary`), whose per-sky opacity is the least that clears the contrast bar on that sky's worst gradient stop. The colours in this system are the palette those roles resolve to: reach for them directly only off the sky (sheets, cards, controls).

### Colour

- Text and ink: `blackPrimary`, `blackSecondary` and the `grayAlpha*` ladder on light grounds; `whiteAlpha*` on dark or imagery.
- System states: `systemErrorMain`, `systemCautionMain`, `systemSuccessMain`, each with a translucent 500 and 100 step for fills.
- Brand accents: `additionalYellow`, `additionalPink`, `additionalBlue`; the Aurie Care surfaces use `aurieCareGlassTint` with the `aurieCareGlow*` pair.
- Measure contrast behind any blur or scrim, not against the flat token on white.

### Gradients

Gradients carry the brand's energy, each named for its hue and angle (`g-blue-45`, `g-peach-0`): use the token, not a hand-built ramp. `g-subscription-badge`, `g-subscription-glow` and `g-subscription-button` are the subscription surfaces; `aurie-accent-gradient-stops` is the accent ramp behind the conversation glow and the tab-bar update dot. The sky swatches (`swatch-*`, under Stops) are the sky gradients' colour sets.

### Type

- **ABC Otto** (bundled) for display: the `display*` styles, light and light italic for the hero lines, regular for small display labels. The API is role-named (`display*`), so the typeface can change without touching a call site.
- **SF Pro** (the system font, never embedded: Apple's licence forbids it) for the interface, reached through Dynamic Type text styles (`bodyRegular`, `calloutSemibold`, `footnoteRegular`…). The size shown is the style's size at the default setting; on device it scales up to 200%.
- Minimum text is 11pt; the two decorative styles below it (`caption2Decorative`, `caption3Decorative`) are for decoration only, never for reading.

### Space, radius, shadow

- **Spacing** is the `space-*` ladder, a linear px scale: the name is the value.
- **Radius** is `radius-8` … `radius-28` and `radius-full` (a capsule).
- **Shadows** are named for their job: `button-shadow-cta-*` under coloured CTAs (blue, pink, green, gray, in sizes s to xl), `button-shadow-primary-*` under neutral buttons, `soft-card-s` and `card-big` for cards, `text-shadow-*` for text over imagery.

## Iconography

Aurie's icons are not in this system yet. Use the app's own icon set from its codebase; never substitute emoji or a generic icon font.

## Screens

The **Screens** assets are live captures from the simulator capture store, each in up to three skies: anytime, light (`_light`) and dark (`_dark`). Use them to see tokens in place on real layouts; their copy is not approved marketing language.
