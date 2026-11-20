#!/usr/bin/env bash
# aiweb-clone.sh
#
# Sparse-clones (or refreshes) AuraHealth/AIWeb at /tmp/aiweb so the
# aurie-web-flow-snapshot skill can read live source files in Step 1.
#
# Usage (from mcp__workspace__bash):
#   bash scripts/aiweb-clone.sh
#
# Inputs (env vars, read from the calling shell):
#   AIWEB_REF        — git ref to fetch (default: main)
#   GITHUB_TOKEN     — optional PAT with read access to AuraHealth/AIWeb.
#                       Usually unset: the script falls back to `gh auth token`
#                       (the gh CLI's own credential), which is the standard
#                       GitHub access path per AINative CLAUDE.md "Secrets and
#                       environment". When neither resolves, the script exits
#                       with TIER2_FALLBACK=no_token and the calling skill
#                       drops to Tier 3 (Notion snapshot).
#
# Output (last line of stdout, parsed by the caller):
#   TIER2_OK=cache_hit COMMIT=<sha>          — repo on disk and < 6h old
#   TIER2_OK=pull COMMIT=<sha>               — existing clone refreshed
#   TIER2_OK=clone COMMIT=<sha>              — fresh sparse clone
#   TIER2_FALLBACK=no_token                  — token missing
#   TIER2_FALLBACK=pull_timeout              — fetch took too long
#   TIER2_FALLBACK=clone_timeout             — first clone took too long
#   TIER2_FALLBACK=sparse_checkout_failed    — sparse-checkout step failed
#
# Side effects:
#   - Creates / mutates /tmp/aiweb (sparse working tree, no blobs).
#   - Caller (the skill) is expected to read files via /tmp/aiweb/<path>.

set -euo pipefail

REPO_DIR="/tmp/aiweb"
AIWEB_HOST_PATH="github.com/AuraHealth/AIWeb.git"
GH_TOKEN="${GITHUB_TOKEN:-}"
# gh's own credential is the normal source; GITHUB_TOKEN is no longer set by default.
if [ -z "$GH_TOKEN" ]; then
  GH_TOKEN="$(gh auth token 2>/dev/null || true)"
fi
REF="${AIWEB_REF:-main}"
MAX_AGE_SEC=$((60 * 60 * 6))   # re-pull if older than 6h

if [ -z "$GH_TOKEN" ]; then
  echo "TIER2_FALLBACK=no_token"; exit 0
fi

if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR"
  # The clone embeds the credential in the remote URL, so an existing checkout keeps
  # fetching with whatever token created it. Re-point it at the one resolved above,
  # or a rotated/revoked token wedges every refresh into a Tier 3 fallback.
  git remote set-url origin "https://oauth2:$GH_TOKEN@$AIWEB_HOST_PATH" 2>/dev/null || true
  # mtime of HEAD: BSD/macOS `stat -f %m` first, GNU/Linux `stat -c %Y` fallback.
  # (`stat -c` errors on macOS, so the old single-form call always read 0 -> cache never hit.)
  HEAD_MTIME=$(stat -f %m "$REPO_DIR/.git/HEAD" 2>/dev/null || stat -c %Y "$REPO_DIR/.git/HEAD" 2>/dev/null || echo 0)
  AGE=$(( $(date +%s) - HEAD_MTIME ))
  if [ "$AGE" -lt "$MAX_AGE_SEC" ]; then
    git fetch --depth=1 origin "$REF" 2>/dev/null || true
    git checkout -q "$REF" 2>/dev/null || true
    echo "TIER2_OK=cache_hit COMMIT=$(git rev-parse --short HEAD)"
    exit 0
  fi
  timeout 25 git fetch --depth=1 --filter=blob:none origin "$REF" \
    && git checkout -q "$REF" \
    && echo "TIER2_OK=pull COMMIT=$(git rev-parse --short HEAD)" \
    || { echo "TIER2_FALLBACK=pull_timeout"; exit 0; }
else
  rm -rf "$REPO_DIR"
  timeout 35 git clone \
    --depth=1 --filter=blob:none --sparse --no-tags --single-branch -b "$REF" \
    "https://oauth2:$GH_TOKEN@$AIWEB_HOST_PATH" "$REPO_DIR" \
    || { echo "TIER2_FALLBACK=clone_timeout"; exit 0; }
  cd "$REPO_DIR"
  git sparse-checkout set \
    src/lib/quiz-config.ts \
    src/lib/constants.ts \
    src/services/onboardingAnalytics.ts \
    src/pages/signup \
    src/pages/subscribe \
    src/pages/get-app \
    docs/web-onboarding-v3-spec.md \
    e2e/signup-onboarding.spec.ts \
    || { echo "TIER2_FALLBACK=sparse_checkout_failed"; exit 0; }
  echo "TIER2_OK=clone COMMIT=$(git rev-parse --short HEAD)"
fi
