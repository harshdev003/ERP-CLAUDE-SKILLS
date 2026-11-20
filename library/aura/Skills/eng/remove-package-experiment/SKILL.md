---
name: remove-package-experiment
description: >
  Winner-dependent wind-down of an ended Aurie package experiment. Given the experiment name
  and winning variant, fetches its packageExperiment rows, shows a before/after plan,
  confirms, then executes the wind-down. The critical invariant: on a treatment win the
  variant is PROMOTED into the canonical lineup BEFORE the experiment rows are soft-delete —
  assignments are sticky, so soft-deleting first snaps everyone back to control. Every write
  is gated behind an explicit yes Trigger on "remove package experiment", "wind down package
  experiment", "end package experiment {name}", "the {name} package test is over", "promote
  the winning package variant", "retire losing package experiment variants", "clean up the
  package experiment now that it's done", or "control won the {name} package test — revert
  it". Do NOT use for planning a new catalogue change or pricing experiment
  (`eng:manage-ai-packages`), assigning a user to a bucket (`eng:set-user-experiment`), or
  sweeping ended experiments (`eng:sweep-ended-experiments`).

metadata:
  version: "3.1.1"
knowledge:
  reads:
    - Guide/Index
    - Tools/Aura API
---

# Remove package experiment

## Goal

Wind down an **ended** Aurie package experiment so the catalogue is left in the
correct steady state for the **winning variant** — and crucially, so that **no
user snaps back to control on a treatment win**.

A "package experiment" (resolved server-side in IntelligenceService) is a set of
per-variant rows in the `packageExperiment` table. Each row carries an `action`
— `patch`, `hide`, `replace`, or `add` — that is resolved **server-side** over
the canonical catalogue returned by `POST /ai/packages/config`. While the
experiment is active, a user in a treatment bucket sees the catalogue *with the
variant's overrides applied*; a control user sees the canonical catalogue.

When the experiment ends on ExperimentService, **the catalogue does not revert
on its own**. Per-user experiment assignments are **sticky** — a user assigned
to bucket `b` stays in `b`. So wind-down is **winner-dependent** and must be
done deliberately. This skill is that deliberate wind-down. Do **not** rely on
auto soft-deletion.

## The core hazard (read this before doing anything)

> **On a treatment win, you must PROMOTE the variant change into the canonical
> `package` lineup BEFORE you soft-delete the experiment rows.**

Here's why. The variant overrides only exist *because* the experiment rows are
active (`deletedAt IS NULL`). The moment you soft-delete those rows (set
`deletedAt = NOW()`), server-side resolution stops applying the override —
every user, including the winning treatment bucket, immediately resolves the
**canonical** (control) catalogue. If you haven't first written the winning
change into the canonical `package` row, you have just reverted the winner to
control for the entire population. That is the single most important thing this
skill prevents.

So the ordering on a treatment win is always: **promote first, soft-delete
second.** On a control win there is nothing to promote, so you soft-delete
directly.

## Inputs

Free-form from the user. You need two things:

1. **`experimentName`** — the ExperimentService experiment name (e.g.
   `aiPackagePricingQ3`). Ask if missing.
2. **The winning variant** — either `control` (variant `b` by convention — the
   canonical `isAvailable=true` catalogue, which has no rows) or a treatment
   bucket (`a`, `c`, …). Ask if missing. **Never guess the winner** — it is a
   product/analytics decision, not something to infer from the rows. A `control`
   / `b` win is always Path A (soft-delete only; nothing to promote).

If either is missing, ask before doing anything. Do not invent an experiment
name or a winner.

## Required env

This skill reads and writes the **production** catalogue and experiment table
via the Aura API. It depends on one shell secret in `~/.claude/settings.json` →
`env`:

| Shell env var    | Purpose |
|------------------|---------|
| `AURA_API_TOKEN` | Bearer token for a user with admin/service/system role on the prod catalogue + experiment endpoints. Required for both the reads and every write. |

If unset or rejected (401/403), stop and tell the user to add/refresh it (see
[`/CLAUDE.md`](../../../../CLAUDE.md) → "Secrets and environment"). Never plan
against a remembered catalogue state.

## Dependency note (read before first run)

