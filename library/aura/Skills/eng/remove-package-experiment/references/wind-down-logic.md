# Package-experiment wind-down logic

The full decision detail for `remove-package-experiment`. The `SKILL.md`
workflow references this file at step 3 (classify) and step 4 (build the plan).

## Background: how a package experiment resolves

(Resolved server-side in IntelligenceService.)

The canonical catalogue is three entity types:

- **`package`** — an entitlement set. Fields that matter here: `id`, `name`,
  `description`, `displayConfig` (JSON), `features` (JSON), `level` (the
  subscription slot / tier rank), `isDefault` (exactly one true), `isAvailable`.
- **`packagePrice`** — maps a purchasable `productId` (a store SKU) to a
  `packageId` for a `provider`, with `isAvailable`.
- **`product`** — the purchasable (provider, price, period) — the store SKU.

A **`packageExperiment`** row layers a per-variant override on top, resolved
server-side when a bucketed user calls `POST /ai/packages/config`. Each row has:

| Field | Meaning |
|---|---|
| `experimentId` | the ExperimentService experiment name it belongs to (column is `experimentId`, holds the name) |
| `variant` | the **treatment** bucket this row applies to (`a`, `c`, …) — never `b`, the control bucket (see below) |
| `action` | one of `patch` / `hide` / `replace` / `add` |
| `controlPackageId` | the canonical package the row targets (patch / hide / replace); null for `add` |
| `variantPackageId` | the hidden (`isAvailable=false`) variant package the row introduces (replace / add); null for patch / hide |
| field changes | `name`, `description`, `displayConfig`, `features`, and/or `priceIds`, depending on action |
| `deletedAt` | soft-delete timestamp — the override is **active while `deletedAt IS NULL`**; winding a row down sets `deletedAt = NOW()` (there is no `isActive` column) |

> **Control is the `isAvailable=true` catalogue, never a row.** Control packages
> and prices are decided purely by the `isAvailable` flag. By convention variant
> `b` is the control bucket, so it has **no `packageExperiment` rows** — a `b`
> user (like any unbucketed user) resolves the canonical catalogue. Every row you
> wind down belongs to a treatment variant (`a`, `c`, …). On a **control win**
> there is nothing in the canonical catalogue to promote — control already *is*
> the canonical catalogue.

### The four actions

- **`patch`** — overlay field changes onto an **existing** canonical package
  (e.g. different `displayConfig` copy, different `features` limits, a different
  price set) without introducing a new package. Reversible by definition while
  active.
