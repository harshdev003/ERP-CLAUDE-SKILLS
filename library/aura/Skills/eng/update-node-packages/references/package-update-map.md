# Node packages — update map

Where npm dependencies get bumped for the recurring "Update node packages" card,
and how each tier deploys. Root: the Aura workspace — set `$AURA_ROOT` or keep repos in a common location (auto-discovered); never hardcoded. The bump itself is
`npx ncu -u` (latest, incl. majors) + tier pins + `npm install` + `npm audit fix`,
committed as `"Update packages"` — see [`scripts/bump_packages.sh`](../scripts/bump_packages.sh).

## Tiers, pins, and deploy

| Tier | Repos | Pins after `ncu -u` | Manifest | Deploy (packages change the running code → must redeploy) |
|---|---|---|---|---|
| **services** | `Services/*` (excl. CoachService) | `eslint@^9`; `nanoid@^3`, `uuid@^11` (all but PaymentService); `overrides.jose=5.10.0` + `metascraper*@5.50.6` (UserService) | `package.json` (+lock) | `eng:release-version` → `v<x.y.z>` tag → CircleCI build/deploy |
| **data** | `Data/Ingestor` | `eslint@^9` | `package.json` (+lock) | `eng:release-version` → `v<x.y.z>` tag → CircleCI build/deploy |
| **functions** | `Functions/*` (9) | `eslint@^9`, `@types/node` at the `engines.node` major | `functions/package.json` (+lock) | `eng:release-version` — detects the Functions shape and bumps `functions/package.json`; Firebase deploys on the tag |
| **cli** | `Tools/CLI` | — | `package.json` (+lock) | **merge-only** (local tool, no deploy) |
| **model** | `AI/4U-engine/models/activation-model`, `.../activation-category-model` | `stream-json@1.9.1` | per-model `package.json` (+lock) | `eng:release-version` — one `<pkg>@<x.y.z>` tag per changed model, then it re-triggers the default-branch pipeline that builds the images |

**Why not `npm run release` / `lerna version`.** Both push the version commit straight to the default branch, which every repo's ruleset rejects with `GH013: Changes must be made through a pull request` — and release-it's rollback deletes the tag it already pushed, stranding a CI pipeline (the phantom "test job has failed" alert). `eng:release-version` lands the bump through a PR instead, which is the only route onto the branch.

## Why the pins exist (do not drop them)

- **jose@5.10.0** — jose v6 is ESM-only (no CommonJS build); it crashes Jest's CJS
  test runtime with `Unexpected token 'export'`. UserService pulls it transitively via
  `jwks-rsa`, so it needs an npm `overrides` entry (a direct pin wouldn't stop jwks-rsa
  nesting its own jose@6).
- **uuid@^11** (services except PaymentService + 4U-engine models) — uuid ≥ 12 is
  ESM-only, crashes Jest CJS.
- **PaymentService has no pins.** v2.231.31 shipped jose 6, uuid 14, nanoid 6 and
  app-store-server-api 1.0 to prod. app-store-server-api 1.0 is import-only, so PaymentService loads it through src/services/appStoreServer.js; Jest compiles uuid, nanoid and jose with babel-jest.
- **metascraper\*@5.50.6** (UserService) — metascraper ≥ 5.51 pulls an ESM-only
  `condense-whitespace`, which crashes Jest's CommonJS runtime the same way jose v6
  does. Hold the whole `metascraper*` set at 5.50.6 until its `condense-whitespace`
  dep is CJS again. (Same failure signature — `Unexpected token 'export'`.)
