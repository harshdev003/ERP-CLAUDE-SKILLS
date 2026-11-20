#!/bin/bash
# Deterministic per-repo npm dependency bump for the "Update node packages" flow.
# Runs `npx ncu -u`, re-applies the tier's known-good pins/overrides, installs,
# runs `npm audit fix`, then stages ONLY the manifest files and commits
# "Update packages". The CALLER creates the branch beforehand and handles
# push / PR / merge / release afterward.
#
# Usage: bump_packages.sh <repo-dir> <tier> [<repo-name>]
#   tier ∈ services | data | functions | cli | model
#   repo-name picks the repo-specific pins. Default: the `origin` remote's repo name, then the
#   directory name. A worktree's directory (wt-2170-PaymentService) is not the repo's name, and
#   matching on it once skipped every PaymentService and UserService pin silently (ENGBACK-2170).
#   (functions runs npm inside <repo-dir>/functions; model = a single 4U-engine
#    node model dir, e.g. .../4U-engine/models/activation-model)
#
# Pins (from $AURA_ROOT/{Services,Data,Functions}/update.sh — keep in sync):
#   services  : eslint@^9; nanoid@^3, uuid@^11 (all but PaymentService); jose@5.10.0
#               UserService override — jose v6 is ESM-only, crashes Jest CJS. PaymentService
#               takes the latest uuid, nanoid, jose and app-store-server-api (since v2.231.31)
#   data      : eslint@^9
#   functions : eslint@^9 (eslint-plugin-import 2.32 peers eslint <= 9);
#               @types/node held at the engines.node major (Functions move to a new Node
#               major only once it is GA on Cloud Functions)
#   any tier  : overrides.websocket-driver=0.7.5 where firebase or firebase-admin is a
#               dependency (0.8.0 breaks RTDB reads through faye-websocket)
#   cli/model : eslint@^9 (eslint 10 breaks eslint-plugin-import@2 + flat-config
#               globals import); uuid@^11 where present (uuid >= 12 is ESM-only);
#               stream-json@1.9.1 where present (>= 3 is ESM-only, >= 2's exports
#               map breaks the extensionless subpath require)
set -uo pipefail
DIR="$1"; TIER="$2"; NAME="${3:-}"
abort(){ echo "BUMP-ABORT: $*"; exit 1; }
[ -d "$DIR" ] || abort "no dir $DIR"
if [ -z "$NAME" ]; then
  ORIGIN=$(git -C "$DIR" remote get-url origin 2>/dev/null)
  NAME=$(basename -s .git "${ORIGIN:-$DIR}")
fi
PINS=()   # every pin applied, printed before the install so a skipped pin is visible
pin(){ PINS+=("$*"); }

WORK="$DIR"
[ "$TIER" = "functions" ] && WORK="$DIR/functions"
cd "$WORK" || abort "no work dir $WORK"
[ -f package.json ] || abort "no package.json in $WORK"

echo ">>> [$NAME/$TIER] ncu -u"
npx --yes npm-check-updates -u || abort "ncu failed"

