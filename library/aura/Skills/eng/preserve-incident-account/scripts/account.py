#!/usr/bin/env python3
"""Preserve one account's data for an incident hold: resolve, plan, verify, then contain.

  account.py resolve --uid U --dir D [--uuid X] [--referral-code R]   read-only: live Auth state, ids
  account.py plan --dir D --go-id N [--ips-file F] [--owners F] [--card C]   go lines, no side effects
  account.py verify --dir D --manifest F [--manifest F ...]             each object listed by itself
  account.py contain --dir D                                            the containment go, once verified

Everything about the account lives in D (mode 700): account.json, email.txt, plan.json, the verify
result. Output that leaves the machine carries the uid prefix, never the full uid, the email or a row.
The go lines carry the full uid because Jerome pastes them into local sessions; they never carry the
email, only the path of email.txt. Calls run as GCP_KEY (gcloud_sa.py) with the AuraClaude user agent.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcloud_sa  # noqa: E402
import held_uids  # noqa: E402  (a copy; canonical in cleanup-orphaned-auth)
import writeup  # noqa: E402

FIREBASE_PROJECT = "firebase-auratech16"
LOG_PROJECTS = ("firebase-auratech16", "aura-main")
AURA_API = "https://services.aurahealth.io"
PREFIX = 6
UID_RE = re.compile(r"^[A-Za-z0-9]{20,128}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Owner keys. Who runs each one is personal setup, not repo content: owners.json maps a key to the
# session or person that owns it. Without the file, a go line names the key's role.
OWNERS = {
    "logs": "the log owner",
    "firebase": "the Firebase owner",
    "service-db": "the service-database owner",
    "payment-db": "the PaymentService owner",
    "intelligence-db": "the IntelligenceService owner",
    "content-db": "the ContentService owner",
    "analytics": "the analytics owner",
    "bugsnag": "the Bugsnag owner",
    "third-party": "the third-party owner",
}
PHASES = ("logs", "snapshot", "confirm", "contain")


class Refused(SystemExit):
    pass


def prefix(uid: str) -> str:
    return uid[:PREFIX]


def private_write(path: str, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def private_dir(d: str) -> str:
    os.makedirs(d, mode=0o700, exist_ok=True)
    if os.stat(d).st_mode & 0o077:
        raise Refused(f"REFUSED: {d} is readable by group or others; it holds the account's ids (chmod 700)")
    return d


def load(d: str, name: str) -> dict:
    with open(os.path.join(d, name), encoding="utf-8") as f:
        return json.load(f)


# --- GCP and Aura API --------------------------------------------------------------------------------
def gcloud(argv, run=None) -> str:
    out = (run or gcloud_sa.run)(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode:
        raise Refused(f"gcloud {' '.join(argv[:2])} failed as {gcloud_sa.credential()}: "
                      f"{(out.stderr or '').strip()[-300:]}")
    return out.stdout


def http(method, url, body=None, token=None, opener=None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "User-Agent": gcloud_sa.user_agent()})
    with (opener or urllib.request.urlopen)(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def lookup(field, value, run=None, opener=None) -> list:
    """Identity Toolkit accounts:lookup by localId or email. One id per call: never a listing."""
    token = gcloud(["auth", "print-access-token"], run).strip().splitlines()[-1]
    r = http("POST", f"https://identitytoolkit.googleapis.com/v1/projects/{FIREBASE_PROJECT}/accounts:lookup",
             {field: [value]}, token, opener)
    return r.get("users") or []


def ms_to_iso(v) -> str | None:
    try:
        return dt.datetime.fromtimestamp(int(v) / 1000, dt.timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def s_to_iso(v) -> str | None:
    try:
        return dt.datetime.fromtimestamp(int(v), dt.timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


def auth_state(users: list) -> dict:
    """The live state, read now. Never inherited from incident notes: on an earlier run the notes said
    "deleted" and the account was live and enabled."""
    if not users:
        return {"exists": False}
    u = users[0]
    return {"exists": True, "disabled": bool(u.get("disabled")), "createdAt": ms_to_iso(u.get("createdAt")),
            "lastLoginAt": ms_to_iso(u.get("lastLoginAt")), "lastRefreshAt": u.get("lastRefreshAt"),
            "tokensValidAfter": s_to_iso(u.get("validSince")),
            "providers": sorted(p.get("providerId", "") for p in u.get("providerUserInfo") or []) or ["password"]}


def aura_user(uid, opener=None) -> tuple[dict, str]:
    """The userDB row through the admin API (`GET /users/{uid}`), and why it is empty when it is.
    A 404 is "no userDB row"; any other failure is said as a failure, never read as an account
    without a uuid."""
    token = os.environ.get("AURA_API_TOKEN") or os.environ.get("AURA_ADMIN_TOKEN")
    if not token:
        return {}, "AURA_API_TOKEN unset"
    try:
        return http("GET", f"{AURA_API}/users/{uid}", token=token, opener=opener) or {}, ""
    except urllib.error.HTTPError as e:
        return {}, "no userDB row (404)" if e.code == 404 else f"admin API failed (HTTP {e.code})"
    except (urllib.error.URLError, ValueError) as e:
        return {}, f"admin API failed ({type(e).__name__})"


def log_retention(project, run=None) -> int | None:
    try:
        out = gcloud(["logging", "buckets", "describe", "_Default", "--location=global", f"--project={project}",
                      "--format=value(retentionDays)"], run).strip()
        return int(out) if out else None
    except (Refused, ValueError):
        return None


# --- resolve -----------------------------------------------------------------------------------------
def cmd_resolve(a, run=None, opener=None) -> int:
    if not UID_RE.match(a.uid):
        raise Refused("REFUSED: --uid is not a Firebase uid")
    d = private_dir(a.dir)
    users = lookup("localId", a.uid, run, opener)
    state = auth_state(users)
    email = (users[0].get("email") or "").strip() if users else ""
    sharing = 0
    if email:
        # Always from Auth: userDB's email can be null while Auth holds it.
        private_write(os.path.join(d, "email.txt"), email + "\n")
        sharing = len(lookup("email", email, run, opener))
    row, row_note = aura_user(a.uid, opener)
    uuid = (a.uuid or row.get("uuid") or "").lower() or None
    if uuid and not UUID_RE.match(uuid):
        raise Refused("REFUSED: the uuid doesn't look like a uuid")
    retention = {p: log_retention(p, run) for p in LOG_PROJECTS}
    acct = {"uid": a.uid, "uidPrefix": prefix(a.uid), "uuid": uuid,
            "referralCode": a.referral_code or row.get("referralCode") or None,
            "emailFile": os.path.join(d, "email.txt") if email else None, "accountsSharingEmail": sharing,
            "auth": state, "userDbFields": len(row), "userDbNote": row_note, "logRetentionDays": retention,
            "resolvedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    private_write(os.path.join(d, "account.json"), json.dumps(acct, indent=1))
    print(f"account {prefix(a.uid)}…: " + (
        "Auth NOT FOUND (check the uid; a deleted account can't be snapshotted)" if not state["exists"] else
        f"Auth exists, {'DISABLED' if state['disabled'] else 'enabled'}, created {state['createdAt']}, "
        f"last sign-in {state['lastLoginAt']}, tokens valid after {state['tokensValidAfter']}"))
    print(f"email: {'held in ' + acct['emailFile'] if email else 'none on the Auth record'}; "
          f"accounts sharing it: {sharing}")
    print(f"uuid: {'resolved' if uuid else 'UNRESOLVED (pass --uuid)'}; referral code: "
          f"{'resolved' if acct['referralCode'] else 'unresolved (the Firebase owner reads users/{uid}/referralCode)'}; "
          f"userDB row via admin API: {f'{len(row)} fields' if row else row_note}")
    for p, days in retention.items():
        print(f"log retention {p} _Default: {days if days is not None else 'unknown'} days")
    print(keep_list_line(a.uid))
    return 0


def keep_list_line(uid: str) -> str:
    """This skill is exempt from the keep list's refusal (preserving a held account is its job), but it
    says when its target is on it. It never deletes either way."""
    try:
        held = held_uids.load(required=False)
    except held_uids.Refused as e:
        return f"keep list: not read ({e})"
    if uid in held:
        return f"{prefix(uid)}…: keep list (a held account: preserved and disabled, never deleted)"
    return f"keep list: {prefix(uid)}… not listed"


# --- plan --------------------------------------------------------------------------------------------
def store_rows(acct: dict, bucket: str, stamp: str, ips: bool) -> list:
    """One row per store, in the order the run proved: logs first, every snapshot before any change,
    containment last. `folder` is where the object goes; names carry the uid prefix and a UTC date."""
    p, b = acct["uidPrefix"], f"gs://{bucket}"
    rows = []

    def add(phase, owner, store, folder, what, verify=True):
        rows.append({"id": f"{len(rows) + 1:02d}", "phase": phase, "owner": owner, "store": store,
                     "folder": folder if folder == "-" else f"{b}/{folder}", "what": what, "verify": verify})
    add("logs", "logs", "existing log holds", "-",
        "list the eng:preserve-prod-logs hold buckets first; copy only what they don't already cover", False)
    if ips:
        add("logs", "logs", "logs by IP", f"incident-accessed/logs-{p.lower()}/",
            "every entry from the IPs in the IP file (httpRequest.remoteIp), both projects")
    add("logs", "logs", "logs by id", f"incident-accessed/logs-{p.lower()}/",
        "entries naming the uid, uuid, referral code or email, from account creation to now, one project at "
        "a time with a pause between, tagged ours (AuraClaude UA, team principals, team IPs) vs actor")
    add("snapshot", "firebase", "Auth record", f"auth/{stamp}-{p}-pre/",
        "accounts:lookup, password hash, salt, passwordUpdatedAt and validSince dropped; plus the email lookup")
    add("snapshot", "firebase", "RTDB", f"rtdb/{stamp}-{p}/",
        "/users/{uid} and every uid-keyed node from rules.json (exact paths), the emailUser mapping, "
        "users/{uid}/referralCode and the referrer's referralFriends entry")
    add("snapshot", "firebase", "Storage", f"rtdb/{stamp}-{p}/",
        "profilePictures/{uid}/ (0 recorded too)")
    add("snapshot", "service-db", "userDB, coachDB, businessDB and other service DBs", "incident-accessed/userDB-",
        "token rows, the user row, userNoProfile, each DB's own user copy, then the scan: every public table's "
        "*id/*userId/*uid text columns = uid, uuid columns = uuid, jsonb top-level keys = uid, and email columns")
    add("snapshot", "payment-db", "paymentDB", "incident-accessed/paymentDB-",
        "the user row and the same scan (the referral code lives in RTDB, not here)")
    add("snapshot", "intelligence-db", "intelligenceDB", "incident-accessed/intelligenceDB-",
        "the user row and the same scan (llmCall is BigQuery, in the analytics row)")
    add("snapshot", "content-db", "contentDB", "incident-accessed/contentDB-", "the user row and the same scan")
    add("snapshot", "analytics", "BigQuery and Mixpanel", "bigquery/",
        "raw_events (uid fields, email, uuid, referral), events, user_following, llm_call, aurie_cost, "
        "activation; Mixpanel People, Engage and Export by uid, email and merged device ids")
    add("snapshot", "bugsnag", "Bugsnag", "bugsnag/", "errors by uid and by uuid (0 recorded too)")
    add("snapshot", "third-party", "Iterable, Stripe", "incident-accessed/third-party/",
        "Iterable both projects by user id (Aurie refuses email lookups: not searchable, not 0); "
        "Stripe customers by email")
    add("confirm", "analytics", "every object above", "-", "account.py verify: each object listed, size matched")
    add("contain", "firebase", "Auth", f"auth/{stamp}-{p}-post-containment/",
        "disable (never delete), revoke refresh tokens, read back, post-containment snapshot", False)
    add("contain", "service-db", "userDB token rows and token cache", "-",
        "after the token-row export is confirmed: delete the token rows and the cache keys. UserService skips "
        "revocation and expiry for accounts with a role, so only this cuts off a staff or coach token held "
        "outside the app",
        False)
    return rows


GO_TEXT = {
    "logs": "copies, read-only, {what_logs} to {folder} with a per-source manifest (source, query, time range, "
            "count, bytes, crc32c), nothing excluded, no delete",
    "firebase": "writes read-only, exact-path snapshots for {uid}: the Auth record (no password hash) to "
                "{auth}, /users/{uid}, the uid-keyed nodes, the email mapping (email in {email}), the referral "
                "code and referrer, and Storage profilePictures/{uid}/ to {rtdb}; no change, no delete",
    "service-db": "exports, read-only, every row keyed to uid {uid} or uuid {uuid} (token, user, userNoProfile, "
                  "each DB's user copy, and the id, uuid, jsonb and email-column scan, email in {email}) as JSON "
                  "to {bucket}/incident-accessed/<db>-<table>-{p}-<date>.json, with a per-table count (0 recorded too); deletes nothing",
    "payment-db": "exports, read-only, paymentDB's rows for uid {uid} (the user row and the scan) as JSON to "
                  "{folder}<table>-{p}-<date>.json, with a per-table count (0 recorded too); no change",
    "intelligence-db": "exports, read-only, intelligenceDB's rows for uid {uid} (the user row and the scan) as "
                       "JSON to {folder}<table>-{p}-<date>.json, 0 recorded too; no change",
    "content-db": "exports, read-only, contentDB's rows for uid {uid} (the user row and the scan) as JSON to "
                  "{folder}<table>-{p}-<date>.json, 0 recorded too; no change",
    "analytics": "exports BigQuery and Mixpanel data for held account {uid} (by uid, its email in {email}, uuid "
                 "{uuid} and referral code, incl. merged $device ids and Mixpanel People) to {bucket}/bigquery/ "
                 "and mixpanel/, no local copy",
    "bugsnag": "searches Bugsnag by uid {uid} and uuid {uuid} and writes the results (0 recorded too) to {folder}",
    "third-party": "exports, read-only, the Iterable (both projects) and Stripe records for uid {uid} and the "
                   "email in {email} to {folder}, 0 or not-searchable recorded",
}


def go_lines(rows, acct, go_id, bucket, owners, ips_file) -> list:
    """One paste line per owner: a relayed go is refused by the owner, so Jerome types each one there."""
    out = []
    names = {**OWNERS, **owners}
    by_owner = {}
    for r in rows:
        if r["phase"] in ("logs", "snapshot"):
            by_owner.setdefault(r["owner"], []).append(r)
    for owner, rs in by_owner.items():
        folders = [r["folder"] for r in rs if r["folder"] != "-"]
        fill = {"uid": acct["uid"], "p": acct["uidPrefix"], "uuid": acct.get("uuid") or "(unresolved)", "bucket": f"gs://{bucket}",
                "email": acct.get("emailFile") or "(no email)", "folder": folders[-1] if folders else f"gs://{bucket}",
                "auth": next((r["folder"] for r in rs if r["store"] == "Auth record"), ""),
                "rtdb": next((r["folder"] for r in rs if r["store"] == "RTDB"), ""),
                "what_logs": ("every log entry from the IPs in " + ips_file + " and " if ips_file else "") +
                             "every entry naming uid " + acct["uid"] + ", its uuid, referral code or email "
                             "(in " + (acct.get("emailFile") or "-") + ") since the account's creation, "
                             "one project at a time, tagged ours vs actor"}
        out.append({"owner": owner, "who": names[owner],
                    "go": f"go {go_id}: {names[owner]} {GO_TEXT[owner].format(**fill)}; counts only"})
    return out


# UserService's Redis (db 0), every key under its package and minor version, `@aura/user-service:<major.minor>/`.
# Token and session-cookie keys are sha256 hashes. verifyToken reads the cache before the token table, so
# deleting the rows alone leaves cached tokens valid for up to 24 h.
# The keys come from the ids the delete returns, not from the earlier export: a token minted between the
# snapshot and containment has a row (and a cache entry) the export never saw.
TOKEN_CACHE = ("deletes the UserService Redis cache keys "
               "`@aura/user-service:<major.minor>/userService/verifyToken/<id>/withProfile/true` and "
               "`…/withProfile/false` for every id that delete returned (each a `sha256:<hex>`), under every "
               "live `<major.minor>`, and the `@aura/user-service:*/userService/sessionCookie/sha256:*` keys "
               "whose cached value's id is {uid}")


def contain_lines(acct, rows, go_id, owners) -> list:
    """Firebase first (disable, never delete; revoke), then the token step: an account with a role skips
    UserService's revocation check and token expiry, so its stored tokens keep working until the userDB
    token rows and their cache keys are gone (ENGBACK-2320)."""
    folder = next(r["folder"] for r in rows if r["phase"] == "contain" and r["owner"] == "firebase")
    names = {**OWNERS, **owners}
    return [
        f"go {go_id}: {names['firebase']} disables (no delete) Auth account {acct['uid']}, revokes its refresh "
        f"tokens, reads back disabled=true and tokensValidAfter, and writes the post-containment Auth snapshot "
        f"to {folder}; counts only",
        f"go {go_id}: {names['service-db']}, in one transaction, exports to the hold any userDB \"token\" row "
        f"where \"userId\" = '{acct['uid']}' that the earlier export doesn't hold, then deletes all of that "
        f"account's \"token\" rows with RETURNING id, then {TOKEN_CACHE.replace('{uid}', acct['uid'])}, and reads back 0 rows and 0 keys; "
        f"no other change; counts only",
    ]


def cmd_plan(a) -> int:
    d = private_dir(a.dir)
    acct = load(d, "account.json")
    owners = {}
    if a.owners and os.path.exists(os.path.expanduser(a.owners)):
        with open(os.path.expanduser(a.owners), encoding="utf-8") as f:
            owners = {k: v for k, v in json.load(f).items() if k in OWNERS}
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")
    rows = store_rows(acct, a.bucket, stamp, bool(a.ips_file))
    gos = go_lines(rows, acct, a.go_id, a.bucket, owners, a.ips_file)
    plan = {"uidPrefix": acct["uidPrefix"], "bucket": a.bucket, "goId": a.go_id, "card": a.card,
            "stamp": stamp, "rows": rows, "gos": gos, "contain": contain_lines(acct, rows, a.go_id, owners)}
    private_write(os.path.join(d, "plan.json"), json.dumps(plan, indent=1))
    auth = acct.get("auth") or {}
    out = []
    out.append(f"# Preservation plan for {acct['uidPrefix']}… ({a.card or 'no card'})\n")
    if not auth.get("exists"):
        out.append("**Auth record not found.** Re-check the uid before planning containment.\n")
    elif auth.get("disabled"):
        out.append("**Already disabled.** Snapshot it as it is now; containment then revokes, reads back and runs "
              "the token step.\n")
    if not acct.get("uuid"):
        out.append("**uuid unresolved:** the scans by uuid are skipped until resolve runs with --uuid.\n")
    days = [v for v in (acct.get("logRetentionDays") or {}).values() if v]
    if days:
        out.append(f"Log retention: _Default keeps {min(days)} days, so the account's earliest entries expire "
              f"{min(days)} days after they were written. Logs run first.\n")
    out.append("| # | Phase | Owner | Store | To | What |\n|---|---|---|---|---|---|")
    for r in rows:
        out.append(f"| {r['id']} | {r['phase']} | {r['owner']} | {r['store']} | {r['folder']} | {r['what']} |")
    out.append("\n## Paste lines (one per owner, typed by Jerome in that owner's session)\n")
    for g in gos:
        out.append(f"**{g['who']}**\n> {g['go']}\n")
    out.append("## Then\n\nEach owner reports path, bytes and crc32c (taken on the uploading side). Run "
          "`account.py verify`; `account.py contain` prints the containment lines once every object checks out.")
    # An incident document is labelled and fact-only.
    print(writeup.render("\n".join(out)))
    return 0


# --- verify and contain ------------------------------------------------------------------------------
def read_manifests(paths) -> list:
    """TSV rows: gs:// path, bytes, crc32c, rows. A row with rows 0 and no path records a zero."""
    out = []
    for p in paths:
        with open(os.path.expanduser(p), encoding="utf-8") as f:
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                cols = (line.rstrip("\n").split("\t") + ["", "", "", ""])[:4]
                out.append({"path": cols[0], "bytes": cols[1], "crc32c": cols[2], "rows": cols[3]})
    return out


