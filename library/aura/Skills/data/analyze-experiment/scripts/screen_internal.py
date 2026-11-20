#!/usr/bin/env python3
"""Identify internal/comped Aura accounts so they can be dropped from experiment statistics.

Three independent nets are applied and unioned, because none is a superset of the others:

  1. Mixpanel exclusion cohorts — catches historical code redeemers (redemption is an EVENT, so
     an account can carry no live code today and still be internal) and test-email accounts.
  2. Per-subscription code scan — catches accounts the cohorts have not picked up.
  3. Email domain + role — catches staff who hold no code at all. The experiment service refuses
     only the roles it considers internal, so a staffer whose account carries no redemption code
     is assigned and stamped into analytics like any other user, invisible to nets 1 and 2.

Usage:
    python3 screen_internal.py --uids uid1,uid2,...
    python3 screen_internal.py --uids-file uids.txt        # one UID per line
    cat uids.txt | python3 screen_internal.py              # stdin

    --json           machine-readable output (default is a human summary)
    --refresh        ignore the on-disk caches and re-fetch everything
    --max-age-days N re-fetch cached verdicts older than N days (default 7)
    --cache-dir DIR  where to keep caches (default: ~/.cache/aura-skills/analyze-experiment —
                     deliberately outside the repo so cache files are never committed)

Env: MIXPANEL_SERVICE_ACCOUNT_USERNAME / _SECRET, plus an Aura token — AURA_ADMIN_TOKEN,
AURA_USER_TOKEN or AURA_API_TOKEN. Candidates are *probed*, not just read, so a set-but-rejected
token cannot shadow a working one, and each net probes the endpoint it reads: `AURA_API_TOKEN` is
rejected by `/subscriptions` (code net) but accepted by `/users` (staff net), so the staff net
still runs where it is the only token present.

Failure model — the guiding rule is that a degraded screen must never look like a clean one:

  * The nets are independent. One failing leaves the others running, and `nets` in the
    output records each net's state with `complete: false`.
  * A cohort fetch failure falls back to a past-TTL cohort cache when one exists, reported as
    stale, rather than screening nobody.
  * A failed re-check of a UID whose stale verdict says *internal* keeps excluding it — days
    out of date beats readmitting a tester. A stale *clean* verdict is not trusted, since the
    account may have redeemed an internal code since.
  * Otherwise a failed lookup lands in `unscreened`: neither confirmed internal nor clean.

A net raises rather than exiting, so a missing credential in one net cannot take the other down
with it. A stale cohort snapshot counts as degraded-but-usable, not failed.

Exit codes: 0 success (even when nothing is excluded, and when one net is degraded), 1 on
unusable input or when EVERY net failed — the only case where continuing would print a
confident drop count over a population that was never screened.
"""

import argparse
import base64
import concurrent.futures
import datetime
import hashlib
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import pathlib


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


MIXPANEL_PROJECT = "963821"
AURA_BASE = "https://services.aurahealth.io"

# Mixpanel cohorts maintained by the data team as the canonical internal-account lists.
INTERNAL_COHORTS = {
    5954114: "Code Redeemed is Aurie-Tester/Employee/Creator + Email Contains 'test'",
    5972972: "Internal is True",
}

# Redemption codes are inconsistently formatted, so normalise before matching: upper-case and
# collapse underscores/whitespace to hyphens. Both brands are live — AURA- on the `content`
# subscription, AURIE- on the `ai` one.
#
# Every AURIE code issued to date is a staff/tester/comp grant, and their shapes defeat both a
# literal list and a prefix anchor: prefixed (AURIE-TESTER), embedded (QA_AURIE_SIM_TEST_1MONTH),
# per-person suffixed (SUSAN2026-AURIE), and named one-offs (AURIE-DESTINY, AURIE-LEAH). So AURIE
# matches as a substring anywhere in the code. AURA is the consumer brand and an acquisition code
# may legitimately carry it, so those grants stay enumerated. An Aurie-branded *marketing* code
# would need an explicit carve-out here.
#
# Genuine acquisition codes (affiliate, podcast, seasonal discounts) are deliberately NOT
# matched — those identify real acquired users who belong in the analysis.
INTERNAL_CODE_SUBSTRING = "AURIE"
INTERNAL_CODE_RE = re.compile(r"^(QA-|AURA-(TESTER|EMPLOYEE|CREATOR|FRIEND))")

