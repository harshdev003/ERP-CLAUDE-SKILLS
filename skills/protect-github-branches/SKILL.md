---
name: protect-github-branches
description: >
  Audit and apply GitHub branch protection across a repo, a list of repos, or a whole org —
  checking the SHAPE of protection, not its presence. Reports which default branches
  block force-push and deletion with no bypass actors, applies the standard ruleset, migrates
  classic protection to rulesets losslessly, and turns on "Automatically delete head
  branches". Trigger on "protect the branches", "audit branch protection", "are our branches
  protected", "check branch protection", "can someone force-push to main", "apply branch
  protection", "set up branch rulesets", "block force pushes", "require a PR to merge",
  "require CI to pass before merging", "add required status checks", "make CI green
  mandatory", "migrate to rulesets", "why was main force-pushed if it was protected", "turn on
  auto-delete head branches", or "stacked PRs keep targeting a merged branch". Also trigger
  after a force-push incident to confirm the fix held. Do NOT use to review a PR
  (`review-pr`) or clean up worktrees (`clean-up-worktrees`).

metadata:
  version: "3.1.1"
---

# Protect GitHub branches

## Goal

Answer "can someone force-push our default branches?" with evidence, then close the gap.

The trap this skill exists for: **a repo can report protection and still permit
force-push.** Five such configurations were found live in one org that believed it was
protected — including a ruleset that was active but targeted no branches and carried no
rules. Counting rulesets tells you nothing. See
[`references/failure-modes.md`](references/failure-modes.md).

## Inputs

- **scope** — a GitHub owner (`harshdev003`) or a single repo (`harshdev003/ERP-BACKEND-SERVICE`)
- **mode** — `audit` (default) · `preflight` · `apply` · `migrate`
- optional: `--approvals N` (default 0), `--days N` (preflight window, default 90),
  `--require-check CONTEXT` (repeatable), `--strict-checks`, `--check-sample N` (default 10),
  `--no-coverage-gate`, `--no-pull-request`, `--exclude a,b`, `--include-cert-repos`,
  `--dry-run`, `--json`

## Workflow

### 1. Audit first — always

```bash
python3 $HOME/.claude/skills/protect-github-branches/scripts/protect.py audit <org-or-repo>
```

Emits a per-repo table (repo, mechanism, enforcement, bypass count, checks, `autodel`,
verdict, detail) and a `N/M fully protected` summary.

`autodel` is the repo setting **"Automatically delete head branches"**
(`delete_branch_on_merge`): `on`, `OFF`, or `?` when GitHub does not return it (the caller
lacks admin on that repo). It is not part of the protection verdict, but it is reported
next to it because `apply` sets it. With it off, merged branches pile up (one repo held
111), and a stacked PR keeps targeting its merged base instead of being retargeted onto the
default branch, so a chain cannot be merged one PR after another. The org repo listing
omits the field, so the audit makes one extra `GET repos/{owner}/{repo}` per repo.

**A run with a non-zero error count is INCOMPLETE — never read a zero-gap result from
it.** The script fails closed: any non-200 becomes an error rather than an empty result.
This matters because GitHub's secondary rate limiter returns 403 while `/rate_limit`
still reports ~5000 core remaining, and a helper that maps a failed fetch onto the same
value as "nothing found" reports clean.

Report the verdict split, then name every `WEAK` repo with its `detail` — the detail is
the actionable part (`missing rule(s): ['non_fast_forward']` is a very different problem
from `2 bypass actor(s)`).

### 2. Pre-flight before changing anything

```bash
python3 $HOME/.claude/skills/protect-github-branches/scripts/protect.py preflight <org-or-repo>
```

Reports, per repo, 90 days of force-pushes and direct pushes to the default branch plus
the actors, and flags **blockers**.

**Present the blockers to the user and get confirmation before applying.** Two matter:

- **Automation that cannot open a PR.** fastlane `match` certificate repos push directly
  and non-interactively; a `pull_request` rule breaks iOS code signing at the next
  certificate rotation — months later, far from this change. `apply` and `migrate`
  auto-exclude repos matching `cert|fastlane|match`; `--include-cert-repos` overrides,
  and should only be used once you have verified nothing automated pushes there.
