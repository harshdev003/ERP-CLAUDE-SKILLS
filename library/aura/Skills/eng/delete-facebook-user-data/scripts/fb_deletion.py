#!/usr/bin/env python3
"""Process Meta "User Data Deletion Request" ID files against Aura.

  plan            CSV(s) of app-scoped Facebook IDs -> classified plan JSON (read-only)
  delete-accounts full account delete via UserService DELETE /users/:userId   (dry-run unless --execute)
  delete-logins   delete only the Firebase Facebook login (accounts:batchDelete) (dry-run unless --execute)

Classes: account (login is the profile's own uid), secondary (no profile under the uid, but its
email resolves to a live profile), orphan (no profile, nothing resolves), not_found (no Aura login).

Auth: GCP_KEY (SA on firebase-auratech16) for Identity Toolkit; AURA_API_TOKEN for the read-only
GETs; AURA_ADMIN_TOKEN (admin/service role) for DELETE /users/:userId. delete-logins needs a
temporary roles/firebaseauth.admin grant on the SA.

Both deletes need the keep list (held_uids.py): without it they refuse, and a listed uid is skipped
(one "keep list" line, uid prefix only; "kept: N" on a dry run too) before any delete or backup.
"""
import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import held_uids  # noqa: E402  (a copy; canonical in cleanup-orphaned-auth)

PROJECT = "firebase-auratech16"
IDTK = f"https://identitytoolkit.googleapis.com/v1/projects/{PROJECT}"
USERS = "https://services.aurahealth.io/users"

_CREDS = None



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
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls in RTDB and GCP logs.

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