echo ">>> [$NAME/$TIER] apply pins"
case "$TIER" in
  services)
    npm i -D eslint@^9.0.0 || abort "services eslint pin"; pin eslint@^9
    case "$NAME" in
      # app-store-server-api 1.0 is import-only, so PaymentService loads it through
      # src/services/appStoreServer.js; Jest compiles uuid, nanoid and jose with babel-jest.
      PaymentService) ;;
      *) npm i -S nanoid@^3.0.0 uuid@^11.1.0 || abort "services pins"; pin nanoid@^3 uuid@^11 ;;
    esac
    case "$NAME" in
      UserService)
        npm pkg set overrides.jose=5.10.0 || abort "jose override"
        pin overrides.jose=5.10.0
        # metascraper >= 5.51 pulls an ESM-only condense-whitespace, which crashes
        # Jest's CommonJS runtime ("Unexpected token 'export'"). Hold the whole
        # metascraper* set at the last-good 5.50.6. Revisit when metascraper's
        # condense-whitespace dep is CJS again.
        MS=$(node -e "const d=require('./package.json').dependencies||{};console.log(Object.keys(d).filter(k=>k==='metascraper'||k.startsWith('metascraper-')).map(k=>k+'@5.50.6').join(' '))" 2>/dev/null)
        [ -n "$MS" ] && { npm i -S $MS || abort "metascraper pin"; pin $MS; } ;;
    esac ;;
  data)      npm i -D eslint@^9.0.0 || abort "data pin"; pin eslint@^9 ;;
  functions)
    npm i -D eslint@^9.0.0 || abort "functions eslint pin"; pin eslint@^9
    # ncu takes @types/node to the newest Node's types; hold it at the runtime Functions deploy on.
    MAJOR=$(node -p "((require('./package.json').engines||{}).node||'').match(/\\d+/)?.[0]||''" 2>/dev/null)
    HAS=$(node -p "(require('./package.json').devDependencies||{})['@types/node']||''" 2>/dev/null)
    if [ -n "$MAJOR" ] && [ -n "$HAS" ]; then
      npm i -D "@types/node@^$MAJOR" || abort "functions @types/node pin"; pin "@types/node@^$MAJOR"
    fi ;;
  cli|model)
    # eslint 10 drops support in eslint-plugin-import@2.x (ERESOLVE) and breaks
    # the flat-config `globals` import. Keep eslint on ^9 like the other tiers.
    if npm pkg get devDependencies.eslint 2>/dev/null | grep -q '[0-9]'; then
      npm i -D eslint@^9.0.0 || abort "cli/model eslint pin"; pin eslint@^9
    elif npm pkg get dependencies.eslint 2>/dev/null | grep -q '[0-9]'; then
      npm i -S eslint@^9.0.0 || abort "cli/model eslint pin"; pin eslint@^9
    fi
    # uuid >= 12 is ESM-only and crashes Jest's CJS runtime — same as the
    # services tier's uuid@^11 pin. 4U-engine's node models use uuid.
    if npm pkg get dependencies.uuid 2>/dev/null | grep -q '[0-9]'; then
      npm i -S uuid@^11.1.0 || abort "cli/model uuid pin"; pin uuid@^11
    fi
    # stream-json >= 3 is ESM-only and crashes Jest's CJS runtime. 2.x is still
    # CommonJS but adds an `exports` map that does not resolve the extensionless
    # subpath the models require ('stream-json/streamers/StreamObject'), so 1.9.1
    # is the last usable release rather than the last CJS one. Exact, not a range:
    # ^1 would be safe today but the pin records a tested version.
    if npm pkg get dependencies.stream-json 2>/dev/null | grep -q '[0-9]'; then
      npm i -S stream-json@1.9.1 || abort "cli/model stream-json pin"; pin stream-json@1.9.1
    fi ;;
  *) abort "unknown tier: $TIER" ;;
esac
# websocket-driver 0.8.0 breaks RTDB reads through faye-websocket, which firebase and
# firebase-admin both use. Exact, since 0.8.x is what's broken.
if node -e "const p=require('./package.json');const d={...p.dependencies,...p.devDependencies};process.exit(d.firebase||d['firebase-admin']?0:1)" 2>/dev/null; then
  npm pkg set overrides.websocket-driver=0.7.5 || abort "websocket-driver override"
  pin overrides.websocket-driver=0.7.5
fi
echo "PINS [$NAME/$TIER]: ${PINS[*]:-none}"

echo ">>> [$NAME/$TIER] npm install + audit fix"
npm install || abort "npm install failed"
npm audit fix || true   # audit fix exits non-zero on unfixable advisories — not fatal

# Bumping eslint/prettier reformats existing source, and the repos' husky
# pre-commit hook runs `eslint . --cache` and blocks the commit on ANY error.
# Auto-fix so formatting drift from the bump is reconciled before committing.
echo ">>> [$NAME/$TIER] eslint --fix (reconcile formatting drift)"
if npm run 2>/dev/null | grep -qE '^  lint:fix'; then npm run lint:fix || true; else npx eslint . --fix || true; fi

# Stage manifests + tracked code-file reformats. NEVER `git add -A` (repos may
# carry large untracked/bloated local data — the Tools/CLI 562MB lesson). The
# code-extension whitelist keeps data files (.json/.jsonl/.csv under data/) out.
git add package.json package-lock.json 2>/dev/null
# Stage reformatted code files by EXPLICIT path (a `git add -u -- '*.js'` pathspec
# does NOT match nested paths like src/index.js — it silently stages nothing, so
# the fix never lands and CI fails on a fresh, cache-less checkout). Filter to
# code extensions so data files (.json/.jsonl/.csv) are never swept.
CODE=$(git diff --name-only | grep -E '\.(js|jsx|ts|tsx|mjs|cjs)$' || true)
# Abort if staging the eslint reformats fails — otherwise the commit proceeds with
# manifests only and the unstaged fixes fail lint on a clean CI checkout.
[ -n "$CODE" ] && { git add $CODE || abort "failed to stage reformatted files: $CODE"; }
STAGED=$(git diff --cached --name-only)
[ -z "$STAGED" ] && { echo "VERDICT: NO-CHANGE $NAME (deps already current)"; exit 0; }
BAD=$(echo "$STAGED" | grep -vE '(^|/)(package\.json|package-lock\.json)$|\.(js|jsx|ts|tsx|mjs|cjs)$' || true)
[ -n "$BAD" ] && abort "unexpected staged files: $BAD"
echo "--- staged ($NAME) ---"; echo "$STAGED"
git commit -q -m "Update packages" || abort "commit failed (unfixable lint error? inspect the repo)"
echo "VERDICT: BUMPED $NAME"
