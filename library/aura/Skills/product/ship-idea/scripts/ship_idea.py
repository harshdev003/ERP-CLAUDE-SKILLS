"""Ship leg: a merged PR becomes a released build becomes a running experiment.

`build-idea` stops at a reviewable PR, because review, merge and release are human
decisions. This picks the row up on the other side of them and carries it to
`experiment_running`, so nobody has to remember which of last week's merges still
needs an experiment made for it.

  plan                Where is this row in the ship leg, and what runs next. No writes.
  merged              PR merged  -> Status `merged`.
  released            In a build  -> Status `released`, version in the note.
  request-experiment  Render (and optionally post) the ask to Viktor in #support-bot.
  check-experiment    The experiment exists -> Status `experiment_running`, row linked.
  announce            One line back into the idea's own Slack thread.

Every write is one status transition, guarded on the status it comes from. A row
that skipped a step is a row whose history is wrong, and the history is the whole
point of the log — so a transition from the wrong state refuses rather than
repairing, and says which step was missed.

Nothing here merges, releases, or creates an experiment itself. The furthest it
goes is *asking* for one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import pathlib
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notion_rest import (  # noqa: E402
    MCPFallback,
    make_rich_text,
    make_select,
    notion,
    rich_text_value,
    select_value,
    title_text,
)


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


def make_relation(page_ids: list[str]) -> dict[str, Any]:
    """A Notion `relation` property value.

    Built here rather than added to `notion_rest.py`: that file is a vendored
    byte-identical copy across 22 skills, and a helper added to one copy is drift
    the drift-check catches in CI. If relations turn out to be wanted elsewhere,
    they go into the canonical copy and propagate to all 22 in one PR.
    """
    return {"relation": [{"id": pid} for pid in page_ids]}


AURIE_FEATURES_DS = "8d14f3eb-0c55-4f25-9322-ab2b2a69f092"
EXPERIMENTS_DS = "c0209685-17ac-49e2-849a-85fa8178decd"

# Where experiments are made. Private, and that is not an obstacle to route
# around: it is where the team already does this, and Viktor posts its results
# back into the same channel, so the request and its outcome stay together.
SUPPORT_BOT_CHANNEL = "C02FQE15YGP"

# Viktor is the orchestrator, not the primitive. Asked in prose, it issues the
# `@AuraBot create experiment …` command AND does the documentation — Mixpanel
# dashboard and the AB sheet. One ask covers both.
#
# Addressing Viktor rather than AuraBot directly is deliberate. AuraBot takes a
# fixed grammar, and a skill that emits a grammar gets it subtly wrong at some
# point — a missing `and`, a variant list that does not match the notes — and a
# malformed command to a parser fails silently. Viktor reads prose and answers
# when something is off, which is the difference between a stuck row and a
# question in a channel somebody is watching.
VIKTOR = "U0AV303B4VB"

# The fallback, and the likely eventual default. Viktor is a paid agent; AuraBot
# is the primitive underneath it and costs nothing per request. Going direct
# gives up the documentation half — Viktor also builds the Mixpanel dashboard and
# the AB-sheet entry — so a direct request has to say so, or the experiment runs
# with nothing to read it in.
#
# It also gives up the error channel: AuraBot parses a fixed grammar and a
# malformed command produces no experiment and no complaint. That is why this is
# the fallback rather than the default, and why `--via aurabot` prints what it is
# giving up rather than quietly substituting.
AURABOT = "U02FZEU0TDH"

# Aurie's `minimum version` is the release the flag ships in; Aura's is its own.
# `targetApp ai` means Aurie and is OMITTED ENTIRELY for Aura — not set to
# something else. Both conventions are read off every real request in the
# channel, not from a spec.
TARGET_APP_AURIE = "ai"

AURA_API = "https://services.aurahealth.io"

# The ship leg. `implementing` covers everything from the first commit to the
# build a person can hold: PR, code review, merge, TestFlight, and every round
# the release gate sends back. Those were four statuses once — in_progress,
# in_review, merged, released — and they tracked pull-request mechanics GitHub
# already tracks better. What the loop needs to know is narrower: is somebody
# building this, and is it live.
IMPLEMENTING = "implementing"
LIVE = "experiment_running"

# Built, shipped, and never seen by anyone.
#
# A release carries several ideas, so one failing its gate cannot hold the build.
# The code goes out and that idea's experiment is simply not turned on — everyone
# sees control. That makes this the NORMAL landing spot for a rejected idea, not
# an edge case.
#
# It is emphatically not a result. Filing it as `lost` would retire a question
# nobody ever asked, which is the failure `learn-idea` refuses `Turned off` for.
DORMANT = "shipped_dormant"
ABANDONED = "abandoned"

# What each status may be written from.
#
# One entry here is a backward move, and it is the only one: an idea that shipped
# dormant can be picked up again, and the code is already in the build, so the
# next round is an edit rather than a rebuild. Everything else moves forward
# only — the row is a log somebody reads months later, and a rewound step still
# reads perfectly plausible, which is why it has to be refused at write time.
REQUIRED_FROM = {
    IMPLEMENTING: {"approved", DORMANT},
    LIVE: {IMPLEMENTING},
    DORMANT: {IMPLEMENTING},
    ABANDONED: {DORMANT},
}

# What a row in each state is actually waiting for. A refusal that says "wrong
# status" sends the reader to the database; one that says which human owes what
# sends them to the thread, which is where the answer is.
WAITING_ON = {
    "pending": "It has not been shared yet — `share-ideas` posts it.",
    "shared": "Nobody has approved it for implementation yet.",
    "approved": "It has not been built yet — `build-idea` starts that.",
    IMPLEMENTING: "It is still being built, or waiting for its PR to merge (the release gate).",
    DORMANT: "It shipped with its experiment off. Iterate it back into the build, or abandon it.",
    ABANDONED: "It was abandoned. Record a successor idea rather than reviving this row.",
    LIVE: "The experiment is running — Yawar and Viktor end it and reach the verdict.",
    "won": "The question is answered.",
    "lost": "The question is answered.",
    "inconclusive": "The question is answered.",
    "rejected": "It was declined at the first gate.",
    "deferred": "It was parked. Re-share it to put it back in front of the team.",
}

# What `plan` should tell you to run. Keyed by status rather than derived from a
# sequence, because the machine branches: `implementing` has three legal next
# moves and which one applies is a human decision, not a position.
NEXT_COMMAND = {
    "approved": "`build-idea start` — this skill picks it up once it is being built.",
    IMPLEMENTING: "merged --post once the PR is merged, then "
                  "request-experiment --post and check-experiment; qa-bug / unwind if QA "
                  "or TestFlight testing holds it back.",
    DORMANT: "iterate (another round) or abandon (stop, and take the flag out).",
}

# Progress order, for reporting only. `shipped_dormant` sits level with
# `implementing` rather than after it: nothing has been learned, and the work
# that produced it may yet be resumed.
SHIP_SEQUENCE = ["approved", IMPLEMENTING, LIVE]

# A version as the release skills produce it: `1.30.0 (1)` or `1.30.0-1`.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\s*(?:\((\d+)\)|-(\d+))?$")


# ---------- resolution ------------------------------------------------------

# An idea is addressed by its immutable, human-friendly `Idea ID` (`42`, `#42`,
# `IDEA-42`). Bounded to ≤6 digits so it can never collide with a 12-hex
# fingerprint, which still resolves for references predating the id.
_IDEA_REF_RE = re.compile(r"^(?:idea[-\s]?|#)?(\d{1,6})$", re.IGNORECASE)


def _ref_idea_num(ref: str) -> Optional[int]:
    """The integer of an Idea-# reference (`42`, `#42`, `IDEA-42`), or None."""
    m = _IDEA_REF_RE.match((ref or "").strip())
    return int(m.group(1)) if m else None


def _row_idea(row: dict[str, Any]) -> tuple[Optional[int], str]:
    """(number, 'IDEA-42') from a row's auto-`Idea ID` property; (None, '') if unset."""
    uid = (row.get("properties", {}).get("Idea ID") or {}).get("unique_id") or {}
    num = uid.get("number")
    if num is None:
        return None, ""
    prefix = uid.get("prefix")
    return num, (f"{prefix}-{num}" if prefix else str(num))


