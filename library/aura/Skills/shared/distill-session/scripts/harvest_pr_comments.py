"""
harvest_pr_comments.py — fetch PR review comments + reply threads as a flat list.

Returns JSON to stdout:
  {
    "pr_url": "<url>",
    "comments": [
      {
        "id": <int>,
        "in_reply_to_id": <int|null>,
        "user": "<login>",
        "path": "<file>",
        "line": <int>,
        "body": "<comment-body>",
        "diff_hunk": "<surrounding-diff>",
        "html_url": "<comment-permalink>"
      },
      ...
    ]
  }

Output is ordered: each top-level comment is followed by its replies in
chronological order. Useful for the learning extractor in Step 3 — keeps
the discussion thread together rather than fragmenting it by author.
"""

import argparse
import json
import re
import subprocess
import sys


def parse_pr(spec):
    """Accept a full PR URL or owner/repo#N shorthand -> (owner, repo, number)."""
    m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", spec)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = re.match(r"^([^/\s]+)/([^/#\s]+)#(\d+)$", spec.strip())
    if m:
        return m.group(1), m.group(2), m.group(3)
    return None


def fetch_comments(owner, repo, number):
    """Return the raw list of review comments (paginated), or None on failure."""
    cmd = ["gh", "api", f"repos/{owner}/{repo}/pulls/{number}/comments", "--paginate"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return [] if p.returncode == 0 else None
    # `gh api --paginate` concatenates pages; normally one merged JSON array.
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        # Defensive: stitch multiple top-level arrays if gh emitted them separately.
        merged = []
        for chunk in re.findall(r"\[.*?\]", p.stdout, re.S):
            try:
                merged.extend(json.loads(chunk))
            except json.JSONDecodeError:
                pass
        return merged


def order_threaded(raw):
    """Group every comment under its thread root, roots ordered by created_at.

    Resolves each comment to its thread root by walking `in_reply_to_id` to the
    top — so a reply-to-a-reply still lands in the right thread instead of being
    deferred to a global pass that could split it from its parent. A comment
    whose parent isn't in this page (cross-page reply) becomes its own root.
    """
    def slim(c):
        return {
            "id": c.get("id"),
            "in_reply_to_id": c.get("in_reply_to_id"),
            "user": (c.get("user") or {}).get("login"),
            "path": c.get("path"),
            "line": c.get("line") if c.get("line") is not None else c.get("original_line"),
            "body": c.get("body"),
            "diff_hunk": c.get("diff_hunk"),
            "html_url": c.get("html_url"),
            "created_at": c.get("created_at"),
        }

    comments = [slim(c) for c in raw]
    by_id = {c["id"]: c for c in comments}
    by_created = lambda c: c.get("created_at") or ""

    def root_of(c):
        cur, seen = c, set()
        while cur["in_reply_to_id"] in by_id and cur["id"] not in seen:
            seen.add(cur["id"])
            cur = by_id[cur["in_reply_to_id"]]
        return cur["id"]

    groups = {}
    for c in comments:
        groups.setdefault(root_of(c), []).append(c)

    ordered = []
    for root_id in sorted(groups, key=lambda rid: by_created(by_id[rid])):
        ordered.extend(sorted(groups[root_id], key=by_created))
    for c in ordered:
        c.pop("created_at", None)
    return ordered


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pr",
        required=True,
        help="PR identifier — either a full URL "
        "(https://github.com/owner/repo/pull/N) or owner/repo#N shorthand.",
    )
    args = parser.parse_args()

    parsed = parse_pr(args.pr)
    if not parsed:
        print(json.dumps({"pr_url": args.pr, "comments": [], "error": "could not parse --pr (use a PR URL or owner/repo#N)"}, indent=2))
        sys.exit(2)

    owner, repo, number = parsed
    raw = fetch_comments(owner, repo, number)
    if raw is None:
        print(json.dumps({"pr_url": args.pr, "comments": [], "error": "gh api call failed (auth? network? gh installed?)"}, indent=2))
        sys.exit(1)

    print(json.dumps({
        "pr_url": f"https://github.com/{owner}/{repo}/pull/{number}",
        "comments": order_threaded(raw),
    }, indent=2))


if __name__ == "__main__":
    main()
