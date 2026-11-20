"""
Shared request rules for detect.py, apply.py and blocklist.py.

- path_family(): one normalized family per request path, so the detector and
  the Incident Log writer group the same request the same way.
- is_public(): routes where a 2xx tells the client nothing (static assets, page GETs).
- is_own_traffic(): our own service traffic, never flagged or logged.
- group_key(), severity(): the Incident Log group and Info/Low/Medium/High of a campaign.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

OTHER = "Other paths"

# First match wins. Payload families come first: a traversal can ride on any path.
FAMILIES = [
    ("Path traversal", r"\.\./|\.\.\\|%2e%2e|/etc/passwd|win\.ini|/proc/self"),
    ("Injection payloads", r"\$\{jndi|<script|union(\s|\+)+select|sleep\(\d|benchmark\(|"
                           r"\bor\s+1=1|/bin/(ba)?sh|cmd\.exe|\bwget\s|\bcurl\s"),
    (".env files", r"/\.env"),
    ("Git / VCS", r"/\.git|/\.svn|/\.hg/|/\.bzr"),
    ("Cloud credentials", r"/\.aws|/\.ssh|/\.docker|/\.kube|/\.azure|/\.gcloud|\.s3cfg|"
                          r"id_rsa|id_ed25519|credentials|service[-_]?account.*\.json|"
                          r"/latest/meta-data|/computemetadata"),
    ("phpMyAdmin", r"phpmyadmin|/pma/|/myadmin|/mysqladmin|adminer"),
    ("WordPress", r"/wp-|xmlrpc\.php|wordpress|/wlwmanifest"),
    ("Backups and dumps", r"\.(sql|bak|backup|old|orig|swp|save|zip|tar|tgz|gz|7z|rar|dump)$|"
                          r"backup|/dump|/db\.|database\.sql"),
    ("Config files", r"(config|configuration|settings|secrets?|parameters|database|application|"
                     r"appsettings|local)\.(json|ya?ml|php|js|ini|xml|py|rb|inc)$|"
                     r"\.(ini|conf|cfg|properties|toml)$|web\.config|"
                     r"/\.(ds_store|htaccess|htpasswd|npmrc|vscode|idea|travis|circleci)|"
                     r"phpinfo|info\.php|/server-info"),
    ("Actuator / API docs", r"/actuator|swagger|api-docs|openapi\.(json|ya?ml)|/server-status|"
                            r"graphiql|/__debug|/debug/(pprof|vars)|/jolokia"),
    ("VPN and appliance exploits", r"cisco|\+cscoe\+|juniper|fortinet|fortigate|globalprotect|"
                                   r"global-protect|pulse|/dana-|citrix|netscaler|sslvpn|"
                                   r"devmode\.action|/portal/|/remote/|boaform|hnap1|/cgi-bin/|"
                                   r"/owa/|/ecp/|autodiscover|/geoserver|/solr|/jenkins|"
                                   r"/hudson|/manager/html|/console/|/vendor/phpunit|/boa|"
                                   r"/goform|/setup\.cgi"),
    ("MCP / AI endpoints", r"^/(mcp|sse)(/|$)|/v1/models|/v1/chat/completions|/api/chat|"
                           r"/ollama|/api/tags|/api/generate"),
    ("PHP scripts", r"\.php\d?(/|$)"),
]
FAMILY_RE = [(name, re.compile(rx, re.I)) for name, rx in FAMILIES]
FAMILY_NAMES = [name for name, _ in FAMILIES] + [OTHER]

# A 2xx on these families is never "public", even as a page GET on a web host:
# getting one back may mean the file was served.
SENSITIVE_FAMILIES = {"Path traversal", "Injection payloads", ".env files", "Git / VCS",
                      "Cloud credentials", "Backups and dumps", "Config files", "phpMyAdmin",
                      "Actuator / API docs"}


def path_family(path: str, query: str = "") -> str:
    """Normalized family of one request. Matches the raw and the twice-decoded form, family
    by family, so %252e%252e, %2e%2e and ../ all land in the same family. The path is also
    matched alone, so end-anchored families (`config.json$`) still hold with a query."""
    path = path or "/"
    candidates = [path, unquote(unquote(path))]
    if query:
        target = f"{path}?{query}"
        candidates += [target, unquote(unquote(target))]
    for name, rx in FAMILY_RE:
        if any(rx.search(c) for c in candidates):
            return name
    return OTHER


def route_family(route: str) -> str:
    """Family of a 'METHOD /path' or 'METHOD host/path' string from a plan or state."""
    rest = route.split(" ", 1)[-1]
    if not rest.startswith("/"):
        rest = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    return path_family(rest)


SERVICES_HOST = "services.aurahealth.io"

# GET routes on services.aurahealth.io that answer anyone: ContentService routes with no
# auth middleware or only `identify` (optional; a token only adds the caller's own data).
# Read from ContentService src/http/router/*/index.js at 07bc101d (2.304.8); coach GETs
# re-read at v2.304.25, which put `authenticate` on all of them except the health routes.
# The prod ingress sends these prefixes to content-service. Anything not listed stays non-public.
# Express matches in order, so a single-segment id route excludes the names registered
# before it with auth (/contents/favorites).
_ID = r"[^/]+"
PUBLIC_SERVICE_ROUTES = [
    ("ContentService", re.compile(
        rf"^/coaches/(ready|status)/?$"
        rf"|^/channels(/list|/{_ID})?/?$"
        rf"|^/contents(/list|/topicPreferencesConfig|/(?!favorites/?$){_ID})?/?$"
        rf"|^/reels(/list|/{_ID})?/?$"
        rf"|^/(contents|reels)/assets/photo/[cm]/{_ID}(/{_ID})?/?$"
        rf"|^/assets/{_ID}/{_ID}/{_ID}(/{_ID})?/?$")),
]


def is_public_service_route(path: str) -> str | None:
    """Name of the service whose public GET route this path is, or None."""
    for name, rx in PUBLIC_SERVICE_ROUTES:
        if rx.search(path or ""):
            return name
    return None


def is_public(method: str, host: str, path: str, query: str = "") -> bool:
    """True when a 2xx on this request reveals nothing: a static asset or a page GET on a
    web host (every host the load balancer fronts except services.aurahealth.io, bare-IP
    requests included, since they land on the web default backend), or a GET on one of the
    public service routes above. Anything else on services.aurahealth.io, and any write, is
    non-public. So is a GET in a sensitive family even on a public route: a catch-all may
    answer it, but a 2xx there needs a look."""
    if method not in ("GET", "HEAD"):
        return False
    if host == SERVICES_HOST and not is_public_service_route(path):
        return False
    return path_family(path, query) not in SENSITIVE_FAMILIES


SOFT_404_TOLERANCE = 0.10


def is_soft_404(method: str, host: str, path: str, status: int, size: int, page_sizes: dict | None) -> bool:
    """A web page answering a probe as if it were any other request: CoachWeb serves its app
    shell for /live/.env, and `/?phpinfo=1` gets the home page. Seen as a 2xx within 10%
    of the same path's usual size (key host+path) or the host's usual HTML page size (key
    host) in the same window. No baseline or no size is not a soft 404: it stays escalated.
    Sizes are as the LB logs them (compressed), so baselines come from the same logs."""
    if host == SERVICES_HOST or method not in ("GET", "HEAD") or not 200 <= status < 300 or not size:
        return False
    sizes = page_sizes or {}
    for typical in (sizes.get(host + path), sizes.get(host)):
        if typical and abs(size - typical) <= SOFT_404_TOLERANCE * typical:
            return True
    return False


def is_success(method: str, status: int) -> bool:
    # A CORS preflight answers 204 to anyone; it confirms nothing about the route.
    return 200 <= status < 300 and method != "OPTIONS"


# Our own service traffic, matched on user agent AND route (never IP alone: the LiveKit
# voice agent runs on Oracle Cloud IPs that rotate). Only answered requests are exempt,
# so a stolen-UA brute force on these routes still shows up as 401/403s.
OWN_TRAFFIC = [
    # LiveKit voice agent: the OpenAI SDK pointed at our AI proxy.
    ("LiveKit voice agent",
     re.compile(r"^(Async)?OpenAI/Python \d"),
     SERVICES_HOST,
     re.compile(r"^/ai/(openai/(responses|models|chat/completions)(/[^/]+)?|"
                r"conversations/[^/]+/metrics)$")),
    # AINative's coach-upload-monitor GitHub Action: ~430 token-carrying GETs on coach and content routes at 00:00Z.
    ("coach-upload-monitor",
     # detect.fetch drops the <who>/ segment before matching; accept the label with or without it.
     re.compile(r"^AuraClaude/(?:[^/\s:]+/)?ci:coach-upload-monitor "),
     SERVICES_HOST,
     re.compile(rf"^/coaches(/{_ID})?/?$|^/contents/(?!favorites/?$){_ID}/?$")),
]


def is_own_traffic(ua: str, host: str, path: str, status: int) -> str | None:
    """Name of the allow-list entry this request matches, or None."""
    if not (200 <= status < 400):
        return None
    for name, ua_re, want_host, route_re in OWN_TRAFFIC:
        if host == want_host and ua_re.search(ua or "") and route_re.search(path or ""):
            return name
    return None


BRUTE_FORCE = "Brute force / credential stuffing"

# Group key: `<system>|<type>|<outcome>`, shared with eng:monitor-rtdb-probes
# (references/rules.md, "Group key"). Exact strings: rows are matched on them.
SYSTEM = "Web"
ATTACK_TYPE = {
    "Vulnerability scanner": "vuln-scanner",
    BRUTE_FORCE: "brute-force",
    "Scraping / abuse": "scraping",
    "Injection / fuzzing": "injection-fuzzing",
    "Forced browsing": "forced-browsing",
    "Flood / DoS": "flood",
    "Other attack": "other",
}
OUTCOME_COLUMN = {"refused": "No impact (refused / 404)", "obtained": "Partial impact",
                  "data-accessed": "Data accessed"}

SEVERITIES = ["Info", "Low", "Medium", "High"]


def outcome(f: dict) -> str:
    """refused: every request got 3xx/4xx/5xx, or only public pages and assets were served.
    data-accessed: a sensitive file answered 2xx with a body. obtained: any other 2xx on a
    non-public route."""
    if f.get("data_accessed"):
        return "data-accessed"
    return "obtained" if f.get("ok_nonpublic") else "refused"


STAFF = "staff"


def group_key(f: dict) -> str:
    """`Web|<type>|<outcome>`, or `Web|<type>|staff|<outcome>` for clients on a Confirmed Known Team
    Networks row, so a teammate's traffic never joins an outside client's row."""
    staff = [STAFF] if f.get("staff") else []
    return "|".join([SYSTEM, ATTACK_TYPE.get(f["kind"], "other"), *staff, outcome(f)])


def outcome_token(gk: str) -> str:
    return gk.rsplit("|", 1)[-1]


# Ids (20+ characters) and email addresses in a path become :id and :email before anything is
# written: the Incident Log keeps routes, never a person's address or account id.
_ID_SEGMENT = re.compile(r"/[A-Za-z0-9_-]{20,}(?=/|$|\?)")
_EMAIL = re.compile(r"[^/?&=\s]+(?:@|%40)[^/?&=\s]+\.[A-Za-z]{2,}", re.I)


def template_path(path: str) -> str:
    """`/users/email/a@b.io` -> `/users/email/:email`; `/users/<uid>` -> `/users/:id`."""
    return _ID_SEGMENT.sub("/:id", _EMAIL.sub(":email", path or ""))


def rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else -1


def severity(f: dict, new_types: list[str] | None = None) -> tuple[str, list[str]]:
    """(severity, reasons) for one campaign. new_types: the categories and path families it
    brought that were not seen in the last 14 days (history lives in apply.py)."""
    high, medium = [], []
    n = f.get("ok_nonpublic", 0)
    if n:
        sample = ", ".join(f.get("ok_nonpublic_samples", [])[:3])
        high.append(f"{n} request(s) got a 2xx on a non-public route ({sample})")
    if f.get("data_accessed"):
        high.append(f"{f['data_accessed']} sensitive path(s) answered 2xx with a body")
    if f.get("signin_after_fail"):
        high.append(f"{f['signin_after_fail']} answered sign-in request(s) after refused ones (possible "
                    "successful sign-in; the LB cannot tell a session from an error page)")
    if high:
        return "High", high
    if f.get("kind") == BRUTE_FORCE:
        medium.append("brute force / credential stuffing on auth routes")
    impact = f.get("impact") or {}
    if impact.get("moved"):
        medium.append(f"flood moved the load balancer: {impact.get('why')}")
    if medium:
        return "Medium", medium
    if new_types:
        return "Low", [f"{t} not seen in the last 14 days" for t in new_types]
    return "Info", ["same type as an existing group, nothing obtained"]


def families_of(f: dict) -> set[str]:
    """Families a plan campaign or a state row touched, ignoring the catch-all."""
    fams = set(f.get("families") or ())
    if not fams:
        fams = {route_family(r) for r in f.get("routes", [])}
    return fams - {OTHER}
