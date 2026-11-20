#!/usr/bin/env python3
"""The weekly unattended Claude usage check: harvest the trailing days, then check freshness.

Run by the launchd job `io.aurahealth.anthropic-usage-check` (Mondays) with the framework
interpreter, the one that has google-cloud-bigquery:

    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 weekly_check.py

Every stop raises one `shared:raise-followup` post (kind `blocked`, a card), through this
skill's own copy of `followup.py`, keyed `harvest-anthropic-usage:<stage>:stop`:

  credentials  GCP_KEY is unset, so neither the harvest nor the check can reach BigQuery
  harvest      no session key, an expired one (harvest.py exit 2), or any other failure
  check        the warehouse is stale (harvest.py --check exit 1), or BigQuery could not be read (exit 3)

A stage that keeps failing stays one post, with a changed reason as a reply in its thread.
Secrets are read from ~/.claude/settings.json `env` at run time and never printed or logged.
Exit 0 when nothing was raised, 1 when something was, 2 when a follow-up could not be posted
(the log says which).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import followup  # noqa: E402

HARVEST = HERE / "harvest.py"
LOG = pathlib.Path.home() / ".claude/jobs/anthropic-usage-check.log"
SETTINGS = pathlib.Path.home() / ".claude/settings.json"
ENV = ("GCP_KEY", "SLACK_BOT_TOKEN", "LINEAR_API_KEY", "CLAUDE_ADMIN_SESSION_KEY", "CLAUDE_ADMIN_ORG_ID")
UUID = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I)
RERUN = "Ask Claude Code to \"update the key\" (eng:harvest-anthropic-usage); missed days backfill on the next run."


def log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}  {line}\n")


def load_env() -> None:
    """Fill the credentials the run needs from settings.json, without overriding the process env."""
    try:
        env = json.loads(SETTINGS.read_text(encoding="utf-8")).get("env", {})
    except (OSError, ValueError):
        env = {}
    for name in ENV:
        if not os.environ.get(name) and env.get(name):
            os.environ[name] = str(env[name])


def run(args: list[str]) -> tuple[int, str]:
    """Exit code and output, stderr last: harvest.py prints its per-day progress on stdout and
    its failure summary on stderr, so the last line is the one that says what went wrong."""
    p = subprocess.run([sys.executable, str(HARVEST), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, "\n".join(s.strip() for s in (p.stdout, p.stderr) if s.strip())


def first_line(text: str, prefix: str = "", last: bool = False) -> str:
    """One line of tool output (the first, or with `last` the last), with anything raise-followup
    refuses (ids, emails, IPs, tokens) cut."""
    lines = [l for l in text.splitlines() if l.strip()]
    if prefix:
        lines = [l for l in lines if l.startswith(prefix)] or lines
    line = (lines[-1] if last else lines[0]).strip()[:200] if lines else ""
    line = followup.TOKEN.sub("[token]", line)
    line = followup.EMAIL.sub("[email]", line)
    line = followup.IPV4.sub("[ip]", followup.IPV6.sub("[ip]", line))
    line = UUID.sub(lambda m: m.group(0)[:8] + "…", line)
    return followup.FULL_ID.sub(lambda m: m.group(0)[:8] + "…", line)


def stops(runner=run) -> list[tuple[str, str, str]]:
    """(stage, what happened, what to do) for every stop. Empty means a clean run."""
    if not os.environ.get("GCP_KEY"):
        log("SKIP  GCP_KEY unset")
        return [("credentials", "GCP_KEY is not set, so neither the harvest nor the freshness check ran.",
                 "Set GCP_KEY in ~/.claude/settings.json env, then let the next Monday run catch up.")]
    out = []
    if not os.environ.get("CLAUDE_ADMIN_SESSION_KEY"):
        log("no session key — check only")
        out.append(("harvest", "CLAUDE_ADMIN_SESSION_KEY is not set, so no usage was harvested.", RERUN))
    else:
        # A generous trailing window costs little: a day already held is replaced, unchanged.
        rc, text = runner(["--window", "10"])
        if rc == 0:
            log(f"HARVEST ok  {first_line(text, 'total:')}")
        elif rc == 2:
            log("COOKIE EXPIRED — nothing written")
            out.append(("harvest", "The claude.ai session key expired; nothing was written.", RERUN))
        else:
            log(f"HARVEST rc={rc}  {first_line(text, last=True)}")
            out.append(("harvest", f"harvest.py exited {rc}: {first_line(text, last=True)}",
                        "Read ~/.claude/jobs/anthropic-usage-check.log, fix the cause, and re-run harvest.py."))
    # Always end on the credential-free check: silence here would look identical to success.
    rc, text = runner(["--check"])
    if rc == 0:
        log(f"OK    {first_line(text)}")
    elif rc == 1:
        log(f"STALE {first_line(text)}")
        out.append(("check", f"The usage warehouse is stale: {first_line(text)}",
                    "Fix the harvest stop above if there is one; otherwise re-run harvest.py --window 30."))
    else:  # exit 3: BigQuery could not be read, so freshness is unknown, not stale
        log(f"CHECK rc={rc}  {first_line(text, last=True)}")
        out.append(("check", f"The freshness check could not read BigQuery (exit {rc}): {first_line(text, last=True)}",
                    "Check GCP_KEY and BigQuery access for the service account, then re-run harvest.py --check."))
    return out


def main(argv=None, runner=run, raise_one=followup.raise_followup) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the follow-ups, post nothing")
    args = ap.parse_args(argv)
    load_env()
    cfg = followup.load_config()
    worst = 0
    for stage, what, ask in stops(runner):
        spec = {"kind": "blocked", "title": f"Claude usage harvest: {stage} stopped",
                "what": what, "why": "The Claude usage tables stay stale until this is fixed; no other job reports it.",
                "ask": ask, "key": f"harvest-anthropic-usage:{stage}:stop",
                "session": "launchd:anthropic-usage-check"}
        code, res = raise_one(spec, cfg, args.dry_run)
        print(json.dumps({"stage": stage, **res}, ensure_ascii=False))
        if code:
            log(f"FOLLOW-UP {stage} not posted: {res.get('action')} {res.get('error') or res.get('problems')}")
            worst = 2
        else:
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    sys.exit(main())
