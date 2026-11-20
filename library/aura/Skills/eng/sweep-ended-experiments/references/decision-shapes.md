# Decision shapes — treatment-won / control-won / mixed-flagged

For each ended experiment the skill picks exactly one of three shapes. The shape decides two things:

1. Whether the card is **marked ready** (safe to pick up and implement) or **flagged for human review**.
2. The one-sentence `{{winner_meaning}}` in the card Summary.

The removal **checklist itself is identical across all cards** — the shape never expands into per-file edit instructions (the implementer does that). This file is the canonical source for the shape decision, the `{{winner_meaning}}` wording, and the audience-gate paragraphs that fill `{{audience_gate_paragraph}}` / `{{audience_gate_checklist_items}}`.

The picker reads two things:

1. The API row's `active` letter — a single letter for ended experiments: the variant that was rolled out.
2. The variant convention for the selected repo — typically `"a" = treatment, "b" or nil = control`. For `auraconsumerapp` this comes from the enum doc comment in `Experiments.swift`; for `aura` it comes from the consuming guard `experiments?.<key> === 'a'` in `src/`; for the web repos (`aiweb`, `userweb`) it likewise comes from the consuming guards in `src/` (`experiments.<key> === 'a'`, or `experiments.chosen?.[AB_EXPERIMENTS.<KEY>] === 'a'` in AIWeb). (See "Which letter is treatment" below for the fallback when the signal is missing or disagrees with the code.)

## Shape A — Treatment won

`active` matches the variant the doc comment calls treatment (typically `active = "a"`).

- **`{{winner_meaning}}`:** resolve this after the audience gate, not from Shape A alone. If the experiment is audience-gated, the audience-gate wording is mandatory and the generic non-audience treatment sentence is forbidden.
- **Non-audience-gated treatment wording:** "Variant `{{winner}}` introduced new behavior that is now the normal path; the control and any other branches are dead and should be removed."
- **Audience-gated treatment wording:** "Variant `{{winner}}` introduced new behavior that is now permanent only for users matching `{{version_guard}}`; the control and any other branches are dead for that eligible audience and should be removed."
- **Keep the winning path.** Retain the code that path actually uses — its views, request/response models, and supporting infrastructure. For non-audience-gated experiments, inline treatment as the normal path. For audience-gated experiments, preserve the original audience boundary with the joined-version guard described below. Only the scaffolding (assignment, variant check, gating flag) and the dead branches for the relevant audience come out. (Surfaced as a card checklist item.)
- **Watch for a fallback / graceful-degradation path.** If the treatment falls back to other behavior on failure (network error, empty data), that fallback may be shared with — or governed by — a **separate, still-running experiment**. Do not delete it or touch the owning experiment. Example from the ENGMOB-1662 validation run: the prompt-results path fell through to archetype/quadrant on network failure, governed by the still-running `onboardingResultsArchetype` experiment, which had to stay untouched. (Surfaced as a card checklist item.)
- **Marked ready:** yes (subject to the audience gate below).

## Shape B — Control won

`active` matches the control variant (typically `active = "b"` / nil).

- **`{{winner_meaning}}`:** "Variant `{{winner}}` is the original default behavior, so the experiment's treatment branch(es) never shipped — delete the experiment scaffolding outright."
- No UI logic changes are required. Remove the assignment call, the variant check, any `@Published` flag the experiment drove, and the `+Experiment.swift` extension if it only handled this experiment. The default already matches control, so nothing needs to be inlined or rewritten.
- **Marked ready:** yes (subject to the audience gate below).

## Shape C — Mixed / unclear

`active` is empty, multi-letter, or doesn't match either variant the doc comment describes. Examples:

- `endedAt != null` but `active` is `""` (empty string)
- `active = "ab"` (two letters — experiment paused without resolution)
- `active = "c"` but the doc comment only describes `a` / `b`

