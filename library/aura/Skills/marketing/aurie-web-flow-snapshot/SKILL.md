---
name: aurie-web-flow-snapshot
description: >
  Build a structured flow_map of the Aurie Web (a.k.a. AIWeb / AI Web) signup
  onboarding flow from live AuraHealth/AIWeb source — variant-aware, with
  per-screen shipped_features taxonomy, Mixpanel event lexicon, and live SSR
  verification. Reads source via a 3-tier cascade (GitHub Connector → git
  clone with the gh CLI credential → Notion-backed snapshot fallback) and
  optionally refreshes the Aurie Web Signup Flow — Static Snapshot Notion
  page after a successful live build. Returns the flow_map in memory for
  any caller skill (audit, ideation, doc-gen, etc.). Trigger when the user
  mentions refreshing or inspecting the Aurie Web flow snapshot, asking
  what's currently shipped on a specific signup screen, or building the
  flow_map for a downstream skill.
metadata:
  version: "3.0.3"
knowledge:
  reads:
    - Guide/Index                                                       # baseline routing aid (universal)
    - Products/Aurie/Features/Onboarding/Aurie Web Signup Flow — Static Snapshot       # page 35375e23af2080c581a8ebc7bb8c1af8 (Tier 3 fallback + drift comparison)
    - Tools/Mixpanel/Events                                             # canonical event lexicon for cross-check
    - Tools/Notion                                                # Notion REST / MCP access pattern (NOTION_TOKEN)
  writes:
    - Products/Aurie/Features/Onboarding/Aurie Web Signup Flow — Static Snapshot       # optional refresh after live build (only when --refresh-snapshot is passed)
---

# aurie-web-flow-snapshot

A foundation skill that produces a clean, typed `flow_map` of the Aurie Web signup onboarding flow. Other skills (`aurie-web-onboarding-audit`, `aurie-web-onboarding-experimentation-ideas`) call this one to get the flow data instead of duplicating sync + extraction logic.

**Scope:** Aurie Web onboarding/paywall surface only (`https://www.aurie.ai/signup`). Returns `flow_map` + `variant_decision` + `shipped_features` per screen + optional drift report.

**What this skill is NOT:**

- **Not an analyzer.** It does not pull Mixpanel funnel data or audit drop-offs — that's `aurie-web-onboarding-audit`.
- **Not an idea generator.** It does not propose experiments — that's `aurie-web-onboarding-experimentation-ideas`.
- **Not a browser crawler.** Cowork can't host Chromium reliably. SSR pages are spot-checked via `curl`; SPA-rendered quiz pages are trusted to source.
- **Not multi-variant.** Default extraction targets the active variant (Notion-driven via R1 in `references/flow-extract.md`). Auditing under-test variants needs an explicit `aiweb-ref=<branch>` to the variant branch.

## When to use

Trigger when the user says any of (`Aurie Web`, `AIWeb`, and `AI Web` all mean the same thing — match on any):

- "build the Aurie Web flow_map"
- "refresh the Aurie Web flow snapshot"
- "what's currently shipped on the Aurie Web `<screen>` screen"
- "show me the Aurie Web V3 signup flow"
- "what variant is currently live on Aurie Web"
- "extract the Aurie Web onboarding flow from source"

Also runs as a sub-step when called by `aurie-web-onboarding-audit` or `aurie-web-onboarding-experimentation-ideas`.

## Inputs

The skill takes no required arguments. Optional flags:

- `aiweb-ref=<branch-or-sha>` — pin the live source read to a specific git ref. Default: `main`. Useful for reproducing previous runs or auditing unmerged branches.
- `skip-aiweb-sync` — skip live sync entirely, use the Notion snapshot directly. Forces snapshot-only `flow_map`.
- `skip-live-verify` — skip the SSR verification step. Use when offline or when you trust source completely.
- `refresh-snapshot` — after a successful live build, write the refreshed `flow_map` back to the `Aurie Web Signup Flow — Static Snapshot` Notion page.

Default: attempt live sync (3-tier cascade), do not refresh Notion.

