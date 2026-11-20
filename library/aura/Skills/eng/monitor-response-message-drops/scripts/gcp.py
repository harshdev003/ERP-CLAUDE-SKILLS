"""
GCP-facing helpers: auth ladder, Cloud Logging query wrapper, Logs Explorer URL decoder.

Auth ladder (per Google's best practices — prefer ADC over long-lived SA keys when possible):
  1. GCP_KEY        (inline JSON string in env)         → SA, project derived from key
  2. GOOGLE_APPLICATION_CREDENTIALS  (file path in env) → SA, project derived from key file
  3. ADC            (gcloud auth application-default login) → user creds
  4. None of above  → AuthError with a clear remediation message

The skill exposes --project to override the SA's home project for one-off cross-project queries.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterator, Optional

try:
    from google.api_core.gapic_v1.client_info import ClientInfo
    from google.cloud import logging as cloud_logging
    from google.oauth2 import service_account
    import google.auth
except ImportError as e:
    print(
        f"ERROR: {sys.executable} has no {e.name}. "
        f"Run it with ~/.claude/python-env/bin/python3 (built by shared:setup-ainative).",
        file=sys.stderr,
    )
    sys.exit(2)


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


class AuthError(RuntimeError):
    pass


@dataclass
class AuthResult:
    client: "cloud_logging.Client"
    project: str
    source: str  # "GCP_KEY" | "GOOGLE_APPLICATION_CREDENTIALS" | "ADC"
    principal: str  # email of SA or user


def get_client(project_override: Optional[str] = None) -> AuthResult:
    """Resolve credentials per the auth ladder and return a Cloud Logging client."""

    # 1. GCP_KEY (inline JSON)
    gcp_key = os.environ.get("GCP_KEY")
    if gcp_key:
        try:
            info = json.loads(gcp_key)
        except json.JSONDecodeError as e:
            raise AuthError(
                f"GCP_KEY is set but did not parse as JSON ({e}). "
                f"Check ~/.claude/settings.json — the value must be a single-line JSON string with internal quotes escaped. "
                f"See references/auth-setup.md for the conversion one-liner."
            )
        try:
            creds = service_account.Credentials.from_service_account_info(info)
        except (KeyError, ValueError) as e:
            raise AuthError(
                f"GCP_KEY parsed as JSON but is not a valid service-account key ({e}). "
                f"Re-download the key from GCP Console → IAM → Service Accounts → Keys."
            )
        project = project_override or info.get("project_id")
        if not project:
            raise AuthError(
                "GCP_KEY has no project_id field and --project was not provided. "
                "Re-download the SA key or pass --project explicitly."
            )
        return AuthResult(
            client=cloud_logging.Client(project=project, credentials=creds, client_info=ClientInfo(user_agent=user_agent())),
            project=project,
            source="GCP_KEY",
            principal=info.get("client_email", "<unknown>"),
        )

    # 2. GOOGLE_APPLICATION_CREDENTIALS (file path)
    gac_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if gac_path:
        if not os.path.isfile(gac_path):
            raise AuthError(
                f"GOOGLE_APPLICATION_CREDENTIALS points to {gac_path} but the file does not exist."
            )
        try:
            with open(gac_path) as f:
                info = json.load(f)
            creds = service_account.Credentials.from_service_account_file(gac_path)
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            raise AuthError(
                f"GOOGLE_APPLICATION_CREDENTIALS file at {gac_path} could not be parsed as a SA key ({e})."
            )
        project = project_override or info.get("project_id")
        if not project:
            raise AuthError(
                f"SA key at {gac_path} has no project_id field and --project was not provided."
            )
        return AuthResult(
            client=cloud_logging.Client(project=project, credentials=creds, client_info=ClientInfo(user_agent=user_agent())),
            project=project,
            source="GOOGLE_APPLICATION_CREDENTIALS",
            principal=info.get("client_email", "<unknown>"),
        )

    # 3. ADC (gcloud auth application-default login)
    try:
        creds, default_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/logging.read"]
        )
    except Exception as e:
        raise AuthError(
            f"No GCP_KEY or GOOGLE_APPLICATION_CREDENTIALS set, and ADC failed ({e}).\n"
            f"Fix: either set GCP_KEY in ~/.claude/settings.json (see references/auth-setup.md) "
            f"or run: gcloud auth application-default login"
        )
    project = project_override or default_project
    if not project:
        raise AuthError(
            "ADC succeeded but no default project. Run: gcloud config set project <id> "
            "or pass --project explicitly."
        )
    # Best-effort principal label for ADC; not always available
    principal = getattr(creds, "service_account_email", None) or "<adc-user>"
    return AuthResult(
        client=cloud_logging.Client(project=project, credentials=creds, client_info=ClientInfo(user_agent=user_agent())),
        project=project,
        source="ADC",
        principal=principal,
    )


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def list_entries(
    auth: AuthResult,
    filter_str: str,
    page_size: int = 1000,
    max_entries: int = 5000,
    order_by: str = "timestamp desc",
) -> Iterator[dict]:
    """Yield Cloud Logging entries as dicts (.to_api_repr())."""
    count = 0
    for entry in auth.client.list_entries(
        filter_=filter_str, page_size=page_size, order_by=order_by
    ):
        yield entry.to_api_repr()
        count += 1
        if count >= max_entries:
            break


# ---------------------------------------------------------------------------
# Logs Explorer URL decoder
# ---------------------------------------------------------------------------

# Matches https://console.cloud.google.com/logs/query;query=<encoded>;...?...&project=<id>
_LOGS_EXPLORER_RE = re.compile(
    r"^https?://console\.cloud\.google\.com/logs/query[^?]*", re.IGNORECASE
)
_SHORT_LINK_RE = re.compile(
    r"^https?://cloudlogging\.app\.goo\.gl/[A-Za-z0-9]+/?$", re.IGNORECASE
)


@dataclass
class DecodedQuery:
    filter_str: str           # the Cloud Logging API filter (LQL)
    project: Optional[str]    # extracted from ?project= if present
    duration_iso: Optional[str]  # PT12H, PT1H, etc., if present
    cursor_timestamp: Optional[str]  # cursorTimestamp= if present


def expand_short_link(url: str) -> str:
    """Follow the cloudlogging.app.goo.gl/... 302 redirect to the full Logs Explorer URL."""
    if not _SHORT_LINK_RE.match(url):
        return url
    req = urllib.request.Request(url, method="HEAD")
    try:
        # Don't follow redirects automatically — capture the Location header
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
        # Actually we DO want to follow, just to get the final URL
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.geturl()
    except Exception as e:
        raise RuntimeError(f"Failed to expand short link {url}: {e}")


def decode_logs_explorer_url(url: str) -> DecodedQuery:
    """
    Parse a Logs Explorer URL into a query filter and optional project/window.

    The format is unusual: query parameters live in the *path* segment, separated by
    semicolons, URL-encoded. Example:

      https://console.cloud.google.com/logs/query;query=resource.type%3D%22k8s_container%22;duration=PT12H?project=foo

    The path part holds query/duration/cursorTimestamp; the standard ?... query string
    holds project, organizationId, etc.
    """
    if _SHORT_LINK_RE.match(url):
        url = expand_short_link(url)

    if not _LOGS_EXPLORER_RE.match(url):
        raise ValueError(
            f"Not a recognised Logs Explorer URL: {url}\n"
            f"Expected https://console.cloud.google.com/logs/query;... or https://cloudlogging.app.goo.gl/..."
        )

    parsed = urllib.parse.urlparse(url)

    # Standard query string for ?project=, etc.
    qs = urllib.parse.parse_qs(parsed.query)
    project = qs.get("project", [None])[0]

    # Logs Explorer crams query/duration/cursorTimestamp into URL `params`
    # (the bit after the FIRST `;` in the last path segment). urlparse separates
    # this into parsed.params automatically — DO NOT split parsed.path manually.
    # Example:
    #   /logs/query;query=...;duration=PT12H?project=foo
    #   path:   "/logs/query"
    #   params: "query=...;duration=PT12H"
    params_blob = parsed.params or ""
    path_kv = {}
    for seg in params_blob.split(";"):
        if "=" in seg:
            k, v = seg.split("=", 1)
            path_kv[k] = urllib.parse.unquote(v)

    filter_str = path_kv.get("query", "").strip()
    if not filter_str:
        raise ValueError(
            f"Logs Explorer URL has no `query` parameter: {url}\n"
            f"(parsed params: {params_blob!r})"
        )

    return DecodedQuery(
        filter_str=filter_str,
        project=project,
        duration_iso=path_kv.get("duration"),
        cursor_timestamp=path_kv.get("cursorTimestamp"),
    )


def iso_duration_to_seconds(iso: str) -> int:
    """PT12H → 43200, PT1H → 3600, PT30M → 1800. Minimal parser, only what Logs Explorer emits."""
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        raise ValueError(f"Unsupported ISO 8601 duration: {iso}")
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s
