# Package experiment playbook

Decision logic, the `packageExperiment` row shape, write-time validation, and
the plan template for **package experiments** — copy / feature / price / new-tier
tests resolved server-side inside `/ai/packages/config`.

This draws on the experimentation playbook (rules, edge cases, lifecycle) and the
server-side resolution architecture (the row model and apply order). The
two-mechanism decision reuses the catalogue-change playbook
(see [catalogue-playbook.md](catalogue-playbook.md)).

## What an experiment is, here

A package experiment layers **per-variant changes over the canonical catalogue**
for the users a bucket assigns. ExperimentService stays a pure bucket assigner;
IntelligenceService resolves the variant server-side and returns a per-user
package array. Mobile never sends or claims a variant.

- **Control is the `isAvailable=true` catalogue — never a row.** Control packages
  and prices are decided **purely by the `isAvailable` flag**: the canonical
  catalogue is exactly the `package` / `packagePrice` rows with `isAvailable=true`,
  and only control packages/prices carry it. There is no per-experiment control to
  author — a user in no treatment bucket resolves that same canonical catalogue.
  Never author a "control" `packageExperiment` row.
- **Variant `b` is always control.** By convention every experiment assigns `b` as
  its control bucket. Because control is decided purely by `isAvailable`, **`b` has
  no rows in the `packageExperiment` table** — it resolves the canonical catalogue
  like any unbucketed user. Treatment variants (`a`, `c`, …) carry the rows; a row
  with `variant = 'b'` is always a mistake (see V0).
- **`deletedAt IS NULL` is the on/off control.** A rule is **active while
  `deletedAt IS NULL`**; the set of active (non-deleted) rows is the single
  source of truth for which experiments are live and which variants get assigned.
  Wind-down soft-deletes a rule by setting its `deletedAt`.

## Three axes, two mechanisms