The authorized list endpoint this skill relies on — **`POST
/ai/packages/experiments/list`** (filters in the JSON body: `experimentId`,
`variant`, `action`, `controlPackageId`, `variantPackageId`) — is restricted to
internal callers (admin / service / system role) and may not be deployed to your
target environment yet. It returns **active rules (`deletedAt IS NULL`) by
default**, with an `includeDeleted` filter to also surface soft-deleted rows; it
is backed by the model's `list({includeDeleted, ...})` method (there is no
`listActive`), and the wind-down soft-delete is the model's `apply(...)` path.
There is **no write** path for `packageExperiment` rows or for promoting/retiring
`package` / `packagePrice` rows. The
IntelligenceService `package` model historically has **no DB write path** (rows
are seeded directly — see `eng:manage-ai-packages`).

So at each step, prefer the Aura API. **If the needed endpoint returns 404 / is
not deployed, do not fail silently and do not fall back to a direct DB write
from this session** — instead, surface the gap explicitly as a dependency, and
emit the exact change as runnable `UPDATE`/`INSERT` SQL for the user to apply
(per this repo's convention of handing shared-DB writes to the user). Flag the
missing endpoint so it can be tracked. Read-only reads through the API are
always fine.

## Workflow

### 1. Fetch the canonical catalogue + the experiment's rows (read-only)

Run the bundled script. It calls the catalogue list endpoints and the
`packageExperiment` list endpoint, filtered to this experiment
(active rows — `deletedAt IS NULL` — by default), and prints a combined
snapshot:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/remove-package-experiment/scripts/fetch_experiment.py" \
  --experiment "<experimentName>"
```

Use `--json` for raw payloads when you need exact field values.

- If `AURA_API_TOKEN` is missing/rejected → stop and ask (see Required env).
- If the `packageExperiment` list endpoint is **not deployed** (404) → stop,
  report the missing-endpoint dependency, and ask the user to supply the
  experiment's rows another way (read-only DB query through the tunnel is
  acceptable) before continuing.
- If the experiment has **no rows** → stop and report; nothing to wind down.

Capture, per variant row: `id`, `experimentId`, `variant` (always a treatment
bucket — `a`, `c`, … — never `b`/control), `action`
(`patch`/`hide`/`replace`/`add`), `controlPackageId` (the canonical package it
targets, for patch/hide/replace) and `variantPackageId` (the hidden variant
package it introduces, for replace/add), the field changes it carries
(`name`, `description`, `displayConfig`, `features`, `priceIds`), and `deletedAt`
(a row is active while `deletedAt IS NULL`).

### 2. Confirm the experiment is actually ended

Read the experiment config:

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:remove-package-experiment (session ${AINATIVE_SESSION_NAME:-adhoc})" -sS -X GET "https://services.aurahealth.io/experiments/<experimentName>" \
  -H "Authorization: Bearer $AURA_API_TOKEN"
```

- If `endedAt` / `deletedAt` are **both null** (still live) → **stop and warn**.
  Winding down a live experiment is almost always a mistake. Require the user to
  reconfirm explicitly that they intend to end it anyway.
- Note `targetApp` and `active` for the summary.

### 3. Classify the outcome path

Cross the **winner** with the **action** of the winning/variant rows to pick
exactly one path. See
[references/wind-down-logic.md](references/wind-down-logic.md) for the full
decision detail and field-merge rules. The three paths:

| Winner | Row action(s) | Path | One-line consequence |
|---|---|---|---|
| **Control** | any | **A — soft-delete** | Variant rows soft-deleted; new-package variants retired. No promotion. |
| **Treatment** | `patch` / `hide` (overlay) | **B — promote overlay, then soft-delete** | Variant's field change written into the canonical `package`; then rows soft-deleted. |
| **Treatment** | `replace` (new package) | **C — promote new package, then soft-delete** | Variant package joins canonical lineup; **the replaced control package + its prices retired; its store SKUs archived**; then rows soft-deleted. |
| **Treatment** | `add` (new package) | **C — promote new package, then soft-delete** | Variant package joins canonical lineup as an **addition**; **no control package retired, no SKUs archived**; then rows soft-deleted. |

State the chosen path and its consequence to the user in one line before
building the plan. If the rows mix overlay and new-package actions, treat each
row by its own action but keep the **promote-before-soft-delete** ordering for
the whole set.

### 4. Build the plan (before any write)

Produce a single markdown plan with an explicit **before → after** for every
row and package touched. Never write anything in this step. The plan must list,
in execution order:

**Path A — Control wins (soft-delete):**
1. For every variant row of this experiment: soft-delete it —
   `UPDATE "packageExperiment" SET "deletedAt" = NOW() WHERE "id" = …`.
2. For `replace`/`add` rows only: the variant **package** + its `packagePrice`
   rows `isAvailable: true → false` (retire the package nobody won with).
3. No canonical `package` change. No store SKU change for the *winning*
   (control) catalogue.

**Path B — Treatment wins, overlay (`patch`/`hide`) — PROMOTE FIRST:**
1. **Promote** the winning row's change into the canonical `package` row:
   - `name` / `description` → **whole-value replace**.
   - `displayConfig` / `features` → **RFC 7386 JSON Merge Patch** (a `null` in
     the patch deletes that key; objects merge recursively). See
     [references/wind-down-logic.md](references/wind-down-logic.md).
   - **Price-set patch** → flip the variant `packagePrice` rows to canonical
     (`isAvailable: true`) and **retire the old** price rows (`isAvailable:
     false`).
   - **`hide`** → retire the hidden package (`isAvailable: false` on the
     `package` and its `packagePrice` rows).
2. **Then** soft-delete the experiment rows:
   `UPDATE "packageExperiment" SET "deletedAt" = NOW() WHERE …`.

**Path C — Treatment wins, new package (`replace`/`add`) — PROMOTE FIRST:**
1. **Promote** the variant package into the canonical lineup: `isAvailable:
   false → true`; for `replace`, it inherits the **`level`** (subscription slot)
   of the control package it replaces; for `add`, keep its defined `level` **and
   bump every surviving canonical package at `≥` that level up by one** — runtime
   resolution shifted them in memory, so promotion must do it for real (see
   [references/wind-down-logic.md](references/wind-down-logic.md)). Verify exactly
   one `isDefault` survives.
2. **`replace` only — retire the old package + its prices**: `isAvailable: true
   → false` on the **control** `package` it replaced and all that package's
   `packagePrice` rows. **Skip this step entirely for `add`** — an `add` win is
   an *addition* to the lineup; there is no control package to take offline, and
   retiring one would remove a package nobody decided to retire.
3. **`replace` only — archive the losing store SKUs** — App Store Connect /
   Stripe / Google Play — for the **retired control** package's SKUs. This is
   **operational** (per the catalogue-change playbook) and largely
   **not automatable** from here — emit it as a **manual checklist** the user
   ticks off, with the exact product IDs to archive. **Skip for `add`** (nothing
   was retired).
4. **Then** soft-delete the experiment rows:
   `UPDATE "packageExperiment" SET "deletedAt" = NOW() WHERE …`.

Show the plan and **wait**. Do not proceed to step 5 until the user approves.

### 5. Execute (confirmation-gated, in order)

Execute the plan **in the order shown**, one write at a time. For **each** write
(promotion, retirement, soft-delete), follow the
`eng:set-user-experiment` pattern:

1. Show the assembled request — the Aura API curl with `$AURA_API_TOKEN`
   referenced (never inlined), **or** the runnable SQL if the write endpoint is
   not deployed (step "Dependency note").
2. Show a resolution table (which row, before value → after value, source).
3. Prompt: "This writes to **production**. Reply `yes` to execute." On `yes`,
   run it and print the result. On anything else, **stop** — and if you stop
   mid-sequence on a treatment win **after** promoting but **before**
   soft-deleting, say so loudly: the catalogue is in a safe (promoted) state but
   the experiment rows are still active (`deletedAt IS NULL`).

**Ordering is load-bearing on Paths B and C:** every promotion/retirement write
must succeed (or be applied as SQL by the user) **before** the soft-delete
write is even shown. Never reorder the soft-delete ahead of promotion.

The store-SKU archival on Path C (`replace` only) is a manual checklist, not an
executed write — present it and ask the user to confirm they've done it (or
will), then proceed to the soft-delete. On an `add` win there is no retirement
and no archival; go straight to the soft-delete.

