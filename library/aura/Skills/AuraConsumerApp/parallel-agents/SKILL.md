---
name: parallel-agents
description: End-to-end protocol for executing a Linear ticket via a sub-card fan-out — every non-trivial ticket is decomposed into Design / Integration / Analytics / Sim-test sub-cards (via `shared:add-linear-issues`) BEFORE any code is written, each sub-card opens its own PR off a shared base branch, and a final `eng:review-pr` pass runs against the base→main PR once every sub-PR has merged. Use whenever the user provides a Linear ticket reference (`AUR-123`, `ENGMOB-1810`, a `linear.app` URL, "work on this ticket"). Trigger on phrases like "Work on AUR-123", "Work on ENGMOB-1810 in worktree mode", "set up worktree for AUR-456", "parallel feature work on AUR-789", "implement AUR-123 in a worktree", "spawn parallel agents for AUR-123 and AUR-456", "run AUR-789 as a parallel agent", or any Linear ticket ID — with or without a simulator UDID. Also trigger when the user pastes a `linear.app` URL. The skill enforces: (1) mandatory sub-card decomposition before implementation, (2) shared base branch with one PR per sub-card, (3) mode selection (worktree or in-place), (4) simulator UDID binding via `SIM_SESSION_ID` when sim install/launch/sim-test is requested, (5) the six-gate definition of done per sub-PR, (6) the PR + Cursor-bot review flow per `.claude/rules/pr-process.md`, and (7) a final `eng:review-pr` pass on the base→main PR. A single bundled PR for ticket work that spans UI + integration + analytics is REJECTED by default — decomposition is non-negotiable.
---

# Parallel Agents

End-to-end protocol for executing a Linear ticket via a sub-card fan-out: every non-trivial ticket is decomposed into Design / Integration / Analytics / Sim-test sub-cards before any code is written, each sub-card opens its own PR off a shared base branch, and a final `review-pr` pass runs against the base→main PR once every sub-PR has merged.

## Related Rules + Skills

- `.claude/rules/pr-process.md` — PR creation, Cursor-bot review cycle, reply formats.
- `.claude/rules/sim-testing.md` and `.claude/rules/sim-testing-conventions.md` — simulator testing knowledge (referenced, not always executed).
- `.claude/rules/config.md` — gitignored file restoration in worktrees (REAL copy of plist).
- `.claude/rules/testing.md` — full validation sequence (lint → unit tests → build → install → launch → sim-test).
- `shared:add-linear-issues` — invoked during **Phase 1.5** to create the sub-cards under the parent ticket.
- `eng:eng-handoff` — invoked during **Phase 1.5** when the parent ticket scope is unclear and product/design context is needed before decomposition.
- `eng:review-pr` — invoked during **Phase 5.5** against the base→main PR once every sub-PR has merged.

## When This Skill Applies

Triggered when the user provides a Linear ticket reference (e.g. "AUR-123", a `linear.app` URL, "work on this ticket"). Applies whether you are running in the main repo or already inside a worktree. The Phase 1.5 decomposition is **mandatory** for any ticket touching UI + integration — single-PR delivery is rejected by default. Skip decomposition only when the ticket is a true one-shot (single line in a config file, a typo fix, a comment change) — and even then, document the skip decision back to the user in one line.

---

## Phase 1 — Mode Selection (MANDATORY FIRST ACTION)

Before doing **any** implementation work — before fetching the Linear issue, before running `gh`, `git`, or `xcodebuild` — ask the user:

> Worktree mode (parallel-safe, recommended) or in-place mode for this ticket?

Wait for the answer. Do not proceed until you have the mode (`worktree` or `in-place`).

If the user already provided the mode in the original message, confirm the value back in one line and proceed without asking again.

### Optional: Simulator Binding

```bash
export SIM_SESSION_ID="<UDID>"
```

Simulator binding is optional. It is only needed when the user explicitly requests sim install, sim launch, or sim-test validation. See **Phase 6 — Sim Test Knowledge Update** for when this applies.

## Phase 1.5 — Sub-Card Decomposition (MANDATORY BEFORE ANY CODE)

**This phase is non-negotiable.** Before writing a single line of code, the agent must decompose the parent ticket into the standard sub-cards and create them in Linear under the parent. Skipping decomposition is what causes single-PR bundled deliveries — the failure mode this skill exists to prevent.

### Step 1 — Read the parent ticket end-to-end

Pull the parent ticket via `mcp__claude_ai_Linear__get_issue` and extract:

- Title, description, acceptance criteria
- Figma link(s) — open via `mcp__claude_ai_Figma__get_design_context` if a Figma URL is present
- Feature area, dependencies, experiment / flag gating
- Existing parent metadata: team, project, cycle, milestone, labels, priority, assignee — sub-cards inherit these

If the parent description is unclear or product/design context is missing, invoke `eng:eng-handoff` first to produce an implementation-ready breakdown, then continue.

### Step 2 — Decompose into the standard sub-cards

