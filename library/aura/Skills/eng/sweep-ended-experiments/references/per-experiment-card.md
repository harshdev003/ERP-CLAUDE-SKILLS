# Remove Experiment — Linear card template (v4)

Used by the `sweep-ended-experiments` skill (ENGMOB-1638) to file one card per ended A/B experiment in a target repo (`AuraConsumerApp`, `Aura`, `AIWeb`, or `UserWeb` — see the skill's Repo profiles tables). Placeholders in `{{...}}` are filled by the skill from the experiment-server API row plus the selected repo's variant-convention signal and paths. The skill verifies the card before filing it.

**Agent-agnostic:** the card content never names a specific executor. It is written so that **anyone — a human engineer or any AI coding agent — can pick it up** from the issue alone. ("The implementer" below means whoever picks up the card.)

**Design intent (v4):** the card is deliberately **lean**. It does **not** enumerate files, line ranges, methods, or exact edits. The implementer owns the investigation — they open the experiment, trace every reference themselves, and decide what to change. The card gives the implementer only what they can't derive on their own: which variant won, what that means, and a fixed checklist of removal obligations. This keeps cards short, avoids stale line numbers, and lets the implementer use their own repo view as the source of truth.

**Web-profile behavior (aiweb/userweb — no template-version bump; placeholder-level only):**
- `{{simtest_knowledge_dir}}` carries the Playwright spec dir (`e2e/` for AIWeb, `tests/` for UserWeb); the same edit/skip rule applies to specs, fixtures, and helpers.
- `{{version_guard}}` is never synthesized for a web card — web users have no joined app version. Every audience-gated web row is flagged for human review, and `{{audience_gate_paragraph}}` / `{{audience_gate_checklist_items}}` take the human-review wording.
- Multi-arm winners: a non-control letter with its own consuming guard is a described variant (treatment win for that arm), not mixed/unclear.

**Changes from v3:**
- Added **Pre-removal verification** subsection: verify metadata against API, update dev Firebase, explicit `targetLocale` review gate, and `preventOldUsers`/`preventNewUsers` joined-version guard handling.
- Added "Remove unused styles" as a dedicated checklist item (was implicit in assets).
- Grouped checklist into **Pre-removal verification / Code removal / Verification** sub-headings to match the Linear workspace template.
- Template version bumped to v4 to stay in sync with the Linear workspace template (also v4).

**Changes from v2:**
- Removed the per-file "Files to modify or delete" section, the `{{files_section}}` / `{{extension_path}}` / `{{method_name}}` placeholders, the step-by-step edit instructions, and the per-card sibling file list. The implementer finds all of this itself.
- Replaced the prose "What to keep, what to remove" decision paragraph with a single **"What the winning variant means"** sentence in the Summary, plus a **fixed removal checklist** that is identical across every card.
- Folded the smart-skip guidance directly into the "update sim-test knowledge" checklist item so the card is self-contained.
- Kept the audience-gate handling (`preventOldUsers` / `preventNewUsers`) — that governs whether the card is marked ready and is correctness-critical, not code detail.

**Changes from v1 → v2 (retained):** dropped the "run validation locally" step (the skill and the implementing environment run in Linux sandboxes with no platform toolchain — CI is the gating signal).

**Repo-aware (multi-repo support):** the paths and language references below are filled per repo from the skill's Repo profiles tables — `{{repo}}` (GitHub repo), `{{catalog_source}}` (where the key is declared/consumed), `{{simtest_knowledge_dir}}` (test-knowledge path — sim-test knowledge on mobile, Playwright spec dir on web), `{{final_grep_path}}` (the dir the final zero-hits grep runs against), and `{{surface_display}}` (the metadata "Surface" value). The checklist itself is identical across repos.

---

## Title

`Remove experiment: {{key}} — variant {{winner}} rolled out ({{repo}})`

## Body

### Summary

Remove the **`{{key}}`** experiment from `{{repo}}`. It ended on **{{ended_at}}** with **variant `{{winner}}`** as the rolled-out version. {{result_blurb}}

Now that the experiment has ended, the experiment-assignment service will always return variant **`{{winner}}`** for this key. {{winner_meaning}}

Treat this as making the codebase behave **as if the experiment never existed**. **The implementer owns the investigation** — open the experiment, trace its references across the app, and decide what to change. This card intentionally does **not** list files or exact edits; find them yourself and satisfy the checklist below. The experiment key lives in `{{catalog_source}}`; start there and follow the references out.

{{audience_gate_paragraph}}

### Experiment metadata

| Field | Value |
|---|---|
| Key | `{{key}}` |
| Surface | {{surface_display}} |
| Ended at | `{{ended_at}}` |
| Winning variant (`active`) | `{{winner}}` |
| Variants originally tested | `{{original_variants}}` |
| Min version | `{{min_version}}` |
| Max version | `{{max_version}}` |
| Audience — `preventOldUsers` | `{{prevent_old_users}}` |
| Audience — `preventNewUsers` | `{{prevent_new_users}}` |
| Server notes | {{notes_or_dash}} |
| Server result (free-text) | {{result_or_dash}} |
| Source | `GET https://services.aurahealth.io/experiments` (row id `{{key}}`) |

### Removal checklist

Complete every applicable item before marking the PR ready for review:

**Pre-removal verification**

- [ ] Verify the metadata table above against the live API row — confirm `endedAt` is set, `active` matches the winning variant, and note `targetLocales`, `minVersion`, `maxVersion`, `preventOldUsers`, `preventNewUsers`.
- [ ] Update dev Firebase: if `/experiments/{{key}}` exists in the dev Firebase console, set its value to `{{winner}}` (the winning variant) so the dev environment matches the baked-in behavior.
- [ ] If `targetLocales` is set in the metadata → **stop and discuss with Rohan** before removing any code.
- [ ] If both `preventOldUsers` and `preventNewUsers` are set, stop and discuss with Rohan before removing any code; the server row has no eligible audience. If the resolved shape is Control won, no version guard is needed. If exactly one audience flag is set and the resolved shape is Treatment won, keep that audience boundary while removing the experiment key using the concrete joined-version predicate in the audience checklist below; if that treatment-won path is missing the required version bound, stop and discuss with Rohan before removing any code.

**Code removal**

- [ ] Remove the experiment assignment, its variant conditions, and all dead-code branches. Bake in variant `{{winner}}` using the scope selected in the Summary and Audience note: normal path for non-audience-gated treatment wins, guarded path for audience-gated treatment wins, and no behavior rewrite for control wins.
- [ ] **Keep the winning path's implementation.** When baking in the rolled-out variant, retain the code that path actually uses — its views, request/response models, and supporting infrastructure. Only the experiment scaffolding (assignment, variant check, gating flag) and the now-dead other branches come out; don't delete the behavior that is now permanent.
- [ ] **Watch for a fallback / graceful-degradation path.** If the winning variant falls back to other behavior on failure (network error, empty data, etc.), that fallback may be shared with — or governed by — a **separate, still-running experiment**. Do **not** delete it or touch the experiment that owns it just because it looks like leftover control code. Confirm what owns the fallback before removing anything.
- [ ] Remove any assets/images used by the experiment code that are no longer referenced anywhere else.
- [ ] Remove unused styles only referenced by removed branches.
- [ ] Remove unused views and logic related to the experiment that are no longer used anywhere else — including view models, extensions, and services that were added for the experiment and have no other consumers.
- [ ] Remove any analytics specific to the experiment that are no longer used.
- [ ] Remove any API keys or secrets specific to the experiment.

**Verification**

- [ ] Update test knowledge (`{{simtest_knowledge_dir}}` — sim-test knowledge on mobile, Playwright specs on web) as needed. **Edit** references that frame the experiment as a live A/B variant ("(treatment variant)", "control arm", "variant a/b", "A/B"). **Skip** (leave untouched): (1) screen / agent / feature names that merely share a word with the experiment and stay accurate after removal; (2) historical audit-log entries in JSON files such as `simulators.json` and `users.json` — these are immutable records of past test runs and must not be rewritten; (3) references inside files you are deleting entirely. Rule of thumb: edit only when the text describes the experiment as a *live variant*; if the sentence is still true once the experiment is gone, leave it.
- [ ] Update test cases as needed.
- [ ] Do **not** add comments about the removed experiment — the new code should read as if the experiment never existed.
- [ ] Be careful about behavior with other live experiments. If another experiment depends on or shares code paths with this one, verify its functionality does **not** change.
- [ ] Make only changes related to this ended experiment — no unrelated edits.
- [ ] Remove every reference to `{{key}}` (in `{{repo}}`: the catalog/enum entry where applicable, plus all consuming guards), then confirm `git grep "{{key}}" {{final_grep_path}}` returns zero hits before opening the PR.
{{audience_gate_checklist_items}}

### Implementation process

Follow this sequence in order — do not skip steps or reorder them:

1. **Plan** — before touching any file, trace all references to `{{key}}` in `{{final_grep_path}}`. Understand the removal shape (treatment-won vs control-won), list which files change, and confirm the baked-in behavior. Post a short plan as a comment on this card before writing any code.

2. **Implement** — make the changes per the removal checklist above. One experiment per PR; no unrelated edits.

3. **Open the PR**
   - Title: `Remove experiment: {{key}}{{audience_title_tag}} ({{winner}} won)`
   - Description: link this Linear card and the parent batch card. Include an **Experiment result** section sourced from the live API row:
     - API status: ended (`endedAt`: `{{ended_at}}`)
     - API active winner: `{{winner}}` rolled out
     - API result note: `{{result_or_dash}}` when present
     - Variant convention used for cleanup: `a = treatment`, `b/nil = control` unless the consuming code proves a different convention

4. **Bugbot + adversarial review** — run `eng:bugbot-review` on the PR, then spawn **one** `code-reviewer` agent with this prompt:

   > Review this experiment-removal PR. Adversarial check only — three questions:
   > 1. Does `git grep <key> {{final_grep_path}}` return zero hits?
   > 2. Does any sibling experiment share a code path with the removed branch?
   > 3. Does removing this expand behavior to an audience that was previously excluded?
   > Report pass/fail per question. No style comments.

   Address every finding from both passes.

   > ⚠️ **Known issue:** `eng:bugbot-review` polling can hang indefinitely if the completion signal is missed. If the review has not returned after ~5 minutes, re-run the skill rather than waiting. Do not assume silence means success.

5. **Address comments** — reply on every open thread with the fixing commit SHA and a one-line explanation before requesting human review. Never leave a thread dangling.

6. **Mark PR ready for review** — only after: all bugbot + adversarial findings are addressed, CI is green, and all comment threads are resolved. In the handoff after each PR becomes ready, include a **Ready for review** list of every open experiment-cleanup PR that still needs human review, with PR link and experiment key.

### Skill provenance

| Field | Value |
|---|---|
| Generated by | `sweep-ended-experiments` skill (ENGMOB-1638) |
| Target repo | `{{repo}}` |
| Parent batch card | `Clean up inactive experiments — {{repo}} [{{cycle_or_date}}]` |
| API source row | `id: {{key}}`, `endedAt: {{ended_at}}`, `active: {{winner}}` |
| Template version | v4 (lean — checklist-driven, implementer owns investigation; pre-removal verification section added) |

---

## Skill-side notes (not part of the Linear card body)

How the skill fills the placeholders.

| Placeholder | Source |
|---|---|
| `{{repo}}` | Selected repo profile — `AuraConsumerApp`, `Aura`, `AIWeb`, or `UserWeb` |
| `{{catalog_source}}` | Repo profile — `AuraConsumerApp/Services/Experiments/Models/Experiments.swift` (auraconsumerapp); `src/stores/ExperimentsStore.js` + the `experiments.<key>` guards in `src/` (aura); `AB_EXPERIMENTS` in `src/lib/constants.ts` + the consuming guards in `src/` (aiweb); the per-page choose lists + `experiments.<key>` guards in `src/` (userweb) |
| `{{simtest_knowledge_dir}}` | Repo profile — `AuraConsumerAppSimTests/knowledge/` (auraconsumerapp), `AuraSimTests/knowledge/` (aura), `e2e/` Playwright specs (aiweb), `tests/` Playwright specs (userweb) |
| `{{final_grep_path}}` | Repo profile — `AuraConsumerApp/` (auraconsumerapp); `src/ app.js` (aura — the repo-root `app.js` holds a fetch array too); `src/` (aiweb, userweb) |
| `{{surface_display}}` | `` `Experiments.{{surface}}.{{key}}` `` for auraconsumerapp; `` `{{key}}` `` (Firebase experiments node) for aura and the web repos, with the page-list surface appended for aiweb when Step 1 mapped one |
| `{{key}}` | API row `id` |
| `{{surface}}` | auraconsumerapp: the Swift `enum` group inside `Experiments.swift` that holds the key. aiweb: the page/hook experiment list that references the key (payment, quiz, get-app, signup/landing, webview paywall), else `—`. aura / userweb: `—` (no grouping) |
| `{{ended_at}}` | API row `endedAt` (ISO date) |
| `{{winner}}` | API row `active` (single letter for ended experiments) |
| `{{min_version}}` | API row `minVersion`, or `—` if blank |
| `{{max_version}}` | API row `maxVersion`, or `—` if blank |
| `{{original_variants}}` | auraconsumerapp: from the enum doc comment in `Experiments.swift`. aura: from the consuming guard `experiments?.<key> === 'a'`. aiweb/userweb: from the consuming guards in `src/`, collecting every letter checked (multi-arm experiments occur on web). All follow `a = treatment, b/nil = control` |
| `{{notes_or_dash}}` | API row `notes`, or `—` if blank |
| `{{result_or_dash}}` | API row `result`, or `—` if blank |
| `{{result_blurb}}` | One-sentence summary of the readout when `result` is non-empty (e.g. "The readout showed a strong win for `a` — Trial Starts +54%, N=247."). Empty string if `result` is blank. |
| `{{winner_meaning}}` | One sentence describing what the win means, selected by shape plus audience gate: **non-audience-gated treatment won** → "Variant `{{winner}}` introduced new behavior that is now the normal path; the control and any other branches are dead and should be removed." **audience-gated treatment won** → "Variant `{{winner}}` introduced new behavior that is now permanent only for users matching `{{version_guard}}`; the control and any other branches are dead for that eligible audience and should be removed." **control won** → "Variant `{{winner}}` is the original default behavior, so the experiment's treatment branch(es) never shipped — delete the experiment scaffolding outright."  **mixed/unclear** → the card is flagged for human review, not marked ready (see `decision-shapes.md`). |
| `{{prevent_old_users}}` | API row `preventOldUsers` (`true` / `false` / `null`), rendered as-is |
| `{{prevent_new_users}}` | API row `preventNewUsers` (`true` / `false` / `null`), rendered as-is |
| `{{audience_gate_paragraph}}` | Empty when neither `preventOldUsers == true` nor `preventNewUsers == true`. When audience-gated, select the addendum from the resolved shape in `references/decision-shapes.md`: control won gets the "safe to remove" note; Shape A treatment won with the required bound gets a guarded-removal note with the concrete `{{version_guard}}`; mixed/unclear, both flags true, or missing bound gets a human-review warning and no synthesized guard. **Web profiles:** always the human-review warning — the audience boundary needs a human-decided web-side predicate (web has no joined app version). |
| `{{audience_gate_checklist_items}}` | Empty when not audience-gated. Shape B control-won audience-gated: `- [ ] Verified no behavior change for {{excludedAudience}} (control variant won; {{audienceField}} == true was in effect)`. Shape A treatment-won audience-gated with the required bound: `- [ ] Preserve the audience boundary after removing the experiment: keep the winning behavior behind {{version_guard}}` and `- [ ] Include {{audience_title_tag}} in the PR title`. Mixed/unclear, missing version bound, or both flags true: reviewer-decision item naming the blocker, with no joined-version predicate invented by the skill. |
| `{{audience_title_tag}}` | Empty when not audience-gated. `[preventOldUsers]` when `preventOldUsers == true`, `[preventNewUsers]` when `preventNewUsers == true`, or both tags if both flags are set. Used in the PR title only. |
| `{{version_guard}}` | Empty except for audience-gated treatment wins on a **mobile** profile. Render with the concrete API value: `joinedVersion >= {{min_version}}` when `preventOldUsers == true`; `joinedVersion <= {{max_version}}` when `preventNewUsers == true`. Always empty on web profiles — never synthesize a joined-version guard for a web card. |
| `{{cycle_or_date}}` | The parent batch card's cycle name (e.g. `Cycle 28`), or the ISO run date if there's no active cycle |

### Readiness checks the skill runs before a card is marked ready

1. `endedAt` is non-null.
2. `active` is exactly one letter and matches one of the variants the convention describes.
3. A `git grep` for the key returns ≥ 1 hit in `{{final_grep_path}}` (else there's nothing to remove — no card needed).
4. No `{{...}}` placeholders remain in the filled body.
5. The shape decision is one of the three known shapes (treatment / control / mixed).
6. **Audience gate.** On a web profile (`aiweb`, `userweb`), any row with either audience flag true fails this check outright — filed but flagged for human review, no joined-version guard synthesized. For the mobile profiles: if `preventOldUsers == true` OR `preventNewUsers == true`, first reject the invalid state where both flags are true. Otherwise, the gate passes when either the resolved shape is Control won or the resolved shape is Treatment won and the required version bound exists for guarded treatment removal (`minVersion` for `preventOldUsers`, `maxVersion` for `preventNewUsers`). Every ready audience-gated card, including control wins, must render the audience tag (`[preventOldUsers]` or `[preventNewUsers]`) in the PR title instruction. Audience-gated treatment wins are ready only when the rendered body also includes the exact joined-version predicate with the API value substituted (for example, `joinedVersion >= 4.1.0`, not `joinedVersion >= minVersion`) in the summary/audience note and checklist; otherwise the card is flagged for human review.

If any check fails, the card is still filed but flagged for human review (not marked ready) and stays assigned to the operator until a human resolves the approach.

### What the skill no longer does

The skill does **not** capture method names, enumerate touched files, or compile a per-card sibling file list. That investigation is the implementer's job, driven by the fixed checklist. The skill's only code-side action is the light `git grep` count in check #3 above (presence + idempotency), plus reading the variant-convention signal (`Experiments.swift` doc comment for auraconsumerapp; the consuming `experiments.<key> === 'a'`-style guards for aura, aiweb, and userweb) to determine which variant is treatment.
