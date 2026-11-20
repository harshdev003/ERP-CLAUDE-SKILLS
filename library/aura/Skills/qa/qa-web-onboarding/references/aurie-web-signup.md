# Aurie Web signup: reference

Facts gathered in the first full QA pass on 2026-11-04, checked again on 2026-11-05. The
site redeploys often, so treat file names, copy and open issues as a starting point and
re-verify before reporting them as current.

## Hosts and source

| Environment | Landing URL | Analytics project |
|---|---|---|
| Dev | `https://aura-ai-dev-989cf9e0116c.herokuapp.com/signup` | Mixpanel "Project Aura Dev" (970535, workspace 30772) |
| Live | `https://www.aurie.ai/signup` | Mixpanel "Project Aura" (963821, workspace 30768) |

- Source repo: `AuraHealth/AIWeb` (Next.js). `marketing:aurie-web-flow-snapshot` reads it.
- Legal pages live on aurie.ai: `/privacy-policy`, `/terms-of-service`, `/crisis-protocol`.
  The short paths `/privacy` and `/terms` return 404 on both hosts.

## Flow

Landing (CTA "Try Aurie Free") → 33-screen quiz at `/signup/quiz?q=N`, where N is the
screen number minus one → plan loader → sign-up form (name, then email, then password, each
revealed once the previous field has a value) → credentials, paywall and fee choice. The
first QA pass stopped at the sign-up form.

Other routes in the build: `/signup/choose-fee`, `/signup/goal`, `/signup/insight`,
`/signup/results`, `/signup/what-to-expect`.

## Screen map

"Screen N" is the N/33 counter at the top of each quiz screen. The second column is the
`Screen` value sent with `AI Sign Up Screen Completed`.

| N | Screen property | Type |
|---|---|---|
| 1 | Opener What Brings You Here Screen | Multi-select |
| 2 | Opener When You Feel It Most Screen | Single |
| 3 | Opener Off Guard Response Screen | Single |
| 4 | Opener Support Style Screen | Single |
| 5 | Past Approaches Screen | Multi-select |
| 6 | Age Range Screen | Single |
| 7 | Clinical Authority Screen | Interstitial |
| 8 | Testimonial Natalia Screen | Testimonial |
| 9 | Conversation Replay Frequency Screen | Single |
| 10 | Carrying Education Screen | Interstitial, loop diagram |
| 11 | Processing Loop Education Screen | Interstitial, chart |
| 12 | Nighttime Switch Off Difficulty Screen | Single |
| 13 | Stress Body Location Screen | Single |
| 14 | Asking For Help Difficulty Screen | Single |
| 15 | Pattern Reflection Screen | Result |
| 16 | Where It Started Soft Gate Screen | Opt-in gate, can skip the childhood section |
| 17 | Childhood Emotional Caretaking Screen | Single with Skip |
| 18 | Childhood Validation Screen | Single with Skip |
| 19 | Childhood Hidden Feelings Screen | Single with Skip |
| 20 | Testimonial Patterns 30 Years Screen | Testimonial |
| 21 | Childhood Stability Screen | Single with Skip |
| 22 | Childhood Perfectionism Screen | Single with Skip |
| 23 | Childhood Reactions Today Screen | Single with Skip |
| 24 | Before After Education Screen | Interstitial, bar chart |
| 25 | 30 Day Outcome Goal Screen | Multi-select |
| 26 | Life Transformation Areas Screen | Multi-select |
| 27 | Change Importance Screen | Multi-select |
| 28 | One Thing To Change Screen | Single |
| 29 | Current Support Level Screen | Slider, defaults to 3 |
| 30 | Daily Time Commitment Screen | Choice, defaults to 15 min |
| 31 | Aurie Depth Differentiator Screen | Interstitial |
| 32 | Aurie Vs ChatGPT Screen | Comparison table |
| 33 | Testimonial Shannon Screen | Testimonial |
| — | Answers Processing Screen | Plan loader |

Sections, shown as the progress label: What brings you here (1–8), How it shows up (9–15),
Where it started (16–24), What you want (25–30), What makes Aurie different (31–33).

The quiz definitions (ids, labels, titles, types) ship in a shared chunk. In November 2026
that was `2490-<hash>.js`. Slider labels live in the quiz page chunk.

## Event contract

| Event | Fires on | Key properties |
|---|---|---|
| `AI Landing Page Seen` | Landing load | Super properties only |
| `AI Sign Up Start` | CTA click | Super properties only |
| `AI Sign Up Screen Completed` | Each quiz screen and the loader | `Quiz`, `Screen`, `Quiz Screen Order`, `Quiz Multi Select`, plus `Quiz Answer` and `Quiz Answer ID` (single) or `Quiz Answers` and `Quiz Answer IDs` (multi) |

Interstitials send their headline, or "Testimonial #N", as `Quiz`, with no answer.

Defined in the code but not exercised in the first pass: `AI Sign Up Credentials Screen
Seen`, `AI Sign Up Login`, `AI Login`, `AI Subscription Modal Seen`, `AI Subscription Modal
Closed`, `AI Subscription Purchase Button Tapped`, `AI Subscription Purchase Try`, `AI
Subscription Purchase Failed`, `AI Subscription Purchased`, `AI Trial Fee Selected`, `AI Get
App Screen Seen`, `AI Get App CTA Tapped`.

Super properties: `Source App` = "Aurie Web", `Sent From` = "AIWeb", `Platform` = "AIWeb",
`Day` (weekday number), `Time` (hour string), one `Experiment: <name>` per assigned
experiment. Campaign tags map as follows: `utm_source` to `AI Attribution Install Source`,
`utm_campaign` to `AI Attribution Campaign`, `utm_medium` to `AI Attribution Appsflyer
Channel`, plus `initial_utm_*`.

Other sinks seen on the wire:
- GA4 (event names and URL only, no answers);
- Bugsnag sessions;
- a TikTok no-op logger;
- `/api/log`, the app's own server logger, which logs every tracked event and its properties,
  about 450 calls per full run;
- `/api/auraServices/ai/onboarding/quiz/results`, which receives all answers as a chat
  transcript and returns the AI-written headline, patterns and reflection.

## Experiments seen

| Experiment | Where | Notes |
|---|---|---|
| `aiWebLandingPageCopy` | Dev landing only, in Nov 2026 | Live showed a different headline with no assignment |
| `webLandingPageFooter` | Both | |

No paywall experiment was observed, because the paywall was not reached.

## Already decided: do not re-raise

The data owner reviewed these on 2026-11-04 and chose to keep them:
- single-select vs multi-select answer properties, since `Quiz Multi Select` handles the breakdown;
- untouched slider and minutes defaults logged as answers;
- the safety answer ("feeling unsafe") stored in Mixpanel and the server log;
- the AI-written reflection stored in the server log.

## History

| Date | What |
|---|---|
| 2026-11-04 | First pass. Summary card PROD-8239 under QA [Cycle 51]; findings card PROD-8238; user-reported email field and card-details zoom issues in PROD-8284 |
| 2026-11-05 | Both hosts redeployed and now differ. Fixed on both: legal links on the sign-up form, "log in to the Aurie app", "Sleep deprivation". Still present: most copy issues and the reused Screen 14 answer ID `sometimes` |
