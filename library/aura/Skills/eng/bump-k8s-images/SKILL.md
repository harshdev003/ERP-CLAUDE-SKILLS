---
name: bump-k8s-images
description: >
  Bump the container image tags in Kubernetes manifests to the latest semver
  version published in Google Artifact Registry. For each image referenced under
  the k8s manifests tree (default `k8s/apps` in the AuraInfra repo), it resolves
  the highest `vX.Y.Z` tag for that package from Artifact Registry
  (`us-central1-docker.pkg.dev/aura-build/aura-repository`) and rewrites the tag in
  place — Deployments, Jobs, and CronJobs — touching only the image tag and
  preserving all formatting. Loaders and trainers resolve against their own
  packages. Trigger when the user says "bump the k8s images", "update the
  deployment.yaml versions to latest", "update all the image tags", "bump the
  manifests to the latest version", "update content-service (and the rest) to the
  newest build", "upgrade the k8s deployments to latest", or asks to refresh
  Artifact Registry image versions across the infra manifests. Supports scoping to
  deployments only vs jobs too, a dry-run preview, and a per-package allowlist.
metadata:
  version: "3.3.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/GCP
    - Tools/Kubernetes
---

# Bump k8s images to latest

Update the container image tag on Kubernetes manifests to the newest semver
version available in Google Artifact Registry, one package at a time.

Each Deployment/Job/CronJob under the manifest tree pins an image like
`us-central1-docker.pkg.dev/aura-build/aura-repository/<package>:vX.Y.Z`. Over time
these drift behind the images that CI has actually built and pushed. This skill
reconciles them: for every referenced package it finds the highest `vX.Y.Z` tag in
the registry and rewrites the manifest tag to match — nothing else on the line
changes.

## Prerequisites

- `gcloud` on PATH. The script runs it through the bundled `scripts/gcloud_sa.py`, as the
  `GCP_KEY` service account (your own login only when `GCP_KEY` is unset), which needs
  **Artifact Registry Reader** on `aura-build`. Verify with:
  ```bash
  python3 ${CLAUDE_PLUGIN_ROOT}/skills/bump-k8s-images/scripts/gcloud_sa.py artifacts packages list --repository=aura-repository \
    --location=us-central1 --project=aura-build --limit=1
  ```
  If this fails, the error names the credential it ran as: grant that account access, or (no
  `GCP_KEY`) run `gcloud auth login`.
- The infra repo checked out locally (default target: **AuraInfra**, manifests under
  `k8s/apps`).

## Workflow

### Step 1: Confirm target and scope

Establish three things (ask only if not already clear from the request):

- **Repo root** — where the manifests live. Default to the current working
  directory if it contains `k8s/apps`; otherwise ask for the AuraInfra checkout path.
- **Scope** — one of:
  - `deployments` — only `deployment*.yaml` (the long-running Deployments).
  - `jobs` — only Jobs/CronJobs (everything that isn't `deployment*.yaml`).
  - `all` — both (default).
  Jobs and CronJobs frequently pin **older** tags than their parent Deployment, so
  `all` produces the largest, most reconciling diff. If the user only asked to
  "update the deployments", use `deployments`.
- **Registry** — defaults to `us-central1-docker.pkg.dev/aura-build/aura-repository`.
  Only override if the user names a different registry.

### Step 2: Dry-run first (always)

Never write on the first pass. Run the engine in preview mode and show the user the
full change list before touching any file. Pass the **scope you settled on in Step 1**
(`<scope>` below is `deployments`, `jobs`, or `all` — do not default to `all` if the
user only asked for deployments):

```bash
python3 scripts/bump_k8s_images.py \
  --repo-root <path-to-infra-repo> \
  --scope <scope> \
  --dry-run
```

The output lists every `package  old -> new  file` it would change, plus any
package it could **not** resolve (marked `!`, left untouched). Read this to the
user, and call out large jumps — a CronJob leaping many minor versions after
sitting stale is worth a human sanity check before rollout, because "latest built"
can be ahead of what is currently running in production.

### Step 3: Apply

Once the user is happy with the preview, apply for real — same `<scope>` as the
dry-run, just drop `--dry-run`:

```bash
python3 scripts/bump_k8s_images.py \
  --repo-root <path-to-infra-repo> \
  --scope <scope>
```

Only image-tag substrings change; YAML structure, comments, and spacing (including
irregular indentation like `image:  ` with two spaces) are preserved.

### Step 4: Verify the diff is tag-only

Confirm the change set contains nothing but image lines before handing off:

```bash
git -C <path-to-infra-repo> diff -U0 \
  | grep '^[+-]' | grep -vE '^(\+\+\+|---)' | grep -v 'image:' \
  || echo "(clean — all changes are image lines)"
```

Report the file count (`git -C <repo> diff --name-only | wc -l`) and a short summary
of the notable version jumps.

### Step 5: Hand off (do not commit unless asked)

Stop here unless the user asked you to commit/open a PR. If they did, branch off the
default branch first (never commit the bump directly to `master`/`main`), then use
the standard commit/PR flow. A good PR body notes: (a) how many files changed,
(b) that edits are tag-only, and (c) the caveat that "latest" is the newest *built*
image and may be ahead of what's live in prod.

## Options reference

| Flag | Purpose | Default |
|---|---|---|
| `--repo-root` | Infra repo root | `.` (cwd) |
| `--k8s-path` | Manifest tree, relative to repo root | `k8s/apps` |
| `--registry` | Artifact Registry image prefix | `us-central1-docker.pkg.dev/aura-build/aura-repository` |
| `--scope` | `deployments` \| `jobs` \| `all` | `all` |
| `--only` | Comma-separated package allowlist (e.g. `content-service,coach-service`) | (all packages) |
| `--dry-run` | Preview without writing | off |
| `--workers` | Parallel `gcloud` queries | `8` |

## Notes

- **Idempotent.** Re-running when everything is current prints "Everything already
  at the latest tag" and writes nothing.
- **Per-package resolution.** `content-service` and `content-service`'s CronJobs
  resolve to the same tag; loaders/trainers (`activation-model-loader`,
  `neural-model-trainer`, …) are distinct packages and resolve to their own latest
  — the skill never conflates a loader with the service it supports.
- **"Latest" = highest semver tag, not most-recently-pushed.** A tag like
  `v2.293.3` beats `v2.99.9`. Non-semver tags (branch names, `latest`, shas) are
  ignored.
- Scoping to a single service is a two-flag combo: `--scope all --only content-service`.
