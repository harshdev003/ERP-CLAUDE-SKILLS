#!/usr/bin/env python3
"""Orphaned Firebase auth cleanup — dump-free, safety-gated.

Subcommands:
  harvest  Query GCP logs for the orphan signals UserService emits and emit the distinct
           candidate UIDs (no Postgres dump needed).
  confirm  For each candidate: Firebase accounts:lookup -> age/activity gates -> a read-only
           POST /users/email/lookup; keep only UIDs whose email resolves to NO profile.
  confirm-deleted
           For reconcile's deleted_with_live_auth list (profile soft-deleted, Auth account left
           live): re-check each uid read-only and keep it only when the Auth account is live and
           enabled, the profile still has deletedAt, and the delete is older than the grace window.
  confirm-no-email
           Profileless Auth with no email anywhere: keep a uid only when it is inactive past the
           window, has no profile (GET /users/<uid> 404, exact RTDB /users/{uid} absent) and no
           userId claim to a live profile. Prints counts only.
  delete   Back up records, then delete confirmed orphans via accounts:batchDelete
           (resumable, verified, paced: batches of 100, --pause apart). Requires a temporary roles/firebaseauth.admin grant.
  recent   Dump-free drip finder: pull auth created in the last N hours (sorted query, no
           full export), drop UIDs that have a profile, then resolve the rest read-only via
           POST /users/email/lookup so existing users re-logging-in (multi-account per email) are
           not miscounted as orphans -> true orphan UIDs for confirm/delete.

Auth: GCP_KEY (service-account JSON for project firebase-auratech16) for logging / lookup /
batchDelete; AURA_API_TOKEN (or AURA_USER_TOKEN) for the read-only GET /users/<uid> and
POST /users/email/lookup lookups. The orphan check is read-only — it never signs in, so it never bumps lastLoginAt.
Run with PYTHONSAFEPATH=1 so a stray local module can't shadow google.* imports.

ONLY deletes the Firebase credential — orphans have no profile/subscriptions/tokens, and a
soft-deleted profile has nothing left to restore (UserService sets deletedAt back to null only
on userCreated / userUpdated).
Emails are masked (domain only) in stdout; full records go to the local backup file.
"""
import argparse
import csv
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

PROJECT = "firebase-auratech16"
CLUSTER = "aura-prod-cluster-a"
IDTK = "https://identitytoolkit.googleapis.com/v1/projects/" + PROJECT
USERS_URL = "https://services.aurahealth.io/users"  # GET /users/<uid>: 200 = profile, 404 = none
USERS_LOOKUP_URL = "https://services.aurahealth.io/users/email/lookup"  # POST {"email"}: 200 = resolves, 404 = none
LOGGING_URL = "https://logging.googleapis.com/v2/entries:list"
RTDB_URL = os.environ.get("FIREBASE_DATABASE_URL", "https://auratech16.firebaseio.com")
RTDB_CHUNK = 200  # rtdb_get.js refuses more paths per call without --bulk
ORPHAN_SIGNALS = [
    "No profile for the token",
    "Invalid user id from decoded token",
    "Stored user no profile in database",
]

_CREDS = None
# Accounts that must never be deleted: the keep list (held_uids.py). A delete refuses to run without
# it, so a missing or broken list never reads as an empty one.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import held_uids  # noqa: E402  (canonical copy; docs/canonical-sources.md)

HELD_EXIT = held_uids.REFUSED_EXIT



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

def _creds():
    global _CREDS
    if _CREDS is None:
        from google.oauth2 import service_account
        info = json.loads(os.environ["GCP_KEY"])
        _CREDS = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    if not _CREDS.valid:
        import google.auth.transport.requests
        _CREDS.refresh(google.auth.transport.requests.Request())
    return _CREDS


def _sa_info():
    return json.loads(os.environ["GCP_KEY"])


def _post(url, body, bearer=None, headers=None):
    hdrs = {"Content-Type": "application/json", "User-Agent": user_agent()}
    if bearer:
        hdrs["Authorization"] = "Bearer " + bearer
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdrs)
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def load_held() -> frozenset:
    """The keep list; exit 2 when it is missing, empty or can't be trusted. Nothing is deleted then."""
    try:
        return held_uids.load()
    except held_uids.Refused as e:
        print(f"REFUSED: {e}. Nothing was deleted.", file=sys.stderr)
        sys.exit(HELD_EXIT)


def mask(e):
    # Domain only — never expose the local part (Compliance/Privacy).
    return "…@" + e.split("@", 1)[1] if e and "@" in e else "-"


def parse_since(s):
    s = s.strip().lower()
    n, unit = int(s[:-1]), s[-1]
    mult = {"h": 1, "d": 24, "w": 168}[unit]
    return datetime.now(timezone.utc) - timedelta(hours=n * mult)


def _emails(u):
    # Linked addresses live under different keys by source: accounts:lookup/batchGet ->
    # providerUserInfo; Admin SDK -> providerData; custom NDJSON export -> providerEmails.
    es = [u.get("email")] + (u.get("providerEmails") or [])
    for k in ("providerUserInfo", "providerData"):
        es += [p.get("email") for p in (u.get(k) or [])]
    return [e.lower() for e in es if e]


