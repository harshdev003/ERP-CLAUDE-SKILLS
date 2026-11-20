"""
find_sessions.py — list the Claude Code sessions a daily distill run should read.

Three subcommands:

  find     List sessions that had human turns inside the window, with the reason
           every other session in the window was skipped (counted, never silently
           dropped).
  extract  Print one session's in-window turns, condensed: human turns in full,
           assistant prose truncated, tool calls reduced to their names.
  mark     Record in the ledger that a session was distilled up to a timestamp,
           so the next run starts after it.

Sessions are the top-level `~/.claude/projects/<project>/<sessionId>.jsonl`
transcripts. Sub-agent transcripts (nested directories) are never read: their
learnings surface in the parent session.

A turn counts as human only when the transcript says so (`turnOrigin == "human"`),
and is not a desktop scheduled task's launching prompt, which carries that same
marker and is recognised by its `<scheduled-task …>` wrapper.
Scheduled-task runs (`scheduledTaskId`) and SDK / headless runs have no human
turns unless someone joined in, so they never reach the distill step — that
includes this routine's own runs. A scheduled run someone did talk to is read
for those human turns.

A session can run for days. The window applies to turns, not files: a session
started last week that had human turns yesterday is read for yesterday's turns
only, and the ledger keeps a session from being distilled twice.

Incident sessions are never read (incident_guard.py): a "[Privileged…" title or an id in the local
exclude file is counted as `privileged` / `incident` and listed nowhere, and `extract` refuses it.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import incident_guard  # noqa: E402

PROJECTS = Path.home() / ".claude" / "projects"
LEDGER = Path.home() / ".claude" / "distill-sessions" / "ledger.json"
WORKTREE_RE = re.compile(r"/\.claude/worktrees/[^/]+.*$")


def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def default_window(now: Optional[dt.datetime] = None) -> tuple[dt.datetime, dt.datetime]:
    """Yesterday 00:00 → today 00:00, local time. On Monday it reaches back to
    Friday 00:00 so weekend sessions are covered."""
    now = (now or dt.datetime.now()).astimezone()
    until = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days = 3 if until.weekday() == 0 else 1
    return until - dt.timedelta(days=days), until


def parse_bound(value: Optional[str], fallback: dt.datetime) -> dt.datetime:
    if not value:
        return fallback
    parsed = dt.datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.astimezone()


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sessions": {}}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def repo_root(cwd: Optional[str]) -> Optional[str]:
    """Map a worktree cwd back to its main checkout, so one repo gets one PR."""
    if not cwd:
        return None
    return WORKTREE_RE.sub("", cwd)


def text_of(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def tool_names(message: Any) -> list[str]:
    if not isinstance(message, dict) or isinstance(message.get("content"), str):
        return []
    return [
        block.get("name", "?")
        for block in message.get("content") or []
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def is_scheduled_prompt(rec: dict[str, Any]) -> bool:
    """The desktop app marks a scheduled task's launching prompt as a human turn
    (`turnOrigin: human`, no `scheduledTaskId`); only its text gives it away."""
    return text_of(rec.get("message")).lstrip().startswith("<scheduled-task")


def is_human(rec: dict[str, Any]) -> bool:
    return rec.get("turnOrigin") == "human" and not is_scheduled_prompt(rec)


def read_records(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def summarize(path: Path, since: dt.datetime, until: dt.datetime, after: Optional[dt.datetime]) -> dict[str, Any]:
    records = read_records(path)
    info: dict[str, Any] = {
        "session_id": path.stem,
        "path": str(path),
        "cwd": None,
        "repo": None,
        "branch": None,
        "title": None,
        "entrypoint": None,
        "scheduled": False,
        "human_turns": 0,
        "first_turn": None,
        "last_turn": None,
        "first_prompt": None,
        "pr_urls": [],
    }
    lower = max(since, after) if after else since
    for rec in records:
        kind = rec.get("type")
        if rec.get("scheduledTaskId"):
            info["scheduled"] = True
        if kind in ("custom-title", "ai-title"):
            info["title"] = rec.get("customTitle") or rec.get("aiTitle") or info["title"]
        if kind == "pr-link" and rec.get("prUrl") and rec["prUrl"] not in info["pr_urls"]:
            info["pr_urls"].append(rec["prUrl"])
        if kind != "user" or not is_human(rec) or not rec.get("timestamp"):
            if kind == "user" and is_scheduled_prompt(rec):
                info["scheduled"] = True
            continue
        ts = parse_ts(rec["timestamp"])
        if not lower < ts <= until:
            continue
        info["human_turns"] += 1
        info["cwd"] = rec.get("cwd") or info["cwd"]
        info["branch"] = rec.get("gitBranch") or info["branch"]
        info["entrypoint"] = rec.get("entrypoint") or info["entrypoint"]
        info["first_turn"] = info["first_turn"] or rec["timestamp"]
        info["last_turn"] = rec["timestamp"]
        if info["first_prompt"] is None:
            info["first_prompt"] = text_of(rec.get("message"))[:300]
    info["repo"] = repo_root(info["cwd"])
    return info


def effective_since(entry: dict[str, Any], since: dt.datetime) -> dt.datetime:
    """A session whose last run failed to write is read again from where that run's
    window started, whatever today's window is — otherwise the window moves past it
    and the failed learnings are never retried."""
    if entry.get("outcome") == "failed" and entry.get("retry_from"):
        return min(since, parse_ts(entry["retry_from"]))
    return since


def cmd_find(args: argparse.Namespace) -> None:
    d_since, d_until = default_window()
    since = parse_bound(args.since, d_since)
    until = parse_bound(args.until, d_until)
    ledger = load_ledger(Path(args.ledger))
    root = os.path.expanduser(args.root) if args.root else None

    hold_dir = Path(args.hold_dir)
    candidates, skipped = [], {"scheduled": 0, "no_human_turns": 0, "few_human_turns": 0,
                               "outside_root": 0, "already_distilled": 0, "incident": 0, "privileged": 0}
    skipped_sessions: list[dict[str, str]] = []
    for path in sorted(Path(args.projects).glob("*/*.jsonl")):
        entry = ledger["sessions"].get(path.stem, {})
        lower = effective_since(entry, since)
        if dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone() < lower:
            continue
        done = entry.get("distilled_through")
        after = parse_ts(done) if done else None
        info = summarize(path, lower, until, after)
        # Counted only: an incident session's id, title and cwd go nowhere in the output.
        held = incident_guard.skip_reason(info["session_id"], info["title"], hold_dir)
        if held:
            skipped[held] += 1
            continue
        info["retry"] = lower < since
        info["since"] = lower.isoformat()
        reason = None
        if info["human_turns"] == 0:
            # A scheduled run someone then talked to keeps its human turns; one
            # nobody touched is counted as scheduled rather than as idle.
            if after and summarize(path, lower, until, None)["human_turns"]:
                reason = "already_distilled"
            else:
                reason = "scheduled" if info["scheduled"] else "no_human_turns"
        elif info["human_turns"] < args.min_human_turns:
            reason = "few_human_turns"
        elif root and not (info["cwd"] or "").startswith(root):
            reason = "outside_root"
        if reason:
            skipped[reason] += 1
            if reason in ("few_human_turns", "outside_root"):
                skipped_sessions.append({"session_id": info["session_id"], "reason": reason,
                                         "cwd": info["cwd"] or "", "title": info["title"] or ""})
            continue
        candidates.append(info)

    out = {
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "candidates": sorted(candidates, key=lambda c: (c["repo"] or "", c["first_turn"] or "")),
        "skipped": skipped,
        "skipped_sessions": skipped_sessions,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_extract(args: argparse.Namespace) -> None:
    matches = list(Path(args.projects).glob(f"*/{args.session}.jsonl"))
    if not matches:
        sys.exit(f"session {args.session} not found under {args.projects}")
    held = incident_guard.skip_reason(args.session, incident_guard.title_of(args.session, Path(args.projects)),
                                      Path(args.hold_dir))
    if held:
        print(f"REFUSED: {held} session; it is not read or distilled.", file=sys.stderr)
        sys.exit(incident_guard.SKIP_EXIT)
    d_since, d_until = default_window()
    since = parse_bound(args.since, d_since)
    until = parse_bound(args.until, d_until)
    entry = load_ledger(Path(args.ledger))["sessions"].get(args.session, {})
    since = effective_since(entry, since)
    done = entry.get("distilled_through")
    if done:
        since = max(since, parse_ts(done))

    in_window = False
    for rec in read_records(matches[0]):
        kind = rec.get("type")
        if kind not in ("user", "assistant") or not rec.get("timestamp"):
            continue
        ts = parse_ts(rec["timestamp"])
        if kind == "user" and is_human(rec):
            in_window = since < ts <= until
            if in_window:
                print(f"\n### USER {rec['timestamp']}\n{text_of(rec.get('message'))}")
            continue
        if not in_window or kind != "assistant":
            continue
        text = text_of(rec.get("message")).strip()
        if text:
            clipped = text if len(text) <= args.max_chars else text[: args.max_chars] + " […]"
            print(f"\n### ASSISTANT\n{clipped}")
        tools = tool_names(rec.get("message"))
        if tools:
            print(f"[tools: {', '.join(tools)}]")


def cmd_mark(args: argparse.Namespace) -> None:
    path = Path(args.ledger)
    ledger = load_ledger(path)
    entry = ledger["sessions"].setdefault(args.session, {})
    entry["run"] = args.run
    entry["outcome"] = args.outcome
    if args.outcome == "failed":
        if not args.retry_from:
            sys.exit("--retry-from is required with --outcome failed")
        # Keep the earliest start, so a session that fails twice is still read in full.
        prior = entry.get("retry_from")
        entry["retry_from"] = min(args.retry_from, prior) if prior else args.retry_from
    else:
        if not args.through:
            sys.exit("--through is required unless --outcome failed")
        entry["distilled_through"] = args.through
        entry.pop("retry_from", None)
    save_ledger(path, ledger)
    print(json.dumps({args.session: entry}))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--projects", default=str(PROJECTS), help="Claude Code projects directory")
    ap.add_argument("--ledger", default=str(LEDGER), help="distill ledger JSON")
    ap.add_argument("--hold-dir", default=str(incident_guard.HOLD_DIR),
                    help="incident hold marker and exclude-sessions file")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="list sessions to distill")
    f.add_argument("--since", help="window start, ISO (default: yesterday 00:00 local; Friday on Monday)")
    f.add_argument("--until", help="window end, ISO (default: today 00:00 local)")
    f.add_argument("--min-human-turns", type=int, default=3)
    f.add_argument("--root", default="~/Development/Aura", help="only sessions whose cwd is under this")
    f.set_defaults(func=cmd_find)

    e = sub.add_parser("extract", help="print one session's in-window turns, condensed")
    e.add_argument("session")
    e.add_argument("--since")
    e.add_argument("--until")
    e.add_argument("--max-chars", type=int, default=600, help="truncate assistant prose")
    e.set_defaults(func=cmd_extract)

    m = sub.add_parser("mark", help="record a session as distilled")
    m.add_argument("session")
    m.add_argument("--through", help="ISO timestamp of the last distilled human turn")
    m.add_argument("--retry-from", help="with --outcome failed: the run's window.since, to read again from")
    m.add_argument("--run", required=True, help="run date, YYYY-MM-DD")
    m.add_argument("--outcome", required=True, choices=["learnings", "nothing", "not-relevant", "failed"])
    m.set_defaults(func=cmd_mark)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
