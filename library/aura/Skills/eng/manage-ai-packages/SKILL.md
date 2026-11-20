---
name: manage-ai-packages
description: >
  Guided, read-only planner for Aurie AI catalogue changes and package experiments. Resolves
  the change against the catalogue-change and experimentation playbooks, then outputs the
  exact values for the database, App Store Connect, Stripe and Play. Trigger on any change or
  experiment verb (add, create, change, plan, launch, repoint, grandfather, test, A/B) applied
  to a catalogue artifact (package, product, price, tier, SKU, paywall offering, package or
  pricing experiment). Examples: "add a new AI package", "change package entitlements",
  "change AI prices", "introduce a quarterly plan", "new tier on the paywall", "grandfather
  existing subscribers", "plan a pricing experiment", "A/B test the Pro tagline", "add a
  hidden variant package", "what do I need to create in App Store Connect for the new plan",
  "manage AI packages". Do NOT use for a one-off Aura API call (`eng:call-aura-api`),
  assigning a bucket (`eng:set-user-experiment`), or winding down a finished experiment
  (`eng:remove-package-experiment`)

metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index
    - Tools/Aura API
---

# Manage AI packages

## Goal

Produce a complete, validated execution plan for either:

- **A catalogue change** — which `package` / `packagePrice` / `product` rows to
  create or edit, and the exact matching values to configure in App Store
  Connect, Stripe, and Google Play — following the catalogue-change playbook.
- **A package experiment** — the exact `packageExperiment` row(s) to insert
  plus any hidden variant `package` / `packagePrice` rows, with all write-time
  validation run up front — following the experimentation playbook and the
  server-side resolution architecture.

This skill is a **planner, not an executor**. It makes no writes anywhere — not
to the catalogue and not to the `packageExperiment` table. The output is a plan
a human (or a follow-up task) executes. Re-running it is always safe.

## Inputs

- Optional: the user's intent (add package / change package / change prices /
  add prices / **plan a package experiment**) and any specifics (tier, price,
  period, provider, entitlements, trial; for experiments: the experiment name,
  variant(s), axis being tested, target package).
- Required env: `AURA_API_TOKEN` (production) and `AURA_DEV_API_TOKEN` (dev),
  from the `~/.claude/settings.json` `env` block or the shell environment.

## Environments — dev first

Every change runs against two environments, in a fixed order:

| Env | Base URL | Token |
| -- | -- | -- |
| dev | `https://services.dev.aurahealth.io` | `AURA_DEV_API_TOKEN` |
| production | `https://services.aurahealth.io` (script default) | `AURA_API_TOKEN` |

- **Store products are the exception — create them for dev and prod at the
  same time**, as the rollout's first step: App Store Connect SKUs are shared
  by both environments, while Stripe needs a test-mode price (dev) and a
  live-mode price (prod), which get **different** `price_…` ids.
- **Everything else lands on dev first**: SQL regeneration (step 6), the
  `package` / `packagePrice` seed, any `packageExperiment` insert, and
  verification (step 7) — followed by an end-to-end QA pass on the mobile
  paywall screen (per bucket, for experiments). Only after dev QA passes does
  the same sequence run on production.
- **Regenerate SQL per environment — never reuse ids across environments.**
  Stripe `price_…` ids always differ (test vs live), `packagePrice` row ids
  differ, and the row composition itself can differ (e.g. a package may carry
  aura grant rows in prod but not in dev). Resolve every id from that
  environment's own snapshot (`--base-url`), and verify each environment
  separately after its seed.

## Workflow

### 1. Fetch the live catalogue (deterministic)

Run the bundled script — it calls the three Aura API list endpoints and prints
a combined snapshot (packages, package prices, AI products):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/manage-ai-packages/scripts/fetch_catalogue.py"
```

The script targets **production** by default; pass
`--base-url https://services.dev.aurahealth.io` with
`AURA_API_TOKEN="$AURA_DEV_API_TOKEN"` for the dev snapshot. Build the initial
plan from the production snapshot, then fetch each environment's own snapshot
when its rollout step comes up (see *Environments — dev first*).