def find_idea(ds_id: str, ref: str) -> dict[str, Any]:
    """Resolve an Idea ID, fingerprint, or title substring to exactly one row.

    Same resolution `build-idea` uses, so an id or fingerprint that worked there
    works here. A title substring that matches several refuses rather than
    guessing — every command downstream writes a status, and writing one to the
    wrong row is not visible until someone reads the log months later.
    """
    rows = []
    for row in notion.query_data_source(ds_id, page_size=100):
        idea_num, idea_id = _row_idea(row)
        rows.append({
            "page_id": row["id"],
            "idea_num": idea_num,
            "idea_id": idea_id,
            "title": title_text(row, "Title"),
            "fingerprint": rich_text_value(row, "Fingerprint"),
            "status": select_value(row, "Status"),
            "screen": rich_text_value(row, "Screen"),
            "domain": select_value(row, "Domain"),
            "description": rich_text_value(row, "Description"),
            "hypothesis": rich_text_value(row, "Hypothesis"),
            "lever": rich_text_value(row, "Lever"),
            "experiment": rich_text_value(row, "Experiment name"),
            "note": rich_text_value(row, "Quick status note"),
            "slack": (row.get("properties", {}).get("Slack thread") or {}).get("url") or "",
            "linear": (row.get("properties", {}).get("Linear issue") or {}).get("url") or "",
            "pr": (row.get("properties", {}).get("PR") or {}).get("url") or "",
            "cleanup_issue": (row.get("properties", {}).get("Cleanup issue") or {}).get("url") or "",
            "experiment_rel": [r["id"] for r in
                               (row.get("properties", {}).get("Experiment") or {}).get("relation", [])],
        })
    num = _ref_idea_num(ref)
    if num is not None:
        hit = [r for r in rows if r["idea_num"] == num]
        if len(hit) == 1:
            return hit[0]
        raise SystemExit(f"no idea has id {num} (IDEA-{num})")
    exact = [r for r in rows if r["fingerprint"] == ref.lower()]
    if len(exact) == 1:
        return exact[0]
    matches = [r for r in rows if ref.lower() in (r["title"] or "").lower()]
    if not matches:
        raise SystemExit(f"no idea matches '{ref}'")
    if len(matches) > 1:
        lines = "\n".join(f"  {r['idea_id'] or r['fingerprint']}  [{r['status']}] {r['title'][:70]}"
                          for r in matches)
        raise SystemExit(f"'{ref}' matched {len(matches)} rows — use an Idea ID:\n{lines}")
    return matches[0]


def plan_check_write(status: str, has_relation: bool, row_found: bool) -> str:
    """What `check-experiment` should do, given a confirmed-live experiment.

    Split out because the three cases are easy to get wrong together, and one of
    them was: the skill told the operator to re-run once `data:sync-experiments`
    had created the Experiments row, but the transition guard refused the second
    run outright — so the documented recovery path could never actually attach
    the relation.

    The relation legitimately arrives *after* the status does. `sync-experiments`
    runs on its own cadence, so a row can be correctly `experiment_running` with
    nothing linked yet, and re-running must be able to finish the job without
    rewriting a status that is already right.

    Returns "transition", "attach", or "noop".
    """
    if status != LIVE:
        return "transition"
    return "noop" if has_relation or not row_found else "attach"


def guard_transition(idea: dict[str, Any], to_status: str) -> Optional[str]:
    """Return a refusal message, or None when the transition is allowed.

    The machine is no longer a straight line, so this reads `REQUIRED_FROM`
    directly rather than comparing positions in a sequence. Position arithmetic
    would have to call `shipped_dormant → implementing` a backward move and
    refuse it, when it is the whole point of shipping dormant: the idea comes
    back for another round with its code already in the build.

    The refusal names what the row is waiting for, because "wrong status" sends
    the reader to the database while "this needs a release decision first" sends
    them to the thread.
    """
    current = idea["status"]
    allowed = REQUIRED_FROM[to_status]
    if current in allowed:
        return None
    if current == to_status:
        return (f"already '{to_status}' — nothing to do. Re-running a ship step is a "
                f"no-op, not an error, but it is also not evidence the step happened "
                f"this time.")
    hint = WAITING_ON.get(current)
    tail = f" {hint}" if hint else ""
    return (f"status is '{current}', and '{to_status}' can only be written from "
            f"{sorted(allowed)}.{tail}")


# ---------- round tracking ---------------------------------------------------
#
# An idea can go through the release gate several times, and "we tried it and it
# was wrong" is unreadable later without knowing which build "it" was. Rounds are
# appended to `Quick status note` rather than given a field of their own: the note
# is already what a human reads first, and a separate field would be a second
# place to look that nobody looks at.
#
# Each round is one line prefixed with a bullet, so counting them is counting
# bullets. Crude, and it survives someone editing the note by hand — which a
# structured field would not.
ROUND_PREFIX = "• "

# The two gate verdicts, written by `release` and `unwind` and read back by
# `request-experiment`. Without that read the gate would be advisory — nothing
# would stop an experiment being requested for a build no human ever opened.
#
# They are worded so that NEITHER CONTAINS THE OTHER. An earlier pair —
# "Approved for release" and "Not approved for release" — made a substring test
# read every rejection as an approval, so an unwound-then-iterated row sailed
# through the gate and asked Viktor to create the experiment anyway. Keep them
# disjoint, and keep the match anchored at the start of the round.
# The build announcement, in the note AND in the thread, word for word. The
# harvester matches TESTFLIGHT_ANCHOR to find where the release gate opened: only
# replies after the newest one answer THIS round. Changing this sentence without
# changing the harvester's `RELEASE_ANCHOR_RE` silently closes the gate.
TESTFLIGHT_ROUND = "Build"
TESTFLIGHT_ANCHOR = "in TestFlight for review"

RELEASE_APPROVED = "Release approved"
RELEASE_DECLINED = "Release declined"


def last_gate_decision(note: str) -> Optional[str]:
    """The MOST RECENT gate verdict: "approved", "declined", or None.

    Newest-first, because an idea can be declined, iterated and approved — or
    approved, iterated and declined. "Has ever been approved" is the wrong
    question; the row's state is whatever the gate said last.

    `iterate` writes neither marker on purpose, so picking a dormant idea back up
    leaves the decline standing. A new round needs a new approval.
    """
    for line in reversed((note or "").splitlines()):
        if not line.startswith(ROUND_PREFIX):
            continue
        body = line[len(ROUND_PREFIX):].lstrip()
        if body.startswith(RELEASE_DECLINED):
            return "declined"
        if body.startswith(RELEASE_APPROVED):
            return "approved"
    return None


def release_approved(note: str) -> bool:
    return last_gate_decision(note) == "approved"


def count_rounds(note: str) -> int:
    return sum(1 for line in (note or "").splitlines() if line.startswith(ROUND_PREFIX))


def append_round(note: str, line: str) -> str:
    """Add one round to the note, keeping every earlier one.

    Never replaces. The history of what was tried and rejected is the expensive
    part of a row — losing it to a status write is how a third attempt repeats
    the first.
    """
    entry = f"{ROUND_PREFIX}{' '.join(line.split())}"
    return f"{note.rstrip()}\n{entry}" if (note or "").strip() else entry


# ---------- the merge is the release approval --------------------------------
#
# The PR is reviewed and merged after QA, so the merge is the release gate.
# `merged` reads that fact off GitHub rather than off a thread reply. A person
# can still hold an idea back after TestFlight testing with `unwind`.

EXPERIMENT_REQUESTED = "Experiment requested"
PR_URL_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
TAG_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
RECENT_VERSIONS = 8


def _gh(args: list[str]) -> Optional[Any]:
    """`gh` JSON output, or None when gh fails. Never raises."""
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def pr_merge_info(pr_url: str) -> Optional[dict[str, Any]]:
    """{'state', 'merged_by', 'merged_at', 'sha'} for a PR, or None when unreadable."""
    data = _gh(["pr", "view", pr_url, "--json", "state,mergedBy,mergedAt,mergeCommit"])
    if not isinstance(data, dict):
        return None
    return {
        "state": data.get("state") or "",
        "merged_by": (data.get("mergedBy") or {}).get("login") or "",
        "merged_at": (data.get("mergedAt") or "")[:10],
        "sha": (data.get("mergeCommit") or {}).get("oid") or "",
    }


def _tag_contains(owner: str, repo: str, sha: str, tag: str) -> Optional[bool]:
    data = _gh(["api", f"repos/{owner}/{repo}/compare/{sha}...{tag}"])
    if not isinstance(data, dict):
        return None
    return data.get("status") in ("ahead", "identical")


