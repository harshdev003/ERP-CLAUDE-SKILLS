#!/usr/bin/env python3
"""List the PRs waiting on your review and check each one's Bugbot gate on its head commit.

    triage.py list [--json] [--limit N] [--no-mine] [--no-suggest] [--owner O]... [--suggest-limit N]
    triage.py send-back <pr-url> --head <sha> [--dry-run]
    triage.py remove-self <pr-url> [--dry-run] [--allow-required]

`list` is read-only. Besides the queue it reports two read-only sections: your own open, ready
PRs that no one is reviewing, and other people's Bugbot-clean PRs you could pick up "if you
have time" (unreviewed, or stalled after a Request changes). `send-back` submits a Request-changes review asking the author to run
Bugbot; it re-classifies the PR first and refuses if the head moved or the PR now passes.
`remove-self` withdraws your code-owner review request from a PR another human is already
reviewing; it re-reads the PR and the base branch's rulesets first and refuses when stepping
back would leave the PR without a reviewer or block its merge.
All GitHub access goes through `gh` (its own auth), via the single `_gh` chokepoint.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

BOT_RE = re.compile(r"^cursor(\[bot\])?$", re.I)
CHECK_RE = re.compile(r"bugbot", re.I)
FOOTER_RE = re.compile(r"Cursor Bugbot\]?\(?[^)]*\)? for commit ([0-9a-f]{40})", re.I)
SEARCH_LIMIT = 100
SUGGEST_LIMIT = 50
STALL_DAYS = 2
OWNER_LOOKBACK_DAYS = 90
PAGE = 100

# Statuses, in the order the table shows them. Only the three FAIL ones are sent back.
READY = "ready"
RUNNING = "bugbot_running"
MISSING = "bugbot_missing"
STALE = "bugbot_stale"
FINDINGS = "findings_open"
SENT_BACK = "already_sent_back"
FAIL = (MISSING, STALE, FINDINGS)
ORDER = (FINDINGS, MISSING, STALE, RUNNING, SENT_BACK, READY)

PR_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number title url isDraft state headRefOid baseRefName updatedAt
      author { login }
      reviews(last: %(n)d) {
        nodes { author { __typename login } state submittedAt commit { oid } }
      }
      reviewRequests(first: %(n)d) {
        nodes {
          asCodeOwner
          requestedReviewer {
            __typename
            ... on User { login }
            ... on Team { slug }
            ... on Bot { login }
          }
        }
      }
      reviewThreads(first: %(n)d) {
        pageInfo { hasNextPage }
        nodes {
          isResolved
          comments(first: 1) { nodes { author { login } body originalCommit { oid } } }
        }
      }
      commits(last: 1) {
        nodes { commit { oid committedDate statusCheckRollup { contexts(first: %(n)d) {
          nodes { __typename ... on CheckRun { name status conclusion } }
        } } } }
      }
    }
  }
}
""" % {"n": PAGE}


class GhError(RuntimeError):
    pass


def _gh(args: list[str], stdin: str | None = None) -> str:
    proc = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise GhError(proc.stderr.strip() or f"gh {' '.join(args[:2])} exited {proc.returncode}")
    return proc.stdout


def viewer_login() -> str:
    return _gh(["api", "user", "--jq", ".login"]).strip()


def search_review_requests(limit: int) -> list[dict]:
    """Open, non-draft PRs where you (or a team you're on) are a requested reviewer."""
    out = _gh(["search", "prs", "--review-requested=@me", "--state=open", "--draft=false",
               "--limit", str(limit), "--json", "repository,number,url"])
    return json.loads(out)


def search_my_open_prs(limit: int) -> list[dict]:
    """Your own open, non-draft PRs."""
    out = _gh(["search", "prs", "--author=@me", "--state=open", "--draft=false",
               "--sort=updated", "--order=desc", "--limit", str(limit),
               "--json", "repository,number,url"])
    return json.loads(out)


def search_owner_open_prs(owner: str, limit: int) -> list[dict]:
    """An owner's open, non-draft PRs, most recently updated first."""
    out = _gh(["search", "prs", f"--owner={owner}", "--state=open", "--draft=false",
               "--sort=updated", "--order=desc", "--limit", str(limit), "--json", "url"])
    return json.loads(out)