- **stream-json@1.9.1** (4U-engine node models) — stream-json >= 3.0.0 is
  `type: module`, so Jest's CommonJS runtime fails to parse it (`Cannot use import
  statement outside a module`) — the same signature as jose v6. 2.x looks like a
  middle ground and is not: it is still `type: commonjs`, but its `exports` map
  does not resolve the extensionless subpath the models require
  (`stream-json/streamers/StreamObject` → `Cannot find module`). So the ceiling is
  set by the resolver change at 2.0.0, not by the ESM switch at 3.0.0, and 1.9.1 is
  the last release these models can use. Only these two packages depend on it.
- **firebase-admin is not pinned** (since 2026-11-20). `firebase-functions@7.x` peers
  `firebase-admin ^14`, and all Functions repos run firebase-admin 14.5.1, so `ncu -u` takes it
  to the latest.
- **`@types/node` at the `engines.node` major (Functions)** — Functions move to a new Node major
  only once it is GA on Cloud Functions (`eng:update-nodejs`), so their types stay on the runtime
  they deploy on; `ncu -u` would take them to the newest Node's.
- **eslint@^9 / nanoid@^3 / uuid@^11** (nanoid and uuid: all services but PaymentService) — the versions the repos' configs are
  written against; ncu would otherwise drift them.

Keep this table in sync with `$AURA_ROOT/{Services,Data,Functions}/update.sh`,
which are the canonical source of the bump logic.

## Held back (exceptions)

A package held below its latest major, or below a broken release, for a reason someone has to
come back to. Each row has an owner and a revisit date; on that date the owner checks whether it
can lift, and the row goes when it does.

| Package | Held at | Where | Why | Owner | Revisit | Lift card |
|---|---|---|---|---|---|---|
| `websocket-driver` | `0.7.5`, as an `overrides` entry | every repo with `firebase` or `firebase-admin` as a dependency, any tier | 0.8.0 (2026-11-16) breaks RTDB reads through `faye-websocket`: UserWeb's real-Stripe e2e failed because the paywall never loaded the user | Backend | 2026-12-03: is there a fixed 0.8.x? | — |
| `eslint` | `^9` | Functions repos | `eslint-plugin-import` 2.32.0 peers eslint ≤ 9, and eslint 10 no longer installs `globals`, which the flat config imports | Firebase | 2026-12-03 | ENGBACK-3301 |

## npm overrides (each one has a removal trigger)

An `overrides` entry forces a version on a transitive dependency. `ncu -u` never touches it,
so it outlives its reason unless someone checks. Every override in an in-scope repo is listed
here with the check that says it can go; the standing step in SKILL.md (3a) runs those checks
each cycle.

| Repo | Override | Why | Remove when | Check |
|---|---|---|---|---|
| UserService | `jose` → `5.10.0` | `jwks-rsa` pulls jose v6, ESM-only, which crashes Jest's CJS runtime (pin section above) | jose ships a CommonJS build again, or UserService's tests run as ESM | `npm view jose@latest type exports --json` (no `"type": "module"`, a `require` export) |
| any repo on `firebase` / `firebase-admin` | `websocket-driver` → `0.7.5` | 0.8.0 breaks RTDB reads through `faye-websocket` (held-back table above) | a 0.8.x release reads RTDB again (Backend checks on 2026-12-03) | `npm view websocket-driver versions --json` for a release after 0.8.0, then one RTDB read with it |
| Tools/CLI | `@grpc/grpc-js` → `1.14.7` | Dependabot high/low alerts on `@grpc/grpc-js` < 1.13.6; `firebase` → `@firebase/firestore` pins `~1.9.0` (CLI#90, ENGBACK-2440) | the latest `@firebase/firestore` depends on a fixed `@grpc/grpc-js` (≥ 1.13.6) | `npm view @firebase/firestore@latest dependencies.@grpc/grpc-js` (4.20.0 still says `~1.9.0`, 2026-11-19) |

Removing an override that `scripts/bump_packages.sh` sets (UserService's `jose`, `websocket-driver`) also means removing
it from that script and from the pin notes above, or the next bump writes it back.

An override that is not in this table is a finding: find its reason (`git log -S'"overrides"'
package.json`), add its row, or remove it if nothing needs it.

## Behaviour changes CI does not catch

Some majors pass the test suite and still change what runs in prod. After each
release, read the service's prod logs for new ERROR lines, not just CI.

- **dotenv 18** — every load needs `{quiet: true}`
  (`require('dotenv').config({quiet: true})`, and the `dotenv.config()` form too).
  dotenv 17+ prints a startup banner, and 18 writes it to stderr, which GKE files
  as ERROR. That is two ERROR lines per pod start and per job run, with no
  functional effect. Why: CI stays green, so the only signal is ERROR lines in
  prod logs after release — FeedService logged 20 before anyone noticed.

## Scope is the `update.sh` active array — not "all repos"

The authoritative in-scope list each cycle is the **uncommented `services=(...)` /
`functions=(...)` array** in `$AURA_ROOT/{Services,Data,Functions}/update.sh`
(the maintained working set). Some repos are intentionally held back — do **not**
blindly bump everything. Read the active array first and scope to exactly it.

## Standalone / excluded

- **PaymentService** — takes the latest packages with no pins since v2.231.31 (see the pin
  section). It is still omitted from the active `Services/update.sh` array.
- **CoachService** — never touched here; deferred to [ENGBACK-1218](https://linear.app/aura-health/issue/ENGBACK-1218)
  (ffmpeg/node, PROD-2270). Also absent from the active array.
- **LivekitVoiceAgent** — python runtime; not part of the node-package cadence.
- **4U-engine python/tensorflow models** — only the two **node** models above bump.
- **Web / Mobile** — separate track, not this card.

## Deploy nuance vs. the version bump

For a **version** bump (`eng:update-nodejs`), Functions/CLI were merge-only because
the runtime didn't change. For a **package** bump the dependency code *does* change,
so Services, Data, Functions, and 4U-engine models all **redeploy**. Only CLI stays
merge-only (nothing runs it in prod). Functions' CircleCI deploy is tag-gated, so a
release (not just a merge) is required to ship the new deps.