def _gcp_token():
    global _CREDS
    if _CREDS is None:
        from google.oauth2 import service_account
        _CREDS = service_account.Credentials.from_service_account_info(
            json.loads(os.environ["GCP_KEY"]),
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not _CREDS.valid:
        import google.auth.transport.requests
        _CREDS.refresh(google.auth.transport.requests.Request())
    return _CREDS.token


def _idtk(method, body):
    req = urllib.request.Request(
        f"{IDTK}/{method}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + _gcp_token(),
                 "User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


def _aura(method, path, token, body=None):
    headers = {"Authorization": "Bearer " + token, "User-Agent": user_agent()}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(USERS + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:200]}


def _emails(u):
    found = [u.get("email")] + [p.get("email") for p in u.get("providerUserInfo", [])]
    return sorted({e for e in found if e})


def _mask(email):
    return "@" + email.split("@")[-1] if email else "-"


def _lookup_uids(uids):
    out = {}
    for i in range(0, len(uids), 100):
        for u in _idtk("accounts:lookup", {"localId": uids[i:i + 100]}).get("users", []):
            out[u["localId"]] = u
    return out


def cmd_plan(args):
    read_tok = os.environ.get("AURA_API_TOKEN") or sys.exit("plan needs AURA_API_TOKEN")
    rows = []
    for path in args.csv:
        batch = os.path.splitext(os.path.basename(path))[0]
        # utf-8-sig: Meta's export starts with a BOM; read as utf-8 it glues onto the first ID
        # and that ID silently comes back "not found".
        ids = [line.strip().strip(",") for line in open(path, encoding="utf-8-sig") if line.strip()]
        for fid in ids:
            users = _idtk("accounts:lookup", {
                "federatedUserId": [{"providerId": "facebook.com", "rawId": fid}]}).get("users", [])
            if not users:
                rows.append({"batch": batch, "facebook_id": fid, "class": "not_found"})
                continue
            for u in users:
                uid, emails = u["localId"], _emails(u)
                row = {"batch": batch, "facebook_id": fid, "uid": uid,
                       "providers": [p["providerId"] for p in u.get("providerUserInfo", [])],
                       "email_domains": [_mask(e) for e in emails],
                       "last_login_ms": u.get("lastLoginAt")}
                code, _ = _aura("GET", f"/{uid}", read_tok)
                if code == 200:
                    row["class"] = "account"
                elif code != 404:
                    row["class"] = "error"
                    row["error"] = f"GET /users/:uid -> {code}"
                else:
                    row["class"] = "orphan"
                    for e in emails:
                        # The address goes in the body, never in a URL (service role today; agent once UserService allows it).
                        c2, body = _aura("POST", "/email/lookup", read_tok, {"email": e})
                        if c2 == 200:
                            row["class"] = "secondary"
                            row["main_profile"] = body.get("id") or body.get("userId")
                            break
                        if c2 != 404:
                            row["class"] = "error"
                            row["error"] = f"POST /users/email/lookup -> {c2}"
                            break
                rows.append(row)
    json.dump(rows, open(args.out, "w"), indent=1)

    print(f"{'batch':<12} {'class':<10} {'aura uid':<30} {'emails':<16} main profile")
    for r in rows:
        print(f"{r['batch']:<12} {r['class']:<10} {r.get('uid', '-'):<30} "
              f"{','.join(r.get('email_domains', [])) or '-':<16} {r.get('main_profile', '')}")
    counts = {}
    for r in rows:
        counts[r["class"]] = counts.get(r["class"], 0) + 1
    print(f"\n{len(rows)} rows: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"plan -> {args.out}")


def _load(plan, classes):
    """The plan's rows of these classes, minus every uid on the keep list (refused without the list)."""
    try:
        held = held_uids.load()
    except held_uids.Refused as e:
        sys.exit(f"REFUSED: {e}")
    rows = [r for r in json.load(open(plan)) if r["class"] in classes]
    # A secondary login belongs to the profile in main_profile: a held profile keeps its logins too.
    listed = lambda r: r["uid"] in held or (r.get("main_profile") or "") in held
    kept = [r for r in rows if listed(r)]
    for r in kept:
        print(held_uids.skip_line(r.get("main_profile") if r.get("main_profile") in held else r["uid"]))
    print(f"kept: {len(kept)}")
    return [r for r in rows if not listed(r)]


def cmd_delete_accounts(args):
    rows = _load(args.plan, {"account"})
    if not rows:
        print("no 'account' rows — nothing to delete")
        return
    if not args.execute:
        print(f"DRY RUN — would DELETE /users/:userId for {len(rows)} account(s):")
        for r in rows:
            print(f"  {r['batch']}  {r['uid']}")
        return
    tok = os.environ.get("AURA_ADMIN_TOKEN") or sys.exit("delete-accounts needs AURA_ADMIN_TOKEN")
    for r in rows:
        code, body = _aura("DELETE", f"/{r['uid']}", tok)
        key = (body.get("error") or {}).get("key", "") if isinstance(body, dict) else ""
        verdict = "deleted" if code == 204 else f"NOT deleted ({code} {key})"
        print(f"  {r['batch']}  {r['uid']}  {verdict}")


def cmd_delete_logins(args):
    rows = _load(args.plan, {"secondary", "orphan"})
    uids = sorted({r["uid"] for r in rows})
    if not uids:
        print("no secondary/orphan rows — nothing to delete")
        return
    if not args.execute:
        print(f"DRY RUN — would delete {len(uids)} Firebase login(s):")
        for r in rows:
            print(f"  {r['batch']}  {r['class']:<9} {r['uid']}  {r.get('main_profile', '')}")
        return
    # Firebase deletion is irreversible: back the records up before deleting anything.
    recs = _lookup_uids(uids)
    with open(args.backup, "a") as f:
        for u in recs.values():
            f.write(json.dumps(u) + "\n")
    print(f"backed up {len(recs)} record(s) -> {args.backup}")
    todo = [u for u in uids if u in recs]
    if not todo:
        print(f"all {len(uids)} login(s) already gone — nothing to delete")
        return
    for attempt in range(6):
        try:
            resp = _idtk("accounts:batchDelete", {"localIds": todo, "force": True})
            break
        except urllib.error.HTTPError as e:
            txt = e.read().decode()[:200]
            if e.code in (429, 500, 503) or "INSUFFICIENT_PERMISSION" in txt:
                wait = 15 * (attempt + 1)
                print(f"  {e.code} ({txt[:60]}…) retry in {wait}s — is the firebaseauth.admin grant in place?")
                time.sleep(wait)
                continue
            sys.exit(f"FATAL HTTP {e.code}: {txt}")
    else:
        sys.exit("gave up after retries — check the roles/firebaseauth.admin grant")
    errs = resp.get("errors") or []
    print(f"deleted {len(todo) - len(errs)}, errors {len(errs)}")
    still = _lookup_uids(todo)
    print(f"verify: {len(still)} of {len(todo)} still present (expect 0)")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--csv", nargs="+", required=True)
    p.add_argument("--out", required=True)
    for name in ("delete-accounts", "delete-logins"):
        d = sub.add_parser(name)
        d.add_argument("--plan", required=True)
        d.add_argument("--execute", action="store_true")
        if name == "delete-logins":
            d.add_argument("--backup", required=True)
    args = ap.parse_args()
    {"plan": cmd_plan, "delete-accounts": cmd_delete_accounts,
     "delete-logins": cmd_delete_logins}[args.cmd](args)


if __name__ == "__main__":
    main()
