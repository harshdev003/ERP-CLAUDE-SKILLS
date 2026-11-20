# Node.js version — environment map

Where the pinned Node version lives, grouped by the five phases of the
"Update nodejs to version X.Y.Z" Linear card. `scan_node_versions.py`
auto-discovers repos by globbing each parent for a `.nvmrc`, so this table is
the *shape*, not a hardcoded list — new services/functions are picked up
automatically.

Root: `the Aura workspace ($AURA_ROOT)` (override with `--root`).

| Card phase | Parent dir | Repos (current) | Version pins |
|---|---|---|---|
| **Services** | `Services/` | BusinessService, CoachService, ContentService, EmailService, ExperimentService, FeedService, IntelligenceService, IterableService, NotificationService, PaymentService, ReportService, SupportService, UserService (13) | `.nvmrc` (full) + `Dockerfile` `FROM node:<ver>-alpine` + `.circleci/config.yml` (`cimg/node`) |
| **Data** | `Data/` | Ingestor | `.nvmrc` (full) + `Dockerfile` + `.circleci/config.yml` |
| **Models** | `AI/` | 4U-engine (`.nvmrc` + `.circleci`), LivekitVoiceAgent (`.nvmrc` only — its Dockerfile is `python:*`, **not** node, and it has no CircleCI node pin) | `.nvmrc` (full) + `.circleci/config.yml` where present |
| **CLI** | `Tools/` | CLI | `.nvmrc` (full) + `.circleci/config.yml` |
| **Functions** | `Functions/` | FirebaseAnalytics, FirebaseConsumer, FirebaseDenormalizer, FirebaseFixer, FirebaseJob, FirebaseMixpanel, FirebaseProcessor, FirebaseProducer, FirebaseSocial (9) | `.nvmrc` (full, repo root) + `.circleci/config.yml` + `functions/package.json` `engines.node` (**MAJOR only**, e.g. `"24"`) |

## Pin file formats

- **`.nvmrc`** — bare full version, no `v` prefix, single line: `24.16.0`. Used
  by `nvm` for local dev; present in every repo on the track. A repo may carry more
  than one (4U-engine has one per `models/*`); the scan reads and rewrites them all.
- **`Dockerfile`** — `FROM node:<full-ver>-alpine` (the deployed runtime for
  containerised services). Only rewritten where the base image is actually
  `node:*`. Repos whose Dockerfile is a different base (e.g. LivekitVoiceAgent →
  `python:3.12-slim`) are left untouched.
- **`functions/package.json` `engines.node`** — the deployed Cloud Functions
  runtime, pinned to the **major only** (`"24"`). Only changes on a **major**
  bump (24 → 26); patch/minor bumps within the same major leave it alone. On a
  major bump, the new major must be listed as **GA** (not Preview) on the Cloud
  Functions runtime page, or Functions stay on their current major:
  https://cloud.google.com/functions/docs/concepts/nodejs-runtime
- **`.circleci/config.yml` `cimg/node:<full-ver>`** — the CI executor image that
  runs lint/test/build (and the image `push`). Present in ~29 backend repos,
  often on **multiple lines** (the scanner rewrites every occurrence). Keep it
  in lockstep with the Dockerfile — otherwise CI validates on the old Node while
  the deployed image runs the new one. **Guard:** `cimg/node:<version>` must
  exist on Docker Hub before you bump it, or every CI build fails at executor
  pull. Check: `https://hub.docker.com/v2/repositories/cimg/node/tags/<version>`.

## Explicitly out of scope

- **Web** (`Web/*`) and **Mobile** (`Mobile/Aura`) — separate Node tracks
  (18.x / 22.x / 24.1.x). They are not under the globbed parents above and must
  not be bumped by this skill.
- **Dependency (npm package) bumps** — the sibling recurring card
  "Update node packages" (via each repo's `update.sh` / `npx ncu -u`). This
  skill only moves the *Node runtime version*, not package versions.

## Deploy mechanism per phase

**Cutting a release does not deploy directly.** `eng:release-version` lands the
version bump on the default branch through a pull request, tags the squash-merge
commit `v<x.y.z>`, and cuts a GitHub release. The **tag push triggers CircleCI**,
whose tag-gated jobs (`test` → `push`) build the Docker image from the new
`Dockerfile` `FROM` and push it (that is the deploy). So verify the **CircleCI**
pipeline for the tag, not just the release output — the skill's `verify` does this:

```bash
# jobs on the tag pipeline should be test:success, push:success
curl -s -H "Circle-Token: $CIRCLE_TOKEN" \
  "https://circleci.com/api/v2/project/gh/AuraHealth/<Repo>/pipeline" | ...
```

A canary is only truly green when the `test` job (running on the bumped
`cimg/node`) **and** the `push` job both succeed on the tag pipeline.

Each repo group has helper scripts at the parent dir (e.g. `Services/pull.sh`,
`Services/release.sh`, `Functions/release.sh`, `Data/release.sh`). They iterate
a (sometimes commented-down) list of repos. Deploy is driven by `release-it`:

Deploy differs by phase — and crucially, **whether a release is even needed
depends on whether the phase has a node runtime that the bump actually changes**:

- **Services / Data** — containerised; `Dockerfile` `FROM` changes → must
  release: merge the runtime PR, then `eng:release-version` (bump lands via its own
  PR → tag → CircleCI builds+deploys the image).
- **Models** — mixed, check per repo:
  - `AI/4U-engine` — a published lerna lib that ships, one `<pkg>@<x.y.z>` tag per
    changed package; the node bump **does** affect its runtime → release it.
  - `AI/LivekitVoiceAgent` — runtime container is `python:3.12-slim`; node is
    only CI-executor + local-dev, so the `.nvmrc`/`.circleci` bump changes no
    node runtime → merge-only, no release.
- **CLI** (`Tools/CLI`) — no container, no deploy; the merged pin bump is the
  whole change → merge-only.
- **Functions** — `engines.node` is major-only (`"24"`), so a **minor/patch**
  node bump leaves the deployed Cloud Functions runtime unchanged; the
  `.nvmrc`/`.circleci` bump is CI-executor + local-dev only → merge-only (the
  CircleCI deploy job is tag-gated, so merging without a release does not
  redeploy the 9 functions). On a **major** bump, `engines.node` changes → then
  a release IS required to move the runtime, and only once Cloud Functions
  lists that major as GA.

**Rule of thumb:** release only where the bump changes something the phase
actually *runs* — a node `Dockerfile FROM`, a shipped lerna lib, or Functions'
`engines.node` on a major. Otherwise the pins are CI/local hygiene → merge-only.

Canary order from the card's **Prepare** section: deploy **Ingestor** and
**Feed** first with the new Node before touching the rest — they validate the
new runtime end-to-end.