def recent_authored_owners(now: datetime) -> set[str]:
    """Owners of the repos you opened a PR in over the lookback window.

    Org membership is not used: it includes orgs whose PRs are none of your business.
    """
    since = (now - timedelta(days=OWNER_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    out = _gh(["search", "prs", "--author=@me", f"--created=>={since}", "--limit", "100",
               "--json", "url"])
    return {parse_pr_url(h["url"])[0] for h in json.loads(out)}


def fetch_pr(owner: str, name: str, number: int) -> dict:
    out = _gh(["api", "graphql", "-f", f"query={PR_QUERY}", "-F", f"owner={owner}",
               "-F", f"name={name}", "-F", f"number={number}"])
    data = json.loads(out)
    if data.get("errors"):
        raise GhError("; ".join(e.get("message", "") for e in data["errors"]))
    return data["data"]["repository"]["pullRequest"]


def parse_pr_url(url: str) -> tuple[str, str, int]:
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url.strip())
    if not m:
        raise ValueError(f"not a GitHub PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def _login(node: dict | None) -> str:
    return ((node or {}).get("author") or {}).get("login") or ""


def _is_bot(actor: dict | None) -> bool:
    actor = actor or {}
    login = actor.get("login") or ""
    return actor.get("__typename") == "Bot" or bool(BOT_RE.match(login)) or login.endswith("[bot]")


def _requested_name(reviewer: dict) -> str:
    """`login` for a user, `team:<slug>` for a team, so the two can't collide."""
    if reviewer.get("__typename") == "Team":
        return f"team:{reviewer.get('slug') or ''}"
    return reviewer.get("login") or ""


def code_owner_signal(pr: dict, viewer: str) -> tuple[bool, list[str]]:
    """Pure: is `viewer` requested by name *as a code owner*, and who else is reviewing.

    GitHub sets `asCodeOwner` on the request it creates from CODEOWNERS, so this needs
    neither the CODEOWNERS file nor the timeline (which attributes code-owner requests to
    the PR author). A team request can't be withdrawn for one member, so only a direct
    User request counts. "Someone else" is any other requested user or team, plus any
    human other than the author and the viewer who has already submitted a review —
    submitting a review removes that person from the requested list.
    """
    author = _login(pr)
    requests = (pr.get("reviewRequests") or {}).get("nodes") or []
    mine = any(
        (r.get("requestedReviewer") or {}).get("__typename") == "User"
        and (r.get("requestedReviewer") or {}).get("login") == viewer
        and r.get("asCodeOwner")
        for r in requests
    )
    others = {
        _requested_name(r.get("requestedReviewer") or {})
        for r in requests
        if not _is_bot(r.get("requestedReviewer"))
        and _requested_name(r.get("requestedReviewer") or {}) not in ("", viewer)
    }
    for rv in (pr.get("reviews") or {}).get("nodes") or []:
        login = _login(rv)
        if login and login not in (viewer, author) and not _is_bot(rv.get("author")):
            others.add(login)
    return mine, sorted(others)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def human_reviewers(pr: dict) -> tuple[list[str], dict[str, list[dict]]]:
    """Pure: requested reviewers and each human's submitted reviews, bots and the author excluded."""
    author = _login(pr)
    requested = sorted({
        _requested_name(r.get("requestedReviewer") or {})
        for r in (pr.get("reviewRequests") or {}).get("nodes") or []
        if not _is_bot(r.get("requestedReviewer"))
        and _requested_name(r.get("requestedReviewer") or {}) not in ("", author)
    })
    reviewed: dict[str, list[dict]] = {}
    for rv in (pr.get("reviews") or {}).get("nodes") or []:
        login = _login(rv)
        if login and login != author and not _is_bot(rv.get("author")):
            reviewed.setdefault(login, []).append(rv)
    return requested, reviewed


def head_committed_at(pr: dict) -> datetime | None:
    commits = (pr.get("commits") or {}).get("nodes") or []
    return _parse_ts(commits[0]["commit"].get("committedDate")) if commits else None


def stalled_reviewers(pr: dict, now: datetime, days: int = STALL_DAYS) -> list[dict]:
    """Pure: reviewers who requested changes, then went quiet after the author pushed.

    A reviewer is stalled when their latest decisive review (approve / request changes /
    dismissed) is Request changes, the head commit is newer than *every* review they
    submitted, and that commit is at least `days` old. A thread reply after the push is a
    COMMENTED review, so a reviewer still talking it through is not stalled.
    """
    head_at = head_committed_at(pr)
    if not head_at or now - head_at < timedelta(days=days):
        return []
    _, reviewed = human_reviewers(pr)
    out = []
    for login, revs in reviewed.items():
        decisive = [r for r in revs if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")]
        latest_decisive = max(decisive, key=lambda r: r.get("submittedAt") or "", default=None)
        latest_any = max((_parse_ts(r.get("submittedAt")) for r in revs if r.get("submittedAt")),
                         default=None)
        if (latest_decisive and latest_decisive.get("state") == "CHANGES_REQUESTED"
                and latest_any and latest_any < head_at):
            out.append({"reviewer": login, "days": (now - head_at).days})
    return sorted(out, key=lambda r: r["reviewer"])


def unreviewed_row(pr: dict, viewer: str) -> dict | None:
    """Pure: the gate row for a PR nobody has been asked to review or has reviewed; else None."""
    requested, reviewed = human_reviewers(pr)
    if requested or reviewed:
        return None
    return classify(pr, viewer)


def suggestion_row(pr: dict, viewer: str, now: datetime, days: int = STALL_DAYS) -> dict | None:
    """Pure: an "if you have time" row for someone else's PR, or None.

    Only Bugbot-clean PRs qualify, so no suggestion spends review time the gate would refuse.
    A PR requesting you by name is your queue, not a suggestion.
    """
    row = classify(pr, viewer)
    if row["author"] == viewer or row["status"] != READY:
        return None
    requested, reviewed = human_reviewers(pr)
    if viewer in requested:
        return None
    if not requested and not reviewed:
        return {**row, "why": "no_reviewer", "reviewers": [], "stalled_days": 0}
    stalled = stalled_reviewers(pr, now, days)
    if stalled:
        return {**row, "why": "stalled", "reviewers": [s["reviewer"] for s in stalled],
                "stalled_days": max(s["days"] for s in stalled)}
    return None


def code_owner_review_required(owner: str, name: str, base: str) -> bool | None:
    """True when a ruleset on `base` requires code-owner review; None when unreadable."""
    branch = urllib.parse.quote(base or "", safe="")
    # The listing is paged; a rule on a later page must not read as "not required".
    # `--jq '.[]'` flattens every page into one compact JSON rule per line.
    try:
        out = _gh(["api", "--paginate", f"repos/{owner}/{name}/rules/branches/{branch}?per_page=100",
                   "--jq", ".[]"])
        rules = [json.loads(line) for line in out.splitlines() if line.strip()]
    except (GhError, ValueError):
        return None
    return any(r.get("type") == "pull_request"
               and (r.get("parameters") or {}).get("require_code_owner_review")
               for r in rules)


def _finding_commit(comment: dict) -> str:
    """The commit Bugbot raised the finding on — never the re-anchored `commit`."""
    oid = (comment.get("originalCommit") or {}).get("oid")
    if oid:
        return oid
    m = FOOTER_RE.search(comment.get("body") or "")
    return m.group(1) if m else ""


def classify(pr: dict, viewer: str) -> dict:
    """Pure: one GraphQL pullRequest node → gate verdict for its current head."""
    head = pr["headRefOid"]
    reviews = (pr.get("reviews") or {}).get("nodes") or []
    threads_conn = pr.get("reviewThreads") or {}
    threads = threads_conn.get("nodes") or []
    commits = (pr.get("commits") or {}).get("nodes") or []
    rollup = (commits[0]["commit"].get("statusCheckRollup") if commits else None) or {}
    checks = [c for c in ((rollup.get("contexts") or {}).get("nodes") or [])
              if c.get("__typename") == "CheckRun" and CHECK_RE.search(c.get("name") or "")]

    bot_reviews = [r for r in reviews if BOT_RE.match(_login(r))]
    bot_on_head = [r for r in bot_reviews if (r.get("commit") or {}).get("oid") == head]
    check_done = any(c.get("status") == "COMPLETED" for c in checks)
    check_running = any(c.get("status") not in (None, "COMPLETED") for c in checks)

    open_head, open_older = 0, 0
    for t in threads:
        first = ((t.get("comments") or {}).get("nodes") or [{}])[0]
        if t.get("isResolved") or not BOT_RE.match(_login(first)):
            continue
        if _finding_commit(first) == head:
            open_head += 1
        else:
            open_older += 1

    mine_on_head = [r for r in reviews
                    if _login(r) == viewer and (r.get("commit") or {}).get("oid") == head]
    latest_mine = max(mine_on_head, key=lambda r: r.get("submittedAt") or "", default=None)

    if latest_mine and latest_mine.get("state") == "CHANGES_REQUESTED":
        status = SENT_BACK
    elif check_running:
        status = RUNNING  # findings can still land; never judge a run in flight
    elif not bot_on_head and not check_done:
        status = STALE if (bot_reviews or checks) else MISSING
    elif open_head:
        status = FINDINGS
    else:
        status = READY

    code_owner, others = code_owner_signal(pr, viewer)

    return {
        "url": pr["url"],
        "number": pr["number"],
        "title": pr["title"],
        "author": _login(pr),
        "head": head,
        "is_draft": pr.get("isDraft", False),
        "status": status,
        "open_findings_on_head": open_head,
        "open_findings_older": open_older,
        "bugbot_reviewed_head": bool(bot_on_head or check_done),
        "threads_truncated": bool((threads_conn.get("pageInfo") or {}).get("hasNextPage")),
        "base": pr.get("baseRefName") or "",
        "code_owner_request": code_owner,
        "other_reviewers": others,
        "step_back": code_owner and bool(others),
        # Filled in by `triage` for step-back candidates only; None means unread/unknown.
        "code_owner_review_required": None,
    }


def _repo(url: str) -> str:
    owner, name, _ = parse_pr_url(url)
    return f"{owner}/{name}"


def _live(url: str) -> dict | None:
    """The live PR node, or None when it turned draft or closed since the search indexed it."""
    owner, name, number = parse_pr_url(url)
    pr = fetch_pr(owner, name, number)
    if pr.get("isDraft") or pr.get("state") != "OPEN":
        return None
    return pr


def triage(limit: int, mine: bool = True, suggest: bool = True, owners: list[str] | None = None,
           suggest_limit: int = SUGGEST_LIMIT) -> dict:
    viewer = viewer_login()
    now = datetime.now(timezone.utc)
    hits = search_review_requests(limit)
    rows, errors = [], []
    required: dict[tuple[str, str, str], bool | None] = {}
    for h in hits:
        try:
            owner, name, number = parse_pr_url(h["url"])
            pr = fetch_pr(owner, name, number)
            if pr.get("isDraft") or pr.get("state") != "OPEN":
                continue  # search index lag; the live node is authoritative
            row = classify(pr, viewer)
            if row["step_back"]:
                key = (owner, name, row["base"])
                if key not in required:
                    required[key] = code_owner_review_required(*key)
                row["code_owner_review_required"] = required[key]
            rows.append(row)
        except (GhError, ValueError, KeyError, TypeError) as exc:
            errors.append({"url": h.get("url"), "error": str(exc)})
    rows.sort(key=lambda r: (ORDER.index(r["status"]), _repo(r["url"]), r["number"]))
    counts = {s: sum(r["status"] == s for r in rows) for s in ORDER}

    mine_rows, mine_hits = [], []
    if mine or suggest:
        mine_hits = search_my_open_prs(limit)
    if mine:
        for h in mine_hits:
            try:
                pr = _live(h["url"])
                row = unreviewed_row(pr, viewer) if pr else None
                if row:
                    mine_rows.append(row)
            except (GhError, ValueError, KeyError, TypeError) as exc:
                errors.append({"url": h.get("url"), "error": str(exc)})
        mine_rows.sort(key=lambda r: (ORDER.index(r["status"]), _repo(r["url"]), r["number"]))

    suggestions, suggest_owners, suggest_capped = [], [], False
    if suggest:
        if owners:
            suggest_owners = sorted(set(owners))
        else:
            found = {parse_pr_url(h["url"])[0] for h in hits + mine_hits}
            try:
                found |= recent_authored_owners(now)
            except (GhError, ValueError, KeyError) as exc:
                errors.append({"url": "search author:@me", "error": str(exc)})
            suggest_owners = sorted(found)
        queued = {r["url"] for r in rows}
        for owner in suggest_owners:
            try:
                found_prs = search_owner_open_prs(owner, suggest_limit)
            except (GhError, ValueError) as exc:
                errors.append({"url": f"search owner:{owner}", "error": str(exc)})
                continue
            suggest_capped = suggest_capped or len(found_prs) >= suggest_limit
            for h in found_prs:
                if h["url"] in queued:
                    continue
                try:
                    pr = _live(h["url"])
                    row = suggestion_row(pr, viewer, now) if pr else None
                    if row:
                        suggestions.append(row)
                except (GhError, ValueError, KeyError, TypeError) as exc:
                    errors.append({"url": h.get("url"), "error": str(exc)})
        suggestions.sort(key=lambda r: (r["why"] != "no_reviewer", -r["stalled_days"],
                                        _repo(r["url"]), r["number"]))

    return {"viewer": viewer, "rows": rows, "counts": counts, "errors": errors,
            "step_back": sum(r["step_back"] for r in rows),
            "search_capped": len(hits) >= limit, "limit": limit,
            "mine": mine_rows if mine else None,
            "mine_capped": mine and len(mine_hits) >= limit,
            "suggestions": suggestions if suggest else None,
            "suggest_owners": suggest_owners, "suggest_capped": suggest_capped,
            "suggest_limit": suggest_limit}


LABEL = {
    FINDINGS: "❌ Bugbot findings open",
    MISSING: "❌ Bugbot never ran",
    STALE: "❌ Bugbot not on latest commit",
    RUNNING: "⏳ Bugbot running",
    SENT_BACK: "↩️ already sent back",
    READY: "✅ Ready",
}


MINE_LABEL = {**LABEL, READY: "✅ Bugbot clean"}

TABLE = ["| PR | Description | Status | Link |", "|---|---|---|---|"]


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _row(r: dict, description: str, status: str | None = None) -> str:
    """One table row: short `repo#number`, description, optional status, link."""
    pr = f"{parse_pr_url(r['url'])[1]}#{r['number']}"
    cells = [pr, _cell(description)] + ([_cell(status)] if status is not None else [])
    return "| " + " | ".join(cells + [f"[link]({r['url']})"]) + " |"


def _queue_status(r: dict) -> str:
    notes = []
    if r["open_findings_on_head"]:
        notes.append(f"{r['open_findings_on_head']} open on latest commit")
    if r["open_findings_older"]:
        notes.append(f"{r['open_findings_older']} unresolved from older commits")
    if r["threads_truncated"]:
        notes.append(f"⚠️ >{PAGE} threads, counts partial")
    if r["step_back"]:
        also = ", ".join(o if o.startswith("team:") else f"@{o}" for o in r["other_reviewers"])
        notes.append(f"👥 code owner; also reviewing: {also}")
        if r["code_owner_review_required"]:
            notes.append(f"⚠️ `{r['base']}` requires code-owner review")
        elif r["code_owner_review_required"] is None:
            notes.append(f"⚠️ couldn't read `{r['base']}` rulesets")
    return "; ".join([LABEL[r["status"]]] + notes)


def render_queue(result: dict) -> list[str]:
    rows = result["rows"]
    counts = ", ".join(f"{LABEL[s]}: {n}" for s, n in result["counts"].items() if n)
    lines = ["### Assigned to you", "",
             f"**{len(rows)} PR(s) awaiting review by @{result['viewer']}**"
             + (f" — {counts}" if counts else ""), ""]
    if rows:
        lines += TABLE + [_row(r, f"{r['title']} (@{r['author']})", _queue_status(r)) for r in rows]
    else:
        lines.append("Nothing is waiting on your review.")
    if result.get("step_back"):
        lines += ["", f"👥 {result['step_back']} PR(s) requested you as code owner while someone "
                  "else is already reviewing — candidates to step back from."]
    if result["search_capped"]:
        lines += ["", f"⚠️ Search returned the {result['limit']}-result cap; the queue may be longer."]
    return lines


def render_mine(result: dict) -> list[str]:
    """Omitted when empty, unless the search was capped — then the cap is the news."""
    rows, capped = result["mine"], result.get("mine_capped")
    if not rows and not capped:
        return []
    lines = ["", "### Your PRs with no reviewer", ""]
    if not rows:
        return lines + [f"⚠️ None among your {result['limit']} most recently updated open PRs; "
                        "older ones weren't checked."]
    lines += TABLE + [_row(r, r["title"], MINE_LABEL[r["status"]]) for r in rows]
    if capped:
        lines += ["", f"⚠️ Only your {result['limit']} most recently updated open PRs were checked; "
                  "older ones may also lack a reviewer."]
    return lines


def _suggestion_description(r: dict, viewer: str) -> str:
    if r["why"] == "no_reviewer":
        return f"{r['title']} (@{r['author']}, no reviewer)"
    who = ", ".join("you" if x == viewer else f"@{x}" for x in r["reviewers"])
    return (f"{r['title']} (@{r['author']}; stalled {r['stalled_days']}d after Request changes, "
            f"waiting on {who})")


def render_suggestions(result: dict) -> list[str]:
    """Omitted when empty, unless the scan was capped."""
    rows = result["suggestions"]
    if not rows and not result["suggest_capped"]:
        return []
    owners = ", ".join(result["suggest_owners"]) or "none found"
    lines = ["", "### If you have time", ""]
    if not rows:
        return lines + [f"⚠️ None of the {result['suggest_limit']} most recently updated PRs per "
                        f"owner in {owners} is unreviewed or stalled; older ones weren't checked."]
    lines += [f"_Bugbot-clean PRs by others in {owners} — optional, not your queue._", "",
              "| PR | Description | Link |", "|---|---|---|"]
    lines += [_row(r, _suggestion_description(r, result["viewer"])) for r in rows]
    if result["suggest_capped"]:
        lines += ["", f"⚠️ Only the {result['suggest_limit']} most recently updated PRs per owner "
                  "were checked."]
    return lines


def render(result: dict) -> str:
    lines = render_queue(result)
    if result.get("mine") is not None:
        lines += render_mine(result)
    if result.get("suggestions") is not None:
        lines += render_suggestions(result)
    for e in result["errors"]:
        lines += ["", f"⚠️ Could not classify {e['url']}: {e['error']}"]
    return "\n".join(lines)


def _skill_ident() -> str:
    skill_md = pathlib.Path(__file__).resolve().parent.parent / "SKILL.md"
    try:
        fm = skill_md.read_text(encoding="utf-8").split("---", 2)[1]
        m = re.search(r'version:\s*"([^"]+)"', fm)
        return f"triage-review-requests:{m.group(1)}" if m else "triage-review-requests"
    except (OSError, IndexError):
        return "triage-review-requests"


def send_back_body(row: dict) -> str:
    short = row["head"][:7]
    if row["status"] == FINDINGS:
        n = row["open_findings_on_head"]
        ask = (f"Cursor Bugbot reviewed `{short}` and {n} finding(s) on that commit are still "
               "open. Please address or resolve them (or reply on the thread explaining why "
               "not), then re-request my review.")
    elif row["status"] == STALE:
        ask = (f"Cursor Bugbot hasn't reviewed the current head (`{short}`) — its last review "
               "was on an older commit. Please run Bugbot again, address any findings, then "
               "re-request my review.")
    else:
        ask = (f"Cursor Bugbot hasn't run on this PR yet (head `{short}`). Please run it, "
               "address any findings, then re-request my review.")
    how = ("To trigger it, post `bugbot run` as a PR comment **on its own** — inside a longer "
           "comment it doesn't fire.")
    return f"Sending this back before I review it.\n\n{ask}\n\n{how}\n\n_{_skill_ident()}_\n"


def send_back(url: str, expected_head: str, dry_run: bool) -> int:
    owner, name, number = parse_pr_url(url)
    pr = fetch_pr(owner, name, number)
    row = classify(pr, viewer_login())
    if not expected_head or not row["head"].startswith(expected_head):
        print(f"REFUSED: head moved to {row['head'][:7]} since triage "
              f"(expected {expected_head[:7] or '?'}). Re-run `list`.")
        return 3
    if pr.get("state") != "OPEN" or row["is_draft"]:
        print("REFUSED: PR is no longer open and ready.")
        return 3
    if row["status"] not in FAIL:
        print(f"REFUSED: PR now classifies as {row['status']}; nothing to send back.")
        return 3
    body = send_back_body(row)
    if dry_run:
        print(body)
        return 0
    _gh(["pr", "review", str(number), "--repo", f"{owner}/{name}", "--request-changes",
         "--body-file", "-"], stdin=body)
    print(f"SENT_BACK {url} ({row['status']})")
    return 0


def remove_self(url: str, dry_run: bool, allow_required: bool) -> int:
    owner, name, number = parse_pr_url(url)
    pr = fetch_pr(owner, name, number)
    viewer = viewer_login()
    row = classify(pr, viewer)
    if pr.get("state") != "OPEN" or row["is_draft"]:
        print("REFUSED: PR is no longer open and ready.")
        return 3
    if not row["code_owner_request"]:
        print(f"REFUSED: @{viewer} is not (or no longer) requested by name as a code owner.")
        return 3
    if not row["other_reviewers"]:
        print("REFUSED: no one else is reviewing; stepping back would leave the PR without a reviewer.")
        return 3
    required = code_owner_review_required(owner, name, row["base"])
    if required is not False and not allow_required:
        why = (f"`{row['base']}` requires code-owner review, so removing yourself blocks the merge"
               if required else f"couldn't read the rulesets on `{row['base']}`")
        print(f"REFUSED: {why}. Pass --allow-required only after the user confirms.")
        return 3
    also = ", ".join(row["other_reviewers"])
    if dry_run:
        print(f"WOULD_REMOVE @{viewer} from {url} (also reviewing: {also})")
        return 0
    _gh(["pr", "edit", str(number), "--repo", f"{owner}/{name}", "--remove-reviewer", viewer])
    print(f"REMOVED @{viewer} from {url} (also reviewing: {also})")
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="triage the review queue (read-only)")
    ls.add_argument("--json", action="store_true", help="emit JSON instead of a Markdown table")
    ls.add_argument("--limit", type=int, default=SEARCH_LIMIT)
    ls.add_argument("--no-mine", action="store_true", help="skip your own PRs with no reviewer")
    ls.add_argument("--no-suggest", action="store_true", help='skip the "if you have time" section')
    ls.add_argument("--owner", action="append", default=[],
                    help="owner to draw suggestions from (repeatable); default: owners you open PRs in")
    ls.add_argument("--suggest-limit", type=int, default=SUGGEST_LIMIT,
                    help="most recently updated PRs checked per owner")
    sb = sub.add_parser("send-back", help="request changes asking the author to run Bugbot")
    sb.add_argument("url")
    sb.add_argument("--head", required=True, help="head SHA (or prefix) the triage saw")
    sb.add_argument("--dry-run", action="store_true", help="print the review body, post nothing")
    rs = sub.add_parser("remove-self", help="withdraw your code-owner review request")
    rs.add_argument("url")
    rs.add_argument("--dry-run", action="store_true", help="report what would happen, change nothing")
    rs.add_argument("--allow-required", action="store_true",
                    help="proceed even if code-owner review is required or unreadable")
    args = ap.parse_args()
    try:
        if args.cmd == "list":
            result = triage(args.limit, mine=not args.no_mine, suggest=not args.no_suggest,
                            owners=args.owner, suggest_limit=args.suggest_limit)
            print(json.dumps(result, indent=2) if args.json else render(result))
            sys.exit(0)
        if args.cmd == "remove-self":
            sys.exit(remove_self(args.url, args.dry_run, args.allow_required))
        sys.exit(send_back(args.url, args.head, args.dry_run))
    except (GhError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
