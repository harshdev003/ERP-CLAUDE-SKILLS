# Catalogue change playbook

Source: [PROD-2567](https://linear.app/aura-health/issue/PROD-2567/reference-catalogue-change-playbook-products-packages-prices)
(decision reference, including its detailed-analysis comment). Model context:
[PROD-2564](https://linear.app/aura-health/issue/PROD-2564) (Approach B —
`packageId = f(productId)` via IS lookup) and
[PROD-2534](https://linear.app/aura-health/issue/PROD-2534) (parent design).

Data model: `package` (packageId → entitlements) + `packagePrice`
(productId → packageId, price, period, provider). `resolveAIPackageId(productId)`
reads `packagePrice`.

## The two primitives

- **New `package`** ⟺ a new/different **entitlement set** that must coexist
  with the old one.
- **New `product`** ⟺ a new purchasable **(provider, price, period)**, or a
  SKU to carry a new package at purchase.

## Master "when to create" table

| Need | New product? | New package? | Mode |
| -- | -- | -- | -- |
| New price / period / provider, same entitlements | **Yes** | No | 2a |
| New entitlements offered going forward | **Yes** | **Yes** | 2b |
| New tier | **Yes** | **Yes** | 2c |
| Change entitlements for everyone on a plan | No | No | 1a |
| Move a SKU's holders to a different package | No | only if target is new | 1b |
| Test entitlements (reversible) | **Yes** | **Yes** | 3a = 2b, experiment-gated |
| Test presentation / messaging | No | No | 3b |
| Test a price with no existing SKU | **Yes** | No | = 2a, experiment-gated |
| Test an advertised different plan | **Yes** | **Yes** | = 2b, experiment-gated |

**Throughline:** `package` rows move only on entitlement changes; `product`
rows move only on purchasable-price changes; everything reversible/runtime is
an IS experiment — which adds hidden catalogue rows only when the test changes
prices or entitlements.

## Mode 1 — Migrate all (affects existing users) — *edit in place*

### 1a. Edit a package's entitlements (`package` definition)

- **Create product? No. Create package? No.** You change what an existing
  packageId grants.
- **Timing:** ~immediate — entitlements are resolved at runtime from
  packageId; the subscription is untouched.
- **Use cases:** across-the-board tier improvement ("all Pro users now get
  300 min"); roll a **won experiment** out globally; adjust/fix a limit for
  everyone on a plan.
- **Guardrail:** never *shrink* a live package unless a mass-downgrade is
  intended.

### 1b. Repoint a product → different package (`packagePrice` row edit)

- **Create product? No. Create package? Only if the target package doesn't
  already exist.**
- **Timing:** lazy — flips on each user's next re-validation (+ cache TTL);
  run a backfill + cache invalidation for a prompt/clean cutover.
- **Use cases:** migrate only the holders of a **specific SKU/price cohort**
  to a different package (e.g. move the `$39.99 Pro monthly` cohort to
  `pkg_pro_v2`, leaving Pro-annual on `pkg_pro`) — targeting 1a can't do,
  since 1a hits *every* SKU of a packageId; correct a mis-mapping; consolidate
  a deprecated product onto the current package.

## Mode 2 — Grandfather (new users only) — *create new*

Mechanism: **mint a new product**; existing users keep their old
productId → old packageId. Rule: **product always; package only if
entitlements differ.**

### 2a. Price-only change

- **Create product? Yes. Create package? No** (reuse packageId — entitlements
  unchanged).
- **Use cases:** permanent price change protecting existing subscribers ("new
  Pro buyers pay $44.99; current Pro keeps $39.99"); add a new **period** at a
  price (e.g. introduce quarterly) for the same package; permanent
  regional/promo price.

### 2b. New/different entitlements offered going forward

- **Create product? Yes. Create package? Yes** (new entitlements = new
  packageId).
- **Use cases:** re-package a tier for new buyers ("new Pro includes feature
  X; existing Pro unchanged"); launch a revamped lineup while honoring legacy
  plans.

### 2c. New tier

- **Create product? Yes. Create package? Yes.** Plus assign the Apple
  subscription-group **level**.
- **Use cases:** expand the lineup (4 → 5 tiers), add an entry/premium tier.

## Mode 3 — Test (reversible, per-cohort)

Tests that need a genuinely new price, a new advertised plan, or different
entitlements use the Mode 2 mechanism gated by an experiment (3a). Presentation
tests (3b) and SKU reuse (3c) need no catalogue change:

### 3a. Entitlement test (hidden variant package, experiment-gated)

- **New package, new product(s).** Entitlements are enforced from the package a
  buyer purchased (`user.aiPackageId` → that row's `features`); IntelligenceService
  has no bucket-aware entitlement path, and a `features` overlay on a
  `packageExperiment` row changes only what the paywall shows. A reversible
  entitlement test is therefore Mode 2b gated by an experiment: a hidden variant
  package with its own prices, surfaced to the bucket by a `replace` / `add` row
  (see [experiment-playbook.md](experiment-playbook.md)).
- **Use cases:** A/B different **usage limits** for a tier; toggle a **feature**
  for buyers in a cohort; **tune a limit** before rolling it out (then promote
  the winning package, or 1a bakes the winning limits into the control).
- **Constraint:** buyers keep the variant package after the test — wind-down
  leaves it hidden but live for renewals.

### 3b. Paywall presentation test

- **No catalogue change.** Vary copy, layout, ordering, which **existing**
  products are shown, default/highlighted option.
- **Use cases:** messaging, price anchoring, trial framing, plan ordering,
  "most popular" badge.

### 3c. Reuse an existing SKU

- If a SKU at the desired price **already exists and maps to the same
  package**, offer it — **no new product**.
- **Use case:** re-running a price you previously created a SKU for.

## Guardrails

- Never *shrink* a live package's entitlements (definition edit **or**
  row-repoint) unless a mass-downgrade is the explicit intent.
- Key on `productId`, never price; don't parse price from the SKU string.
- Apple subscription-group **levels** are assigned by tier/value, not price.
- Row-repoint (1b) migrates **lazily** — run a backfill + cache invalidation
  for a clean cutover.

## Price-equality edge (different packages, same price)

Non-issue — the lookup key is `productId`, never price. Two packages at the
same price are two distinct productIds → two packageIds. The **only** failure
mode is wiring a package's paywall offer to a *foreign* SKU because the price
matches (→ mis-sell); always offer the productId that maps to the package
being sold. Keep tier in the SKU id so equal prices don't collide into
identical strings. Also watch for unintended **price inversions** (a higher
tier priced ≤ a lower tier) — system-OK, strategy-check.

## Cross-provider

A migration (1a/1b) or a new plan (2x) must be applied across the Apple +
Stripe + Google rows for that plan together, or cross-provider users on "the
same plan" diverge.
