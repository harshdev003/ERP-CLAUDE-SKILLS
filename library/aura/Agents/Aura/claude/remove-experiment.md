---
name: remove-experiment
description: Removes a single ended A/B experiment from the Aura React Native codebase. Traces all references, keeps the winning variant as permanent code, deletes losing branches, opens a PR, runs BugBot, and addresses all review comments. Use for any ended experiment where the winner is known.
tools: Read, Edit, Write, Bash, Grep, Glob
model: sonnet
---

You are implementing experiment cleanup in the Aura React Native codebase at `/Users/jeromecurlier/Development/Aura/Mobile/Aura`.

You will be given:
- `key` — the experiment key (e.g. `newCardDesign`)
- `winner` — the winning variant letter (e.g. `a`, `b`, `d`)
- `linearCard` — the ENGMOB sub-card identifier, if the caller already created one (e.g. `ENGMOB-2087`). **May be omitted** — if it is, you create the card yourself in Step 1.
- `umbrellaCard` — always `PROD-2965`
- Any additional context about non-standard variants

**Convention:** `a = treatment, b/nil = control` — unless the consuming code proves otherwise.

**Linear tracking is not optional.** Every experiment removal — however it was triggered (`sweep-ended-experiments`, a direct API-driven pick, or an ad-hoc request) — MUST end with an ENGMOB sub-card under `PROD-2965` and an updated status-table row. Do not treat this as a follow-up task for later; do it in the same run as Steps 1 and 8 below. This rule exists because a batch of 53 removals were done without cards in a prior run and had to be backfilled after the fact — don't repeat that.

**Credentials:** `LINEAR_API_KEY` and `AURA_API_TOKEN` are in `~/.claude/settings.json` under `env`. Read them with a small Python/`jq` snippet (never hardcode or print the raw token), then call the Linear GraphQL API (`https://api.linear.app/graphql`) and the Aura experiments API (`https://services.aurahealth.io/experiments`) with `curl`.

---

## Step 0 — Isolate your work in a git worktree (MANDATORY, do this first)

**Never work directly in `/Users/jeromecurlier/Development/Aura/Mobile/Aura`.** Many `remove-experiment` runs execute concurrently against this same repo (parallel sweeps, other agents, other sessions). A shared checkout means your branch checkout, edits, or `git reset` can clobber another run's uncommitted work — or another run can clobber yours — silently. This has happened repeatedly: agents have found the main checkout on a stranger's branch mid-task, with someone else's uncommitted diff sitting in the working tree, and one run nearly committed a wrong `Dashboard.js` line edit because of stale line offsets from concurrent drift.

Create an isolated worktree on a fresh branch off `origin/master` before touching any file:

```bash
cd /Users/jeromecurlier/Development/Aura/Mobile/Aura
git fetch origin master
git worktree add ../Aura-<key> -b remove-experiment-<key> origin/master
cd ../Aura-<key>
```

