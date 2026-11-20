---
description: Navigation conventions for Aura — custom Reflux-driven system, with React Navigation reserved for isolated community sub-flows
globs: app.js,src/actions.js,src/screens/**/*.js,src/components/**/*.js,src/stores/**/*.js,src/events/NavigationController.js
alwaysApply: true
---

# Navigation

Aura uses a **custom Reflux-driven navigation system** for the main app shell, NOT React Navigation. React Navigation v6 is in `package.json` but is reserved for a few isolated community sub-flows (see "When React Navigation is used" below). For new code, follow the custom system unless you're explicitly extending one of those community flows.

## How the custom system works

Three pieces:

1. **`Actions.navigateTo(viewName, options, props)`** — declared in [src/actions.js](src/actions.js) alongside `pushNav`, `switchTab`, `superimposeViewControl`, `navigateBack`, `resetToHome`, `navigateToTab`. Call this from anywhere — screens, stores, helpers, deep-link handlers.
2. **`NavigationController`** at [src/events/NavigationController.js](src/events/NavigationController.js) — a Reflux store that listens to those Actions and `trigger`s a corresponding event. It does no resolving itself; it's a bus.
3. **`Dashboard.js`** at [src/screens/Dashboard.js](src/screens/Dashboard.js) — the central resolver. Its `navigateTo = (event, view, options, props)` handler and `navigateToTab` handler switch on the `view` string and render / push the matching screen.

The "view" argument is a **string identifier** (`'meditationPlayer'`, `'liveEventDetails'`, `'challengeDetails'`, `'webViewer'`, `'editAccount'`, etc.), not a component import.

## Adding a new screen

1. Create the screen file under `src/screens/` (or a sub-folder for grouped screens, e.g. `src/screens/familyPlan/`). Match the surrounding naming convention.
2. Wire the screen into `Dashboard.js`:
   - Add the import.
   - Add a new case to the `view` switch in the `navigateTo` handler so `Actions.navigateTo('yourScreen', ...)` resolves to it.
   - Match the surrounding case style — copy a nearby case rather than inventing a new pattern.
3. Call `Actions.navigateTo('yourScreen', null, { ...props })` from wherever the user triggers the navigation.

The second argument (often `null`) is a legacy `options` slot — most call sites pass `null`. Only set it if you're following an existing pattern that uses it (search `Dashboard.js` for examples).

## Modals, overlays, and tab switches

The Reflux Actions cover these too — don't reinvent them:

- **Modal / overlay over the current screen** → `Actions.superimposeViewControl(view, options, props)`. Works the same way (NavigationController bus → Dashboard resolver).
- **Switching tabs** → `Actions.switchTab(...)` or `Actions.navigateToTab(...)`. Tab definitions live in [src/models/tabs.js](src/models/tabs.js) and tab state in [src/stores/TabController.js](src/stores/TabController.js).
- **Going back** → `Actions.navigateBack(options)`.
- **Resetting to home** → `Actions.resetToHome(options)`.
- **Feature-specific modals** — `NavigationController` exports many narrow Actions like `openCalendarModal`, `openCoachingDetailsModal`, `showSubscriptionPaywall`, `openLiveCalendarShareModal`, `openYoutubePlayerModal`. Use the existing one when the modal already has its own Action; only add a new Action when nothing fits.
- **A card that appears on the Home feed by itself** (launch IAM, invitation, promo) → not `superimposeViewControl`. See "Launch cards on Home" below.

## Launch cards on Home (in-app messages)

A card that appears over the Home feed on its own — the podcast launch IAM, the
guest-pass modal, the Meet Aurie invitation — is **not** a `superimposeViewControl`.
It follows a fixed five-piece shape. Copy [`showAuraPodcastLaunchIAM`](src/screens/Dashboard.js);
it is the reference implementation.

1. **Action name** in [src/actions.js](src/actions.js), beside the others.
2. **Trigger** in [src/events/NavigationController.js](src/events/NavigationController.js) —
   an exported `SHOW_*` constant and a one-line method that does nothing but
   `this.trigger(...)`. The bus never decides anything.
3. **A controller decides whether the user is owed the card** and calls
   `Actions.showYourCard()` when they are.
4. **A thin handler on `Dashboard`**, registered in the same `NavigationController.listen`
   block as its neighbours. It guards on the event name, decides whether a card
   can be shown *right now*, and opens the ref. Nothing else.
5. **The modal is mounted unconditionally** in `render` and draws nothing until
   opened. Its visibility is hook state flipped only by the ref's `open()` — so
   an ineligible user can never reach a visible state, and mounting fires no
   analytics.

All five, end to end:

```js
// 1 — src/actions.js, in the list beside the other show* actions
'showYourCard',

// 2 — src/events/NavigationController.js
export const SHOW_YOUR_CARD = 'showYourCard'
// ...beside showAuraPodcastLaunchIAM:
showYourCard(opt) {
  this.trigger(SHOW_YOUR_CARD, opt)
},

// 3 — the store that owns the feature. Entitlement only; no idea what is on
// screen. Called from ONE place — wherever the condition finishes resolving.
showYourCardIfEligible(experiments) {
  try {
    if (!isYourFeatureEnabled(experiments)) {
      return
    }
    // Optional. Whether a card is once-per-lifetime or is allowed to come back
    // is a product decision — ask, rather than assuming this check belongs.
    if (hasSeenYourCard(getProfile())) {
      return
    }
    Actions.showYourCard()
  } catch (error) {
    bugsnag.report('showYourCardIfEligible Error', { error })
  }
},

// 4 — src/screens/Dashboard.js, constructor
this.yourCardRef = React.createRef()

// 4b — in the same NavigationController.listen block as its neighbours
this.showYourCard(...args)

// 4c — the handler. Presentability and the slot claim, nothing else.
showYourCard = (event) => {
  if (event !== SHOW_YOUR_CARD) {
    return
  }
  if (!this.isOnAuraHomeFeed()) {
    return
  }
  if (IAMController.getLastOpenedIAM()) {
    return
  }
  IAMController.setLastOpenedIAM(YOUR_CARD_KEY)
  this.yourCardRef?.current?.open?.()
}

// 5 — rendered unconditionally; it draws nothing until open() is called
renderYourCard = () => {
  return <YourCard ref={this.yourCardRef} profile={this.props.profile} />
}
```