Use `--json` for the raw payloads when you need exact field values. The script
requests `includeUnavailable: true` for packages and package prices (no
`packageId`/`provider` filter) and `type: "ai"` for products, so the snapshot
includes retired/grandfathered rows — you need those to detect SKU reuse
(playbook mode 3c) and naming collisions, **and** to resolve hidden variant
packages (`isAvailable=false`) for experiments.

For a **package experiment** also pass `--experiments` — it adds the active
`packageExperiment` rows (via the authorized list endpoint) needed for
the same-package-field conflict check (V3 below):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/manage-ai-packages/scripts/fetch_catalogue.py" --experiments
```

If the script fails on a missing/rejected token, stop and ask the user to set
`AURA_API_TOKEN` (or `AURA_DEV_API_TOKEN` for dev) — do not guess catalogue
state from memory. If the
`--experiments` fetch reports the endpoint as *unavailable* (not yet
deployed), carry that forward — V3 becomes "could not run", not skipped.

### 2. Resolve intent

If the user already said what they want, map it. Otherwise ask which of these
they want to do (one at a time — a lineup launch is several of these composed):

1. **Add package** — new entitlement set (usually a new tier or revamped plan)
2. **Change package** — edit an existing package's entitlements or displayConfig
3. **Change prices** — change what new buyers pay for an existing plan
4. **Add prices** — new price point / period / provider for an existing package
5. **Plan a package experiment** — a copy / feature / price / new-tier test
   resolved server-side per bucket → **go to section 8**, the experiment branch.
   Use this whenever the change is per-cohort and reversible rather than a
   permanent catalogue edit.

Steps 3–7 below are the **catalogue-change branch** (intents 1–4). The
**experiment branch** (intent 5) is section 8 — it has its own validation and
plan, then rejoins steps 6–7 only when it introduces new store SKUs.

### 3. Map to a playbook mode

Read [references/catalogue-playbook.md](references/catalogue-playbook.md) and
classify the request into mode **1a / 1b / 2a / 2b / 2c / 3a / 3b / 3c**. The
two questions that decide everything:

- Does the **entitlement set** change? → new/edited `package`.
- Does the **purchasable (provider, price, period)** change? → new `product`.

Confirm the mode with the user before drafting the plan, stating the
consequence in one line (e.g. "Mode 2a — new product, existing subscribers
keep their price; package untouched"). If the request affects existing users
versus only new buyers, this is the step where that must be made explicit —
it's the difference between Mode 1 and Mode 2 and is irreversible if wrong.

### 4. Validate against the live catalogue

With the snapshot from step 1 and the field rules in
[references/field-requirements.md](references/field-requirements.md), check:

- **Missing products** — every (provider × price × period) the plan needs but
  no existing `product` row covers. An existing SKU at the same price mapped
  to the **same** package can be reused (mode 3c); one mapped to a *different*
  package must NOT be offered (mis-sell guardrail). Creating any missing
  products is the **first execution step** in the rollout (DB rows reference
  these SKUs).
- **New packages** — needed only if the entitlement set differs from every
  existing package. Name the closest existing package and why it can't be
  reused.
- **Product naming** — proposed product IDs follow the convention
  (`ai_<tier>_<period>_<price>[_<trial>]`) and collide with nothing in the
  snapshot, including unavailable rows.
- **displayConfig** — required on every new/changed package; must carry
  `theme`, `subtitle`, and a **sectioned** `valueProps` (`new` = what the tier
  adds over the one below, `included` = value carried up from lower tiers;
  entry tier uses `included: []`). `theme` is a **color** key and each `icon`
  must come from
  [references/display-config-registry.md](references/display-config-registry.md);
  append (and flag for client mapping) any new theme/icon a package introduces —
  an unmapped key renders the default. Do not add FE-owned copy
  (`cta`/`eyebrow`/`priceLine`/`secondary`). When sourcing copy from design
  screenshots, map each screenshot to its tier by `theme` color (and price), not
  by the screenshot's marketing title. Borrow structure from other packages in
  the snapshot for consistency.
- **Cross-provider parity** — Apple + Stripe + Google rows for the same plan
  change together.
- **Deactivations** — when a lineup launch or repackage supersedes existing
  SKUs (e.g. legacy/trial price points on a package being repurposed), list the
  `packagePrice` rows — and any whole `package` — to flip to `isAvailable:
  false`. Deactivating stops offering a SKU to new buyers without touching
  existing subscribers; never delete rows. Decide this explicitly — a package
  rarely keeps every legacy SKU when it takes on a new identity.
- **Sanity** — no price inversions (higher tier priced ≤ lower tier), Apple
  subscription-group levels assigned by tier (not price), no live package
  shrinks unless a mass-downgrade is the stated intent.

Surface every gap or violation to the user and resolve each one (ask, don't
assume) before producing the plan.

### 5. Produce the final plan

Output a single markdown plan — see the template in
[references/field-requirements.md](references/field-requirements.md) — with
one section per surface, containing the exact values to create or change:

1. **IntelligenceService database** — `package` rows (id, name, level,
   features, displayConfig) and `packagePrice` rows (productId → packageId,
   provider, availability), given as runnable `INSERT`/`UPDATE` SQL (rows are
   seeded directly — the package model has no write path): one
   `BEGIN … COMMIT` block per step, and every production block preceded by
   the guard query in
   [references/field-requirements.md](references/field-requirements.md).
2. **App Store Connect** — product IDs, subscription group + level, price,
   trial/intro offer.
3. **Stripe** — product + price definitions.
4. **Google Play** — subscription + base plan / offer (when applicable).
5. **ExperimentService** — experiment key, buckets, version window, and what
   each bucket gets (mode 3b, or experiment-gated 2x / 3a only).
6. **Rollout notes** — ordering (create any missing store products first — for
   dev and prod at the same time — then dev DB rows → dev QA on the mobile
   screen → prod DB rows, then flip availability), lazy vs backfill cutover for
   repoints (mode 1b), and what to verify after each step.

End by reminding the user that the plan executes nothing — store consoles and
DB seeds are manual next steps.

### 6. Regenerate the SQL once store products exist

Run this step **once per environment, dev first** — each environment's snapshot
supplies its own Stripe price ids, `packagePrice` row ids, and row composition
(see *Environments — dev first*); never carry ids from one environment's SQL
into the other's.

When the user reports the store products created, re-run the fetch script
(with the environment's `--base-url` and token) and reconcile the new
`product` rows against the plan before any DB seed:

- Confirm each new product's `name`, `revenue`, `period`/`duration`, and
  `trial` match the plan, and that cross-provider prices agree. Flag any
  mismatch (e.g. a Stripe price created at the wrong amount) so it is fixed in
  the store before it reaches a `packagePrice` row.
- Substitute the **real** productIds into the `packagePrice` (and `product`)
  SQL. Stripe price ids (`price_…`) are generated at creation time and are
  unknowable until now — the plan carries placeholders that get replaced here.
  Stripe `product` rows come from PaymentService's `price.created` webhook, so
  confirm they appeared rather than inserting them.

Re-emit the affected SQL with the resolved ids. Still read-only — the skill
only re-reads the catalogue and rewrites the plan's SQL.

### 7. Verify the seeded catalogue

Run this step **per environment as its seed lands** — dev first, then (after
the dev QA pass) production.

When the user reports the DB seeded, re-run the fetch script against that
environment and confirm the live snapshot matches the plan. Produce a
pass/fail checklist covering:

- Every planned `package` present with the right `name`, `level`, `features`,
  and `displayConfig` shape (`theme` + `subtitle` + sectioned `valueProps`).
- Every planned `packagePrice` present, mapped to the right package, with the
  intended `isAvailable`.
- Every planned `product` present with correct price/period/duration/trial.
- Planned **deactivations** applied — the right rows, and only those, flipped
  to `isAvailable: false`.
- Cross-provider parity holds per plan; exactly one `isDefault` package; no
  price inversions (higher tier priced ≤ lower).

Call out anything outside the snapshot the executor still owns — App Store
subscription-group levels, client icon/theme mappings, invite-gating.

### 8. Experiment branch — plan a package experiment

Reached from intent 5. Read
[references/experiment-playbook.md](references/experiment-playbook.md) — it
carries the row shape, the action table, the apply order, and the full
validation contract. Work through it in order:

**8.1 Pick the mechanism, then the action(s).** Apply the experiment decision
guide (overlay vs new package + prices). The mechanism maps to an `action`:

- Copy / presentation overlay → `patch` (overlay `name` / `description` /
  `displayConfig`). A `features` overlay changes only what the paywall shows —
  buyers are enforced against the package they purchased — so it is never the
  mechanism for an entitlement test (V4).
- A price test on unchanged entitlements → `patch` with `priceIds` (full
  price-set replacement across every provider). For one provider only, list the
  new hidden rows plus the control's existing rows for the other providers (the
  playbook's *Single-provider price test*).
- Remove a package from the bucket's lineup → `hide`.
- Any entitlement or limit change, alone or with a price change → `replace`
  (needs a hidden `variantPackageId`). A variant priced for some providers only
  is fine: on any other provider's paywall the bucket keeps the whole control
  package.
- A net-new tier only the bucket sees → `add` (needs a hidden `variantPackageId`,
  priced for every provider the bucket's paywalls ask for).

A single experiment may need several rows (e.g. `hide` the control + `add` two
variant packages); rules apply in **`createdAt` ascending order** (not a fixed
verb order), and a later rule overrides an earlier one on overlap.
Confirm the mechanism/action(s) with the user in one line stating who sees what.

**8.2 Author any hidden variant package / prices (replace / add only).** A
`variantPackageId` must already exist as a hidden package (`isAvailable=false`)
with its own `packagePrice` rows. Draft those rows with the
[field-requirements.md](references/field-requirements.md) hidden-variant
`package` template and `packagePrice` template, `isAvailable=false`, minting the
package id per environment so the client can ship it ahead of the seed. Size
`features` and the "Nx chat time" copy with that file's sizing table. If they
introduce new prices, the store-SKU and SQL-regeneration steps (steps 6–7) apply
to them unchanged.

**8.3 Run write-time validation (the core ask).** Run every check in the
playbook and report each as PASS / FAIL / WARN. Produce **no** plan while any
FAIL stands:

- **V0 no control row** — every row targets a **treatment** variant. Control is
  the `isAvailable=true` catalogue, never a row; by convention `b` is the control
  bucket and must have **no** rows. Any row with `variant = 'b'` → FAIL.
- **V1 action shape** — overlay fields vs substitution are mutually exclusive
  (`patch`/`hide` use `controlPackageId`; `replace` uses both; `add` uses
  `variantPackageId` only). A `patch` with no overlay field and no `priceIds` is
  a no-op → FAIL.
- **V2 referenced rows resolve** — `controlPackageId` is in the canonical
  (`isAvailable=true`) snapshot; `variantPackageId` resolves and is **genuinely
  hidden** (`isAvailable=false`); every `priceIds` entry is a `packagePrice` row
  for the control package; `experimentId` is the exact ExperimentService key
  (derive it from the issue when clearly stated; if absent or ambiguous —
  e.g. a ticket-title prefix like `abc:` that names the variant scheme — ask
  the user to confirm the literal key). A mismatched key fails open: every
  bucket silently resolves control.
- **V3 same-package-field conflict** — no other **active** experiment changes
  the same field of the same package. Check against the `--experiments` rows. If
  the authorized list endpoint is unavailable, report V3 as **could-not-run** and
  make the plan conditional on a manual conflict check — never silently skip it.
- **V4 features** — a `patch` that sets `features` → FAIL until the user
  confirms a copy-only test (the overlay is never enforced); for `replace` /
  `add`, diff the variant package's `features` against the package it stands in
  for and **WARN** on each shrunk entitlement.
- **V5 price/SKU readiness** — a price test needs real App Store Connect /
  Stripe / Play SKUs created first (catalogue-change playbook Mode 2a) and a
  `packagePrice` row per SKU → FAIL until they exist.
- **V6 provider coverage** — per provider, what the bucket sees: a `replace`
  variant without that provider's prices → INFO (control kept); an `add` variant
  without them → FAIL / ask (unbuyable tier); `priceIds` missing a provider the
  control has prices for → FAIL.
- **V7 assignment can heal** — `experimentId` starts with `ai` (or is on
  IntelligenceService's include list); otherwise users stored as unassigned
  before their arm is picked stay on control → FAIL.

**8.4 Produce the experiment plan.** Use the *Final experiment-plan template* in
[experiment-playbook.md](references/experiment-playbook.md): the validation
findings, any hidden variant package/price SQL, the exact `packageExperiment`
row(s) to insert (every column spelled out — `experimentId`, `variant`,
`action`, `controlPackageId`, `variantPackageId`, overlay fields as merge
patches, `priceIds`; there is no `isActive` column — a row is active while
`deletedAt IS NULL`), the ExperimentService definition per environment (active
variants, `targetApp`, version window — `minVersion` compares the joined
version), the client handoff for `replace` / `add` (variant package id per
environment and the URL the bucket opens), and the winner-dependent wind-down
note (owned by `eng:remove-package-experiment`). Remind the user the plan
executes nothing — store SKUs and any hidden package/prices first, then the
experiment on ExperimentService, then the rows (the experiment must exist before
its rows are live, or be parked at an unreachable `minVersion` until launch) —
on dev first, with a per-bucket, per-provider QA pass (the playbook's *QA*
steps) before the same sequence runs on production (see *Environments — dev
first*).

## Output

For a **catalogue change**: a markdown plan as described in step 5, preceded by
the mode classification and the validation findings (missing products / new
packages / reused SKUs).

For a **package experiment**: the experiment plan from section 8.4 — the V0–V7
validation findings, any hidden variant package/price SQL, the exact
`packageExperiment` row(s) to insert, the ExperimentService definition and
client handoff, and the wind-down note.

## Edge cases

- **`AURA_API_TOKEN` / `AURA_DEV_API_TOKEN` missing or rejected** → stop after
  step 1 and ask the user to add it to `~/.claude/settings.json`; never plan
  against a stale snapshot.
- **Experiment key unclear** → when the ExperimentService key can't be derived
  unambiguously from the issue, ask the user for the literal key before
  emitting `packageExperiment` rows (V2).
- **Ambiguous intent** (e.g. "change the price" without saying whether existing
  subscribers move) → ask; the Mode 1 vs Mode 2 distinction must come from the
  user, never be assumed.
- **Permanent change vs experiment unclear** → ask. A reversible, per-cohort
  test is the experiment branch (section 8); a change everyone sees is the
  catalogue branch (steps 3–7).
- **Request shrinks a live package** → warn explicitly and require the user to
  confirm a mass-downgrade is intended (playbook guardrail). For an experiment,
  a smaller entitlement set belongs on a hidden variant package (V4).
- **Authorized list endpoint not deployed** → V3 same-package-field conflict
  can't run; report it as could-not-run and make the experiment plan conditional
  on a manual conflict check. Never emit experiment rows claiming a clean V3.
- **User asks to execute the plan** → out of scope; point at the relevant
  surface (store consoles, DB seeding, ExperimentService, `packageExperiment`
  insert) and offer `eng:call-aura-api` for any read-back verification.
- **Re-run** → safe; the skill is read-only end to end (it never inserts
  `packageExperiment` rows or seeds the catalogue).

## References

- [references/catalogue-playbook.md](references/catalogue-playbook.md) — modes,
  master decision table, scenario-by-scenario use cases, guardrails.
- [references/experiment-playbook.md](references/experiment-playbook.md) — the
  `packageExperiment` row shape, the four actions + apply order, provider scope,
  the single-provider price recipe, the V0–V7 write-time validation contract,
  hidden-variant authoring and client handoff, assignment mechanics and QA,
  wind-down, and the final experiment-plan template.
- [references/field-requirements.md](references/field-requirements.md) — field
  checklists per entity and per surface, product naming convention,
  displayConfig schema, `features` / chat-time sizing, Stripe webhook-created
  product rows, `package`/`packagePrice` SQL templates and the production guard
  query, final plan template.
- [references/display-config-registry.md](references/display-config-registry.md)
  — the supported `theme` colors and `icon` keys for `displayConfig`; a living
  list extended as packages add new ones.