(`.claude/worktrees/agent-<key>` is an equally acceptable location if you prefer nesting under the main checkout — either path works as long as it's a dedicated worktree, not the shared main tree.)

Do all remaining steps — grep, edits, lint, commit, push, `gh pr create` — from inside this worktree. Never `git checkout` a different branch in the main checkout, never `git stash`/`git reset` there "to clean up" — if you find the main checkout mid-task on an unexpected branch with a dirty working tree, that's another concurrent run's state, not yours to touch. Just don't work there; use your own worktree instead.

If disk space or `npm install` cost is a concern for a one-off tiny fix, a shallow worktree still works the same way — the isolation is what matters, not the install.

## Step 1 — Ensure a Linear card exists

Skip this step only if `linearCard` was passed in.

1. **Search for an existing card first** (idempotency — don't duplicate if a prior run already filed one):
   ```graphql
   query { issues(filter: { title: { contains: "<key>" } }) { nodes { identifier title state { name } } } }
   ```
   Match on the title containing `` `<key>` `` — if a card already exists whose title is `Remove experiment: <key> (...)`, reuse its identifier as `linearCard` and skip creation.

2. **If none exists, fetch the experiment's live metadata**:
   ```bash
   curl -sS "https://services.aurahealth.io/experiments?limit=5000" -H "Authorization: Bearer $AURA_API_TOKEN"
   ```
   Find the row where `id == "<key>"`. Pull `endedAt`, `active` (winner), `result`, `rowid`, `preventOldUsers`, `preventNewUsers`, `targetLocale`.

3. **Create the card** via `issueCreate`, using these fixed IDs (verified 2026-07-02 — re-verify if the workspace structure changes):

   | Field | Value |
   | --- | --- |
   | `teamId` | `5497338e-312b-4ddf-ac5f-3339399c956d` (Mobile — keeps the `ENGMOB-` prefix) |
   | `projectId` | `a13d07fd-f427-4f35-a5af-fba68b015f95` (Aura - Core App) |
   | `parentId` | UUID of `PROD-2965` (resolve via `query { issue(id: "PROD-2965") { id } }`) |
   | `labelIds` | `["2511d2ae-b39c-4d2b-bc7c-14ba33d8a1b7"]` (Tech Quality) |
   | `priority` | `3` |
   | `stateId` | `090653f2-f21a-4718-89cd-0438e781f82c` (Todo) at creation time — you'll move it forward in Step 8 |

   Title: `Remove experiment: <key> (<winner> won)`.

   Description: follow the template on any existing sub-card (e.g. fetch `ENGMOB-1996` for the canonical shape) — **Summary**, **Experiment metadata** table (Key, Surface, Ended at, Winning variant, Variants originally tested, Audience flags, Target locale, Server result, Source), **Removal checklist**, **Implementation process**, **Skill provenance**. Fill every field from the live API row fetched above — never invent a value.

Capture the returned identifier as `linearCard` for the rest of this run.

## Step 2 — Trace all references

```bash
git grep -rn "<key>" -- src/ app.js
```

**Always include `app.js` in the grep scope, not just `src/`.** `app.js` at the repo root holds top-level navigation and deep-link handling and directly calls into store helpers (e.g. `ChallengesController.<method>(this.state.experiments)`). A `git grep` scoped to `src/**/*.js` alone will silently miss these call sites and can report "zero callers" for a method that `app.js` still depends on — deleting it then breaks that code path at runtime with no compile-time warning (verified in PR #6452: `newReferralUsersChallenges` looked unused by a `src/`-only grep but was called from `app.js`'s challenge deep-link handler). Read every matched file. Understand what each variant does before touching anything.

**Grep the bare key, never narrow to `experiments?.<key>`.** The experiments object is bound to many different variable names across the codebase — `experiments`, `cachedExperiments`, and helper parameters named `experiment` (singular). A helper like `ChallengesController.canShowPresentDayTrackOnTopChallenges(experiment)` reads `experiment?.showPresentDayTrackOnTopChallenges === 'a'`, which a `grep "experiments?.<key>"` (plural) misses entirely. The bare-key `git grep "<key>"` above is receiver-agnostic on purpose — trust it, read every hit, and treat any `.<key>` property access on *any* object as a real read (verified in PR #6657: a bulk-audit that grepped only the plural `experiments?.` wrongly classified `showPresentDayTrackOnTopChallenges` as unused and BugBot caught the resulting dead treatment branch). Two more non-standard read forms the bare-key grep catches but a narrowed grep misses (verified ENGMOB-2687 — all three were missed by an enumeration keyed on fetch-array + `experiments?.` reads):

- **Digit-prefixed keys** can only be read via **bracket notation** — `experiments?.['50PercentDiscountMobile']`, never dot (JS forbids `experiments.50…`). So `experiments?.<key>` never matches them.
- **Direct profile / RTDB reads**: some keys are read straight off `profile.experiments.<key>` or `fbdb.ref('experiments/<key>')` and are **not in any fetch array at all** (e.g. `rainbowBrand2`). A registration-only or `returnExperiments()`-only enumeration misses these entirely.

**"No client read" does NOT mean "safe to remove" — check for server-side consumption.** Some keys are registered in a fetch array but never read in client JS because their effect is *server-side*: the client's `chooseAll` writes the user's assigned bucket to Firebase (`users/{id}/experiments/<key>`), and a backend (pricing, `includeOnExperiment` content gating) reads that bucket. Removing such a key's registration stops the client bucketing/exposure-logging for future users and **changes server behavior** — it is NOT behaviour-neutral. Tells: an adjacent `// keep fetching … so the server keeps bucketing / logging exposure` comment, `notes` mentioning pricing/content, or a still-open server experiment. When a key has no client read, confirm it isn't server-consumed before removing; if it is (or the comment says keep it), leave it registered and flag it for a product/backend decision instead of trimming (verified on `googlePricingIntroOffer`, ENGMOB-2678 — a live Android pricing test with `targetPlatform: android` + `preventOldUsers`, gated so only Android users joined ≥ its minVersion get the winner).

## Step 2.5 — Check the audience gate

**Do this before writing any code — it changes what "remove the experiment" means.**

Fetch the experiment's row (reuse the Step 1 fetch if you already have it). The Postgres API carries the version + prevent fields you need here:

```bash
curl -sS "https://services.aurahealth.io/experiments/<key>" -H "Authorization: Bearer $AURA_API_TOKEN"
```

Read `preventOldUsers`, `preventNewUsers`, `minVersion`, `maxVersion`.

`preventOldUsers` / `preventNewUsers` gate on **`profile.joinedVersion`** — the app version a user's account was created on, fixed permanently at signup (`Onboarding6.js`) — **not** the currently-running app build. This is implemented in `src/lib/ExperimentHelper.js`'s `chooseAll`: if `minVersion > profile.joinedVersion`, the experiment key is never set on that user's `experiments` object at all, which every `=== 'a'` check treats as control. **A newer app build does not make this "moot"** — a user who joined on an old version stays excluded forever, regardless of what version they're running today. (This exact mistake shipped 10 flawed removal PRs in one run before being caught — do not repeat it.)

Let `audienceGated = preventOldUsers === true || preventNewUsers === true`.

- **Not audience-gated** → proceed to Step 3 as normal, no guard needed.
- **Audience-gated and the winner is control** → the excluded audience was never on the treatment path; deleting the scaffolding is safe as-is. No guard needed.
- **Audience-gated and the winner is treatment**:
  - `preventOldUsers === true` (new-users-only rollout) → the winning behavior must stay gated on `compareVersions(profile.joinedVersion, '<minVersion>', { zeroExtend: true }) >= 0` (import from `../lib/CompareVersions`, adjust relative path per file). Never inline the winner unconditionally — that permanently expands the feature to the pre-`<minVersion>` cohort the server excluded.
  - `preventNewUsers === true` (existing-users-only rollout) → same pattern but `<= 0` against `maxVersion`.
  - If the required version bound (`minVersion` for `preventOldUsers`, `maxVersion` for `preventNewUsers`) is **missing**, stop and flag for human review instead of guessing — do not synthesize a threshold.
  - The consuming component needs `profile` (or `profile.joinedVersion`) available — check if it's already a prop; if not, thread it in from the caller the same way `Dashboard.js` passes `profile` to `FilterPickerModal` (see PR #6276 for the pattern), and add it to `propTypes`.
  - If the file already has a purpose-built guard helper (e.g. an `isXOldUser()` method checking `joinedVersion`), **do not delete it** — the audience restriction is a permanent product decision, not experiment scaffolding. Only the experiment plumbing (the `experiments?.<key> === 'a'` check itself) gets removed; the version guard it was combined with must survive.

Record whether this experiment is audience-gated — it changes the PR title in Step 5.

## Step 2.6 — Deleted experiments and the `result` field (check per-key, at work-time)

Not every stale experiment ended cleanly. Some are **deleted** server-side (`deletedAt` set) rather than formally **ended** (`endedAt` set), and the list endpoint (`/experiments?limit=5000`) **silently hides deleted rows** — so a batch/audit snapshot can misclassify them. Always confirm status with a **per-key** lookup *at the moment you work the card*, not from a stale list:

```bash
# Lifecycle + result (Postgres API):
curl -sS "https://services.aurahealth.io/experiments/<key>" -H "Authorization: Bearer $AURA_API_TOKEN"
# Gating config the client actually evaluates (Firebase RTDB — public read, carries ALL gate fields):
curl -sS "https://auratech16.firebaseio.com/experiments/<key>.json"
```

The Postgres row carries most fields — `deletedAt`, `endedAt`, `result`, `active`, `minVersion`, `maxVersion`, `targetPlatform`, `targetTrafficSource`, `preventOldUsers`, `preventNewUsers`, and `targetLocale` (**singular** column). It does **not** carry the tester-scope flags `internalTestersOnly` / `alphaTestersOnly`, and its singular `targetLocale` is **not** the plural `targetLocales` key the client actually evaluates. So cross-check the RTDB row for tester scope and the real locale key — see the callout below.

**A deleted experiment resolves by its `active` winner — the same as an ended one.** `deletedAt` means the experiment row was cleaned up server-side after it concluded; the `active` letter still records what won and what the intended permanent behavior is. Do NOT default a deleted experiment to `b`. (Earlier guidance said "deleted → control" — that was wrong; the `active` winner is the signal, deleted or ended.)

1. **`active = b` / nil (control won)** → remove the gate, keep the default/control path. No version/locale/platform guard needed (the excluded audience was never on treatment). PR title: `Remove experiment: <key> (deleted — control)` (or `(b won)` for ended).
2. **`active = a` (treatment won)** → **inline the treatment path**, guarded by the experiment's audience scope (see below). PR title: `Remove experiment: <key> (a won)` plus the audience tag(s).
3. **404 / `EXPERIMENT_NOT_FOUND`** (never in catalog / hard-gone) → no `active` to read, treat as control (`b`), keep the default path. Note in the PR that there's no API row.
4. **Multi-letter `active`** → ambiguous winner; file/flag for human decision, don't guess.

### ⚠️ Cross-check Firebase RTDB for tester scope + the real locale key

The Postgres-backed `/experiments/<key>` row (`services.aurahealth.io`) has most gating fields (`active`, `minVersion`, `maxVersion`, `targetPlatform`, `targetTrafficSource`, `preventOldUsers`, `preventNewUsers`, and the **singular** `targetLocale` column), but two decisive things live only in the client-authoritative RTDB row: the **tester-scope** flags `internalTestersOnly` / `alphaTestersOnly` (absent from Postgres entirely), and the **plural `targetLocales`** key the client actually reads (`chooseAll` never reads the singular `targetLocale` — see below). The **client reads its config from Firebase RTDB** (`src/lib/ExperimentHelper.js` → `fetchExperiment` uses `code.firebaseURL`), so RTDB is authoritative for what actually gated users. Read it directly (public, no auth):

```bash
curl -sS "https://auratech16.firebaseio.com/experiments/<key>.json"
```

These fields change the answer decisively:

- **`active=a` but `internalTestersOnly`/`alphaTestersOnly` is set** → real (non-tester) users only ever saw control, so **remove as `b` (control)**, NOT treatment. (`chooseAll` skips the experiment for anyone who isn't the matching tester type.)
- **`active=a` real rollout** → inline treatment, guarded **only by the surviving gates** (see below — NOT necessarily "version + platform + locale").

The RTDB row is the source of truth for what the client actually did — start there rather than guessing. Only fall back to asking the product owner when the RTDB row itself is genuinely ambiguous. Never assume `active=a` means "ship to everyone" — but equally, never assume a `minVersion`/`targetLocale` on the row was applied (it often was not — see below).

### Building the treatment guard for a real `active=a` rollout

**⚠️ Critical — a pinned winner SKIPS version/traffic gating.** Once an experiment is decided by pinning `active` to a **single letter** (`a`/`b`) — the state of every ended/deleted experiment you remove — the client's `chooseAll` (`src/lib/ExperimentHelper.js`) treats it as decided and takes the `results[expName] = exp.active` branch, which **skips `minVersion` / `maxVersion` / `targetTrafficSource` entirely**. A version bound re-enters *only* when paired with `preventOldUsers` (`minVersion`) or `preventNewUsers` (`maxVersion`) — that is exactly the Step 2.5 audience-gate case. So the guard you inline must be built from **only the gates that actually survived for a pinned winner**:

- **Platform** (`targetPlatform`, e.g. `ios` or `ios,android`): survives. `if (targetPlatform && !targetPlatform.includes(os)) → control`. `ios,android` = all mobile → no platform guard needed.
- **Tester scope** (`alphaTestersOnly` / `internalTestersOnly`): survives → real users saw control → remove as `b` (see the callout above).
- **Locale** (`targetLocales`, **plural**, `|`-delimited): survives. The user is dropped to control only if their locale is a *supported* locale not in the list — so English **and** unsupported-locale users see treatment ("en or no-locale"). Replicate with `UserController.isEnglishUser()` / the existing locale pattern. **The singular `targetLocale` key is never read by the client — it is a silent no-op typo.** A scope expressed with `targetLocale` (singular) was NEVER applied → add no locale guard.
- **Version** (`minVersion` / `maxVersion`): **does NOT survive for a plain pinned winner.** Add a `compareVersions(profile.joinedVersion, '<minVersion>', { zeroExtend: true }) >= 0` guard **only if the row also has `preventOldUsers` (or `preventNewUsers` for `maxVersion`)** — see Step 2.5. For a plain `active=a` with just a `minVersion`, add **no** version guard: the treatment shipped to everyone regardless of joined version.

Example — `communityForums` (`active:a, minVersion:3.18.149, targetPlatform:ios`, **no** `preventOldUsers`): the `minVersion` was dead in the pinned client, so the guard is **iOS only**:

```js
// was: if (experiments?.communityForums === 'a') { <treatment> }
if (Platform.OS === 'ios') {   // NO version guard — minVersion never applied to the pinned winner
  <treatment>
}
```

Counter-example — a pinned `active=a` with `preventOldUsers:true` + `minVersion:X`: the `joinedVersion >= X` guard DOES survive (Step 2.5), so keep it.

> ⚠️ **`chooseOnboarding` gates differently from `chooseAll` — check which resolver assigns the key first.** Experiments registered in the `Experiment.chooseOnboarding` block of `src/screens/Onboarding6.js` (e.g. `coachVoiceTestInOnboarding`, `reduceMinTopicTo1Mobile`) are resolved by a **separate** path in `src/lib/ExperimentHelper.js` that checks only `targetPlatform` / `targetLocales` and returns the pinned `active` value — it does **not** consult `minVersion` / `maxVersion` / `preventOldUsers` / `preventNewUsers` at all. So an onboarding-assigned treatment winner needs **no `joinedVersion` guard even when its config carries `preventOldUsers` + `minVersion`**: the audience gate never applied client-side, so inlining the winner unconditionally matches shipped behavior. The Step 2.5 audience-gate guard applies **only** to keys resolved by `chooseAll` (the Dashboard / screen experiment-fetch arrays). Before adding any guard, confirm the key is fetched via `chooseAll`; if it's only in a `chooseOnboarding` block, skip the guard. Verified on `coachVoiceTestInOnboarding` (PR #6636) and `reduceMinTopicTo1Mobile` (PR #6627).

Thread `profile` into the component if it isn't already available (see PR #6276 pattern) and add it to `propTypes`.

> ⚠️ This rule was learned the hard way: five pinned `active=a` winners (`autoplayTracksMultipleDuration`, `communityForums`, `commnunityMessageRequests`, `communityCardCoachProfile`, `auraCommunity`) were first inlined with a `joinedVersion >= minVersion` guard the client never applied — regressing lower-version users to control. Corrected in ENGMOB-2620–2624. Verify every guard against `chooseAll` before shipping.

**Confirm status still holds at work-time** — re-fetch rather than trusting an audit file; a deleted experiment could have been un-deleted, and an "active" one could have just been deleted.

## Step 3 — Implement

For each file:
- **Winner is treatment (`a`)**: inline the `a` code path as permanent, delete the `b`/control branch and the experiment gate (`experiments?.<key> === 'a'`)
- **Winner is control (`b`/nil)**: delete the entire `if (experiments?.<key> === 'a') { ... }` block, keep the surrounding default code
- **Multi-variant (c, d, …)**: keep only the winning variant's code path, delete all other branches
- **`['a','c'].includes(...)` style**: resolve based on whether the winner is in the list

Also remove the key from **every** experiment-fetch array. The `Experiment.chooseAll`/`chooseOnboarding` registration is **spread across multiple files** and uses **both `'single-quote'` and `` `backtick` `` string literals**, so a Dashboard-only or single-quote-only grep silently misses most of them. Grep with both quote styles across the whole repo: `git grep -nE "['\`]<key>['\`]" -- src/ app.js`. Known fetch-array locations (verified 2026-07-06, ENGMOB-2678):

- `src/screens/Dashboard.js` — the `allExperiments` variable array (~103 keys, single quotes)
- `src/screens/MeditationPlayer2.js` — inline `chooseAll` array (~77 keys, backticks)
- `src/screens/SubscriptionModal3.js` (~34) — inline `chooseAll` arrays, backticks
- `app.js` — inline `chooseAll` array
- `src/stores/RecommendationListsController.js` — onboarding experiments come from a **remote** `getAssignOnboardingExperimentsList()` call, NOT code literals (nothing to remove here)

A single key can appear in more than one of these arrays — remove it from all of them.

**If the gate is an OR/AND expression referencing another experiment key** (e.g. `canShow<X>() || canShow<SiblingKey>()`), collapsing your own key's side to a constant can make the *whole expression's boolean output* correct while still breaking something real: if the sibling key is a **different, still-live experiment** (check `endedAt` on its own API row — `null` means it's still running), removing the sibling's fetch-array entry as "now-dead" stops the app from ever requesting that experiment's bucket assignment from the server, silently killing exposure logging/data collection for the live test — even though your local boolean logic never changes. Before deleting a sibling key's fetch entry as a side effect of simplifying an OR/AND, check that sibling's own `endedAt` first. If it's still live, keep fetching it (with a comment explaining why the key is fetched but unused by the simplified boolean) even though your own key's removal makes the local gate's output no longer depend on it.

**Rules:**
- One experiment per PR — no unrelated edits
- No `console.log`, no hardcoded colors/sizes (use `theme.*` / `responsiveDimension()`)
- Preserve existing `propTypes` declarations
- Keep all existing comments unless the code they describe is being deleted

**Watch for orphaned destructures (`no-unused-vars` — a CI error):** when you delete a gate, the `experiments` local may become unused. If the function still calls `this.props.experiments` directly, drop `experiments` from the destructure but keep the direct references:

```js
// before — gate used the destructured `experiments`
const { experiments, imageOverlay } = this.props
if (experiments?.<key> === 'a') { ... }        // gate removed
// after — `experiments` no longer read locally, so remove it from the destructure
const { imageOverlay } = this.props
// (a later `UserController.getTrackUIWidth(this.props.experiments)` call is fine and stays)
```

If removing it empties the destructure of its last `experiments = {}` entry, also delete its `// eslint-disable-next-line react/prop-types` comment. `git grep -n "experiments" <file>` after editing to confirm no local destructure is left unused.

## Step 4 — Lint

```bash
cd /Users/jeromecurlier/Development/Aura/Mobile/Aura && npm run lint 2>&1 | grep -E "error|warning" | grep -v "node_modules"
```

Fix all errors in files you touched. Pre-existing errors in untouched files can be ignored.

## Step 5 — Commit and open PR

Commit message: `Remove experiment: <key> (<winner> won)`

PR title: `Remove experiment: <key> (<winner> won)` — **if Step 2.5 found `audienceGated == true`, append the flag(s) that were true**: `Remove experiment: <key> (<winner> won) [preventOldUsers]`, `[preventNewUsers]`, or both if somehow both are set. This is mandatory whenever the audience gate applies, even for a control-win case that needed no code guard — it tells the human reviewer to specifically verify the audience-gate handling (or its absence) rather than skim it as a routine removal.

**Before writing the description, fetch `<linearCard>`'s full description** (Linear API `issue(id: "<linearCard>") { description }`, or the Linear MCP if loaded). The sweep that files these cards embeds a complete "Experiment metadata" table (key, surface, ended-at timestamp, winning variant, original variants, `preventOldUsers`/`preventNewUsers`, target locale, free-text server result, and the source API row/rowid). Do not summarize or drop fields from it — reproduce the table in full in the PR body. If the card lookup fails or the table is missing/incomplete, fall back to whatever fields you can find and explicitly note what's unavailable (e.g. `Ended: not available — Linear card lookup failed`) rather than silently omitting the row.

PR description must include:
- One-line summary of what the winning variant did
- Links to `<linearCard>` and `<umbrellaCard>`
- **Experiment result** section — the full metadata table copied from the Linear card, at minimum:
  ```
  | Field | Value |
  | --- | --- |
  | Key | <key> |
  | Ended at | <exact endedAt timestamp> |
  | Winning variant | <winner> |
  | Variants originally tested | <a=treatment, b/nil=control, or the card's own wording> |
  | Audience — preventOldUsers | <value or None> |
  | Audience — preventNewUsers | <value or None> |
  | Server result (free-text) | <the actual metrics/notes from the card — never omit this> |
  | Source | <API source row / rowid from the card> |
  ```
- **If `audienceGated == true`** (Step 2.5), add an explicit **Audience gate** section right after the metadata table stating: which flag is set, the version bound used, the exact guard added (or why none was needed on a control win), and asking the reviewer to confirm the threshold matches the intended cutoff.

```bash
gh pr create --title "Remove experiment: <key> (<winner> won)" --body "..." --base master
```

**Commit every edit before pushing.** `git status` must be clean afterward — a fix left in the working tree (never `git add`ed) will let CI keep failing on an error you already "fixed". Confirm with `git status --short` before `git push`.

**If the branch conflicts with master** (another experiment-removal PR often touches the same `Dashboard.js` fetch array): `git fetch origin master && git merge origin/master`. In the fetch array, both sides typically each delete a different key — take the **union**: keep neither deleted key. Verify the other PR's key is fully gone from `src/` (`git grep <otherKey> src/`) so you don't leave an orphan fetch entry, then commit the merge.

### Conflict recipe for `Onboarding6.js` / `Dashboard.js` (read this before resolving any conflict here)

During a high-volume sweep, dozens of experiment-removal PRs touch the same handful of files — almost always `src/screens/Onboarding6.js` (the `Experiment.chooseOnboarding` registration block, the `handleNext` navigation switch, the fade-out switch, the render-switch, the `CAN_SHOW_AURA_RING` array, assorted `renderX`/helper methods) and `src/screens/Dashboard.js` (the experiment-fetch array). A PR opened hours ago can re-conflict every time a sibling sweep PR merges first. Do not treat this as a real merge each time — verify, don't guess:

1. **Classify the conflict before touching it.** Almost every hit is one of three shapes:
   - **Simple union (by far the most common)** — HEAD (your branch, based on an older master) still has a stale reference to a sibling key that a *different*, since-merged sweep PR already removed; origin/master has your own key's not-yet-removed reference. Both sides are dead code from each PR's own perspective. Resolution: drop the whole overlapping block, keep the code flowing from whatever came before it straight to whatever comes after.
   - **Orthogonal combination** — both sides made a genuine, distinct fix in the same function (e.g. your branch fixed an audience gate, a sibling PR unconditioned a different call in the same method). Resolution: keep both fixes, don't let a text-diff conflict make you drop one.
   - **Real bug** — auto-merge silently dropped something outside any conflict marker (an import, a prop). This only surfaces via lint, not the diff — see step 4.
   - Never resolve a conflict by "picking a side" without checking what each side actually does. Guessing has produced wrong merges before.
   2. **Verify dead code with `git grep`, never assume.** For every key/reference on either side of the marker:
      ```bash
      git grep -n "<key>" origin/master -- src/ app.js
      git grep -n "<key>" HEAD -- src/ app.js
      ```
      Zero hits on a side confirms that side's reference is safe to drop. If a key/reference is still live on the side you're about to keep, it's an orthogonal combination, not a simple union — don't drop it.
3. **File deletions (modify/delete conflicts)** — if your PR deletes a file (e.g. a component only the removed experiment used) but master has an unrelated edit to it from a sibling PR, confirm the file's only consumer is also being removed, then `git rm -f <file>` to keep the deletion.
4. **Lint the whole touched file, not just the conflict block** — auto-merge can drop an import or reference outside any `<<<<<<<` marker with no visible diff. `npx eslint <file>` after every conflict resolution catches `no-undef` from a silently-dropped import; this has happened (a missing `UserController` import survived an auto-merge undetected until lint caught 3 call sites).
5. **After resolving, sweep for leftovers**: `grep -n "^<<<<<<<" <file>` (zero results) and a full-repo `git grep -n "<key>"` for every key involved (zero hits outside intentional survivors like permanent screen-name identifiers).
6. **If the same PR re-conflicts repeatedly** (three or more times against `Onboarding6.js`/`Dashboard.js` is common in a busy sweep), that's not a sign anything is wrong with the resolution — it means sibling PRs are merging faster than this one. Flag it in your report (Step 9) so the human knows merging this PR sooner breaks the cycle, rather than silently re-fixing it in a loop.

## Step 6 — BugBot review

```bash
# Get the PR number from the URL
gh pr view <pr-number> --json number,url
```

Run BugBot by invoking the `eng:bugbot-review` skill with the PR URL. Wait for it to complete.

If BugBot is not available, independently review your own diff asking: "what could go wrong — missing reference, broken sibling experiment, unintended behavior change?"

## Step 7 — Address all review comments

```bash
gh api repos/AuraHealth/Aura/pulls/<pr-number>/comments
```

For every comment:
1. Fix the issue in a new commit
2. Reply on the thread with the fixing commit SHA and a one-line explanation

Do not mark ready for review until:
- CI is green (`gh pr view <pr-number> --json statusCheckRollup`)
- All comment threads are resolved or replied to

## Step 8 — Update Linear (mandatory, every run)

Do this regardless of whether the PR is merged yet:

1. **Move `linearCard`'s status:**
   - PR open, CI green, ready for review → `In Review` (`7b782b06-d9c5-41ba-8588-35b193d928ae`)
   - PR merged → `Done` (`c6860d45-1749-4d95-8f40-3e509d58862e`)
   ```graphql
   mutation { issueUpdate(id: "<linearCard uuid>", input: { stateId: "<state id>" }) { success } }
   ```
   (Resolve the card's UUID from its identifier with `query { issue(id: "<linearCard>") { id } }` if you only have the identifier.)

2. **Flip the row in `PROD-2965`'s status table.** Fetch the current description, find the line matching `` | `<key>` | `<winner>` | ... | ``, and replace the status cell:
   - `⬜` / `🔄` → `✅ [Aura#<pr-number>](<https://github.com/AuraHealth/Aura/pull/<pr-number>>)`
   - **`✅` means "PR opened for this key", not "PR merged."** Use `✅` as soon as you have a PR URL, even if it isn't merged yet — do not invent a distinct "🔄 in progress with an open PR" state. `🔄` is reserved for a run that's still working (no PR yet); once a PR exists, mark `✅` regardless of merge status.
   - If the key has **no row at all** (some in-code experiments predate the original PROD-2940 sweep and were never listed), append a new row at the end of the table rather than skipping it.
   Push the updated description with `issueUpdate` on `PROD-2965`.

3. **Refresh the summary line** at the top of the "Experiment removal status" section (`**N tracked ended experiments** in src: **M removed** (X%), **R remaining**, **P in progress**. Last updated YYYY-MM-DD.`) — recompute by counting `✅`/`🔄`/total rows in the table after your edit, don't just increment blindly (another concurrent run may have already changed the counts).

4. **Set the estimate** on `linearCard` (Fibonacci, revised 2026-07-04 — four tiers, starting at 3):
   - `3` — simple: a single fetch-array entry plus one or two straightforward call sites, no reconstruction needed.
   - `5` — medium: a few files, or combining your own removal with an already-merged sibling's leftover cleanup during a conflict resolution, but no guard logic or deep restructuring.
   - `8` — high effort: multiple files with real prop-drilling/import cleanup, or an audience-gated `compareVersions`/`joinedVersion` guard to add and verify.
   - `13` (or higher) — extra-high/complex: a full component/screen reconstruction (e.g. removing an entire alternate UI path across many call sites), or a fix that also resolves a discovered regression/bug along the way.
   ```graphql
   mutation { issueUpdate(id: "<linearCard uuid>", input: { estimate: <N> }) { success } }
   ```
   Set this in the same mutation as the state change in step 1, or as a separate call — either is fine, but don't skip it. Every card should leave this run with a non-null estimate.

Skip nothing here even for a single ad-hoc removal — a card with no table row, a table row with no card, or a card with no estimate is exactly the drift this step exists to prevent.

## Step 9 — Report

Output:
- PR URL
- Linear card identifier (new or reused) and its current status
- Confirmation that the `PROD-2965` row was updated (or added) and the summary line refreshed
- Summary of what changed (files, key decisions)
- Whether the experiment was audience-gated (Step 2.5) and, if so, the exact guard added and the PR title tag used
- Any findings from BugBot or self-review
- Confirmation that CI is green and threads are addressed
- If the PR re-conflicted against master 3+ times before landing, say so explicitly and note it's likely to keep happening until it's merged — that's a signal to prioritize merging it, not a resolution problem