def listed_size(path, run=None) -> int | None:
    """One URL per call: `gcloud storage ls` with several fails as a whole when any one matches nothing."""
    out = (run or gcloud_sa.run)(["storage", "ls", "-l", path], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace")
    if out.returncode:
        return None
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] == path and parts[0].isdigit():
            return int(parts[0])
    return None


def cmd_verify(a, run=None) -> int:
    d = private_dir(a.dir)
    plan = load(d, "plan.json")
    entries = read_manifests(a.manifest)
    bad, ok, zeros = [], 0, 0
    for e in entries:
        if not e["path"]:
            zeros += e["rows"] == "0"
            continue
        if not e["path"].startswith(f"gs://{plan['bucket']}/"):
            bad.append((e["path"], "outside the hold bucket"))
            continue
        size = listed_size(e["path"], run)
        if size is None:
            bad.append((e["path"], "not listed"))
        elif not e["bytes"].isdigit() or size != int(e["bytes"]):
            bad.append((e["path"], f"listed {size} B, manifest {e['bytes'] or '?'} B"))
        else:
            ok += 1
    # A store with no manifest entry under its folder is not preserved, whatever else checked out.
    folders = {r["folder"] for r in plan["rows"] if r["verify"] and r["phase"] in ("logs", "snapshot")}
    covered = {f for f in folders if any(e["path"].startswith(f) for e in entries)}
    missing = sorted(folders - covered)
    token_export = any("/userDB-token-" in e["path"] for e in entries if e["path"])
    result = {"checkedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "ok": ok,
              "tokenExport": token_export and not any("/userDB-token-" in p for p, _ in bad),
              "zeros": zeros, "bad": len(bad), "missingFolders": missing, "passed": not bad and not missing}
    private_write(os.path.join(d, "verify.json"), json.dumps(result, indent=1))
    for path, why in bad:
        print(f"FAIL {path.replace(plan['bucket'], '<bucket>')}: {why}")
    for f in missing:
        print(f"MISSING no manifest entry under {f.replace(plan['bucket'], '<bucket>')}")
    print(f"verify: {ok} object(s) match, {zeros} zero(s) recorded, {len(bad)} failed, "
          f"{len(missing)} folder(s) without an entry → {'PASSED' if result['passed'] else 'NOT PASSED'}")
    return 0 if result["passed"] else 1


