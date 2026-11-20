---
name: sweep-ended-experiments
description: >
  Detects A/B experiments that have ended on Aura's experiment server but still have code in a
  target app repo, and files one Linear cleanup card per experiment under a batch parent that
  a human engineer or an AI coding agent can pick up and turn into a removal PR. The skill
  files the tickets; it does not edit code itself. Supports four repos via a `repo=` argument:
  auraconsumerapp (default), aura, aiweb and userweb. For each ended experiment in the repo it
  reads the rolled-out variant from the API, git-greps to confirm the code still exists, picks
  one of three removal shapes, and fills a lean checklist-driven template. Trigger on "sweep
  ended experiments", "run the experiment sweep", "clean up ended A/B tests in mobile", "file
  experiment cleanup cards", "sweep the web experiments", and also on the former wording many
  users still type — "remove experiment", "remove ended experiments", "auto remove experiment
  code", "run the experiment-removal sweep". Also triggers on "remove inactive experiments".

metadata:
  version: "3.1.1"
knowledge:
  reads:
    - Guide/Index       # baseline routing aid (universal)
    - Tools/Aura API    # experiment endpoints — how ended (endedAt) + winner (active) are read
    - Tools/Linear      # cleanup cards, parent/child issue relationships, and status tracking
    - Tools/GitHub      # PR description, review, and ready-for-review handoff conventions
    - Learnings/Experiments  # the experiment catalog/readouts DB — context on what each experiment was and its outcome
---

# sweep-ended-experiments

Sweeps Aura's experiment catalog for experiments that have ended but still live in a target app repo (mobile or web), and produces one **lean, checklist-driven** Linear ticket per experiment that any engineer or AI coding agent can pick up and turn into a clean removal PR. The skill's deliverable is **Linear cards, not code edits** — it never modifies the target repo.

The card gives the implementer the winning variant, what it means, and a fixed removal checklist — it deliberately does **not** enumerate files, line numbers, or exact edits. The implementer owns the investigation: open the experiment, trace references, and decide what to change (including protecting any sibling experiments that share code). This keeps cards short, avoids stale line numbers, and trusts the implementer's own repo view as the source of truth.

The **issue content is agent-agnostic** — it never names a specific executor, so any engineer or AI agent can pick it up. As a final, separate execution step (Step 9), the skill routes each *ready* card to an execution agent so the work gets picked up automatically. That routing is purely operational; it does not change the issue body, and the card stays pickable by a human or any other agent.

