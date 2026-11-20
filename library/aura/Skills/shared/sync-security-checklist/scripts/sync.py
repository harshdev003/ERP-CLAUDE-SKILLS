#!/usr/bin/env python3
"""Carry the security checklist and logging rule from AI Knowledge to every repo's copies.

Subcommands, in the order a change goes through them:

  fetch    render the two canonical Notion pages to Markdown snapshots (read-only)
  plan     apply the line edits between two snapshots to each repo's files (read-only)
  drift    report repos whose copy differs from the rest of its layer (read-only)
  rollout  one docs-only draft PR per planned repo, then `bugbot run`
  merge    merge docs-only rollout PRs that are clean, stopping at the first that is not

`plan` never overwrites a file with a rebuilt copy. It takes the line edits between the old
and the new snapshot and applies each one where its old text appears exactly once. A file
that does not carry that text (a section its layer omits, or a BUGBOT.md that has its own
wording) is left as it is and the miss is reported. Text more than one place is a stop.

Repos come from ../assets/repos.txt (paths under AURA_DEV_DIR, default ~/Development/Aura),
handled one at a time. State (snapshots, plans) lives in SYNC_CHECKLIST_DIR, default
~/.claude/sync-security-checklist.

    python3 sync.py fetch [--out DIR]
    python3 sync.py plan --old SNAPSHOT_DIR --new SNAPSHOT_DIR [--only A,B] [--out DIR]
    python3 sync.py drift [--only A,B]
    python3 sync.py rollout --plan DIR --branch SLUG --title T --message-file F --body-file F [--only A,B]
    python3 sync.py merge Repo#N [Repo#N ...]

Exit 0 when the step finished, 1 when it stopped on a finding (a miss in `plan` is not one),
2 on a bad argument or missing input.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"

# Compliance/Security/Code Review Checklist and Procedures/Engineering/Logging Standards.
PAGES = {
    "checklist": ("40275e23af208173815af65a63bd4061", "Security review checklist"),
    "logging": ("40275e23af208161ad2bfa23289b716d", "Logging Standards"),
}
# The page's Sources section is not carried in the repo copies.
STOP_HEADINGS = ("Sources",)

# A rollout PR may touch only these paths; anything else staged is a stop.
ALLOWED = re.compile(r"^(\.cursor/|\.claude/|CLAUDE\.md$|AGENTS\.md$|\.github/PULL_REQUEST_TEMPLATE\.md$)")
# A docs-only merge may touch these too (README and docs pages).
MERGE_ALLOWED = re.compile(r"^(\.cursor/|\.claude/|CLAUDE\.md$|AGENTS\.md$|(.*/)?README\.md$|"
                           r"\.github/PULL_REQUEST_TEMPLATE\.md$|docs/[^/]+\.md$)")
# These three name their rule files security-review.*; every other repo uses security.*.
REVIEW_NAMED = {"Aura", "AuraConsumerApp", "LivekitVoiceAgent"}
# A Linear card id; the integration moves a card when a PR whose branch or title names it merges.
CARD_ID = re.compile(r"(?<![a-z0-9])(eng|engback|engmob|engweb|engsup|engprom|prod)-\d+", re.I)
PENDING = {"queued", "in_progress", "waiting", "requested", "pending"}


# ---------------------------------------------------------------- repos


def aura_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("AURA_DEV_DIR") or os.path.expanduser("~/Development/Aura"))


def state_dir() -> pathlib.Path:
    d = pathlib.Path(os.environ.get("SYNC_CHECKLIST_DIR") or os.path.expanduser("~/.claude/sync-security-checklist"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def repos(only: set[str] | None = None) -> list[str]:
    out = []
    for line in (ASSETS / "repos.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "-wt-" in line or "/.claude/worktrees/" in line:  # never a worktree copy
            continue
        if only and pathlib.PurePosixPath(line).name not in only:
            continue
        out.append(line)
    return out


def layer(rel: str) -> str:
    """The checklist layer a repo carries: service ABF, web ACF, firebase ADF, mobile AEF, general AF."""
    if rel.startswith("Services/"):
        return "service"
    if rel.startswith("Web/"):
        return "web"
    if rel.startswith("Functions/") or rel == "Data/Firebase":
        return "firebase"
    if rel.startswith("Mobile/"):
        return "mobile"
    return "general"


def files_for(rel: str) -> dict[str, str]:
    """Each file the rollout keeps current in a repo -> which canonical page it follows."""
    name = pathlib.PurePosixPath(rel).name
    f = "security-review" if name in REVIEW_NAMED else "security"
    out = {f".claude/rules/{f}.md": "checklist", f".cursor/rules/{f}.mdc": "checklist",
           ".cursor/BUGBOT.md": "checklist"}
    if layer(rel) == "service":
        out[".claude/rules/logging.md"] = "logging"
        out[".cursor/rules/logging.mdc"] = "logging"
    return out


def sh(args: list[str], cwd, check: bool = True) -> str:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if check and p.returncode:
        raise RuntimeError(f"{' '.join(args[:3])}…: {p.stderr.strip()[-400:]}")
    return p.stdout


def default_branch(root: pathlib.Path) -> str:
    ref = sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root, check=False).strip()
    if not ref.startswith("origin/"):
        raise RuntimeError("origin/HEAD is not set (run `git remote set-head origin -a`)")
    return ref.split("/", 1)[1]


def show(root: pathlib.Path, base: str, path: str) -> str | None:
    p = subprocess.run(["git", "show", f"origin/{base}:{path}"], cwd=root, capture_output=True,
                       text=True, encoding="utf-8")
    return p.stdout if p.returncode == 0 else None


# ---------------------------------------------------------------- fetch


def rich(rt: list[dict]) -> str:
    """Notion rich text -> the Markdown the repo copies use.

    One ** span per run of bold text (code spans inside it), one * span per run of
    italic, and links as their plain text: the copies carry no Notion links.
    """
    segs: list[list] = []
    for r in rt:
        a = r.get("annotations") or {}
        t = r.get("plain_text", "")
        if a.get("code"):
            t = f"`{t}`"
        key = (bool(a.get("bold")), bool(a.get("italic")))
        if segs and segs[-1][1] == key:
            segs[-1][0] += t
        else:
            segs.append([t, key])
    out = []
    for t, (bold, italic) in segs:
        mark = ("**" if bold else "") + ("*" if italic else "")
        if mark and t.strip():
            lead = t[: len(t) - len(t.lstrip(" "))]
            trail = t[len(t.rstrip(" ")):]
            t = f"{lead}{mark}{t.strip(' ')}{mark[::-1]}{trail}"
        out.append(t)
    return "".join(out)


def render(blocks: list[dict], title: str, depth: int = 0):
    """Notion blocks -> Markdown in the copies' layout: `##` then a blank line, `###` straight
    into its list, a list item's paragraph child after a blank line at the item's indent."""
    lines = [f"# {title}", ""] if depth == 0 else []
    ind = "  " * depth
    prev, n = None, 0
    for b in blocks:
        t = b["type"]
        body = b.get(t, {})
        text = rich(body.get("rich_text", []))
        kids = b.get("_children") or []
        if t in ("heading_1", "heading_2", "heading_3"):
            if depth == 0 and text.strip() in STOP_HEADINGS:
                break
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"{'#' * int(t[-1])} {text}")
            if t != "heading_3":
                lines.append("")
        elif t == "paragraph":
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"{ind}{text}")
            if depth == 0:
                lines.append("")
        elif t in ("bulleted_list_item", "numbered_list_item"):
            if t == "numbered_list_item":
                n = n + 1 if prev == t else 1
                mark = f"{n}."
            else:
                mark = "-"
            lines.append(f"{ind}{mark} {text}")
            lines += render(kids, title, depth + 1)
        elif t == "code":
            lang = body.get("language") or ""
            code = "".join(r.get("plain_text", "") for r in body.get("rich_text", []))
            if lines and lines[-1] != "":
                lines.append("")
            lines += [f"```{'' if lang == 'plain text' else lang}", *code.split("\n"), "```", ""]
        elif t == "divider":
            lines += ["---", ""]
        else:
            raise SystemExit(f"fetch: unsupported Notion block type {t!r}; add it to render()")
        prev = t
    if depth == 0:
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines) + "\n"
    return lines


def fetch(out: pathlib.Path, get_blocks=None) -> dict[str, pathlib.Path]:
    if get_blocks is None:
        sys.path.insert(0, str(HERE))
        from notion_rest import notion  # noqa: E402
        if not notion.available:
            raise SystemExit("fetch: NOTION_TOKEN is not set; the canonical pages can't be read")
        get_blocks = lambda pid: notion.get_block_children(pid, recursive=True)  # noqa: E731
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for key, (pid, title) in PAGES.items():
        p = out / f"{key}.md"
        p.write_text(render(get_blocks(pid), title), encoding="utf-8")
        written[key] = p
    return written


# ---------------------------------------------------------------- plan


def edits(old: str, new: str) -> list[tuple]:
    """The line edits from old to new: (tag, old_text, new_text, anchor_line_before)."""
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        ops.append((tag, "".join(a[i1:i2]), "".join(b[j1:j2]), a[i1 - 1] if i1 else None))
    return ops


def apply_edits(text: str, ops: list[tuple]) -> tuple[str, int, list[str], list[str]]:
    """(new text, edits applied, edits whose old text is absent, edits whose old text repeats).

    A replace or delete applies where its old text appears exactly once. An insert goes after
    its anchor line when that line appears exactly once and the new text isn't there yet.
    """
    applied, missed, multi = 0, [], []
    for tag, o, n, anchor in ops:
        if tag in ("replace", "delete"):
            c = text.count(o)
            if c == 1:
                text = text.replace(o, n)
                applied += 1
            elif c == 0:
                missed.append(o.strip()[:70])
            else:
                multi.append(o.strip()[:70])
        else:
            if n in text:
                continue
            if anchor is not None and text.count(anchor) == 1:
                k = text.index(anchor) + len(anchor)
                text = text[:k] + n + text[k:]
                applied += 1
            else:
                missed.append(n.strip()[:70])
    return text, applied, missed, multi


def plan(old_dir: pathlib.Path, new_dir: pathlib.Path, out: pathlib.Path,
         only: set[str] | None = None) -> list[dict]:
    pairs = {k: edits((old_dir / f"{k}.md").read_text(encoding="utf-8"),
                      (new_dir / f"{k}.md").read_text(encoding="utf-8")) for k in PAGES}
    out.mkdir(parents=True, exist_ok=True)
    report = []
    for rel in repos(only):  # one repo at a time
        root = aura_dir() / rel
        name = pathlib.PurePosixPath(rel).name
        row = {"repo": name, "rel": rel, "files": {}, "missed": {}, "multi": {}}
        try:
            sh(["git", "fetch", "-q", "origin"], root)
            base = default_branch(root)
        except (RuntimeError, OSError) as e:
            row["error"] = str(e)
            report.append(row)
            continue
        row["base"] = base
        for dest, page in files_for(rel).items():
            text = show(root, base, dest)
            if text is None:
                row["missed"][dest] = ["file not on the default branch"]
                continue
            new, applied, missed, multi = apply_edits(text, pairs[page])
            if missed:
                row["missed"][dest] = missed
            if multi:
                row["multi"][dest] = multi
                continue  # never apply part of a file whose edit is ambiguous
            if new != text:
                f = out / name / dest
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(new, encoding="utf-8")
                row["files"][dest] = applied
        report.append(row)
    (out / "plan.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


# ---------------------------------------------------------------- drift


def drift(only: set[str] | None = None) -> dict:
    """Per layer and rule file, the repos whose blob differs from the layer's most common blob.

    BUGBOT.md is not a layer copy (each repo has its own wording) and is not compared.
    """
    blobs: dict[tuple, dict[str, str]] = {}
    errors = {}
    for rel in repos(only):
        root = aura_dir() / rel
        name = pathlib.PurePosixPath(rel).name
        try:
            sh(["git", "fetch", "-q", "origin"], root)  # a stale ref reads as drift
            base = default_branch(root)
        except (RuntimeError, OSError) as e:
            errors[name] = str(e)
            continue
        for dest in files_for(rel):
            if dest.endswith("BUGBOT.md"):
                continue
            kind = dest.rsplit("/", 1)[-1].replace("security-review", "security")
            sha = sh(["git", "rev-parse", "--verify", "--quiet", f"origin/{base}:{dest}"], root, check=False).strip()
            # LivekitVoiceAgent's .md carries the .mdc frontmatter, so it is its own layer copy.
            key = (layer(rel) + ("+frontmatter" if name == "LivekitVoiceAgent" and kind.endswith(".md") else ""), kind)
            blobs.setdefault(key, {})[name] = sha or "missing"
    out = {"differs": {}, "missing": {}, "errors": errors}
    for (lay, kind), by_repo in sorted(blobs.items()):
        counts: dict[str, int] = {}
        for sha in by_repo.values():
            if sha != "missing":
                counts[sha] = counts.get(sha, 0) + 1
        common = max(counts, key=counts.get) if counts else None
        for name, sha in sorted(by_repo.items()):
            if sha == "missing":
                out["missing"].setdefault(name, []).append(kind)
            elif sha != common:
                out["differs"].setdefault(name, []).append(f"{lay}:{kind}")
    return out


# ---------------------------------------------------------------- rollout


def rollout(plan_dir: pathlib.Path, branch: str, title: str, message_file: pathlib.Path,
            body_file: pathlib.Path, only: set[str] | None = None) -> list[dict]:
    if CARD_ID.search(branch + " " + title):
        raise SystemExit("rollout: no card id in the branch or title unless the PR closes that card; "
                         "link the tracking card from the body instead")
    rows = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
    results = []
    for row in rows:  # one repo at a time, never in parallel
        if not row.get("files") or (only and row["repo"] not in only):
            continue
        if row.get("multi"):
            results.append({"repo": row["repo"], "status": "skipped: ambiguous edit in the plan"})
            continue
        try:
            results.append(_rollout_one(row, plan_dir, branch, title, message_file, body_file))
        except (RuntimeError, OSError) as e:
            results.append({"repo": row["repo"], "status": "error", "detail": str(e)[-400:]})
        print(json.dumps(results[-1]), flush=True)
    return results


def _rollout_one(row, plan_dir, branch, title, message_file, body_file) -> dict:
    root = aura_dir() / row["rel"]
    name = row["repo"]
    slug = sh(["git", "remote", "get-url", "origin"], root).strip().rsplit(":", 1)[-1].removesuffix(".git")
    slug = "/".join(slug.split("/")[-2:])
    sh(["git", "fetch", "-q", "origin"], root)
    base = default_branch(root)
    if sh(["gh", "pr", "list", "-R", slug, "--head", branch, "--state", "open", "--json", "number",
           "-q", ".[0].number"], root, check=False).strip():
        return {"repo": name, "status": "skipped: an open PR already uses this branch"}
    wt = root / ".claude/worktrees" / f"sync-checklist-{branch.replace('/', '-')}"
    if wt.exists():
        sh(["git", "worktree", "remove", "--force", str(wt)], root, check=False)
    sh(["git", "worktree", "prune"], root, check=False)
    sh(["git", "branch", "-D", branch], root, check=False)
    # Always a fresh branch from the current default: never merge the default into an old one.
    sh(["git", "worktree", "add", "-q", "-b", branch, str(wt), f"origin/{base}"], root)
    try:
        return _commit_push_open(root, wt, row, plan_dir, branch, title, message_file, body_file, slug, base)
    finally:
        # The branch is on the remote (or the run failed); the worktree is not needed either way.
        sh(["git", "worktree", "remove", "--force", str(wt)], root, check=False)
        sh(["git", "worktree", "prune"], root, check=False)


def _commit_push_open(root, wt, row, plan_dir, branch, title, message_file, body_file, slug, base) -> dict:
    name = row["repo"]
    # Hooks run (never --no-verify); give the worktree the checkout's installed hook runner.
    for link in (".husky/_", "node_modules"):
        src, dst = root / link, wt / link
        if src.exists() and dst.parent.exists() and not dst.exists():
            os.symlink(src, dst)
    files = list(row["files"])
    for dest in files:
        (wt / dest).parent.mkdir(parents=True, exist_ok=True)
        (wt / dest).write_text((plan_dir / name / dest).read_text(encoding="utf-8"), encoding="utf-8")
    sh(["git", "add", "-f", "--", *files], wt)  # some repos gitignore .claude/ or .cursor/
    staged = [p for p in sh(["git", "diff", "--cached", "--name-only"], wt).splitlines() if p]
    bad = [p for p in staged if not ALLOWED.match(p)]
    if bad:
        raise RuntimeError(f"paths outside the docs-only set staged: {bad}")
    if not staged:
        return {"repo": name, "status": "nothing to change"}
    sh(["git", "commit", "-q", "-F", str(message_file)], wt)
    p = subprocess.run(["git", "push", "-q", "-u", "origin", branch], cwd=wt, capture_output=True, text=True, encoding="utf-8")
    if p.returncode:
        return {"repo": name, "status": "push failed", "detail": p.stderr.strip()[-400:]}
    url = sh(["gh", "pr", "create", "-R", slug, "--draft", "--base", base, "--head", branch,
              "--title", title, "--body-file", str(body_file)], wt).strip()
    n = url.rsplit("/", 1)[-1]
    # The trigger is its own comment: appended to another one it never fires.
    sh(["gh", "pr", "comment", n, "-R", slug, "--body", "bugbot run"], wt)
    return {"repo": name, "status": "opened", "pr": int(n), "url": url, "files": files}


# ---------------------------------------------------------------- merge


def holds_file() -> pathlib.Path:
    return pathlib.Path(os.environ.get("SYNC_CHECKLIST_HOLDS")
                        or os.path.expanduser("~/.claude/librarian/release-holds.txt"))


def held_repos() -> set[str]:
    """Repos whose release is running; a missing hold list stops every merge."""
    p = holds_file()
    if not p.is_file():
        raise SystemExit(f"merge: the release-hold list {p} is missing; nothing is merged without it")
    return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")}


def gh_json(args: list[str]):
    p = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if p.returncode:
        raise RuntimeError(p.stderr.strip()[-300:])
    return json.loads(p.stdout or "null")


def merge_check(repo: str, n: int, gh=gh_json) -> tuple[str, str]:
    """('ready'|'wait'|'stop'|'done', why) for one docs-only PR."""
    slug = f"AuraHealth/{repo}"
    pr = gh(["pr", "view", str(n), "-R", slug, "--json", "state,isDraft,headRefOid,files"])
    if pr["state"] != "OPEN":
        return "done", pr["state"].lower()
    bad = [f["path"] for f in pr["files"] if not MERGE_ALLOWED.match(f["path"])]
    if bad:
        return "stop", f"files outside the docs-only set: {bad} (code PRs are never merged here)"
    runs = gh(["api", f"repos/{slug}/commits/{pr['headRefOid']}/check-runs?filter=all&per_page=100",
               "--jq", "[.check_runs[]|{name,status,conclusion}]"]) or []
    bb = [r for r in runs if r["name"] == "Cursor Bugbot"]
    if not bb:
        return "wait", "no BugBot run on the head"
    if any(r["status"] in PENDING for r in runs):
        return "wait", "checks running"
    q = ('query($n:Int!,$r:String!){repository(owner:"AuraHealth",name:$r){pullRequest(number:$n)'
         '{reviewThreads(first:100){nodes{isResolved}}}}}')
    data = gh(["api", "graphql", "-F", f"n={n}", "-f", f"r={repo}", "-f", f"query={q}"])
    threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    unresolved = sum(1 for t in threads if not t["isResolved"])  # outdated ones count too
    if unresolved:
        return "stop", f"{unresolved} unresolved review threads"
    if any(r["conclusion"] not in ("success", "skipped", "neutral") for r in bb):
        return "stop", f"BugBot concluded {[r['conclusion'] for r in bb]}"
    failed = [r["name"] for r in runs if r["conclusion"] not in ("success", "skipped", "neutral")]
    if failed:
        return "stop", f"checks failed: {failed}"
    return "ready", "clean"


def merge(targets: list[str], gh=gh_json, wait_min: int = 0) -> list[tuple[str, str, str]]:
    held = held_repos()
    out = []
    for t in targets:  # in the order given; stop at the first failure
        repo, _, n = t.partition("#")
        if repo in held:
            out.append((t, "stop", "repo is on release hold"))
            break
        deadline = time.time() + wait_min * 60
        while True:
            verdict, why = merge_check(repo, int(n), gh)
            if verdict != "wait" or time.time() >= deadline:
                break
            time.sleep(60)
        if verdict == "ready":
            pr = gh(["pr", "view", n, "-R", f"AuraHealth/{repo}", "--json", "isDraft"])
            if pr.get("isDraft"):
                subprocess.run(["gh", "pr", "ready", n, "-R", f"AuraHealth/{repo}"], capture_output=True,
                               text=True, encoding="utf-8")
            p = subprocess.run(["gh", "pr", "merge", n, "-R", f"AuraHealth/{repo}", "--squash",
                                "--delete-branch"], capture_output=True, text=True, encoding="utf-8")
            verdict, why = ("done", "merged") if p.returncode == 0 else ("stop", f"merge failed: {p.stderr.strip()[-200:]}")
        out.append((t, verdict, why))
        if verdict not in ("done",):
            break
    return out


# ---------------------------------------------------------------- cli


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", help="snapshot folder (default: <state>/snapshots/<UTC time>)")
    p = sub.add_parser("plan")
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--out", help="plan folder (default: <state>/plans/<UTC time>)")
    p.add_argument("--only", default="")
    d = sub.add_parser("drift")
    d.add_argument("--only", default="")
    r = sub.add_parser("rollout")
    r.add_argument("--plan", required=True)
    r.add_argument("--branch", required=True)
    r.add_argument("--title", required=True)
    r.add_argument("--message-file", required=True)
    r.add_argument("--body-file", required=True)
    r.add_argument("--only", default="")
    m = sub.add_parser("merge")
    m.add_argument("targets", nargs="+", help="Repo#N, merged in the order given")
    m.add_argument("--wait", type=int, default=0, help="minutes to wait for running checks")
    a = ap.parse_args(argv)
    only = {x.strip() for x in getattr(a, "only", "").split(",") if x.strip()} or None
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if a.cmd == "fetch":
        out = pathlib.Path(a.out) if a.out else state_dir() / "snapshots" / stamp
        for k, path in fetch(out).items():
            print(f"{k}: {path}")
        return 0
    if a.cmd == "plan":
        for x in (a.old, a.new):
            if not all((pathlib.Path(x) / f"{k}.md").is_file() for k in PAGES):
                print(f"plan: {x} lacks {', '.join(f'{k}.md' for k in PAGES)}", file=sys.stderr)
                return 2
        out = pathlib.Path(a.out) if a.out else state_dir() / "plans" / stamp
        rows = plan(pathlib.Path(a.old), pathlib.Path(a.new), out, only)
        for row in rows:
            print(json.dumps({k: row[k] for k in ("repo", "files", "missed", "multi", "error") if row.get(k)}))
        print(f"plan: {out}")
        return 1 if any(r.get("multi") or r.get("error") for r in rows) else 0
    if a.cmd == "drift":
        out = drift(only)
        print(json.dumps(out, indent=1))
        return 1 if out["differs"] or out["missing"] or out["errors"] else 0
    if a.cmd == "rollout":
        res = rollout(pathlib.Path(a.plan), a.branch, a.title, pathlib.Path(a.message_file),
                      pathlib.Path(a.body_file), only)
        return 1 if any(r["status"] in ("error", "push failed") for r in res) else 0
    if a.cmd == "merge":
        bad = [t for t in a.targets if not re.fullmatch(r"[A-Za-z0-9_.-]+#\d+", t)]
        if bad:
            print(f"merge: expected Repo#N, got {bad}", file=sys.stderr)
            return 2
        res = merge(a.targets, wait_min=a.wait)
        for t, verdict, why in res:
            print(f"{t}: {verdict} — {why}")
        return 0 if all(v == "done" for _, v, _ in res) and len(res) == len(a.targets) else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