## Prerequisites

### Hard requirements

- **Notion access** to `Aurie Web Signup Flow — Static Snapshot` (page id `35375e23af2080c581a8ebc7bb8c1af8`) — used as the Tier 3 source fallback and as the destination of the optional `--refresh-snapshot` write. At least one of:
  - `NOTION_TOKEN` set in `~/.claude/settings.json` env — preferred. REST fetch + update are direct (see root [CLAUDE.md → Notion REST integration](../../../../CLAUDE.md#notion-rest-integration)).
  - **Notion MCP** connected — fallback. `notion-fetch` for reads and `notion-update-page` for the refresh write both work; this skill operates on a single known page id and doesn't need DB enumeration, so MCP is fully sufficient here.

  Without either, the skill cannot resolve `flow_map` (Tier 3) and cannot complete its persistence step. Stop with: *"Notion not available — set NOTION_TOKEN or connect the Notion MCP and re-invoke."*

- The reference file `references/flow-extract.md` is present (defines R1–R12).

### Soft requirements (graceful degradation)

- **Live AIWeb source access** — Step 1 attempts a 3-tier sync (GitHub Connector → git clone with the `gh` CLI credential → Notion-knowledge fallback). At least one of the first two tiers is *strongly recommended* but not required. Without either, every run uses the Notion-backed snapshot.
- **`mcp__workspace__bash`** — required for Tier 2 (git clone) and Step 2 SSR verification. If unavailable, Tier 1 or Tier 3 still produces a usable `flow_map`; SSR verification is skipped.
- **Snapshot freshness** — when the Notion snapshot is the active source (Step 1 fell back to Tier 3), if the page's `Captured:` date is >30 days old, prepend the staleness warning to the run output.

## Workflow

### Step 0 — Entry banner + pre-load Notion snapshot

#### (a) Entry banner — always first

Before any tool call, generate a fresh `run_id` and print an entry banner. This banner is how callers (orchestrator, audit, user) verify the skill actually ran instead of being silently shortcut by an agent reusing prior context.

Generate `run_id = "fls-" + random 8-char hex` (fresh on every invocation — never reuse a `run_id` from a prior turn).

Print:

```
🔧 aurie-web-flow-snapshot starting (run_id: <id>)
   invoked_by: <caller-skill-name | "user">
   invoked_at: <ISO timestamp>
   inputs: { aiweb-ref: <ref>, skip-aiweb-sync: <bool>, skip-live-verify: <bool>, refresh-snapshot: <bool> }
```

The `invoked_at` timestamp must be the actual current time at the start of this run — not lifted from a prior run. The `run_id` proves a fresh invocation.

#### (b) Pre-load Notion snapshot

Fetch `Aurie Web Signup Flow — Static Snapshot` (page id `35375e23af2080c581a8ebc7bb8c1af8`) at the start of every run. Two reasons:

1. **Tier 3 fallback** — if Step 1's live cascade fails, this is what Step 2b parses.
2. **Drift comparison** — when Tier 1 or Tier 2 wins, comparing the live `flow_map` against the snapshot surfaces stale screens; the run prints `📌 Snapshot drift: N screens changed since <Captured: date>` so the team knows when to refresh.

The page stores its content as a sequence of `code` blocks (each holding a chunk of the original markdown — Notion's per-rich-text size cap forces the chunking). Fetch the page's children, extract every `code` block's `rich_text` content in order, and concatenate them. The recombined string is byte-for-byte the original snapshot markdown (intro + lexicon + per-screen YAML blocks under `## flow_map`).

If the `Captured:` date is older than 30 days AND Step 1 ends up using Tier 3, prepend:

```
⚠️ Flow snapshot is N days old AND no live source available. Returned flow_map may reference copy that's no longer live.
   Either configure live AIWeb access or refresh `Aurie Web Signup Flow — Static Snapshot`.
```

### Step 1 — Sync AIWeb source (live → snapshot fallback)

Read live signup-flow source from `AuraHealth/AIWeb` via the most reliable channel available. This step probes three tiers in order and records the outcome on a `flow_source` variable used by Step 2.

**Skip this step entirely if** the user passed `skip-aiweb-sync` — set `flow_source = "snapshot (skip-aiweb-sync)"` and continue to Step 2.

Use `aiweb-ref` (default `main`) wherever the channel needs a git ref.

#### Tier 1 — GitHub Connector (preferred)

Probe whether a GitHub Connector is available in this turn. Look for any tool whose name resembles `github_get_file_contents`, `github_search_code`, `github_read_file`, `read_repository_file`, or any Anthropic-managed GitHub tool registered in the current MCP/connector descriptors. If none of these tools is exposed, **skip to Tier 2**.

If the connector is present, fetch each of these files from `AuraHealth/AIWeb` at ref `aiweb-ref`, in **parallel** (issue all reads in a single tool-call batch when the connector permits it):

| Path | Why we need it |
|---|---|
| `src/lib/quiz-config.ts` | `cOnboardingQuizConfig.questions` (V3 desktop quiz, ~lines 751–1273), `cSections` (~line 1285) |
| `src/lib/constants.ts` | `ROUTES` object — canonical signup paths |
| `src/services/onboardingAnalytics.ts` | `EVENTS` const, `OnboardingScreenName` enum, `QUIZ_QUESTION_TO_SCREEN`, `MOBILE_QUIZ_QUESTION_TO_SCREEN`, all `track*` functions |
| `src/pages/signup/index.tsx` | Landing page H1, CTA, variant bucketing |
| `src/pages/signup/quiz.tsx` | Quiz host page; confirms the V3 question rendering wrapper |
| `src/pages/signup/results.tsx` | Results page H1, components |
| `src/pages/signup/goal.tsx` | Goal page H1, body copy |
| `src/pages/signup/choose-fee.tsx` | Trial-fee selector, options, CTA |
| `src/pages/subscribe/index.tsx` | Paywall — variants, headlines, plans |
| `src/pages/get-app/index.tsx` | Get-app confirmation page |
| `docs/web-onboarding-v3-spec.md` | Authoritative narrative — section names, intent annotations |
| `e2e/signup-onboarding.spec.ts` | Top-of-file comment with verified happy-path order |

Capture each file's **content** + the **commit SHA** the connector returns. If any one file fails, log the per-file failure but proceed with the others — only fall through to Tier 2 if **all** reads fail (likely an auth or permission problem).

On success, set:

```yaml
flow_source: "live-github-connector @ <sha>"
live_files: { "<path>": "<content>" }   # in-memory, used by Step 2
```

#### Tier 2 — Git clone via bash (fallback)

If the GitHub Connector isn't available (Tier 1 skipped) **or** all Tier 1 file reads failed:

1. Probe for `mcp__workspace__bash`. If unavailable → skip to Tier 3.
2. Invoke [`scripts/aiweb-clone.sh`](scripts/aiweb-clone.sh) via `mcp__workspace__bash`. The script sparse-clones (or refreshes) `AuraHealth/AIWeb` at `/tmp/aiweb` and prints a single trailing line that the caller parses (see table below). The token is hardcoded inside the script; pass `AIWEB_REF` through the env block when the user supplied `aiweb-ref=<ref>`. The script's full I/O contract — env vars, every possible trailing-line value, and side effects — is documented in its header comment; treat that header as the source of truth and don't duplicate it here.

Parse the trailing line:

| Output | Action |
|---|---|
| `TIER2_OK=cache_hit\|pull\|clone COMMIT=<sha>` | Set `flow_source = "live-github-clone @ <sha>"`. Step 2 reads files from `/tmp/aiweb/` via `Read`/`Grep`. |
| `TIER2_FALLBACK=no_token` | Skip to Tier 3. (Should not happen — token is hardcoded.) |
| `TIER2_FALLBACK=pull_timeout\|clone_timeout\|sparse_checkout_failed` | Skip to Tier 3. Surface the reason in the readout. |
| Non-zero exit | Skip to Tier 3. Surface "tier 2 unexpected error". |

#### Tier 3 — Notion-knowledge snapshot fallback (always works when Notion is reachable)

If both Tier 1 and Tier 2 returned a fallback signal, set `flow_source = "snapshot"` and continue to Step 2. Step 2 will parse the in-memory copy of `Aurie Web Signup Flow — Static Snapshot` (already loaded at Step 0) instead of live source. **Step 1 itself never aborts** — falling through to the Notion-backed snapshot is a valid run.

Apply the snapshot freshness check from Prerequisites: if the page's `Captured:` date is >30 days old, prepend the staleness warning.

#### Outcomes summary (always print)

```
🔌 AIWeb source sync:
   Tier 1 (GitHub Connector): <one of: ✅ ok | ⏭️ not available | ⚠️ partial (X/12 files) | ❌ all reads failed>
   Tier 2 (git clone):        <one of: ✅ ok | ⏭️ not attempted | ⚠️ no token | ❌ <reason>>
   Tier 3 (snapshot):         <one of: ⏭️ not used | ✅ used (Captured: <date>)>
   ─────────────────────────────────────────────
   flow_source: <value>
```

### Step 2 — Build flow_map (live source or snapshot)

Branch on `flow_source` from Step 1.

#### Step 2a — flow_source starts with "live-github-…"

Build `flow_map` from live AIWeb source by following the **runtime extraction procedure** in [`references/flow-extract.md`](references/flow-extract.md#runtime-extraction-procedure-when-live-source-is-available). The procedure (R1–R12) tells you exactly which sections of which file to parse, in what order, and how to compute Mixpanel resolution + tracking gaps + mapping mismatches + per-screen `shipped_features` from raw source.

**Critical pre-step (R1) — variant decision is Notion-first.** R1 needs `experiment_state` (Aurie Web rows from `Learnings/Experiments`) to pick which onboarding variant to audit. Two ways to satisfy this:

1. **Caller-supplied** (preferred): when this skill is invoked by `aurie-web-onboarding-experimentation-ideas` (which calls `aurie-web-experiment-history` first), the caller passes its `experiment_state` in. Use it as-is.
2. **Self-fetch** (standalone runs): when invoked directly (the user wants just the flow_map), query `Learnings/Experiments` (`2cbe45a3246040658b9f20559b5b452b`) inline with filter `Title matches ^webOnboarding[A-Z][A-Za-z0-9]*$`. This is a lightweight one-DB-call dependency — the regex is narrow enough that the return is small.

After R1 sets `variant_decision`, R2–R10 extract `flow_map` from the chosen variant's source export, R11 sanity-checks the result, and R12 annotates per-screen `shipped_features` (12-tag taxonomy: `personalization`, `social_proof`, `trust_signals`, `risk_reversal`, `urgency_scarcity`, `pricing_patterns`, `progress_indicators`, `conditional_branching`, `transition_copy`, `safety_guardrails`, `save_progress`, `post_signup_followup`).

Two access modes inside Step 2a depending on which Tier 1 won:

| Tier won | How Step 2a reads the files |
|---|---|
| Tier 1 (Connector) | Files are in the `live_files` in-memory map from Step 1. Parse by string operations on those values — no further tool calls needed. |
| Tier 2 (clone) | Files live in `/tmp/aiweb/<path>`. Use `Read`/`Grep` to inspect them on demand. |

Both modes produce the same `flow_map` shape — entries with `screen_index`, `screen_id`, `screen_name`, `url_path`, `route_kind`, `applicable_variants`, `headline`, `subheadline`, `options`, `cta_primary_text`, `components_present`, `mixpanel_event`, `notes`, **`shipped_features`**.

After building, **also compute drift vs the Notion-backed snapshot** as a free sanity check:

- For each `screen_name` in the live `flow_map`, look up the matching entry in the Step 0 in-memory copy of `Aurie Web Signup Flow — Static Snapshot`.
- Flag drift if `headline`, `cta_primary_text`, or `options` differ.
- Drift > 0 means the snapshot page is stale — note in the readout but do NOT fail (the live result is authoritative). When drift > 0, surface a one-line recommendation: *"Pass `refresh-snapshot` to update the Notion page."*

```yaml
snapshot_drift:
  vs_captured: <date from Aurie Web Signup Flow — Static Snapshot page header>
  screens_changed: <int>
  changed_screen_names: [...]
```

#### Step 2b — flow_source == "snapshot"

The `Aurie Web Signup Flow — Static Snapshot` Notion page (loaded at Step 0) is the runtime source of truth when no live tier is available.

Parse the per-screen YAML blocks in the recombined Step 0 snapshot string into the in-memory `flow_map` list, same shape as Step 2a (every screen carries `applicable_variants` + `shipped_features`). The Notion-side chunking into multiple `code` blocks is an encoding artifact only — once concatenated, the string parses the same way it would have as a local markdown file.

Even in snapshot mode, R1 still runs (against `experiment_state`) to set `variant_decision.variant`. Then apply the **same R5.3 filter** before handoff: `flow_map = [s for s in flow_map if variant_decision.variant in s.applicable_variants]`.

#### Print

```
✅ Built flow_map from <flow_source>: <N> screens for variant <variant_decision.variant>
   Tracking gaps: <N> unmapped screens, <M> mapping mismatches
   Shipped features audited: <K> screens carry shipped_features annotations
```

When `flow_source` starts with `live-github-…` and `snapshot_drift.screens_changed > 0`, also print:

```
   📌 Snapshot drift: <N> screens changed since Aurie Web Signup Flow — Static Snapshot was captured (<date>).
      Affected: <comma-separated screen_names>
      Pass refresh-snapshot to update the Notion page.
```

### Step 3 — Live SSR verification (graceful degrade)

Cowork can't run a full browser crawl, but it **can** fetch the server-rendered HTML for the landing page and 5 post-quiz routes via `mcp__workspace__bash` + `curl`. This catches the most likely staleness cases — a copy change on the landing CTA, a new post-quiz route, a removed paywall — without needing a browser.

**Skip this step entirely if** the user passed `skip-live-verify`, OR if `mcp__workspace__bash` isn't available, OR if the network is unreachable. Failure to verify live is **never a blocker** — fall back to snapshot-only.

#### What we can verify with curl

| Page | Verifiable? | Why |
|---|---|---|
| `/signup` (landing) | ✅ | SSR via `getServerSideProps` — full HTML in first response |
| `/signup/results` | ✅ | SSR shell with static copy in first paint |
| `/signup/goal` | ✅ | SSR shell (only applies to `webOnboardingVariant2`) |
| `/signup/choose-fee` | ✅ | SSR shell (only applies to `webOnboardingVariant2`) |
| `/subscribe` | ✅ | SSR shell — confirms paywall is still present |
| `/get-app` | ✅ | SSR shell with the get-app heading |
| `/signup/quiz?q=N` | ❌ | All `?q=N` URLs return the same SSR shell. Per-question copy is hydrated client-side. |
| Modals (save-progress, etc.) | ❌ | Overlays, not separate routes |

Skip URLs whose path is not in any `applicable_variants` for the chosen variant.

#### Verification procedure

For each verifiable URL, run a single `curl` via `mcp__workspace__bash`:

```bash
curl -sS -L --max-time 10 -o /tmp/live-<slug>.html -w "HTTP_STATUS=%{http_code}\n" \
  "https://www.aurie.ai<url-path>"
```

Then read `/tmp/live-<slug>.html` and check for **one or two distinctive strings** from the corresponding snapshot entry. Use `rg`/`grep` against the file rather than loading the whole HTML into context.

```bash
rg -F "Feel lighter in" /tmp/live-signup.html && echo OK_LANDING_HEADLINE || echo MISMATCH_LANDING_HEADLINE
rg -F "Try Aurie Free"   /tmp/live-signup.html && echo OK_LANDING_CTA      || echo MISMATCH_LANDING_CTA
```

Use literal-string match (`-F`) — don't try to regex-match marketing copy with hyphens, em-dashes, or Unicode.

#### Outcomes

For each verified page, classify:

| Result | What it means | What to do |
|---|---|---|
| `OK` | All key strings found, status 200 | Snapshot matches live. Continue. |
| `MISMATCH_<field>` | Page reachable but a key string is missing | Snapshot may be stale. Add to `live_verification_mismatches` and surface in readout. **Do not block the run.** |
| `UNREACHABLE` | curl failed / network error / non-2xx after retries | Skip this page. Surface as a single line. |
| `SKIPPED` | `skip-live-verify` flag, or `mcp__workspace__bash` not available | Don't run further verification. |

Aggregate into:

```yaml
live_verification:
  attempted: <bool>
  attempted_at: <ISO timestamp>
  results:
    /signup:           OK | MISMATCH_HEADLINE | UNREACHABLE | SKIPPED
    /signup/results:   ...
    /signup/goal:      ...
    /signup/choose-fee: ...
    /subscribe:        ...
    /get-app:          ...
  unverifiable_count: <int>   # quiz steps + modals
  mismatches: [...]
```

#### Print

```
🌐 Live verification (curl, SSR pages only):
   /signup            ✅ OK (Feel lighter in, Try Aurie Free)
   /signup/results    ✅ OK (Aurie shell, 200)
   /subscribe         ✅ OK (Stripe paywall present)
   /get-app           ✅ OK (Aurie shell, 200)
   ────────────────────────────────────────────
   ⚠️ <N> quiz screens + modals not live-verifiable in Cowork (SPA-rendered) —
      trusting source/snapshot from <flow_source>
```

### Step 4 — Optional: refresh Notion snapshot

**Only run this step when the user passed `refresh-snapshot` AND Step 1 won via Tier 1 or Tier 2 (live source).** Refreshing from a snapshot-only run would just rewrite the page with itself.

Re-render the live `flow_map` (plus the lexicon and tracking-gap annotations) into the canonical markdown layout the snapshot page uses (intro + lexicon + per-screen YAML blocks under `## flow_map`), then update the Notion page:

1. Update the `Captured:` date at the top of the page to today's ISO date.
2. Replace the body content (the sequence of `code` blocks) with freshly chunked content holding the regenerated markdown.
3. Surface the new page URL + screen count in the print output.

When the maintainer-refresh procedure documented in `references/flow-extract.md → Maintainer refresh procedure` is appropriate (manual edits, partial refreshes), defer to that — Step 4 is the automated path; the maintainer procedure is the manual fallback.

### Step 5 — Return artifact

Return the full bundle to the caller:

```yaml
flow_snapshot_run:
  run_id: <fls-<8-char-hex> from Step 0 entry banner>
  invoked_by: <caller-skill-name | "user">
  invoked_at: <ISO timestamp from Step 0>
  flow_source: <"live-github-connector @ <sha>" | "live-github-clone @ <sha>" | "snapshot">
  commit_sha: <full SHA, or null if Tier 3>          # freshness signal for live tiers
  cloned_at: <ISO timestamp, or null if Tier 1/3>    # freshness signal for Tier 2
  snapshot_captured_at: <ISO from Notion page Captured: line, or null if live>  # freshness signal for Tier 3
  variant_decision:
    variant: <e.g. webOnboardingV3>
    source_export: <e.g. cOnboardingQuizConfig>
    active_when: <description>
    source_step_count: <int>
    basis: <notion | source-heuristic | no-signal-fallback | user-override>
    basis_detail: <human-readable>
  flow_map: [...]                    # variant-filtered, ~39 screens for V3
  snapshot_drift: { ... }            # only when live source won
  live_verification: { ... }         # only when Step 3 ran
  notion_refreshed: <bool>           # true only when Step 4 ran
```

After the artifact, always print the exit banner (whether standalone or as a sub-call):

```
✅ aurie-web-flow-snapshot complete (run_id: <same id from Step 0>)
   flow_source: <value>
   commit_sha: <sha | "snapshot — Captured: <date>">
   cloned_at: <ISO | n/a>
   variant: <variant>
   flow_map: <N> screens
   elapsed_ms: <int>
```

When invoked standalone (not as a sub-call), also print a final summary:

```
aurie-web-flow-snapshot run <run-id> complete:
  ✅ flow_source: <value>
  ✅ Variant: <variant_decision.variant> (basis: <basis>)
  ✅ flow_map: <N> screens, shipped_features on <K>
  ✅ Snapshot drift: <0 | N changed since <date>>
  ✅ Live verification: <X>/<Y> SSR pages OK (or skipped)
  ✅ Notion refreshed: <yes | no>
```

## Output format

When invoked **as a sub-call** by another skill, return the `flow_snapshot_run` object in memory; the caller prints what's relevant for its own readout.

When invoked **standalone**, print the per-step blocks above (sync outcomes, build summary, drift, verification) followed by the run summary. Do not print the entire `flow_map` — it's hundreds of lines. If the user wants a specific screen, ask which one and print just that screen's YAML.

## Notes and gotchas

- **Credential comes from `gh`, not the repo.** `aiweb-clone.sh` uses `GITHUB_TOKEN` if it happens to be set, and otherwise falls back to `gh auth token` — the `gh` CLI credential is the standard GitHub access path and `GITHUB_TOKEN` is deliberately unset (AINative CLAUDE.md → "GitHub credentials", [ENG-971](https://linear.app/aura-health/issue/ENG-971)). Whichever resolves must have read access to `AuraHealth/AIWeb`. When neither does, the script exits with `TIER2_FALLBACK=no_token` and the skill drops to Tier 3 (Notion snapshot). Inside Cowork neither the host env nor the keyring reaches the sandbox (PROD-1305) — Tier 2 simply degrades; do not paste-export a secret unless the unauthenticated Tier 1/Tier 3 paths can't answer the question.
- **Cache locality across skill calls.** When `aurie-web-onboarding-audit` and `aurie-web-onboarding-experimentation-ideas` both call this skill in the same Cursor session, `/tmp/aiweb` from Tier 2 is reused (`TIER2_OK=cache_hit`). Don't re-clone unnecessarily.
- **R1 self-fetch is intentionally lightweight.** When invoked standalone, the skill fetches `experiment_state` itself (one Notion DB query, narrow regex filter). Callers that already have it should pass it through to avoid duplicate queries.
- **Snapshot drift is informational only.** A non-zero drift count never fails the run. The team decides when to refresh based on cadence + the size of the drift.
- **`refresh-snapshot` writes are atomic per page.** Step 4 replaces the page body in one Notion API call, not block-by-block — partial writes can't leave the page in an inconsistent state.

## References

### Notion knowledge

- **`Aurie Web Signup Flow — Static Snapshot`** — page id `35375e23af2080c581a8ebc7bb8c1af8`. Tier 3 fallback for `flow_map`, also used for drift comparison when live source wins. URL: <https://app.notion.com/p/aurahealth/Aurie-Web-Signup-Flow-Static-Snapshot-35375e23af2080c581a8ebc7bb8c1af8>.
- **`Tools/Mixpanel/Events`** — canonical event lexicon. Used by R6/R7 in `flow-extract.md` to cross-check event names.

### Skill-local

- [`references/flow-extract.md`](references/flow-extract.md) — runtime extraction procedure (R1–R12) + maintainer refresh procedure. Single source of truth for *how* to turn AIWeb source into `flow_map`.
- [`scripts/aiweb-clone.sh`](scripts/aiweb-clone.sh) — Tier 2 sparse-clone helper.

### Sibling skills

- `aurie-web-onboarding-audit` — calls this skill for `flow_map`, then layers Mixpanel + qualitative friction analysis on top.
- `aurie-web-onboarding-experimentation-ideas` — top-level orchestrator; calls audit (which calls this) plus history.

### External

- AIWeb repo: <https://github.com/AuraHealth/AIWeb>
- Live signup URL (verified via curl in Step 3): <https://www.aurie.ai/signup>