- **A repo with real force-push history.** Blocking it removes a capability someone uses.
  Surface who and when; let the user decide.

### 3. Apply

```bash
# see the plan first
python3 $HOME/.claude/skills/protect-github-branches/scripts/protect.py apply <org> --dry-run
# then for real
python3 $HOME/.claude/skills/protect-github-branches/scripts/protect.py apply <org>
```

Applies the standard shape:

```json
{"name":"main","target":"branch","enforcement":"active",
 "conditions":{"ref_name":{"include":["~DEFAULT_BRANCH"],"exclude":[]}},
 "rules":[{"type":"deletion"},{"type":"non_fast_forward"},
          {"type":"pull_request","parameters":{"required_approving_review_count":0}}],
 "bypass_actors":[]}
```

`required_approving_review_count: 0` forces a PR — a diff, a record, a CI hook — without
requiring a second human. Raise it with `--approvals 1` where review capacity allows.
`bypass_actors: []` means admins are bound too; that is the point, since an outside account
operating with admin rights is the scenario.

`apply` also sets `delete_branch_on_merge: true` (`PATCH repos/{owner}/{repo}`) on every
repo it touches, read back from the response before being counted as `enabled`. It is
independent of the ruleset: a repo skipped on coverage still gets the setting.
`--keep-head-branches` leaves it as it is.

**Existing rules are preserved verbatim and appended to. The script never downgrades** —
if a `pull_request` rule already exists it is left untouched rather than overwritten with
a lower approval count. Every write is read back before being counted as applied.

#### Requiring CI to be green

A `pull_request` rule forces the diff through CI; it does not make the result matter. Until
a `required_status_checks` rule names a context, a red run still merges. Add one with
`--require-check`, repeatable:

```bash
python3 $HOME/.claude/skills/protect-github-branches/scripts/protect.py \
  apply <org> --require-check "ci/circleci: test" --dry-run
```

```json
{"type":"required_status_checks",
 "parameters":{"required_status_checks":[{"context":"ci/circleci: test"}],
               "strict_required_status_checks_policy":false}}
```

`--strict-checks` additionally requires the branch be up to date with base, forcing a rebase
every time base moves — across an org that is substantial churn for little added safety, so
it is off unless asked for. A context is matched against both commit statuses and check-run
names.

Contexts are **merged, never replaced**: adding one to a repo that already requires another
unions the two, and `strict` only ever goes off→on. Raising the bar is allowed; lowering it
is the downgrade the tool refuses.

**The coverage gate is what makes this safe to run org-wide.** Before installing a context,
`apply` samples the most recently *merged* PRs (`--check-sample`, default 10) and requires
the context to have reported on every one. A repo where it never reports is skipped with
`action: skip-coverage` and listed in the output rather than silently passed over — see the
edge case below for why an unreported context is worse than a failing one. `--no-coverage-gate`
overrides, and needs a reason.

### 4. Migrate classic protection

```bash
python3 $HOME/.claude/skills/protect-github-branches/scripts/protect.py migrate <org> --dry-run
```

Creates the ruleset **first**, verifies it, and only then deletes classic protection. Both
apply briefly, which is strictly stronger — there is never a window where neither does,
and a failed verify leaves classic in place. Required reviews, status checks and linear
history are translated faithfully; the mapping is in
[`references/failure-modes.md`](references/failure-modes.md).

### 5. Re-audit and report

Re-run `audit` and report the before/after. State explicitly what was **not** done —
excluded repos and why, repos that errored, and repos GitHub refuses (a locked fork
returns `403 Repository has been locked`).

## Output

```
mode=audit  scope=harshdev003  checked=101

repo                        mech      enf       bypass  checks  autodel  verdict     detail
  ERP-WEB-SERVICE           ruleset   disabled  0       0       OFF      WEAK        enforcement=disabled
  ERP-BACKEND-SERVICE       ruleset   active    0       0       on       WEAK        missing rule(s): ['non_fast_forward']
  ERP-APP-SERVICE           ruleset   active    1       1       OFF      WEAK        1 bypass actor(s)
  ...
  96/101 fully protected
  5/101 have no required status check — a red CI run does not block merge there
  60/101 keep merged head branches (delete_branch_on_merge off) — `apply` turns it on

  SKIPPED (4):
    ERP-IOS-CERTIFICATES  cert/fastlane repo — fastlane cannot open a PR
```