def _gh_raw(owner: str, repo: str, path: str, ref: str) -> str:
    """A file's text at `ref`, or '' when gh can't read it."""
    try:
        out = subprocess.run(["gh", "api", f"repos/{owner}/{repo}/contents/{path}?ref={ref}",
                              "-H", "Accept: application/vnd.github.raw"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout if out.returncode == 0 else ""


EXPERIMENTS_SWIFT = "AuraConsumerApp/Services/Experiments/Models/Experiments.swift"
PROJECT_YML = "project.yml"


def onboarding_names(swift: str) -> set[str]:
    """Experiment names declared in `enum Onboarding { … }` — the pre-auth ones."""
    m = re.search(r"enum\s+Onboarding\s*\{", swift or "")
    if not m:
        return set()
    depth, i = 1, m.end()
    while i < len(swift) and depth:
        depth += {"{": 1, "}": -1}.get(swift[i], 0)
        i += 1
    return set(re.findall(r'=\s*"([A-Za-z0-9_]+)"', swift[m.end():i]))


def is_onboarding_experiment(pr_url: str, sha: str, experiment: str) -> Optional[bool]:
    m = PR_URL_RE.search(pr_url or "")
    if not m or not sha:
        return None
    swift = _gh_raw(m.group(1), m.group(2), EXPERIMENTS_SWIFT, sha)
    return (experiment in onboarding_names(swift)) if swift else None


def marketing_version(yml: str) -> str:
    m = re.search(r"MARKETING_VERSION:\s*\"?(\d+\.\d+\.\d+)", yml or "")
    return m.group(1) if m else ""


def release_version_for(pr_url: str, sha: str) -> str:
    """The first app version that carries a merge commit.

    The earliest release tag containing the commit when one exists. Before the
    release is cut, the next patch after the newest tag: every build at or past
    it is cut from a main that already holds the merge. Empty when GitHub can't
    be read — never a guess past that.
    """
    m = PR_URL_RE.search(pr_url or "")
    if not m or not sha:
        return ""
    owner, repo = m.group(1), m.group(2)
    # The tags endpoint orders by name, not version, so read every page.
    try:
        out = subprocess.run(["gh", "api", "--paginate", f"repos/{owner}/{repo}/tags?per_page=100",
                              "--jq", ".[].name"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if out.returncode != 0:
        return ""
    versions: dict[tuple[int, int, int], list[str]] = {}
    for name in out.stdout.split():
        vm = TAG_VERSION_RE.match(name)
        if vm:
            versions.setdefault(tuple(int(x) for x in vm.groups()), []).append(name)
    if not versions:
        return ""
    # Builds of one version, and hotfix tags off a release branch, need not share
    # history, so every tag of the recent versions is checked and the earliest
    # version with any tag carrying the merge wins.
    containing = ""
    for key in sorted(versions, reverse=True)[:RECENT_VERSIONS]:
        for name in versions[key]:
            inside = _tag_contains(owner, repo, sha, name)
            if inside is None:
                return ""
            if inside:
                containing = f"{key[0]}.{key[1]}.{key[2]}"
                break
    return containing or _next_version(owner, repo, sha, max(versions))


def _next_version(owner: str, repo: str, sha: str, newest_tag: tuple[int, int, int]) -> str:
    """The version in git at the merge, or the next patch once that one is tagged.

    `MARKETING_VERSION` in project.yml is the app's current version; a release
    tag for it means it is already cut, so the merge lands in the next patch.
    """
    current = _vtuple(marketing_version(_gh_raw(owner, repo, PROJECT_YML, sha)))
    base = max(current, newest_tag) if current else newest_tag
    if current and current > newest_tag:
        return f"{current[0]}.{current[1]}.{current[2]}"
    return f"{base[0]}.{base[1]}.{base[2] + 1}"


# An experiment assigns nobody until a build at or past its minimum version is on
# the App Store, so "created on the server" is not "live".
APP_STORE_BUNDLE_AURIE = "com.aurahealth.AuraConsumerApp"
EXPERIMENT_CREATED = "Experiment created"


def app_store_version(bundle_id: str = APP_STORE_BUNDLE_AURIE) -> str:
    """The version the App Store serves today, or '' when it can't be read."""
    url = f"https://itunes.apple.com/lookup?bundleId={bundle_id}&country=us"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            results = json.loads(resp.read().decode("utf-8")).get("results") or []
    except (urllib.error.URLError, OSError, ValueError):
        return ""
    return (results[0].get("version") or "") if results else ""


def _vtuple(v: str) -> Optional[tuple[int, ...]]:
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def released(store: str, minimum: str) -> Optional[bool]:
    """Whether the App Store version has reached the minimum. None when either is unreadable."""
    a, b = _vtuple(store), _vtuple(minimum)
    if a is None or b is None:
        return None
    return a >= b


# The release process posts every App Store release to #releases ("New Aurie
# Release / ## v1.31.3 (1) …"). Either that post or the App Store itself says a
# version is out.
RELEASES_CHANNEL = "C0B056LKRAB"
RELEASE_POST_RE = re.compile(r"New Aurie Release.*?##\s*v?(\d+\.\d+\.\d+)", re.S)


def find_release_post(minimum: str, days: int = 60) -> tuple[str, str]:
    """(version, permalink) of the earliest #releases Aurie post at or past `minimum`."""
    want = _vtuple(minimum)
    if want is None:
        return "", ""
    try:
        return _find_release_post(want, days)
    except (Exception, SystemExit):  # noqa: BLE001 — a Slack blip must not block the App Store path
        return "", ""


def _find_release_post(want: tuple[int, ...], days: int) -> tuple[str, str]:
    import time  # noqa: PLC0415
    from slack_post import _api, _token, get_permalink  # noqa: PLC0415
    resp = _api("conversations.history", token=_token(None), http="GET",
                params={"channel": RELEASES_CHANNEL, "limit": 200,
                        "oldest": str(time.time() - days * 86400)})
    hits = []
    for m in resp.get("messages") or [] if resp.get("ok") else []:
        vm = RELEASE_POST_RE.search(m.get("text") or "")
        if vm and _vtuple(vm.group(1)) and _vtuple(vm.group(1)) >= want:
            hits.append((_vtuple(vm.group(1)), vm.group(1), m["ts"]))
    if not hits:
        return "", ""
    _, version, ts = min(hits)
    link = get_permalink(RELEASES_CHANNEL, ts)
    return version, (link.get("permalink") or "") if link.get("ok") else ""


REQUEST_TS_RE = re.compile(r"in #support-bot ts=(\d+\.\d+)")


def request_link(note: str) -> str:
    """Permalink to the newest #support-bot request recorded in the note, or ''."""
    found = REQUEST_TS_RE.findall(note or "")
    if not found:
        return ""
    try:
        from slack_post import get_permalink  # noqa: PLC0415
        resp = get_permalink(SUPPORT_BOT_CHANNEL, found[-1])
    except (Exception, SystemExit):  # noqa: BLE001 — the reply still posts without a link
        return ""
    return (resp.get("permalink") or "") if resp.get("ok") else ""


def created_text(note: str, experiment: str, minimum: str) -> str:
    """The idea-thread reply once Viktor has created the experiment."""
    link = request_link(note)
    where = f" in #support-bot: {link}" if link else " in #support-bot"
    return (f"Experiment `{experiment}` created{where}. It starts assigning once "
            f"{minimum or 'its minimum version'} is on the App Store.")


def experiment_created(note: str) -> bool:
    return _since_last_verdict(note, EXPERIMENT_CREATED)


EXPERIMENT_PINNED = "Experiment pinned"
# Written once the thread has been told the experiment is live, so neither
# `check-experiment` nor `announce` posts the same line twice.
LIVE_ANNOUNCED = "Live announced in thread"


def live_text(experiment: str, version: str, note: str = "", post_url: str = "") -> str:
    """The thread's "it is live" line, linking the #releases post when one is known."""
    text = f"Live as experiment `{experiment or '?'}` in {version or '?'}."
    if not post_url:
        found = re.search(r"https://\S+/archives/" + RELEASES_CHANNEL + r"/p[\w.?=&]+", note or "")
        post_url = found.group(0) if found else ""
    if post_url:
        text += f" Release: {post_url}"
    return text


def announce_live(idea: dict[str, Any], experiment: str, note: str,
                  version: str = "", post_url: str = "") -> bool:
    """Post the live line in the idea's thread once, then stamp the note. True when posted now.

    A Slack failure leaves the note unstamped, so the next `check-experiment` tries again.
    """
    if LIVE_ANNOUNCED in (note or "") or not idea["slack"]:
        return False
    text = live_text(experiment, version or _version_from_note(note), note, post_url)
    try:
        rc = reply_in_thread(idea, text)
    except (SystemExit, Exception) as e:  # noqa: BLE001 — retried on the next tick
        print(f"  live announcement not posted ({e}); the next check-experiment retries.",
              file=sys.stderr)
        return False
    if rc != 0:
        return False
    try:
        notion.update_page_props(idea["page_id"], {"Quick status note": make_rich_text(
            append_round(note, LIVE_ANNOUNCED + "."))})
    except (Exception, SystemExit) as e:  # noqa: BLE001
        print(f"  announced, but the note was not stamped ({e}) — add '{LIVE_ANNOUNCED}' "
              f"by hand so the next check does not post it again.", file=sys.stderr)
    return True


def pinned_arm(record: dict) -> str:
    """The single arm a record is pinned to, or "" when it splits.

    A one-letter `active` gives that arm to every user and ignores per-user sets, so there is
    no control group: the release would ship a rollout, not an experiment.
    """
    active = str((record or {}).get("active") or "").strip()
    return active if len(active) == 1 and active.isalpha() else ""


def pinned_text(experiment: str, arm: str, version: str) -> str:
    return (f"{version} is out, but experiment `{experiment}` is pinned to arm `{arm}` "
            f"(`active: {arm}`), so every user gets `{arm}` and there is no control group. "
            f"It goes live here once `active` is back to the split.")


def _since_last_verdict(note: str, marker: str) -> bool:
    """`marker` recorded since the latest gate verdict. A new verdict starts a new round."""
    for line in reversed((note or "").splitlines()):
        if not line.startswith(ROUND_PREFIX):
            continue
        body = line[len(ROUND_PREFIX):].lstrip()
        if body.startswith(marker):
            return True
        if body.startswith((RELEASE_APPROVED, RELEASE_DECLINED)):
            return False
    return False


def experiment_requested(note: str) -> bool:
    return _since_last_verdict(note, EXPERIMENT_REQUESTED)


QA_DECLINE = f"{RELEASE_DECLINED}: QA found"


def qa_fix_round(note: str) -> bool:
    """The current approval follows a QA decline, and an earlier round already asked.

    Then the experiment exists and only its minimum version moves to the fix's
    release. Any other approval — the first, one after `unwind`, a re-ask with
    `--again` — is a create.
    """
    bodies = [line[len(ROUND_PREFIX):].lstrip() for line in (note or "").splitlines()
              if line.startswith(ROUND_PREFIX)]
    verdicts = [i for i, b in enumerate(bodies)
                if b.startswith((RELEASE_APPROVED, RELEASE_DECLINED))]
    if len(verdicts) < 2 or not bodies[verdicts[-1]].startswith(RELEASE_APPROVED):
        return False
    if not bodies[verdicts[-2]].startswith(QA_DECLINE):
        return False
    return any(b.startswith(EXPERIMENT_REQUESTED) for b in bodies[:verdicts[-2]])


# ---------- the Viktor request ----------------------------------------------

def _sentence(text: str) -> str:
    """One line, ending in exactly one period."""
    body = " ".join((text or "").strip().split())
    return body.rstrip(".").strip() + "." if body else ""


def render_notes(idea: dict[str, Any], arms: Optional[str] = None) -> str:
    """The `notes` clause: a summary sentence, then every arm named.

    Shaped after the real requests in the channel, which all read
    `{summary}. a: {treatment}, b: control` — the summary is what an analyst
    reads first in the AB sheet, and the arm list is what tells them which side
    is the baseline. `b` is always `control`.

    Newlines are collapsed because the command is one line: a raw newline splits
    it and AuraBot parses only the half before it.
    """
    if arms:
        return f"{_sentence(idea['title'])} {' '.join(arms.split())}"
    summary = _sentence(idea["title"])
    treatment = _sentence(idea["description"]).rstrip(".") or "treatment"
    return f"{summary} a: {treatment}, b: control"


def missing_arms(variants: str, arms: Optional[str]) -> list[str]:
    """Variant letters the notes will not describe.

    Auto-generated notes can only name `a` and `b` — nothing on the idea row says
    what a third or fourth arm would be. Requesting `abcd` without spelling them
    out creates an experiment with two undocumented arms, and nobody reading the
    result later can recover what they were. Caught here rather than discovered
    in the readout.
    """
    described = set("ab") if not arms else {c for c in "abcd" if f"{c}:" in arms}
    return [c for c in variants if c not in described]


def _qualifiers(target_app: Optional[str], platform: Optional[str],
                onboarding: bool = False) -> str:
    """` and targeting platform ios and targetApp ai`, or empty.

    Each is omitted **entirely** when absent rather than emitted with a blank or
    a placeholder — that is what every real Aura request in the channel does, and
    `targetApp ` with nothing after it is a parse the grammar has never seen.
    """
    parts = []
    if onboarding:
        # Assigned before sign-in. AuraBot sets targetOnboarding only from the literal
        # `true` (SupportService createExperiment); any other word leaves it unset.
        parts.append("targeting onboarding true")
        if platform:
            parts.append(f"platform {platform}")
    elif platform:
        parts.append(f"targeting platform {platform}")
    if target_app:
        parts.append(f"targetApp {target_app}")
    return "".join(f" and {q}" for q in parts)


def render_aurabot_command(experiment: str, notes: str, version: str,
                           target_app: Optional[str], platform: Optional[str],
                           variants: str, onboarding: bool = False) -> str:
    """The exact grammar AuraBot parses. One line, no backticks, no markdown.

    Reproduced from the real requests in `#support-bot` rather than from a spec —
    there isn't one. Formatting it is not cosmetic: AuraBot reads the literal
    text, so a backticked name or a wrapped line is a different command.
    """
    return (f"<@{AURABOT}> create experiment {experiment} with notes {notes} "
            f"and variants {variants} and minimum version {version}"
            f"{_qualifiers(target_app, platform, onboarding)}")


def render_request(idea: dict[str, Any], experiment: str, version: str,
                   target_app: Optional[str], platform: Optional[str],
                   variants: str, via: str = "viktor",
                   arms: Optional[str] = None, onboarding: bool = False) -> str:
    """The ask. `via='viktor'` (default) is prose; `via='aurabot'` is the grammar.

    Viktor is the default because it reads prose and answers when a request is
    off, and because it does the documentation half in the same breath. AuraBot
    is the fallback for when Viktor is unavailable — and is the likely eventual
    default if Viktor is retired on cost, at which point the documentation has to
    be picked up somewhere else.
    """
    notes = render_notes(idea, arms)
    if via == "aurabot":
        return render_aurabot_command(experiment, notes, version, target_app, platform, variants,
                                      onboarding)

    lines = [
        f"<@{VIKTOR}> please create experiment `{experiment}` with notes {notes} "
        f"and variants {variants} and minimum version {version}"
        f"{_qualifiers(target_app, platform, onboarding)}",
        "",
        f"Surface: `{idea['screen'] or '?'}` (domain: {idea['domain'] or '?'}). "
        f"Primary metric: {idea['lever'] or '?'}.",
    ]
    if idea["hypothesis"]:
        lines.append(f"Hypothesis: {' '.join(idea['hypothesis'].split())}")
    refs = [r for r in (idea["linear"], idea["pr"], idea["slack"]) if r]
    if refs:
        lines.append("Refs: " + " · ".join(refs))
    return "\n".join(lines)


# ---------- the Aura API read -----------------------------------------------

def aura_token() -> Optional[str]:
    """Bearer for the authenticated experiment read.

    `GET /experiments/{name}` is authenticated: 200 with a valid token, 401 with
    a missing or invalid one, 403 with a valid token of the wrong role. So
    unlike the *set* route, a missing token here fails loudly, which is what
    makes this safe to poll.
    """
    return os.environ.get("AURA_API_TOKEN") or os.environ.get("AURA_ADMIN_TOKEN")


def experiment_exists(name: str) -> tuple[Optional[bool], str]:
    exists, detail, _ = experiment_record(name)
    return exists, detail


def experiment_record(name: str) -> tuple[Optional[bool], str, dict]:
    """(exists, detail, record). `None` means the question could not be answered.

    The tri-state matters more than it looks. "Could not check" must never read
    as "not there yet": the caller flips a status on a definite yes and waits on
    a definite no, and a network blip collapsed into either one produces a row
    that is silently wrong.
    """
    token = aura_token()
    if not token:
        return None, "AURA_API_TOKEN is not set — cannot check whether the experiment exists.", {}
    req = urllib.request.Request(
        f"{AURA_API}/experiments/{name}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent(), "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
        try:
            record = json.loads(body)
        except ValueError:
            record = {}
        return True, f"HTTP {resp.status} — {body[:200]}", record if isinstance(record, dict) else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "HTTP 404 — not created yet.", {}
        if e.code in (401, 403):
            return None, (f"HTTP {e.code} — the token was rejected. This read is "
                          f"authenticated, so a rejected token means 'unknown', not 'absent'."), {}
        return None, f"HTTP {e.code} — {e.reason}", {}
    except Exception as e:  # noqa: BLE001 — any transport failure is 'unknown'
        return None, f"{type(e).__name__}: {e}", {}


def live_minimum(note_minimum: str, server_minimum: str) -> str:
    """The version the experiment actually starts at: the higher of the row's and the server's.

    Viktor may set the record past the version the row expected — the release that
    carries the flag moved on — and users below the server's minimum are never assigned.
    """
    a, b = _vtuple(note_minimum), _vtuple(server_minimum or "")
    if b is None:
        return note_minimum
    if a is None or b > a:
        return server_minimum
    return note_minimum


def find_experiment_row(name: str) -> Optional[str]:
    """Page id of the Learnings/Experiments row for `name`, if it exists yet.

    `data:sync-experiments` creates these on its own cadence, so the row may not
    be there the moment the experiment is. Absent is normal and not an error —
    the relation is set on a later run.
    """
    target = (name or "").strip().lower()
    for row in notion.query_data_source(EXPERIMENTS_DS, page_size=100):
        if title_text(row, "Title").strip().lower() == target:
            return row["id"]
    return None


# ---------- commands --------------------------------------------------------

def _require_notion() -> None:
    if not notion.available:
        raise MCPFallback("NOTION_TOKEN is not set.")


def cmd_plan(args: argparse.Namespace) -> int:
    _require_notion()
    idea = find_idea(args.data_source_id, args.ref)
    print(f"Idea       : {idea['title']}")
    print(f"Fingerprint: {idea['fingerprint']}")
    print(f"Status     : {idea['status']}")
    print(f"Experiment : {idea['experiment'] or '-'}")
    print(f"PR         : {idea['pr'] or '-'}")
    print(f"Linear     : {idea['linear'] or '-'}")

    rounds = count_rounds(idea["note"])
    if rounds:
        print(f"Rounds     : {rounds}")

    nxt = NEXT_COMMAND.get(idea["status"])
    if nxt is None:
        print(f"\n{WAITING_ON.get(idea['status'], 'Nothing for this skill to do.')}")
        return 0
    print(f"\nNext: {nxt}")
    if idea["status"] == IMPLEMENTING and not idea["experiment"]:
        print("  ! no Experiment name on the row — set it before requesting one.")
    if idea["status"] == IMPLEMENTING and not release_approved(idea["note"]):
        print("  ! no release approval yet — it is recorded by `merged` when the PR merges.")
    return 0


def cmd_testflight(args: argparse.Namespace) -> int:
    """Record the build the release gate will be asked to judge.

    Writes no status — the row is already `implementing` and stays there. What it
    records is which build a review round was against, because an idea can go
    round several times and "we tried it and it was wrong" is unreadable later
    without knowing which build "it" was.

    With `--post` it also announces the build in the idea's thread, and that post
    is what OPENS the release gate: the harvester reads only replies newer than
    the most recent announcement, so an `iterate` round's verdict cannot be
    confused with the previous round's. Without the post the gate stays shut.
    """
    _require_notion()
    if not VERSION_RE.match(args.version.strip()):
        print(f"REFUSED: '{args.version}' is not a release version. Expected `1.30.0 (1)` "
              f"or `1.30.0-1` — the shape the release skills produce.\n"
              f"  A later reader uses this to find the build, so free text makes the row "
              f"useless for that.", file=sys.stderr)
        return 2
    idea = find_idea(args.data_source_id, args.ref)
    if idea["status"] != IMPLEMENTING:
        print(f"REFUSED: status is '{idea['status']}', not '{IMPLEMENTING}'. "
              f"{WAITING_ON.get(idea['status'], '')}", file=sys.stderr)
        return 2
    version = args.version.strip()
    rounds = count_rounds(idea["note"]) + 1
    note = append_round(idea["note"], f"{TESTFLIGHT_ROUND} {version} {TESTFLIGHT_ANCHOR} (round {rounds}).")
    notion.update_page_props(idea["page_id"], {"Quick status note": make_rich_text(note)})
    print(f"= {idea['fingerprint']} still {IMPLEMENTING}, round {rounds} on {version}")

    text = (f"{TESTFLIGHT_ROUND} {version} {TESTFLIGHT_ANCHOR} (round {rounds}).\n"
            f"Reply *I approve for release* or *not approved for release because …*. "
            f"A reaction is not a decision.")
    print(text)
    if not args.post:
        print("\n-- not posted. Add --post to reply in the thread. Until the build is "
              "announced there, the release gate has nothing to read. --", file=sys.stderr)
        return 0

    if not idea["slack"]:
        print("REFUSED: the row has no Slack thread — nothing to reply to.", file=sys.stderr)
        return 2
    parsed = parse_permalink(idea["slack"])
    if not parsed:
        print(f"REFUSED: could not read a channel and timestamp out of {idea['slack']}. "
              f"Expected a Slack archives permalink.", file=sys.stderr)
        return 2
    channel, ts = parsed

    from slack_post import post_message  # noqa: PLC0415
    resp = post_message(channel=channel, text=text, thread_ts=ts,
                        footer_skill="product:ship-idea")
    if not resp.get("ok"):
        print(f"\nSlack refused the post: {resp.get('error')}", file=sys.stderr)
        return 2
    print(f"\nreplied in thread ts={resp.get('ts')}", file=sys.stderr)
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    """The release gate said yes. Only records the decision — the experiment is
    requested separately and `check-experiment` is what confirms it is live.

    Deliberately does not write `experiment_running`. An idea is live when an
    experiment is actually assigning users, not when somebody approved it, and
    collapsing those two would mark a row live while the request sat unanswered
    in `#support-bot`.
    """
    _require_notion()
    idea = find_idea(args.data_source_id, args.ref)
    if idea["status"] != IMPLEMENTING:
        print(f"REFUSED: status is '{idea['status']}', not '{IMPLEMENTING}'. "
              f"{WAITING_ON.get(idea['status'], '')}", file=sys.stderr)
        return 2
    who = f" ({args.by})" if args.by else ""
    pr = f" ({idea['pr']})" if idea["pr"] else ""
    note = append_round(idea["note"], f"{RELEASE_APPROVED}{who}{pr}. {args.note or ''}".strip())
    notion.update_page_props(idea["page_id"], {"Quick status note": make_rich_text(note)})
    print(f"= {idea['fingerprint']} approved for release — still {IMPLEMENTING}")
    print("  Next: request-experiment --post, then check-experiment. The row goes live when "
          "the experiment does, not when the gate passed.")

    text = f"Approved for release{who}."
    if args.note:
        text += f" {args.note}"
    if args.release_post_url:
        # Link the existing build / release post rather than re-announcing it — the
        # release process already posts to #qa-…-mobile / #releases.
        text += f"\nBuild / release post: {args.release_post_url}"
    print("\n" + text)
    if not args.post:
        print("\n-- not posted. Add --post to reply the verdict in the thread. The people "
              "who were asked to try the build never learn what was concluded otherwise. --",
              file=sys.stderr)
        return 0
    return reply_in_thread(idea, text)


# The feature card's state belongs to the release process: Done means a TestFlight
# build carries the work (the Mobile team's convention). The loop only reads it, and
# once the card is Done swaps the thread's 🚀 for ✅ — the same moment Linear's Slack
# link marks the thread.
_CARD_IDENT_RE = re.compile(r"/issue/([A-Z]+-\d+)", re.I)


def card_done(card_url: str) -> Optional[bool]:
    """Whether the idea's card is in a completed state. None when it can't be read."""
    m = _CARD_IDENT_RE.search(card_url or "")
    if not m:
        return None
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linear_api.py")
    try:
        got = subprocess.run([sys.executable, script, "get-issue", "--id", m.group(1).upper()],
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        state = (json.loads(got.stdout or "{}").get("state") or {}) if got.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return (state.get("type") == "completed") if state else None


def mark_if_done(idea: dict[str, Any]) -> None:
    """🚀 → ✅ on the idea's thread once its card is Done. Idempotent."""
    if card_done(idea["linear"]):
        from thread_marks import mark_thread  # noqa: PLC0415
        print(f"  card Done — thread: {mark_thread(idea['slack'], 'done')}")

def cmd_merged(args: argparse.Namespace) -> int:
    """The PR merged — record it as the release approval, with the version it ships in."""
    _require_notion()
    idea = find_idea(args.data_source_id, args.ref)
    if idea["status"] != IMPLEMENTING:
        print(f"REFUSED: status is '{idea['status']}', not '{IMPLEMENTING}'. "
              f"{WAITING_ON.get(idea['status'], '')}", file=sys.stderr)
        return 2
    if release_approved(idea["note"]):
        print(f"= {idea['fingerprint']} already approved for release — nothing to record.")
        mark_if_done(idea)
        return 0
    pr_url = args.pr or idea["pr"]
    if last_gate_decision(idea["note"]) == "declined" and pr_url and pr_url in idea["note"]:
        print(f"REFUSED: the last verdict is a decline and {pr_url} is the PR already approved "
              f"before it. The fix PR's merge approves the next round — put it on the row "
              f"(build-idea finish) or pass --pr.", file=sys.stderr)
        return 2
    if not pr_url:
        print("REFUSED: no PR on the row — pass --pr.", file=sys.stderr)
        return 2
    info = pr_merge_info(pr_url)
    if info is None:
        print(f"REFUSED: could not read {pr_url} with gh.", file=sys.stderr)
        return 2
    if info["state"] != "MERGED":
        print(f"= {idea['fingerprint']} PR is {info['state'].lower() or 'unknown'} — the "
              f"release gate passes on merge. Nothing recorded.")
        return 3
    version = args.version or release_version_for(pr_url, info["sha"])
    if not version:
        print("REFUSED: could not work out the release the merge ships in (GitHub tags "
              "unreadable). Nothing recorded, so the next run retries; or pass --version.",
              file=sys.stderr)
        return 2
    by = f"PR merged by {info['merged_by']}" if info["merged_by"] else "PR merged"
    when = f" on {info['merged_at']}" if info["merged_at"] else ""
    ships = f" Ships in {version}."
    text = (f"{by} — that is the release approval.{ships} Asking Viktor for the experiment "
            f"next. To hold it back after TestFlight testing, say so here.")
    print(text)
    if not args.post:
        # A dry run writes nothing: a recorded approval makes the next run a no-op, so
        # writing here would swallow the thread reply the --post run owes.
        print("\n-- dry run, nothing written. Add --post to record it and reply in the "
              "thread. --", file=sys.stderr)
        return 0
    rc = reply_in_thread(idea, text)
    if rc:
        return rc
    note = append_round(idea["note"], f"{RELEASE_APPROVED} ({by}{when}; {pr_url}).{ships}")
    notion.update_page_props(idea["page_id"], {"Quick status note": make_rich_text(note)})
    print(f"= {idea['fingerprint']} approved for release by merge{ships}")
    mark_if_done(idea)
    print("  Next: request-experiment --post.")
    return 0


def cmd_qa_bug(args: argparse.Namespace) -> int:
    """QA found a bug in the build: back to the build for a fix PR. The experiment stays."""
    _require_notion()
    idea = find_idea(args.data_source_id, args.ref)
    if idea["status"] != IMPLEMENTING:
        print(f"REFUSED: status is '{idea['status']}', not '{IMPLEMENTING}'.", file=sys.stderr)
        return 2
    if not args.reason:
        print("REFUSED: --reason is required — what QA found.", file=sys.stderr)
        return 2
    text = (f"QA found a bug: {_sentence(args.reason)} Back to the build. The fix PR's merge "
            f"approves the next round, and Viktor is asked to raise the experiment's minimum "
            f"version to the release the fix ships in.")
    print(text)
    if not args.post:
        print("\n-- dry run, nothing written. Add --post to record it and reply. --",
              file=sys.stderr)
        return 0
    rc = reply_in_thread(idea, text)
    if rc:
        return rc
    on_pr = f" (on {idea['pr']})" if idea["pr"] else ""
    note = append_round(idea["note"], f"{QA_DECLINE} a bug{on_pr} — {args.reason}")
    notion.update_page_props(idea["page_id"], {"Quick status note": make_rich_text(note)})
    print(f"= {idea['fingerprint']} back to the build — still {IMPLEMENTING}")
    return 0


def cmd_unwind(args: argparse.Namespace) -> int:
    """The release gate said no. The build ships anyway; this experiment does not.

    A release carries several ideas, so one failing cannot hold it. The code goes
    out and the flag stays off — which is a state, not a result, and must never
    be filed as one.
    """
    _require_notion()
    idea = find_idea(args.data_source_id, args.ref)
    refusal = guard_transition(idea, DORMANT)
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2
    if not args.reason:
        print("REFUSED: --reason is required. An idea that was built and then not shipped is "
              "the most expensive thing the loop produces; the reason is what stops the next "
              "round repeating it.", file=sys.stderr)
        return 2
    on_pr = f" (on {idea['pr']})" if idea["pr"] else ""
    note = append_round(idea["note"], f"{RELEASE_DECLINED}{on_pr}: {args.reason} "
                                      f"Code shipped with the experiment off.")
    notion.update_page_props(idea["page_id"], {
        "Status": make_select(DORMANT),
        "Quick status note": make_rich_text(note),
    })
    print(f"~ {idea['fingerprint']} [{idea['status']} -> {DORMANT}]  {idea['title'][:60]}")
    print("  The release still ships. This experiment stays off — nobody sees the treatment.")
    print("  Next: `iterate` for another round, or `abandon` to take the flag out.")

    # Which mode was taken matters: unwind keeps the code in with the experiment off,
    # so an iterate round is an edit; `abandon` is the mode that removes the code.
    text = (f"Not shipping this experiment: {args.reason} The release still ships — the "
            f"code stays in with the experiment turned off, so nobody sees the treatment. "
            f"It can be picked up for another round (an edit, not a rebuild) or abandoned, "
            f"which takes the code out.")
    print("\n" + text)
    if not args.post:
        print("\n-- not posted. Add --post to reply the verdict in the thread. --",
              file=sys.stderr)
        return 0
    return reply_in_thread(idea, text)


def cmd_iterate(args: argparse.Namespace) -> int:
    """Pick a dormant idea back up. The one legal backward move in the machine."""
    _require_notion()
    idea = find_idea(args.data_source_id, args.ref)
    refusal = guard_transition(idea, IMPLEMENTING)
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2
    note = append_round(idea["note"], f"Iterating: {args.note}" if args.note else "Iterating.")
    notion.update_page_props(idea["page_id"], {
        "Status": make_select(IMPLEMENTING),
        "Quick status note": make_rich_text(note),
    })
    print(f"~ {idea['fingerprint']} [{idea['status']} -> {IMPLEMENTING}]  {idea['title'][:60]}")
    print("  The code is already in the build, so this round is an edit, not a rebuild.")
    return 0


# Where an abandon cleanup card lands. It cannot go through
# `eng:sweep-ended-experiments`: that skill only detects experiments the server
# reports ENDED, and an abandoned idea's experiment may never have run — so the
# loop files this one itself, in the same team and project the sweep uses for
# mobile cleanups, so the two are indistinguishable on the board.
CLEANUP_CARD_TEAM = "5497338e-312b-4ddf-ac5f-3339399c956d"     # Mobile (ENGMOB-)
CLEANUP_CARD_PROJECT = "50b657cd-f184-4068-aec5-a1dbcac49d22"  # Aurie AI - Mobile


def render_cleanup_card(idea: dict[str, Any], reason: str) -> tuple[str, str]:
    """(title, body) for the cleanup card an abandoned idea files.

    Names the experiment, the flag and the treatment path to remove, and a fixed
    checklist — deliberately not file names or line numbers, matching
    `eng:sweep-ended-experiments`: the implementer owns the investigation.
    """
    exp = idea["experiment"] or "<experiment>"
    title = f"Remove {exp} — abandoned before release"
    body = "\n".join([
        f"The `{exp}` experiment was **abandoned at the release gate** and never ran, so "
        f"`eng:sweep-ended-experiments` cannot detect it — the code has to be removed by hand.",
        "",
        f"- **Experiment flag:** `{exp}`",
        f"- **Treatment path:** the `a` branch gated on `{exp}` (control `b`/`nil` is the "
        "current behaviour and stays)",
        f"- **Surface:** `{idea['screen'] or '?'}`",
        f"- **Why abandoned:** {reason}",
        "",
        "## Removal checklist",
        "",
        f"- [ ] Remove the `{exp}` flag and its assignment/exposure",
        "- [ ] Remove the treatment (`a`) path; keep control as the shipped behaviour",
        "- [ ] Remove the analytics exposure event for the flag",
        "- [ ] Protect any sibling experiment that shares this code",
        "- [ ] Lint, unit tests and build green",
        "",
        f"Idea: `{idea.get('idea_id') or idea['fingerprint']}` in Learnings/Loop/Aurie Features Ideas"
        + (f" · Slack thread: {idea['slack']}" if idea['slack'] else ""),
    ])
    return title, body


def file_cleanup_card(title: str, body: str) -> Optional[str]:
    """File one cleanup card via the vendored `linear_api.py`; return its URL or None.

    Reuses the shared Linear client rather than opening a second one (repo
    vendored-copy convention). The input goes through a temp file so a long body
    and the API key never touch the command line.
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linear_api.py")
    payload = {"teamId": CLEANUP_CARD_TEAM, "projectId": CLEANUP_CARD_PROJECT,
               "title": title, "description": body, "priority": 3}
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(payload, fh)
            tmp = fh.name
        out = subprocess.run([sys.executable, script, "create-issue", "--input-file", tmp],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            print(f"  ! cleanup card not filed: {out.stdout.strip() or out.stderr.strip()}",
                  file=sys.stderr)
            return None
        return (json.loads(out.stdout) or {}).get("url")
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        print(f"  ! cleanup card not filed ({exc})", file=sys.stderr)
        return None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _abandon_reason_from_note(note: str) -> str:
    """The reason a prior `abandon` appended to the note, for a cleanup retry."""
    m = re.search(r"Abandoned:\s*(.+?)(?:\n|$)", note or "")
    return m.group(1).strip() if m else ""


def _handle_cleanup(idea: dict[str, Any], reason: str, file_it: bool) -> None:
    """Render, and (with `file_it`) file + link, the cleanup card. Idempotent —
    never files a second card once the row carries a `Cleanup issue`. Filing is
    separated from the status transition so a Linear miss can be retried without
    the forward-only guard refusing an already-abandoned row.
    """
    title, body = render_cleanup_card(idea, reason)
    if idea["cleanup_issue"]:
        print(f"  Cleanup already filed: {idea['cleanup_issue']} — not filing a second.")
    elif not file_it:
        print(f"  Would file a cleanup card: “{title}”. Add --file-cleanup to file it and "
              f"link it on the row.")
    else:
        url = file_cleanup_card(title, body)
        if url:
            notion.update_page_props(idea["page_id"], {"Cleanup issue": {"url": url}})
            print(f"  Cleanup card filed and linked: {url}")
        else:
            print("  Cleanup card was NOT filed — the abandon still stands; re-run "
                  "`abandon <ref> --file-cleanup` once Linear is reachable.", file=sys.stderr)


def cmd_abandon(args: argparse.Namespace) -> int:
    """Stop for good — and say what has to come out of the codebase.

    Abandoning is the one exit that leaves work behind. A status flip alone would
    leave a dormant flag nobody will ever turn on, which is how a codebase
    accumulates branches no one dares delete. `--file-cleanup` turns the printed
    reminder into an actual Linear cleanup card, linked back on the row.
    """
    _require_notion()
    idea = find_idea(args.data_source_id, args.ref)

    # A cleanup retry: a prior run already flipped the status but the Linear card
    # never filed (a token, a 5xx). The forward-only guard would refuse a second
    # `abandon`, so handle it BEFORE the guard — file/link the card and stop, using
    # the reason from the note when it is not re-supplied. No second transition.
    if idea["status"] == ABANDONED:
        reason = args.reason or _abandon_reason_from_note(idea["note"]) or "(see the row note)"
        print(f"= {idea['fingerprint']} already {ABANDONED} — cleanup retry only.")
        _handle_cleanup(idea, reason, args.file_cleanup)
        return 0

    refusal = guard_transition(idea, ABANDONED)
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2
    if not args.reason:
        print("REFUSED: --reason is required. Abandoned is the one terminal state that is not "
              "an answer, so without a reason the next ideation run cannot tell it apart from "
              "a question nobody got to.", file=sys.stderr)
        return 2
    note = append_round(idea["note"], f"Abandoned: {args.reason}")
    notion.update_page_props(idea["page_id"], {
        "Status": make_select(ABANDONED),
        "Quick status note": make_rich_text(note),
    })
    print(f"~ {idea['fingerprint']} [{idea['status']} -> {ABANDONED}]  {idea['title'][:60]}")

    # The flag has to come out, and this is the one trigger the sweep can't cover —
    # the experiment may never have run — so file the cleanup card here, explicitly.
    print()
    _handle_cleanup(idea, args.reason, args.file_cleanup)

    text = (f"Abandoned: {args.reason} The flag and its dead branch come out of the app — "
            f"this is the mode that removes the code, not the one that parks it.")
    print("\n" + text)
    if not args.post:
        print("\n-- not posted. Add --post to reply in the thread. --", file=sys.stderr)
        return 0
    return reply_in_thread(idea, text)


def cmd_request_experiment(args: argparse.Namespace) -> int:
    _require_notion()
    idea = find_idea(args.data_source_id, args.ref)
    experiment = args.experiment or idea["experiment"]
    if not experiment:
        print("REFUSED: no experiment name — pass --experiment or set 'Experiment name' "
              "on the row. Asking for an unnamed experiment gets an unnamed experiment.",
              file=sys.stderr)
        return 2
    if idea["status"] != IMPLEMENTING:
        print(f"REFUSED: status is '{idea['status']}', not '{IMPLEMENTING}'. "
              f"{WAITING_ON.get(idea['status'], '')}", file=sys.stderr)
        return 2
    if not release_approved(idea["note"]):
        print(f"REFUSED: not approved for release. Run `merged --post` once the PR "
              f"is merged (after a QA bug, the fix PR's merge).\n"
              f"  An experiment requested before the gate assigns users to a variant nobody "
              f"has approved.", file=sys.stderr)
        return 2

    if experiment_requested(idea["note"]) and not args.again:
        print(f"= {idea['fingerprint']} experiment already requested — run check-experiment, "
              f"or pass --again to ask a second time.")
        return 0

    version = args.version or _version_from_note(idea["note"])
    if not version:
        print("REFUSED: no release version. Pass --version; it becomes the experiment's "
              "`minimum version`, which is what keeps users on older builds out of the "
              "assignment.", file=sys.stderr)
        return 2

    gaps = missing_arms(args.variants, args.arms)
    if gaps:
        print(f"REFUSED: --variants {args.variants} but the notes would not describe "
              f"{', '.join(gaps)}. Nothing on the idea row says what those arms are, so the "
              f"experiment would run with undocumented variants and no way to recover them "
              f"from the result.\n"
              f"  Pass --arms 'a: …, b: control, c: …' to spell them out.", file=sys.stderr)
        return 2

    onboarding = args.onboarding
    if onboarding is None and idea["pr"]:
        info = pr_merge_info(idea["pr"])
        if info and info["sha"]:
            onboarding = is_onboarding_experiment(idea["pr"], info["sha"], experiment)
    if onboarding is None:
        # The app names the experiments it fetches during onboarding, so assignment does
        # not depend on this flag; it only documents the experiment's context.
        print("  ! could not read Experiments.swift at the merge — requesting without "
              "`targeting onboarding true`; pass --onboarding to add it.", file=sys.stderr)
        onboarding = False

    if qa_fix_round(idea["note"]) and args.via == "viktor":
        text = (f"<@{VIKTOR}> please update experiment `{experiment}` to minimum version "
                f"{version} — QA found a bug in the earlier build and the fix ships in "
                f"{version}, so no one on an older build should be assigned.\n"
                f"Refs: {idea['pr'] or '-'} · {idea['slack'] or '-'}")
    else:
        text = render_request(idea, experiment, version, args.target_app, args.platform,
                              args.variants, via=args.via, arms=args.arms,
                              onboarding=onboarding)
    print(text)
    if args.via == "aurabot":
        print("\n-- via AuraBot directly. Viktor's documentation half (Mixpanel dashboard,\n"
              "   AB-sheet entry) does NOT happen — arrange it separately, or the experiment\n"
              "   runs with nothing to read it in. AuraBot also fails silently on a malformed\n"
              "   command, so check the channel for the confirmation rather than assuming. --",
              file=sys.stderr)
    if not args.post:
        print("\n-- not posted. Add --post to send it to #support-bot. --", file=sys.stderr)
        return 0

    from slack_post import post_message  # noqa: PLC0415 — only needed on the post path

    # Footer on the Viktor ask (prose — a trailing line is fine), but NEVER on
    # the AuraBot grammar: AuraBot parses the literal text, so an appended
    # attribution line is part of the command it tries to read.
    footer = "" if args.via == "aurabot" else "product:ship-idea"
    resp = post_message(channel=SUPPORT_BOT_CHANNEL, text=text, footer_skill=footer)
    if not resp.get("ok"):
        print(f"\nSlack refused the post: {resp.get('error')}", file=sys.stderr)
        return 2
    print(f"\nposted to #support-bot ({SUPPORT_BOT_CHANNEL}) ts={resp.get('ts')}", file=sys.stderr)
    note = append_round(idea["note"], f"{EXPERIMENT_REQUESTED} ({experiment}, minimum version "
                                      f"{version}) in #support-bot ts={resp.get('ts')}.")
    notion.update_page_props(idea["page_id"], {"Quick status note": make_rich_text(note)})
    print("Viktor replies in-channel. Run check-experiment once it has.", file=sys.stderr)
    return 0


# `https://auramobile.slack.com/archives/C0ATUL8T936/p1776464801853489` — the
# `p` timestamp is the ts with the dot removed, so it goes back in 10 digits,
# a dot, then the remaining 6. Slack rejects a ts in any other shape.
PERMALINK_RE = re.compile(r"/archives/(C[A-Z0-9]+)/p(\d{10})(\d{6})")


def parse_permalink(url: str) -> Optional[tuple[str, str]]:
    """(channel_id, thread_ts) from a Slack archives permalink, or None."""
    m = PERMALINK_RE.search(url or "")
    return (m.group(1), f"{m.group(2)}.{m.group(3)}") if m else None


def _version_from_note(note: str) -> str:
    """The MOST RECENT build mentioned in the note.

    Rounds accumulate, so a note that has been through the gate twice names two
    builds. Taking the first would send the **rejected** build as the
    experiment's `minimum version` — enabling it for users on the version that
    was turned down, and excluding the ones who have the fix.
    """
    found = re.findall(r"(\d+\.\d+\.\d+\s*(?:\(\d+\)|-\d+)?)", note or "")
    return found[-1].strip() if found else ""


def cmd_check_experiment(args: argparse.Namespace) -> int:
    _require_notion()
    idea = find_idea(args.data_source_id, args.ref)
    experiment = args.experiment or idea["experiment"]
    if not experiment:
        print("REFUSED: no experiment name on the row.", file=sys.stderr)
        return 2

    exists, detail, record = experiment_record(experiment)
    print(f"{experiment}: {detail}")
    if exists is None:
        print("UNKNOWN — not writing anything. 'Could not check' is not 'not there'.",
              file=sys.stderr)
        return 2
    if not exists:
        print("Not created yet — leaving the row as it is.", file=sys.stderr)
        return 1

    if idea["status"] == IMPLEMENTING and not release_approved(idea["note"]):
        print(f"= {idea['fingerprint']} is not approved for release (the last verdict is a "
              f"decline, or none) — waiting for the fix PR's merge. Nothing written.")
        return 1

    live_version, live_url = "", ""
    if idea["status"] == IMPLEMENTING and args.target_app == TARGET_APP_AURIE:
        minimum = live_minimum(_version_from_note(idea["note"]), str(record.get("minVersion") or ""))
        store = app_store_version()
        post_version, post_url = find_release_post(minimum)
        if (released(store, minimum) or post_version) and pinned_arm(record):
            arm = pinned_arm(record)
            print(f"= released ({post_version or store}) but {experiment} is pinned to arm "
                  f"'{arm}' — not live until `active` is the split. Nothing marked live.")
            if not _since_last_verdict(idea["note"], EXPERIMENT_PINNED):
                rc = reply_in_thread(idea, pinned_text(experiment, arm, post_version or store))
                if rc:
                    return rc
                note = append_round(idea["note"], f"{EXPERIMENT_PINNED} to arm {arm} "
                                                  f"({experiment}) at release.")
                notion.update_page_props(idea["page_id"],
                                         {"Quick status note": make_rich_text(note)})
            mark_if_done(idea)
            return 1
        if released(store, minimum) or post_version:
            live_version, live_url = post_version or store, post_url
            args.note = args.note or (f"Live as experiment {experiment} "
                                      f"(released {post_version or store}"
                                      f"{' — ' + post_url if post_url else ''}).")
        else:
            why = (f"the App Store has {store}" if store else "the App Store version is unreadable")
            print(f"= created on the server, not live — it starts at {minimum or '?'} and {why}. "
                  f"Next: the TestFlight build goes to QA, then the App Store release.")
            if not experiment_created(idea["note"]):
                rc = reply_in_thread(idea, created_text(idea["note"], experiment, minimum))
                if rc:
                    return rc
                note = append_round(idea["note"], f"{EXPERIMENT_CREATED} on the server "
                                                  f"({experiment}); live once {minimum or '?'} "
                                                  f"is on the App Store.")
                notion.update_page_props(idea["page_id"],
                                         {"Quick status note": make_rich_text(note)})
            mark_if_done(idea)
            return 1

    row_id = find_experiment_row(experiment)
    action = plan_check_write(idea["status"], bool(idea["experiment_rel"]), bool(row_id))

    if action in ("noop", "attach"):
        mark_if_done(idea)
        announce_live(idea, experiment, idea["note"])

    if action == "noop":
        print(f"= {idea['fingerprint']} already experiment_running"
              f"{' and linked' if idea['experiment_rel'] else ''} — nothing to do.")
        if not idea["experiment_rel"] and not row_id:
            print("  still no Learnings/Experiments row — `data:sync-experiments` creates it "
                  "on its own cadence; re-run this once it has.")
        return 0

    if action == "attach":
        # The status is already right; only the relation is missing. Writing the
        # status again here would restamp the note and lose what it said.
        notion.update_page_props(idea["page_id"], {"Experiment": make_relation([row_id])})
        print(f"~ {idea['fingerprint']} linked to the Learnings/Experiments row "
              f"(status already experiment_running, left alone)")
        return 0

    refusal = guard_transition(idea, LIVE)
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2

    props: dict[str, Any] = {
        "Status": make_select(LIVE),
        "Experiment name": make_rich_text(experiment),
        "Quick status note": make_rich_text(append_round(
            idea["note"], args.note or f"Live as experiment {experiment}.")),
    }
    if row_id:
        props["Experiment"] = make_relation([row_id])
    notion.update_page_props(idea["page_id"], props)
    print(f"~ {idea['fingerprint']} [{idea['status']} -> experiment_running]  {idea['title'][:60]}")
    announce_live(idea, experiment,
                  append_round(idea["note"], args.note or f"Live as experiment {experiment}."),
                  live_version, live_url)
    mark_if_done(idea)
    if row_id:
        print("  linked to the Learnings/Experiments row")
    else:
        print("  no Learnings/Experiments row yet — `data:sync-experiments` creates it on "
              "its own cadence; re-run this to attach the relation.")
    return 0


def reply_in_thread(idea: dict[str, Any], text: str) -> int:
    """Post `text` as a reply in the idea's own Slack thread. 0 on success, 2 on failure.

    The shape every status-change reply shares: a row with no thread is refused
    (there is nothing the team is watching), the permalink is parsed once, and the
    reply carries the ship-idea footer. A new bot reply is safe next to the release
    gate — the harvester flags bot replies in `normalise_replies`, so this can never
    be misread as a human verdict even when it names a decision.
    """
    if not idea["slack"]:
        print("REFUSED: the row has no Slack thread — nothing to reply to. An idea that "
              "was never shared has no thread the team is watching.", file=sys.stderr)
        return 2
    parsed = parse_permalink(idea["slack"])
    if not parsed:
        print(f"REFUSED: could not read a channel and timestamp out of {idea['slack']}. "
              f"Expected a Slack archives permalink.", file=sys.stderr)
        return 2
    channel, ts = parsed
    from slack_post import post_message  # noqa: PLC0415
    resp = post_message(channel=channel, text=text, thread_ts=ts,
                        footer_skill="product:ship-idea")
    if not resp.get("ok"):
        print(f"\nSlack refused the post: {resp.get('error')}", file=sys.stderr)
        return 2
    print(f"\nreplied in thread ts={resp.get('ts')}", file=sys.stderr)
    return 0


def cmd_announce(args: argparse.Namespace) -> int:
    _require_notion()
    idea = find_idea(args.data_source_id, args.ref)
    if not idea["slack"]:
        print("REFUSED: the row has no Slack thread — nothing to reply to. An idea that "
              "was never shared has no thread the team is watching.", file=sys.stderr)
        return 2
    experiment = args.experiment or idea["experiment"] or "?"
    version = args.version or _version_from_note(idea["note"]) or "?"
    text = live_text(experiment, version, idea["note"])
    print(text)
    if args.post and LIVE_ANNOUNCED in (idea["note"] or ""):
        print("\n= already announced in the thread — not posting it again.", file=sys.stderr)
        return 0
    if not args.post:
        print("\n-- not posted. Add --post to reply in the thread. --", file=sys.stderr)
        return 0

    parsed = parse_permalink(idea["slack"])
    if not parsed:
        print(f"REFUSED: could not read a channel and timestamp out of {idea['slack']}. "
              f"Expected a Slack archives permalink.", file=sys.stderr)
        return 2
    channel, ts = parsed

    from slack_post import post_message  # noqa: PLC0415
    resp = post_message(channel=channel, text=text, thread_ts=ts,
                        footer_skill="product:ship-idea")
    if not resp.get("ok"):
        print(f"\nSlack refused the post: {resp.get('error')}", file=sys.stderr)
        return 2
    print(f"\nreplied in thread ts={resp.get('ts')}", file=sys.stderr)
    if LIVE_ANNOUNCED not in (idea["note"] or ""):
        notion.update_page_props(idea["page_id"], {"Quick status note": make_rich_text(
            append_round(idea["note"], LIVE_ANNOUNCED + "."))})
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ship_idea", description=__doc__.split("\n")[0])
    p.add_argument("--data-source-id", default=AURIE_FEATURES_DS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", help="Where this row is in the ship leg. No writes.")
    sp.add_argument("ref")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("testflight", help="Record the build the release gate will judge")
    sp.add_argument("ref")
    sp.add_argument("--version", required=True, help="e.g. '1.30.0 (1)'")
    sp.add_argument("--post", action="store_true",
                    help="Announce the build in the thread. The release gate stays "
                         "shut until this lands.")
    sp.set_defaults(func=cmd_testflight)

    sp = sub.add_parser("release", help="The release gate said yes")
    sp.add_argument("ref")
    sp.add_argument("--by", help="Who approved it")
    sp.add_argument("--note")
    sp.add_argument("--release-post-url", dest="release_post_url",
                    help="Permalink to the existing build / release post (#qa-…-mobile or "
                         "#releases) to link from the thread. Optional.")
    sp.add_argument("--post", action="store_true",
                    help="Reply the verdict in the idea's thread")
    sp.set_defaults(func=cmd_release)

    sp = sub.add_parser("merged", help="The PR merged — that is the release approval")
    sp.add_argument("ref")
    sp.add_argument("--pr", help="Defaults to the row's PR")
    sp.add_argument("--version", help="Defaults to the first release carrying the merge")
    sp.add_argument("--post", action="store_true", help="Reply in the idea's thread")
    sp.set_defaults(func=cmd_merged)

    sp = sub.add_parser("qa-bug", help="QA found a bug — back to the build for a fix PR")
    sp.add_argument("ref")
    sp.add_argument("--reason", help="What QA found. Required.")
    sp.add_argument("--post", action="store_true", help="Record it and reply in the thread")
    sp.set_defaults(func=cmd_qa_bug)

    sp = sub.add_parser("unwind", help="Gate said no — ships dormant, experiment off")
    sp.add_argument("ref")
    sp.add_argument("--reason", help="Why it is not shipping. Required.")
    sp.add_argument("--post", action="store_true",
                    help="Reply the verdict and which mode was taken in the idea's thread")
    sp.set_defaults(func=cmd_unwind)

    sp = sub.add_parser("iterate", help="Pick a dormant idea back up for another round")
    sp.add_argument("ref")
    sp.add_argument("--note")
    sp.set_defaults(func=cmd_iterate)

    sp = sub.add_parser("abandon", help="Stop for good, and file the cleanup")
    sp.add_argument("ref")
    sp.add_argument("--reason", help="Why it is being dropped. Required.")
    sp.add_argument("--file-cleanup", dest="file_cleanup", action="store_true",
                    help="File the Linear cleanup card and link it on the row "
                         "(default: dry-run, just prints what it would file)")
    sp.add_argument("--post", action="store_true",
                    help="Reply in the idea's thread that it was abandoned")
    sp.set_defaults(func=cmd_abandon)

    sp = sub.add_parser("request-experiment", help="Ask Viktor to create it, in #support-bot")
    sp.add_argument("ref")
    sp.add_argument("--experiment", help="Defaults to the row's Experiment name")
    sp.add_argument("--version", help="Defaults to the version in the row's note")
    sp.add_argument("--variants", default="ab", choices=["ab", "abc", "abcd"])
    sp.add_argument("--platform", default="ios", help="'' to omit")
    sp.add_argument("--target-app", default=TARGET_APP_AURIE,
                    help="'ai' for Aurie; pass '' for Aura, which omits it entirely")
    sp.add_argument("--arms", help="Spell out every arm, e.g. \"a: new copy, b: control, "
                                   "c: no header\". Required for abc/abcd.")
    sp.add_argument("--via", default="viktor", choices=["viktor", "aurabot"],
                    help="viktor (default) asks in prose and gets documentation too; "
                         "aurabot is the fallback and emits the raw grammar")
    sp.add_argument("--onboarding", dest="onboarding", action="store_true", default=None,
                    help="Pre-auth onboarding experiment (default: read Experiments.Onboarding "
                         "at the merge)")
    sp.add_argument("--no-onboarding", dest="onboarding", action="store_false")
    sp.add_argument("--again", action="store_true",
                    help="Ask again although the note records a request")
    sp.add_argument("--post", action="store_true", help="Actually send it")
    sp.set_defaults(func=cmd_request_experiment)

    sp = sub.add_parser("check-experiment",
                        help="Exists and its minimum version is on the App Store -> experiment_running")
    sp.add_argument("ref")
    sp.add_argument("--target-app", default=TARGET_APP_AURIE,
                    help="'' for Aura, whose App Store check is not wired yet")
    sp.add_argument("--experiment")
    sp.add_argument("--note")
    sp.set_defaults(func=cmd_check_experiment)

    sp = sub.add_parser("announce", help="One line back into the idea's Slack thread")
    sp.add_argument("ref")
    sp.add_argument("--experiment")
    sp.add_argument("--version")
    sp.add_argument("--post", action="store_true")
    sp.set_defaults(func=cmd_announce)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MCPFallback as e:
        print(f"ship-idea: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