def _to_ms(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        if not v:
            return 0
        try:
            return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return 0


# --------------------------------------------------------------------------- recent
def _has_profile(uid):
    """GET /users/<uid>: True if a profile exists (200), False if none (404), None on error.

    UserService answers 401 to an unauthenticated call, which would read as an error for every uid.
    """
    req = urllib.request.Request(f"{USERS_URL}/{urllib.parse.quote(uid)}")
    req.add_header("User-Agent", user_agent())
    req.add_header("Authorization", "Bearer " + _aura_token())
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except Exception:
        return None


def cmd_recent(args):
    """Find orphans created in the last N hours without a full export.

    Two stages: (1) pull recently-created auth via accounts:query (sorted by CREATED_AT desc,
    paging until the window cutoff — ~1-2 pages for 24h) and drop any UID that already has a
    profile under its own id (fast `GET /users/<uid>` pre-filter); (2) resolve the survivors with
    the shared read-only `_is_orphan_readonly` (retrieve by uid via `GET /users/<uid>`, then
    `POST /users/email/lookup`) — a uid match or a resolving email means an existing user
    re-logging-in (multi-account per email) is reported as `sso-resolvable`, NOT a true orphan.
    Both are read-only lookups — they never sign in, so lastLoginAt is untouched. Both stages need AURA_API_TOKEN
    (or AURA_USER_TOKEN).
    Writes the confirmed orphans (UID per line) for `confirm`/`delete`; confirm's age/activity
    gates still apply, so a short window mostly won't clear --min-age-days — intended (a fresh
    abandoned signup may still complete). The fast path for the daily drip.
    """
    if not (os.environ.get("AURA_API_TOKEN") or os.environ.get("AURA_USER_TOKEN")):
        sys.exit("recent needs AURA_API_TOKEN (or AURA_USER_TOKEN) for the read-only profile and "
                 "email lookups — add it under env in ~/.claude/settings.json.")
    now = datetime.now(timezone.utc)
    cutoff_ms = int((now - timedelta(hours=args.hours)).timestamp() * 1000)
    floor_ms = int((now - timedelta(minutes=args.min_age_minutes)).timestamp() * 1000)
    qurl = f"{IDTK}/accounts:query"
    recent, offset, page = [], 0, 0
    while offset < args.max_scan:
        body = {"sortBy": "CREATED_AT", "order": "DESC", "returnUserInfo": True,
                "limit": "500", "offset": str(offset)}
        users = _post(qurl, body, bearer=_creds().token).get("userInfo") or []
        if not users:
            break
        stop = False
        for u in users:
            cms = _to_ms(u.get("createdAt"))
            if cms >= cutoff_ms:
                recent.append(u)
            elif cms:
                stop = True  # sorted desc: once we drop below the window, we're done
        page += 1
        print(f"  …page {page}, {len(recent):,} auth in window", flush=True)
        if stop:
            break
        offset += 500

    # Stage 1 — fast pre-filter: drop accounts that already have a profile under their own uid.
    candidates, healthy, in_flight, errs = [], 0, 0, 0
    for u in recent:
        has = _has_profile(u.get("localId"))
        if has is True:
            healthy += 1
        elif has is None:
            errs += 1
        elif _to_ms(u.get("createdAt")) > floor_ms:
            in_flight += 1  # too new to judge — profile may still be in flight
        else:
            candidates.append(u)
        time.sleep(0.02)
    if recent and errs == len(recent):
        sys.exit(f"recent: every profile lookup failed ({errs:,} of {len(recent):,}) — check "
                 "AURA_API_TOKEN and UserService before trusting an empty result.")

    # Stage 2 — resolve each candidate with the shared read-only check `_is_orphan_readonly` (the
    # same function `confirm` uses): retrieve by uid (`GET /users/<uid>`) — a profile can exist
    # under the uid even when its email wouldn't resolve, so the uid match wins — then fall back to
    # `POST /users/email/lookup`. A uid match or a resolving email => not an orphan (`sso-resolvable`,
    # e.g. an existing user re-logging-in with another provider); neither => true orphan. Read-only
    # (lookups only) — never signs in, so lastLoginAt is untouched and confirm's inactivity gate stays
    # valid.
    orphans, resolvable = [], []
    for u in candidates:
        try:
            (orphans if _is_orphan_readonly(u.get("localId"), _emails(u)) else resolvable).append(u)
        except (urllib.error.HTTPError, RuntimeError):
            errs += 1
            orphans.append(u)  # couldn't resolve -> keep it for confirm to re-check
        time.sleep(0.02)

    with open(args.out, "w") as f:
        f.write("\n".join(u.get("localId") for u in orphans) + ("\n" if orphans else ""))
    print(f"\nrecent: last {args.hours}h — {len(recent):,} new auth | healthy {healthy} | "
          f"orphans {len(orphans)} | sso-resolvable {len(resolvable)} | "
          f"in-flight<{args.min_age_minutes}m {in_flight} | errs {errs} -> {args.out}")
    for u in orphans:
        ems = _emails(u)
        provs = [p.get("providerId") for p in (u.get("providerUserInfo") or [])]
        cms = _to_ms(u.get("createdAt"))
        cts = datetime.fromtimestamp(cms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M") if cms else "?"
        print(f"  {u.get('localId')}  {cts}  {mask(ems[0]) if ems else '(no email)'}  {provs}")
    print("\nNext — confirm + delete (dry-run by default; confirm's age/activity gates still apply):")
    print(f"  python3 orphan_cleanup.py confirm --in {args.out} --out /tmp/orphan_confirmed.txt")
    print(f"  python3 orphan_cleanup.py delete  --in /tmp/orphan_confirmed.txt            # dry-run")
    print(f"  python3 orphan_cleanup.py delete  --in /tmp/orphan_confirmed.txt --execute  # real")


# --------------------------------------------------------------------------- harvest
def cmd_harvest(args):
    start = parse_since(args.since).strftime("%Y-%m-%dT%H:%M:%SZ")
    signal_filter = " OR ".join(f'jsonPayload.message:"{s}"' for s in ORPHAN_SIGNALS)
    flt = (
        f'resource.type="k8s_container" '
        f'AND resource.labels.cluster_name="{CLUSTER}" '
        f'AND resource.labels.container_name="{args.container}" '
        f'AND timestamp>="{start}" AND ({signal_filter})'
    )
    uids, token, pages = set(), None, 0
    while True:
        body = {
            "resourceNames": [f"projects/{PROJECT}"],
            "filter": flt,
            "orderBy": "timestamp desc",
            "pageSize": 1000,
        }
        if token:
            body["pageToken"] = token
        resp = _post(LOGGING_URL, body, bearer=_creds().token)
        for e in resp.get("entries", []):
            jp = e.get("jsonPayload") or {}
            uid = jp.get("signInUid") or jp.get("tokenUserId") or jp.get("userId") or (jp.get("verifiedToken") or {}).get("tokenUserId")
            if uid:
                uids.add(uid.strip())
        token = resp.get("nextPageToken")
        pages += 1
        print(f"  …page {pages}, {len(uids):,} distinct candidate UIDs", flush=True)
        if not token:
            break
    with open(args.out, "w") as f:
        f.write("\n".join(sorted(uids)) + "\n")
    print(f"harvest: {len(uids):,} candidate UIDs (since {args.since}) -> {args.out}")


# --------------------------------------------------------------------------- confirm
def _lookup(uids):
    """Batch accounts:lookup (<=100). Returns {uid: record}."""
    out = {}
    for i in range(0, len(uids), 100):
        chunk = uids[i:i + 100]
        resp = _post(f"{IDTK}/accounts:lookup", {"localId": chunk}, bearer=_creds().token)
        for u in resp.get("users", []):
            out[u["localId"]] = u
    return out


def _aura_token():
    t = os.environ.get("AURA_API_TOKEN") or os.environ.get("AURA_USER_TOKEN")
    if not t:
        raise RuntimeError(
            "AURA_API_TOKEN (or AURA_USER_TOKEN) is required for the read-only email resolution. "
            "Add it under env in ~/.claude/settings.json."
        )
    return t


def _email_resolves(email):
    """Read-only: True if `email` maps to a live Aura profile (sso-resolvable), False if not.

    POST /users/email/lookup with {"email": …} in the body (service role today; agent once UserService allows it), so the address
    never sits in a URL -> 200 = resolves, 404 = no profile for this email. A lookup, not a write: unlike minting a token via signInWithCustomToken, it NEVER signs in and NEVER bumps the
    account's lastLoginAt/lastRefreshAt. That keeps confirm's inactivity gate valid and leaves the
    account's activity record untouched.
    """
    req = urllib.request.Request(USERS_LOOKUP_URL, data=json.dumps({"email": email}).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", user_agent())
    req.add_header("Authorization", "Bearer " + _aura_token())
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def _is_orphan_readonly(uid, emails):
    """True iff there is no profile under `uid` AND none of `emails` resolves to a live profile.

    Read-only check (uid -> email -> profile): `GET /users/<uid>` (404) + `POST /users/email/lookup`
    (404). No sign-in, so no lastLoginAt contamination. A no-email account (empty `emails`) is a
    true orphan — nothing can resolve it.

    Raises on an indeterminate UID lookup (non-404 error from `GET /users/<uid>`) so a transient
    failure NEVER confirms a real user as an orphan — the caller skips it rather than deleting.
    """
    prof = _has_profile(uid)
    if prof is None:
        raise RuntimeError(f"profile lookup for {uid} returned a non-404 error — cannot confirm")
    if prof:
        return False
    return not any(_email_resolves(e) for e in emails)


def cmd_confirm(args):
    if not (os.environ.get("AURA_API_TOKEN") or os.environ.get("AURA_USER_TOKEN")):
        sys.exit("confirm needs AURA_API_TOKEN (or AURA_USER_TOKEN) for the read-only email "
                 "resolution — add it under env in ~/.claude/settings.json.")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    min_age = args.min_age_days * 86400e3
    min_inact = args.min_inactive_days * 86400e3
    candidates = [l.strip() for l in open(args.infile) if l.strip()]
    records = _lookup(candidates)
    confirmed, skipped, gone, not_orphan, errored = [], 0, 0, 0, 0
    for uid in candidates:
        u = records.get(uid)
        if not u:
            gone += 1  # already deleted / no longer exists
            continue
        if u.get("disabled"):
            skipped += 1
            continue
        if not _emails(u):
            skipped += 1  # no email -> out of scope (anonymous / phone-only)
            continue
        created = _to_ms(u.get("createdAt"))
        last = max(_to_ms(u.get("lastLoginAt")), _to_ms(u.get("lastRefreshAt")))
        if created and now_ms - created < min_age:
            skipped += 1  # too new (signup-race)
            continue
        if last and now_ms - last < min_inact:
            skipped += 1  # active too recently
            continue
        try:
            if _is_orphan_readonly(uid, _emails(u)):
                confirmed.append(uid)
            else:
                not_orphan += 1
        except urllib.error.HTTPError as e:
            errored += 1  # transient API error -> skip, never confirm on uncertainty
            print(f"  ! {uid[:12]}… resolve error {e.code}: {e.read().decode()[:120]} (skipped)", flush=True)
        except RuntimeError as e:
            errored += 1  # indeterminate uid lookup -> skip, never confirm
            print(f"  ! {uid[:12]}… {e} (skipped)", flush=True)
    with open(args.out, "w") as f:
        f.write("\n".join(confirmed) + "\n")
    print(f"confirm: candidates={len(candidates)} confirmed-orphan={len(confirmed)} "
          f"skipped(gates/disabled)={skipped} not-orphan={not_orphan} already-gone={gone} "
          f"errored(skipped)={errored}")
    print(f"  -> {args.out}")
    sample = confirmed[:10]
    if sample:
        srec = _lookup(sample)
        print("  sample (masked):")
        for uid in sample:
            u = srec.get(uid, {})
            em = mask(_emails(u)[0]) if _emails(u) else "-"
            provs = ",".join(p.get("providerId", "?") for p in (u.get("providerUserInfo") or []))
            print(f"    {uid}  {provs}  {em}")


# --------------------------------------------------------------------------- confirm-deleted
def _profile(uid):
    """GET /users/<uid> (role admin/service): the profile row, deletedAt included. None on 404.

    UserService answers 404 for a missing row and also when its read fails, so None only ever
    drops a uid. Any other failure raises: the caller skips the uid, never confirms it.
    """
    req = urllib.request.Request(f"{USERS_URL}/{urllib.parse.quote(uid)}")
    req.add_header("User-Agent", user_agent())
    req.add_header("Authorization", "Bearer " + _aura_token())
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


DELETED_REASONS = ("confirmed", "auth_gone", "auth_disabled", "profile_gone", "restored",
                   "in_grace", "errored")


def cmd_confirm_deleted(args):
    """Gate for reconcile's deleted_with_live_auth list. Read-only: one accounts:lookup per 100
    uids and one GET /users/<uid> each. Keeps a uid only when every condition holds now."""
    if not (os.environ.get("AURA_API_TOKEN") or os.environ.get("AURA_USER_TOKEN")):
        sys.exit("confirm-deleted needs AURA_API_TOKEN (or AURA_USER_TOKEN) for the read-only "
                 "profile lookup — add it under env in ~/.claude/settings.json.")
    now = datetime.now(timezone.utc)
    grace = timedelta(days=args.grace_days)
    with open(args.infile) as f:
        uids = list(dict.fromkeys(l.strip() for l in f if l.strip()))
    counts = {k: 0 for k in DELETED_REASONS}
    confirmed = []
    for i in range(0, len(uids), 100):
        chunk = uids[i:i + 100]
        try:
            records = _lookup(chunk)
        except Exception as e:  # noqa: BLE001 — any lookup failure skips the whole chunk
            counts["errored"] += len(chunk)
            print(f"  ! auth lookup failed for {len(chunk)} uid(s): {type(e).__name__} (skipped)", flush=True)
            continue
        for uid in chunk:
            u = records.get(uid)
            if not u:
                counts["auth_gone"] += 1
                continue
            if u.get("disabled"):
                counts["auth_disabled"] += 1
                continue
            try:
                prof = _profile(uid)
                if prof is None:
                    counts["profile_gone"] += 1
                    continue
                deleted = prof.get("deletedAt")
                if not deleted:
                    counts["restored"] += 1
                    continue
                at = datetime.fromisoformat(str(deleted).replace("Z", "+00:00"))
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
            except Exception as e:  # noqa: BLE001 — uncertainty never confirms
                counts["errored"] += 1
                print(f"  ! {uid[:12]}… profile check failed: {type(e).__name__} (skipped)", flush=True)
                continue
            if now - at < grace:
                counts["in_grace"] += 1
                continue
            counts["confirmed"] += 1
            confirmed.append(uid)
    with open(args.out, "w") as f:
        f.write("\n".join(confirmed) + ("\n" if confirmed else ""))
    print(f"confirm-deleted: candidates={len(uids)} " + " ".join(f"{k}={counts[k]}" for k in DELETED_REASONS))
    print(f"  -> {args.out}")
    return counts


# --------------------------------------------------------------------------- confirm-no-email
def _rtdb_exists(uids):
    """{uid: True/False}: does /users/{uid} exist in RTDB. Exact paths through rtdb_get.js (Admin SDK
    realtime protocol, never REST, never /users itself), existence only, no values. Raises on any
    failed or missing row, so an unread path never reads as gone."""
    import subprocess
    env = {**os.environ, "AINATIVE_USER_AGENT": user_agent()}
    env.setdefault("RTDB_URL", RTDB_URL)
    if not env.get("GCP_KEY") and env.get("GOOGLE_APPLICATION_CREDENTIALS"):
        env["GCP_KEY"] = env["GOOGLE_APPLICATION_CREDENTIALS"]
    script = pathlib.Path(__file__).resolve().parent / "rtdb_get.js"
    out = {}
    for i in range(0, len(uids), RTDB_CHUNK):
        paths = {f"/users/{u}": u for u in uids[i:i + RTDB_CHUNK]}
        try:
            proc = subprocess.run(["node", str(script)], input="\n".join(paths), capture_output=True,
                                  text=True, encoding="utf-8", env=env, timeout=600)
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(f"rtdb_get.js failed to run: {type(e).__name__}") from e
        rows = {}
        for line in proc.stdout.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("path") in paths:
                rows[row["path"]] = row
        bad = [p for p in paths if "exists" not in rows.get(p, {})]
        if bad:
            raise RuntimeError(f"RTDB read failed for {len(bad)} of {len(paths)} path(s); needs node, "
                               "GCP_KEY and AURA_CLI_DIR (firebase-admin)")
        out.update({u: bool(rows[p]["exists"]) for p, u in paths.items()})
    return out


PROVIDERS = {"facebook.com": "Facebook", "apple.com": "Apple", "google.com": "Google",
             "password": "password", "phone": "phone"}
NO_EMAIL_EXCLUDED = ("held", "auth_gone", "disabled", "has_email", "recent_activity", "live_profile",
                     "rtdb_profile", "claim_live", "errored")


def _provider(u):
    ids = sorted({p.get("providerId") for p in (u.get("providerUserInfo") or []) if p.get("providerId")})
    if not ids:
        return "none"
    return " + ".join(PROVIDERS.get(i, "other") for i in ids)


def _inactive_bucket(days):
    years = int(days // 365)
    return "<1y" if years < 1 else f"{years}-{years + 1}y" if years < 5 else "5y+"


def _write_private(path, lines):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def cmd_confirm_no_email(args):
    """Gate for profileless Auth accounts with no email anywhere. Read-only. A uid is confirmed only
    when all hold now: not on the keep list; the Auth account exists, isn't disabled and has no email
    on it or any provider; its last sign-in, refresh or creation is older than --min-inactive-days;
    GET /users/<uid> is 404; RTDB /users/{uid} doesn't exist; and any userId claim reaches no live
    profile. Any lookup failure drops the uid as `errored`. Prints counts only, never a uid or email.
    """
    if not (os.environ.get("AURA_API_TOKEN") or os.environ.get("AURA_USER_TOKEN")):
        sys.exit("confirm-no-email needs AURA_API_TOKEN (or AURA_USER_TOKEN) for the read-only "
                 "profile lookups — add it under env in ~/.claude/settings.json.")
    from collections import Counter
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    min_inact = args.min_inactive_days * 86400e3
    with open(args.infile) as f:
        uids = list(dict.fromkeys(l.strip() for l in f if l.strip()))
    uids, kept = held_uids.split(uids, load_held())
    excluded = Counter({k: 0 for k in NO_EMAIL_EXCLUDED})
    excluded["held"] = len(kept)
    survivors = {}
    for i in range(0, len(uids), 100):
        chunk = uids[i:i + 100]
        try:
            records = _lookup(chunk)
        except Exception as e:  # noqa: BLE001 — a failed lookup skips the chunk, never confirms it
            excluded["errored"] += len(chunk)
            print(f"  ! auth lookup failed for {len(chunk)} uid(s): {type(e).__name__} (skipped)", flush=True)
            continue
        for uid in chunk:
            u = records.get(uid)
            if not u:
                excluded["auth_gone"] += 1
                continue
            if u.get("disabled"):
                excluded["disabled"] += 1
                continue
            if _emails(u):
                excluded["has_email"] += 1
                continue
            last = max(_to_ms(u.get("lastLoginAt")), _to_ms(u.get("lastRefreshAt")), _to_ms(u.get("createdAt")))
            if not last or now_ms - last < min_inact:
                excluded["recent_activity"] += 1  # no timestamp at all counts as unknown, so kept
                continue
            try:
                prof = _has_profile(uid)
                if prof is None:
                    raise RuntimeError("profile lookup error")
                if prof:
                    excluded["live_profile"] += 1
                    continue
                claim = _claimed_user_id(u)
                if claim and claim != uid:
                    time.sleep(args.pause)
                    p = _profile(claim)
                    if p is not None and not p.get("deletedAt"):
                        excluded["claim_live"] += 1
                        continue
            except Exception as e:  # noqa: BLE001 — uncertainty never confirms
                excluded["errored"] += 1
                print(f"  ! {held_uids.label(uid)} profile check failed: {type(e).__name__} (skipped)", flush=True)
                continue
            finally:
                time.sleep(args.pause)
            survivors[uid] = (u, now_ms - last)
    confirmed = []
    pending = list(survivors)
    for i in range(0, len(pending), RTDB_CHUNK):
        chunk = pending[i:i + RTDB_CHUNK]
        try:
            exists = _rtdb_exists(chunk)
        except Exception as e:  # noqa: BLE001
            excluded["errored"] += len(chunk)
            print(f"  ! RTDB check failed for {len(chunk)} uid(s): {e} (skipped)", flush=True)
            continue
        for uid in chunk:
            if exists[uid]:
                excluded["rtdb_profile"] += 1
            else:
                confirmed.append(uid)
    _write_private(args.out, confirmed)

    by_provider, by_year, by_inactive = Counter(), Counter(), Counter()
    for uid in confirmed:
        u, idle_ms = survivors[uid]
        by_provider[_provider(u)] += 1
        cms = _to_ms(u.get("createdAt"))
        by_year[datetime.fromtimestamp(cms / 1000, timezone.utc).year if cms else "unknown"] += 1
        by_inactive[_inactive_bucket(idle_ms / 86400e3)] += 1
    total = len(uids) + len(kept)
    print(f"confirm-no-email: candidates={total:,} confirmed={len(confirmed):,} "
          f"(inactive over {args.min_inactive_days} days)")
    print("  excluded: " + " ".join(f"{k}={excluded[k]:,}" for k in NO_EMAIL_EXCLUDED))
    for title, counter in (("by provider", by_provider), ("by created year", by_year),
                           ("by inactive age", by_inactive)):
        print(f"  {title}: " + (", ".join(f"{k} {v:,}" for k, v in sorted(counter.items(), key=lambda kv: str(kv[0])))
                                 or "none"))
    print(f"  -> {args.out} (mode 600; uids only in the file, never printed)")
    return {"confirmed": len(confirmed), **excluded, "by_provider": dict(by_provider),
            "by_year": dict(by_year), "by_inactive": dict(by_inactive)}


# --------------------------------------------------------------------------- delete
def cmd_delete(args):
    uids = [l.strip() for l in open(args.infile) if l.strip()]
    uids, kept = held_uids.split(uids, load_held())
    if not args.counts_only:
        for u in kept:
            print(held_uids.skip_line(u))
    print(f"kept: {len(kept):,}")
    done = set()
    if os.path.exists(args.done):
        done = {l.strip() for l in open(args.done) if l.strip()}
    todo = [u for u in uids if u not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"delete: {len(todo):,} to process (already done {len(done):,}) batch {args.batch}")
    if not todo:
        print("nothing to do")
        return

    # Dry run is the default — deletion is irreversible, so require an explicit --execute.
    if not args.execute:
        batches = -(-len(todo) // args.batch)
        print(f"DRY RUN — would delete {len(todo):,} orphan auth credential(s) in {batches:,} batch(es) "
              f"of {args.batch}, {args.pause:g}s apart. Re-run with --execute to actually delete.")
        if args.counts_only:
            return
        preview = todo[:10]
        recs = _lookup(preview)
        print("  sample (masked):")
        for uid in preview:
            u = recs.get(uid, {})
            em = mask(_emails(u)[0]) if _emails(u) else "-"
            print(f"    {uid}  {em}")
        return

    # Back up full records first (Firebase deletion is irreversible). Merge with any
    # existing backup so a rerun or a larger --in list never deletes a UID that wasn't
    # backed up — every UID in the input set must be on file before we delete anything.
    backed_up = set()
    if os.path.exists(args.backup):
        for line in open(args.backup):
            line = line.strip()
            if not line:
                continue
            try:
                backed_up.add(json.loads(line)["localId"])
            except (ValueError, KeyError):
                pass
    missing = [u for u in uids if u not in backed_up]
    if missing:
        recs = _lookup(missing)
        fd = os.open(args.backup, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.chmod(args.backup, 0o600)  # full Auth records: owner-only, an older file included
        with os.fdopen(fd, "a") as f:
            for u in recs.values():
                f.write(json.dumps(u) + "\n")
        print(f"  backed up {len(recs):,} new records "
              f"({len(backed_up):,} already on file) -> {args.backup}")
    else:
        print(f"  backup already covers all {len(uids):,} UIDs -> {args.backup}")

    if os.path.exists(args.backup):
        os.chmod(args.backup, 0o600)  # tighten a backup left by an earlier run too
    donef = os.fdopen(os.open(args.done, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a")
    os.chmod(args.done, 0o600)
    ok = err = 0
    t0 = time.time()
    for i in range(0, len(todo), args.batch):
        chunk = todo[i:i + args.batch]
        for attempt in range(8):
            try:
                resp = _post(f"{IDTK}/accounts:batchDelete",
                             {"localIds": chunk, "force": True}, bearer=_creds().token)
                erridx = {e["index"] for e in (resp.get("errors") or [])}
                for j, u in enumerate(chunk):
                    if j not in erridx:
                        donef.write(u + "\n")
                donef.flush()
                ok += len(chunk) - len(erridx)
                err += len(erridx)
                break
            except urllib.error.HTTPError as e:
                txt = e.read().decode()[:200]
                transient = e.code in (429, 500, 503) or (e.code in (400, 403) and "INSUFFICIENT_PERMISSION" in txt)
                if transient:
                    w = min(90, 15 * (attempt + 1))
                    print(f"  {e.code} transient ({txt[:60]}…) retry in {w}s", flush=True)
                    time.sleep(w)
                else:
                    print(f"  FATAL HTTP {e.code}: {txt}", flush=True)
                    sys.exit(1)
        else:
            print("  gave up on a batch after retries — check the firebaseauth.admin grant", flush=True)
            sys.exit(1)
        if (i // args.batch) % 10 == 0:
            print(f"  …{ok:,} deleted, {err} errs, {int(time.time() - t0)}s", flush=True)
        time.sleep(args.pause)  # each deleted Auth fires the auth onDelete Functions
    print(f"DONE: {ok:,} deleted, {err} errors, {int(time.time() - t0)}s")

    # verify a spread is gone
    sample = todo[:5] + todo[len(todo) // 2: len(todo) // 2 + 5] + todo[-5:]
    present = _lookup(sample)
    print(f"verify: sampled {len(set(sample))} deleted UIDs -> still present {len(present)} (expect 0)")


# --------------------------------------------------------------------------- reconcile (full audit)
def _claimed_user_id(u):
    """The `userId` custom claim on an export line, or None. The export carries claims as
    `customClaims` (an object, or null); `customAttributes` (a JSON string, the Identity
    Toolkit shape) is the fallback."""
    claims = u.get("customClaims")
    if not isinstance(claims, dict):
        raw = u.get("customAttributes")
        try:
            claims = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
        except ValueError:
            claims = {}
    if not isinstance(claims, dict):
        return None
    v = claims.get("userId")
    return str(v) if v else None


def cmd_reconcile(args):
    """Full exhaustive audit: a complete Firebase auth export (NDJSON) reconciled against
    Postgres dumps (user / emailUser). Classifies every account into buckets, prints the
    report, and writes orphan candidate lists for `confirm`/`delete`. Needs a SQL dump
    (unlike `harvest`) — use for periodic deep audits, not routine cleanup."""
    csv.field_size_limit(10_000_000)

    # profiles: uid -> deleted? ; plus active/deleted email indexes
    profile_deleted, email_active, email_deleted = {}, {}, {}
    with open(args.user_csv, newline="") as f:
        for row in csv.DictReader(f):
            uid = row["id"].strip()
            deleted = bool((row.get("deletedAt") or "").strip())
            profile_deleted[uid] = deleted
            em = (row.get("email") or "").strip().lower()
            if em:
                # A set per email, not the first uid: two active profiles sharing an email is
                # exactly the ambiguity the claim backfill must not resolve by picking one.
                (email_deleted if deleted else email_active).setdefault(em, set()).add(uid)

    emailuser = {}  # email -> {userId} (every mapping kept, so duplicates are visible)
    try:
        with open(args.emailuser_csv, newline="") as f:
            for row in csv.DictReader(f):
                em = (row.get("email") or "").strip().lower()
                target = (row.get("userId") or "").strip()
                if em and target:
                    emailuser.setdefault(em, set()).add(target)
    except FileNotFoundError:
        print(f"  (no emailUser csv at {args.emailuser_csv} — skipping that mapping)")

    from collections import Counter
    counts = Counter()
    auth_uids = set()
    profileless, deleted_match, claim_candidates, claim_ambiguous = [], [], [], []
    deleted_live, no_email = [], []
    no_email_claimed = 0
    claim_new_candidates = []
    claim_stamped = claim_stale = unresolvable = 0
    relay = 0
    with open(args.export) as f:
        for line in f:
            u = json.loads(line)
            # Export NDJSON uses `uid`; Identity Toolkit accounts:batchGet uses `localId`.
            uid = u.get("uid") or u.get("localId")
            if not uid:
                continue
            auth_uids.add(uid)
            emails = _emails(u)  # handles providerEmails / providerUserInfo / providerData shapes
            d = profile_deleted.get(uid)
            if d is not None:
                counts["deleted_with_live_auth" if d else "healthy"] += 1
                if d:
                    deleted_live.append(uid)
                continue
            if not emails:
                counts["anonymous_or_no_email"] += 1
                # No own profile and no email: a confirm-no-email candidate, unless its userId claim
                # reaches a live profile in the dump (confirm-no-email re-checks every one live).
                if profile_deleted.get(_claimed_user_id(u)) is False:
                    no_email_claimed += 1
                else:
                    no_email.append(uid)
                continue
            # Every ACTIVE profile any of this account's emails points at, through either
            # index. An emailUser entry only counts if it maps to an ACTIVE profile; a
            # stale/deleted mapping must NOT suppress cleanup (verifyToken would return no id).
            direct = set().union(*(email_active.get(e, ()) for e in emails))
            mapped = {t for e in emails for t in emailuser.get(e, ()) if profile_deleted.get(t) is False}
            resolved = direct | mapped
            if resolved:
                # Bucketing is unchanged by ambiguity; only which list the uid lands in is.
                if direct:
                    counts["sso_resolvable"] += 1
                else:
                    counts["orphan"] += 1
                    counts["orphan_emailuser_mapped"] += 1
                if len(resolved) > 1:
                    # Emails resolving to different profiles: stamping any one of them would
                    # silently pick an identity, so these go to a human, not the backfill.
                    claim_ambiguous.append(uid)
                    continue
                profile = next(iter(resolved))
                claim_candidates.append(uid)
                current = _claimed_user_id(u)
                if current == profile:
                    claim_stamped += 1
                    continue
                if current:
                    claim_stale += 1
                claim_new_candidates.append(uid)
                continue
            unresolvable += 1
            counts["orphan"] += 1
            if any(e in email_deleted for e in emails):
                counts["orphan_deleted_profile_match"] += 1
                deleted_match.append(uid)
            else:
                counts["orphan_truly_profileless"] += 1
                profileless.append(uid)
                if any(e.endswith("privaterelay.appleid.com") for e in emails):
                    relay += 1

    locked_out = sum(1 for uid, dele in profile_deleted.items() if not dele and uid not in auth_uids)
    total_auth = sum(counts[k] for k in
                     ("healthy", "anonymous_or_no_email", "sso_resolvable", "orphan", "deleted_with_live_auth"))

    os.makedirs(args.out_dir, exist_ok=True)
    pl_path = os.path.join(args.out_dir, "orphan_reconcile_profileless.txt")
    dp_path = os.path.join(args.out_dir, "orphan_reconcile_deleted_profile.txt")
    cc_path = os.path.join(args.out_dir, "reconcile_claim_candidates.txt")
    ca_path = os.path.join(args.out_dir, "reconcile_claim_ambiguous.txt")
    cn_path = os.path.join(args.out_dir, "reconcile_claim_new_candidates.txt")
    dl_path = os.path.join(args.out_dir, "reconcile_deleted_with_live_auth.txt")
    ne_path = os.path.join(args.out_dir, "orphan_reconcile_no_email.txt")
    with open(pl_path, "w") as f:
        f.write("\n".join(profileless) + "\n")
    with open(dp_path, "w") as f:
        f.write("\n".join(deleted_match) + "\n")
    # Secondary logins of a live profile (sso-resolvable + emailUser-mapped): never delete
    # candidates, but the input of the FirebaseAuth userId-claim backfill, which re-resolves each
    for path, uids in ((cc_path, claim_candidates), (ca_path, claim_ambiguous),
                       (cn_path, claim_new_candidates), (dl_path, deleted_live), (ne_path, no_email)):
        with open(path, "w") as f:
            f.write("\n".join(uids) + ("\n" if uids else ""))

    print(f"\n# AUTH {total_auth:,} | PROFILES {len(profile_deleted):,} (deleted {sum(profile_deleted.values()):,})")
    for k in ("healthy", "sso_resolvable", "orphan", "anonymous_or_no_email", "deleted_with_live_auth"):
        print(f"  {k:24}: {counts[k]:>10,}")
    print(f"\nORPHAN breakdown ({counts['orphan']:,}):")
    print(f"  truly profile-less     : {counts['orphan_truly_profileless']:>9,}  (apple-relay {relay:,})  -> {pl_path}")
    print(f"  matches deleted profile: {counts['orphan_deleted_profile_match']:>9,}  -> {dp_path}")
    print(f"  emailUser-mapped       : {counts['orphan_emailuser_mapped']:>9,}  (resolves to a userId — NOT a delete candidate)")
    print(f"\nsoft-deleted profile, live auth: {len(deleted_live):,}  -> {dl_path}")
    print(f"  gate: python3 orphan_cleanup.py confirm-deleted --in {dl_path} --out /tmp/deleted_confirmed.txt")
    print(f"\nno email, no own profile: {len(no_email):,}  -> {ne_path}  "
          f"(claim to a live profile, not listed: {no_email_claimed:,})")
    print(f"  gate: python3 orphan_cleanup.py confirm-no-email --in {ne_path} --out /tmp/no_email_confirmed.txt")
    print(f"\nactive profile, NO live auth (locked-out, NOT a delete candidate): {locked_out:,}")
    print(f"userId-claim candidates (sso-resolvable + emailUser-mapped): {len(claim_candidates):,}  -> {cc_path}")
    print("  input for FirebaseAuth `functions/scripts/backfillUserClaim.js` (stamps the userId claim)")
    no_own = counts["sso_resolvable"] + counts["orphan"]
    print("\nuserId claim coverage (accounts whose uid is not a profile id):")
    print(f"  no own profile, has email : {no_own:>9,}")
    print(f"  already stamped           : {claim_stamped:>9,}")
    print(f"  stale claim (other userId): {claim_stale:>9,}")
    print(f"  new candidates            : {len(claim_new_candidates):>9,}  -> {cn_path}")
    print(f"  ambiguous (>1 profile)    : {len(claim_ambiguous):>9,}  -> {ca_path}  (NOT stamped — review)")
    print(f"  unresolvable (no profile) : {unresolvable:>9,}")
    print("\nNext — confirm + delete a candidate list (dry-run by default):")
    print(f"  python3 orphan_cleanup.py confirm --in {pl_path} --out /tmp/orphan_confirmed.txt")
    print("  python3 orphan_cleanup.py delete  --in /tmp/orphan_confirmed.txt           # dry run")
    print("  python3 orphan_cleanup.py delete  --in /tmp/orphan_confirmed.txt --execute # real")


# --------------------------------------------------------------------------- main
def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Orphaned Firebase auth cleanup (dump-free).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("harvest", help="GCP logs -> candidate orphan UIDs")
    h.add_argument("--since", default="7d")
    h.add_argument("--container", default="user-service")
    h.add_argument("--out", default="/tmp/orphan_candidates.txt")

    c = sub.add_parser("confirm", help="lookup + gates + verifyToken -> confirmed orphans")
    c.add_argument("--in", dest="infile", required=True)
    c.add_argument("--min-age-days", type=int, default=2)
    c.add_argument("--min-inactive-days", type=int, default=7)
    c.add_argument("--out", default="/tmp/orphan_confirmed.txt")

    cd = sub.add_parser("confirm-deleted",
                        help="reconcile's deleted_with_live_auth list -> live re-check -> confirmed uids")
    cd.add_argument("--in", dest="infile", required=True)
    cd.add_argument("--grace-days", type=float, default=1,
                    help="skip a profile deleted more recently than this (an in-flight self-delete)")
    cd.add_argument("--out", default="/tmp/deleted_confirmed.txt")

    d = sub.add_parser("delete", help="batchDelete confirmed orphans (resumable)")
    d.add_argument("--in", dest="infile", required=True)
    d.add_argument("--batch", type=int, default=100,
                   help="uids per accounts:batchDelete (max 1000); each delete fires the auth onDelete Functions")
    d.add_argument("--pause", type=float, default=5.0, help="seconds between batches")
    d.add_argument("--counts-only", action="store_true",
                   help="print counts only: no uid prefix, no masked-email sample")
    d.add_argument("--limit", type=int, default=0)
    d.add_argument("--done", default="/tmp/orphan_deleted.log")
    d.add_argument("--backup", default="/tmp/orphan_deleted_backup.ndjson")
    d.add_argument("--execute", action="store_true",
                   help="actually delete; without this flag delete does a dry run (default)")

    ne = sub.add_parser("confirm-no-email",
                        help="profileless Auth with no email -> live re-check -> confirmed uids, counts only")
    ne.add_argument("--in", dest="infile", required=True)
    ne.add_argument("--min-inactive-days", type=int, default=365,
                    help="skip an account signed in, refreshed or created more recently than this")
    ne.add_argument("--pause", type=float, default=0.1, help="seconds between UserService GETs (~10/s)")
    ne.add_argument("--out", default="/tmp/no_email_confirmed.txt")

    r = sub.add_parser("reconcile", help="full audit: Firebase export + Postgres dumps -> buckets + candidate lists")
    r.add_argument("--export", default="/tmp/firebase_auth_export.ndjson",
                   help="full Firebase auth export, one JSON object per line")
    r.add_argument("--user-csv", default="/tmp/user.csv", help="Postgres user dump (id,email,deletedAt)")
    r.add_argument("--emailuser-csv", default="/tmp/emailuser.csv", help="Postgres emailUser dump (email,userId)")
    r.add_argument("--out-dir", default="/tmp", help="where to write the candidate lists")

    rc = sub.add_parser("recent", help="dump-free: orphans created in the last N hours (sorted query, no export)")
    rc.add_argument("--hours", type=int, required=True, help="lookback window in hours")
    rc.add_argument("--min-age-minutes", type=int, default=10,
                    help="exclude orphans younger than this (likely signup in-flight)")
    rc.add_argument("--max-scan", type=int, default=20000,
                    help="safety cap on accounts scanned from the top of CREATED_AT desc")
    rc.add_argument("--out", default="/tmp/orphan_candidates.txt")

    args = ap.parse_args()
    {"harvest": cmd_harvest, "confirm": cmd_confirm, "confirm-deleted": cmd_confirm_deleted,
     "confirm-no-email": cmd_confirm_no_email, "delete": cmd_delete,
     "reconcile": cmd_reconcile, "recent": cmd_recent}[args.cmd](args)


if __name__ == "__main__":
    main()
