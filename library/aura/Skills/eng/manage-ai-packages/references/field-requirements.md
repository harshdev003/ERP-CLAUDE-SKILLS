# Field requirements and final-plan template

Field checklists for each catalogue entity, the product naming convention, the
`displayConfig` schema from
[PROD-2584](https://linear.app/aura-health/issue/PROD-2584/intelligenceservice-add-displayconfig-jsonb-column-to-package),
per-surface value checklists, and the template for the final plan.

## Product naming convention

```
ai_<tier>_<period>_<price>[_<trial>]
```

| Segment | Rule | Examples |
| -- | -- | -- |
| `ai` | Fixed prefix for Aurie AI subscriptions | `ai` |
| `tier` | Package tier slug, lowercase | `lite`, `grow`, `bloom`, `flourish` |
| `period` | Billing period | `monthly`, `yearly` |
| `price` | Price in cents, no separator (USD list price) | `1999` = $19.99, `3999` = $39.99 |
| `trial` | Optional trial suffix | `3days`, `7days` |

Examples: `ai_lite_yearly_4999`, `ai_bloom_monthly_3999_3days`.

Rules:

- The tier **must** be in the id so equal prices on different tiers never
  collide into identical strings.
- The price segment is **naming only** — never parse price (or anything else)
  back out of the SKU string at runtime; the lookup key is always `productId`.
- A price change for new buyers = a **new** productId (the old SKU keeps its
  name and its existing subscribers); never rename or re-price an existing SKU
  in place.
- Check proposed ids against the full snapshot **including unavailable rows**
  — store product ids are globally unique forever in App Store Connect.

## `package` row

| Field | Required | Rule |
| -- | -- | -- |
| `id` (packageId) | Yes | Stable identifier; new id ⟺ new entitlement set |
| `name` | Yes | Display name (top-level column, not duplicated in displayConfig) |
| `level` | Yes (NOT NULL) | Paywall tier level — **higher level = higher tier** (top-level column) |
| `features` | Yes (NOT NULL) | Machine-readable flags + limits. Backend-enforced. Never rendered. |
| `displayConfig` | Yes (NOT NULL) | Presentation-only blob, schema below. **Required on every package** — must always carry `theme`, `subtitle`, and `valueProps`. Never enforces anything. |

### `displayConfig` schema (PROD-2584 column; lineup shape)

`displayConfig` is **required on every package** and must always include
`theme`, `subtitle`, and `valueProps`. `badge` is optional.

```jsonc
{
  "theme": "purple",          // a COLOR key into the app's theme registry — never the package name, never a hex/asset URL
  "subtitle": "Everything for healing and growth, in one membership",
  "valueProps": {
    "new": [                  // what this tier adds over the one below it
      { "icon": "time",    "title": "10x chat time",   "description": "For deeper, more regular work" },
      { "icon": "library", "title": "Aura app access", "description": "The full Aura app — meditations, sleep, and wellness content" }
    ],
    "included": [             // value carried up from lower tiers; [] for the entry tier
      { "icon": "memory", "title": "Permanent memory", "description": "Aurie never forgets your story" }
    ]
  },
  "badge": null               // optional override; "Save X%" is computed FE from prices
}
```

- `theme` is a **color** (e.g. `green`, `pink`, `purple`, `gold`), and each
  `icon` is a **key** the app maps to its design system (gradient/orb/SF
  Symbol). An unknown key falls back to a safe client default. The supported
  themes and icon keys are listed in
  [display-config-registry.md](display-config-registry.md) — append any new
  theme/icon there **and flag it for client mapping**, since an unmapped key
  renders the default.
- `valueProps` is **sectioned**: `new` (what this tier adds over the one below)
  and `included` (value carried up from lower tiers). The entry tier has
  `included: []`.
- Copy the paywall shows around the props — eyebrow, price line, CTA label,
  secondary action — is **FE-owned and not stored** in `displayConfig`; do not
  add `cta`/`eyebrow`/`priceLine`/`secondary` keys.
- When sourcing copy from design screenshots, map each screenshot to its tier by
  `theme` **color** (and price), not by the screenshot's marketing title — the
  modal name often differs from the tier slug.
- When drafting a new package's `displayConfig`, mirror the structure, icon
  keys, and tone of the existing packages in the live snapshot — consistency
  across tiers matters more than novelty.
- `features` stays the enforcement config; never move entitlement data into
  `displayConfig` or presentation copy into `features`.

### Sizing `features` and the chat-time copy

The lineup's "Nx chat time" value prop counts from **Lite = 1x**, on weekly
voice (`features.voice.weeklyLimit`, ms):

| Tier (prod) | Weekly voice | Weekly messages | Copy |
| -- | -- | -- | -- |
| Lite | 12,000,000 ms (≈3h20m) | 500 | 1x |
| Plus | 36,000,000 ms (10h) | 1,500 | 3x |
| Growth | 72,000,000 ms (20h) | 3,000 | 6x |
| Flourish | 96,000,000 ms (≈26h40m) | 4,000 | 8x |

- Multiplier = `weeklyLimit / 12,000,000`; hours = multiplier × 3h20m. State the
  exact multiplier when it isn't a whole number (14h = 4.2x) and let the user
  pick the copy — copy should round down, never overclaim.
- Keep `features.message.weeklyLimit` proportional (500 per 1x). Daily caps
  (`voice.dailyLimit` 14,400,000 ms, `message.dailyLimit` 550) are the same on
  every paid tier.
- Only `features.voice` is enforced in IntelligenceService; message limits are
  shown and enforced elsewhere.
- Dev's Lite and Plus carry small test limits — size from the **prod** snapshot.

## `packagePrice` row

The mapping row read by `resolveAIPackageId(productId)`. It carries **no price
or period itself** — those live on the `product` row.

| Field | Required | Rule |
| -- | -- | -- |
| `productId` | Yes | The SKU string (naming convention above); the lookup key. Stripe rows use the Stripe price id (`price_…`) as the productId — that id is generated when the Stripe price is created, so it is unknowable at planning time and gets filled in during SQL regeneration. |
| `packageId` | Yes | Must reference an existing (or in-this-plan) `package` row |
| `provider` | Yes | `apple` / `stripe` / `google` / `aura` (internal grants) — one row per provider SKU |
| `isAvailable` | Yes | New rows start unavailable until store products are live |

## `product` row (catalogue `products` table)

The purchasable definition, keyed `<provider>-<name>` (e.g.
`apple-ai_monthly_1999`). This is where price and period actually live.

| Field | Required | Rule |
| -- | -- | -- |
| `name` | Yes | The SKU string — must equal the `packagePrice.productId` and the store product id |
| `provider` | Yes | `apple` / `stripe` / `google` / `aura` |
| `revenue` | Yes | List price in currency units (e.g. `19.99`) — matches the SKU's price segment and the store price |
| `period` / `duration` | Yes | e.g. `year`/1, `month`/1 — matches the store subscription period |
| `trial` | If trial | Trial length in days — matches the `<trial>` SKU segment and the store intro offer |
| `aiPlan` | Yes | `true` — marks the product as an Aurie AI plan (`type=ai` filter) |
| `active` | Yes | New rows can stay inactive until launch |

**Stripe `product` rows are written for you.** PaymentService's `price.created`
/ `price.updated` webhook upserts `stripe-<price id>` with `revenue`, `period`,
`duration` and `trial` from the Stripe price, and sets `aiPlan` when the Stripe
product's name contains `Aura AI`, `AI ` or `Aurie`. Plan to **verify** those
rows (step 6), not to insert them; if one is missing, the webhook didn't land —
resend the `price.created` event from Stripe. A price nickname ending in `xdays`
or containing `_xdays_` sets `dynamicTrial`, so keep that pattern out of
nicknames.

**Omit every column that doesn't apply — never write `false` or `0`.** The
catalogue represents "not this kind of plan" as NULL, and PaymentService reads
it that way: `productModel.list()` translates a false flag into an `IS NULL`
predicate (`src/models/product.js`), so a row carrying an explicit `false` is
invisible to the very filter meant to find it. The rule covers the plan flags
an Aurie SKU doesn't use — `businessPlan`, `coachPlan`, `coachingPlan`,
`communityPlan`, `contentPlan`, `familyPlan`, `videoPlan`, `dynamicTrial` —
and the optional integers `maxFamilyPlanMembers`, `videoCredit` and `trial`,
which stay unnamed rather than `0` when the plan carries no trial or credit.
Name only the columns in the table above; the rest stay out of the INSERT
entirely.

Cross-provider parity: a plan needs its Apple + Stripe + Google rows created
or repointed **together** — same packageId, equivalent price/period — or
cross-provider users on "the same plan" diverge.

## Per-surface checklists

- `package` insert/update (id, name, level, features, displayConfig) — note:
  the IntelligenceService package model has no write path
  (`retrieve`/`list`/`getConfig` only); rows are seeded directly by SQL.
- `packagePrice` insert/repoint per provider SKU.
- `product` row per provider (the table behind `/products/list`) with
  `aiPlan: true` and revenue/period/duration/trial matching the store config —
  Stripe rows arrive through the webhook above and are verified, not inserted.
- Mode 1b repoints: plan the backfill + cache invalidation for a clean
  cutover (otherwise lazy — next re-validation + TTL).

Give `package` and `packagePrice` changes as runnable SQL in the plan, one
`BEGIN; … COMMIT;` block per rollout step so a step lands whole or not at all.
Precede every **production** block with a guard query, because a local Postgres
can listen on the same `127.0.0.1:5432` a prod tunnel uses and answer with a
plausible scratch copy:

```sql
SELECT current_database(), current_schema(), inet_server_addr(),
       (SELECT COUNT(*) FROM conversation) AS conversations,
       (SELECT COUNT(*) FROM "package" WHERE id = '<known prod package id>') AS known_package;
```

Proceed only on `intelligenceDB` / `public`, a non-local server address, a
production-scale conversation count, and `known_package = 1`.

**New package (mode 2b/2c):**

```sql
INSERT INTO "package" ("name", "level", "features", "displayConfig", "isAvailable", "isDefault")
VALUES (
  'Aurie <Tier>',
  <level>,                                 -- higher = higher tier
  '<features-json>'::jsonb,
  '<displayConfig-json>'::jsonb,
  false,                                   -- start unavailable until store products are live
  false
)
RETURNING "id";                            -- capture the generated packageId for the packagePrice rows
```

**Hidden variant package for an experiment** — mint the id per environment
(a fresh UUID for dev, another for prod) so the client can ship it before the
seed lands:

```sql
INSERT INTO "package" ("id", "name", "description", "level", "features", "displayConfig", "isAvailable", "isDefault")
VALUES (
  '<minted uuid for this env>',
  'Aurie <Tier>',
  'Aurie <Tier> — <what differs>',         -- internal label; the paywall shows displayConfig.subtitle
  <level of the package it stands in for>,
  '<features-json>'::jsonb,
  '<displayConfig-json>'::jsonb,
  false,                                   -- hidden: only the experiment row surfaces it
  false
);
```

**Edit an existing package's entitlements / presentation (mode 1a):**

```sql
UPDATE "package"
SET "features" = '<features-json>'::jsonb,
    "displayConfig" = '<displayConfig-json>'::jsonb,
    "level" = <level>,
    "updatedAt" = NOW()
WHERE "id" = '<packageId>';
```

**Map a SKU to a package — one row per provider (insert for a new SKU, or
repoint via `UPDATE … SET "packageId"` for mode 1b):**

```sql
INSERT INTO "packagePrice" ("packageId", "productId", "provider", "isAvailable")
VALUES
  ('<packageId>', '<apple-productId>',  'apple',  false),
  ('<packageId>', '<stripe-price-id>',  'stripe', false);
```

**Deactivate a superseded / legacy SKU — flip availability, never delete:**

```sql
UPDATE "packagePrice"
SET "isAvailable" = false,
    "updatedAt" = NOW()
WHERE "packageId" = '<packageId>'
  AND "productId" IN ('<legacy-sku-1>', '<legacy-sku-2>');
```

Deactivating a `packagePrice` stops offering that SKU to new buyers without
touching existing subscribers — keep the `product` row `active` so in-flight
renewals still resolve. Use it to retire legacy/trial price points a repackaged
tier no longer sells. To retire a whole package, set the `package`'s
`isAvailable = false` the same way.

### App Store Connect

- Product ID (= `productId`, convention above).
- Subscription group; **level within the group assigned by tier/value, not
  price** (upgrade/downgrade behavior follows level).
- Price; intro/trial offer matching the `<trial>` segment if present.

### Stripe

- Product (one per plan) + Price (one per price/period). Add a new price to the
  plan's existing Stripe product; its name must contain `Aurie` (or `Aura AI` /
  `AI `) so the webhook flags the row `aiPlan`.