### 6. Verify and summarize

Re-run the fetch script and confirm the steady state:

- **Path A:** variant rows soft-deleted (`deletedAt` set); retired variant
  packages/prices `isAvailable=false`; canonical catalogue unchanged.
- **Path B:** canonical `package` now carries the promoted change; variant rows
  soft-deleted (`deletedAt` set); old price rows retired (price-set patch) /
  package hidden (`hide`).
- **Path C (`replace`):** variant package `isAvailable=true` in the right
  `level` slot; exactly one `isDefault`; the **replaced control** package +
  prices `isAvailable=false`; variant rows soft-deleted (`deletedAt` set); the
  control SKUs archived (per the checklist).
- **Path C (`add`):** variant package `isAvailable=true`; exactly one
  `isDefault`; canonical packages at `≥` the variant's `level` bumped up by one
  (levels stay unique, lineup order matches what the bucket saw); **no control
  package retired** (the prior lineup is intact apart from the addition); variant
  rows soft-deleted (`deletedAt` set); no SKUs archived.

Print a one-line summary plus the raw `<METHOD> <url> → <status>` (or
`SQL applied: <table> <n> rows`) lines so the caller sees exactly what changed.
Explicitly state: "No user reverts to control" on Paths B/C (the promotion
guarantees it).

## Output

