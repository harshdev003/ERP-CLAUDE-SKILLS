# Worked examples

Three end-to-end runs for the most common modes. Inputs are the kind of
phrasings a user actually types; outputs are what this skill should produce.

## Example 1 — `linear` mode (designing new events)

**User input:**
> Design events for the new Aurie onboarding screen where users pick which
> of three emotional verticals they want help with (Anxiety, Sleep, Focus).
> They tap a card and then a Continue button. Add this to a Linear card.

**Skill flow:**
1. Confirm product → Aurie (explicit in the prompt)
2. Load `Tools/Mixpanel/Data Design`
3. Mode: `linear` (Linear card requested)
4. Optionally query Mixpanel Lexicon for `AI Sign Up Onboarding` to see what
   nearby events already exist — confirms `AI Sign Up Onboarding Screen Seen`
   pattern is in use, so screen-seen event aligns with existing naming
5. Apply rules + format

**Output:**

```
- [ ] `AI Sign Up Onboarding Vertical Screen Seen` — fires when the vertical-selection screen first appears during Aurie onboarding
   - `Source App` (string) — example: `Aurie Mobile`
   - `Screen` (string) — example: `Vertical Selection Screen`
---
- [ ] `AI Sign Up Onboarding Vertical Selected` — fires when the user taps one of the three vertical cards (Anxiety / Sleep / Focus)
   - `Source App` (string) — example: `Aurie Mobile`
   - `Vertical` (string) — example: `Anxiety` | `Sleep` | `Focus`
   - `Sent From` (string) — example: `Vertical Selection Screen`
---
- [ ] `AI Sign Up Onboarding Vertical Continue Tapped` — fires when the user taps Continue after selecting a vertical
   - `Source App` (string) — example: `Aurie Mobile`
   - `Vertical` (string) — example: `Anxiety` | `Sleep` | `Focus`
   - `Sent From` (string) — example: `Vertical Selection Screen`
---
```

## Example 2 — `audit` mode

**User input:**
> Audit these events for naming issues:
> - ai_coach_message_sent
> - AI Tooltip Seen
> - AICoachOpened
> - AI Subscription Trial Started (with Source App filter)

**Skill flow:**
1. Product clear from `AI` prefix → Aurie
2. Load canonical doc
3. Mode: `audit`
4. Apply rules

**Output:**

```
| Event Name | Issue | Recommendation |
|---|---|---|
| `ai_coach_message_sent` | snake_case in event name | Rename to `AI Coach Message Sent` (Title Case with spaces, `AI` prefix preserved) |
| `AICoachOpened` | camelCase / PascalCase concatenation | Rename to `AI Coach Opened` |
| `AI Subscription Trial Started` filtered by Source App | `AI Subscription Trial Started` is a backend event (no `Source App` property) — filter silently drops all rows | Remove the `Source App` filter. Use `Mixpanel Library = node` as the canonical backend-event indicator. For mobile/web split, filter the upstream frontend event (`AI Subscription Purchased`) by `Source App` instead. |

Passes audit:
- `AI Tooltip Seen`
```

## Example 3 — `gap` mode

**User input:**
> What events are we missing on the Aurie paywall screen?

**Skill flow:**
1. Confirm product → Aurie
2. Load canonical doc
3. Mode: `gap`
4. Query Lexicon for existing `AI Subscription Modal` + `AI Subscription Purchase` events to see baseline coverage
5. Identify gaps

**Output:**

```
The Aurie paywall currently fires `AI Subscription Modal Seen` (impression)
and `AI Subscription Purchase Button Tapped` (intent), so we know who saw
the paywall and who attempted to buy. Missing: paywall-level engagement
that doesn't end in a purchase attempt (scroll depth, plan toggle, FAQ
expansion). These four events close the loop:

- [ ] `AI Subscription Modal Plan Toggled` — fires when the user toggles between monthly / yearly / lifetime plan options
   - `Source App` (string) — example: `Aurie Mobile`
   - `Plan` (string) — example: `monthly` | `yearly` | `lifetime`
   - `Sent From` (string) — example: `Subscription Modal Screen`
---
- [ ] `AI Subscription Modal Faq Expanded` — fires when the user expands any FAQ item on the paywall
   - `Source App` (string) — example: `Aurie Mobile`
   - `Faq Item` (string) — example: `cancel anytime` | `how billing works`
---
- [ ] `AI Subscription Modal Restore Tapped` — fires when the user taps the "Restore Purchases" link
   - `Source App` (string) — example: `Aurie Mobile`
---
- [ ] `AI Subscription Modal Dismissed` — fires when the user closes the paywall without purchasing (back button, swipe down, or X)
   - `Source App` (string) — example: `Aurie Mobile`
   - `Dismiss Method` (string) — example: `back button` | `swipe` | `close button`
---
```

## Things to notice in all three examples

- **Product confirmed** before any design work
- **Standard properties reused** (`Source App`, `Sent From`, `Screen`)
  before any new one is invented
- **Enum-style example values** for any property that could otherwise hold
  raw user input (mental-health text)
- **Backend annotations** when a subscription event is involved
- **Lexicon checked** when designing or doing gap analysis, never assumed
