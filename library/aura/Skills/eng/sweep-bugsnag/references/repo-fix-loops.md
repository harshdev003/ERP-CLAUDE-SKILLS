# Per-repo fix loops — routing and stack-specific mechanics

One Bugsnag project maps to one repo, and the repos do not share a stack, a default
branch, a lint command, or an error-reporting idiom. Resolve the target before the
first fix, then follow the loop for that stack.

## Routing: Bugsnag project → repo → stack

`eng:investigate-bugsnag-errors` reports the suspect repo and app family for an error
(its Mode 2.5 hands the same repo to `eng:code-investigator`) — take the routing from
there rather than re-deriving it. The table below is the same map, for orientation:

| Bugsnag project | Repo | Stack | Fix loop |
|---|---|---|---|
| `aura-ios`, `aura-android` | `AuraHealth/Aura` | React Native | [`aura-rn-fix-loop.md`](aura-rn-fix-loop.md) |
| `ai-aurie-ios` | `AuraHealth/AuraConsumerApp` | Swift / iOS native | [Swift](#swift--auraconsumerapp) below |
| `ai-aurie-macos` | `AuraHealth/ConsumerAIDesktop` | Electron | [Electron](#electron) below |
| `aiweb` | `AuraHealth/AIWeb` | Next.js / React | [Web](#web--reactnextjs) below |
| `aura-userweb` | `AuraHealth/UserWeb` | Next.js / React | [Web](#web--reactnextjs) below |
| `aura-coachweb` | `AuraHealth/CoachWeb` | Next.js / React | [Web](#web--reactnextjs) below |
| `aura-businessweb` | `AuraHealth/BusinessWeb` | React | [Web](#web--reactnextjs) below |
| `aura-crmweb` | `AuraHealth/CRMWeb` | React | [Web](#web--reactnextjs) below |
| `aura-investweb` | `AuraHealth/InvestWeb` | React | [Web](#web--reactnextjs) below |
| `aura-front-plugin` | `AuraHealth/AuraFrontPlugin` | React | [Web](#web--reactnextjs) below |
| `aura-aimacos` | *(unmapped)* | macOS native | no verified repo — stop and ask |

`aura-aimacos` is deliberately unmapped: two Swift candidates, no committed api_key, and
no events ever received. Do not guess a repo for it — a wrong repo returns a confident
"no matching code found" rather than an error.

**A routing row is a claim about a repo, not about a slug.** Slug and repo names do not
track each other (`aura-ios` is the Aura RN app, not an iOS-native one). When a row looks
wrong, verify it against the project's own `api_key` from
`GET /organizations/<org-id>/projects` and grep the candidate repo for that literal —
per-row evidence lives in `eng:investigate-bugsnag-errors`'s
`references/app-family-routing.md`.

## Universal loop skeleton

Every stack follows the same seven beats; only the commands in beats 3–4 differ.

1. **Find the checkout.** Aura repos live under `~/Development/Aura/<area>/<Repo>`; confirm
   with `git -C <dir> remote get-url origin`. Clone if absent.
2. **Worktree off the fresh default branch.** The default branch is per-repo — `master` on
   the older repos, `main` on the newer ones — so read it rather than assuming:

   ```bash
   cd "$MAIN"
   git fetch origin
   BASE=$(git symbolic-ref --short refs/remotes/origin/HEAD | sed 's|^origin/||')
   git worktree add -b jcurlier/<card>-<slug> ".claude/worktrees/<card>" "origin/$BASE"
   ```

   Worktrees live under `<repo>/.claude/worktrees/<card>` in every Aura repo (gitignored,
   never a sibling of the repo). One card → one worktree → one branch → one PR.
3. **Read the target repo's own conventions.** Several repos ship `.claude/rules/` —
   `error-handling.md`, `security.md`, `pr-process.md`, `comments.md`. Where they exist they
   are authoritative for that repo and outrank anything in this skill. Where they do not,
   fall back to the stack loop below.
4. **Apply the fix, lint, test.** Per stack, below.
5. **Commit** with the message in a temp file + `git commit -F <file>` — never a heredoc
   inside `$()`, never `-m` with embedded newlines.
6. **Draft PR** (`gh pr create --draft`) with the repo's PR template and the Linear link.
7. **Review gate**, then post-merge cleanup — SKILL.md Steps 5 and 6.

## Fix shapes that hold on every stack

The mechanism differs per stack, the judgement does not:

- **Expected flows are not errors.** Network-offline, permission-denied, validation
  failures, and expected new-user / SSO branches should not reach Bugsnag. Downgrade them
  to analytics or a log line.
- **Raw error objects serialize to nothing.** Attach the message and stack as fields, not
  the `Error` itself.
- **Never put PII or tokens in metadata.** The logged-in user id already rides on the event
  via the global user context; keep an id only when it identifies a *different* entity.
- **Interpolated titles fragment groups.** The title drives grouping, so keep it a constant
  string and move the variable into metadata.

## Swift — AuraConsumerApp

Reporting idiom: `reportService.report(_ error: Error)` (`Services/Report/BugsnagReportService.swift`).
`.claude/rules/error-handling.md` is authoritative and already states the expected-flow
rule: log with `logger.error()` first, and do not report expected user-facing errors.

```bash
swiftlint lint --strict        # must be zero violations before committing
make proj                      # regenerate the Xcode project after adding/removing files
```

- The project is generated by xcodegen from `project.yml` — a new file needs `make proj`,
  not a manual `.xcodeproj` edit.
- `.claude/rules/pr-process.md` owns this repo's PR gate, including its BugBot frequency
  rule; follow it rather than SKILL.md Step 5 where the two differ.
- Watch for trailing whitespace and `print()` statements — both fail the repo's lint gate.

## Web — React/Next.js

Covers `AIWeb`, `UserWeb`, `CoachWeb`, `BusinessWeb`, `CRMWeb`, `InvestWeb`,
`AuraFrontPlugin`.

Reporting idiom in `AIWeb`: `notifyAPIError(error, data)` / `notifyHandledError(error, data)`
from `src/services/ErrorMonitoring.tsx`, which wrap `BugsnagClient.notify` and attach a
`CUSTOM` metadata block. The sibling web repos wrap Bugsnag similarly — read the repo's own
`src/services/` before adding a call site, and reuse its helper rather than calling
`Bugsnag.notify` directly.

Read the available commands from the repo instead of assuming they exist:

```bash
python3 -c "import json;print(json.load(open('package.json'))['scripts'])"
npm run lint
npm test          # several of these repos have no unit tests — the script echoes and exits 0
```

- A `test` script that echoes `No Test` means there is no unit coverage. Say so in the PR
  rather than inventing a suite.
- Some repos carry Playwright e2e (`npm run test:e2e`); it needs a running app, so it is
  not part of the per-fix loop.
- These are the surfaces most likely to carry checkout, paywall, and subscription errors —
  treat anything on the revenue path as higher-priority than its raw event count suggests.

## Electron

`ConsumerAIDesktop`. Node/React tooling as for the web loop, plus a main-process /
renderer-process split: an error's process of origin decides which half you are fixing, and
the two report through different Bugsnag clients. Confirm which one from the event's
stacktrace before editing.
