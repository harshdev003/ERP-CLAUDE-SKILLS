#!/usr/bin/env python3
"""Audit and apply GitHub branch protection — shape-aware, fail-closed.

Modes
  audit      read-only posture report (default), incl. "Automatically delete
             head branches" (delete_branch_on_merge)
  preflight  what would break if `apply` ran
  apply      create/update rulesets to the standard shape, and turn
             delete_branch_on_merge on (`--keep-head-branches` to leave it)
  migrate    classic branch protection -> rulesets, losing nothing

`--require-check CONTEXT` additionally requires a CI context to be green before a
PR can merge. It is gated on observed coverage: a context that never reports
leaves every future PR pending forever, so `apply` refuses to install one it has
not seen report on recent merges.

Every mode verifies the SHAPE of protection, never its mere presence, and never
its mere existence on *some* ref — a ruleset that targets only tags protects the
default branch not at all. See references/failure-modes.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gh_api import gh, gh_optional, GhError  # noqa: E402

REQUIRED_RULES = {"deletion", "non_fast_forward"}
DEFAULT_INCLUDE = ["~DEFAULT_BRANCH"]
# fastlane `match` pushes directly and non-interactively; it cannot open a PR
CERT_RE = re.compile(r"cert|fastlane|match", re.I)
AUTODEL = {True: "on", False: "OFF", None: "?"}
MAX_WORKERS = 4          # the secondary rate limiter trips well before the hourly quota


def standard_rules(approvals: int = 0) -> list[dict]:
    return [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "pull_request", "parameters": {
            "required_approving_review_count": approvals,
            "dismiss_stale_reviews_on_push": False,
            "require_code_owner_review": False,
            "require_last_push_approval": False,
            "required_review_thread_resolution": False,
            "allowed_merge_methods": ["merge", "squash", "rebase"]}},
    ]


def status_check_rule(checks: list, strict: bool = False) -> dict:
    """`checks` may be context strings or full `{context, integration_id}` objects.

    An `integration_id` pins the context to one app. Rebuilding a rule from
    context strings alone drops that binding, so any provider can then satisfy
    the name — a downgrade, and a silent one. Objects are kept verbatim.

    `strict` additionally requires the branch be up to date with base, which
    forces a rebase every time base moves. Off by default: across an org that is
    substantial churn for little added safety.
    """
    return {"type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": bool(strict),
        "required_status_checks": [c if isinstance(c, dict) else {"context": c}
                                   for c in checks]}}


def ctx_of(c) -> str:
    return c["context"] if isinstance(c, dict) else c


def merge_status_checks(rules: list[dict], checks: list, strict: bool) -> None:
    """Union `checks` into `rules` in place; `strict` only ever goes off->on.

    Both `apply` and `migrate` write this rule, and a repo can pass through both.
    Replacing the set in either one loses whatever the other put there — which is
    a downgrade, so neither may do it. Existing entries are carried **verbatim**
    so an `integration_id` binding survives the merge.
    """
    if not checks:
        return
    old = next((x for x in rules if x["type"] == "required_status_checks"), None)
    if old is None:
        rules.append(status_check_rule(checks, strict))
        return
    prm = old.get("parameters") or {}
    have = list(prm.get("required_status_checks") or [])
    have_ctx = {ctx_of(c) for c in have}
    rules[rules.index(old)] = status_check_rule(
        have + [c for c in checks if ctx_of(c) not in have_ctx],
        strict or bool(prm.get("strict_required_status_checks_policy")))


def classic_checks_from(rsc: dict) -> list:
    """Classic's checks as ruleset objects. `checks` carries `app_id`, which maps
    to `integration_id`; `contexts` is the legacy shape with no binding."""
    if rsc.get("checks"):
        return [{"context": c["context"],
                 **({"integration_id": c["app_id"]} if c.get("app_id") else {})}
                for c in rsc["checks"]]
    return list(rsc.get("contexts") or [])


def classic_checks(slug: str, branch: str) -> tuple[list, bool]:
    """(checks, strict) from classic protection — ([], False) if there is none."""
    rsc = (gh_optional(f"repos/{slug}/branches/{branch}/protection")
           or {}).get("required_status_checks") or {}
    return classic_checks_from(rsc), bool(rsc.get("strict"))


def merged_prs(slug: str, sample: int) -> list[dict]:
    """The `sample` most recently MERGED pull requests.

    The pulls endpoint sorts by `created` or `updated` only — it cannot sort by
    merge date. `updated` drifts the sample toward old PRs that a recent comment
    bumped, which reads as missing coverage on a repo that adopted the check
    later; on one repo that produced 1/10 where the true figure was 10/10. So
    fetch a wide page and order by `merged_at` here.
    """
    prs = gh(f"repos/{slug}/pulls?state=closed&per_page=100&sort=created&direction=desc")
    return sorted((p for p in prs if p.get("merged_at")),
                  key=lambda p: p["merged_at"], reverse=True)[:sample]


def commit_contexts(slug: str, sha: str) -> set[str]:
    """Every status context and check-run name on a commit.

    Both endpoints stop at 30 items by default. A context dropped by truncation
    is indistinguishable from one that never ran, so the gate would exclude a
    repo that is in fact covered — the same false-exclusion the `merged_at`
    ordering exists to avoid. `/statuses` is a plain array and paginates;
    `/check-runs` wraps its array in an object, so page it until `total_count`
    is satisfied.
    """
    names = {s["context"] for s in
             (gh(f"repos/{slug}/commits/{sha}/statuses?per_page=100", paginate=True) or [])}
    runs, page = [], 1
    while True:
        d = gh(f"repos/{slug}/commits/{sha}/check-runs?per_page=100&page={page}") or {}
        got = d.get("check_runs") or []
        runs += got
        if not got or len(runs) >= (d.get("total_count") or 0):
            break
        page += 1
    return names | {c["name"] for c in runs}


def check_coverage(slug: str, contexts: list[str], sample: int = 10) -> dict:
    """Has each context actually reported on the most recent merges?

    This gate is the reason `--require-check` is safe to run across an org. A
    required context that never reports does not fail the PR — it sits at
    "Expected — waiting for status to be reported" forever. GitHub applies no
    timeout, and with `bypass_actors: []` not even an admin can merge past it.
    The only escape is editing the ruleset back. So a context is installable
    only once we have watched it report.

    A ruleset context matches either a commit status or a check-run name, so
    both are collected.
    """
    prs = merged_prs(slug, sample)
    seen = {c: 0 for c in contexts}
    for pr in prs:
        names = commit_contexts(slug, pr["head"]["sha"])
        for c in contexts:
            if c in names:
                seen[c] += 1
    # no merged PRs means nothing to learn from — fail closed, same as a miss
    missing = list(contexts) if not prs else [c for c in contexts if seen[c] < len(prs)]
    return {"sampled": len(prs), "seen": seen, "missing": missing}


def payload(name: str, rules: list[dict], keep: dict | None = None) -> dict:
    """`keep` is an existing ruleset's ref_name condition, preserved verbatim.

    Writing DEFAULT_INCLUDE unconditionally would NARROW a ruleset that also
    targets e.g. refs/heads/release/* — silently unprotecting those branches.
    That is a downgrade, which this tool must never do.
    """
    cond = {"include": list((keep or {}).get("include") or DEFAULT_INCLUDE),
            "exclude": list((keep or {}).get("exclude") or [])}
    return {"name": name, "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": cond},
            "rules": rules, "bypass_actors": []}


GLOB_CHARS = "*?["


def ref_coverage(include: list[str], exclude: list[str], branch: str) -> str:
    """Does this ruleset's ref condition reach the default branch?

    Returns "covers" | "no" | "uncertain".

    Deliberately does NOT reimplement GitHub's glob grammar. An earlier version
    did, and every bug found in review lived there: `*` was first treated as a
    literal, then as Python fnmatch (which lets it cross `/`, MORE permissive
    than GitHub — a false PROTECTED), then bracket classes were missing. Each
    fix was inferred from a counterexample rather than a spec we can verify, and
    GitHub can change the grammar underneath us.

    A survey of the org found 96 of 96 rulesets using `~DEFAULT_BRANCH` and zero
    globs, so the grammar bought nothing and cost three rounds of defects. Any
    pattern we cannot decide exactly is reported as "uncertain" and treated as
    unprotected — the caller surfaces it for a human instead of guessing, which
    is the fail-closed direction this tool exists to hold.
    """
    ref = f"refs/heads/{branch}"
    exact = ("~ALL", "~DEFAULT_BRANCH", ref)

    for e in (exclude or []):
        if e in exact:
            return "no"
        if any(ch in e for ch in GLOB_CHARS):
            return "uncertain"

    if any(i in exact for i in (include or [])):
        return "covers"
    if any(ch in i for i in (include or []) for ch in GLOB_CHARS):
        return "uncertain"
    return "no"


def covers_default(include: list[str], exclude: list[str], branch: str) -> bool:
    """True only when coverage is certain. 'uncertain' is not protection."""
    return ref_coverage(include, exclude, branch) == "covers"


# ---------------------------------------------------------------- discovery
def repos_for(scope: str) -> list[dict]:
    if "/" in scope:                                   # owner/repo
        return [gh(f"repos/{scope}")]
    out = gh(f"orgs/{scope}/repos?per_page=100", paginate=True)
    return [m for m in out if not m.get("archived") and m.get("default_branch")]


def rulesets_for(slug: str, branch: str) -> tuple[list[dict], dict | None, list[dict]]:
    """(covering rulesets, the repo-level one we may mutate, undecidable ones).

    /rulesets returns repo rulesets AND inherited org/enterprise ones, unordered.
    Both protect, so both count for the audit; only a Repository-source ruleset
    can be edited here.
    """
    covering, own, unsure = [], None, []
    for rs in gh(f"repos/{slug}/rulesets"):
        d = gh(f"repos/{slug}/rulesets/{rs['id']}")
        cond = (d.get("conditions") or {}).get("ref_name") or {}
        if d.get("target", "branch") != "branch":
            continue
        cov = ref_coverage(cond.get("include"), cond.get("exclude"), branch)
        if cov == "uncertain":
            unsure.append(d)
            continue
        if cov != "covers":
            continue
        covering.append(d)
        src = (d.get("source_type") or rs.get("source_type") or "Repository")
        if own is None and src == "Repository":
            own = d
    return covering, own, unsure


# ---------------------------------------------------------------- audit
def inspect(repo: dict) -> dict:
    owner, name = repo["owner"]["login"], repo["name"]
    slug, branch = f"{owner}/{name}", repo["default_branch"]
    row = {"repo": name, "slug": slug, "branch": branch, "mechanism": "none",
           "enforcement": None, "rules": [], "bypass": 0, "approvals": None,
           "checks": [], "checks_strict": False,
           "verdict": "UNPROTECTED", "detail": "", "ruleset_id": None}

    covering, own, unsure = rulesets_for(slug, branch)
    if covering:
        active = [d for d in covering if d.get("enforcement") == "active"]
        rules, bypass = set(), 0
        approvals = None
        checks: set[str] = set()
        checks_strict = False
        for d in active:
            rules |= {x["type"] for x in d.get("rules", [])}
            bypass += len(d.get("bypass_actors") or [])
            for x in d.get("rules", []):
                if x["type"] == "pull_request":
                    approvals = x.get("parameters", {}).get("required_approving_review_count")
                if x["type"] == "required_status_checks":
                    prm = x.get("parameters", {})
                    checks |= {c["context"] for c in
                               prm.get("required_status_checks") or []}
                    checks_strict |= bool(prm.get("strict_required_status_checks_policy"))
        row.update(mechanism="ruleset", ruleset_id=(own or covering[0])["id"],
                   enforcement=("active" if active else covering[0].get("enforcement")),
                   rules=sorted(rules), bypass=bypass, approvals=approvals,
                   checks=sorted(checks), checks_strict=checks_strict,
                   covering=len(covering))
        if not active:
            row["verdict"], row["detail"] = "WEAK", "no ruleset is enforcement=active"
        elif not REQUIRED_RULES <= rules:
            row["verdict"] = "WEAK"
            row["detail"] = f"missing rule(s): {sorted(REQUIRED_RULES - rules)}"
        elif bypass:
            row["verdict"], row["detail"] = "WEAK", f"{bypass} bypass actor(s)"
        else:
            row["verdict"] = "PROTECTED"
        return row

    if unsure:
        pats = sorted({p for d in unsure
                       for p in ((d.get("conditions") or {}).get("ref_name") or {}).get("include", [])})
        row.update(mechanism="ruleset", verdict="UNCERTAIN",
                   detail=f"ref pattern(s) {pats} — cannot verify coverage, check manually")
        return row

    classic = gh_optional(f"repos/{slug}/branches/{branch}/protection")
    if classic:
        fp = (classic.get("allow_force_pushes") or {}).get("enabled")
        ea = (classic.get("enforce_admins") or {}).get("enabled")
        rpr = classic.get("required_pull_request_reviews")
        row.update(mechanism="classic", enforcement="n/a", rules=["classic"],
                   approvals=(rpr or {}).get("required_approving_review_count"),
                   checks=sorted((classic.get("required_status_checks") or {})
                                 .get("contexts") or []),
                   checks_strict=bool((classic.get("required_status_checks") or {})
                                      .get("strict")),
                   bypass=0 if ea else 1)
        if fp:
            row["verdict"], row["detail"] = "WEAK", "force-push allowed"
        elif not ea:
            row["verdict"], row["detail"] = "WEAK", "enforce_admins=false (admins bypass)"
        else:
            row["verdict"] = "PROTECTED"
    return row


# ---------------------------------------------------------------- preflight
def preflight(repo: dict, days: int = 90) -> dict:
    from datetime import datetime, timedelta, timezone
    slug = f"{repo['owner']['login']}/{repo['name']}"
    branch = repo["default_branch"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    acts = gh(f"repos/{slug}/activity?per_page=100&ref=refs/heads/{branch}", paginate=True)
    # the activity API has no server-side day filter — bound it here, or `days`
    # is decorative and the blocker set reflects whatever window the API chose.
    acts = [a for a in acts if (a.get("timestamp") or "") >= cutoff]
    fps = [a for a in acts if a.get("activity_type") == "force_push"]
    pushes = [a for a in acts if a.get("activity_type") == "push"]
    actors: dict[str, int] = {}
    for a in pushes:
        who = (a.get("actor") or {}).get("login") or "?"
        actors[who] = actors.get(who, 0) + 1
    blockers = []
    if CERT_RE.search(repo["name"]):
        blockers.append("name matches cert/fastlane/match — fastlane cannot open a PR")
    for who in actors:
        if who and ("[bot]" in who or "bot" in who.lower()):
            blockers.append(f"non-human pusher: {who}")
    return {"repo": repo["name"], "window_days": days, "force_pushes": len(fps),
            "direct_pushes": len(pushes), "actors": actors, "blockers": blockers}


# ---------------------------------------------------------------- apply
def conforms(row: dict, want_pr: bool, want_checks: list[str] | None = None,
             want_strict: bool = False) -> bool:
    """Verdict PROTECTED is necessary but not sufficient — it does not imply a
    pull_request rule or any required check, so `apply` must not treat it as
    'nothing to do'.

    `want_strict` is part of the shape: a repo carrying the right contexts with
    `strict` off has not yet had a requested off->on upgrade applied, and
    reporting it as conforming would make `--strict-checks` a silent no-op.
    """
    if row["verdict"] != "PROTECTED":
        return False
    if want_pr and "pull_request" not in row["rules"]:
        return False
    if want_checks:
        if not set(want_checks) <= set(row.get("checks") or []):
            return False
        if want_strict and not row.get("checks_strict"):
            return False
    return True


def apply_one(repo: dict, approvals: int, dry: bool, want_pr: bool = True,
              checks: list[str] | None = None, strict: bool = False,
              sample: int = 10, gate: bool = True) -> dict:
    slug = f"{repo['owner']['login']}/{repo['name']}"
    branch = repo["default_branch"]
    cur = inspect(repo)
    if conforms(cur, want_pr, checks, strict):
        return {"repo": repo["name"], "action": "skip", "why": "already conforming"}

    _, own, _unsure = rulesets_for(slug, branch)

    # Contexts the RULESET already requires. `cur["checks"]` is the wrong source:
    # on a classic-protected repo it reports classic's contexts, and those are
    # precisely the ones about to be written into a ruleset with
    # `bypass_actors: []` — where a stale one becomes absolutely blocking rather
    # than admin-bypassable. They need the gate, not an exemption from it.
    own_ctx: set[str] = set()
    if own:
        for x in own.get("rules", []):
            if x["type"] == "required_status_checks":
                own_ctx |= {ctx_of(c) for c in
                            x.get("parameters", {}).get("required_status_checks") or []}

    # classic's contexts ride along when a ruleset is seeded (see below)
    seed_checks, seed_strict = ([], False)
    if checks and not own:
        seed_checks, seed_strict = classic_checks(slug, branch)

    new_checks: list = []
    for c in list(checks or []) + list(seed_checks):       # de-dup, keep order
        if ctx_of(c) not in own_ctx | {ctx_of(x) for x in new_checks}:
            new_checks.append(c)
    if new_checks and gate:
        cov = check_coverage(slug, [ctx_of(c) for c in new_checks], sample)
        if cov["missing"]:
            inherited = {ctx_of(c) for c in seed_checks} & set(cov["missing"])
            note = (f" ({sorted(inherited)} come from classic protection and must be "
                    f"resolved there first)") if inherited else ""
            return {"repo": repo["name"], "action": "skip-coverage",
                    "why": f"{cov['missing']} did not report on all {cov['sampled']} "
                           f"most-recent merged PRs — requiring it would leave every "
                           f"future PR pending forever{note}"}

    if own:
        have = {x["type"] for x in own.get("rules", [])}
        rules = list(own.get("rules", []))                    # preserve verbatim
        for t in sorted(REQUIRED_RULES - have):
            rules.append({"type": t})
        if want_pr and "pull_request" not in have:            # NEVER downgrade
            rules.append(standard_rules(approvals)[2])
        # driven by `checks`, not `new_checks`, so a strict-only upgrade still
        # lands on a repo that already carries every requested context
        merge_status_checks(rules, checks, strict)
        keep = (own.get("conditions") or {}).get("ref_name")
        body, verb, path = payload(own["name"], rules, keep), "PUT", \
            f"repos/{slug}/rulesets/{own['id']}"
    else:
        rules = standard_rules(approvals) if want_pr else standard_rules(approvals)[:2]
        if checks:
            # seed from classic too. `migrate` later deletes classic, and it skips
            # translating a rule type the ruleset already has — so a context only
            # classic carried would be dropped at that point. Both sets passed the
            # gate above.
            merge_status_checks(rules, list(seed_checks) + list(checks),
                                strict or seed_strict)
        body, verb, path = payload("main", rules), "POST", f"repos/{slug}/rulesets"

    if dry:
        ctx = next((x for x in body["rules"] if x["type"] == "required_status_checks"), None)
        return {"repo": repo["name"], "action": "would-apply",
                "rules": sorted(x["type"] for x in body["rules"]),
                "checks": sorted(c["context"] for c in
                                 (ctx or {}).get("parameters", {}).get(
                                     "required_status_checks") or []),
                "strict": bool((ctx or {}).get("parameters", {}).get(
                    "strict_required_status_checks_policy"))}
    gh(path, method=verb, input_json=body)
    after = inspect(repo)                                     # verify the write
    if not conforms(after, want_pr, checks, strict):
        return {"repo": repo["name"], "action": "FAILED-VERIFY",
                "why": after["detail"] or f"rules={after['rules']} checks={after['checks']}"}
    return {"repo": repo["name"], "action": "applied", "rules": after["rules"],
            "checks": after["checks"]}


# ---------------------------------------------------------------- head-branch cleanup
def head_branch_cleanup(repo: dict) -> bool | None:
    """The repo's `delete_branch_on_merge` ("Automatically delete head branches").

    The org repo listing omits this field, so a repo dict from `repos_for(org)`
    needs its own GET. GitHub also omits it for a caller without admin on the
    repo; that is reported as None — unknown, never counted as on.
    """
    if "delete_branch_on_merge" not in repo:
        repo = gh(f"repos/{repo['owner']['login']}/{repo['name']}")
    val = repo.get("delete_branch_on_merge")
    return val if isinstance(val, bool) else None


def audit_one(repo: dict) -> dict:
    row = inspect(repo)
    row["delete_branch_on_merge"] = head_branch_cleanup(repo)
    return row


def enable_head_branch_cleanup(repo: dict, dry: bool) -> str:
    """"on" | "would-enable" | "enabled" | "FAILED-VERIFY".

    With the setting off, merged branches pile up and a stacked PR keeps
    targeting its merged base instead of being retargeted onto the default
    branch, so a chain cannot merge one PR after another.
    """
    if head_branch_cleanup(repo) is True:
        return "on"
    if dry:
        return "would-enable"
    slug = f"{repo['owner']['login']}/{repo['name']}"
    after = gh(f"repos/{slug}", method="PATCH",
               input_json={"delete_branch_on_merge": True})
    # verify from the PATCH response — a repo whose field reads back as
    # anything but True was not changed, whatever the status code said
    if (after or {}).get("delete_branch_on_merge") is not True:
        return "FAILED-VERIFY"
    return "enabled"


def apply_repo(repo: dict, approvals: int, dry: bool, want_pr: bool = True,
               checks: list[str] | None = None, strict: bool = False,
               sample: int = 10, gate: bool = True,
               head_cleanup: bool = True) -> dict:
    """Rulesets, then the head-branch setting. Independent writes: a ruleset
    skipped on coverage still gets the setting, and vice versa."""
    row = apply_one(repo, approvals, dry, want_pr, checks, strict, sample, gate)
    if head_cleanup:
        row["delete_branch_on_merge"] = enable_head_branch_cleanup(repo, dry)
    return row


# ---------------------------------------------------------------- migrate
def migrate_one(repo: dict, dry: bool) -> dict:
    """Create/update the ruleset FIRST, verify, and only then delete classic."""
    slug = f"{repo['owner']['login']}/{repo['name']}"
    branch = repo["default_branch"]
    classic = gh_optional(f"repos/{slug}/branches/{branch}/protection")
    if not classic:
        return {"repo": repo["name"], "action": "skip", "why": "no classic protection"}

    rpr = classic.get("required_pull_request_reviews")
    rsc = classic.get("required_status_checks") or {}
    rules = [{"type": "deletion"}, {"type": "non_fast_forward"}]
    if rpr:
        rules.append({"type": "pull_request", "parameters": {
            "required_approving_review_count": rpr.get("required_approving_review_count", 0),
            "dismiss_stale_reviews_on_push": bool(rpr.get("dismiss_stale_reviews")),
            "require_code_owner_review": bool(rpr.get("require_code_owner_reviews")),
            "require_last_push_approval": False,
            "required_review_thread_resolution": bool(
                (classic.get("required_conversation_resolution") or {}).get("enabled"))}})
    cchecks = classic_checks_from(rsc)
    if cchecks:
        rules.append(status_check_rule(cchecks, bool(rsc.get("strict"))))
    if (classic.get("required_linear_history") or {}).get("enabled"):
        rules.append({"type": "required_linear_history"})

    # a repo can carry BOTH classic protection and a ruleset — reuse it rather
    # than POSTing a second one and leaving a duplicate behind
    _, own, _unsure = rulesets_for(slug, branch)
    if own:
        have = {x["type"] for x in own.get("rules", [])}
        merged = list(own.get("rules", []))
        for r in rules:
            if r["type"] == "required_status_checks":
                prm = r.get("parameters") or {}
                merge_status_checks(
                    merged, [c["context"] for c in prm.get("required_status_checks") or []],
                    bool(prm.get("strict_required_status_checks_policy")))
            elif r["type"] not in have:
                merged.append(r)
        rules = merged
        keep = (own.get("conditions") or {}).get("ref_name")
        verb, path, nm = "PUT", f"repos/{slug}/rulesets/{own['id']}", own["name"]
    else:
        keep = None
        verb, path, nm = "POST", f"repos/{slug}/rulesets", "main"

    if dry:
        return {"repo": repo["name"], "action": "would-migrate",
                "via": verb, "rules": sorted(x["type"] for x in rules)}

    gh(path, method=verb, input_json=payload(nm, rules, keep))
    after = inspect(repo)
    want = {x["type"] for x in rules}
    if after["verdict"] != "PROTECTED" or not want <= set(after["rules"]):
        return {"repo": repo["name"], "action": "FAILED-VERIFY",
                "why": f"got={after['rules']} want={sorted(want)} — classic LEFT IN PLACE"}
    # deleting classic is irreversible, so verify its contexts survived the
    # translation rather than trusting the rule type alone
    want_ctx = {ctx_of(c) for c in cchecks}
    if not want_ctx <= set(after.get("checks") or []):
        return {"repo": repo["name"], "action": "FAILED-VERIFY",
                "why": f"checks {sorted(want_ctx - set(after.get('checks') or []))} did not "
                       f"survive — classic LEFT IN PLACE"}
    gh(f"repos/{slug}/branches/{branch}/protection", method="DELETE")
    return {"repo": repo["name"], "action": "migrated", "rules": sorted(want)}


# ---------------------------------------------------------------- driver
def run(fn, repos, *a):
    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fn, r, *a): r for r in repos}
        for f in as_completed(futs):
            r = futs[f]
            try:
                rows.append(f.result())
            except GhError as e:                    # fail closed — never "clean"
                errors.append({"repo": r["name"], "error": str(e)[:160]})
    return rows, errors


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["audit", "preflight", "apply", "migrate"])
    ap.add_argument("scope", help="org name, or owner/repo")
    ap.add_argument("--approvals", type=int, default=0)
    ap.add_argument("--days", type=int, default=90, help="preflight lookback window")
    ap.add_argument("--no-pull-request", action="store_true",
                    help="apply deletion+non_fast_forward only (e.g. fastlane cert repos)")
    ap.add_argument("--require-check", action="append", default=[], metavar="CONTEXT",
                    help="CI context that must be green to merge, e.g. 'ci/circleci: test'. "
                         "Repeatable. Gated on observed coverage.")
    ap.add_argument("--strict-checks", action="store_true",
                    help="also require the branch be up to date with base (forces a rebase "
                         "whenever base moves)")
    ap.add_argument("--check-sample", type=int, default=10, metavar="N",
                    help="merged PRs to sample for the coverage gate (default 10)")
    ap.add_argument("--no-coverage-gate", action="store_true",
                    help="install --require-check contexts without verifying they report "
                         "(DANGEROUS: a context that never reports blocks every PR forever)")
    ap.add_argument("--keep-head-branches", action="store_true",
                    help="apply: leave 'Automatically delete head branches' as it is")
    ap.add_argument("--exclude", default="", help="comma-separated repo names to skip")
    ap.add_argument("--include-cert-repos", action="store_true",
                    help="do NOT auto-exclude cert/fastlane/match repos (dangerous)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repos = repos_for(args.scope)
    skipped, keep = [], []
    manual = {s.strip() for s in args.exclude.split(",") if s.strip()}
    for r in repos:
        if r["name"] in manual:
            skipped.append((r["name"], "excluded by --exclude")); continue
        if args.mode in ("apply", "migrate") and not args.include_cert_repos \
                and CERT_RE.search(r["name"]):
            skipped.append((r["name"], "cert/fastlane repo — fastlane cannot open a PR"))
            continue
        keep.append(r)

    if args.mode == "audit":
        rows, errors = run(audit_one, keep)
    elif args.mode == "preflight":
        rows, errors = run(preflight, keep, args.days)
    elif args.mode == "apply":
        rows, errors = run(apply_repo, keep, args.approvals, args.dry_run,
                           not args.no_pull_request, args.require_check,
                           args.strict_checks, args.check_sample,
                           not args.no_coverage_gate, not args.keep_head_branches)
    else:
        rows, errors = run(migrate_one, keep, args.dry_run)

    result = {"mode": args.mode, "scope": args.scope, "checked": len(keep),
              "rows": rows, "errors": errors,
              "skipped": [{"repo": n, "why": w} for n, w in skipped],
              "complete": not errors}
    if args.mode == "audit":
        prot = [r for r in rows if r["verdict"] == "PROTECTED"]
        result["summary"] = f"{len(prot)}/{len(rows)} fully protected"
        result["gaps"] = [r for r in rows if r["verdict"] != "PROTECTED"]
        result["no_required_checks"] = [r["repo"] for r in rows if not r.get("checks")]
        result["head_branches_kept"] = [r["repo"] for r in rows
                                        if r.get("delete_branch_on_merge") is False]
        result["head_branches_unknown"] = [r["repo"] for r in rows
                                           if r.get("delete_branch_on_merge") is None]

    if args.json:
        print(json.dumps(result, indent=2, default=str)); return

    print(f"mode={args.mode}  scope={args.scope}  checked={len(keep)}")
    if args.mode == "audit":
        print(f"\n  {'repo':<26}{'mech':<10}{'enf':<10}{'bypass':<8}{'checks':<8}"
              f"{'autodel':<9}{'verdict':<12}detail")
        for r in sorted(rows, key=lambda x: (x["verdict"] == "PROTECTED", x["repo"])):
            print(f"  {r['repo']:<26}{r['mechanism']:<10}{str(r['enforcement']):<10}"
                  f"{r['bypass']:<8}{len(r.get('checks') or []):<8}"
                  f"{AUTODEL[r.get('delete_branch_on_merge')]:<9}"
                  f"{r['verdict']:<12}{r['detail']}")
        print(f"\n  {result['summary']}")
        nochk = [r for r in rows if not r.get("checks")]
        if nochk:
            print(f"  {len(nochk)}/{len(rows)} have no required status check — "
                  f"a red CI run does not block merge there")
        kept, unknown = result["head_branches_kept"], result["head_branches_unknown"]
        if kept:
            print(f"  {len(kept)}/{len(rows)} keep merged head branches "
                  f"(delete_branch_on_merge off) — `apply` turns it on")
        if unknown:
            print(f"  {len(unknown)}/{len(rows)} head-branch setting unreadable "
                  f"(needs admin on the repo): {', '.join(sorted(unknown))}")
    else:
        for r in sorted(rows, key=lambda x: x["repo"]):
            print(f"  {r['repo']:<26}{r}")
    if skipped:
        print(f"\n  SKIPPED ({len(skipped)}):")
        for n, w in skipped:
            print(f"    {n:<26}{w}")
    if errors:
        print(f"\n  ERRORS ({len(errors)}) — run is INCOMPLETE:")
        for e in errors:
            print(f"    {e['repo']:<26}{e['error']}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
