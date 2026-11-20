"""
classify_session.py — detect the shape of the current Claude Code session.

Returns JSON to stdout:
  {
    "classification": "coding-with-review" | "coding-only" | "non-coding",
    "is_coding": bool,
    "repo": "<owner/name>" | null,
    "pr_url": "<url>" | null,
    "pr_number": <int> | null,
    "commit_count": int,
    "pr_comment_count": int,          # all inline review comments
    "pr_human_comment_count": int     # excluding bots (cursor/bugbot/dependabot/…)
  }

Detection signals:
- commit_count via the `--since` anchor: a git ref uses the exclusive
  `<ref>..HEAD` range (so the anchor is a commit that *predates* the session —
  e.g. origin/main, NOT the session's first commit), and a date is retried as
  `--since=<date>`. Default anchor is the latest tag, falling back to HEAD~10.
- `gh pr view --json url,number,author` to discover the PR for the current branch.
- `gh api repos/<owner>/<repo>/pulls/<N>/comments` to count reviewer comments
  (bots and the PR author's own replies are excluded from the human count).

Classification:
- commit_count == 0                                  -> non-coding
- commits + a PR with >= 1 *human* reviewer comment  -> coding-with-review
- commits otherwise (no PR, or only bot reviews)     -> coding-only

The review signal counts **human** comments only: a PR whose sole reviewer is a
bot (Cursor Bugbot, dependabot, …) leaves nothing to harvest, so it classifies
as coding-only rather than coding-with-review.
"""

import argparse
import json
import re
import subprocess
import sys

# Reviewer logins that don't represent harvestable human review.
BOT_LOGIN = re.compile(r"(\[bot\]$|^cursor$|bugbot|dependabot|github-actions|coderabbit|renovate)", re.I)

# Heuristic: does an anchor look like a date/relative-time (vs a git ref)? Gates the
# `--since=<date>` fallback so a genuinely-bad anchor still warns instead of being
# swallowed by git's lenient approxidate parser (which treats garbage as "now" -> 0).
DATE_LIKE = re.compile(r"(^\d{4}-\d{2}-\d{2}|\bago\b|\b(yesterday|today|now)\b|\d+\s*(second|minute|hour|day|week|month|year))", re.I)


def run(cmd, cwd=None):
    """Run a command; return (returncode, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError as exc:
        return 127, "", str(exc)


def git(args, cwd):
    return run(["git", *args], cwd=cwd)


def resolve_anchor(since, cwd):
    """The commit to count from. Explicit --since wins; else latest tag, else HEAD~N."""
    if since:
        return since
    rc, out, _ = git(["describe", "--tags", "--abbrev=0"], cwd)
    if rc == 0 and out:
        return out
    rc, out, _ = git(["rev-list", "--count", "HEAD"], cwd)
    if rc == 0 and out.isdigit():
        n = min(10, max(0, int(out) - 1))
        return f"HEAD~{n}" if n > 0 else None
    return None


def count_commits(anchor, cwd):
    """Commits since the anchor → (count, warning).

    A ref anchor uses the exclusive `<ref>..HEAD` range (so the anchor must be a
    commit that *predates* the session — e.g. origin/main). If that range is
    invalid, the anchor is retried as a date (`--since=<anchor>`). If neither
    works, fall back to the full HEAD count and surface a warning rather than
    silently reporting zero (which would misclassify an active session).
    """
    if not anchor:
        rc, out, _ = git(["rev-list", "--count", "HEAD"], cwd)
        return (int(out) if rc == 0 and out.isdigit() else 0), None
    rc, out, err = git(["rev-list", "--count", f"{anchor}..HEAD"], cwd)
    if rc == 0 and out.isdigit():
        return int(out), None
    if DATE_LIKE.search(anchor):
        rc, out, _ = git(["rev-list", "--count", f"--since={anchor}", "HEAD"], cwd)
        if rc == 0 and out.isdigit():
            return int(out), None
    rc, out, _ = git(["rev-list", "--count", "HEAD"], cwd)
    cnt = int(out) if rc == 0 and out.isdigit() else 0
    return cnt, f"anchor {anchor!r} invalid as a ref range or date ({err or 'rev-list failed'}); counted all commits on HEAD instead"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        help="git revision/ref/date to anchor 'session start' against. "
        "Defaults to the latest tag on main, falling back to HEAD~10.",
    )
    parser.add_argument(
        "--repo-path",
        default=".",
        help="Path to the git repo to classify (default: cwd).",
    )
    args = parser.parse_args()
    cwd = args.repo_path

    def emit(**extra):
        base = {
            "classification": "non-coding",
            "is_coding": False,
            "repo": None,
            "pr_url": None,
            "pr_number": None,
            "commit_count": 0,
            "pr_comment_count": 0,
            "pr_human_comment_count": 0,
        }
        base.update(extra)
        print(json.dumps(base, indent=2))

    rc, _, _ = git(["rev-parse", "--is-inside-work-tree"], cwd)
    if rc != 0:
        emit(note="not a git repository")
        return

    anchor = resolve_anchor(args.since, cwd)
    commit_count, warning = count_commits(anchor, cwd)

    repo = None
    rc, out, _ = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=cwd)
    if rc == 0 and out:
        repo = out

    pr_url = pr_number = pr_author = None
    rc, out, _ = run(["gh", "pr", "view", "--json", "url,number,author"], cwd=cwd)
    if rc == 0 and out:
        try:
            d = json.loads(out)
            pr_url, pr_number = d.get("url"), d.get("number")
            pr_author = (d.get("author") or {}).get("login")
        except json.JSONDecodeError:
            pass

    logins = []
    if repo and pr_number:
        rc, out, _ = run(
            ["gh", "api", f"repos/{repo}/pulls/{pr_number}/comments", "--paginate", "-q", ".[].user.login"],
            cwd=cwd,
        )
        if rc == 0:
            logins = [ln.strip() for ln in out.splitlines() if ln.strip()]
    pr_comment_count = len(logins)
    # Human *reviewer* signal: exclude bots and the PR author's own replies.
    pr_human_comment_count = sum(1 for ln in logins if not BOT_LOGIN.search(ln) and ln != pr_author)

    is_coding = commit_count > 0
    if not is_coding:
        classification = "non-coding"
    elif pr_url and pr_human_comment_count >= 1:
        classification = "coding-with-review"
    else:
        classification = "coding-only"

    emit(
        classification=classification,
        is_coding=is_coding,
        repo=repo,
        pr_url=pr_url,
        pr_number=pr_number,
        commit_count=commit_count,
        pr_comment_count=pr_comment_count,
        pr_human_comment_count=pr_human_comment_count,
        anchor=anchor,
        warning=warning,
    )


if __name__ == "__main__":
    main()