The modal itself hangs `open` / `close` on the ref from its hook and returns
`null` while `isVisible` is false — copy an existing card's hook rather than
writing a new one.

### Entitlement belongs to a controller; presentability belongs to Dashboard

This split is the whole point, and getting it backwards is what goes wrong.

- **Controller** — "is this user owed this card?" The experiment assignment,
  whatever else the feature requires, and whether they have already had it. It has
  no idea what is on screen and must not guess.
- **Dashboard** — "can a card be shown right now?" Is the feed actually in front of
  the user (`isOnAuraHomeFeed`), and has another launch card already taken the slot
  (`IAMController.getLastOpenedIAM`).

**Claim the launch slot on the Dashboard side**, next to the presentability check —
never in the controller. A controller that claims the slot and then finds Dashboard
had nowhere to paint has burned the slot for every other card on that launch.

### One caller

A card is offered from exactly one place. Two call sites is the bug, not the
safety net — if one of them looks like it fires too early, fix the timing rather
than adding a second to cover it.

Offer it from wherever the answer settles. When a card's condition resolves
asynchronously, that means the function that *finishes* resolving it, not the
callback that kicked it off — offering from the starting point asks a question
that cannot be answered yet, and it will still look correct on a warm start where
the answer happens to be cached.

### Gating a card

Put the whole condition behind one helper and have every surface call that
helper. A card usually has more to satisfy than the experiment assignment: an
entitlement, a platform, a region, a prerequisite the user has to have finished
first. What those are is the feature's business, not this pattern's — what
matters here is that they live together in one function instead of being
re-assembled at each call site, so no surface can ship half the gate.

Fail closed. An unresolved assignment must read as "not eligible", which optional
chaining gives you for free, and a condition that has not answered yet must never
read as a pass.

## Deep links

**Every deep link goes through `DeeplinkHandler`** (`src/components/DeeplinkHandler.js`,
rendered once by `app.js`). That covers universal links, the `aurahealth://`
scheme, Branch, OneSignal and Iterable push, local notifications, and payment
return links.

A pure parser in `src/models/deeplink.js` turns each source's input into
`{ destination, objectId, params }`, and one router, `handleDeeplinkIntent`, opens
it. The router also parks a link until a user is signed in and the dashboard is up.

Never listen for, parse or navigate a link anywhere else: not in `app.js`, a
store or a screen. To make a screen deep-linkable, register it in `Dashboard.js`
(step 2 above), then add its `case` to `routeDestination` and map it in the
parsers.

The full rule, covering adding a destination or source, parking, validation and
simulator testing, is in [`deeplinks.md`](deeplinks.md).

## When React Navigation IS used

React Navigation (`@react-navigation/native`, `@react-navigation/stack`, `@react-navigation/drawer`) is currently used **only** in these files:

- `src/screens/community/CommunityUnsubscribe.js`
- `src/screens/community/CommunityTabItem.js`
- `src/components/community/EventInsetModal.js`
- `src/components/community/CommunityInsetModal.js`
- `src/components/community/CourseInsetModal.js`

They use it for **nested navigation inside community sub-flows** (e.g. an inset modal that contains its own multi-screen flow). Each file owns its own `<NavigationContainer>` — there is no app-level navigator.

If you're extending one of these flows: keep using React Navigation, follow the local pattern. If you're adding a brand-new community sub-flow that needs nested navigation: it's reasonable to reach for React Navigation, but coordinate with the team first — the long-term plan should be consistent with how community navigation is structured. **Don't** introduce a `<NavigationContainer>` outside the community area.

## Don't

- Don't try to register screens via `<Stack.Screen>` in `app.js` — `app.js` doesn't have a Stack.Navigator, and adding one would conflict with the Reflux navigation system.
- Don't call `navigation.navigate(...)` from screens that aren't inside one of the React Navigation containers above — there's no `navigation` prop outside those subtrees.
- Don't pass non-serializable data (functions, class instances, refs) through `Actions.navigateTo` props. Use a Reflux store or a context.
- Don't trigger `Actions.navigateTo(...)` from inside a `render` method — it'll loop. Wire it to a press handler or an effect.
- Don't introduce a parallel navigation library (react-native-navigation, native-stack, etc.) — the codebase has settled on this pattern.
- Don't keep a navigation reference on a global object. If you need to navigate from a non-component context (push notification handler, background task), dispatch the Action — that's what the bus is for.
- Don't put a launch card's eligibility logic on `Dashboard`. The screen decides whether a card can be shown, not whether the user has earned one — see "Launch cards on Home".
- Don't handle a deep link outside `DeeplinkHandler`: no `Linking` listener, URL
  parse or link navigation in `app.js`, a store or a screen. See `deeplinks.md`.
- Don't offer a launch card from more than one place. If one call site looks like it fires too early, fix the timing; a second caller papers over it.
- Don't gate a surface on a raw experiment read (`experiments?.yourFlag === 'a'`). Call the feature's own helper so every surface asks the same question.
