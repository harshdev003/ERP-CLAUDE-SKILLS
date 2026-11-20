# displayConfig registry — supported themes & icon keys

`displayConfig.theme` and each `valueProps[].icon` are **keys** the Aurie app
resolves against its design system. An unknown key falls back to a safe client
default, so a typo degrades silently — always pick from the lists below, and
**append a new entry here whenever a package introduces one** (this is a living
list that grows with the lineup).

## Themes

`theme` is a **color** — never the package/tier name, never a hex value or
asset URL.

| theme | Used by (reference) |
| -- | -- |
| `green` | Lite |
| `pink` | Grow |
| `purple` | Bloom |
| `gold` | Flourish |

## Icon keys

`valueProps[].icon` — one key per value-prop row.

| icon | Meaning |
| -- | -- |
| `time` | Weekly time / usage allowance (e.g. "100x Weekly Time") |
| `memory` | Memory — rolling or permanent |
| `voice` | Voice & text / talk with Aurie |
| `pattern` | Pattern recognition across conversations |
| `letter` | Daily letter / morning reflection |
| `pillars` | Weekly pillars |
| `modality` | Choose your modality (CBT, DBT, ACT, IFS…) |
| `deep` | Deep processing mode |
| `crisis` | Crisis toolkit |
| `library` | Aura library (meditation, sleep, wellness content) |
| `specialist` | Specialist modes (sleep, relationships, work stress) |
| `adfree` | Ad-free experience |
| `model` | Advanced AI model |
| `practices` | Daily Practices — small daily moments to reflect and reset |
| `insights` | Profile Insights — what Aurie is learning about you |
| `quick` | Quick processing — short, fast sessions |
| `voice_premium` | Premium voice — most natural voice |
| `voice_priority` | Priority voice — faster responses, no waiting |
| `early_access` | Early access — try new features first |

> Before adding a new key, confirm the app actually maps it in its theme/icon
> registry — adding it here without the client mapping still falls back to the
> default at render time.