# Tags each cached verdict with the rule that produced it, so widening the rule invalidates the
# `internal: false` entries written under the narrower one instead of trusting them until their TTL
# expires. Derived from the rule itself — a hand-bumped counter is a second thing to remember.
CODE_RULE = hashlib.sha256(
    f"{INTERNAL_CODE_SUBSTRING}|{INTERNAL_CODE_RE.pattern}".encode()).hexdigest()[:8]

# Net 3 — staff who hold no redemption code at all.
#
# The experiment service refuses to assign only the roles it treats as internal, and that list
# covers bot roles rather than people. A staffer or an `admin` whose account carries no override
# code is therefore bucketed and stamped into experiment analytics like any other user: `code`,
# `aiCode`, `internal` and `role` all read empty, so nets 1 and 2 cannot see them. The email
# domain is the only signal that does. Observed on aiLiveKitIntegration4 (2026-09-10), where two
# @aurahealth.io accounts sat undetected in one arm.
#
# Only the domain is ever read or reported — never the local part. See `Compliance/Privacy`.
STAFF_DOMAINS = ("aurahealth.io",)
STAFF_ROLES = ("admin", "agent", "system")

# Same purpose as CODE_RULE: widening the rule invalidates verdicts cached under the narrower one.
STAFF_RULE = hashlib.sha256(
    f"{'|'.join(STAFF_DOMAINS)}|{'|'.join(STAFF_ROLES)}".encode()).hexdigest()[:8]

# Caches live outside the repo so a run never leaves uncommitted artefacts in a checkout.
DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "aura-skills",
                                 "analyze-experiment")

# The two endpoints this script reads do not accept the same tokens, so each net probes its own.
# `/subscriptions/{userId}` needs an admin- or user-scoped token: AURA_API_TOKEN — the service
# token the rest of the Aura skills use — returns 403 (wrong role) there, so it is tried last and cannot carry
# the code net on its own. `/users/{userId}` does accept it, so the staff net still runs in an
# environment where AURA_API_TOKEN is the only token present. Order matters: first accepted wins.
AURA_TOKEN_VARS = ("AURA_ADMIN_TOKEN", "AURA_USER_TOKEN", "AURA_API_TOKEN")

# Both caches expire. Internal accounts are created continuously (a new employee, a fresh tester
# code), so a snapshot cached forever means newly-flagged accounts silently stay in the rates. A
# week keeps the routine run cheap while bounding how stale a verdict can be.
MAX_AGE_DAYS = 7.0

# How long a superseded entry is kept on disk beyond its TTL. Retaining stale entries costs
# nothing and keeps the shared cache useful across experiments; the multiple bounds growth.
RETENTION_MULTIPLE = 4

# Net states. A net is USABLE unless it is FAILED — a stale cohort snapshot still screens most
# internal accounts, so it must not be lumped in with "screened nobody".
NET_OK = "ok"
NET_STALE = "stale cache (Mixpanel unreachable)"
NET_FAILED = "failed"


class ScreeningError(RuntimeError):
    """A whole net could not run. Raised instead of exiting, so one net cannot kill the other."""


# MARK: - Public Methods

def is_internal_code(code: str) -> bool:
    """Whether a subscription redemption code marks an internal/comped account.

    Args:
        code (str): raw `code` from a subscription record (may be None).

    Returns:
        bool: True when the normalised code matches an internal pattern.
    """
    normalised = re.sub(r"[_\s]+", "-", (code or "").strip().upper())
    return INTERNAL_CODE_SUBSTRING in normalised or bool(INTERNAL_CODE_RE.match(normalised))


