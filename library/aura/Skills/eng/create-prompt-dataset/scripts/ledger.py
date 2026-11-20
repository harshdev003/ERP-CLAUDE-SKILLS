#!/usr/bin/env python3
"""Read the prod llmCall ledger (BigQuery `aura-main.llm.llm_call`, auth GCP_KEY) for one
prompter or user, newest to oldest, in one query through the bundled `llm_ledger.py`.

Writes a compact JSON array and prints a one-line summary (rows, distinct users, oldest and
newest createdAt). No user content.

usage:
  ledger.py --prompter feed-prompter --from 2026-07-20T00:00:00Z --to 2026-10-08T00:00:00Z --out f.json
  ledger.py --user {uid} --from ... --to ... --out f.json        # every prompter for one user
  add --openai-api responses to keep only calls stored_responses.py can read
"""
import argparse
import json
import os
import sys
import pathlib
import re


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


FIELDS = ('id', 'createdAt', 'userId', 'conversationId', 'openaiId', 'openaiAPI', 'project',
          'prompterName', 'serviceTier', 'model', 'inputTokens', 'outputTokens')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--prompter', help='prompterName, e.g. feed-prompter')
    ap.add_argument('--user', help='userId')
    ap.add_argument('--project', help='OpenAI project key, e.g. conversation')
    ap.add_argument('--openai-api', dest='openai_api', choices=('responses', 'chatCompletions'),
                    help='keep one OpenAI API; stored_responses.py reads only `responses` rows')
    ap.add_argument('--from', dest='frm', required=True, help='ISO lower bound (createdAt >=)')
    ap.add_argument('--to', required=True, help='ISO upper bound (createdAt <, exclusive)')
    ap.add_argument('--out', required=True, help='output JSON path')
    args = ap.parse_args()
    if not (args.prompter or args.user):
        ap.error('pass --prompter and/or --user')
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import llm_ledger  # noqa: E402  (bundled copy — canonical at data:analyze-user-conversations)
    params = {k: v for k, v in (('prompterName', args.prompter), ('userId', args.user),
                                ('project', args.project), ('openaiAPI', args.openai_api),
                                ('from', args.frm), ('to', args.to)) if v}
    try:
        rows = [{k: r.get(k) for k in FIELDS} for r in llm_ledger.rows(params)]
    except llm_ledger.LedgerError as e:
        sys.exit(f'ledger: {e}')

    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(rows, f)
    print(json.dumps({
        'rows': len(rows),
        'users': len({r['userId'] for r in rows if r['userId']}),
        'oldest': rows[-1]['createdAt'] if rows else None,
        'newest': rows[0]['createdAt'] if rows else None,
        'out': args.out,
    }))


if __name__ == '__main__':
    main()
