"""
incident_guard.py — keep incident sessions out of a distill.

A distill reads a session's transcript with an AI tool and writes what it learned to AI Knowledge,
Linear and repo PRs, none of which is a privileged place. So a session is never read when:

  privileged  its title starts with "[Privileged" (any case);
  incident    its id is in the local exclude file (one id per line, `#` comments), or an incident
              data hold is marked and that file is missing: with no list to go by, every session is
              held back rather than guessed at.

The hold marker is the one shared:delete-archived-sessions honors: AURA_LEGAL_HOLD set to anything
but empty or 0, or the file `<hold dir>/active`. The exclude file is `<hold dir>/exclude-sessions`.
Both live outside the repo, so no session name or id is ever written here.

  python3 incident_guard.py check <session_id>   exit 0 to read it, 4 (with the reason) to skip it
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

HOLD_DIR = Path.home() / ".claude" / "legal-hold"
PROJECTS = Path.home() / ".claude" / "projects"
PRIVILEGED_TITLE = re.compile(r"^\s*\[privilege", re.IGNORECASE)
SKIP_EXIT = 4


def hold_active(hold_dir: Path = HOLD_DIR) -> bool:
    env = os.environ.get("AURA_LEGAL_HOLD", "").strip()
    return (bool(env) and env != "0") or (hold_dir / "active").exists()


def excluded_ids(hold_dir: Path = HOLD_DIR) -> Optional[set[str]]:
    """The exclude file's session ids, or None when there is no file."""
    path = hold_dir / "exclude-sessions"
    if not path.exists():
        return None
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            ids.add(line)
    return ids


def skip_reason(session_id: str, title: Optional[str], hold_dir: Path = HOLD_DIR) -> Optional[str]:
    """`privileged`, `incident`, or None when the session may be read."""
    if PRIVILEGED_TITLE.match(title or ""):
        return "privileged"
    ids = excluded_ids(hold_dir)
    if ids is None:
        return "incident" if hold_active(hold_dir) else None
    return "incident" if session_id in ids else None


def title_of(session_id: str, projects: Path = PROJECTS) -> Optional[str]:
    """The session's latest custom or AI title, read from its transcript's title records only."""
    title = None
    for path in projects.glob(f"*/{session_id}.jsonl"):
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"custom-title"' not in line and '"ai-title"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") in ("custom-title", "ai-title"):
                    title = rec.get("customTitle") or rec.get("aiTitle") or title
    return title


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--projects", default=str(PROJECTS))
    ap.add_argument("--hold-dir", default=str(HOLD_DIR))
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="exit 4 when the session must not be read")
    c.add_argument("session")
    args = ap.parse_args()
    reason = skip_reason(args.session, title_of(args.session, Path(args.projects)), Path(args.hold_dir))
    if reason:
        print(f"SKIP: {reason} session; it is not read or distilled.")
        sys.exit(SKIP_EXIT)
    print("OK")


if __name__ == "__main__":
    main()