def cmd_contain(a) -> int:
    d = private_dir(a.dir)
    plan = load(d, "plan.json")
    try:
        v = load(d, "verify.json")
    except FileNotFoundError:
        raise Refused("REFUSED: run verify first; containment comes after every object is confirmed")
    if not v.get("passed"):
        raise Refused("REFUSED: the last verify did not pass; containment waits for every object")
    lines = plan["contain"] if isinstance(plan["contain"], list) else [plan["contain"]]
    print(lines[0])
    if len(lines) > 1:
        if v.get("tokenExport"):
            print(lines[1])
        else:
            # The rows are deleted only once their export is in the hold and listed.
            print("HELD: the token step waits for a userDB-token export in a verified manifest "
                  "(0 rows is recorded as a zero with no object, and then there is nothing to delete).")
    return 0


def main(argv=None, run=None, opener=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve")
    r.add_argument("--uid", required=True)
    r.add_argument("--dir", required=True)
    r.add_argument("--uuid")
    r.add_argument("--referral-code")
    p = sub.add_parser("plan")
    p.add_argument("--dir", required=True)
    p.add_argument("--go-id", required=True)
    p.add_argument("--bucket", default="aura-legal-hold-prod6926")
    p.add_argument("--ips-file")
    p.add_argument("--owners", default="~/.claude/preserve-incident-account/owners.json")
    p.add_argument("--card")
    v = sub.add_parser("verify")
    v.add_argument("--dir", required=True)
    v.add_argument("--manifest", action="append", required=True)
    c = sub.add_parser("contain")
    c.add_argument("--dir", required=True)
    a = ap.parse_args(argv)
    a.dir = os.path.expanduser(a.dir)
    try:
        if a.cmd == "resolve":
            return cmd_resolve(a, run, opener)
        if a.cmd == "plan":
            return cmd_plan(a)
        if a.cmd == "verify":
            return cmd_verify(a, run)
        return cmd_contain(a)
    except Refused as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
