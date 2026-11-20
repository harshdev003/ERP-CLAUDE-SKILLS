#!/usr/bin/env python3
"""Check one person against the ONE auth rules: one Auth per email, its id = the Profile id,
and the Profile email = the Auth email = every Sign-in's email.

Terms (approved, the ONE auth doc): Profile; Auth = a Firebase Auth account; Sign-in = Google, Apple,
Facebook or Password on an Auth; Main Auth = the Auth whose id is the Profile id; Extra Auth = any
other Auth reaching the Profile; Mapping = the claim (userId) link; Empty Auth = an Auth with no
Sign-in and no email. An Auth reaches a Profile by its own id or by the claim, nothing else.

Read-only, one person per run. Input is a Firebase uid or an email. Reads, exact paths only:
- Firebase Auth (Identity Toolkit, GCP_KEY): accounts:lookup by uid, accounts:query on the one email;
- RTDB: /users/{id}/email, /users/{id}/deletedAt, /users/{id}/role only; never /users/{id} whole, never a
  listing or shallow read. /emailUser is not read: that node is being deleted;
- userDB through UserService (AURA_API_TOKEN): GET /users/{id} for email, deletedAt and role only, and
  POST /users/email/lookup (the email in the body) for the live Profile holding an email.

Prints verdict codes with their rule ids and next step, uid prefixes (6 characters), providers and
trust, and masked emails. Never a full email, a full uid, a token or a hash. A person in the held set
prints only HELD.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import queue
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gcloud_sa  # noqa: E402

FIREBASE_PROJECT = "firebase-auratech16"
AURA_API = "https://services.aurahealth.io"
HELD = Path.home() / ".claude" / "held-accounts" / "held-uids.json"
PREFIX = 6
RELAY = "@privaterelay.appleid.com"
GMAIL = ("@gmail.com", "@googlemail.com")

# Rule ids and next steps from the ONE auth doc (ENGBACK-2048); P6–P8 from SecurityAuth's pattern list.
NEXT = {
    "OK": ("R1, R2", "Nothing to do."),
    "DUPLICATE_ACCOUNTS": ("R3, R5", "Merge the group: keep the R3 Profile's Main Auth; Sign-ins on its email stay or "
                           "move onto it, the others come off; emptied Auths are deleted after export."),
    "CLAIM_ONLY": ("R4, ENGBACK-2485", "Create the Main Auth at the Profile id, move the Sign-ins on the Profile email "
                   "onto it (a generated password if none), then delete the Extra Auth after export."),
    "PROFILE_EMAIL_EMPTY/copyable": ("R13", "Set the Profile email from the Main Auth email with the email-update "
                                     "job; sessions are kept."),
    "PROFILE_EMAIL_EMPTY/taken-live": ("R13, P6", "Stopped: another live Profile has the Auth email. Bring it "
                                       "to the SecurityAuth session."),
    "PROFILE_EMAIL_EMPTY/deleted-only": ("R13, P7", "Only a deleted Profile has the Auth email. Bring it to the "
                                         "SecurityAuth session."),
    "PROFILE_EMAIL_EMPTY/no-email": ("R13", "Nothing to set: no Auth email. Left untouched."),
    "PROFILE_EMAIL_EMPTY/unchecked": ("R13", "Not decided: AURA_API_TOKEN is unset, so no one read whether "
                                      "another live Profile holds the Auth email. Set it and run again."),
    "ONLY_SIGNIN_EMAIL_DIFFERS/free": ("R15 case 1, R7", "The Profile and Auth email become the Sign-in's email "
                                       "through the email-update job."),
    "ONLY_SIGNIN_EMAIL_DIFFERS/taken-live": ("R15 case 1, E1, P6", "OPEN (P6): another live Profile has this email. "
                                             "Bring it to the SecurityAuth session."),
    "ONLY_SIGNIN_EMAIL_DIFFERS/deleted-only": ("R15 case 1, P7", "Only a deleted profile has the sign-in's email. "
                                               "Bring it to the SecurityAuth session."),
    "ONLY_SIGNIN_EMAIL_DIFFERS/unchecked": ("R15 case 1", "Not decided: AURA_API_TOKEN is unset, so no one read "
                                            "whether another live Profile holds this email. Set it and run again."),
    "OTHER_EMAIL_SIGNIN": ("R15 case 2, R5, R8", "The sign-in on the other email comes off. Removing a password "
                           "here is UNCONFIRMED."),
    "NO_EMAIL_SIGNIN": ("E5", "Left alone."),
    "ANONYMOUS": ("R13 note", "No Sign-in: left alone."),
    "ORPHAN_ACCOUNT": ("R10", "Export, then delete (held uids are skipped)."),
    "PROFILE_DELETED": ("R10", "Accounts that reach only this profile are exported, then deleted."),
    "ACCOUNT_AT_PROFILE_ID": ("P1", "APPROVED (P1): the Main Auth at the Profile id is reused; Sign-ins on the "
                              "Profile email move onto it, then the Extra Auth is deleted after export."),
    "LEGACY_UID": ("P2", "OPEN: the repoint step rejects ':'. Bring it to the SecurityAuth session."),
    "UNTRUSTED": ("R6, ENGBACK-2455", "Stays as it is; its email is confirmed later."),
    "STAFF_OR_TEST": ("P8", "Staff or test Profile: check with the SecurityAuth session before any change."),
    "CASE_VARIANT": ("R1", "The profile email has uppercase letters or surrounding spaces. Bring it to the "
                     "SecurityAuth session."),
    "AUTH_EMAIL_DIFFERS": ("R1", "The ONE auth run sets the Auth email to the Profile email (sessions kept)."),
    "SIGNINS_NONE_MATCH": ("R1, OPEN", "OPEN: no rule covers 2+ Sign-ins with none on the Profile email. Bring it "
                           "to the SecurityAuth session."),
    "NOT_ALIGNED": ("R1", "Doesn't meet R1, and no listed verdict covers how. Bring it to the SecurityAuth "
                    "session."),
    "EMAIL_ON_2PLUS_PROFILES": ("R3, R12", "R3 picks the kept profile; the others stay untouched until "
                                "ENGBACK-2410 soft-deletes them."),
}
# Verdicts that say nothing is wrong with the shape of ONE auth.
INFO = {"OK", "UNTRUSTED", "STAFF_OR_TEST", "NO_EMAIL_SIGNIN", "ANONYMOUS"}


class ReadFailed(RuntimeError):
    pass


# --- masking -------------------------------------------------------------------------------------
def mask_email(e: str | None) -> str:
    """j***@g***.com"""
    if not e:
        return "(none)"
    local, _, domain = e.strip().partition("@")
    label, _, rest = domain.partition(".")
    return f"{local[:1]}***@{label[:1]}***" + (f".{rest.rsplit('.', 1)[-1]}" if rest else "")


def pre(uid: str | None) -> str:
    """The first 6 characters; a legacy `provider:digits` id keeps its provider so ids don't collapse."""
    head, sep, rest = (uid or "").partition(":")
    return (f"{head}:{rest[:PREFIX]}" if sep else head[:PREFIX]) or "(none)"