def cohort_uids(cache_path: str, refresh: bool = False, max_age_days: float = MAX_AGE_DAYS) -> set:
    """Firebase UIDs belonging to the Mixpanel internal-account cohorts.

    Read from the `UserID` profile property — never `distinct_id`, which is ~92% anonymous
    `$device:` ids and will not join to anything.

    Args:
        cache_path (str): file to read/write the cached UID list.
        refresh (bool): re-fetch from Mixpanel instead of using the cache.
        max_age_days (float): re-fetch when the cached snapshot is older than this.

    Returns:
        set: internal UIDs known to Mixpanel.
    """
    if not refresh and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as handle:
            cached = json.load(handle)
        if _fresh(cached.get("fetched_at"), max_age_days):
            return set(cached["uids"])

    missing = [name for name in ("MIXPANEL_SERVICE_ACCOUNT_USERNAME",
                                 "MIXPANEL_SERVICE_ACCOUNT_SECRET") if not os.environ.get(name)]
    if missing:
        # Raise rather than exit: the subscription-code net needs nothing from Mixpanel and must
        # still run. SystemExit is not an Exception, so exiting here would bypass that handler.
        raise ScreeningError(f"{', '.join(missing)} not set")
    auth = base64.b64encode(
        f"{os.environ['MIXPANEL_SERVICE_ACCOUNT_USERNAME']}:"
        f"{os.environ['MIXPANEL_SERVICE_ACCOUNT_SECRET']}".encode()).decode()
    ctx = _ssl_context()
    uids = set()
    for cohort_id in INTERNAL_COHORTS:
        page, session_id = 0, None
        while True:
            params = {"project_id": MIXPANEL_PROJECT,
                      "filter_by_cohort": json.dumps({"id": cohort_id}),
                      "page": str(page)}
            if session_id:
                params["session_id"] = session_id
            req = urllib.request.Request(
                "https://mixpanel.com/api/2.0/engage?" + urllib.parse.urlencode(params),
                headers={"Authorization": f"Basic {auth}"})
            data = json.load(urllib.request.urlopen(req, context=ctx, timeout=180))
            session_id = data.get("session_id")
            results = data.get("results", [])
            uids.update(row["$properties"]["UserID"] for row in results
                        if row.get("$properties", {}).get("UserID"))
            if len(results) < data.get("page_size", 1000):
                break
            page += 1
    _atomic_write_json(cache_path, {"cohorts": {str(k): v for k, v in INTERNAL_COHORTS.items()},
                                    "fetched_at": _now_iso(), "uids": sorted(uids)})
    return uids


def code_uids(uids: list, cache_path: str, refresh: bool = False,
              max_age_days: float = MAX_AGE_DAYS) -> tuple:
    """Of `uids`, those holding at least one subscription with an internal code.

    A user holds SEVERAL subscriptions (`type`: content / ai / community), each with its own
    `code`, so every subscription must be inspected. The cache is incremental — only UIDs never
    checked before hit the API.

    Args:
        uids (list): candidate Firebase UIDs.
        cache_path (str): file to read/write the per-UID verdict cache.
        refresh (bool): re-check every UID instead of trusting the cache.
        max_age_days (float): re-check a UID whose cached verdict is older than this.

    Returns:
        tuple: (set of internal UIDs, list of UIDs whose lookup failed).
    """
    # Cache entries map uid -> {"internal": bool, "at": iso}. The file is shared across every
    # experiment analysed on this machine, so a write MERGES into what is already there. Replacing
    # it with just this run's map would delete other experiments' verdicts, and a batch where every
    # lookup failed would truncate the file to `{}`.
    stored = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as handle:
            stored = {uid: entry for uid, entry in json.load(handle).items()
                      if isinstance(entry, dict) and "internal" in entry}

    # Only entries inside the TTL and written under the current matching rule decide anything. A
    # superseded entry stays on disk (it costs nothing and other runs may still supersede it) but is
    # re-checked rather than trusted. `at` is never re-stamped on a carried-forward entry — that
    # would renew the TTL of every UID appearing in consecutive runs, so no verdict would ever age
    # out. An entry from a script version that stamped no rule is re-checked for the same reason a
    # timestampless one is: nothing about it can be verified.
    usable = {} if refresh else {uid: entry for uid, entry in stored.items()
                                 if _fresh(entry.get("at"), max_age_days)
                                 and entry.get("rule") == CODE_RULE}

    todo = [uid for uid in uids if uid not in usable]
    failed = set()
    if todo:
        token = _aura_token(todo[0])
        ctx = _ssl_context()

        def check(uid):
            req = urllib.request.Request(f"{AURA_BASE}/subscriptions/{uid}",
                                         headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent()})
            try:
                payload = json.load(urllib.request.urlopen(req, context=ctx, timeout=30))
                subs = payload.get("data", payload) if isinstance(payload, dict) else payload
                return uid, any(is_internal_code(s.get("code")) for s in (subs or []))
            except Exception:  # pylint: disable=broad-except
                return uid, None  # unknown — must never be silently treated as clean
        stamp = _now_iso()
        checked = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            for uid, verdict in pool.map(check, todo):
                if verdict is None:
                    # Never cached: persisting a failure would make a transient network error
                    # permanent, skipping that user on every later run.
                    failed.add(uid)
                else:
                    checked[uid] = {"internal": verdict, "at": stamp, "rule": CODE_RULE}
        if checked:
            merged = {**stored, **checked}
            # Bound growth without discarding anything still plausibly useful.
            merged = {uid: entry for uid, entry in merged.items()
                      if _fresh(entry.get("at"), max_age_days * RETENTION_MULTIPLE)}
            _atomic_write_json(cache_path, merged)
        usable.update(checked)

    # A failed re-check must never promote a known-internal account back into the rates. When the
    # lookup fails but a stale entry says internal, keep excluding on the stale verdict — being a
    # few days out of date is far better than silently readmitting a tester. The reverse does not
    # hold: a stale `internal: false` stays unscreened, because the account may have redeemed an
    # internal code since, and asserting clean from an expired record is the error that matters.
    stale_internal = {uid for uid in failed
                      if stored.get(uid, {}).get("internal") is True}
    internal = {uid for uid in uids if usable.get(uid, {}).get("internal") is True}
    internal |= stale_internal
    return (internal,
            [uid for uid in uids if uid in failed and uid not in stale_internal])