The before/after plan from step 4, the confirmation-gated writes from step 5,
and the verification checklist + one-line summary from step 6.

## Edge cases

- **`AURA_API_TOKEN` missing/rejected** → stop; ask the user to set/refresh it.
- **`packageExperiment` list or write endpoint not deployed (404)** → surface
  the missing-endpoint dependency; read rows via a tunnel query, emit writes as
  SQL for the user. Never fall back to a silent direct DB write from this session.
- **Experiment doesn't exist** → the fetch script hard-fails on a `404` from
  `GET /experiments/<name>` (the experiment-config lookup is **not** soft-404'd;
  only the not-yet-deployed `packageExperiment` list endpoint is). A mistyped or
  nonexistent name stops the run with a clear "experiment does not exist"
  message — never confuse it with the empty-rows "nothing to wind down" path.
- **Winner not given** → ask. Never infer the winner from the data.
- **Experiment still live** (`endedAt`/`deletedAt` null) → warn and require
  explicit reconfirmation before any wind-down.
- **Rows mix overlay + new-package actions** → apply each row by its own action;
  keep promote-before-soft-delete for the whole set.
- **User stops mid-sequence on a treatment win after promotion** → safe state,
  but say so: catalogue promoted, experiment rows still active. Re-running
  resumes cleanly.
- **Re-run on an already-wound-down experiment** → idempotent. Rows already
  soft-deleted (`deletedAt` set) and packages already in their target
  `isAvailable` state → the plan is empty; report "already wound down" and make
  no writes.
- **Path C `add` win** → promote the variant package only; do **not** retire any
  control package and do **not** archive any SKUs (an `add` is an addition, not
  a swap). Only `replace` retires + archives.
- **Path C `replace` with no store SKUs to archive** (e.g. a free/entitlement-
  only control package) → still retire the control package + prices; skip the
  archival checklist and note it.

## Security

- Never inline the bearer token in rendered curl text — reference
  `$AURA_API_TOKEN`. Mask it in resolution tables (first-4 + `…` + last-3), same
  rule as `eng:call-aura-api` / `eng:set-user-experiment`.
- The catalogue and experiment rows are config, not user PII — but if the
  caller asks to share a run externally, still follow the repo PII rule for any
  incidental user data (UID prefix + email domain only).

## References

- [references/wind-down-logic.md](references/wind-down-logic.md) — the full
  outcome decision matrix, the RFC 7386 merge-patch rules for
  `displayConfig`/`features`, price-set promotion mechanics, the `level`
  inheritance rule for `replace`, and the store-SKU archival checklist.
- Sibling `eng:manage-ai-packages` — read-only catalogue planner; shares the
  catalogue shape and the catalogue-change playbook. Re-read it for `package` /
  `packagePrice` / `product` field meanings and the `fetch_catalogue.py`
  pattern this skill's fetch script mirrors.
- Sibling `eng:set-user-experiment` — the per-write `yes`-confirmation pattern
  this skill follows for every production write.
- Sibling `eng:call-aura-api` — the generic curl + token-resolution wrapper;
  use it for any ad-hoc read-back.