The axis is *what you're testing*; the mechanism is *how the catalogue carries it*
(reuses the catalogue-change playbook's overlay-vs-new-package decision).

| Axis | What it touches |
| -- | -- |
| Visual / copy | package `name`, `displayConfig` (taglines, value props, themes, badges) — presentation only, never enforced |
| Features | what a package entitles the user to — enforced server-side from the **purchased** package row |
| Prices | the price / period / provider offered — needs real store SKUs |

| Mechanism | Meaning | Catalogue rows |
| -- | -- | -- |
| **Overlay** | change an existing package in place for the bucket | no new catalogue rows |
| **New package + prices** | introduce variant packages/prices only the bucket sees | new hidden `package` + `packagePrice` (+ store SKUs) |

A new package **always** implies new prices/SKUs (a store SKU maps 1:1 to a
package). A price-only test is the new-prices mechanism with the new prices
pointing at the **existing** package.

### Decision guide (which mechanism)

| What you're testing | Existing buyers must keep what they have? | Mechanism |
| -- | -- | -- |
| Copy / displayConfig | n/a | Overlay |
| Features or limits (any change to what a buyer is entitled to) | Yes (always) | New package + prices (`replace` / `add`) |
| Price only, same entitlements | (always) | New prices on the existing package (`patch` + `priceIds`) |
| Price only, one provider (e.g. Stripe-only) | (always) | New prices for that provider only — see *Single-provider price test* |
| Price **and** entitlements | Yes (always) | New package + prices (`replace`), priced for the providers under test |
| Add a new tier / extra package for a bucket | n/a | New package + prices |
| Remove a package from a bucket's lineup | n/a | Hide (overlay-style) |

**Rule of thumb:** an overlay changes what the paywall *shows*; only the
package a buyer purchased decides what they *get*. Any entitlement change → new
package + prices. Copy and price sets on unchanged entitlements → overlay.

**Why a `features` overlay never changes entitlements.** A purchase resolves the
bought SKU to its `packagePrice` row and stamps that package on the user
(`user.aiPackageId`). Enforcement reads that package row directly —
`usage.resolveVoiceLimits` takes `features.voice` from it — and never applies
experiment overlays. A `patch` on `features` therefore changes the paywall copy
the bucket sees while every buyer keeps the control package's real limits. Only
`features.voice` is enforced in IntelligenceService; `features.message` is not
read there.

## Row model — four actions

Each `packageExperiment` row carries an `action` and the experiment + variant it
belongs to. There is **no `isActive` column** — a rule is **active while
`deletedAt IS NULL`** (wind-down soft-deletes by setting `deletedAt`). Columns:

| Column | Type | Notes |
| -- | -- | -- |
| `experimentId` | text | The ExperimentService experiment name (buckets/weights live there) |
| `variant` | text | The **treatment** variant slug this row applies to (e.g. `a`, `c`). Never `b` — `b` is control and has no rows (see V0) |
| `action` | text | `patch` \| `hide` \| `replace` \| `add` |
| `controlPackageId` | uuid \| null | Existing package the row acts on; **null only for `add`** |
| `variantPackageId` | uuid \| null | Hidden (`isAvailable=false`) variant package; for `replace` / `add` |
| `name` | text \| null | Overlay name (patch) |
| `description` | text \| null | Overlay description (patch) |
| `displayConfig` | jsonb \| null | RFC 7386 merge patch over the package's `displayConfig` (patch) |
| `features` | jsonb \| null | RFC 7386 merge patch over the package's `features` (patch) — **display-only**: changes what `/config` returns, never what is enforced (see V4) |
| `priceIds` | uuid[] \| null | **Full replacement** price set for the package (patch), across **every** provider (see V6) |
| `deletedAt` | timestamptz \| null | Soft-delete marker. A rule is **active while this is `NULL`**; the assign list is derived from non-deleted rows |

### Action → references → effect (apply order: `createdAt` ascending — later rule wins on overlap)

| Action | References | Overlay fields | Effect for the matched bucket |
| -- | -- | -- | -- |
| `patch` | `controlPackageId` | `name` / `description` / `displayConfig` / `features` and/or `priceIds` | Modify an existing package in place and/or swap its price set |
| `hide` | `controlPackageId` | — | Remove an existing package from the bucket's lineup |
| `replace` | `controlPackageId` + `variantPackageId` | — | Show the hidden variant package **instead of** control, in control's slot — for each provider the variant has prices for; on any other provider's paywall the bucket keeps the whole control package |
| `add` | `variantPackageId` | — | Add a net-new hidden package to the bucket's lineup, in its own slot, on every provider's paywall |

- `patch` overlays compose at **field granularity** — two experiments changing
  *different* fields of the same package both apply. `displayConfig` / `features`
  are merge patches (RFC 7386): a key set to `null` deletes it, absent keys are
  untouched. `priceIds` is a **full replacement** of the package's price set, not
  a merge.
- `replace` / `add` introduce packages that only the bucket sees; their prices
  ride along by package via `packagePrice` rows.
- "Hidden because experimental" and "retired loser" are both `isAvailable=false`;
  they're distinguished only by whether an active (`deletedAt IS NULL`)
  `packageExperiment` row references the package.
- **Apply order is `createdAt` ascending — there is no fixed verb order.** Rules
  are applied oldest-first; when two rules overlap on the same target, the **later
  (higher `createdAt`) rule wins**. Do not rely on a `hide → replace → patch → add`
  precedence; the action verb does not determine ordering.

### Provider scope

Every paywall asks for one provider's prices (`POST /ai/packages/config
{provider}` — `apple` for the native StoreKit paywall, `stripe` for the
WebView). Experiments are applied with that provider in hand, and provider
filtering happens after the rows apply:

| Action | Variant/price set has no prices for the requested provider |
| -- | -- |
| `replace` | The row is skipped for that call: the bucket sees the **whole control package** (name, copy and that provider's prices). A Stripe-only variant is a supported shape — Apple-paywall users in the bucket keep control. |
| `add` | The variant is still added and renders as a tier with **no prices** on that provider's paywall — a card the user cannot buy. |
| `patch` + `priceIds` | `priceIds` replaces the package's prices for **all** providers; a provider with no entry in `priceIds` renders the package with no prices. |
| `patch` overlay fields | Apply on every provider's paywall. |

V6 turns this table into a per-provider check.

### Single-provider price test (same entitlements)

To test a price on one provider only — e.g. a cheaper Stripe price while Apple
stays unchanged — keep the control package and swap only its price set:

1. Create the new store prices for the provider under test (Stripe: test-mode
   for dev, live-mode for prod).
2. Map them to the **control** package as hidden rows (`packagePrice`,
   `isAvailable=false`) so canonical users never see them.
3. One `patch` row with `priceIds` = the new hidden rows **plus** the control's
   existing `packagePrice` ids for every provider that should stay unchanged.

Buyers of the new price get the control package (same entitlements), the
package id the client opens does not change, and no client release is needed.
When entitlements change too, use `replace` with a hidden variant package priced
for the providers under test only — the provider-scope rule above keeps the
others on control.

## Write-time validation (the core of this capability)

Run **all** of these before emitting any row. Report each as PASS / FAIL
(blocking) or WARN (non-blocking). Produce no plan while any FAIL stands.

### V0 — No control row (blocking)

Every row must target a **treatment** variant. A row with `variant = 'b'` (the
control bucket) — or any row that re-states the canonical catalogue rather than
changing it for a treatment bucket — is a control row → FAIL. Control is the
`isAvailable=true` catalogue and is never authored as a `packageExperiment` row.

### V1 — Action shape (mutually exclusive; blocking)

Overlay fields and substitution are mutually exclusive. For the chosen `action`:

| Action | Must have | Must NOT have |
| -- | -- | -- |
| `patch` | `controlPackageId`; at least one of `name`/`description`/`displayConfig`/`features`/`priceIds` | `variantPackageId` |
| `hide` | `controlPackageId` | `variantPackageId`; any overlay field; `priceIds` |
| `replace` | `controlPackageId` + `variantPackageId` | overlay fields; `priceIds` (the variant package carries its own) |
| `add` | `variantPackageId` | `controlPackageId`; overlay fields; `priceIds` |

A `patch` with no overlay field and no `priceIds` is a no-op → FAIL.

### V2 — Referenced rows resolve (blocking)

- `controlPackageId` (patch/hide/replace) resolves to a package present in the
  canonical snapshot (`isAvailable=true`). A control that isn't in the canonical
  set would be silently dropped at runtime → FAIL at authoring instead.
- `variantPackageId` (replace/add) resolves to a real package that is
  **genuinely hidden** (`isAvailable=false`). A variant package that is missing,
  or that is `isAvailable=true`, → FAIL. (An `isAvailable=true` variant would leak
  into the canonical catalogue for everyone.)
- Every `priceIds` entry (patch) resolves to a `packagePrice` row for the
  `controlPackageId`'s package → FAIL on any that doesn't.
- `experimentId` is the **exact ExperimentService key**. Derive it from the
  issue when clearly stated; if absent or ambiguous (e.g. a ticket-title
  prefix like `abc:` that names the variant scheme, not the key), ask the user
  to confirm the literal key → FAIL until confirmed. A mismatched key fails
  open at runtime — assignments never match the rows, so every bucket silently
  resolves control.

### V3 — Same-package-field conflict across active experiments (blocking)

No two **active** experiments may change the **same field of the same package**.
Fetch existing active rows — the authorized list endpoint
`POST /ai/packages/experiments/list` returns active rules (`deletedAt IS NULL`)
**by default** (POST an empty body for all active rows, or a filter body with
any of `action` / `controlPackageId` / `experimentId` / `variant` /
`variantPackageId`; pass `includeDeleted: true` for the rare case you need the
soft-deleted rows). It is restricted to internal callers (admin / service /
system role) and returns the raw rows. Compare against those active rows:

- Same `controlPackageId` **and** an overlapping changed field (`name`,
  `description`, a shared `displayConfig` key, a shared `features` key, or both
  touching `priceIds` / a substitution on the same package) → FAIL, naming the
  colliding `experimentId` / `variant`.
- Different fields of the same package (one tests tagline, another tests value
  props) compose → PASS.
- Different packages always compose → PASS.

If two experiments must touch the same field, fold them into one multi-arm
experiment instead. The runtime has a deterministic `createdAt` fallback, but it
**corrupts measurement** (a user counted in A's treatment could be shown B's
change) — prevent it here, at creation.

> **If the list endpoint is not yet available** (the fetch returns a
> 404 / "Cannot GET" / auth-shape error): you cannot complete V3. Do **not**
> silently skip it — state clearly in the validation findings that the
> same-package-field conflict check could not run because the authorized
> `POST /ai/packages/experiments/list` endpoint isn't deployed, and that the
> plan is conditional on a manual conflict check until it is.

### V4 — Features: overlay is display-only; shrinks warn

- **A `patch` that sets `features` → FAIL until the user confirms.** The overlay
  changes only what the bucket's paywall shows; buyers are enforced against the
  control package's real `features` (see *Why a `features` overlay never changes
  entitlements*). State that consequence and propose a hidden variant package
  (`replace` / `add`). Proceed with the overlay only when the user confirms a
  copy-only test is intended.
- **Shrinks warn (non-blocking).** Diff the variant package's `features`
  (`replace` / `add`) against the package it stands in for. List each entitlement
  that shrinks (a limit lowered, a flag turned off, a feature removed) as a WARN.
  Existing buyers of the control package are unaffected — only people who buy the
  variant get the smaller set — so confirm the shrink is the intended test.

### V5 — Price experiments need real store SKUs (blocking for price tests)

A price test requires real App Store Connect / Stripe / Play products created
**first** (catalogue-change playbook Mode 2a) and a `packagePrice` row per SKU before the
experiment goes live. If the plan introduces prices with no backing store SKU →
FAIL until the SKUs exist (purchase attribution then works automatically — a
purchased variant SKU resolves to its package with no experiment awareness).

### V6 — Provider coverage (blocking for `add` and `priceIds`)

For each provider the paywall serves (`apple`, `stripe`, `google` where it
ships), state what the bucket sees, using the *Provider scope* table:

- `replace` whose variant has no prices for a provider → **INFO**: that
  provider's paywall keeps the whole control package for the bucket. List it so
  the user confirms that is the intended split.
- `add` whose variant has no prices for a provider → **FAIL / ask**: the added
  tier renders with no prices there. Price the variant for that provider, or
  confirm the bucket never reaches that paywall.
- `patch` with `priceIds` → every provider the control package currently has
  prices for must have at least one entry in `priceIds`. A provider left out →
  **FAIL** (its paywall would show the package with no prices).

### V7 — Assignment can heal (blocking)

IntelligenceService stores "not assigned" (`''`) when ExperimentService
returns nothing, and only its `experimentAssigned` listener corrects that later
(see *Assignment mechanics*). The listener handles experiments whose id starts
with `ai` or that sit on IntelligenceService's experiment include list. An
`experimentId` that matches neither → **FAIL**: users who open a paywall before
they are assigned would stay on control permanently. Rename the experiment to
an `ai…` key.

## Authoring the hidden variant package / prices (replace & add)

`replace` and `add` reference a `variantPackageId` that must already exist as a
**hidden** package (`isAvailable=false`) with its own `packagePrice` rows. The
planner outputs the exact rows to create — reuse the
[field-requirements.md](field-requirements.md) `package` / `packagePrice`
templates, with `isAvailable=false`. These are catalogue rows the experiment
references; they are seeded the same way as any other catalogue creation (SQL),
and the store-SKU + SQL-regeneration steps (catalogue workflow steps 6–7) apply
unchanged for any new prices.

A hidden variant package is **not** in the canonical catalogue (V2 enforces
`isAvailable=false`), so it never reaches a non-bucketed user; only the
`replace`/`add` row surfaces it, and only to the assigned variant.

**Mint the variant package id per environment** and put it in the INSERT's
`"id"` column (one UUID for dev, another for prod) instead of capturing a
generated id. The client needs it before the seed lands (next section).

### Client handoff (replace / add)

A bucket's lineup carries the **variant** package id in place of (or next to)
the control's. A client that opens a package by id must use the variant id for
that bucket: AIWeb's package WebView (`{baseURL}/{userId}/package/{packageId}`)
looks the route's `packageId` up in the `/config` lineup, and an id that isn't
there renders an error state. The plan lists, per environment, the variant
package id and the URL form the client opens for the bucket, and the rollout
ships that client change before the bucket can be assigned.

## Assignment mechanics & QA

Bucket assignment is **automatic, lazy, and sticky** — nothing needs to
trigger it:

1. The paywall config request (`POST /ai/packages/config`) resolves the
   canonical catalogue, then applies experiments: the active rows'
   distinct `experimentId`s are collected and resolved for the user. **Every
   user who opens any paywall** is resolved this way — whatever the platform,
   country, or subscription.
2. Per experiment, IntelligenceService first checks the stored assignment
   (cache, then the `userExperiment` table, row id `<userId>-<experiment>`);
   only when none exists does it call ExperimentService
   (`POST /experiments/assign/<userId>`) and persist the result — so
   ExperimentService is consulted once per user per experiment, and the
   assignment is sticky from then on.
3. Rows whose (experimentId, variant) match the assignment are applied. The
   whole path **fails open** — any failure serves the canonical catalogue
   rather than a paywall error, which is also why a wrong `experimentId`
   produces no symptom beyond every bucket seeing control (V2).

**"Not assigned" is stored too.** When ExperimentService returns no variant
(experiment missing, ended, or the user is out of its version window),
IntelligenceService stores `''` and caches it (about a day). It never asks
again. The only thing that corrects it is the `experimentAssigned` listener:
ExperimentService publishes `ExperimentAssigned` whenever it picks a variant for
a user or a variant is set, and IntelligenceService stores that value and clears
the user's cache entry — for `ai…` experiments and its include list (V7).

**Targeting is by joined version only.** ExperimentService's `minVersion` /
`maxVersion` compare the user's **joined** version (`aiJoinedVersion`, falling
back to `aiVersion` for `targetApp: ai`), not the build they run — a `minVersion`
makes the experiment new-users-only. `targetPlatform` and `targetLocales` are not
enforced at assignment, and there is no country targeting, so a bucket reaches
every paywall and storefront its users open (V6 covers what each provider shows).

**Launch order.** The experiment must exist on ExperimentService before its rows
are live, or every paywall opener in between is stored as `''`. To stage rows
ahead of a client release, create the experiment with a `minVersion` no user has
(e.g. `5.0.0`), insert the rows, and lower `minVersion` at launch; the
`ExperimentAssigned` events then correct the stored `''` values as users get
their arms.

**QA.** Put a test user in a bucket with ExperimentService's self route as that
user — `POST /experiments/<experimentId>/set/<variant>` with the user's own token
(`AURA_DEV_API_TOKEN` / `AURA_API_TOKEN`). `set` does not apply the version
window, and the listener carries the value to IntelligenceService, so the next
`/config` call reflects it. (`eng:set-user-experiment` covers other users on
production, via the admin form `/experiments/<experimentId>/set/<userId>/<variant>`.)
Set the bucket **before** calling `/config` as that user. Then verify each bucket
by calling `POST /ai/packages/config` once per provider (`{"provider":"stripe"}`,
`{"provider":"apple"}`) and comparing the lineup against the plan.

QA each bucket **on dev** before anything reaches production: treatment prices
per variant and per provider, control unchanged, other tiers unaffected, a
sandbox purchase of a variant SKU resolving to the right package (the buyer's
`aiPackageId`), and the unbucketed paywall unchanged. Dev's lower-tier `features`
carry small test limits, so compare dev against the dev snapshot, not prod.

## Lifecycle & wind-down

Wind-down is **winner-dependent and must not be left to auto-deactivation** —
per-user assignments are sticky, so ending the experiment server-side does not
revert the catalogue on its own. The dedicated `remove-package-experiment` skill
owns this; the planner only notes the plan for both outcomes:

| Outcome | Action |
| -- | -- |
| **Control wins** | Soft-delete the variant rows (set `deletedAt`). Bucketed users revert to canonical control. For new-package tests, mark the variant packages/prices `isAvailable=false`. |
| **Treatment wins (overlay)** | **Promote** the variant change into the canonical package first, *then* soft-delete the rows (set `deletedAt`). Soft-deleting without promoting snaps everyone — including the winning bucket — back to control. |
| **Treatment wins (new package)** | Promote the variant package to the canonical lineup, mark the old package/prices `isAvailable=false`, archive the losing store SKUs. |

## Pre-launch checklist (carry into the plan)

- [ ] Mechanism chosen per the decision guide (overlay vs new package + prices)
- [ ] No control row — every row is a treatment variant, none is `b` (V0)
- [ ] Action shape valid (V1) — overlay vs substitution not mixed
- [ ] Referenced packages resolve and variant package is hidden (V2)
- [ ] No other active experiment touches the same package-field (V3)
- [ ] Entitlement changes carried by a new package, not a `features` overlay (V4)
- [ ] If price/new-package: real store SKUs created in all relevant stores (V5)
- [ ] Each provider's paywall shows a buyable lineup for every bucket (V6)
- [ ] `experimentId` equals the ExperimentService key exactly (V2) and starts with `ai` (V7)
- [ ] Experiment defined on ExperimentService (buckets/weights, version window) before rows go live — or parked at an unreachable `minVersion`
- [ ] Client opens the variant package id for the bucket (replace / add), per environment
- [ ] Dev seeded and per-bucket mobile-paywall QA passed before any production insert
- [ ] Paywall renders correctly at the variant's package count and price set
- [ ] Wind-down owner/plan noted for both outcomes

## Final experiment-plan template

```markdown
# Package experiment plan — <one-line summary>

**Axis:** <copy | features | price | new tier | hide>
**Mechanism:** <overlay | new package + prices> — <one-line consequence: who sees what>
**Experiment:** `<experimentId>` · treatment variant(s) `<a/c/…>` · control `b` (no rows) (buckets/weights live on ExperimentService)

## Validation findings
- V0 no control row: <PASS / FAIL — any row on variant `b`>
- V1 action shape: <PASS / FAIL — detail>
- V2 referenced rows: <PASS / FAIL — detail>
- V3 same-package-field conflict: <PASS / FAIL / COULD NOT RUN (list endpoint not deployed) — colliding experiments if any>
- V4 features: <PASS / FAIL (features overlay — confirm copy-only) / WARN — shrunk entitlements>
- V5 price/SKU readiness: <PASS / FAIL / n/a>
- V6 provider coverage: <per provider: variant / control / FAIL — no prices>
- V7 assignment can heal: <PASS / FAIL — experimentId not `ai…`>

## 1. Hidden variant package & prices  (replace / add only — else "n/a")
<package INSERT with a pre-minted "id" per env and isAvailable=false; packagePrice INSERTs with isAvailable=false>
<store-SKU + SQL-regeneration notes per the catalogue workflow if new prices>

## 2. packageExperiment row(s) to insert
<the exact row(s): experimentId, variant, action, controlPackageId, variantPackageId,
 name/description/displayConfig/features (merge patches), priceIds — no isActive column;
 a row is active while deletedAt IS NULL>
<given as a runnable INSERT, or as a row table if the model exposes a write path>

## 3. ExperimentService & client
<experiment definition per env: active variants, targetApp, minVersion (or a parked minVersion until launch)>
<client handoff: variant package id per env and the URL the bucket opens — or "n/a" for overlays>

## 4. Wind-down plan
- Control wins: <action>
- Treatment wins: <promote-then-deactivate / new-package promotion> (owned by remove-package-experiment)

> This plan executes nothing. Create store SKUs, seed any hidden package/prices,
> author the experiment on ExperimentService, then insert the packageExperiment
> rows — on dev first, with a per-bucket, per-provider paywall QA pass before
> repeating on production. Re-running this planner is always safe.
```
