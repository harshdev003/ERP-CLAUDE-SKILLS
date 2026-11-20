#!/usr/bin/env python3
"""
Flag suspicious requests in the prod load-balancer logs, per source IP.

Read-only. Queries Cloud Logging (resource.type="http_load_balancer") for a window,
groups requests by client IP and applies deterministic rules (references/rules.md).
Prints a report and, with --plan, writes a JSON plan that apply.py turns into
Security Incident Log rows and a Slack alert. Also summarises Cloud Armor (armor.py): where each
campaign was stopped, denies by rule and signature, bans and saturated throttles, own-caller
denies, incident block rules joined to the Blocklist, and policy coverage per backend.

Auth ladder: GCP_KEY (inline JSON) -> GOOGLE_APPLICATION_CREDENTIALS -> ADC.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import armor  # noqa: E402
import probe_rules as rules  # noqa: E402


def _gcp():
    """Import the GCP client on first use, so the rules can be tested without it."""
    try:
        from google.api_core.gapic_v1.client_info import ClientInfo
        from google.cloud import logging as cloud_logging
        from google.oauth2 import service_account
        import google.auth
    except ImportError as e:  # pragma: no cover
        print(f"ERROR: {sys.executable} has no {e.name}. "
              "Run it with ~/.claude/python-env/bin/python3 (built by shared:setup-ainative).", file=sys.stderr)
        sys.exit(2)
    return ClientInfo, cloud_logging, service_account, google.auth

DEFAULT_PROJECT = "firebase-auratech16"

# Scanner and probe-tool user agents. Matched case-insensitively anywhere in the UA.
SCANNER_UA = re.compile(
    r"nuclei|sqlmap|nikto|masscan|zgrab|wpscan|dirbuster|gobuster|ffuf|feroxbuster|"
    r"acunetix|nessus|openvas|qualys|burp|owasp|zaproxy|\bzap\b|httpx|wfuzz|whatweb|"
    r"nmap|securitytest|jaeles|arachni|w3af|commix|xsstrike|dalfox|interactsh",
    re.I,
)
# Paths and payloads no Aura client requests. Matched on the decoded path + query.
ATTACK_PATH = re.compile(
    r"/\.env|/\.git/|/\.aws|/\.ssh|/\.svn|/\.DS_Store|wp-admin|wp-login|xmlrpc\.php|"
    r"phpinfo|phpmyadmin|/cgi-bin/|/etc/passwd|\.\./|%2e%2e|/actuator|/server-status|"
    r"/console/|/solr/|/jenkins|/boaform|/HNAP1|/vendor/phpunit|\$\{jndi|<script|"
    r"union(\s|\+|%20)+select|sleep\(\d|/nuclei",
    re.I,
)
# Crawlers and probes that legitimately produce volume. Exempt from the volume rules
# only; a known-crawler UA that also hits ATTACK_PATH is still flagged (UAs are spoofable).
CRAWLER_UA = re.compile(
    r"googlebot|bingbot|applebot|duckduckbot|yandexbot|baiduspider|google-read-aloud|"
    r"googlehc|kube-probe|uptimerobot|facebookexternalhit|slackbot|twitterbot|linkedinbot",
    re.I,
)
# Build assets a browser asks for from an HTML page cached before the last deploy:
# their 404s are stale caches, not probing.
STALE_ASSET = re.compile(r"^/(_next/static|static)/")
AUTH_ROUTE = re.compile(r"/(users/(signup|signupOrLogin|session|token|login)|auth|login|token|oauth)", re.I)
# Where credentials are tried: a failure here is a failed sign-in, whoever sends it.
# `/users/session` is the sign-in itself; `/users/session/cookie` is the app's session check.
CREDENTIAL_ROUTE = re.compile(r"/(users/(signup|signupOrLogin|token|login|password|session(?![/\w]))|login|token|oauth|password)",
                              re.I)
APP_EXPIRED = "app sign-in expired"


def app_signin_expired(r) -> bool:
    """One of our apps retrying with a sign-in that is no longer valid: the backend (not Cloud Armor)
    answered 401/403 on an ordinary app route. Only for a client whose user agents are all ours; a
    credential route still counts, so guessing passwords under our user agent is still flagged."""
    return (r["status"] in (401, 403) and not CREDENTIAL_ROUTE.search(r["path"])
            and ((r.get("armor") or {}).get("enforced") or {}).get("outcome") != "DENY")
ID_SEGMENT = re.compile(r"/[A-Za-z0-9_-]{20,}(?=/|$)")


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


def _no_who(ua: str) -> str:
    """Drop the person from our AuraClaude/<who>/<plugin>:<skill> agent: Slack and the Incident Log name the skill, never a teammate."""
    return re.sub(r"^AuraClaude/[^/\s:]+/(?=\S)", "AuraClaude/", ua)


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


def client_for(project_override: str | None):
    ClientInfo, cloud_logging, service_account, google_auth = _gcp()
    key = os.environ.get("GCP_KEY")
    if key:
        info = json.loads(key)
        creds = service_account.Credentials.from_service_account_info(info)
        project = project_override or info.get("project_id") or DEFAULT_PROJECT
        return cloud_logging.Client(project=project, credentials=creds, client_info=ClientInfo(user_agent=user_agent())), project
    creds, project = google_auth.default()
    project = project_override or project or DEFAULT_PROJECT
    return cloud_logging.Client(project=project, credentials=creds, client_info=ClientInfo(user_agent=user_agent())), project


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Server-side pre-filter: only traffic a rule can act on. Prod serves ~150k LB requests an
# hour, and Cloud Logging allows 60 read pages a minute, so reading everything would hit
# the quota on any window over a few minutes. --full reads everything (short windows only)
# and is the only mode where the scraping rule sees successful GETs.
SUSPECT_LQL = (
    '(httpRequest.status>=400 '
    'OR httpRequest.userAgent=~"(?i)nuclei|sqlmap|nikto|masscan|zgrab|wpscan|gobuster|ffuf|'
    'feroxbuster|acunetix|nessus|openvas|burp|zaproxy|httpx|nmap|securitytest|curl/|python|go-http|wget" '
    'OR httpRequest.requestUrl=~"(?i)/\\.env|/\\.git|wp-admin|wp-login|xmlrpc|phpinfo|phpmyadmin|'
    'cgi-bin|etc/passwd|\\.\\./|actuator|server-status|jndi|<script|union.+select|'
    # Every sensitive family (probe_rules.SENSITIVE_FAMILIES), so a 2xx on one is always
    # read, whatever the user agent: that is what makes an outcome obtained or data-accessed.
    '%2e%2e|/\\.aws|/\\.ssh|/\\.docker|/\\.kube|credentials|id_rsa|meta-data|computemetadata|'
    '/\\.svn|/\\.hg/|/pma/|myadmin|adminer|backup|/dump|\\.(sql|bak|old|orig|swp|save|zip|tar|tgz|gz|7z|rar|dump)(\\?|$)|'
    '(config|configuration|settings|secrets?|parameters|database|appsettings)\\.(json|ya?ml|php|js|ini|xml|py|rb|inc)|'
    '\\.(ini|conf|cfg|properties|toml)(\\?|$)|web\\.config|/\\.(ds_store|htaccess|htpasswd|npmrc)|info\\.php|'
    'swagger|api-docs|openapi|graphiql|jolokia")'
)


def list_with_retry(client, flt):
    """Stream entries newest first. On a per-minute read-quota error, wait and resume the
    query from the last timestamp seen, skipping entries already yielded at that instant."""
    from google.api_core.exceptions import ResourceExhausted
    cloud_logging = _gcp()[1]
    last_ts, seen_at_last, tries = None, set(), 0
    while True:
        # Newest first: if --limit cuts the read, the live end of a flood is what is kept.
        q = flt if last_ts is None else f'{flt} timestamp<="{last_ts.isoformat()}"'
        try:
            for e in client.list_entries(filter_=q, order_by=cloud_logging.DESCENDING, page_size=1000):
                if last_ts is not None and e.timestamp == last_ts and e.insert_id in seen_at_last:
                    continue
                if e.timestamp != last_ts:
                    last_ts, seen_at_last = e.timestamp, set()
                seen_at_last.add(e.insert_id)
                tries = 0
                yield e
            return
        except ResourceExhausted:
            tries += 1
            if tries > 5:
                raise
            print("# read quota hit; waiting 60s", file=sys.stderr)
            time.sleep(60)


def latency_s(v):
    """httpRequest.latency as seconds; the client hands back '0.12s', a dict or a timedelta."""
    if v is None:
        return None
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, dict):
        return float(v.get("seconds", 0)) + float(v.get("nanos", 0)) / 1e9
    try:
        return float(str(v).rstrip("s"))
    except ValueError:
        return None


PAGE_SAMPLE = 200
ASSET_LQL = r'\.(js|css|map|png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|json|txt|xml)(\?|$)|/_next/|/static/'


# Not HTML pages: API proxies, health checks, RSC payloads.
NOT_PAGE_LQL = r'/auraServices/|/api/|/status|/ready|/health|_rsc='


def soft_404_candidates(rows):
    """(host, path) of sensitive-family GETs a web host answered 2xx: what needs a baseline."""
    return sorted({(r["host"], r["path"]) for r in rows if r["host"] != rules.SERVICES_HOST
                   and rules.is_success(r["method"], r["status"]) and r["method"] in ("GET", "HEAD")
                   and rules.path_family(r["path"], r["query"]) in rules.SENSITIVE_FAMILIES})


def median_size(client, flt, limit):
    sizes = []
    for e in list_with_retry(client, flt):
        r = e.http_request or {}
        parts = urlsplit(r.get("requestUrl") or "")
        if rules.path_family(parts.path, parts.query) not in rules.SENSITIVE_FAMILIES:
            sizes.append(int(r.get("responseSize") or 0))
        if len(sizes) >= limit:
            break
    return sorted(sizes)[len(sizes) // 2] if sizes else None


def page_sizes(client, since, until, candidates):
    """Usual 200 GET response size, as the LB logs it, per host (HTML pages) and per
    host+path, from the same window. The pre-filter never reads ordinary 200s, so this is
    a few small queries, and only when a sensitive-path GET on a web host got a 2xx."""
    base = (f'resource.type="http_load_balancer" timestamp>="{iso(since)}" timestamp<"{iso(until)}" '
            'httpRequest.status=200 httpRequest.requestMethod="GET" httpRequest.responseSize>0 ')
    out = {}
    for host in sorted({h for h, _p in candidates})[:10]:
        size = median_size(client, base + f'httpRequest.requestUrl=~"^https?://{re.escape(host)}/" '
                           f'NOT httpRequest.requestUrl=~"(?i){ASSET_LQL}|{NOT_PAGE_LQL}"', PAGE_SAMPLE)
        if size:
            out[host] = size
    for host, path in candidates[:20]:
        size = median_size(client, base + f'httpRequest.requestUrl=~"^https?://{re.escape(host + path)}(\\?|$)"', 50)
        if size:
            out[host + path] = size
    return out


def auth_success_lql(ips):
    """Answered auth requests from these IPs: the half the pre-filter never reads."""
    who = " OR ".join(f'httpRequest.remoteIp="{ip}"' for ip in ips)
    return (f"({who}) httpRequest.status>=200 httpRequest.status<300 "
            'httpRequest.requestUrl=~"(?i)/(users/(signup|signupOrLogin|session|token|login)|auth|login|token|oauth)"')


def fetch(client, since: dt.datetime, until: dt.datetime, limit: int, full: bool = False, extra: str = "",
          sample: bool = False):
    flt = (f'resource.type="http_load_balancer" timestamp>="{iso(since)}" '
           f'timestamp<"{iso(until)}"' + ("" if full else f" {SUSPECT_LQL}")
           + (f" {extra}" if extra else ""))
    print(f"# query: {flt}", file=sys.stderr)
    n = 0
    for e in list_with_retry(client, flt):
        r = e.http_request or {}
        ip = r.get("remoteIp")
        if not ip:
            continue
        url = r.get("requestUrl") or ""
        parts = urlsplit(url)
        yield {
            "ts": e.timestamp,
            "ip": ip,
            "ua": _no_who(r.get("userAgent") or "")[:300],
            "method": r.get("requestMethod") or "",
            "host": parts.netloc,
            "path": parts.path or "/",
            "query": parts.query,
            "status": int(r.get("status") or 0),
            "size": int(r.get("requestSize") or 0),
            "backend": (e.resource.labels or {}).get("backend_service_name", ""),
            "latency": latency_s(r.get("latency")),
            "bytes": int(r.get("responseSize") or 0),
            "id": e.insert_id,
            "armor": armor.fields(e.payload),
        }
        n += 1
        if n >= limit:
            if sample:  # a sample stops at its size by design
                return
            print(f"WARNING: hit --limit {limit}; kept the newest entries, older ones in the window "
                  f"were not read and counts are lower bounds", file=sys.stderr)
            return


def refused(r):
    """A 4xx that is not a stale-cache asset 404."""
    return 400 <= r["status"] < 500 and not (r["status"] == 404 and STALE_ASSET.search(r["path"]))


def analyse(rows, args, since=None, until=None, page_sizes=None, staff_lookup=None):
    """staff_lookup(ips) -> the set of IPs on a Confirmed Known Team Networks row. Staff and
    outside IPs never share a campaign."""
    by_ip = collections.defaultdict(list)
    own = collections.Counter()
    five_per_min = collections.Counter()
    for r in rows:
        if r["status"] >= 500:
            five_per_min[iso(r["ts"])[:16]] += 1
        name = rules.is_own_traffic(r["ua"], r["host"], r["path"], r["status"])
        if name:
            # Counted, not silently dropped: the plan reports how much was exempt.
            own[name] += 1
            continue
        by_ip[r["ip"]].append(r)
    flagged = []
    for ip, rs in by_ip.items():
        named_uas = {r["ua"] for r in rs if r["ua"]}
        if named_uas and all(armor.OWN_UA.match(u) for u in named_uas):
            # Counted under own traffic, never dropped silently (ENG-1879).
            expired = [r for r in rs if app_signin_expired(r)]
            if expired:
                own[APP_EXPIRED] += len(expired)
                rs = [r for r in rs if not app_signin_expired(r)]
            if not rs:
                continue
        n = len(rs)
        uas = collections.Counter(r["ua"] for r in rs)
        statuses = collections.Counter(r["status"] for r in rs)
        n4 = sum(1 for r in rs if refused(r))
        n_err = sum(1 for r in rs if r["status"] >= 400)
        n_auth_fail = sum(1 for r in rs if r["status"] in (401, 403) and AUTH_ROUTE.search(r["path"]))
        n_401_403 = statuses.get(401, 0) + statuses.get(403, 0)
        scanner = [u for u in uas if SCANNER_UA.search(u)]
        attack_hits = [r for r in rs if ATTACK_PATH.search(r["path"] + "?" + r["query"])]
        named = [u for u in uas if u]
        crawler = bool(named) and all(CRAWLER_UA.search(u) for u in named)
        odd = [r for r in rs if r["method"] not in ("GET", "HEAD", "OPTIONS") and r["status"] in (404, 405)]
        big_odd = [r for r in odd if r["size"] >= args.big_body]
        id_paths = {rules.template_path(r["path"]) for r in rs}
        ids = {m for r in rs for m in ID_SEGMENT.findall(r["path"])}
        edge = collections.Counter(armor.edge(r["armor"]) for r in rs if "armor" in r)
        edge_rules = collections.Counter(str(r["armor"]["enforced"]["priority"]) for r in rs
                                         if "armor" in r and (r["armor"]["enforced"] or {}).get("outcome") == "DENY")

        reasons = []
        if scanner:
            reasons.append(f"scanner user agent ({', '.join(sorted(scanner))[:120]})")
        if len(attack_hits) >= args.attack_paths:
            reasons.append(f"{len(attack_hits)} requests for known probe paths/payloads")
        if not crawler:
            if n4 >= args.min_refused:
                reasons.append(f"{n4} requests refused with 4xx")
            if n_auth_fail >= args.auth_fail or n_401_403 >= args.auth_fail * 3:
                reasons.append(f"{n_401_403} 401/403 ({n_auth_fail} on auth routes): brute force?")
            # Rotation counts only when no Aura app UA is present (one phone sends several
            # Aura/... and WebView UAs, and an office NAT mixes many) and most of the
            # traffic was refused or non-GET. An answered request is neither: an
            # authenticated client posting to its API is not probing.
            app = any(u.startswith("Aura") or "CFNetwork" in u for u in uas)
            n_bad = sum(1 for r in rs if refused(r) or (
                r["method"] not in ("GET", "HEAD", "OPTIONS") and not 200 <= r["status"] < 300))
            if (len(uas) >= args.ua_rotation and not app and n >= args.min_requests
                    and n_bad >= n // 2):
                reasons.append(f"rotated {len(uas)} user agents from one IP, {n_bad}/{n} refused or non-GET")
            if len(odd) >= args.odd_method:
                reasons.append(f"{len(odd)} non-GET requests refused 404/405"
                               + (f", {len(big_odd)} with bodies >= {args.big_body} B" if big_odd else ""))
            if len(ids) >= args.scrape_ids:
                reasons.append(f"{len(ids)} distinct ids requested: scraping?")
            # Errors only: scripted clients of our own APIs send thousands of answered
            # requests a day under python/curl user agents.
            if n_err >= args.flood:
                reasons.append(f"{n_err} error responses to one IP: flood?")
        if not reasons:
            continue

        paths = collections.Counter((r["method"], r["host"], rules.template_path(r["path"]), r["status"]) for r in rs)
        if scanner:
            kind = "Vulnerability scanner"
        elif n_auth_fail >= args.auth_fail or (n_401_403 >= args.auth_fail * 3 and not attack_hits):
            kind = "Brute force / credential stuffing"
        elif len(ids) >= args.scrape_ids:
            kind = "Scraping / abuse"
        elif big_odd:
            kind = "Injection / fuzzing"
        elif attack_hits:
            kind = "Forced browsing"
        elif n_err >= args.flood:
            kind = "Flood / DoS"
        else:
            kind = "Other attack"
        # Ids stripped and no query string: a non-public route may carry a user id.
        # The family says why a page path counts: its query carried the probe.
        soft = [r for r in rs if rules.path_family(r["path"], r["query"]) in rules.SENSITIVE_FAMILIES
                and rules.is_soft_404(r["method"], r["host"], r["path"], r["status"], r.get("bytes", 0),
                                      page_sizes)]
        answered = [r for r in rs if rules.is_success(r["method"], r["status"])
                    and not rules.is_public(r["method"], r["host"], r["path"], r["query"])
                    and not any(r is x for x in soft)]
        ok_np = collections.Counter(
            f"{r['method']} {r['host']}{rules.template_path(r['path'])} "
            f"({rules.path_family(r['path'], r['query'])}) -> {r['status']}"
            for r in answered)
        lat = sorted(r["latency"] for r in rs if r.get("latency") is not None)
        # A sensitive file answered with a body may have been served: data, not just a 2xx.
        accessed = sum(1 for r in answered if r.get("bytes", 0) > 0
                      and rules.path_family(r["path"], r["query"]) in rules.SENSITIVE_FAMILIES)
        # Sign-in after brute force: an answered auth request after this IP's first
        # refused one. The LB cannot see whether the answer was a session or an error
        # page, and UserService's /users/* routes are not in the LB log at all.
        fails = sorted(r["ts"] for r in rs if r["status"] in (401, 403) and AUTH_ROUTE.search(r["path"]))
        # Writes only: loading /login or /oauth as a page is not signing in.
        signin = sum(1 for r in rs if fails and r["ts"] > fails[0] and AUTH_ROUTE.search(r["path"])
                     and r["method"] not in ("GET", "HEAD", "OPTIONS")
                     and rules.is_success(r["method"], r["status"]))
        flagged.append({
            "ip": ip,
            "first": iso(min(r["ts"] for r in rs)),
            "last": iso(max(r["ts"] for r in rs)),
            "requests": n,
            "kind": kind,
            "reasons": reasons,
            "user_agents": [[u, c] for u, c in uas.most_common(8)],
            "statuses": dict(sorted(statuses.items())),
            "hosts": sorted({r["host"] for r in rs})[:10],
            "top_routes": [[f"{m} {h}{p}", s, c] for (m, h, p, s), c in paths.most_common(12)],
            "top_routes_all": [[f"{m} {h}{p}", s, c] for (m, h, p, s), c in paths.items()],
            "user_agents_all": [[u, c] for u, c in uas.items()],
            "attack_samples": sorted({rules.template_path(r["path"] + ("?" + r["query"] if r["query"] else ""))[:120]
                                      for r in attack_hits})[:10],
            "per_minute": dict(sorted(collections.Counter(iso(r["ts"])[:16] for r in rs).items())),
            "distinct_routes": len(id_paths),
            "max_body": max(r["size"] for r in rs),
            "families": dict(collections.Counter(rules.path_family(r["path"], r["query"]) for r in rs)),
            "ok_nonpublic": sum(ok_np.values()),
            "ok_nonpublic_samples": [k for k, _c in ok_np.most_common(5)],
            "n5xx": sum(c for s, c in statuses.items() if s >= 500),
            "latencies": lat,
            "data_accessed": accessed,
            "soft_404": len(soft),
            # One IP over the flood threshold on its own: a grouped scan of many slow IPs
            # is not a flood, however many requests it adds up to.
            "flood_ip": not crawler and n_err >= args.flood,
            "signin_after_fail": signin,
            "ip_minutes": {ip: dict(collections.Counter(iso(r["ts"])[:16] for r in rs))},
            "ua_minutes": {u: dict(collections.Counter(iso(r["ts"])[:16] for r in rs if r["ua"] == u))
                           for u in uas},
            "ip_last": {ip: iso(max(r["ts"] for r in rs))},
            "family_minutes": by_minute(rs, lambda r: rules.path_family(r["path"], r["query"])),
            "status_minutes": by_minute(rs, lambda r: str(r["status"])),
            "edge": dict(edge),
            "edge_rules": dict(edge_rules),
        })
    staff = staff_lookup([f["ip"] for f in flagged]) if staff_lookup and flagged else set()
    for f in flagged:
        f["staff"] = f["ip"] in staff
    flagged = group_campaigns(flagged)
    for f in flagged:
        f["impact"] = flood_impact(f, five_per_min, since, until, args)
    flagged.sort(key=lambda f: -f["requests"])
    return flagged, len(by_ip), dict(own)


def flood_impact(f, five_per_min, since, until, args):
    """Did this campaign's volume move the load balancer? Compares LB-wide 5xx per minute
    while it was active against the rest of the window, and its own median latency.
    Every 5xx passes the pre-filter, so the LB-wide 5xx count is complete; latency is
    only the flood's own requests, since the pre-filter drops other 2xx traffic.

    With under 15 quiet minutes left in the window there is no baseline to compare to, so
    only the flood's own 5xx count: the backend failing the flood itself."""
    if not f.get("flood_ip"):
        return None
    active = set(f["per_minute"])
    during = sum(five_per_min.get(m, 0) for m in active) / max(len(active), 1)
    own = f.get("n5xx", 0) / max(len(active), 1)
    total_minutes = (int((until - since).total_seconds() // 60) if since and until
                     else len(set(five_per_min) | active))
    rest = total_minutes - len(active)
    baseline = ((sum(five_per_min.values()) - sum(five_per_min.get(m, 0) for m in active)) / rest
                if rest >= 15 else None)
    why = []
    if baseline is not None and during >= args.flood_5xx_per_min and during >= 3 * baseline:
        why.append(f"LB 5xx {during:.1f}/min while active vs {baseline:.1f}/min otherwise")
    elif baseline is None and own >= args.flood_5xx_per_min:
        why.append(f"{own:.1f} 5xx/min on its own requests (no quiet baseline in the window)")
    p50 = f.get("latency_p50")
    if p50 is not None and p50 >= args.flood_latency:
        why.append(f"median latency {p50:.1f}s on its requests")
    return {"lb_5xx_per_min": round(during, 2),
            "baseline_5xx_per_min": None if baseline is None else round(baseline, 2),
            "latency_p50": p50, "moved": bool(why), "why": "; ".join(why)}


CATEGORY_ORDER = ["Vulnerability scanner", "Brute force / credential stuffing", "Scraping / abuse",
                  "Injection / fuzzing", "Forced browsing", "Flood / DoS", "Other attack"]
SIMILAR = 0.5
NEAR_MINUTES = 30


def parse(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def route_set(f):
    out = set()
    for route, _status, _count in f["top_routes_all"]:
        method, _, rest = route.partition(" ")
        out.add((method, "/" + rest.split("/", 1)[1] if "/" in rest else "/"))
    return out


def signature(f):
    """What an IP did, without where it came from: the set of (method, path) it requested,
    host and ids stripped. Two IPs with the same signature run the same tool or script."""
    routes = set()
    for route, _status, _count in f["top_routes_all"]:
        method, _, rest = route.partition(" ")
        path = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
        routes.add((method, path))
    return hashlib.sha1(json.dumps([bool(f.get("staff")), sorted(routes)]).encode()).hexdigest()[:12]


def by_minute(rs, key):
    """{key(row): {minute: requests}}: lets the writer count only minutes it has not seen."""
    out = collections.defaultdict(collections.Counter)
    for r in rs:
        out[key(r)][iso(r["ts"])[:16]] += 1
    return {k: dict(v) for k, v in out.items()}


def merge_minutes(maps):
    out = collections.defaultdict(collections.Counter)
    for m in maps:
        for key, minutes in m.items():
            out[key].update(minutes)
    return {k: dict(v) for k, v in out.items()}


def group_campaigns(flagged):
    """Merge flagged IPs that share a signature into one campaign (one log row)."""
    # Union-find: same signature always merges; similar requests (Jaccard >= SIMILAR on the
    # method+path sets) merge when the IPs were active within NEAR_MINUTES of each other.
    parent = list(range(len(flagged)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    sigs = [signature(f) for f in flagged]
    sets = [route_set(f) for f in flagged]
    spans = [(parse(f["first"]), parse(f["last"])) for f in flagged]
    for i in range(len(flagged)):
        for j in range(i + 1, len(flagged)):
            near = (spans[j][0] - spans[i][1]).total_seconds() <= NEAR_MINUTES * 60 and \
                   (spans[i][0] - spans[j][1]).total_seconds() <= NEAR_MINUTES * 60
            inter = len(sets[i] & sets[j])
            union = len(sets[i] | sets[j]) or 1
            if bool(flagged[i].get("staff")) != bool(flagged[j].get("staff")):
                continue  # a teammate's IP never joins an outside campaign
            if sigs[i] == sigs[j] or (near and inter / union >= SIMILAR):
                parent[find(i)] = find(j)
    groups = collections.OrderedDict()
    for i, f in enumerate(flagged):
        groups.setdefault(find(i), []).append(f)
    out = []
    for _root, fs in groups.items():
        sig = signature(fs[0])
        if len(fs) == 1:
            f = fs[0]
        else:
            uas = collections.Counter()
            statuses = collections.Counter()
            routes = collections.Counter()
            for g in fs:
                uas.update({u: c for u, c in g["user_agents_all"]})
                statuses.update(g["statuses"])
                routes.update({(r, s): c for r, s, c in g["top_routes_all"]})
            f = dict(fs[0])
            f.update({
                # Highest-priority category among the members (same order as analyse()).
                "kind": min((g["kind"] for g in fs), key=CATEGORY_ORDER.index),
                "ips": [g["ip"] for g in fs],
                "first": min(g["first"] for g in fs),
                "last": max(g["last"] for g in fs),
                "requests": sum(g["requests"] for g in fs),
                "reasons": fs[0]["reasons"] + [f"same requests from {len(fs)} IPs"],
                "user_agents": [[u, c] for u, c in uas.most_common(8)],
                "statuses": dict(sorted(statuses.items())),
                "hosts": sorted({h for g in fs for h in g["hosts"]})[:10],
                "top_routes": [[r, s, c] for (r, s), c in routes.most_common(12)],
                "attack_samples": sorted({a for g in fs for a in g["attack_samples"]})[:10],
                "per_minute": dict(sorted(sum((collections.Counter(g["per_minute"]) for g in fs),
                                              collections.Counter()).items())),
                "families": dict(sum((collections.Counter(g["families"]) for g in fs),
                                     collections.Counter())),
                "ok_nonpublic": sum(g["ok_nonpublic"] for g in fs),
                "ok_nonpublic_samples": sorted({x for g in fs for x in g["ok_nonpublic_samples"]})[:5],
                "n5xx": sum(g["n5xx"] for g in fs),
                "latencies": sorted(x for g in fs for x in g["latencies"]),
                "data_accessed": sum(g["data_accessed"] for g in fs),
                "soft_404": sum(g["soft_404"] for g in fs),
                "flood_ip": any(g["flood_ip"] for g in fs),
                "signin_after_fail": sum(g["signin_after_fail"] for g in fs),
                "ip_minutes": {k: v for g in fs for k, v in g["ip_minutes"].items()},
                "ua_minutes": merge_minutes(g["ua_minutes"] for g in fs),
                "ip_last": {k: v for g in fs for k, v in g["ip_last"].items()},
                "family_minutes": merge_minutes(g["family_minutes"] for g in fs),
                "status_minutes": merge_minutes(g["status_minutes"] for g in fs),
                "edge": dict(sum((collections.Counter(g.get("edge", {})) for g in fs), collections.Counter())),
                "edge_rules": dict(sum((collections.Counter(g.get("edge_rules", {})) for g in fs),
                                       collections.Counter())),
            })
        f["campaign"] = sig
        f["routes"] = sorted({f"{m} {path}" for g in fs for m, path in route_set(g)})[:300]
        f.setdefault("ips", [f["ip"]])
        f["ip"] = ", ".join(f["ips"])
        # One median over every member's requests: a max of medians lets one slow IP
        # make the whole campaign look like it slowed the load balancer.
        lat = f.pop("latencies", None) or []
        f["latency_p50"] = lat[len(lat) // 2] if lat else None
        f.pop("user_agents_all", None)
        f.pop("top_routes_all", None)
        out.append(f)
    return out


def report(flagged, total_ips, n_rows, since, until, own=None):
    print(f"flag-suspicious-traffic {iso(since)}..{iso(until)}: {n_rows} suspicious LB requests from {total_ips} IPs, "
          f"{len(flagged)} flagged")
    for name, c in (own or {}).items():
        print(f"  exempt as our own traffic: {c} requests ({name})")
    for f in flagged:
        # Novelty needs apply.py's history, so this is the floor: apply may raise Info to Low.
        sev, why = rules.severity(f)
        print(f"\nFLAG {f['ip']}  {f['kind']}  {f['requests']} req  {f['first']}..{f['last']}"
              f"  [{sev.upper()}] {rules.group_key(f)}")
        for r in f["reasons"]:
            print(f"  - {r}")
        if sev != "Info":
            for r in why:
                print(f"  ! {r}")
        print("  families: " + ", ".join(f"{k}:{v}" for k, v in
                                        sorted(f["families"].items(), key=lambda x: -x[1])))
        print("  user agents: " + "; ".join(f"{c}x {u[:90]}" for u, c in f["user_agents"][:4]))
        print("  statuses: " + ", ".join(f"{s}:{c}" for s, c in f["statuses"].items()))
        for route, s, c in f["top_routes"][:5]:
            print(f"  {c:>5}  {s}  {route[:110]}")
        if f["attack_samples"]:
            print("  probe paths: " + " | ".join(f["attack_samples"][:5]))
        print("  " + edge_line(f))


def edge_line(f):
    """Where Cloud Armor stopped this campaign: at the edge, or not at all."""
    e = f.get("edge") or {}
    rules_ = ", ".join(f"{p}:{n}" for p, n in sorted((f.get("edge_rules") or {}).items()))
    return (f"edge: {e.get('blocked', 0)} blocked by Cloud Armor"
            + (f" (rules {rules_})" if rules_ else "")
            + f", {e.get('reached', 0)} reached the backend"
            + (f", {e['other']} other" if e.get("other") else ""))


def armor_pass(client, since, until, args, call=None):
    """Cloud Armor summary for the window (armor.summarise). Counts only, never raises: a
    failed read comes back as {"error": …} and is reported, without holding back the probe
    rows. call is the Notion client for the Blocklist join (None: skipped)."""
    try:
        entries = list(fetch(client, since, until, args.armor_limit, full=True, extra=armor.ARMOR_LQL))
        own = sorted({e["ip"] for e in entries if armor.OWN_UA.match(e["ua"])
                      and any((e["armor"][k] or {}).get("outcome") == "DENY" for k in ("enforced", "preview"))})[:20]
        confirmed = set()
        if own:
            who = " OR ".join(f'httpRequest.remoteIp="{ip}"' for ip in own)
            ok = (f"({who}) httpRequest.status>=200 httpRequest.status<300 "
                  'jsonPayload.statusDetails="response_sent_by_backend"')
            confirmed = {r["ip"] for r in fetch(client, since, until, 2000, full=True, extra=ok, sample=True)}
        sample = [(r["backend"], r["armor"]["enforced"] is not None)
                  for r in fetch(client, since, until, args.coverage_sample, full=True, sample=True)]
        blocklist, bl_error = None, None
        if call is not None:
            try:
                blocklist = armor.blocklist_rows(call)
            except Exception as e:  # the join is extra; the counts stand without it
                bl_error = str(e)[:200]
        s = armor.summarise(entries, sample, confirmed, blocklist)
        s.update({"entries": len(entries), "limited": len(entries) >= args.armor_limit,
                  "coverage_sample": len(sample), "blocklist_error": bl_error,
                  "blocklist_joined": blocklist is not None})
        return s
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def armor_report(s):
    if "error" in s:
        print(f"\nCloud Armor: read FAILED ({s['error']}): no Cloud Armor summary this run (missing, not clean)")
        return
    for line in armor.report(s, s["entries"], s["limited"], s["coverage_sample"]):
        print(line)
    if not s["blocklist_joined"]:
        print("  incident rules not joined to the Blocklist: "
              + (s["blocklist_error"] or "NOTION_TOKEN unset"))


def window(args):
    until = dt.datetime.fromisoformat(args.until.replace("Z", "+00:00")) if args.until else dt.datetime.now(dt.timezone.utc)
    since = (dt.datetime.fromisoformat(args.since.replace("Z", "+00:00")) if args.since
             else until - dt.timedelta(minutes=args.minutes))
    return since, until


def build_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--minutes", type=int, default=60, help="window length ending now (default 60)")
    p.add_argument("--since", help="window start, ISO UTC (overrides --minutes)")
    p.add_argument("--until", help="window end, ISO UTC (default now)")
    p.add_argument("--project", help=f"GCP project (default from GCP_KEY, else {DEFAULT_PROJECT})")
    p.add_argument("--limit", type=int, default=200000, help="max log entries to read")
    p.add_argument("--plan", help="write the flagged IPs as JSON to this file")
    p.add_argument("--full", action="store_true",
                   help="read every request, not just suspicious ones (short windows only)")
    p.add_argument("--min-requests", type=int, default=100, help="minimum requests for UA rotation")
    p.add_argument("--min-refused", type=int, default=150, help="4xx responses that flag an IP")
    p.add_argument("--auth-fail", type=int, default=30)
    p.add_argument("--ua-rotation", type=int, default=3)
    p.add_argument("--odd-method", type=int, default=50)
    p.add_argument("--big-body", type=int, default=16384)
    p.add_argument("--probe-paths", "--attack-paths", dest="attack_paths", type=int, default=3)
    p.add_argument("--scrape-ids", type=int, default=300)
    p.add_argument("--flood", type=int, default=2000, help="requests from one IP that count as a flood")
    p.add_argument("--flood-5xx-per-min", type=float, default=5.0,
                   help="LB-wide 5xx per minute during a flood that counts as impact (and >= 3x baseline)")
    p.add_argument("--flood-latency", type=float, default=2.0,
                   help="median latency (s) of a flood's requests that counts as impact")
    p.add_argument("--no-armor", action="store_true", help="skip the Cloud Armor summary")
    p.add_argument("--armor-limit", type=int, default=50000,
                   help="max Cloud Armor deny / over-limit entries to read")
    p.add_argument("--coverage-sample", type=int, default=2000,
                   help="newest responses sampled for policy coverage per backend")
    return p


def team_lookup():
    """(staff_lookup for analyse, the stats it fills). Without NOTION_TOKEN nobody is staff."""
    team = {"checked": 0, "staff": 0, "lookup_failed": 0, "error": None}
    if not os.environ.get("NOTION_TOKEN"):
        team["error"] = "NOTION_TOKEN unset: Known Team Networks not read"
        return None, team

    def lookup(ips):
        import incident_log
        import team_networks
        staff, stats = team_networks.staff_ips(ips, incident_log.notion)
        team.update(stats)
        if stats["error"] or stats["lookup_failed"]:
            print(f"WARNING: team networks: {stats['error'] or ''} {stats['lookup_failed']} whois lookup(s) failed; "
                  "those IPs are treated as outside", file=sys.stderr)
        return staff
    return lookup, team


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()

    since, until = window(args)
    client, project = client_for(args.project)
    rows = list(fetch(client, since, until, args.limit, args.full))
    sizes = page_sizes(client, since, until, soft_404_candidates(rows))
    for host, size in sizes.items():
        print(f"# usual 200 size for {host}: {size} bytes (soft-404 baseline)", file=sys.stderr)
    staff_lookup, team = team_lookup()
    flagged, total_ips, own = analyse(rows, args, since, until, sizes, staff_lookup)
    # The pre-filter drops answered requests from browser UAs, so a sign-in that worked
    # after a brute force is invisible to it. Ask for exactly those.
    brute = sorted({ip for f in flagged if f["kind"] == rules.BRUTE_FORCE for ip in f["ips"]})[:20]
    if brute and not args.full:
        extra = list(fetch(client, since, until, args.limit, full=True, extra=auth_success_lql(brute)))
        seen = {r["id"] for r in rows}
        extra = [r for r in extra if r["id"] not in seen]
        if extra:
            rows += extra
            flagged, total_ips, own = analyse(rows, args, since, until, sizes, staff_lookup)
    report(flagged, total_ips, len(rows), since, until, own)
    summary = None
    if not args.no_armor:
        call = None
        if os.environ.get("NOTION_TOKEN"):
            import incident_log
            call = incident_log.notion
        summary = armor_pass(client, since, until, args, call)
        armor_report(summary)
    if args.plan:
        with open(args.plan, "w", encoding="utf-8") as fh:
            json.dump({"project": project, "since": iso(since), "until": iso(until),
                       "requests": len(rows), "ips": total_ips, "own_traffic": own, "page_sizes": sizes,
                       "flagged": flagged, "armor": summary, "team_networks": team}, fh, indent=1)
        print(f"\nplan: {args.plan}", file=sys.stderr)
    sys.exit(10 if flagged or (summary or {}).get("alerts") else 0)


if __name__ == "__main__":
    main()