def norm(e: str | None) -> str:
    return (e or "").strip().lower()


# --- reads ---------------------------------------------------------------------------------------
class Reader:
    """Every read the skill makes. Tests replace it with an in-memory fake."""

    def __init__(self):
        self._auth_token = None
        self._db = None

    def _http(self, method, url, body=None, token=None):
        headers = {"User-Agent": gcloud_sa.user_agent(), "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"null")

    def _auth(self, path, body):
        if not self._auth_token:
            out = gcloud_sa.run(["auth", "print-access-token"], capture_output=True, text=True, encoding="utf-8",
                                errors="replace")
            if out.returncode:
                raise ReadFailed(f"Auth token as {gcloud_sa.credential()}: {(out.stderr or '').strip()[-200:]}")
            self._auth_token = out.stdout.strip().splitlines()[-1]
        try:
            return self._http("POST", f"https://identitytoolkit.googleapis.com/v1/projects/{FIREBASE_PROJECT}/"
                              f"{path}", body, self._auth_token) or {}
        except (urllib.error.URLError, ValueError) as e:
            raise ReadFailed(f"Identity Toolkit {path}: {type(e).__name__} {getattr(e, 'code', '')}")

    def account(self, uid: str) -> dict | None:
        return (self._auth("accounts:lookup", {"localId": [uid]}).get("users") or [None])[0]

    def accounts_by_email(self, email: str) -> list:
        """Every Auth whose top-level email is this one (one email, never a listing). An Auth holding it only as
        a Sign-in's provider email is assumed not to match: not verified on aura-dev yet."""
        return self._auth("accounts:query", {"expression": [{"email": email}], "returnUserInfo": True}).get(
            "userInfo") or []

    def rtdb(self, path: str):
        """One exact path through the Admin SDK realtime protocol (rtdb_realtime.js), never REST: a run
        over many people must not count toward the RTDB REST read-surge alert."""
        path = urllib.parse.unquote(path)
        if not any(re.fullmatch(rx, path) for rx in RTDB_PATHS):
            raise ReadFailed("RTDB path refused: only exact /users/{id}/email|deletedAt|role")
        if self._db is None:
            self._db = RealtimeReader()
        return self._db.get(path)

    def userdb(self, pid: str) -> tuple[dict | None, str]:
        token = os.environ.get("AURA_API_TOKEN")
        if not token:
            return None, "AURA_API_TOKEN unset"
        try:
            row = self._http("GET", f"{AURA_API}/users/{urllib.parse.quote(pid, safe='')}", token=token) or {}
            return {k: row.get(k) for k in ("email", "deletedAt", "role")}, ""
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, ""
            raise ReadFailed(f"UserService GET /users: HTTP {e.code}")
        except (urllib.error.URLError, ValueError) as e:
            raise ReadFailed(f"UserService GET /users: {type(e).__name__}")

    def profile_by_email(self, email: str) -> tuple[str | None, str]:
        """(id of the live Profile whose userDB email is this one, case-insensitive, or None; why unread)."""
        token = os.environ.get("AURA_API_TOKEN")
        if not token:
            return None, "AURA_API_TOKEN unset"
        try:
            row = self._http("POST", f"{AURA_API}/users/email/lookup", {"email": email}, token=token) or {}
            return row.get("id"), ""
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, ""
            raise ReadFailed(f"UserService POST /users/email/lookup: HTTP {e.code}")
        except (urllib.error.URLError, ValueError) as e:
            raise ReadFailed(f"UserService POST /users/email/lookup: {type(e).__name__}")


RTDB_PATHS = (r"/users/[^/.#$\[\]]+/(email|deletedAt|role)",)
HELPER = Path(__file__).resolve().parent / "rtdb_realtime.js"
RTDB_URL = "https://auratech16.firebaseio.com"


class RealtimeReader:
    """A child `node rtdb_realtime.js` holding one realtime connection. One request at a time."""

    def __init__(self, popen=None):
        env = dict(os.environ, AINATIVE_USER_AGENT=gcloud_sa.user_agent(), RTDB_URL=RTDB_URL)
        try:
            self.proc = (popen or subprocess.Popen)(["node", str(HELPER)], stdin=subprocess.PIPE,
                                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                    text=True, encoding="utf-8", env=env)
        except OSError as e:
            raise ReadFailed(f"RTDB realtime reader did not start (node): {type(e).__name__}")
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        first = self._line()
        if not first.get("ready"):
            raise ReadFailed(first.get("fatal") or "RTDB realtime reader did not start")

    TIMEOUT = 60  # seconds per answer, as the REST read had; the helper gives up on a read at 30

    def _pump(self) -> None:
        """Reads the helper's lines on a thread, so every wait has a deadline on every platform."""
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put("")

    def _line(self) -> dict:
        try:
            line = self._lines.get(timeout=self.TIMEOUT)
        except queue.Empty:
            self.close()
            raise ReadFailed(f"RTDB realtime reader gave no answer in {self.TIMEOUT}s")
        if not line:
            raise ReadFailed("RTDB realtime reader stopped")
        try:
            return json.loads(line)
        except ValueError:
            raise ReadFailed("RTDB realtime reader sent an unreadable line")

    def close(self) -> None:
        kill = getattr(self.proc, "kill", None)
        if kill:
            try:
                kill()
            except OSError:
                pass

    def get(self, path: str):
        try:
            self.proc.stdin.write(json.dumps({"path": path}) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            raise ReadFailed("RTDB realtime reader stopped")
        got = self._line()
        if got.get("path") != path or "error" in got:
            raise ReadFailed(f"RTDB read: {got.get('error') or 'answer for another path'}")
        return got.get("value")


def held_set(path: Path = HELD) -> set:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return set()


# --- the person ----------------------------------------------------------------------------------
def claim_of(acct: dict) -> str | None:
    try:
        return (json.loads(acct.get("customAttributes") or "{}") or {}).get("userId")
    except ValueError:
        return None


def signins(acct: dict) -> list[dict]:
    """[{provider, email}]; a sign-in with no provider email falls back to the account email (FirebaseAuth#89)."""
    return [{"provider": p.get("providerId", "?"), "email": p.get("email") or acct.get("email") or ""}
            for p in acct.get("providerUserInfo") or []]


def is_empty(acct: dict) -> bool:
    """An Empty Auth: no Sign-in and no email."""
    return not acct.get("providerUserInfo") and not acct.get("email")


def trusted(provider: str, email: str, acct: dict) -> bool:
    if provider == "password":
        return bool(acct.get("emailVerified"))
    if provider == "facebook.com":
        return False
    if provider == "google.com":
        return norm(email).endswith(GMAIL)
    return True


def subtype(provider: str, email: str) -> str:
    e = norm(email)
    if provider == "apple.com":
        return "apple-relay" if e.endswith(RELAY) else "apple-real"
    if provider == "google.com":
        return "google-gmail" if e.endswith(GMAIL) else "google-other"
    return {"facebook.com": "facebook", "password": "password"}.get(provider, provider)


def load_profile(r: Reader, pid: str) -> dict:
    row, why = r.userdb(pid)
    rt = {k: r.rtdb(f"/users/{urllib.parse.quote(pid, safe='')}/{k}") for k in ("email", "deletedAt", "role")}
    src = row if row is not None else rt
    exists = row is not None or any(v is not None for v in rt.values())
    # The userDB value wins when the row exists, even when it is empty: RTDB's copy never fills it in.
    email = (row.get("email") if row is not None else rt.get("email")) or ""
    return {"id": pid, "exists": exists, "email": email,
            "deleted": bool(src.get("deletedAt") or rt.get("deletedAt")), "role": src.get("role") or rt.get("role"),
            "userdb": "ok" if row is not None else (why or "no row"),
            "emails_differ": row is not None and rt.get("email") is not None and row.get("email") != rt.get("email")}


def email_state(r: Reader, email: str, pid: str, cache: dict) -> str:
    """Who else holds the email: `taken-live` (another live profile), `deleted-only`, `free`, or `unchecked`
    when the userDB lookup could not run."""
    owners = set()
    for a in r.accounts_by_email(email):
        owners |= {a.get("localId"), claim_of(a)}
    holder, why = r.profile_by_email(email)
    if holder and holder != pid:
        return "taken-live"
    states = []
    for o in sorted(x for x in owners if x and x != pid):
        p = cache.get(o) or cache.setdefault(o, load_profile(r, o))
        if p["exists"]:
            states.append("deleted" if p["deleted"] else "live")
    if "live" in states:
        return "taken-live"
    # Without the lookup no one knows whether a live Profile holds it: never deleted-only or free.
    return "unchecked" if why else "deleted-only" if states else "free"


def check(r: Reader, uid: str | None = None, email: str | None = None, held: set | None = None) -> dict:
    held = held or set()
    if uid and uid in held:  # before any read of the person
        return {"held": True}
    group: dict[str, dict] = {}     # the accounts that carry the email
    outside: dict[str, dict] = {}   # the --uid account when it doesn't carry the email (P1)
    first = r.account(uid) if uid else None
    if first:
        email = email or first.get("email") or next((s["email"] for s in signins(first) if s["email"]), None)
    if uid and not email:  # no account email to start from: start from the profile's email
        row, _ = r.userdb(uid)
        email = (row or {}).get("email") or r.rtdb(f"/users/{urllib.parse.quote(uid, safe='')}/email") or None
    if email:
        for a in r.accounts_by_email(email):
            group[a["localId"]] = a
    if first and first["localId"] not in group:
        carries = norm(email) in {norm(first.get("email"))} | {norm(s["email"]) for s in signins(first)}
        (group if carries or not email else outside)[first["localId"]] = first
    accounts = {**group, **outside}
    # The live Profile whose userDB email is this one, when no Auth reaches it.
    holder = r.profile_by_email(email)[0] if email else None
    pids = set(accounts) | {c for c in map(claim_of, accounts.values()) if c} | ({holder} if holder else set()) \
        | ({uid} if uid else set())
    if held & (pids | set(accounts)):
        return {"held": True}

    profiles = {p: load_profile(r, p) for p in sorted(pids)}
    live = {p: v for p, v in profiles.items() if v["exists"] and not v["deleted"]}
    reach = {p: [a for a in accounts.values() if a["localId"] == p or claim_of(a) == p] for p in profiles}
    verdicts: list[dict] = []

    def add(code, detail="", key=None):
        rule, step = NEXT[key or code]
        verdicts.append({"code": code, "detail": detail, "rule": rule, "next": step})

    # R3: which live profile would be kept (paying is not read here).
    def r3_key(p):
        accts = reach[p]
        last = max((int(a.get("lastLoginAt") or 0) for a in accts), default=0)
        oldest = min((int(a.get("createdAt") or 0) for a in accts), default=0)
        return (-last, -len(accts), oldest or float("inf"), p)
    kept = min(live, key=r3_key) if live else None

    if len(group) >= 2:
        add("DUPLICATE_ACCOUNTS", f"{len(group)} Auths; R3 would keep Profile {pre(kept)} "
                                  "(paying not checked: no payment read)")
    for a in accounts.values():
        c, u = claim_of(a), a["localId"]
        if c and c != u and c in live and u not in live:
            add("CLAIM_ONLY", f"Extra Auth {pre(u)} reaches Profile {pre(c)} only by the claim Mapping")
        if not a.get("providerUserInfo"):
            add("ANONYMOUS", f"{'Empty Auth' if is_empty(a) else 'Auth with no Sign-in'} {pre(u)}")
        if not any(p in live for p in {u, c}):
            add("ORPHAN_ACCOUNT", f"Auth {pre(u)}")
        for s in signins(a):
            if not s["email"]:
                add("NO_EMAIL_SIGNIN", f"{s['provider']} on {pre(u)}")
            if not trusted(s["provider"], s["email"], a):
                add("UNTRUSTED", f"{s['provider']} on {pre(u)}")
    for p, prof in profiles.items():
        if prof["exists"] and prof["deleted"]:
            add("PROFILE_DELETED", f"Profile {pre(p)}")
        if ":" in p:
            add("LEGACY_UID", f"Profile {pre(p)}")
    cache = dict(profiles)
    for p, prof in live.items():
        if p not in group and (p in outside or r.account(p)):
            add("ACCOUNT_AT_PROFILE_ID", f"a Main Auth sits at Profile {pre(p)} outside this email's group")
        if prof["role"] or norm(prof["email"]).endswith("@aurahealth.io") or norm(prof["email"]).startswith("test+"):
            add("STAFF_OR_TEST", f"Profile {pre(p)}")
        pe = prof["email"]
        if pe and pe != pe.strip().lower():
            add("CASE_VARIANT", f"Profile {pre(p)}")
        own = [a for a in reach[p]]
        if not pe:
            src = next((a.get("email") for a in own if a.get("email")), None)
            state = email_state(r, src, p, cache) if src else "no-email"
            state = "copyable" if state == "free" else state
            add("PROFILE_EMAIL_EMPTY", f"Profile {pre(p)}: {state}", f"PROFILE_EMAIL_EMPTY/{state}")
            continue
        ins = [s for a in own for s in signins(a) if s["email"]]
        if len(ins) == 1 and norm(ins[0]["email"]) != norm(pe):
            s = ins[0]
            state = email_state(r, s["email"], p, cache)
            add("ONLY_SIGNIN_EMAIL_DIFFERS", f"Profile {pre(p)}: {subtype(s['provider'], s['email'])} {state}",
                f"ONLY_SIGNIN_EMAIL_DIFFERS/{state}")
        elif any(norm(s["email"]) == norm(pe) for s in ins):
            off = [s["provider"] for s in ins if norm(s["email"]) != norm(pe)]
            if off:
                add("OTHER_EMAIL_SIGNIN", f"Profile {pre(p)}: {', '.join(off)} on another email"
                    + (" (password: UNCONFIRMED)" if "password" in off else ""))
            else:  # every Sign-in matches; the Auth's own email may not
                for a in own:
                    if norm(a.get("email")) != norm(pe):
                        add("AUTH_EMAIL_DIFFERS", f"Auth {pre(a['localId'])} email {mask_email(a.get('email'))}, "
                                                  f"Profile {pre(p)} email {mask_email(pe)}")
        elif len(ins) >= 2:
            add("SIGNINS_NONE_MATCH", f"Profile {pre(p)}: {len(ins)} Sign-ins, none on the Profile email")
    if email:
        same = [p for p, v in live.items() if norm(v["email"]) == norm(email)]
        if len(same) >= 2:
            add("EMAIL_ON_2PLUS_PROFILES", f"{len(same)} live profiles")

    problems = [v for v in verdicts if v["code"] not in INFO]
    aligned = (not outside and len(accounts) == 1 and len(live) == 1 and next(iter(accounts)) in live
               and all(norm(s["email"]) == norm(live[next(iter(live))]["email"])
                       for s in signins(next(iter(accounts.values()))) if s["email"])
               and norm(next(iter(accounts.values())).get("email")) == norm(live[next(iter(live))]["email"]))
    # Only a missing token leaves the row unread: a 404 is a read that found no row, shown on the Profile line.
    unread = [p for p, v in live.items() if v["userdb"] == "AURA_API_TOKEN unset"]
    if not problems and not aligned and accounts and live:
        # Never an empty verdict list for someone who isn't aligned.
        why = []
        if len(accounts) != 1:
            why.append(f"{len(accounts)} Auths")
        if len(live) != 1:
            why.append(f"{len(live)} live Profiles reached")
        if len(accounts) == 1 and next(iter(accounts)) not in live:
            why.append("the Auth isn't at a live Profile id")
        add("NOT_ALIGNED", ", ".join(why) or "emails differ in a way no other verdict covers")
        problems = [verdicts[-1]]
    if not problems and aligned and not unread:
        add("OK")
    elif not problems and aligned:
        verdicts.append({"code": "NOT_CHECKED", "detail": "userDB not read (" + profiles[unread[0]]["userdb"] + ")",
                         "rule": "R1, R2", "next": "Set AURA_API_TOKEN and run again before calling it OK."})
    seen, out = set(), []
    for v in verdicts:  # one line per code and detail
        if (v["code"], v["detail"]) not in seen:
            seen.add((v["code"], v["detail"]))
            out.append(v)
    return {"held": False, "email": email, "accounts": list(accounts.values()), "outside": sorted(outside),
            "profiles": profiles,
            "holder": holder, "verdicts": out}


# --- output --------------------------------------------------------------------------------------
def date(ms) -> str:
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "never"


def render(res: dict) -> str:
    if res["held"]:
        return "HELD"
    out = [f"email {mask_email(res['email'])}", f"Auths ({len(res['accounts'])}):"]
    for a in res["accounts"]:
        ins = ", ".join(f"{s['provider']} {mask_email(s['email'])} "
                        f"{'trusted' if trusted(s['provider'], s['email'], a) else 'untrusted'}" for s in signins(a))
        exists = lambda pid: (res["profiles"].get(pid) or {}).get("exists")  # noqa: E731
        kind = ("Empty Auth" if is_empty(a) else "Main Auth" if exists(a["localId"])
                else "Extra Auth" if exists(claim_of(a)) else "Auth reaching no Profile")
        tag = f" {kind}" + (" (not in the email's group)" if a["localId"] in res.get("outside", []) else "")
        out.append(f"  {pre(a['localId'])}{tag} email {mask_email(a.get('email'))} verified={bool(a.get('emailVerified'))} "
                   f"disabled={bool(a.get('disabled'))} claim={'yes' if claim_of(a) else 'no'} "
                   f"last sign-in {date(a.get('lastLoginAt'))} Sign-ins: {ins or 'none'}")
    out.append(f"Profiles ({len(res['profiles'])}):")
    for p, v in res["profiles"].items():
        state = "missing" if not v["exists"] else "deleted" if v["deleted"] else "live"
        out.append(f"  {pre(p)} {state} email {mask_email(v['email'])} role={'set' if v['role'] else 'none'} "
                   f"userDB={v['userdb']}" + (" (RTDB and userDB emails differ)" if v["emails_differ"] else ""))
    out.append("Profile with this email (userDB): " + (pre(res["holder"]) if res.get("holder") else "none"))
    out.append("verdicts:")
    if not res["accounts"] and not any(v["exists"] for v in res["profiles"].values()):
        out.append("  none: no Auth and no Profile reaches this input")
    for v in res["verdicts"]:
        out.append(f"  {v['code']} ({v['rule']})" + (f": {v['detail']}" if v["detail"] else "") + f". Next: {v['next']}")
    return "\n".join(out)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--uid", help="a Firebase uid or a profile id")
    g.add_argument("--email", help="the person's email")
    ap.add_argument("--cli-dir", help="the Aura CLI checkout whose node_modules has firebase-admin (or AURA_CLI_DIR)")
    args = ap.parse_args()
    if args.cli_dir:
        os.environ["AURA_CLI_DIR"] = args.cli_dir
    try:
        res = check(Reader(), uid=args.uid, email=args.email, held=held_set())
    except ReadFailed as e:
        print(f"READ FAILED: {e}")
        return 2
    print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
