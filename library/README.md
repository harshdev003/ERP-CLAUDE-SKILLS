# Reference library

Material kept for reference. **Nothing in `library/` is installed into Claude** — the
sync script installs only `skills/`, `agents/` and `rules/CLAUDE.md`.

## `aura/` — imported 2026-11-21

A snapshot of another engineering team's Claude/Cursor setup ("Aura AI rules and
skills"), shared by a friend as `Aura-AI-Rules-and-Skills.zip` (103 MB, 3,568 real
files + 4,688 macOS `__MACOSX`/`.DS_Store` junk files).

### Where every part went — nothing was deleted

| Part of the zip | Files | Where it is now |
|---|---|---|
| `Skills/` (375 skills) | 1,789 | `library/aura/Skills/` (unchanged) — 12 adapted into `skills/` |
| `Rules/` (per-repo CLAUDE.md, Claude & Cursor rules) | 670 | `library/aura/Rules/` (unchanged) — adapted into `rules/services/` and `rules/personal.md` |
| `Agents/` (40 subagents) | 40 | `library/aura/Agents/` (unchanged) — 3 adapted into `agents/` |
| `Settings/` (settings, plugin manifests, keys already hidden) | 33 | `library/aura/Settings/` |
| `Other/` (AINative docs, scripts, tests, plans) | 370 | `library/aura/Other/` |
| `README.md` | 1 | `library/aura/SOURCE-README.md` |
| `Memory/` (Claude's saved notes about their prod work) | 201 | **Not on GitHub.** Local only: `C:\Users\Harshit\ERP-SOURCE-ARCHIVE\Aura-AI-Rules-and-Skills\Memory` |
| `Handoffs/` (prod QA logs, 180 screenshots) | 463 | **Not on GitHub.** Local only: `C:\Users\Harshit\ERP-SOURCE-ARCHIVE\Aura-AI-Rules-and-Skills\Handoffs` |
| `.DS_Store`, `__MACOSX/` | — | dropped (macOS metadata, no content) |

The complete original is also still at `Downloads\Aura-AI-Rules-and-Skills.zip`, and a
checksum-verified extraction of all 3,568 files is at `C:\Users\Harshit\ERP-SOURCE-ARCHIVE\`.

**Why Memory and Handoffs stay off GitHub:** they contain 33 real email addresses
(their employees, end users, an enterprise customer) and production screenshots.

**Redaction in `library/aura/`:** real email addresses (personal Gmail/Outlook/iCloud, the
company's and its customer's domains, service accounts, Apple relay addresses) in 100+ files were
replaced with `<redacted-email>`. Test placeholders (`a@x.io`, `@example.com`) were left.
The unredacted originals are in the local archive.

### Adapted into this repo

| Original | Now | What changed |
|---|---|---|
| `Skills/eng/review-pr` | `skills/review-pr` | rubric = ERP service rules; ERP copy lens instead of brand voice; multi-tenant probes |
| `Skills/eng/linear-task` | `skills/linear-task` | ERP repo map, ERP examples |
| `Skills/eng/stack-prs` | `skills/stack-prs` | ERP repo paths and examples |
| `Skills/eng/update-changelog` | `skills/update-changelog` | ERP repos, generic ticket ids |
| `Skills/eng/eng-handoff` | `skills/eng-handoff` | ERP repo map |
| `Skills/eng/protect-github-branches` | `skills/protect-github-branches` | harshdev003 / ERP repos |
| `Skills/eng/clean-up-worktrees` | `skills/clean-up-worktrees` | `ERP_REPOS_DIR`, ERP examples |
| `Skills/eng/bugbot-review` | `skills/bugbot-review` | ERP examples |
| `Skills/eng/triage-review-requests` | `skills/triage-review-requests` | unchanged (already generic) |
| `Skills/shared/convene-panel` | `skills/convene-panel` | specialised personas → ERP compliance, finance controller, operator, data integrity |
| `Skills/shared/guide-me` | `skills/guide-me` | removed their plugin dependencies |
| `Skills/My skills/senior-frontend` | `skills/senior-frontend` | unchanged (already generic) |
| `Agents/eng/implementation-agent` | `agents/implementation-agent.md` | ERP repo paths, ERP rules |
| `Agents/eng/code-investigator` | `agents/code-investigator.md` | ERP repo locations and examples |
| `Agents/AuraConsumerApp/cursor/error-analyzer` | `agents/error-analyzer.md` | ERP error patterns (money, tenants, concurrency) |
| `Rules/BusinessService/claude/*` | `rules/services/*.md` | rewritten stack-neutral + ERP rules (money, double-entry, tenants, audit, numbering) |
| `Rules/My rules/CLAUDE.md` | `rules/personal.md` | credit guard + handoff notes kept; team Linear bookkeeping not adopted |

Every adapted skill: their knowledge-base (`knowledge.reads`) frontmatter removed,
plugin prefixes (`eng:`, `shared:`) dropped, company/repo/people names replaced.

### Candidates — adapt once the ERP stack is chosen

Useful ideas, but built on the source team's infrastructure; adapting now would be guesswork.

| Skill in `library/aura/Skills/` | Why it's interesting | Blocked on |
|---|---|---|
| `eng/setup-backend-database` | guided DB onboarding (proxy, client, first query) | which database/host the ERP uses |
| `eng/update-node-packages`, `eng/update-nodejs` | dependency & runtime bump runbooks across services | backend/web stack and CI |
| `eng/release-version` | release cutting runbook | CI/CD and hosting |
| `qa/write-test-cases`, `qa/run-test-cases` | regression-suite authoring & running | where test cases live (repo vs a tool) |
| `Not merged yet/.../eng/detect-attacks` | attack detection from load-balancer logs | hosting / WAF provider |
| `eng/investigate-gcp-logs`, `eng/analyze-gcp-logs` | log triage | logging provider |
| `eng/respond-to-sar`, `eng/respond-to-erasure` | data-subject access / erasure requests | ERP data model |
| `shared/distill-session` | turn session learnings into repo rules | (works with repo rules only once trimmed) |
| `product/brainstorm-ideas` | multi-lens feature ideation | a product backlog/knowledge base |
| `Agents/AuraConsumerApp/cursor/*` (security, performance, accessibility auditors) | mobile app auditors | ERP-APP-SERVICE stack (they are Swift/iOS) |

Everything else in `library/aura/Skills/` is specific to the source company's product
(voice/coaching app, ad creatives, their vendors' cost reports) and is kept only as
examples of how they write skills.