Default decomposition (omit any sub-card that's genuinely N/A — for example, a backend-only ticket with no UI surface skips the Design sub-card):

| Sub-card               | Scope                                                                                                                                 | Owns                                                                                                                                                                                                                                                                                                               | Branches off |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------ |
| **Design / UI**        | `Features/{Feature}/Views/`, custom `Components/`, `PaywallBackgroundView`-style theme additions                                      | Screen file, reusable visual components, accessibility identifiers, previews. **No** VM wiring, **no** service calls, **no** analytics.                                                                                                                                                                            | base         |
| **Integration**        | `Features/{Feature}/ViewModels/`, coordinator wiring, experiment service calls, IAP / API hooks, `NavigationStack` step additions     | All ViewModel state + methods, experiment resolution, purchase / verification flows, navigation step routing. Pulls in the Design sub-card via the base branch.                                                                                                                                                    | base         |
| **Analytics**          | `Services/Analytics/Models/AnalyticsEventsNames.swift` additions, `*ViewModel+Analytics.swift` extensions, experiment exposure events | Every event for the new surface (screen seen, CTA taps, success / failure), experiment exposure, funnel events. Maps to any analytics spec. Pulls in the Integration sub-card via the base branch. **The Linear card itself MUST follow the canonical analytics-card format — see "Analytics card format" below.** | base         |
| **Sim-test knowledge** | `AuraConsumerAppSimTests/knowledge/screens/<name>.md`, updates to `flows.md` / `workarounds.md`                                       | New screen knowledge file, accessibility identifier reference, edge cases, known workarounds. Pulls in the Design sub-card via the base branch.                                                                                                                                                                    | base         |

If any of the four are obviously N/A, skip — but document the skip in the user-facing summary so the user can challenge it.

### Step 3 — Create the sub-cards via `add-linear-issues`

Invoke `shared:add-linear-issues` to create one Linear issue per sub-card. Each sub-card MUST include:

- **Title**: `[{PARENT-ID}] {Layer}: {one-line description}` (e.g. `[ENGMOB-1810] Design: Monthly downgrade screen UI`)
- **Parent**: the parent ticket ID (`parentId`)
- **Inherited metadata**: team / project / cycle / milestone / priority / assignee from the parent — do not silently change any of these
- **Labels**: inherit the parent's labels for Design / Integration / Sim-test. **The Analytics sub-card is the exception** — it MUST use the `Analytics & Data` label (id `db17094a-b8e8-4afb-83d1-dc55c652c2d3`) instead of the parent's label, so the analytics team sees it in their queue.
- **Description body**: scope, acceptance criteria, ownership, branch name (`{parent-id}/{layer}` e.g. `engmob-1810/design`), dependencies on other sub-cards
- **Estimate**: per the table in `add-linear-issues/SKILL.md` — Design typically 3–5, Integration 5–8, Analytics 2–3, Sim-test knowledge 1–2

#### Analytics card format (MANDATORY — see ENG-502 as the canonical example)

The Analytics sub-card is the **only** sub-card whose ticket body does NOT use the engineering "Scope / Deliverables / Acceptance Criteria" template. It uses the product-owned event-spec format because the analytics team reads it as the source of truth for the tracking plan.

Reference: [ENG-502 — Analytics: Bring Your Memory \[Yawar\]](https://linear.app/aura-health/issue/ENG-502).

**Title**: `Analytics: {Feature Name} [{Assignee First Name}]` — no `[PARENT-ID]` prefix, no "events + experiment exposure" suffix. Example: `Analytics: Cancel Monthly Downgrade [Naveed]`.

**Label**: `Analytics & Data` (id `db17094a-b8e8-4afb-83d1-dc55c652c2d3`). Replace any parent label with this one.

**Body**: one block per event, separated by `---` horizontal rules. Each block follows this exact shape:

```markdown
- [ ] Event Name : `<Event Name In Title Case>` - <one-sentence "fires when..." description>
  - [ ] Event Properties : `<Property Name> : <values | comma separated>`, `<Property Name> : <...>`
```

Trailing sections that may appear:

- A bullet describing **experiment exposure** when the feature is gated by an experiment (`experimentService.assignExperiments(...)` site + the variant log line).
- A bullet pair adding any new **User Property** and **Super Property** (e.g. `Memory Imported : boolean`) — both kept in sync.

**Conventions taken from ENG-502 verbatim**:

- Event names use Title Case, prefixed by `AI` and the feature name (e.g. `AI Import Memory Skipped`, `AI Cancel Monthly Downgrade Switch To Monthly Tapped`).
- Property names use Title Case (e.g. `Sent From`, `Fail Reason`, `Subscription Period`).
- Property value lists use `/` to enumerate the enum (e.g. `Sent From : Onboarding/Home Screen/Settings`).
- One `---` between events. No extra prose between blocks.
- Do NOT add a "Scope" / "Deliverables" / "Acceptance Criteria" section to the analytics body — those belong to the Integration card. The analytics ticket is purely the spec the engineer implements against.

If an existing event is reused (rather than added), still list it so the analytics team has a complete inventory for this surface.

### Step 4 — Create the shared base branch

```bash
PARENT_ID="engmob-1810"        # lowercase
EXPERIMENT="cancelMonthlyDowngrade"   # the experiment / feature key — used in branch + PR scope
USER_PREFIX="naveed1"          # the assignee's git namespace prefix
BASE="${USER_PREFIX}/${EXPERIMENT}-base"
cd "$MAIN"
git fetch origin main
git switch -c "$BASE" origin/main
git push -u origin "$BASE"
```

The base branch is the integration point — every sub-PR targets it, and only the **base→main PR** is reviewed via `eng:review-pr` in Phase 5.5.

#### Branch + PR naming convention (MANDATORY)

When the parent ticket is experiment- or feature-gated, the **experiment / feature name is the scope** in every branch and PR title — not the layer, not the parent ticket ID. The layer slot still appears as the kebab suffix on the branch and the dash-segment in the PR title, but the _scope_ identifier is always the experiment.

| Slot               | Format                                                                     | Example                                                                            |
| ------------------ | -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Sub-card branch    | `{user-prefix}/{sub-ticket-id}-{experimentName}-{layer}`                   | `naveed1/engmob-1815-cancelMonthlyDowngrade-design`                                |
| Sub-card PR title  | `{type}({experimentName}): {Layer} — {one-line summary} [{SUB-TICKET-ID}]` | `feat(cancelMonthlyDowngrade): Design — monthly downgrade screen UI [ENGMOB-1815]` |
| Base branch        | `{user-prefix}/{experimentName}-base`                                      | `naveed1/cancelMonthlyDowngrade-base`                                              |
| Base→main PR title | `{type}({experimentName}): {parent ticket summary} [{PARENT-ID}]`          | `feat(cancelMonthlyDowngrade): A/B monthly downgrade screen [ENGMOB-1810]`         |

**Type slot** (conventional-commits): `feat` for Design / Integration (user-visible behaviour), `chore` for Analytics, `docs` for Sim-test knowledge, `fix` only when the parent is a bugfix ticket. Match what existing merged PRs in this repo use as the scope precedent (`feat(paywall)`, `feat(welcome)`, `chore(sim-tests)`, etc.).

**Why the experiment name is the scope, not the ticket ID or layer:**

- GitHub search / filter groups every PR in the family under one query (`is:pr scope:cancelMonthlyDowngrade`).
- The repo's existing merged-PR history uses `feat({feature}):` not `feat({layer}):` — staying consistent.
- The ticket ID is still in the trailing bracket so it's clickable from the PR list.

**When NOT to use an experiment scope:** non-experiment tickets (a pure bug fix, a release-build chore, a doc update). Those use the natural feature scope of the touched area (`fix(chat)`, `chore(release)`, `docs(readme)`) — exactly the existing repo convention.

### Step 5 — Present the plan to the user

Print a one-screen summary back to the user before moving on:

> **Parent**: AUR-123 — Title  
> **Sub-cards created** (linked under parent):
>
> 1. AUR-124 — Design: Monthly downgrade screen UI
> 2. AUR-125 — Integration: cancellation flow + IAP wiring
> 3. AUR-126 — Analytics: events + experiment exposure
> 4. AUR-127 — Sim-test knowledge: screen file + flow updates
>
> **Base branch**: `aur-123/base` (pushed to origin)  
> **Sub-PRs will target**: `aur-123/base`  
> **Final review**: `eng:review-pr` on `aur-123/base → main` after all sub-PRs merge.
>
> Proceed?

Wait for confirmation. After "yes", every subsequent worktree / agent works on **one** sub-card branch off the base.

## Phase 2 — Analysis & Planning (MANDATORY BEFORE IMPLEMENTATION)

**This phase is non-negotiable.** Before writing any code, the agent must analyze the ticket and determine the right execution strategy.

### Step 1: Fetch and Understand the Ticket

Read the Linear ticket (via `gh` or the Linear URL). Extract:

- **Title and description**
- **Acceptance criteria** (explicit or implied)
- **Feature area** (which part of the app is affected)
- **Dependencies** (other tickets, backend changes, config changes)
- **Experiment/flag gating** (is this behind a feature flag or A/B experiment?)

### Step 2: Complexity Assessment

Evaluate the ticket across these dimensions:

| Dimension                     | Low                            | Medium                             | High                                                      |
| ----------------------------- | ------------------------------ | ---------------------------------- | --------------------------------------------------------- |
| **Files touched**             | 1–3 files                      | 4–8 files                          | 9+ files                                                  |
| **Layers crossed**            | Single layer (e.g. just Views) | Two layers (View + ViewModel)      | Three+ layers (View + ViewModel + Service + Domain)       |
| **Cross-functional concerns** | None                           | One (e.g. analytics OR experiment) | Multiple (analytics + experiment + new service + UI)      |
| **Domain knowledge required** | Straightforward CRUD           | Moderate business logic            | Complex state machines, concurrency, SDK integration      |
| **Risk of PR conflicts**      | Isolated files                 | Shared files with low contention   | Hot files (coordinators, ServiceContainer, shared models) |

Assign an overall complexity: **Small**, **Medium**, **Large**, or **XL**.

### Step 3: Decomposition Decision

Based on the complexity assessment, decide whether to decompose:

| Complexity | Action                                                                                                                              |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Small**  | Implement directly. No decomposition needed. Proceed to Phase 3.                                                                    |
| **Medium** | Consider decomposition if there are clearly separable concerns (e.g. analytics can be split out). Document the decision either way. |
| **Large**  | **Must decompose.** Create sub-tickets before implementing.                                                                         |
| **XL**     | **Must decompose.** Flag to the user that this may need architectural discussion before proceeding.                                 |

### Step 4: Sub-Ticket Creation (when decomposition is needed)

When decomposing, create sub-issues under the parent ticket. Each sub-ticket must include:

```
Title:        [Parent-ID] <clear, action-oriented title>
Scope:        What files/layers this sub-ticket owns
Description:  What the sub-ticket delivers, with enough context for an independent agent
Acceptance Criteria:
  - [ ] Criterion 1
  - [ ] Criterion 2
  - [ ] ...
Ownership:    Which layer/domain this sub-ticket is responsible for
Dependencies: Other sub-tickets that must complete first (if any)
```

#### Common Decomposition Patterns

**Feature + Analytics split** (most common for experiment-based features):

| Sub-ticket                                  | Scope                                                 | Owns                                                              |
| ------------------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------- |
| `[AUR-123] Feature UI & Logic`              | Views, ViewModels, feature-local models               | Screen implementation, navigation, state management               |
| `[AUR-123] Analytics & Experiment Tracking` | AnalyticsManager extensions, experiment service calls | All analytics events, experiment exposure tracking, funnel events |

The Analytics sub-ticket must document:

- What analytics events were added (event names, properties)
- Where tracking was implemented (which ViewModels/Views)
- What experiment exposure or funnel events were introduced
- Mapping to any analytics spec or tracking plan

**Feature + Service + Domain split** (for features requiring new backend integration):

| Sub-ticket                    | Scope                                   | Owns                                         |
| ----------------------------- | --------------------------------------- | -------------------------------------------- |
| `[AUR-123] Domain Models`     | `Domains/` structs, Codable conformance | Data models, API response types              |
| `[AUR-123] Service Layer`     | `Services/` actor, protocol, API calls  | Business logic, networking, caching          |
| `[AUR-123] UI Implementation` | `Features/` Views + ViewModels          | Screens, state management, user interactions |

**Feature + Migration/Config split** (for features requiring config or data changes):

| Sub-ticket                              | Scope                                         | Owns                              |
| --------------------------------------- | --------------------------------------------- | --------------------------------- |
| `[AUR-123] Config & Feature Flag Setup` | RemoteConfig keys, xcconfig, ServiceContainer | Flag registration, default values |
| `[AUR-123] Feature Implementation`      | Feature Views + ViewModels + Services         | Everything behind the flag        |

#### Sub-ticket creation via GitHub/Linear

Create sub-issues programmatically when possible:

```bash
# If using GitHub issues
gh issue create --title "[AUR-123] Analytics & Experiment Tracking" \
  --body "$(cat <<'EOF'
## Scope
Analytics events and experiment tracking for the XYZ feature.

## Acceptance Criteria
- [ ] Experiment exposure event fires on screen load
- [ ] CTA tap event includes variant property
- [ ] Funnel events: screen_viewed → cta_tapped → action_completed
- [ ] Events validated in debug console

## Ownership
AnalyticsManager, ExperimentService extensions only.

## Dependencies
Depends on: [AUR-123] Feature UI & Logic (needs identifier names and screen structure)

Parent: AUR-123
EOF
)"
```

### Step 5: Integration branch and PR (when decomposing — RECOMMENDED)

For **Large / XL** (and **Medium** when sub-tickets may touch overlapping or hot files), use **one integration branch and one integration PR** as the merge hub. Sub-tickets still each get **their own worktree and their own PR**, but those PRs **target the integration branch**, not `main`, until everything lands. That keeps parallel work off `main`, reduces rebase churn, and funnels conflicts into one place.

| Artifact                | Role                                                                                                                                                                                                                           |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Integration branch**  | e.g. `aur-123-integration` — cut from `origin/main` after Phase 2; accumulates all sub-ticket merges.                                                                                                                          |
| **Integration PR**      | **Single** open PR: `aur-123-integration` → `main` (usually **draft** until all sub-work is merged). Description lists every sub-ticket with checkboxes and links to sub-PRs. This is the “base” PR that **manages** the epic. |
| **Sub-ticket branches** | e.g. `aur-123-feature-ui`, `aur-123-analytics` — each in its **own worktree**.                                                                                                                                                 |
| **Sub-ticket PRs**      | Each PR: **base = `aur-123-integration`**, **compare = sub branch**. Merge sub-PRs into integration as they pass review and gates.                                                                                             |

**Flow**

1. Create and push `aur-<parent>-integration`, open the draft integration PR to `main`, paste the sub-ticket checklist in the body.
2. For each sub-ticket: add worktree + branch **from the current tip of `aur-<parent>-integration`** (refresh before starting if siblings already merged).
3. Open sub-PR with `gh pr create --base aur-<parent>-integration ...` (or equivalent in the UI). Each sub-PR gets `eng:review-pr` + a BugBot pass before review — triggered when implemented and re-triggered after each fix that addresses its findings, but not re-fired on unrelated pushes.
4. After **all** sub-PRs are merged into integration, run final validation on the integration branch, mark the integration PR ready, run `eng:review-pr` + a BugBot pass on the integration→`main` PR (re-triggering BugBot after any fix that addresses its findings) + confirm CI, then merge **once** to `main`.

**When sub-PRs may target `main` directly**

Only if sub-tickets are **strictly file-disjoint** (ownership boundaries in Phase 2 guarantee no overlap on hot paths) and the team prefers fewer layers. Default remains **integration hub** for Large/XL.

### Step 6: Report Plan to User

Before proceeding to implementation, present the plan:

> **Complexity Assessment**: [Small/Medium/Large/XL]
>
> **Decision**: [Implement directly / Decompose into N sub-tickets]
>
> **Sub-tickets** (if decomposing):
>
> 1. `[AUR-123] Sub-ticket title` — scope summary
> 2. `[AUR-123] Sub-ticket title` — scope summary
>
> **Execution order**: [parallel / sequential with dependency chain]
>
> **Integration**: [One integration PR `aur-<id>-integration` → `main` + sub-PRs into integration / or direct-to-main if justified]
>
> **Estimated scope per PR**: [files/changes summary]

Wait for user confirmation before proceeding. If the user disagrees with the decomposition, adjust and re-present.

---

## Phase 3 — Worktree Setup (worktree mode only)

Detect if you are already inside a worktree by comparing `git rev-parse --git-common-dir` and `git rev-parse --git-dir`. If they differ, you're already in a worktree — skip to Phase 4.

Otherwise, set up a new worktree from the main repo.

```bash
MAIN="$(git rev-parse --show-toplevel)"
ID="aur-123"      # lowercase Linear ID
SLUG="<short-slug-from-title>"   # e.g., fix-journal-save (kebab-case, ~3-5 words)
WT="$MAIN/.claude/worktrees/$ID"

cd "$MAIN"
git fetch origin main

# Clean any stale worktree from a previous aborted run
if git worktree list | grep -q ".claude/worktrees/$ID"; then
  git worktree remove --force ".claude/worktrees/$ID"
fi

git worktree add "$WT" -b "$ID-$SLUG" origin/main
cd "$WT"

# Configure commit identity for this worktree from the GitHub account
GH_LOGIN=$(gh api user --jq .login)
GH_EMAIL=$(gh api user --jq .email)
if [ -z "$GH_EMAIL" ] || [ "$GH_EMAIL" = "null" ]; then
  GH_EMAIL="${GH_LOGIN}@users.noreply.github.com"
fi
git config user.name  "$GH_LOGIN"
git config user.email "$GH_EMAIL"
```

### Restore gitignored files (MANDATORY — see `.claude/rules/config.md`)

The build crashes at launch without these. The plist must be a **real copy**, not a symlink — xcodebuild silently drops symlinked plists from `Copy Bundle Resources` and Firebase crashes at launch.

```bash
mkdir -p AuraConsumerApp/Config

# REAL COPY for the plist (not symlink)
cp "$MAIN/AuraConsumerApp/Config/GoogleService-Info.plist" \
   "AuraConsumerApp/Config/GoogleService-Info.plist"

# Symlinks are fine for xcconfig (build-time only, not bundled)
for f in Secrets.xcconfig Secrets.debug.xcconfig Secrets.release.xcconfig; do
  ln -sf "$MAIN/AuraConsumerApp/Config/$f" "AuraConsumerApp/Config/$f"
done

# Regenerate .xcodeproj so the plist lands in Copy Bundle Resources
make proj
```

If any source file is missing in `$MAIN`, stop and ask the user. Do not guess.

### Confirm worktree readiness

After setup, print one line back to the user:

> Worktree ready at `<absolute-path>` on branch `<branch-name>`. Open this folder in a new Cursor window and say "continue" to start implementation, or stay here to drive it from this tab.

Wait for the user's response before proceeding to Phase 4.

---

## Phase 4 — Implementation

All edits, builds, commits, and pushes happen inside the worktree (or in the main repo if `in-place` mode was chosen). Use the Linear ticket details, repo conventions in `.claude/rules/`, and existing code as the source of truth.

If working on a sub-ticket from Phase 2, stay strictly within the sub-ticket's declared scope and ownership boundaries. Do not modify files owned by sibling sub-tickets unless a dependency requires it — and document any cross-boundary change.

Follow all existing rules during implementation — `.claude/rules/coding.md`, `.claude/rules/architecture.md`, `.claude/rules/comments.md` (preserve existing comments), `.claude/rules/accessibility.md` (identifiers on every new View), `.claude/rules/service-patterns.md` (a cohesive new concern goes in its own `{Type}+{Concern}.swift` file — never an inline `extension` appended to the primary file), etc.

**Keep every file small and reuse what already exists** (`.claude/rules/coding.md` → "Reviewability — small files, reuse, safe renames"): split any ViewModel/Service past ~350 lines into `{Type}+{Concern}.swift` files **as you write it** (not as a later cleanup); `grep` for an existing component/type before creating a new one; and `grep` the target name before renaming a type so the rename can't collide with an existing type (a colliding rename is a build break, not an improvement). When a create screen and an edit screen are nearly identical, share one view model rather than forking two that drift.

---

## Phase 5 — Validation Gates (ALL REQUIRED)

The task is complete only when all gates pass.

| #   | Gate                         | What it proves               | Command                                              |
| --- | ---------------------------- | ---------------------------- | ---------------------------------------------------- |
| 1   | SwiftLint                    | Code style + repo lint rules | `swiftlint lint --strict <changed-files>`            |
| 2   | Unit tests pass              | Logic correctness            | `xcodebuild test -only-testing:AuraConsumerAppTests` |
| 3   | App builds for iOS Simulator | Compiles cleanly             | `xcodebuild build`                                   |

### Running the gates

```bash
# Gate 1: Lint changed files
swiftlint lint --strict <changed-files>

# Gate 2: Unit tests (use DerivedData-CLI to avoid Xcode lock)
make proj
xcodebuild test \
  -project AuraConsumerApp.xcodeproj \
  -scheme AuraConsumerApp \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro,OS=latest' \
  -only-testing:AuraConsumerAppTests \
  -derivedDataPath DerivedData-CLI \
  -resultBundlePath TestResults/UnitTests

# Gate 3: Build validation
xcodebuild -project AuraConsumerApp.xcodeproj -scheme AuraConsumerApp \
  -destination 'generic/platform=iOS Simulator' \
  build -quiet -allowProvisioningUpdates -destination-timeout 10
```

If any gate fails, fix and re-run from the failed gate forward. Do not skip gates.

### Cleanup after validation

```bash
rm -rf DerivedData-CLI TestResults
```

---

## Phase 6 — Sim Test Knowledge Update (MANDATORY — every PR)

**Do NOT install the app on the simulator or "walk the flow" autonomously.** The user does hands-on QA themselves; auto-install + walk-the-flow is the wrong default and burns time without adding signal. Never add "install on sim" or "walk the flow on sim" as a todo item.

What you **must** do every time you ship a new feature, screen, or user-visible behavior change: update the sim test knowledge so the next time anyone (the user or a sim-test agent) runs a sim test, the feature is documented.

### Files to touch (under `AuraConsumerAppSimTests/knowledge/`)

| File                | When to update                                                                                                                        |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `screens/<name>.md` | New screen added, OR existing screen got a new element, button, or accessibility identifier. Create the file if the screen is new.    |
| `screens/README.md` | New screen file added — register it in the index.                                                                                     |
| `flows.md`          | New end-to-end flow that traverses multiple screens (paywall → checkout → success, onboarding → home, etc).                           |
| `workarounds.md`    | Discovered a XCTest quirk, timing trap, or platform bug during implementation. Even speculative ones — sim test agents will hit them. |
| `rules.md`          | New global rule, safety constraint, or experiment-API surface. Rare — usually only on infra changes.                                  |

### What to put in `screens/<name>.md`

For each screen new or modified:

- The exact `accessibilityIdentifier` for the screen container (must end in `_screen`).
- Every interactive element's identifier + label.
- Any UIViewRepresentable children with explicit identifiers (to prevent propagation bleed — see `.claude/rules/sim-testing.md`).
- A short prose note on what the screen does and how to enter / exit it.
- Any experiment gates that affect what shows here.
- Audio status hooks if the screen plays audio (`audio_playing`, `audio_stopped`).
- Known XCTest quirks specific to this screen (capsule-clipped buttons that need coordinate taps, sheets that need `waitForExistence`, etc).

### Code-side accessibility requirements (always)

Whether or not you add knowledge entries, every new SwiftUI View must already conform to `.claude/rules/accessibility.md` — without identifiers, the sim test framework can't see the screen no matter how well the knowledge file is written.

- Root container: `.accessibilityIdentifier("{feature}_screen")` — snake_case, ends in literal `_screen`.
- Buttons / fields / toggles: `{feature}_{action}Button` / `{feature}_{name}Field` / etc.
- Audio-playing views: `.audioStatusAccessibility(ttsState:)`.
- `UIViewRepresentable` children (text fields, voice buttons): explicit identifiers so they don't inherit the screen's.
- Session-ID convention: never `"default"` — always the simulator UDID — see `.claude/rules/sim-testing-conventions.md`.

This way the user can immediately run a sim test against the new feature when they want to — but no agent attempts an install or sim walk on their behalf.

### When the user explicitly asks for sim validation

Only if the user says something like "run a sim test on this", "install and verify on the simulator", or "walk the flow", do you run the actual sim install + sim-test scripts. Treat that as a separate, explicit user request — not part of the default per-PR workflow.

```bash
# Only when explicitly requested:
export SIM_SESSION_ID="<UDID>"
bash AuraConsumerAppSimTests/Scripts/sim_install.sh --session "$SIM_SESSION_ID"
bash AuraConsumerAppSimTests/Scripts/sim_test.sh --session "$SIM_SESSION_ID" --goal "<flow>" --max-iterations 150
```

If a feature needs a brand-new sim-test flow and the user has asked for sim validation, add the flow under `AuraConsumerAppSimTests/Flows/` per `.claude/rules/sim-testing.md`.

---

## Phase 7 — PR Creation

Once all required gates are green:

1. **Commit** using the format from `.claude/rules/pr-process.md`:

   ```
   <type>(<scope>): <one-line summary>

   Issue: <what was wrong / what the ticket asks for>
   Root Cause: <one sentence — for bugs>
   Fix: <what the change does>
   Testing: SwiftLint --strict passed; unit tests passed; build succeeded.

   Closes: AUR-123
   ```

   `<type>` is `feat`, `fix`, `refactor`, `chore`, etc. `<scope>` is the feature name (e.g. `journal`, `onboarding`).

   If sim validation was performed, add to Testing:

   ```
   Testing: SwiftLint --strict passed; unit tests passed; sim install + launch + sim-test passed on UDID <UDID>.
   ```

2. **Push** the branch:

   ```bash
   git push -u origin "$(git branch --show-current)"
   ```

3. **Open the PR** via `gh pr create`. Use the template in `.claude/rules/pr-process.md`.
   - **Integration PR** (first, when decomposed): `gh pr create --base main --head aur-<parent>-integration` — draft is fine; body lists all sub-tickets.
   - **Sub-ticket PRs**: `gh pr create --base aur-<parent>-integration --head <sub-branch>` so each sub-PR merges into the hub branch, not directly into `main`.

   ```markdown
   ## Description

   [Brief summary]

   ## Changes

   [Detailed list]

   ## Complexity Assessment

   [Small/Medium/Large/XL — from Phase 2]

   ## Sub-tickets

   [If decomposed: list sibling sub-tickets and their status]

   ## Testing

   - [x] SwiftLint --strict passes
   - [x] Unit tests pass
   - [x] Build succeeds
   - [ ] Sim install + launch verified (if applicable)
   - [ ] Sim-test passes (if applicable)

   ## Related

   - Linear Issue: [AUR-123](https://linear.app/aurahealth/issue/AUR-123)
   - Parent ticket: [AUR-100](link) (if this is a sub-ticket)
   ```

4. **Run a BugBot pass on the sub-PR before review.** Trigger BugBot when the sub-PR is implemented (Phase 5 gates green), address every finding, then re-trigger it on the fix commit so BugBot verifies the fix — repeating until a run on the current head is clean. Don't re-fire on pushes unrelated to its findings. `eng:review-pr` is available on request for a deeper pass but is not a required gate.

5. **Push fix commits** for any gate failures and confirm CI is green.

6. Mark the sub-PR ready for review (or merge it into the base per the integration flow).

## Phase 5.5 — Sub-PR Aggregation + Final review-pr (MANDATORY)

This phase replaces the legacy single-PR finish line. Run it once **every** sub-PR opened in Phase 5 has merged into the shared base branch.

### Step 1 — Verify every sub-PR has merged

```bash
gh pr list --state merged --base "$BASE" --json number,title,headRefName
```

Confirm Design / Integration / Analytics / Sim-test sub-PRs all show as merged into `$BASE`. If any are still open or in cursor-bot review, stop here and finish them under Phase 5 first.

### Step 2 — Resolve conflicts in the base branch

```bash
cd "$MAIN"
git switch "$BASE"
git pull origin "$BASE"
git fetch origin main
git merge origin/main          # only if main has moved since base was cut
```

If merging `origin/main` into the base surfaces conflicts, resolve them inside the base branch, run SwiftLint + build to confirm, then push:

```bash
swiftlint lint --strict <changed-files>
xcodebuild -project AuraConsumerApp.xcodeproj -scheme AuraConsumerApp \
    -destination 'generic/platform=iOS Simulator' \
    build -quiet -allowProvisioningUpdates -destination-timeout 10
git push origin "$BASE"
```

Never resolve conflicts inside a leaf sub-PR by force-pushing — that rewrites history other agents may be sitting on. Conflicts always resolve in the base.

### Step 3 — Open the base→main PR

```bash
gh pr create \
  --base main \
  --head "$BASE" \
  --title "{PARENT-ID} — {parent ticket title}" \
  --body "$(cat <<'EOF'
## Description
Aggregates the four sub-PRs that landed on this base branch:

- [x] Design — #<sub-PR-1>
- [x] Integration — #<sub-PR-2>
- [x] Analytics — #<sub-PR-3>
- [x] Sim-test knowledge — #<sub-PR-4>

## Testing
- [x] SwiftLint --strict passes on the merged base
- [x] Unit tests pass on the merged base
- [x] Build succeeds for iOS Simulator
- [x] App installs and launches on sim UDID <UDID> (if requested)
- [x] Sim-test for feature passes (if requested)

## Related
- Linear Issue: [<PARENT-ID>](https://linear.app/aurahealth/issue/<PARENT-ID>)
EOF
)"
```

### Step 4 — BugBot review on the base→main PR

Run a BugBot pass on the base→main PR — this is the aggregate review over the combined diff. Post a `bugbot run` comment, address every finding inside the base branch (not in a leaf sub-card), push, then re-trigger BugBot so it verifies the fix — repeating until a run on the current head is clean. BugBot is the required gate.

`eng:review-pr` is available on request for a deeper adversarial pass (brand voice, repo conventions, cross-file consistency, naming). Invoke it here if the engineer asks, or if the combined diff is large enough to warrant it.

### Step 5 — Merge the base→main PR

Only after the BugBot cycle on the base→main PR is clean (and `eng:review-pr` if requested). Then merge via `gh pr merge --squash` (or whatever merge strategy the repo defaults to).

### Step 6 — Tear down

```bash
git push origin --delete "$BASE"
# Plus each sub-card worktree per Phase 7
```

## Phase 8 — Iteration on User-Reported Bugs

When the user reports a bug:

1. Stay in the same worktree, on the same branch.
2. If a simulator UDID is bound, reproduce the bug on the assigned sim before fixing — do not assume.
3. Fix it.
4. Re-run the validation gates (Phase 5, and Phase 6 if sim-bound) before declaring it fixed.
5. Commit the fix, push, address any new Cursor-bot findings.

---

## Phase 9 — Teardown (when feature is wrapped)

After the PR is merged, or when the user explicitly says "tear down AUR-123":

```bash
cd "$MAIN"
git worktree remove ".claude/worktrees/<id>"
```

If the worktree has unexpected uncommitted changes, surface them to the user before removing. Do not auto-discard.

---

## Parallel Run Conventions

Multiple worktrees can exist concurrently — they share `$MAIN/.git` but have isolated working trees, `DerivedData-CLI/`, and branches.

For each parallel feature, create a separate worktree. If sim testing is needed, bind a distinct simulator UDID per worktree. To create more sims:

```bash
xcrun simctl clone "iPhone 17 Pro" "iPhone 17 Pro #2"
xcrun simctl boot "iPhone 17 Pro #2"
xcrun simctl list devices booted    # capture the new UDID
```

Each agent with sim binding owns one UDID end-to-end, set as `SIM_SESSION_ID`. Never share a UDID between two active worktrees.

### Sub-ticket parallelism and the integration hub

When Phase 2 produces sub-tickets:

1. **Integration hub (default for decomposed work):** One branch `aur-<parent>-integration` and **one** integration PR into `main` tracks the whole epic. Sub-ticket PRs use **`--base aur-<parent>-integration`** so there is a single funnel into `main`, not N unrelated PRs racing on `main`.
2. Each sub-ticket still gets **its own worktree** and **its own branch** (e.g. `aur-123-feature-ui`, `aur-123-analytics`).
3. Independent sub-tickets can run in parallel **when their ownership boundaries do not overlap**; otherwise serialize merges into integration or split scope further.
4. Each sub-ticket opens **its own PR** into the integration branch (not `main`), linking the parent Linear issue and the integration PR in the description.
5. Close the **parent** Linear issue when the **integration** PR merges to `main` (all sub-work already merged into integration). Sub-issues close as their sub-PRs merge into integration.

---

## Quick Reference — Per-Tab Prompt

Once this skill is in place, the per-tab prompt for any Linear ticket is just one of:

- **Parent ticket (first invocation)**: "Work on AUR-123 in worktree mode" (or "…sim UDID ABCD-1234" if sim install/launch/sim-test is needed).
  - Agent runs Phase 1 → 1.5 (decompose + create sub-cards via `add-linear-issues` + base branch) → presents the plan → waits for confirmation.
- **Sub-card (after decomposition)**: "Work on AUR-124" (one of the sub-cards created in Phase 1.5).
  - Agent jumps to Phase 2 → 3 → 4 → 5 for that single sub-card, branching off `aur-123/base` and opening a sub-PR targeting the base.
- **Aggregation (after every sub-PR has merged)**: "Run review-pr on aur-123 base" or "Finish AUR-123 base".
  - Agent runs Phase 5.5: opens the base→main PR, runs `eng:review-pr`, fixes findings on the base, merges, tears down.

The shape of the workflow is: **one parent prompt → N sub-card prompts in parallel → one aggregation prompt at the end.** A single bundled PR for parent ticket work is not part of the workflow.