- **`hide`** — suppress an existing canonical package for the variant bucket
  (it doesn't appear in their resolved catalogue).
- **`replace`** — show a **new** variant package *in place of* a control package
  (same `level` slot) for the variant bucket.
- **`add`** — show a **new** variant package *in addition to* the canonical
  lineup for the variant bucket.

`patch` + `hide` are **overlays** (they modify how an existing package
resolves). `replace` + `add` are **new-package** tests (they introduce a
package that exists in the catalogue as `isAvailable=false` until promoted).

## Why ending the experiment does not revert the catalogue

Per-user assignments are **sticky**: ExperimentService remembers each user's
bucket. Ending the experiment server-side stops *new* assignments but does not
move already-assigned users out of their bucket. Server-side resolution applies
overrides **while the experiment rows are active (`deletedAt IS NULL`)**,
regardless of the experiment's `endedAt`. So:

- Leaving the rows active forever = the experiment never actually ends for
  assigned users.
- Soft-deleting the rows (set `deletedAt = NOW()`) = resolution falls back to
  canonical for everyone.

Neither is automatically "the winner won". You must choose the steady state
deliberately — that's the whole point of this skill, and why **auto
soft-deletion is the wrong default**.

## The outcome decision matrix

| Winner | Winning/variant row action | Path | Promotion needed? |
|---|---|---|---|
| Control | `patch` | **A** | No |
| Control | `hide` | **A** | No |
| Control | `replace` | **A** (+ retire variant pkg/prices) | No |
| Control | `add` | **A** (+ retire variant pkg/prices) | No |
| Treatment | `patch` | **B** | **Yes — promote overlay first** |
| Treatment | `hide` | **B** | **Yes — retire hidden pkg first** |
| Treatment | `replace` | **C** | **Yes — promote new pkg first** |
| Treatment | `add` | **C** | **Yes — promote new pkg first** |

### Path A — Control wins (soft-delete only)

The control catalogue is already the canonical catalogue, so nothing is
promoted. Steps:

1. Every `packageExperiment` row for the experiment: soft-delete it —
   `UPDATE "packageExperiment" SET "deletedAt" = NOW() WHERE "id" = …`.
2. **`replace`/`add` rows only:** the variant **package** and all its
   `packagePrice` rows: `isAvailable: true → false` (retire — nobody won with
   them). For `replace`/`add` the variant package was created `isAvailable=false`
   and only made visible to the bucket via resolution; if it was ever flipped
   available, retire it now.
3. No canonical `package` change.

### Path B — Treatment wins, overlay (`patch` / `hide`) — PROMOTE FIRST

The override only exists because the rows are active (`deletedAt IS NULL`).
Soft-deleting without promoting reverts the winning bucket to control. So
**promote into the canonical `package` row first**, then soft-delete.

**Promotion mechanics by what the `patch` carries:**

- **`name` / `description`** — **whole-value replace** on the canonical
  `package`. These are scalar strings; the variant value wins outright.
- **`displayConfig` / `features`** — **RFC 7386 JSON Merge Patch** (see below).
  The variant carries a *partial* object; merge it into the canonical object.
- **Price-set patch** — the variant `patch` swapped which `packagePrice` rows
  apply. Promote by flipping the **variant** price rows to canonical
  (`isAvailable: true`) and **retiring the old** canonical price rows
  (`isAvailable: false`). Key on `productId`, never on the price string
  (catalogue-change playbook guardrail).
- **`hide`** — the winning behaviour is "this package is gone". Promote by
  retiring the package: `isAvailable: true → false` on the canonical `package`
  and all its `packagePrice` rows. (No field merge; the package simply leaves
  the lineup.)

Then: soft-delete every experiment row —
`UPDATE "packageExperiment" SET "deletedAt" = NOW() WHERE …`.

### Path C — Treatment wins, new package (`replace` / `add`) — PROMOTE FIRST

The variant package exists in the catalogue as `isAvailable=false`, shown only
to the bucket via resolution. Promote it into the real lineup first, then
retire what it beat, then soft-delete.

1. **Promote the variant package:** `isAvailable: false → true`.
   - **`replace`:** the variant package inherits the **`level`** (subscription
     slot / tier rank) of the control package it replaces — copy the control
     package's `level` onto the variant package so it occupies the same slot.
     Apple subscription-group **levels are assigned by tier/value, not price**
     (catalogue-change playbook guardrail).
   - **`add`:** the variant package takes whatever `level` it was defined with;
     it's an addition, not a slot swap. **Re-level the canonical lineup to match
     what the bucket saw.** At runtime `add` inserts the variant at its `level`
     and shifts every canonical package at that `level` or higher **up by one**
     (in memory only). Promotion must reproduce that: bump the `level` of each
     surviving canonical package at `≥` the variant's `level` up by one, so the
     promoted lineup matches the experiment and `level` stays a unique ordered
     slot. Without this, the variant collides on `level` with an existing package
     and ordering falls to the `createdAt` tiebreak.
   - Verify **exactly one** `isDefault` package survives across the lineup after
     promotion. If `replace` swapped the default tier, move `isDefault` onto the
     promoted package and clear it on the retired one.
2. **Retire the old package + prices** (`replace` only — `add` has no old
   package to retire): `isAvailable: true → false` on the control `package` and
   all its `packagePrice` rows.
3. **Archive the losing store SKUs** — manual checklist (see below).
4. Soft-delete every experiment row —
   `UPDATE "packageExperiment" SET "deletedAt" = NOW() WHERE …`.

## RFC 7386 JSON Merge Patch (for `displayConfig` / `features`)

When promoting a `patch` that carries a partial `displayConfig` or `features`
object, merge it into the canonical object per
[RFC 7386](https://datatracker.ietf.org/doc/html/rfc7386):

- For each key in the patch:
  - value is an **object** → recurse (merge into the same key in the target,
    creating it if absent).
  - value is `null` → **delete** that key from the target.
  - value is a **scalar or array** → **replace** the target's value outright
    (arrays are not element-merged — the whole array is replaced).
- Keys present in the target but absent from the patch are left untouched.

Worked example:

```
canonical.displayConfig = {
  "theme": "blue",
  "subtitle": "Your plan",
  "valueProps": { "new": ["A"], "included": ["B"] }
}
patch.displayConfig = {
  "subtitle": "Best value",
  "valueProps": { "new": ["A", "C"] },
  "badge": null
}
promoted.displayConfig = {
  "theme": "blue",                       // untouched
  "subtitle": "Best value",              // replaced
  "valueProps": { "new": ["A","C"],      // array replaced (not merged)
                  "included": ["B"] },   // untouched (recursed)
                                         // "badge" deleted if it existed
}
```

Always show the resolved promoted object in the plan's before/after so the user
can eyeball it before confirming the write.

## Store-SKU archival checklist (Path C, operational)

Archiving store SKUs is **not automatable from this skill** — it's manual
console work, called out by the catalogue-change playbook.
Present it as a checklist with the **exact retired product IDs** from the plan:

- [ ] **App Store Connect** — mark each retired subscription product as no
      longer available for new buyers (do **not** delete; existing subscribers
      keep their entitlement). Leave the subscription-group **level** intact.
- [ ] **Stripe** — archive the retired `price` (and `product` if fully retired);
      do not delete — historical invoices reference it.
- [ ] **Google Play** — deactivate the retired base plan / offer on the
      subscription; keep the subscription if other base plans still use it.

Never delete store SKUs — retire/archive only, so existing subscribers and
historical billing are preserved. Confirm the user has completed (or scheduled)
the archival before the experiment rows are soft-deleted.

## Idempotency

A second run on an already-wound-down experiment produces an **empty plan**:
all rows are already soft-deleted (`deletedAt` set) and packages already at
their target `isAvailable`. Report "already wound down" and make no writes.
Partial state (promoted but not yet soft-deleted, after a mid-sequence stop) is
detected the same way and the run resumes from the first not-yet-applied write.