**Multi-arm caveat (web).** Web experiments are occasionally more than two arms — e.g. an AIWeb key with both `=== 'a'` and `=== 'c'` consuming guards. A single-letter winner that has its own consuming guard is a *described* variant, not mixed: a non-control letter with a guard resolves to Shape A for that arm (bake in that arm's branch, delete the others). Only a letter no guard describes makes the row Shape C.

- **`{{winner_meaning}}`:** the summary states the winner is ambiguous and a human must decide whether to bake in a specific variant, delete scaffolding outright, or hold until the server state resolves.
- **Marked ready:** no — flagged for human review. The card body says: *do NOT pick this up or mark it ready until a human has resolved the winner.* It stays assigned to the skill operator.

## Audience gate — `preventOldUsers` / `preventNewUsers`

After the shape decision (A / B / C), the skill applies an **audience gate** that fires when **either** of the two server-side audience fields is true:

- `preventOldUsers == true` — experiment restricted to **new users only**; existing users are never bucketed and continue on the pre-experiment default.
- `preventNewUsers == true` — experiment restricted to **existing users only**; new users are never bucketed and continue on the pre-experiment default.

The semantics are symmetric, but the version bound differs by flag. Do not key guarded cleanup off the raw `active` letter alone: use the resolved shape first, then apply a joined-version guard only for Shape A treatment wins with a valid bound.

Define:

- `audienceGated = (preventOldUsers == true) || (preventNewUsers == true)`
- `excludedAudience = "existing users"` when `preventOldUsers == true`, else `"new users"` when `preventNewUsers == true`
- `bucketedAudience` is the opposite of `excludedAudience`
- `audienceField` is the literal name of the flag that was set (`"preventOldUsers"` or `"preventNewUsers"`)
- `versionGuard` is rendered with the concrete API value: `joinedVersion >= {{min_version}}` when `preventOldUsers == true`, or `joinedVersion <= {{max_version}}` when `preventNewUsers == true`

**Web override — the gate always flags on web profiles.** The joined-version guard is an app-version concept; web users have no joined app version, so the boundary-preserving guard cannot be expressed in web code. For `repo=aiweb` and `repo=userweb`, every `audienceGated` row — all shapes, including Shape B control wins — is filed but flagged for human review, and `versionGuard` is never synthesized. `{{audience_gate_paragraph}}` takes a human-review wording: the audience boundary needs a human-decided web-side predicate, or confirmation that the audience flag never applied to the web surface. The three cases below apply to the mobile profiles only.

The gate has three cases.

### Gate passes — card is marked ready

Exactly one audience flag is true **and** the resolved shape is Shape B (Control won). If both audience flags are true, the invalid-state rule below wins and the card is flagged for human review even when control won.

The winning letter is the codebase's control variant (usually `b`, unless the runtime convention proves otherwise). When control wins on an audience-gated experiment, the rollout is "no change from default" — the excluded audience was on the default the whole time, and the bucketed audience landed on control which is also the default. Deleting the scaffolding leaves every user (new + old) on the same behavior they already had. Safe.

`{{audience_gate_paragraph}}` (appended to the Summary):

> **Audience note.** This experiment was gated to `{{bucketedAudience}}` only (`{{audienceField}} = true`, excluding `{{excludedAudience}}`). Since the control variant won, the rollout is "no UI change," and `{{excludedAudience}}` were on the default behavior throughout. Removing the scaffolding does not change anyone's experience.

### Treatment won — remove the experiment with a version guard

Exactly one audience flag is true **and** the resolved shape is Shape A (**Treatment won**). If both audience flags are true, the invalid-state rule wins and no joined-version guard is synthesized.

Baking in a treatment branch the server only ever showed to the bucketed audience would expand that behavior to the excluded audience that was never bucketed into it. Preserve the rollout boundary as a normal version condition while removing the experiment key:

- `preventOldUsers == true` → keep the winning feature behind `joinedVersion >= {{min_version}}`, substituting the API row's concrete `minVersion`. The experiment was new-users-only, so users whose joined version predates the experiment stay on the old behavior.
- `preventNewUsers == true` → keep the winning feature behind `joinedVersion <= {{max_version}}`, substituting the API row's concrete `maxVersion`. The experiment was existing-users-only, so users whose joined version is newer than the experiment stay on the old behavior.

The card is marked ready when exactly one audience flag is true, the required version bound exists in the API row, and the rendered card includes the concrete joined-version guard plus audience title tag. If the required bound is missing (`minVersion` for `preventOldUsers`, `maxVersion` for `preventNewUsers`), flag for human review because the implementer cannot preserve the original audience boundary. Shape C (mixed / unclear) and both-flags rows never take this guarded path; they stay flagged for human review until a human resolves the winner.

`{{audience_gate_paragraph}}` (appended to the Summary):

> **Audience note.** This experiment was gated to `{{bucketedAudience}}` only (`{{audienceField}} = true`, excluding `{{excludedAudience}}`) and won with variant `{{winner}}`. Remove the experiment key, but keep the winning behavior behind `{{version_guard}}` so the previously-excluded audience stays on the original behavior.

`{{audience_gate_checklist_items}}`:

> - [ ] Preserve the audience boundary after removing the experiment: keep the winning behavior behind `{{version_guard}}`.
> - [ ] Include `{{audience_title_tag}}` in the PR title.

### Gate fails — missing version bound or invalid server state

Audience-gated treatment-won cards are flagged for human review when the API row lacks the version bound needed to preserve the original audience:

- `preventOldUsers == true` and `minVersion` is missing.
- `preventNewUsers == true` and `maxVersion` is missing.
- Both audience flags are true.

The summary should say which field is missing or inconsistent and that the removal is blocked until a human decides the replacement audience predicate.

### Gate is a no-op

Neither `preventOldUsers == true` nor `preventNewUsers == true` (both are `null`, `false`, or absent). The audience gate does not apply; the shape decision (A / B / C) stands on its own.

### Both flags true — invalid server state

If both `preventOldUsers == true` and `preventNewUsers == true`, the experiment was restricted to neither old nor new users — which is nobody. Treat as `audienceGated == true`, surface the inconsistency in the summary (*"Server state inconsistency — both `preventOldUsers` and `preventNewUsers` are set; the experiment had no eligible audience. Hold for human review regardless of the winner."*), and do not mark ready.

## Which letter is "treatment" — heuristic

The convention is the same in all repos (`"a" = treatment, "b" or nil = control`); only the signal differs.

**`auraconsumerapp`:** the enum doc comment in `Experiments.swift` is the primary signal. If it's missing or ambiguous, confirm from the consuming `+Experiment.swift` check:

```swift
let result = assignments[Experiments.<Surface>.<key>]?.result
return result == "a"          // → "a" is treatment, "b"/nil is control
```

**`aura`:** there is no doc comment — the consuming guard in `src/` is the primary signal:

```js
if (experiments?.<key> === 'a') { /* treatment path */ }   // → "a" is treatment, "b"/nil is control
```

**`aiweb` / `userweb`:** no doc comment either — the consuming guards in `src/` are the signal, and there may be more than one (multi-arm):

```js
const showX = experiments.<key> === 'a';                          // both web repos
const showY = experiments.chosen?.[AB_EXPERIMENTS.<KEY>] === 'a'; // AIWeb catalog form
const isAltArm = experiments.<key> === 'c';                       // extra arm — 'c' is a described variant
```

Collect every letter the guards check; each is a described variant for the Shape C decision.

In all repos, the letter on the right of `==` / `===` is the treatment variant. If a file uses `=== 'b'` as the treatment check, `b` is treatment. If the doc comment (auraconsumerapp) and the code check disagree, the **code check is the runtime truth** — note the inconsistency on the card for the human reviewer.