def staff_uids(uids: list, cache_path: str, refresh: bool = False,
               max_age_days: float = MAX_AGE_DAYS) -> tuple:
    """Of `uids`, those that are Aura staff by email domain, internal role, or the internal flag.

    Reads `GET /users/{userId}`. Only the email DOMAIN is compared and only the domain is ever
    surfaced — the local part is never read into the result or the cache.

    Args:
        uids (list): candidate Firebase UIDs.
        cache_path (str): file to read/write the per-UID verdict cache.
        refresh (bool): re-check every UID instead of trusting the cache.
        max_age_days (float): re-check a UID whose cached verdict is older than this.

    Returns:
        tuple: (set of staff UIDs, list of UIDs whose lookup failed).
    """
    stored = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as handle:
            stored = {uid: entry for uid, entry in json.load(handle).items()
                      if isinstance(entry, dict) and "internal" in entry}

    usable = {} if refresh else {uid: entry for uid, entry in stored.items()
                                 if _fresh(entry.get("at"), max_age_days)
                                 and entry.get("rule") == STAFF_RULE}

    todo = [uid for uid in uids if uid not in usable]
    failed = set()
    if todo:
        token = _aura_token(todo[0], endpoint="users")
        ctx = _ssl_context()

        def check(uid):
            req = urllib.request.Request(f"{AURA_BASE}/users/{uid}",
                                         headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent()})
            try:
                payload = json.load(urllib.request.urlopen(req, context=ctx, timeout=30))
                user = payload.get("data", payload) if isinstance(payload, dict) else payload
                email = (user.get("email") or "").lower()
                domain = email.rpartition("@")[2]
                role = (user.get("role") or "").lower()
                return uid, bool(domain in STAFF_DOMAINS
                                 or role in STAFF_ROLES
                                 or user.get("internal") is True)
            except Exception:  # pylint: disable=broad-except
                return uid, None  # unknown — must never be silently treated as clean
        stamp = _now_iso()
        checked = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            for uid, verdict in pool.map(check, todo):
                if verdict is None:
                    failed.add(uid)
                else:
                    checked[uid] = {"internal": verdict, "at": stamp, "rule": STAFF_RULE}
        if checked:
            merged = {**stored, **checked}
            merged = {uid: entry for uid, entry in merged.items()
                      if _fresh(entry.get("at"), max_age_days * RETENTION_MULTIPLE)}
            _atomic_write_json(cache_path, merged)
        usable.update(checked)

    # Same asymmetry as the code net: a failed re-check keeps excluding on a stale `internal: true`
    # verdict, while a stale `false` is not trusted — an account can become staff, and asserting
    # clean from an expired record is the error that matters.
    stale_internal = {uid for uid in failed
                      if stored.get(uid, {}).get("internal") is True}
    internal = {uid for uid in uids if usable.get(uid, {}).get("internal") is True}
    internal |= stale_internal
    return (internal,
            [uid for uid in uids if uid in failed and uid not in stale_internal])


# MARK: - Private Methods