- The `packagePrice.productId` is the Stripe price id (`price_…`) itself, so
  nothing in our code reads the lookup key. Set one for the dashboard anyway,
  following the SKU convention with the plain tier slug:
  `ai_<tier>_<period>_<price>` (e.g. `ai_growth_yearly_15999`).
- Create each price in **test mode** (dev) and **live mode** (prod); the two
  get different `price_…` ids.

### Google Play

- Subscription + base plan (period, price) and offer (trial) when the plan
  ships on Android.

### ExperimentService (mode 3b or experiment-gated 2x / 3a)

- Experiment key (starts with `ai`), active variants (`b` = control),
  `targetApp: ai`, and the version window — `minVersion` compares the user's
  joined version, so it scopes the test to new users.
- What each bucket gets: a presentation overlay (3b), or a hidden variant
  package / price set (experiment-gated 2a / 2b / 3a). The row shapes and
  launch order are in [experiment-playbook.md](experiment-playbook.md).

## Final plan template

```markdown
# Catalogue change plan — <one-line summary>

**Mode:** <1a|1b|2a|2b|2c|3a|3b|3c> — <one-line consequence, incl. who is affected>
**Validation findings:** <missing products / new packages / reused SKUs / warnings>

## 1. IntelligenceService DB
<package rows to create/edit — runnable INSERT/UPDATE SQL incl. features + displayConfig>
<packagePrice rows to create/repoint — runnable INSERT/UPDATE SQL, one per provider>
<deactivations — UPDATE … SET isAvailable=false for superseded/legacy SKUs or packages>

## 2. App Store Connect
<product id, group, level, price, trial>

## 3. Stripe
<product, price, metadata>

## 4. Google Play
<subscription, base plan, offer — or "n/a">

## 5. ExperimentService
<experiment key, buckets, overlay — or "n/a">

## 6. Rollout order & verification
1. **Create any missing store products first** (Apple/Stripe/Google) — **for dev and prod at the same time** (App Store Connect SKUs are shared; Stripe needs a test-mode and a live-mode price, with different `price_…` ids) — and wait for approval where needed. Every required (provider × price × period) with no existing `product` row. This is always the first step; DB rows reference these SKUs.
2. **Regenerate the SQL with the real product ids, per environment (dev first)** — re-fetch that environment's snapshot, replace Stripe `price_…` placeholders with its generated ids, and confirm each product's price/period/duration/trial matches the plan before it reaches a `packagePrice` row. Ids never carry across environments — `packagePrice` row ids and even row composition (e.g. aura grant rows) can differ between dev and prod.
3. Seed the **dev** DB rows via the SQL above (packages, packagePrices, products, deactivations), then QA end-to-end on the mobile screen.
4. **Verify** with the fetch script, per environment: packages/prices/products match the plan, deactivations applied, exactly one default, no price inversions.
5. After dev QA passes, repeat the seed on **production** with production-resolved ids, verify, then flip availability / launch experiment; <mode-specific: backfill + cache invalidation for 1b, etc.>.

### Post-seed verification checklist
- [ ] Every planned `package` present with the right `name`, `level`, `features`, and `displayConfig` shape (`theme` + `subtitle` + sectioned `valueProps`).
- [ ] Every planned `packagePrice` present, mapped to the right package, with the intended `isAvailable`.
- [ ] Every planned `product` present with correct `revenue`/`period`/`duration`/`trial`; cross-provider prices agree per plan.
- [ ] Planned **deactivations** applied — the right rows, and only those, flipped to `isAvailable: false`.
- [ ] Exactly one `isDefault` package; no price inversions (higher tier priced ≤ lower).
- [ ] Out-of-snapshot items called out for the executor — App Store subscription-group levels, client icon/theme mappings, invite-gating.

> This plan executes nothing — apply each surface manually and re-run
> manage-ai-packages afterwards to verify the snapshot matches the plan.
```
