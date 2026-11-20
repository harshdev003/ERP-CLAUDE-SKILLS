#!/usr/bin/env python3
"""Who creates and updates skills in AINative, by week, read from git.

**Read from git, not from a warehouse.** Git is already the authoritative record
of who changed what and when, it is always available, and it needs no sync. A
BigQuery hop would add staleness and a maintenance burden for nothing. This note
exists so nobody later "fixes" the design by adding a store.

Two measurement traps drive everything below. Both were verified against this
repo's real history, and both fail by producing a plausible number.

**A mechanical sweep is not authoring.** Over 2026-08-24 → 09-08, 236 of 350
skill-touches came from two commits: one trimming every SKILL.md description
(149 skills) and one declaring `Tools/*` reads (87). Counting raw skills-touched
makes a repo-wide find-and-replace outrank a month of real work. So a commit
touching `SWEEP_THRESHOLD` or more skills is classed a sweep and counted in its
own column — tagged, never dropped, because a filtered corpus that leaves no
trace is worse than a loud one.

**One person is two git identities, silently.** In this repo Jerome commits as
`Jerome Curlier <<redacted-email>>` *and* `jcurlier <…@users.noreply…>`, while
Rohan commits under two names on one email. Email alone cannot merge the first;
name alone cannot merge the second. So identity goes through an explicit alias
map, and anything unmapped is **reported** rather than quietly shown as a new
person — otherwise one contributor is split in half and both halves understate.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys

# A commit touching this many distinct skills is a mechanical pass, not N pieces
# of authoring. Set from the observed shape of this repo: ordinary work touches
# 1–7 skills; the sweeps were 87 and 149.
SWEEP_THRESHOLD = 10

# Only these count as a skill change.
SKILL_PATH = re.compile(r"^plugins/([^/]+)/skills/([^/]+)/")

# Who is who. Email alone cannot merge Jerome (two different emails); name alone
# cannot merge Rohan (two names, one email) — so this is explicit rather than
# derived, and anything absent from it is REPORTED rather than folded into
# someone else. A split person understates both halves and nothing errors.
#
# The human-facing record of the team is the **Team page in AI Knowledge**. This
# map is deliberately NOT read from there at run time: doing so would mean
# bundling the Notion helper into this skill (a 25th copy of a drift-prone file)
# and making a git-only report depend on a token it otherwise never needs. When
# `unmapped_identities` is non-empty, add the row here and to that page.
IDENTITY_ALIASES = {
    "<redacted-email>": "Jerome Curlier",
    "jcurlier@users.noreply.github.com": "Jerome Curlier",
    "<redacted-email>": "Rohan Agarwal",
    "<redacted-email>": "Alyssa",
    "106625489+iyawar@users.noreply.github.com": "Yawar",
    "<redacted-email>": "Farhan Tahir",
    "30401501+naveed-rana@users.noreply.github.com": "Naveed Rana",
    "<redacted-email>": "Steve Lee",
    "91134658+devashishhp04@users.noreply.github.com": "Devashish",
}

# Eight named colour slots, matching the report's other stacked charts. A ninth
# person folds into a neutral "Other" band rather than minting a hue — and the
# fold is counted and named in the output, never silently applied.
PERSON_SLOTS = 8

# The repo this section is about. Verified before reading, because two diverged
# AINative clones exist on this machine and analysing the wrong one produces a
# confident wrong answer with no error.
EXPECTED_REMOTE = "AuraHealth/AINative"

SEP = "\x1e"


class GitUnavailable(Exception):
    """No usable AINative checkout — the section degrades rather than guessing."""


def _run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def resolve_repo(explicit: str | None = None) -> str:
    """Find the AINative checkout and prove it is the right one.

    In-tree (this skill ships inside AINative) the answer is exact: walk up from
    this file to the git root. Installed to the plugin cache it is not, so
    `--repo` and `AINATIVE_REPO` exist. A glob over likely paths is deliberately
    not attempted — that is how the wrong clone gets picked.
    """
    asked = explicit or os.environ.get("AINATIVE_REPO")
    for cand, authoritative in ((asked, True),
                                (os.path.dirname(os.path.abspath(__file__)), False)):
        if not cand:
            continue
        # A candidate the caller NAMED is authoritative: if it does not resolve,
        # raise rather than fall through to the next one. Falling through would
        # silently report on the checkout the caller was trying not to use — and
        # the remote check below cannot catch that, because the two clones share
        # an origin.
        if not os.path.isdir(cand):
            if authoritative:
                raise GitUnavailable(f"{cand} is not a directory.")
            continue
        r = _run(["git", "rev-parse", "--show-toplevel"], cand)
        if r.returncode != 0:
            if authoritative:
                raise GitUnavailable(f"{cand} is not a git checkout.")
            continue
        root = r.stdout.strip()
        remote = _run(["git", "remote", "get-url", "origin"], root).stdout.strip()
        if EXPECTED_REMOTE.lower() in remote.lower():
            return root
        raise GitUnavailable(
            f"{root} is a git repo but its origin is {remote or '(none)'}, not "
            f"{EXPECTED_REMOTE}. Refusing to report on the wrong checkout — pass "
            f"--repo with the right one.")
    raise GitUnavailable(
        "No AINative checkout found. Pass --repo /path/to/AINative or set "
        "AINATIVE_REPO. (Two diverged clones exist on some machines, so this is "
        "not guessed.)")


def _ref(repo: str) -> str:
    """Prefer origin/main so a stale or dirty local branch cannot skew the read."""
    if _run(["git", "rev-parse", "--verify", "-q", "origin/main"], repo).returncode == 0:
        return "origin/main"
    return "HEAD"


def read_commits(repo: str, since: str, through: str) -> list:
    ref = _ref(repo)
    out = _run(["git", "log", ref, f"--since={since}", f"--until={through} 23:59:59",
                "--no-merges", f"--format={SEP}%H|%an|%ae|%ad|%s", "--date=short",
                "--name-only", "--", "plugins/*/skills/*"], repo)
    if out.returncode != 0:
        raise GitUnavailable(f"git log failed: {out.stderr.strip()[:200]}")

    commits, cur = [], None
    for line in out.stdout.split("\n"):
        if line.startswith(SEP):
            if cur:
                commits.append(cur)
            parts = line[1:].split("|", 4)
            if len(parts) < 5:
                cur = None
                continue
            sha, name, email, date, subject = parts
            cur = {"sha": sha, "name": name, "email": email.lower(), "date": date,
                   "subject": subject, "files": []}
        elif line.strip() and cur is not None:
            cur["files"].append(line.strip())
    if cur:
        commits.append(cur)
    return commits


def created_skills(repo: str, since: str, through: str) -> dict:
    """sha -> set of skills whose SKILL.md was ADDED in that commit.

    A create is unambiguous in a way an update is not, so it is counted from the
    diff filter rather than inferred from which files moved.
    """
    ref = _ref(repo)
    out = _run(["git", "log", ref, f"--since={since}", f"--until={through} 23:59:59",
                "--no-merges", "--diff-filter=A", f"--format={SEP}%H", "--name-only",
                "--", "plugins/*/skills/*/SKILL.md"], repo)
    made, cur = collections.defaultdict(set), None
    for line in out.stdout.split("\n"):
        if line.startswith(SEP):
            cur = line[1:].strip()
        elif line.strip() and cur:
            m = SKILL_PATH.match(line.strip())
            if m:
                made[cur].add(f"{m.group(1)}:{m.group(2)}")
    return made


def person(email: str, name: str, aliases: dict) -> tuple:
    """(display name, was_mapped). An unmapped identity keeps its git name and
    is reported, never silently folded into someone else."""
    if email in aliases:
        return aliases[email], True
    handle = email.split("@")[0].split("+")[-1]
    if handle in aliases:            # a GitHub handle rather than an email
        return aliases[handle], True
    return name, False


def _bucket(d: str, grain: str) -> str:
    day = dt.date.fromisoformat(d)
    if grain == "month":
        return f"{day.year}-{day.month:02d}-01"
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def _tally(commits, creates, aliases):
    """Walk the commits once. Returns (per_day, unmapped, sweeps).

    `per_day` is day -> person -> {created, updated, swept, commits}. Bucketing
    into months or weeks happens afterwards, so the walk — and every judgement
    it makes about sweeps and identity — is shared by both grains rather than
    repeated and free to diverge.
    """
    per_day = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {"created": set(), "updated": set(), "swept": set(), "commits": 0}))
    unmapped = collections.Counter()
    sweeps = []
    for c in commits:
        skills = set()
        for f in c["files"]:
            m = SKILL_PATH.match(f)
            if m:
                skills.add(f"{m.group(1)}:{m.group(2)}")
        if not skills:
            continue
        who, mapped = person(c["email"], c["name"], aliases)
        if not mapped:
            unmapped[f"{c['name']} <{c['email']}>"] += 1
        made = creates.get(c["sha"], set())
        is_sweep = len(skills) >= SWEEP_THRESHOLD
        b = per_day[c["date"]][who]
        b["commits"] += 1
        b["created"] |= made
        # A sweep's touches are kept, in their own column — dropping them would
        # leave no trace that a period's shape came from one mechanical pass.
        (b["swept"] if is_sweep else b["updated"]).update(skills - made)
        if is_sweep:
            sweeps.append({"date": c["date"], "who": who, "skills": len(skills),
                           "subject": c["subject"][:110]})
    return per_day, unmapped, sweeps


def _rank(per_day) -> tuple:
    """(slots, folded). The ranking is over the WHOLE window, so it does not
    depend on how the window is bucketed — that is what lets both grains share
    it. Ranking per grain instead would let a person near the fold hold a named
    hue in one view and sit in Other in the next."""
    totals = collections.Counter()
    for day in per_day.values():
        for who, v in day.items():
            totals[who] += len(v["updated"]) + len(v["created"])
    ranked = [p for p, _ in totals.most_common()]
    return ranked[:PERSON_SLOTS], ranked[PERSON_SLOTS:]


def _series(per_day, grain: str, named: list, folded: list) -> dict:
    """Bucket the shared walk at one grain, under the shared slot assignment.

    Sets are kept **per person** inside a bucket and only counted at row time.
    That is the difference between de-duplication and erasure: one person
    touching the same skill twice in a week is one touch, but two people
    touching it are two. Unioning the folded people into a single set before
    counting would silently make the "Other" band undercount exactly where it
    is least visible, so the band's number would not be comparable with the
    named rows stacked beside it.
    """
    per = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {"created": set(), "updated": set(), "swept": set(), "commits": 0}))
    for day, people in per_day.items():
        b = per[_bucket(day, grain)]
        for who, v in people.items():
            for f in ("created", "updated", "swept"):
                b[who][f] |= v[f]
            b[who]["commits"] += v["commits"]

    periods = sorted(per)
    slots = list(named) + (["Other"] if folded else [])
    # Assignment reads the SHARED lists, so a person folded in one grain is
    # folded in every grain — never named in one view and grey in the next.
    members = {w: [w] for w in named}
    if folded:
        members["Other"] = list(folded)

    def row(who):
        who_keys = members[who]
        def n(pd, field):
            return sum(len(per[pd].get(k, {}).get(field, ())) for k in who_keys)
        return {
            "person": who,
            "created": [n(pd, "created") for pd in periods],
            "updated": [n(pd, "updated") for pd in periods],
            "swept": [n(pd, "swept") for pd in periods],
            "commits": sum(per[pd].get(k, {}).get("commits", 0)
                           for pd in periods for k in who_keys),
            "total_created": sum(n(pd, "created") for pd in periods),
            "total_updated": sum(n(pd, "updated") for pd in periods),
            "total_swept": sum(n(pd, "swept") for pd in periods),
        }

    return {"periods": periods, "series": [row(w) for w in slots]}


def build(repo: str, since: str, through: str, grain: str = "week") -> dict:
    """One grain. `build_both` is what the report uses."""
    d = build_both(repo, since, through)
    g = d["grains"][grain]
    return dict(d, periods=g["periods"], series=g["series"], grain=grain)


def build_both(repo: str, since: str, through: str) -> dict:
    """Both grains from one git read, under one ranking.

    A month says who carried the quarter; a week says who is carrying it now,
    and a monthly bar hides someone who only appeared three weeks ago. Both are
    worth having, so the report carries both rather than making the caller
    choose at collect time.

    **The commits are walked once and ranked once**, then bucketed twice.
    Colour follows the entity, never its rank — so if each grain ranked and
    folded independently, a person near the eight-slot boundary could be named
    in one view and inside the neutral "Other" band in the other, and flipping
    the toggle would appear to recolour them *and* move their work. Sharing the
    walk makes that impossible rather than merely unlikely; it is not enough to
    reorder one grain's output to match the other's, because by then the folds
    have already been decided differently.
    """
    commits = read_commits(repo, since, through)
    creates = created_skills(repo, since, through)
    per_day, unmapped, sweeps = _tally(commits, creates, dict(IDENTITY_ALIASES))
    named, folded = _rank(per_day)
    grains = {g: _series(per_day, g, named, folded) for g in ("month", "week")}

    totals = collections.Counter()
    commit_counts = collections.Counter()
    fields = collections.defaultdict(lambda: {"created": set(), "updated": set(),
                                              "swept": set()})
    for day in per_day.values():
        for who, v in day.items():
            for f in ("created", "updated", "swept"):
                fields[who][f] |= v[f]
            commit_counts[who] += v["commits"]
            totals[who] += len(v["updated"]) + len(v["created"])

    return {
        "repo": repo,
        "ref": _ref(repo),
        # The slot order both grains share, emitted so a reader — and a test —
        # can see that the two views cannot disagree about who gets which hue.
        "slots": list(named) + (["Other"] if folded else []),
        "grains": grains,
        "sweep_threshold": SWEEP_THRESHOLD,
        "sweeps": sorted(sweeps, key=lambda s: -s["skills"])[:10],
        "sweep_count": len(sweeps),
        # Named, not silent: the fold is stated so a reader can see how many
        # people the neutral band stands for and who they are.
        "folded": {"count": len(folded), "people": sorted(folded)},
        # Empty is the healthy state. A non-empty list means someone's work is
        # filed under a git identity nobody has claimed yet.
        "unmapped_identities": [{"identity": k, "commits": v}
                                for k, v in unmapped.most_common()],
        # Every person, unfolded — the chart shows eight, this shows all.
        "everyone": [{"person": w,
                      "created": len(fields[w]["created"]),
                      "updated": len(fields[w]["updated"]),
                      "swept": len(fields[w]["swept"]),
                      "commits": commit_counts[w]}
                     for w, _ in totals.most_common()],
        "commits_read": len(commits),
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Weekly AINative skill authorship from git")
    p.add_argument("--repo", help="Path to an AINative checkout")
    p.add_argument("--since", default="2026-03-02")
    p.add_argument("--through", default=dt.date.today().isoformat())
    p.add_argument("--out")
    a = p.parse_args()
    try:
        repo = resolve_repo(a.repo)
        data = build_both(repo, a.since, a.through)
    except GitUnavailable as e:
        print(f"skill-authorship unavailable: {e}", file=sys.stderr)
        # Still write --out. The report's authorship section is optional, but
        # "no file" is not how a consumer learns that — it is how it crashes.
        # A written reason also puts the miss in the rendered page instead of
        # leaving a silently absent section, which reads like a clean run.
        if a.out:
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump({"unavailable": str(e)}, fh, indent=2)
            print(f"wrote {a.out}  (unavailable: {e})")
        return 3
    if data["unmapped_identities"]:
        print("! unmapped git identities (add to IDENTITY_ALIASES): " +
              ", ".join(u["identity"] for u in data["unmapped_identities"]),
              file=sys.stderr)
    blob = json.dumps(data, indent=2)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(blob)
        print(f"wrote {a.out}  ({data['commits_read']} commits, "
              f"{len(data['grains']['month']['periods'])} months / "
              f"{len(data['grains']['week']['periods'])} weeks, "
              f"{len(data['slots'])} people, {data['sweep_count']} sweeps)")
    else:
        print(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