Validated end-to-end on 2026-05-05 against `onboardingPromptResults` in AuraConsumerApp ([ENGMOB-1662](https://linear.app/aura-health/issue/ENGMOB-1662) → [PR #934](https://github.com/AuraHealth/AuraConsumerApp/pull/934)).

## When to use

Trigger on any of:

- "sweep ended experiments"
- "run the experiment sweep" / "run the experiment cleanup sweep"
- "clean up ended A/B tests in mobile"
- "file experiment cleanup cards" / "file removal cards for inactive experiments"
- "sweep ended experiments in the Aura app" (selects `repo=aura`)
- "sweep ended experiments in Aurie web" / "on the web signup flow" (selects `repo=aiweb`)
- "sweep ended experiments in Aura web" / "in UserWeb" (selects `repo=userweb`)
- "sweep ended experiments on web" (ask which web repo — or run both, one sweep each)
- A user asking to file a batch of experiment cleanups for the team
- The skill's former "remove" wording, preserved so the many users who still type it route here: "remove experiment", "remove experiments", "remove the experiment", "remove ended experiments", "remove inactive experiments", "auto remove experiment code", "run the experiment-removal sweep" (the skill files cleanup cards; it does not remove code itself)

For ad-hoc one-experiment runs (single key, no batch parent), pass `key=<experimentKey>`.

## Repo profiles

The skill targets one repo per run, selected with `repo=`. The **server-side experiment model is identical across all four** — same `GET /experiments` catalog, same `active` letter (a single letter on an ended experiment is the rolled-out variant; a multi-letter `active` is still running), same `endedAt` / `minVersion` / `maxVersion` / `preventOldUsers` / `preventNewUsers` / `targetApp` fields. What differs is how each repo *declares and consumes* experiments, which changes the enumeration source, the detection grep, the variant-convention signal, the test-knowledge path, the Step 2 API slice, the audience-gate handling, and where the Linear cards land (project **and** per-experiment team).

### Mobile profiles

| Aspect | `repo=auraconsumerapp` (**default**) | `repo=aura` |
|---|---|---|
| GitHub repo | `AuraHealth/AuraConsumerApp` (Aurie, iOS) | `AuraHealth/Aura` (`@aura/app`, React Native) |
| Local checkout (default guess) | `~/Development/Aura/Mobile/AuraConsumerApp` | `~/Development/Aura/Mobile/Aura` |
| Language | Swift | JavaScript (React Native) |
| Experiment catalog | `Services/Experiments/Models/Experiments.swift` — a static enum of `static let <key> = "<key>"` | **No static catalog file.** Keys are Firebase Realtime DB node names, consumed in code as `experiments.<key>` (see `src/stores/ExperimentsStore.js`, `src/lib/ExperimentHelper.js`) |
| Code detection grep | `git grep -lI "<key>" -- 'AuraConsumerApp/*.swift'` | `git grep -lI "<key>" -- 'src/' app.js` (bare-key literal — catches fetch-array registrations in `'single'`/`` `backtick` `` quotes across Dashboard.js `allExperiments`, MeditationPlayer2.js, SubscriptionModal3/4.js, and repo-root `app.js`, **and reads in every form**: `experiments?.<key>`, bracket notation `experiments?.['50PercentDiscountMobile']` (required for **digit-prefixed keys** — dot access is illegal), and direct `profile.experiments.<key>` / `fbdb.ref('experiments/<key>')` reads. The bare-key literal is precisely what catches all of these — never narrow it to `experiments?.<key>`) |
| Variant signal (which letter is treatment) | Enum doc comment in `Experiments.swift` (`"a" = treatment, "b"/nil = control`) | No doc comment; confirm from the consuming check `experiments?.<key> === 'a'` in `src/`. Same org convention: `a = treatment, b/nil = control` |
| Per-experiment extension file | `+Experiment.swift` extensions | none — logic is inline `experiments?.<key> === 'a'` guards |
| Test-knowledge dir | `AuraConsumerAppSimTests/knowledge/` (sim-test knowledge) | `AuraSimTests/knowledge/` (sim-test knowledge) |
| Step 2 API slice | `only_ended: true, only_mobile: true` | same |
| Audience gate | full joined-version-guard flow (Step 5) | same |
| Per-experiment card team | `5497338e-312b-4ddf-ac5f-3339399c956d` (**Mobile**, `ENGMOB-`) | same |
| Linear project for cards | `50b657cd-f184-4068-aec5-a1dbcac49d22` (**Aurie AI - Mobile**) | `a13d07fd-f427-4f35-a5af-fba68b015f95` (**Aura - Core App**) |
| Final "zero hits" gate | `git grep "<key>" AuraConsumerApp/` returns 0 | `git grep "<key>" src/ app.js` returns 0 (include repo-root `app.js` — it holds a fetch array too) |

### Web profiles

| Aspect | `repo=aiweb` | `repo=userweb` |
|---|---|---|
| GitHub repo | `AuraHealth/AIWeb` (Aurie web signup/paywall, Next.js) | `AuraHealth/UserWeb` (Aura web onboarding/upsell, Next.js) |
| Local checkout (default guess) | `~/Development/Aura/Web/AIWeb` | `~/Development/Aura/Web/UserWeb` |
| Language | TypeScript | JavaScript |
| Experiment catalog | `AB_EXPERIMENTS` map in `src/lib/constants.ts`, plus the webview-paywall key list in `src/hooks/useWebviewPaywallExperiments.ts`. Used for the surface map only — presence is decided by the per-key grep (Step 3) | **No static catalog.** Keys appear in per-page choose lists (e.g. the array in `src/pages/signup/index.js`) and inline `experiments.<key> === 'a'` guards |
| Code detection grep | `git grep -lI "<key>" -- 'src/'` (bare key literal — catches both the catalog/list entry and property access) | same |
| Variant signal (which letter is treatment) | Consuming checks — `experiments.<key> === 'a'` or `experiments.chosen?.[AB_EXPERIMENTS.<KEY>] === 'a'`. Same org convention: `a = treatment, b/nil = control`. Multi-arm experiments (a letter beyond `a`/`b` with its own guard) do occur — see Step 5 | Consuming check `experiments.<key> === 'a'`. Same org convention |
| Per-experiment extension file | none — inline guards in components/hooks | none — inline guards in components/pages |
| Test-knowledge dir | `e2e/` (Playwright specs) | `tests/` (Playwright specs) |
| Step 2 API slice | `only_ended: true` only, then drop `targetPlatform ∈ {ios, android, mobile}` rows client-side (see Step 2) | same |
| Audience gate | **always flag** audience-gated rows for human review — web has no joined app version (see Step 5) | same |
| Per-experiment card team | `577a9e8b-f2bc-46e1-b170-30aa13e71ee8` (**Web**, `ENGWEB-`) | same |
| Linear project for cards | `fdfd427e-d3aa-44f3-b27d-4736a6226776` (**Aurie AI - Desktop/Web**) | `4ee6841a-f5a7-496e-b546-992cc4ad7cd7` (**Aura - Web**) |
| Final "zero hits" gate | `git grep "<key>" src/` returns 0 | same |

Everything else — the `active`/`endedAt` semantics, the three removal shapes, the readiness gates, the Product/Design team + cycle gate on the parent — is **shared across all profiles**.

**Web scope — client-side experiments only (v1).** Both web repos also consume *backend* experiments (`POST /experiments/list` with server-side assignment — `getActiveBackEndExperiments` in `src/models/experiments.ts` in AIWeb, `src/models/experiments.js` in UserWeb). Backend-experiment keys mostly never appear in client code, so the Step 3 code-presence intersection naturally skips them; when one *does* appear in code it gets a card like any other key. A dedicated backend-experiment sweep is a follow-up, not part of this skill.

**Cross-platform keys.** Web runs use a wider API slice, so a `targetPlatform: null` experiment can legitimately surface in both a mobile run and a web run. That's correct — each repo needs its own removal card — and the project-scoped idempotency check keeps the two cards from suppressing each other.

`<repo-grep-glob>`, `<catalog-source>`, `<simtest-knowledge-dir>` (the test-knowledge dir: sim-test knowledge on mobile, Playwright specs on web), `<card-team-id>`, and `<card-project-id>` below refer to the selected profile's row in these tables.

## Inputs

The skill takes no required arguments. Optional flags:

- `repo=<auraconsumerapp|aura|aiweb|userweb>` — which repo to sweep. Defaults to `auraconsumerapp` (the Swift/Aurie app, unchanged historical behavior). `repo=aura` targets the React-Native Aura app; `repo=aiweb` the Aurie web signup app; `repo=userweb` the Aura web app. The user saying "the Aura app" / "the RN app" selects `aura`; "Aurie" / "the consumer app" selects `auraconsumerapp`; "Aurie web" / "AI web" / "the web signup flow" selects `aiweb`; "Aura web" / "user web" selects `userweb`. A bare "web" with no qualifier: ask which of the two web repos (or run both, one sweep each).
- `repo-path=<abs path>` — override the local checkout path for the selected repo (otherwise the profile's default-guess path is used; if that's missing, ask the user for the path).
- `key=<experimentKey>` — only process this single experiment. Still follows the same flow as the full batch (Step 8 parent batch card → Step 9 single child card under it); the parent card's run summary shows `1 ended experiment` instead of `N`. Useful for re-running on one key after a card refinement.
- `dry-run` — produce the per-experiment plan and the filled template body, but do not call the Linear API. Prints what *would* be filed.
- `limit=<N>` — only process the first N ended experiments after sorting by `endedAt` desc. For staged rollouts.

If the user didn't pass any flags, sweep all ended-and-still-in-code experiments in the default repo (`auraconsumerapp`) and create the full batch.

## Linear IDs used by Steps 8 and 9

The cards filed by this skill always use these IDs, regardless of run mode. The **project** and the **per-experiment card team** are repo-dependent (see the Repo profiles tables); everything else is fixed:

| Concept | ID | Notes |
|---|---|---|
| Parent batch card team | `7e95fa4a-7510-447d-adc7-c663612e70c6` (**Product/Design**) | The cycle gate lives on the Product/Design team |
| Per-experiment card team | `<card-team-id>` from the Repo profiles tables | Mobile (`ENGMOB-`) for the mobile profiles, Web (`ENGWEB-`) for the web profiles — children land in the owning team's queue |
| Per-experiment card template | `de16d5c7-f48d-4386-b024-6f537ffb30f4` (**Remove Experiment**) | Linear workspace template — pre-populates the removal checklist on the Engineering team |
| Project (both cards) | `<card-project-id>` from the Repo profiles table | Aurie AI - Mobile for `auraconsumerapp`; Aura - Core App for `aura` |
| Label (both) | `2511d2ae-b39c-4d2b-bc7c-14ba33d8a1b7` (**Tech Quality**) | Single label; no extras |
| Priority (both) | `3` (Medium) | |
| Assignee (both) | `"me"` (resolved by the API helper to the current viewer) | Per-experiment cards are filed with agent-agnostic content; once a card passes the readiness gates it is routed to an execution agent (`delegate`) in a second call (Step 9) — a pure execution hook that is not part of the issue body |

The mobile IDs were verified against the Linear API on 2026-05-29 and match what the `shared:add-linear-issues` skill uses for the same projects; the Web team + web project IDs were verified on 2026-07-06. Re-verify with `linear_api.py find-issue` / `current-cycle` / etc. if the workspace structure ever changes. The "Remove Experiment" workspace template has only been exercised on Mobile-team issues so far — on the first web run, confirm it renders correctly on a Web-team issue.

## Prerequisites

- The `aura-api` MCP server must be loaded with a valid `AURA_API_TOKEN`. Verify in Settings → Developer (`aura-api` green dot). If missing, point the user at `outputs/aura-api-mcp/README.md`.
- The Linear MCP must be loaded (default in Cowork).
- The user's local checkout of the selected repo must be present and on `main` (or any branch that has the production experiment catalog — the skill reads from disk). For `auraconsumerapp` that's the branch with the production `Experiments.swift`; for `aura`, `aiweb`, and `userweb` it's any branch with the current `src/` (keys are read from the API, intersected with code presence).

## Workflow

### Step 0 — Resolve the repo profile

Read `repo=` (default `auraconsumerapp`) and pick its row from the Repo profiles tables. Resolve the local checkout path from `repo-path=` if given, else the profile's default-guess path; if neither exists on disk, ask the user for the path before continuing. Carry the profile's `<catalog-source>`, `<repo-grep-glob>`, `<simtest-knowledge-dir>`, `<card-team-id>`, and `<card-project-id>` through the rest of the run.

### Step 1 — Enumerate the repo's experiment keys

**`repo=auraconsumerapp`:** Read `AuraConsumerApp/Services/Experiments/Models/Experiments.swift` from the local repo. Parse out every `static let <key> = "<key>"` entry, grouping by surface (`enum Onboarding`, `Feed`, `Home`, `Profile`, `Conversation`, `Player`). Output: `Map<key → surface>` (~36 keys as of 2026-05-05).

**`repo=aura`:** There is no static catalog file, and a reads-only key set is **not** authoritative — many keys are only *registered* in a fetch array (`Experiment.chooseAll(...)` / `Experiment.chooseOnboarding(...)` / the `allExperiments` array) and bucketed, but never read via `experiments?.<key>`. A reads-only grep omits every registered-but-unread key — exactly the ended/dead registrations a sweep must catch. So do **not** build a reads-only key set; intersect by running the Step 4.1 bare-key grep per ended API row (the same approach the web profiles use). The fetch arrays span **five** locations and mix `'single'`- and `` `backtick` ``-quoted literals, so any presence grep must cover all of them:

- `src/screens/Dashboard.js` — the `allExperiments = [ … ]` array
- `src/screens/MeditationPlayer2.js`
- `src/screens/SubscriptionModal3.js`
- `src/screens/SubscriptionModal4.js`
- `app.js` — repo root, **not** under `src/`

Set `{{surface}}` to `—` (no Swift-style surface grouping).

**`repo=aiweb`:** Parse `AB_EXPERIMENTS` in `src/lib/constants.ts` and the webview-paywall key list in `src/hooks/useWebviewPaywallExperiments.ts` to build a key→surface map — surface is the page/hook experiment list that references the key (payment, quiz, get-app, signup/landing, webview paywall; `—` when the key only appears in `AB_EXPERIMENTS`). This map feeds `{{surface}}` only — **presence** is decided per API row by the Step 4.1 bare-key grep, so keys consumed outside the catalog aren't missed.

**`repo=userweb`:** No static catalog and no surface map (`{{surface}}` = `—`). Skip Step 1 entirely — Step 3 intersects by running the Step 4.1 bare-key grep per ended API row.

### Step 2 — Fetch the ended slice from the API

**Mobile profiles:**

```yaml
mcp__aura-api__list_aura_experiments:
  only_ended: true
  only_mobile: true
```

This returns every experiment from the catalog where `endedAt != null` and `targetPlatform ∈ {ios, android, mobile, null}`. The MCP server applies the filter client-side (the API has no server-side `ended` / `platform` filter — `?limit=N` is the only working knob).

> **`endedAt` is not the only "ended" signal.** An experiment can be effectively over via a past `endDate`, a version-cap `endVersion` below the current app version, or a **pinned single-letter `active`** — with `endedAt` still null. The `only_ended` slice keys on `endedAt != null`, so those slip through a purely API-driven sweep. To catch them, periodically run a **code-vs-API reconciliation**: enumerate every experiment key *referenced in the repo* (fetch-array registrations + all read forms in the detection-grep note — `experiments?.`, bracket, `profile.experiments.<key>`, `fbdb.ref('experiments/<key>')`) and check each against `/experiments/<key>` for **any** ended signal (`endedAt`, past `endDate`, `endVersion` < current version, or single-letter `active`) rather than trusting the `only_ended` slice alone. Verified in ENGMOB-2687: `15SecPlayerControlsPodcast` / `50PercentDiscountMobile` (`endDate`, digit-prefixed) and `rainbowBrand2` (`endVersion 3.8.9`, read via `profile.experiments`) were live in Aura code but absent from the `only_ended` slice.

**Web profiles:** `only_mobile` *excludes* `targetPlatform: web`, so call with the ended filter only and slice client-side:

```yaml
mcp__aura-api__list_aura_experiments:
  only_ended: true
```

Keep rows with `targetPlatform ∈ {web, null}`; drop `ios` / `android` / `mobile` rows (they can't have web code). The Step 3 code-presence intersection does the real scoping. If the `aura-api` MCP server later grows an `only_web` flag, prefer it over the client-side slice.

Output: array of API rows, each with at least `id`, `active`, `endedAt`, `notes`, `result`, `targetApp`, `targetPlatform`, `minVersion`, `maxVersion`, `preventOldUsers`, `preventNewUsers`.

The endpoint semantics (what `endedAt` and the `active` letter mean, the lack of server-side filters) are documented in AI Knowledge under `Tools/Aura API` (declared in this skill's `knowledge.reads`) — consult it if a field is unfamiliar. For a richer readout than the row's free-text `result` field (used to fill `{{result_blurb}}` in Step 6), the `Learnings/Experiments` database often has the experiment's hypothesis and outcome; retrieve it with `shared:retrieve-knowledge path=Learnings/Experiments` filtered to the key when the `result` field is thin.

### Step 3 — Intersect with the selected repo's code

For each API row whose `id` matches a key from Step 1, keep the row plus its surface. For the `aura` and web profiles, where Step 1 produced no authoritative key set, intersect by running the Step 4.1 bare-key grep for each surviving API row instead. Drop rows that exist on the server but not in the selected repo's code (those have already been removed, never had code in this repo, or belong to another app). **The code-presence intersection is what scopes the run to the chosen app** — an experiment only gets a card if its key is actually referenced in `<repo-grep-glob>`, so even when the API `targetApp` field is ambiguous, an Aura-repo run only ever covers experiments present in Aura code (and likewise for every other profile).

**Feature flags are never swept.** Drop a record with `featureFlag: true`, or whose `notes` (or `description`) starts with "FEATURE FLAG": it is a kill switch kept on purpose (for example
`editEmailEnabled`), not an ended A/B test, and its code must stay. Count them and state
`skipped.featureFlag: N` with the keys in the run summary and the parent card.

If `limit=N` was passed, sort by `endedAt` desc and slice to N. If `key=<x>` was passed, filter to that one row.

#### Surface the candidate list to the user

Before classifying each candidate, present the candidates back to the user in **two groups** so the audience gate from Step 5 is visible up front:

1. **Standard candidates** — neither `preventOldUsers` nor `preventNewUsers` is true. All-users experiments; safe to process through the normal shape decision.
2. **Audience-gated candidates** — `preventOldUsers == true` OR `preventNewUsers == true`. These were server-side restricted to one audience (new users only, or existing users only). Rows that later resolve to Shape B (Control won) are normal cleanup; do not infer this from the letter alone. Shape A treatment wins with exactly one audience flag and the required bound preserve the original audience boundary with a joined-version guard, while mixed/unclear rows, both-flags rows, or missing bounds are flagged for human review. **On web runs, every row in this group will be flagged** (see the web override in Step 5) — surfacing them up front lets the user scope the run down before any cards are filed.

Within the audience-gated group, sub-label each row as **new-users-only** (`preventOldUsers = true`) or **existing-users-only** (`preventNewUsers = true`) so the user can see which audience was excluded and which version bound must be preserved. The grouped view lets the user spot ahead of time which audience-gated experiments will need guarded treatment removal and decide whether to scope down the run.

### Step 4 — Per-experiment classification (light)

The card is checklist-driven and the implementer owns the code investigation, so the skill does **only** the minimum needed to classify the candidate and fill the lean template. **Do NOT enumerate touched files, capture method names, or compile a per-card sibling file list** — that is the implementer's job (the fixed checklist already tells the implementer to protect sibling experiments). For each surviving row:

1. **Confirm code presence (safety gate + idempotency).** Run a light `git grep` count for the key in the selected repo's checkout, scoped to `<repo-grep-glob>`:
   ```bash
   # auraconsumerapp
   git -C "<repo-path>" grep -lI "<key>" -- 'AuraConsumerApp/*.swift' | wc -l
   # aura — bare-key literal, scoped to src/ AND repo-root app.js; catches
   # fetch-array registrations (either quote style) and inline experiments?.<key> reads
   git -C "<repo-path>" grep -lI "<key>" -- 'src/' app.js | wc -l
   # aiweb / userweb
   git -C "<repo-path>" grep -lI "<key>" -- 'src/' | wc -l
   ```
   ≥ 1 hit means there's code to remove (file a card). Zero hits means the experiment is already gone — skip it. You do **not** need to read the matched files.
2. **Read the variant convention.** For `auraconsumerapp`, the enum doc comment on the key's `static let` in `Experiments.swift` states which letter is treatment (almost always `"a" = treatment, "b"/nil = control`). For `aura`, there is no doc comment — the consuming guard `experiments?.<key> === 'a'` is the runtime signal (same org convention: `a` is treatment). For the web profiles, the consuming guard is likewise the signal — `experiments.<key> === 'a'` in both, or `experiments.chosen?.[AB_EXPERIMENTS.<KEY>] === 'a'` in AIWeb; collect *every* letter the guards check, since web experiments are occasionally multi-arm (e.g. an additional `=== 'c'` guard). Use it to fill `{{original_variants}}` and to decide the shape in Step 5. Only fall back to inspecting more code if the signal is missing or ambiguous.
3. **Record the audience fields** from the API row — `preventOldUsers`, `preventNewUsers`, `minVersion`, and `maxVersion`. These feed the Step 5 audience-gate check and the card's metadata table. The two flags are symmetric — `preventOldUsers = true` means "new users only," `preventNewUsers = true` means "existing users only." Either being true triggers the audience gate.

### Step 5 — Decide the removal shape

Three shapes; pick exactly one based on `{{winner}}` (the API's `active` letter) and the variant convention from Step 4.2:

- **Treatment won** — `{{winner}}` matches the variant called treatment (typically `active = "a"`). If the experiment is not audience-gated, the summary tells the implementer to inline treatment as the normal path and delete the other branches. If the experiment is audience-gated, the audience gate overrides that generic treatment path and keeps treatment only behind the concrete joined-version guard.
- **Control won** — `{{winner}}` matches the control variant (typically `active = "b"`/nil). The summary tells the implementer the default behavior already matches control, so the experiment scaffolding is deleted outright.
- **Mixed / unclear** — `active` is empty, multi-letter, or doesn't match any variant the convention describes. The skill files the card but flags it as needing a human decision; it is **not** marked ready.

**Multi-arm experiments (web).** Web experiments are occasionally more than two arms — e.g. an AIWeb key with both `=== 'a'` and `=== 'c'` consuming guards. A single-letter winner that has its own consuming guard counts as a *described* variant: a non-control letter with a guard is a treatment win for that arm (Shape A, baking in that arm's branch). Only a letter no guard describes makes the row mixed/unclear.

The shape plus audience gate choose the one `{{winner_meaning}}` sentence in the summary (and whether the card is marked ready). Audience-gated treatment wins must use guarded wording that names `{{version_guard}}`; they must never use the generic non-audience-gated treatment wording. The removal **checklist is identical across all cards** except for the audience-gate checklist items. The skill does not translate the shape into per-file edit instructions; the implementer does that.

#### Audience gate — `preventOldUsers` / `preventNewUsers`

After picking a shape, apply the audience gate. The gate fires when **either** `preventOldUsers == true` (experiment restricted to new users only) **or** `preventNewUsers == true` (experiment restricted to existing users only) on the API row. The semantics are symmetric — in both cases, removing the experiment must preserve the audience boundary that the server enforced while the experiment was live.

Let `audienceGated == (preventOldUsers == true || preventNewUsers == true)` and `excludedAudience` be `"existing users"` when `preventOldUsers == true`, or `"new users"` when `preventNewUsers == true`.

**Web override — the audience gate always flags on web profiles.** The joined-version guard is an app-version concept; web users have no joined app version, so the guard that preserves the audience boundary on mobile cannot be expressed in web code. For `repo=aiweb` and `repo=userweb`, **every audience-gated row — all shapes, including control wins — is filed but flagged for human review**, and no `joinedVersion` guard is ever synthesized. The card explains that the audience boundary needs a human-decided web-side predicate (or confirmation that the audience flag never applied to the web surface). The cases below apply to the mobile profiles only.

- **Exactly one audience flag is true and the shape is Control won** → safe to proceed. The winning letter is the codebase's control variant (usually `b`, unless the runtime convention proves otherwise). Removing the scaffolding leaves the excluded audience on their original behavior — they were never on the treatment path, so nothing changes for them. The bucketed audience also lands on default behavior (which is what control already was).
- **Exactly one audience flag is true, `preventOldUsers == true`, and the shape is Treatment won** → remove the experiment, but preserve the new-user-only rollout by keeping a non-experiment condition: the winning feature applies only when the user's joined version is **greater than or equal to the experiment `minVersion`**. The PR title must include `[preventOldUsers]`.
- **Exactly one audience flag is true, `preventNewUsers == true`, and the shape is Treatment won** → remove the experiment, but preserve the existing-user-only rollout by keeping a non-experiment condition: the winning feature applies only when the user's joined version is **less than or equal to the experiment `maxVersion`**. The PR title must include `[preventNewUsers]`.
- **`audienceGated` and the shape is Mixed / unclear** → file the card but flag it for human review. Do not synthesize a joined-version guard for an unresolved winner.
- **Not `audienceGated`** → gate is a no-op; proceed with the shape decision unchanged.

If both audience flags are true, file the card but flag it for human review regardless of the winner, including Shape B control wins. That server state excludes both old and new users, so no automatic replacement predicate is trustworthy.

If the shape is Treatment won and the required version bound is missing (`preventOldUsers == true` with no `minVersion`, or `preventNewUsers == true` with no `maxVersion`), file the card but flag it for human review. The implementer cannot preserve the original audience boundary without the version threshold. Control-won cards do not need a version bound.

See `references/decision-shapes.md` for the canonical rule and the rationale.

### Step 6 — Fill the per-card template

Use `references/per-experiment-card.md` (the v4 template). It has only a handful of placeholders — the body is a fixed summary + metadata table + a removal checklist that is **identical across every card** and **agent-agnostic** (it never names a specific executor). Fill the placeholders from Steps 2–5:

- `{{repo}}` — the selected GitHub repo (`AuraConsumerApp`, `Aura`, `AIWeb`, or `UserWeb`), used in the summary and the catalog/grep references.
- `{{key}}`, `{{surface}}`, `{{ended_at}}`, `{{winner}}`, `{{min_version}}`, `{{max_version}}`, `{{notes_or_dash}}`, `{{result_or_dash}}` — straight from the API row + Step 1 surface map (`{{surface}}` is `—` for `aura` and `userweb`, which have no grouping; for `aiweb` it's the page-list surface from Step 1 when one exists).
- `{{surface_display}}` — the metadata table's "Surface" value, derived from `{{surface}}` + repo: `` `Experiments.{{surface}}.{{key}}` `` for `auraconsumerapp`; `` `{{key}}` `` (the Firebase experiments node) for `aura` and the web repos, with the page-list surface appended for `aiweb` when Step 1 mapped one (e.g. `` `{{key}}` `` — payment). Fill this explicitly; the readiness gate (Step 7.4) rejects any unfilled `{{...}}`.
- `{{original_variants}}` — from the variant convention (Step 4.2).
- `{{result_blurb}}` — if API `result` is non-empty, summarize as one sentence. Empty otherwise.
- `{{winner_meaning}}` — the sentence selected by shape plus audience gate. Non-audience-gated treatment wins use the standard permanent-treatment wording; audience-gated treatment wins use guarded wording that names `{{version_guard}}`; control wins use the control-cleanup wording. See the template's skill-side notes for the exact wording.
- `{{prevent_old_users}}`, `{{prevent_new_users}}`, `{{audience_gate_paragraph}}`, `{{audience_gate_checklist_items}}`, `{{audience_title_tag}}`, `{{version_guard}}` — from the audience gate (Step 5). `{{audience_title_tag}}` is empty for standard experiments, `[preventOldUsers]` when `preventOldUsers == true`, `[preventNewUsers]` when `preventNewUsers == true`, or both tags if both flags are set. `{{version_guard}}` is empty unless an audience-gated treatment win needs a concrete predicate rendered from the API row: `joinedVersion >= {{min_version}}` for `preventOldUsers`, or `joinedVersion <= {{max_version}}` for `preventNewUsers`. **Web profiles:** `{{version_guard}}` is always empty and `{{audience_gate_paragraph}}` / `{{audience_gate_checklist_items}}` always take the human-review wording (web override, Step 5).
- `{{catalog_source}}`, `{{simtest_knowledge_dir}}`, `{{final_grep_path}}` — from the selected repo profile (so the card's checklist references the right paths).
- `{{cycle_or_date}}` — the parent batch card's cycle name, or the ISO run date.

Do **not** synthesize a files list, method names, or sibling-specific warnings — those placeholders no longer exist. Protecting sibling experiments is covered generically by the fixed checklist item ("Be careful about behavior with other live experiments…").

The template's implementation process also defines the PR handoff contract: every removal PR title includes `[preventOldUsers]` or `[preventNewUsers]` when that API audience flag is true, every removal PR description includes an **Experiment result** section copied from the live API row (`endedAt`, `active`, `result` when present, and the variant convention used for cleanup), and every ready-for-review handoff includes the full list of open experiment-cleanup PRs still waiting for human review.

### Step 7 — Readiness safety checks

Before any Linear write happens, the skill runs six gates per card to decide whether it is **ready** (safe to pick up and implement directly) or must be **flagged for a human decision** first:

1. `endedAt` is non-null on the API row.
2. `active` is exactly one letter and matches one of the variants the convention describes.
3. `git grep` (scoped to `<repo-grep-glob>`) returned ≥ 1 hit (else there's nothing in code; no card needed).
4. Every `{{...}}` placeholder in the filled body is gone.
5. The shape decision is one of the three known shapes (treatment / control / mixed).
6. **Audience gate:** on a web profile (`aiweb`, `userweb`), any row with either audience flag true fails this gate outright (web override, Step 5) — the card is filed but flagged. For the mobile profiles: if the API row has `preventOldUsers == true` OR `preventNewUsers == true` (the experiment was restricted to one audience), then first reject the invalid state where both flags are true. Otherwise, the gate passes when either the shape is Control won (removable without changing what either audience sees) or the shape is Treatment won and the required version bound is present for guarded treatment removal (`minVersion` for `preventOldUsers`, `maxVersion` for `preventNewUsers`). If the required bound is missing, the card is flagged for human review. Every ready audience-gated card, including control wins, must have the rendered PR title instruction contain the audience tag (`[preventOldUsers]` or `[preventNewUsers]`). For guarded treatment removal, this gate also requires the rendered card body to contain the exact joined-version predicate with the API value substituted (for example, `joinedVersion >= 4.1.0`, not `joinedVersion >= minVersion`) in the summary/audience note and checklist.

If all gates pass, the card is **ready**. If any gate fails, the card is still filed (so the run is fully traceable) but it is **flagged for human review** — its body carries the mixed-shape or missing-version-bound warning, and it is not routed for automatic execution in Step 9.

### Step 8 — File the parent batch card

The parent batch card is created **on every run** (full batch and `key=<x>` mode alike). It anchors all per-experiment cards from the run under a single Linear card so the work is easy to track.

First, look up the current Product/Design cycle — the parent card's cycle:

```bash
python3 .claude/skills/.../shared/skills/add-linear-issues/scripts/linear_api.py \
  current-cycle --team 7e95fa4a-7510-447d-adc7-c663612e70c6
```

Returns `{"id": "<cycleId>", "name": "<cycleName>", "number": <N>}`. Use `id` as `cycleId` and `name` as the cycle label in the title. If `{"error": "no_active_cycle"}` is returned, omit `cycleId` and substitute the current ISO date for `{{cycle_name}}` in the title.

Then create the parent card (`projectId` is the selected repo profile's `<card-project-id>`):

```yaml
linear_api.py create-issue:
  teamId: 7e95fa4a-7510-447d-adc7-c663612e70c6   # Product/Design
  projectId: <card-project-id>                    # Aurie AI - Mobile (auraconsumerapp) / Aura - Core App (aura)
  cycleId: <id from current-cycle response>
  title: "Clean up inactive experiments — {{repo}} [{{cycle_name}}]"
  labelIds: ["2511d2ae-b39c-4d2b-bc7c-14ba33d8a1b7"]  # Tech Quality
  priority: 3
  assigneeId: "me"
  description: |
    Batch parent for the sweep-ended-experiments skill run on YYYY-MM-DD
    against {{repo}}. Children are one card per ended-and-still-in-code
    experiment — each a self-contained removal task any engineer or AI coding
    agent can pick up.

    **Run summary:**
    - {{N}} cards filed for ended experiments still in {{repo}} code
    - {{T}} treatment-won, not audience-gated (ready to implement)
    - {{C}} control-won, including audience-gated control wins (ready to implement)
    - {{A}} audience-gated treatment-won with the required joined-version guard (ready to implement — mobile profiles only)
    - {{F}} flagged for human decision (mixed/unclear, both audience flags, missing required version bound, failed rendered-guard readiness, or any audience-gated row on a web run)
    - {{S}} skipped before filing (already removed, duplicate open card, or no matching code)
```

Notes:

- The parent card body is **agent-agnostic** — it describes the work, not who executes it.
- **No `parentId`.** The parent batch card stands alone. Each cycle's batch (per repo) is its own root.
- **No `projectMilestoneId`.** Cycle is enough scoping; no milestone is set.
- **Title interpolation:** `{{repo}}` is the selected GitHub repo and `{{cycle_name}}` is the `name` field from the `current-cycle` response (e.g. `Cycle 28`), so the rendered title is `Clean up inactive experiments — AuraConsumerApp [Cycle 28]`. If `current-cycle` returned `no_active_cycle`, fall back to the ISO date.
- **`assigneeId: "me"`** — the script resolves the literal string `"me"` to the current viewer. The skill operator becomes the assignee.
- In **`key=<x>` mode**, the run summary still renders all five filed-card counts (`{{N}}`, `{{T}}`, `{{C}}`, `{{A}}`, `{{F}}`) plus `{{S}}` skipped. If a card is filed, `{{N}} == 1` and exactly one of `{{T}}`, `{{C}}`, `{{A}}`, or `{{F}}` is `1`. If the key is skipped before filing, `{{N}} == 0` and `{{S}} == 1`.

Capture the returned issue `id` for use in Step 9.

### Step 9 — File one card per experiment (then route ready cards for execution)

For each row from Step 5, create a child issue parented to the Step 8 batch card. Per-experiment cards go on the selected profile's engineering team — **Mobile** (`ENGMOB-`) for the mobile profiles, **Web** (`ENGWEB-`) for the web profiles — so the work surfaces in the owning team's queue, even though the parent lives on Product/Design (Linear supports cross-team parent/child relationships). `teamId` is the profile's `<card-team-id>`; `projectId` is the profile's `<card-project-id>`.

```yaml
linear_api.py create-issue:
  teamId: <card-team-id>                          # Mobile (mobile profiles) / Web (web profiles)
  projectId: <card-project-id>                    # matches the parent's project
  parentId: <parent issue ID from Step 8>
  templateId: de16d5c7-f48d-4386-b024-6f537ffb30f4  # "Remove Experiment" workspace template
  title: "Clean up experiment: <key> — variant <winner> rolled out ({{repo}})"
  labelIds: ["2511d2ae-b39c-4d2b-bc7c-14ba33d8a1b7"]  # Tech Quality
  priority: 3
  assigneeId: "me"
  description: <filled, agent-agnostic template body from Step 6>
```

Notes:

- **No `cycleId` and no `projectMilestoneId` on per-experiment cards.** The cycle gate lives on the parent batch card only. Children inherit traceability through the parent.
- **`assigneeId: "me"`** — same as the parent. The skill operator owns the card.
- The issue body never names a specific executor — anyone (a human engineer or any AI coding agent) can pick the card up from its content alone.

**Execution routing (separate, operational step).** After the card is created and its rendering verified, if it is **ready** (all six gates in Step 7 passed), route it to the execution agent so it gets picked up automatically. This is a pure execution hook — it does not modify the issue body and does not change the fact that the card is agent-agnostic:

```yaml
linear_api.py create-issue:
  id: <child issue identifier returned above>
  delegate: Cursor
```

(Two-call pattern is intentional — first create the card with agent-agnostic content, verify rendering, then route it for execution. Matches the validated flow from ENGMOB-1662.)

If the card was **flagged for human review** (a Step 7 gate failed — mixed/unclear, both audience flags are set, the required audience version bound is missing, the rendered guard/tag check failed, or the row is audience-gated on a web run), do **not** route it for execution. It stays filed and assigned to the operator with the warning in its body until a human resolves the approach. Audience-gated treatment-won cards with the required `minVersion` / `maxVersion` guard are ready only when the concrete guard and audience tag render in the card body.

### Step 10 — Report

Print a summary:

```
sweep-ended-experiments ({{repo}}): filed {{N}} cards under {{parent_url}}
  - {{T}} ready to implement (treatment-won, not audience-gated)
  - {{C}} ready to implement (control-won, including audience-gated control wins)
  - {{A}} ready to implement (audience-gated treatment-won with joined-version guard — mobile profiles only)
  - {{F}} flagged — needs a human decision (mixed/unclear, both audience flags, missing required version bound, failed rendered-guard readiness, or any audience-gated row on a web run)
  - {{S}} skipped before filing (already removed, duplicate open card, or no matching code)

Ready cards have been routed for automatic execution and will be picked
up shortly; flagged cards need a human decision first. Audience-gated
treatment-won cards are counted in `{{A}}` only when the required concrete
`joinedVersion` guard is available and included in the card; otherwise
they are counted in `{{F}}`. Watch the parent card for PR links as they
come in.
```

Done.

## Idempotency

Re-running the skill the day after a successful run should report `0 ended-still-in-code experiments` if the removal PRs have all merged. If they haven't merged yet, the skill detects the still-present code and would file *new* cards — to prevent this, the skill checks for an existing open child card for the same key **in the run's repo** before filing.

An open card counts as a duplicate only when **both** of these hold:

1. **Same repo — matched by project, not by title.** The card lives in the run's repo project (`<card-project-id>`). Project is the reliable repo scope: every card has one, including legacy cards filed before titles carried a repo suffix. This is what lets the same server key get its own card in each repo — an open card in one repo's project never suppresses another repo's run, which matters especially for `targetPlatform: null` keys that legitimately need both a mobile and a web card. (Do **not** gate on a `({{repo}})` title suffix; legacy cards don't have it.)
2. **Same key — matched on the delimited title segment, not a bare substring.** The title contains `experiment: <key> —` exactly (the key bounded by the leading `experiment: ` and the trailing ` —` separator that both title formats use). This matches both the current `Clean up experiment: <key> — …` and the legacy `Remove experiment: <key> — …` shapes, so a re-run that straddles the rename doesn't duplicate, while still distinguishing `onboardingFoo` from `onboardingFooBar` (a bare substring check would conflate them).

If an open card satisfies both, it's reported as "already filed" rather than re-created.

## Smart-skip rules (embedded in the card)

The skill does not pre-categorize sim-test/knowledge matches — the implementer does that when working the "Update sim-test knowledge as needed" checklist item, which **carries the rule inline** so the card is self-contained:

- **Edit:** matches that frame the experiment as a live A/B variant ("(treatment variant)", "control arm", "variant a/b", "A/B").
- **Skip:** screen/agent/feature names that merely share a word and stay accurate after removal; audit-log entries in JSON (`simulators.json`, `users.json`); references inside files being deleted entirely. Rule of thumb: if the sentence is still true once the experiment is gone, leave it.

This guidance emerged from the ENGMOB-1662 dry run, where the executing agent correctly skipped three sim-test files and a JSON audit log without prompting — evidence the rule is small enough to live on the card rather than in a separate reference.

For the web profiles, the same rule applies to the Playwright spec dir (`e2e/` in AIWeb, `tests/` in UserWeb): edit specs, fixtures, and helpers that assert or set up a live A/B variant; skip names that merely share a word with the experiment and stay accurate after removal.

## File structure

```
sweep-ended-experiments/
├── SKILL.md                              # this file
├── references/
│   ├── per-experiment-card.md            # v4 Linear card template (summary + metadata + fixed checklist)
│   └── decision-shapes.md                # treatment/control/mixed shapes + audience gate (drives {{winner_meaning}})
└── scripts/
    └── (none required — orchestration is pure markdown + MCP calls)
```

If a future iteration needs deterministic helpers (e.g. a Python script that reads a repo's catalog and emits the key→surface map as JSON), it goes under `scripts/` with a docstring at the top describing inputs and outputs.

## Related skills

- [`data:sync-experiments`](../../../data/skills/sync-experiments/SKILL.md) — companion skill that bulk-imports the experiment catalog into the `Learnings/Experiments` Notion database. This skill (cleanup-card filing) and that one share the same `aura-api` MCP for catalog reads.
- [`data:analyze-experiment`](../../../data/skills/analyze-experiment/SKILL.md) — enriches Notion rows with Mixpanel actuals + learnings after an experiment ends. Independent from this skill but shares the experiment-end signal.
- `aura-bugbot-autofix:fix-bugbot-findings` — reference for the "file the work, an executor implements it" pattern. This skill files the cleanup task; an executor (human or AI agent) does the editing; review bots check the PR; the loop closes.

## References

- ENGMOB-1638 — parent skill ticket
- ENGMOB-1662 — first dry-run card (validated this skill design)
- PR #934 — the removal PR for `onboardingPromptResults` (AuraConsumerApp)
- PROD-2933 — rename + multi-repo support
- `aura-api` MCP server — `outputs/aura-api-mcp/server.py` (or `~/mcp-servers/aura-api/` after install)