def _read_stale_cohorts(cache_path: str):
    """The cohort UID list from a past-TTL cache, or None when there is nothing usable.

    An out-of-date cohort list still screens most internal accounts, which beats screening none
    because Mixpanel was briefly unreachable. Callers must report that it was stale.
    """
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as handle:
            return set(json.load(handle)["uids"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _atomic_write_json(path: str, payload: dict) -> None:
    """Write JSON via a temp file + rename, so an interrupted run cannot truncate the cache."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(tmp, path)


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _fresh(stamp: str, max_age_days: float) -> bool:
    """Whether an ISO-8601 timestamp is within `max_age_days` of now.

    A missing or unparseable stamp counts as stale, so a cache written by an older version of
    this script is re-fetched rather than trusted forever.
    """
    if not stamp:
        return False
    try:
        then = datetime.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return False
    if then.tzinfo is None:
        then = then.replace(tzinfo=datetime.timezone.utc)
    age = datetime.datetime.now(datetime.timezone.utc) - then
    return age <= datetime.timedelta(days=max_age_days)


def _aura_token(probe_uid: str, endpoint: str = "subscriptions") -> str:
    """First Aura token that actually authenticates against `/{endpoint}/{userId}`.

    Being *set* is not the same as being *accepted* — an expired or wrong-scope
    `AURA_ADMIN_TOKEN` would otherwise shadow a working `AURA_USER_TOKEN` and send every
    lookup down the failure path, producing a run where nobody is screened. So each candidate
    is probed with one real request and the first that authenticates is used.

    The probe must target the endpoint the caller will actually read, because the two used here
    do not accept the same tokens: `/subscriptions` rejects `AURA_API_TOKEN` while `/users`
    accepts it. Probing the wrong one fails a net whose own lookups would have succeeded —
    which matters most in cadence, where `AURA_API_TOKEN` may be the only token present.

    Args:
        probe_uid (str): a UID from the batch, used for the one-request auth probe.
        endpoint (str): path segment to probe — `subscriptions` (code net) or `users` (staff net).

    Returns:
        str: a token verified against that endpoint.

    Raises:
        ScreeningError: when nothing is set or no candidate authenticates. Raised rather than
            exiting so the other nets still run; the caller decides whether the run is viable.
    """
    present = [(name, os.environ[name]) for name in AURA_TOKEN_VARS if os.environ.get(name)]
    if not present:
        raise ScreeningError(f"none of {', '.join(AURA_TOKEN_VARS)} is set")

    ctx = _ssl_context()
    rejected = []
    for name, value in present:
        req = urllib.request.Request(f"{AURA_BASE}/{endpoint}/{probe_uid}",
                                     headers={"Authorization": f"Bearer {value}", "User-Agent": user_agent()})
        try:
            urllib.request.urlopen(req, context=ctx, timeout=30).read()
            if rejected:
                print(f"WARNING: {', '.join(rejected)} rejected by the {endpoint} endpoint; "
                      f"using {name}", file=sys.stderr)
            return value
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                rejected.append(f"{name} ({exc.code})")
                continue
            # Any other HTTP status means the token was accepted and something else went wrong.
            return value
        except Exception:  # pylint: disable=broad-except
            return value  # network trouble, not an auth verdict — let the batch surface it
    raise ScreeningError(f"every Aura token rejected by {AURA_BASE}/{endpoint} "
                         f"({', '.join(rejected)})")


def _ssl_context() -> ssl.SSLContext:
    """TLS context using certifi's bundle when available (system store often fails here)."""
    try:
        import certifi  # pylint: disable=import-outside-toplevel
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def main() -> None:
    """Screen the given UIDs and report which are internal."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # em dashes / arrows fail on cp1252 (Windows)

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uids", help="comma-separated Firebase UIDs")
    parser.add_argument("--uids-file", help="file with one UID per line")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--refresh", action="store_true", help="ignore caches and re-fetch")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--max-age-days", type=float, default=MAX_AGE_DAYS,
                        help=f"re-fetch cached verdicts older than this (default {MAX_AGE_DAYS:g})")
    args = parser.parse_args()

    if args.uids:
        raw = args.uids.replace(",", "\n")
    elif args.uids_file:
        with open(args.uids_file, encoding="utf-8") as handle:
            raw = handle.read()
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        parser.error("provide --uids, --uids-file, or pipe UIDs on stdin")
    uids = sorted({line.strip() for line in raw.splitlines() if line.strip()})
    if not uids:
        parser.error("no UIDs supplied")

    os.makedirs(args.cache_dir, exist_ok=True)
    cohort_cache = os.path.join(args.cache_dir, ".internal_cohort_uids.json")

    # The nets are independent, so one failing must not take the others down with it. A Mixpanel
    # blip previously aborted the whole run — yielding no screening at all, including the
    # subscription-code net that needs nothing from Mixpanel.
    nets = {"cohort": NET_OK, "code": NET_OK, "staff": NET_OK}
    try:
        by_cohort = cohort_uids(cohort_cache, refresh=args.refresh,
                                max_age_days=args.max_age_days) & set(uids)
    except Exception as exc:  # pylint: disable=broad-except
        by_cohort, nets["cohort"] = set(), f"{NET_FAILED} ({type(exc).__name__})"
        stale = _read_stale_cohorts(cohort_cache)
        if stale is not None:
            by_cohort = stale & set(uids)
            nets["cohort"] = NET_STALE
        print(f"WARNING: cohort net {nets['cohort']}: {exc}", file=sys.stderr)

    try:
        by_code, unscreened = code_uids(uids,
                                        os.path.join(args.cache_dir, ".internal_code_uids.json"),
                                        refresh=args.refresh, max_age_days=args.max_age_days)
    except Exception as exc:  # pylint: disable=broad-except
        by_code, unscreened = set(), list(uids)
        nets["code"] = f"{NET_FAILED} ({type(exc).__name__})"
        print(f"WARNING: code net {nets['code']}: {exc}", file=sys.stderr)

    try:
        by_staff, staff_unscreened = staff_uids(
            uids, os.path.join(args.cache_dir, ".internal_staff_uids.json"),
            refresh=args.refresh, max_age_days=args.max_age_days)
    except Exception as exc:  # pylint: disable=broad-except
        by_staff, staff_unscreened = set(), list(uids)
        nets["staff"] = f"{NET_FAILED} ({type(exc).__name__})"
        print(f"WARNING: staff net {nets['staff']}: {exc}", file=sys.stderr)

    # Abort only when NEITHER net produced anything. A stale cohort snapshot is degraded but
    # usable, so it must not be counted as a failure — discarding it would throw away the very
    # partial screen the fallback exists to preserve.
    if all(state.startswith(NET_FAILED) for state in nets.values()):
        print("ERROR: every screening net failed — no exclusion was applied. Refusing to report "
              "a screened population.", file=sys.stderr)
        sys.exit(1)

    excluded = by_cohort | by_code | by_staff
    membership = {"cohort": by_cohort, "code": by_code, "staff": by_staff}
    # `unscreened` means "status unknown". A UID any net already confirmed internal is known, so
    # it does not belong here even when another net's lookup failed — otherwise a wholesale
    # net failure reports every excluded account as unknown too, inflating the count the
    # readout prints and making a resolved verdict look unresolved.
    unknown = sorted((set(unscreened) | set(staff_unscreened)) - excluded)
    result = {
        "checked": len(uids),
        "excluded": sorted(excluded),
        # Per-net membership rather than pairwise splits: three nets have seven overlap
        # combinations, and the readout only needs "which nets caught this account".
        "by_net": {net: sorted(hits) for net, hits in membership.items()},
        "caught_by_one_net_only": sorted(
            uid for uid in excluded
            if sum(uid in hits for hits in membership.values()) == 1),
        "unscreened": unknown,
        "nets": nets,
        "complete": all(state == NET_OK for state in nets.values()) and not unknown,
    }
    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"checked {len(uids)} users -> {len(excluded)} internal, excluded")
    print(f"  by Mixpanel cohort       : {len(by_cohort)}")
    print(f"  by subscription code     : {len(by_code)}")
    print(f"  by staff domain/role     : {len(by_staff)}")
    print(f"  caught by one net only   : {len(result['caught_by_one_net_only'])}")
    for uid in result["excluded"]:
        hit = "+".join(net for net, hits in membership.items() if uid in hits)
        print(f"    {uid[:8]}...  ({hit})")
    if unknown:
        print(f"  WARNING: {len(unknown)} lookups failed — these users were "
              f"NOT fully screened and must be reported as such:")
        for uid in result["unscreened"]:
            print(f"    {uid[:8]}...")
    degraded = [f"{net}: {state}" for net, state in result["nets"].items() if state != NET_OK]
    if degraded:
        print(f"  WARNING: screen is PARTIAL — {'; '.join(degraded)}. The readout must say the "
              f"exclusion was incomplete rather than imply a screened population.")
    if not excluded:
        print("  NOTE: zero exclusions. Treat this as suspicious rather than reassuring — "
              "check for a stale cache or missing UserID before trusting it.")


if __name__ == "__main__":
    main()
