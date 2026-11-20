#!/usr/bin/env python3
"""Check, disable or re-enable one Firebase Auth account.

  status   read-only: disabled, sign-in providers, tokens valid since
  disable  disabled=true and refresh tokens revoked (validSince=now), the Admin SDK's
           revokeRefreshTokens; open sessions end when their ID token expires (<= 1 h)
  enable   disabled=false

Auth: GCP_KEY (service-account JSON) through Identity Toolkit. Writes need
roles/firebaseauth.admin on the target project. A prod write needs --prod, --go (Jerome's go,
quoted verbatim) and --card. Every write reads the account before and after and fails if the read-back
doesn't match.

A write needs the keep list (held_uids.py) and refuses a uid on it: a held account is never
disabled, re-enabled or otherwise rewritten here.

Prints the uid prefix only: never an email, a name or a token.
Exit: 0 ok, 1 refused or the read-back didn't match, 2 a read failed (unverified).
"""
import argparse
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import held_uids  # noqa: E402  (a copy; canonical in cleanup-orphaned-auth)
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

PROJECTS = ("aura-dev", "firebase-auratech16")
PROD = "firebase-auratech16"
IDTK = "https://identitytoolkit.googleapis.com/v1/projects/{project}/accounts:{method}"
ROLE = "roles/firebaseauth.admin"
EMAIL = re.compile(r"[^\s@\"']+@[^\s@\"']+")


class Unverified(Exception):
    pass


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
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls in GCP logs.

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


def credential():
    """The GCP_KEY service account's email and home project; never the key."""
    raw = os.environ.get("GCP_KEY")
    if not raw:
        raise Unverified("GCP_KEY is not set")
    try:
        info = json.loads(raw)
    except ValueError:
        raise Unverified("GCP_KEY is not service-account JSON") from None
    return info.get("client_email") or "?", info.get("project_id") or "?"


_TOKEN = None


def _token():
    global _TOKEN
    if _TOKEN is None or not _TOKEN.valid:
        from google.oauth2 import service_account
        import google.auth.transport.requests
        _TOKEN = service_account.Credentials.from_service_account_info(
            json.loads(os.environ["GCP_KEY"]), scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _TOKEN.refresh(google.auth.transport.requests.Request())
    return _TOKEN.token


def call(project, method, body):
    req = urllib.request.Request(
        IDTK.format(project=project, method=method), data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json",
                 "User-Agent": user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode())["error"]["message"]
        except (ValueError, KeyError, TypeError):
            msg = f"HTTP {e.code}"
        if "PERMISSION" in msg or e.code == 403:
            sa, _ = credential()
            msg += f": grant {ROLE} on {project} to {sa}"
        raise Unverified(f"accounts:{method} on {project}: {msg} (credential GCP_KEY)") from None
    except (urllib.error.URLError, OSError) as e:
        raise Unverified(f"accounts:{method} on {project}: {e} (credential GCP_KEY)") from None


def lookup(project, uid=None, email=None):
    body = {"localId": [uid]} if uid else {"email": [email]}
    users = call(project, "lookup", body).get("users") or []
    if len(users) != 1:
        if users:
            raise Unverified(f"{len(users)} accounts match in {project}")
        raise Unverified(f"no such account in {project}" + (
            " by top-level email; a federated account may have none, so use --uid" if email else ""))
    return users[0]


def label(uid):
    """Six characters of the uid, after any `provider:` prefix, so prefixed ids don't all read the same."""
    head, sep, rest = uid.rpartition(":")
    return f"{head}{sep}{rest[:6]}" if sep else uid[:6]


def view(u):
    since = u.get("validSince")
    return {
        "disabled": bool(u.get("disabled")),
        "providers": sorted({p.get("providerId") for p in u.get("providerUserInfo") or [] if p.get("providerId")})
                     or (["password"] if u.get("passwordHash") else []),
        "tokens_valid_since": datetime.fromtimestamp(int(since), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                              if since else "-",
        "valid_since": int(since or 0),
    }


def line(label, v):
    return (f"{label:<7} disabled={'yes' if v['disabled'] else 'no'}  providers={','.join(v['providers']) or '-'}  "
            f"tokens valid since {v['tokens_valid_since']}")


def run(argv=None, now=time.time):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("status", "disable", "enable"))
    ap.add_argument("--project", required=True, choices=PROJECTS)
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--uid")
    who.add_argument("--email", help="looked up, never printed")
    ap.add_argument("--prod", action="store_true", help="required for a write on firebase-auratech16")
    ap.add_argument("--go", help="prod writes: Jerome's go, quoted verbatim")
    ap.add_argument("--card", help="prod writes: the requesting Linear card, e.g. ENGBACK-2248")
    ap.add_argument("--reason", help="for the card record only")
    a = ap.parse_args(argv)

    write = a.action != "status"
    if write and a.project == PROD and not (a.prod and (a.go or "").strip() and (a.card or "").strip()):
        print(f"REFUSED: {a.action} on {PROD} is a prod change: pass --prod, --go with Jerome's go quoted "
              "verbatim, and --card with the requesting card")
        return 1
    held = frozenset()
    if write:
        try:
            held = held_uids.load()
        except held_uids.Refused as e:
            print(f"REFUSED: {e}")
            return 1
    try:
        sa, home = credential()
        print(f"target  {a.project}  credential GCP_KEY ({sa.split('@')[0]}@, home project {home})")
        before = lookup(a.project, a.uid, a.email)
        uid = before.get("localId") or ""
        if a.uid and uid != a.uid:
            raise Unverified(f"lookup returned a different account in {a.project}")
        p = label(uid)
        b = view(before)
        print(f"account {p}…")
        print(line("before", b))
        if not write:
            return 0
        if uid in held:
            print(f"REFUSED: {held_uids.skip_line(uid)}; no {a.action}")
            return 1
        sent = max(int(now()), b["valid_since"])  # never move validSince back: that would un-revoke
        body = {"localId": uid, "disableUser": a.action == "disable"}
        if a.action == "disable":
            body["validSince"] = str(sent)
        call(a.project, "update", body)
        after = view(lookup(a.project, uid=uid))
        print(line("after", after))
        want = a.action == "disable"
        ok = after["disabled"] == want and (not want or after["valid_since"] >= sent)
        if not ok:
            print(f"FAIL: read-back doesn't match {a.action}")
            return 1
        print(f"OK: {a.action}d {p}…" + ("; refresh tokens revoked, open sessions end within 1 h" if want else ""))
        if a.project == PROD:
            stamp = datetime.fromtimestamp(sent, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            print(f"card record ({a.card}): {a.action} {p}… on {PROD} at {stamp}; go: \"{EMAIL.sub('…@…', a.go.strip())}\""
                  + (f"; reason: {a.reason}" if a.reason else ""))
        return 0
    except Unverified as e:
        print(f"UNVERIFIED: {e}")
        return 2


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    return run()


if __name__ == "__main__":
    sys.exit(main())