Follow the table with the gap list, the skipped list with reasons, and — if the error
count is non-zero — a plain statement that the run is incomplete.

## Edge cases

- **A `--jq` parse error empties stdout.** `gh api` exits non-zero, but the common
  wrapper shape `stdout if returncode == 0 else ""` collapses it to an empty string —
  indistinguishable from a legitimate "no matches". A sweep once reported "0 infected
  across 35 repos" this way. jq string literals need `\\.` for a literal dot, not `\.`.
  `scripts/gh_api.py` raises instead of returning empty.
- **Measure exit codes without a pipeline.** `cmd | head; echo $?` reports *head's*
  status, not `cmd`'s — use `${PIPESTATUS[0]}`. This is how the above was originally
  misdiagnosed as "gh exits 0".
- **`GITHUB_TOKEN` shadows the gh keyring credential.** All calls strip `GITHUB_TOKEN`
  and `GH_TOKEN`; a 403 on a repo you can clearly read is a credential fault, not a verdict.
- **Locked forks.** `403 Repository has been locked` on ruleset creation. Report, move on.
- **Empty repos** have no default branch and are skipped.
- **Idempotent.** Re-running `apply` on a conforming repo is a no-op (`action: skip`).
  "Conforming" includes the `pull_request` rule when one is being installed — a repo that
  blocks force-push but has no PR rule is *not* skipped.
- **`/rulesets` is unordered and includes inherited org rulesets.** The audit evaluates
  *every* ruleset that covers the default branch, not the first one returned; `apply` and
  `migrate` only ever mutate a `Repository`-source ruleset.
- **A ruleset that targets only tags or a release branch protects nothing here.** The shape
  check requires the ref condition to actually reach the default branch (`~DEFAULT_BRANCH`,
  `~ALL`, or `refs/heads/<default>`), and honours `exclude`.
- **A glob ref pattern yields `UNCERTAIN`, never a guess.** The skill deliberately does not
  reimplement GitHub's glob grammar — see `references/failure-modes.md`. `UNCERTAIN` counts as
  unprotected and is surfaced for a human, which is the fail-closed direction.
- **Cert repos can still get partial protection.** `--no-pull-request` applies
  `deletion` + `non_fast_forward` only, which stops accidental branch loss without
  blocking fastlane's direct push.
- **An unreported required context blocks the PR forever.** A context that reports
  `failure` blocks the merge, which is the point. A context that never reports at all sits
  at *"Expected — waiting for status to be reported"*, and GitHub applies no timeout — with
  `bypass_actors: []` not even an admin can merge past it, so the only escape is editing the
  ruleset back. This is why `--require-check` is gated on observed coverage rather than
  taken on trust.
- **Sort merged PRs by `merged_at`, never `updated`.** The pulls endpoint offers
  `sort=created|updated` only. On closed PRs, `updated` ranks by last comment, so the sample
  drifts to old PRs and a repo that adopted the check recently reads as uncovered — one repo
  measured 1/10 that way when it was 10/10. `merged_prs()` fetches a wide page and orders by
  `merged_at` locally.
- **`branches/{branch}/protection` is blind to rulesets.** It returns
  `404 Branch not protected` for a branch protected by a ruleset, because it only reports
  *classic* protection. Reading posture from it scored a 38/39-enforced org as 32/39
  unprotected. `inspect()` reads rulesets first and falls back to classic; anything working
  outside this script should use `GET /repos/{o}/{r}/rules/branches/{b}`, the effective-rules
  endpoint, which resolves both.
- **Refs, not settings.** If the job needs refs (post-incident sweeps), use
  `git ls-remote` — no REST quota, and REST `/branches` cannot see `refs/pull`.

## References

- [`references/failure-modes.md`](references/failure-modes.md) — the five shapes that pass
  a presence check, the audit predicate, the classic→ruleset mapping
